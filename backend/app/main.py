import json
import logging
import os
import shutil
import tempfile
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from azure.core.exceptions import HttpResponseError, ServiceRequestError

from backend.app.models.document import (
    ClassificationFlagResolution,
    MetadataApproval,
    MetadataReviewUpdate,
    SearchQuery,
    SharePointStagingImport,
    SharePointVersionActionUpdate,
)
from backend.app.services.reviews import (
    ALL_REVIEW_FIELDS,
    approve_metadata_review,
    get_sharepoint_version_candidate,
    metadata_review,
    resolve_classification_flag,
    retry_metadata_writeback,
    review_lock,
    update_field_review,
    update_version_action,
)
from backend.app.services.search import (
    ApprovedKnowledgeSearch,
    SearchConfigurationError,
    SearchOperationError,
    is_search_eligible,
)
from backend.app.services.sharepoint import SharePointClient
from backend.app.services.writeback import SharePointWritebackService, WritebackConflict, WritebackError
from backend.app.services.storage import DATA_PATH, SAMPLE_DOCS, load_documents, save_documents
from backend.app.services.extractors import extract_labeled_value, extract_text
from backend.app.services.lifecycle import lifecycle_metadata

ROOT = Path(__file__).resolve().parents[2]
logger = logging.getLogger(__name__)
load_dotenv()

app = FastAPI(title="Document Metadata Agent MVP")
SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".pptx"}
MAX_BATCH_FILES = 20
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_BATCH_BYTES = 100 * 1024 * 1024


@app.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    return (ROOT / "frontend" / "static-demo.html").read_text(encoding="utf-8")


@app.get("/api/documents")
def documents() -> list[dict]:
    documents = load_documents(DATA_PATH)
    for document in documents:
        document.setdefault("customMetadata", {})
        needs_country = "countryOfOrigin" not in document
        needs_lifecycle = any(field not in document for field in ("reviewStatus", "approvalStatus", "recencyDays"))
        path = SAMPLE_DOCS / document["documentName"]
        text = extract_text(path) if path.is_file() else ""
        if needs_country:
            document["countryOfOrigin"] = extract_labeled_value(text, "Country of origin") or "Unknown"
        if needs_lifecycle:
            document.update(lifecycle_metadata(text))
        document["metadataReview"] = metadata_review(document, text)
    return documents


@app.get("/api/capabilities")
def capabilities() -> dict:
    return {
        "sharePointWritebackEnabled": (
            os.getenv("SHAREPOINT_WRITEBACK_ENABLED", "").strip().lower() in {"1", "true", "yes"}
        ),
        "sharePointStagingFolder": os.getenv("SHAREPOINT_STAGING_FOLDER_NAME", "Staging"),
        "sharePointReviewedFolder": os.getenv("SHAREPOINT_REVIEWED_FOLDER_NAME", "Reviewed"),
    }


@app.get("/api/connectors/sharepoint/staging")
def sharepoint_staging_documents() -> dict:
    try:
        service = _writeback_service(required=True)
        staged = service.client.list_folder_documents(service.staging_folder)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        logger.exception("Unable to list SharePoint staging documents")
        raise HTTPException(status_code=502, detail="Unable to list SharePoint staging documents") from error
    catalog_names = {
        document.get("documentName", "").casefold()
        for document in load_documents(DATA_PATH)
    }
    for document in staged:
        document["alreadyImported"] = document["documentName"].casefold() in catalog_names
    return {
        "folder": service.staging_folder,
        "documents": staged,
    }


@app.post("/api/connectors/sharepoint/staging/import")
def import_sharepoint_staging_documents(request: SharePointStagingImport) -> dict:
    names = request.documentNames
    if len({name.casefold() for name in names}) != len(names):
        raise HTTPException(status_code=400, detail="Duplicate SharePoint document selection")
    if any(
        Path(name).name != name or Path(name).suffix.lower() not in SUPPORTED_EXTENSIONS
        for name in names
    ):
        raise HTTPException(status_code=400, detail="Selection contains an unsupported or invalid document name")
    existing_names = {
        document.get("documentName", "").casefold()
        for document in load_documents(DATA_PATH)
    }
    duplicates = [name for name in names if name.casefold() in existing_names]
    if duplicates:
        raise HTTPException(
            status_code=409,
            detail=f"Already imported: {', '.join(sorted(duplicates))}",
        )

    try:
        service = _writeback_service(required=True)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    backups: dict[Path, bytes | None] = {}
    metadata_backup = DATA_PATH.read_bytes() if DATA_PATH.exists() else None
    try:
        with tempfile.TemporaryDirectory(prefix="sharepoint-staging-import-") as directory:
            downloaded = service.client.download_folder_documents(
                Path(directory),
                service.staging_folder,
                set(names),
            )
            SAMPLE_DOCS.mkdir(parents=True, exist_ok=True)
            for source in downloaded:
                target = SAMPLE_DOCS / source.file_name
                backups[target] = target.read_bytes() if target.exists() else None
                shutil.copy2(source.local_path, target)

        from backend.scripts.ingest import run_ingestion

        documents = run_ingestion(SAMPLE_DOCS, DATA_PATH, set(names))
        by_name = {document["documentName"]: document for document in documents}
        for source in downloaded:
            document = by_name[source.file_name]
            document["sharePoint"] = {
                "siteId": source.site_id,
                "driveId": source.drive_id,
                "driveItemId": source.drive_item_id,
                "fileName": source.file_name,
                "webUrl": source.web_url,
                "etag": source.etag,
                "listItemEtag": source.existing_columns.get("@odata.etag"),
                "createdDateTime": source.created_datetime,
                "modifiedDateTime": source.modified_datetime,
                "existingColumns": source.existing_columns,
                "parentReference": {"name": service.staging_folder},
                "folderPath": service.staging_folder,
            }
            document["sharePointStage"] = {
                "status": "staged",
                "folder": service.staging_folder,
                "stagedAt": source.modified_datetime,
            }
            document["sharePointWritebackEnabled"] = True
        save_documents(documents, DATA_PATH)
    except ValueError as error:
        _restore_import(backups, metadata_backup)
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        _restore_import(backups, metadata_backup)
        logger.exception("SharePoint staging import failed")
        raise HTTPException(status_code=502, detail="SharePoint staging import failed") from error

    return {
        "status": "ok",
        "imported": sorted(names, key=str.casefold),
        "documents": len(documents),
        "sharePointFolder": service.staging_folder,
    }


@app.get("/api/documents/{document_name}", response_class=FileResponse)
def document_content(document_name: str) -> Path:
    if Path(document_name).name != document_name:
        raise HTTPException(status_code=400, detail="Invalid document name")
    path = SAMPLE_DOCS / document_name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Document not found")
    return path


@app.patch("/api/documents/{document_name}/review")
def review_document_field(document_name: str, update: MetadataReviewUpdate) -> dict:
    if Path(document_name).name != document_name:
        raise HTTPException(status_code=400, detail="Invalid document name")
    try:
        document = update_field_review(
            DATA_PATH, SAMPLE_DOCS, document_name, update.field, update.decision, update.value
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Document not found") from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    withdrawn = _withdraw_from_index(document)
    if withdrawn is not None:
        document["searchIndex"] = withdrawn
    return document


@app.get("/api/documents/{document_name}/sharepoint/version-candidate")
def sharepoint_version_candidate(document_name: str) -> dict:
    if Path(document_name).name != document_name:
        raise HTTPException(status_code=400, detail="Invalid document name")
    try:
        service = _writeback_service(required=True)
        candidate = get_sharepoint_version_candidate(DATA_PATH, document_name, service)
        return {"candidate": candidate}
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Document not found") from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except WritebackError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


@app.post("/api/documents/{document_name}/review/flags/resolve")
def resolve_document_classification_flag(
    document_name: str,
    resolution: ClassificationFlagResolution,
) -> dict:
    if Path(document_name).name != document_name:
        raise HTTPException(status_code=400, detail="Invalid document name")
    try:
        document = resolve_classification_flag(
            DATA_PATH,
            SAMPLE_DOCS,
            document_name,
            resolution.flagId,
            resolution.reviewer,
            resolution.note,
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Document or classification flag not found") from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    withdrawn = _withdraw_from_index(document)
    if withdrawn is not None:
        document["searchIndex"] = withdrawn
    return document


@app.patch("/api/documents/{document_name}/review/version-action")
def review_document_version_action(
    document_name: str,
    update: SharePointVersionActionUpdate,
) -> dict:
    if Path(document_name).name != document_name:
        raise HTTPException(status_code=400, detail="Invalid document name")
    try:
        return update_version_action(
            DATA_PATH,
            document_name,
            update.action,
            _writeback_service(required=True),
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Document not found") from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except WritebackConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except WritebackError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.post("/api/documents/{document_name}/review/approve")
def approve_document_metadata(document_name: str, approval: MetadataApproval) -> dict:
    if Path(document_name).name != document_name:
        raise HTTPException(status_code=400, detail="Invalid document name")
    try:
        result = approve_metadata_review(
            DATA_PATH,
            SAMPLE_DOCS,
            document_name,
            _writeback_service(),
            reviewer=approval.reviewer.strip(),
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Document not found") from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except WritebackConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except WritebackError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    result["searchIndex"] = _index_single_document(document_name)
    return result


def _index_single_document(document_name: str) -> dict:
    """Index the document that was just approved, so it becomes answerable at once.

    Indexing failures must not undo an approval that already succeeded, so this
    reports the outcome rather than raising.
    """
    try:
        search = ApprovedKnowledgeSearch()
        search.ensure_index()
    except SearchConfigurationError as error:
        return {"status": "skipped", "reason": f"Search is not configured: {error}"}
    except (SearchOperationError, HttpResponseError, ServiceRequestError) as error:
        # The approval itself already succeeded and must stand. A 403, outage or
        # timeout here is an indexing failure to report, not a failed approval.
        status = {
            "status": "failed",
            "error": f"Could not prepare the search index: {error}",
        }
        _record_index_status(document_name, status)
        return status

    document = _current_document(document_name)
    if document is None:
        return {"status": "skipped", "reason": "Document not found"}

    path = SAMPLE_DOCS / document_name
    text = extract_text(path) if path.is_file() else ""
    if not is_search_eligible(document):
        return {"status": "blocked", "reason": "Document is not human-approved and published."}
    token = _approval_token(document)
    try:
        chunks = search.index_document(document, text)
    except (SearchOperationError, SearchConfigurationError, HttpResponseError, ServiceRequestError) as error:
        status = {"status": "failed", "error": str(error)}
        _record_index_status(document_name, status)
        return status
    status = {"status": "indexed", "index": search.settings.index_name, "chunks": chunks}
    if not _commit_index_result(document_name, token, status):
        return _discard_stale_index_write(search, document_name, chunks)
    return status


def _approval_token(document: dict) -> str:
    """Identify the exact approval state a set of indexed chunks represents.

    Indexing embeds and uploads, which is slow. If a reviewer revokes approval
    while that is in flight, the withdrawal can complete first and the in-flight
    upload would then restore citable chunks for a document that is no longer
    approved. Comparing this token before committing detects that.
    """
    review = document.get("metadataReview")
    review = review if isinstance(review, dict) else {}
    writeback = document.get("sharePointWriteback")
    writeback = writeback if isinstance(writeback, dict) else {}
    return "|".join(
        str(part)
        for part in (
            review.get("status"),
            review.get("reviewedBy"),
            review.get("reviewedAt"),
            writeback.get("status"),
        )
    )


def _commit_index_result(document_name: str, token: str, status: dict) -> bool:
    """Record an indexing result only if approval has not changed meanwhile.

    Returns False when the snapshot that was indexed is stale, in which case the
    chunks just written describe a document that is no longer approved and the
    caller must withdraw them.
    """
    with review_lock():
        documents = load_documents(DATA_PATH)
        document = next(
            (item for item in documents if item.get("documentName") == document_name),
            None,
        )
        if document is None:
            return False
        if not is_search_eligible(document) or _approval_token(document) != token:
            return False
        document["searchIndex"] = status
        save_documents(documents, DATA_PATH)
        return True


def _discard_stale_index_write(
    search: ApprovedKnowledgeSearch, document_name: str, chunks: int
) -> dict:
    """Undo an index write whose approval was revoked while it was in flight."""
    try:
        removed = search.remove_document(document_name, known_chunk_count=chunks)
    except (SearchOperationError, HttpResponseError, ServiceRequestError) as error:
        status = {
            "status": "withdrawal-failed",
            "error": str(error),
            "reason": (
                "Approval changed while this document was being indexed, and the "
                "chunks written for the superseded approval could not be removed."
            ),
            "chunks": chunks,
        }
        _record_index_status(document_name, status)
        return status
    status = {
        "status": "withdrawn",
        "reason": "Approval changed while this document was being indexed, so the write was discarded.",
        "removedChunks": removed,
    }
    _record_index_status(document_name, status)
    return status


def _current_document(document_name: str) -> dict | None:
    """Read one document as it stands right now.

    Long indexing runs must not act on a stale snapshot, so each document is
    re-read immediately before it is used.
    """
    with review_lock():
        for document in load_documents(DATA_PATH):
            if document.get("documentName") == document_name:
                return document
    return None


def _record_index_status(document_name: str, status: dict) -> None:
    """Persist one document's index status without clobbering concurrent edits.

    The review lock is shared with the review service, so this read-modify-write
    cannot interleave with an approval or field edit and lose it.
    """
    with review_lock():
        documents = load_documents(DATA_PATH)
        for document in documents:
            if document.get("documentName") == document_name:
                document["searchIndex"] = status
                break
        save_documents(documents, DATA_PATH)


def _withdraw_from_index(document: dict) -> dict | None:
    """Remove a document's chunks once it is no longer approved for answering.

    Approval status is frozen into each chunk at index time, so a review edit
    that returns a document to `needs-review` has to withdraw it here. Waiting
    for the next full reindex would leave un-approved content citable.
    """
    document_name = document.get("documentName", "")
    previous = document.get("searchIndex")
    previous = previous if isinstance(previous, dict) else {}
    if not document_name or previous.get("status") != "indexed":
        return None
    if is_search_eligible(document):
        return None
    try:
        search = ApprovedKnowledgeSearch()
    except SearchConfigurationError:
        # Search is not configured, so there is nothing indexed to withdraw.
        return None
    try:
        removed = search.remove_document(
            document_name, known_chunk_count=int(previous.get("chunks") or 0)
        )
    except (SearchOperationError, HttpResponseError, ServiceRequestError) as error:
        status = {
            "status": "withdrawal-failed",
            "error": str(error),
            "reason": "Approval was revoked but the indexed chunks could not be removed.",
            "chunks": previous.get("chunks"),
        }
        _record_index_status(document_name, status)
        return status
    status = {
        "status": "withdrawn",
        "reason": "Approval was revoked, so the document was removed from the answer index.",
        "removedChunks": removed,
    }
    _record_index_status(document_name, status)
    return status


@app.post("/api/documents/{document_name}/review/writeback/retry")
def retry_document_writeback(document_name: str) -> dict:
    if Path(document_name).name != document_name:
        raise HTTPException(status_code=400, detail="Invalid document name")
    try:
        return retry_metadata_writeback(
            DATA_PATH,
            SAMPLE_DOCS,
            document_name,
            _writeback_service(required=True),
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Document not found") from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except WritebackConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except WritebackError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


@app.post("/api/ingest")
def ingest() -> dict:
    from backend.scripts.ingest import run_ingestion

    docs = run_ingestion()
    return {"status": "ok", "documents": len(docs)}


@app.post("/api/search/index")
def index_approved_documents() -> dict:
    try:
        search = ApprovedKnowledgeSearch()
        search.ensure_index()
    except SearchConfigurationError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except (SearchOperationError, HttpResponseError, ServiceRequestError) as error:
        raise HTTPException(status_code=502, detail=f"Could not prepare the search index: {error}") from error

    names = [
        str(document.get("documentName", ""))
        for document in load_documents(DATA_PATH)
        if document.get("documentName")
    ]
    indexed = 0
    blocked = 0
    failed = 0
    removed = 0
    # Embedding and Search calls are slow, so each document is re-read immediately
    # before use and only its own searchIndex entry is written back. Saving the
    # snapshot this loop started from would discard approvals made while it ran.
    for name in names:
        document = _current_document(name)
        if document is None:
            continue
        path = SAMPLE_DOCS / name
        text = extract_text(path) if path.is_file() else ""
        document["metadataReview"] = metadata_review(document, text)
        if not is_search_eligible(document):
            # Approval status is frozen into each chunk at index time, so a
            # document that loses approval must be withdrawn from the index too.
            try:
                previous = document.get("searchIndex")
                previous_count = previous.get("chunks", 0) if isinstance(previous, dict) else 0
                withdrawn = search.remove_document(name, known_chunk_count=int(previous_count or 0))
            except (SearchOperationError, HttpResponseError, ServiceRequestError) as error:
                _record_index_status(name, {"status": "failed", "error": str(error)})
                failed += 1
                continue
            removed += withdrawn
            _record_index_status(name, {
                "status": "blocked",
                "reason": "Document is not human-approved and published.",
                "removedChunks": withdrawn,
            })
            blocked += 1
            continue
        token = _approval_token(document)
        try:
            chunks = search.index_document(document, text)
        except (SearchOperationError, HttpResponseError, ServiceRequestError) as error:
            _record_index_status(name, {"status": "failed", "error": str(error)})
            failed += 1
            continue
        status = {
            "status": "indexed",
            "index": search.settings.index_name,
            "chunks": chunks,
        }
        if not _commit_index_result(name, token, status):
            # A reviewer revoked approval while this document was being indexed.
            outcome = _discard_stale_index_write(search, name, chunks)
            removed += int(outcome.get("removedChunks") or 0)
            if outcome["status"] == "withdrawal-failed":
                failed += 1
            else:
                blocked += 1
            continue
        indexed += 1
    return {
        "status": "ok" if failed == 0 else "partial",
        "indexed": indexed,
        "blocked": blocked,
        "failed": failed,
        "removed": removed,
    }


@app.post("/api/search/query")
def query_approved_documents(request: SearchQuery) -> dict:
    try:
        results = ApprovedKnowledgeSearch().query(request.question, request.top)
    except SearchConfigurationError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except SearchOperationError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    return {
        "question": request.question,
        "answerable": bool(results),
        "message": None if results else "No approved evidence was found for this question.",
        "citations": results,
    }


def _writeback_service(required: bool = False) -> SharePointWritebackService | None:
    enabled = os.getenv("SHAREPOINT_WRITEBACK_ENABLED", "").strip().lower() in {"1", "true", "yes"}
    if not enabled:
        if required:
            raise ValueError("SharePoint write-back is disabled")
        return None
    raw_column_map = os.getenv("SHAREPOINT_COLUMN_MAP", "")
    if not raw_column_map:
        raise ValueError("SHAREPOINT_COLUMN_MAP is required when SharePoint write-back is enabled")
    try:
        column_map = json.loads(raw_column_map)
    except json.JSONDecodeError as error:
        raise ValueError("SHAREPOINT_COLUMN_MAP must be a JSON object") from error
    if not isinstance(column_map, dict) or not all(
        isinstance(field, str) and isinstance(column, str) and column.strip()
        for field, column in column_map.items()
    ):
        raise ValueError("SHAREPOINT_COLUMN_MAP must map metadata fields to SharePoint internal column names")
    unsupported_fields = set(column_map).difference(ALL_REVIEW_FIELDS)
    if unsupported_fields:
        raise ValueError(f"SHAREPOINT_COLUMN_MAP contains unsupported fields: {', '.join(sorted(unsupported_fields))}")
    staging_folder = os.getenv("SHAREPOINT_STAGING_FOLDER_NAME", "Staging").strip()
    reviewed_folder = os.getenv("SHAREPOINT_REVIEWED_FOLDER_NAME", "Reviewed").strip()
    archive_folder = os.getenv("SHAREPOINT_ARCHIVE_FOLDER_NAME", "Archive").strip()
    if not staging_folder or "/" in staging_folder or "\\" in staging_folder:
        raise ValueError("SHAREPOINT_STAGING_FOLDER_NAME must be a single folder name")
    if not reviewed_folder or "/" in reviewed_folder or "\\" in reviewed_folder:
        raise ValueError("SHAREPOINT_REVIEWED_FOLDER_NAME must be a single folder name")
    if not archive_folder or "/" in archive_folder or "\\" in archive_folder:
        raise ValueError("SHAREPOINT_ARCHIVE_FOLDER_NAME must be a single folder name")
    if len({staging_folder.casefold(), reviewed_folder.casefold(), archive_folder.casefold()}) != 3:
        raise ValueError("Staging, Reviewed and Archive must be three distinct folders")
    hostname = os.getenv("SHAREPOINT_HOSTNAME", "").strip()
    if not hostname:
        raise ValueError("SHAREPOINT_HOSTNAME is required when SharePoint write-back is enabled")
    return SharePointWritebackService(
        SharePointClient(
            hostname=hostname,
            site_path=os.getenv("SHAREPOINT_SITE_PATH", "/"),
            library_name=os.getenv("SHAREPOINT_LIBRARY_NAME", "Documents"),
            folder_path=os.getenv("SHAREPOINT_FOLDER_PATH", ""),
        ),
        column_map=column_map,
        staging_folder=staging_folder,
        reviewed_folder=reviewed_folder,
        archive_folder=archive_folder,
    )


def _restore_import(backups: dict[Path, bytes | None], metadata_backup: bytes | None) -> None:
    for path, previous in backups.items():
        if previous is None:
            path.unlink(missing_ok=True)
        else:
            path.write_bytes(previous)
    if metadata_backup is None:
        DATA_PATH.unlink(missing_ok=True)
    else:
        DATA_PATH.write_bytes(metadata_backup)


@app.post("/api/documents/upload")
async def upload_documents(
    files: list[UploadFile] = File(...),
    custom_property_name: str = Form(""),
    custom_property_instruction: str = Form(""),
    stage_in_sharepoint: bool = Form(False),
) -> dict:
    custom_property_name = custom_property_name.strip()
    custom_property_instruction = custom_property_instruction.strip()
    if bool(custom_property_name) != bool(custom_property_instruction):
        raise HTTPException(status_code=400, detail="Custom property name and instruction must be provided together")
    if len(custom_property_name) > 60 or len(custom_property_instruction) > 300:
        raise HTTPException(status_code=400, detail="Custom property request is too long")
    custom_property = (custom_property_name, custom_property_instruction) if custom_property_name else None
    if not files or len(files) > MAX_BATCH_FILES:
        raise HTTPException(status_code=400, detail=f"Upload between 1 and {MAX_BATCH_FILES} documents")

    uploads: list[tuple[str, bytes]] = []
    names: set[str] = set()
    total_bytes = 0
    for file in files:
        name = file.filename or ""
        if Path(name).name != name or Path(name).suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise HTTPException(status_code=400, detail=f"Unsupported or invalid file name: {name}")
        if name.lower() in names:
            raise HTTPException(status_code=400, detail=f"Duplicate file name: {name}")
        content = await file.read(MAX_FILE_BYTES + 1)
        if not content or len(content) > MAX_FILE_BYTES:
            raise HTTPException(status_code=400, detail=f"{name} must be between 1 byte and 20 MB")
        total_bytes += len(content)
        if total_bytes > MAX_BATCH_BYTES:
            raise HTTPException(status_code=400, detail="Batch size exceeds 100 MB")
        names.add(name.lower())
        uploads.append((name, content))

    backups: dict[Path, bytes | None] = {}
    metadata_backup = DATA_PATH.read_bytes() if DATA_PATH.exists() else None
    try:
        writeback_service = _writeback_service(required=True) if stage_in_sharepoint else None
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    staged_documents: list[dict] = []
    try:
        SAMPLE_DOCS.mkdir(parents=True, exist_ok=True)
        for name, content in uploads:
            path = SAMPLE_DOCS / name
            backups[path] = path.read_bytes() if path.exists() else None
            path.write_bytes(content)

        from backend.scripts.ingest import run_ingestion

        documents = run_ingestion(SAMPLE_DOCS, DATA_PATH, {name for name, _ in uploads}, custom_property)
        if writeback_service:
            by_name = {document["documentName"]: document for document in documents}
            for name, content in uploads:
                staged = writeback_service.stage(by_name[name], content)
                staged_documents.append(staged)
            save_documents(documents, DATA_PATH)
    except Exception as error:
        if writeback_service:
            for document in reversed(staged_documents):
                try:
                    writeback_service.remove_staged(document)
                except Exception:
                    logger.exception("Failed to remove partially staged SharePoint document")
        for path, previous in backups.items():
            if previous is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(previous)
        if metadata_backup is None:
            DATA_PATH.unlink(missing_ok=True)
        else:
            DATA_PATH.write_bytes(metadata_backup)
        logger.exception("Document processing failed")
        raise HTTPException(status_code=500, detail="Document processing failed") from error

    return {
        "status": "ok",
        "uploaded": [name for name, _ in uploads],
        "documents": len(documents),
        "sharePointFolder": writeback_service.staging_folder if writeback_service else None,
    }

import json
import logging
import os
import shutil
import tempfile
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from backend.app.models.document import MetadataReviewUpdate, SharePointStagingImport
from backend.app.services.reviews import approve_metadata_review, metadata_review, update_field_review
from backend.app.services.reviews import retry_metadata_writeback
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
        return update_field_review(DATA_PATH, SAMPLE_DOCS, document_name, update.field, update.decision, update.value)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Document not found") from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/api/documents/{document_name}/review/approve")
def approve_document_metadata(document_name: str) -> dict:
    if Path(document_name).name != document_name:
        raise HTTPException(status_code=400, detail="Invalid document name")
    try:
        return approve_metadata_review(DATA_PATH, SAMPLE_DOCS, document_name, _writeback_service())
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Document not found") from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except WritebackConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except WritebackError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


@app.post("/api/documents/{document_name}/review/writeback/retry")
def retry_document_writeback(document_name: str) -> dict:
    if Path(document_name).name != document_name:
        raise HTTPException(status_code=400, detail="Invalid document name")
    try:
        return retry_metadata_writeback(DATA_PATH, document_name, _writeback_service(required=True))
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
    unsupported_fields = set(column_map).difference({"businessArea", "audience", "language", "author", "countryOfOrigin"})
    if unsupported_fields:
        raise ValueError(f"SHAREPOINT_COLUMN_MAP contains unsupported fields: {', '.join(sorted(unsupported_fields))}")
    staging_folder = os.getenv("SHAREPOINT_STAGING_FOLDER_NAME", "Staging").strip()
    reviewed_folder = os.getenv("SHAREPOINT_REVIEWED_FOLDER_NAME", "Reviewed").strip()
    if not staging_folder or "/" in staging_folder or "\\" in staging_folder:
        raise ValueError("SHAREPOINT_STAGING_FOLDER_NAME must be a single folder name")
    if not reviewed_folder or "/" in reviewed_folder or "\\" in reviewed_folder:
        raise ValueError("SHAREPOINT_REVIEWED_FOLDER_NAME must be a single folder name")
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

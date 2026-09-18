import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from backend.app.models.document import MetadataReviewUpdate
from backend.app.services.reviews import approve_metadata_review, metadata_review, update_field_review
from backend.app.services.reviews import retry_metadata_writeback
from backend.app.services.sharepoint import SharePointClient
from backend.app.services.writeback import SharePointWritebackService, WritebackConflict, WritebackError
from backend.app.services.storage import DATA_PATH, SAMPLE_DOCS, load_documents
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
        text = ""
        if needs_country or needs_lifecycle:
            path = SAMPLE_DOCS / document["documentName"]
            text = extract_text(path) if path.is_file() else ""
        if needs_country:
            document["countryOfOrigin"] = extract_labeled_value(text, "Country of origin") or "Unknown"
        if needs_lifecycle:
            document.update(lifecycle_metadata(text))
        document["metadataReview"] = metadata_review(document, document.get("summary", ""))
    return documents


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
        return retry_metadata_writeback(DATA_PATH, document_name, _writeback_service())
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


def _writeback_service() -> SharePointWritebackService:
    return SharePointWritebackService(SharePointClient(
        hostname=os.environ["SHAREPOINT_HOSTNAME"],
        site_path=os.getenv("SHAREPOINT_SITE_PATH", "/"),
        library_name=os.getenv("SHAREPOINT_LIBRARY_NAME", "Documents"),
        folder_path=os.getenv("SHAREPOINT_FOLDER_PATH", ""),
    ))


@app.post("/api/documents/upload")
async def upload_documents(
    files: list[UploadFile] = File(...),
    custom_property_name: str = Form(""),
    custom_property_instruction: str = Form(""),
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
        SAMPLE_DOCS.mkdir(parents=True, exist_ok=True)
        for name, content in uploads:
            path = SAMPLE_DOCS / name
            backups[path] = path.read_bytes() if path.exists() else None
            path.write_bytes(content)

        from backend.scripts.ingest import run_ingestion

        documents = run_ingestion(SAMPLE_DOCS, DATA_PATH, {name for name, _ in uploads}, custom_property)
    except Exception as error:
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
    }

import logging
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from backend.app.services.storage import DATA_PATH, SAMPLE_DOCS, load_documents
from backend.app.services.extractors import extract_labeled_value, extract_text
from backend.app.services.lifecycle import lifecycle_metadata

ROOT = Path(__file__).resolve().parents[2]
logger = logging.getLogger(__name__)

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
    return documents


@app.get("/api/documents/{document_name}", response_class=FileResponse)
def document_content(document_name: str) -> Path:
    if Path(document_name).name != document_name:
        raise HTTPException(status_code=400, detail="Invalid document name")
    path = SAMPLE_DOCS / document_name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Document not found")
    return path


@app.post("/api/ingest")
def ingest() -> dict:
    from backend.scripts.ingest import run_ingestion

    docs = run_ingestion()
    return {"status": "ok", "documents": len(docs)}


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

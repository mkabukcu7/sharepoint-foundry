from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from backend.app.services.governance import build_governance_summary
from backend.app.services.storage import DATA_PATH, load_documents

ROOT = Path(__file__).resolve().parents[2]

app = FastAPI(title="SharePoint Knowledge Agent Transparency Dashboard MVP")


@app.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    return (ROOT / "frontend" / "static-demo.html").read_text(encoding="utf-8")


@app.get("/api/documents")
def documents() -> list[dict]:
    return load_documents(DATA_PATH)


@app.get("/api/governance")
def governance() -> dict:
    return build_governance_summary(load_documents(DATA_PATH))


@app.post("/api/ingest")
def ingest() -> dict:
    from backend.scripts.ingest import run_ingestion

    docs = run_ingestion()
    return {"status": "ok", "documents": len(docs)}


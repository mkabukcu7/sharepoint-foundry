import os
import re
from datetime import date, timedelta
from pathlib import Path

from backend.app.models.document import DocumentMetadata
from backend.app.services.ai_providers import get_provider
from backend.app.services.extractors import extract_text
from backend.app.services.governance import freshness_status, review_status
from backend.app.services.storage import SAMPLE_DOCS, save_documents


def run_ingestion() -> list[dict]:
    provider = get_provider()
    documents = []
    for path in sorted(SAMPLE_DOCS.iterdir()):
        if path.suffix.lower() not in {".pdf", ".docx", ".pptx"}:
            continue
        text = extract_text(path)
        ai = provider.analyze(path.name, text)
        owner = _field(text, "Content owner") or "Unassigned"
        age = int(_field(text, "Last reviewed age days") or "400")
        today = date.fromisoformat(os.getenv("DEMO_TODAY", "2026-08-07"))
        last_reviewed = (today - timedelta(days=age)).isoformat()
        freshness = freshness_status(last_reviewed, today=today)
        review = review_status(ai["documentRisk"], freshness, owner)
        doc = DocumentMetadata(
            documentName=path.name,
            fileType=path.suffix.lower().lstrip(".").upper(),
            contentOwner=owner,
            lastReviewedDate=last_reviewed,
            freshnessStatus=freshness,
            reviewStatus=review,
            humanReviewRequired=review == "Human Review Required",
            sourcePath=str(path),
            extractedCharacters=len(text),
            **ai,
        )
        documents.append(doc.model_dump())
    save_documents(documents)
    return documents


def _field(text: str, label: str) -> str | None:
    match = re.search(rf"{re.escape(label)}:\s*([^.]+)", text, re.IGNORECASE)
    return match.group(1).strip() if match else None


if __name__ == "__main__":
    docs = run_ingestion()
    print(f"Ingested {len(docs)} documents")

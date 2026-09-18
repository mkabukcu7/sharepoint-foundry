from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

from backend.app.services.extractors import extract_labeled_value, extract_text
from backend.app.services.storage import load_documents, save_documents
from backend.app.services.writeback import SharePointWritebackService


REVIEW_FIELDS = {
    "businessArea": "Business area",
    "audience": "Audience",
    "language": "Language",
    "author": "Author",
    "countryOfOrigin": "Country of origin",
}
REVIEW_DECISIONS = {"accepted", "edited", "rejected"}
_review_lock = Lock()


def metadata_review(document: dict, text: str) -> dict:
    stored = document.get("metadataReview") if isinstance(document.get("metadataReview"), dict) else {}
    stored_fields = stored.get("fields") if isinstance(stored.get("fields"), dict) else {}
    fields = {}
    for key, label in REVIEW_FIELDS.items():
        value = _text(document.get(key))
        labeled_value = extract_labeled_value(text, label)
        support = "missing" if value == "Unknown" else "grounded" if labeled_value else "inferred"
        existing = stored_fields.get(key) if isinstance(stored_fields.get(key), dict) else {}
        fields[key] = {
            "label": label,
            "value": _text(existing.get("value")) if existing else value,
            "originalValue": _text(existing.get("originalValue")) if existing else value,
            "support": existing.get("support") if existing.get("support") in {"grounded", "inferred", "missing", "confirmed"} else support,
            "evidence": existing.get("evidence") or (f"{label}: {labeled_value}" if labeled_value else "No explicit labeled evidence found."),
            "reviewDecision": existing.get("reviewDecision") if existing.get("reviewDecision") in REVIEW_DECISIONS else "pending",
            "decidedAt": existing.get("decidedAt"),
        }
    return {
        "status": stored.get("status") if stored.get("status") in {"needs-review", "approved"} else "needs-review",
        "reviewedBy": stored.get("reviewedBy"),
        "reviewedAt": stored.get("reviewedAt"),
        "fields": fields,
    }


def update_field_review(
    data_path: Path,
    source_dir: Path,
    document_name: str,
    field: str,
    decision: str,
    value: str | None = None,
) -> dict:
    if field not in REVIEW_FIELDS:
        raise ValueError("Unsupported review field")
    if decision not in REVIEW_DECISIONS:
        raise ValueError("Unsupported review decision")
    edited_value = (value or "").strip()
    if decision == "edited" and not edited_value:
        raise ValueError("Edited value is required")

    with _review_lock:
        documents = load_documents(data_path)
        document = _find_document(documents, document_name)
        text = _document_text(source_dir, document_name)
        review = metadata_review(document, text)
        field_review = review["fields"][field]
        if decision == "edited":
            document[field] = edited_value
            field_review["value"] = edited_value
            field_review["support"] = "confirmed"
        elif decision == "rejected":
            document[field] = "Unknown"
            field_review["value"] = "Unknown"
            field_review["support"] = "missing"
        else:
            field_review["support"] = "confirmed"
        field_review["reviewDecision"] = decision
        field_review["decidedAt"] = _timestamp()
        review["status"] = "needs-review"
        review["reviewedBy"] = None
        review["reviewedAt"] = None
        document["metadataReview"] = review
        save_documents(documents, data_path)
        return document


def approve_metadata_review(
    data_path: Path,
    source_dir: Path,
    document_name: str,
    writeback_service: SharePointWritebackService | None = None,
    reviewer: str = "Demo reviewer",
) -> dict:
    with _review_lock:
        documents = load_documents(data_path)
        document = _find_document(documents, document_name)
        review = metadata_review(document, _document_text(source_dir, document_name))
        unresolved = [field["label"] for field in review["fields"].values() if field["reviewDecision"] not in {"accepted", "edited"}]
        if unresolved:
            raise ValueError(f"Resolve all review fields before approval: {', '.join(unresolved)}")
        review["status"] = "approved"
        review["reviewedBy"] = reviewer
        review["reviewedAt"] = _timestamp()
        document["metadataReview"] = review
        if writeback_service and document.get("sharePoint"):
            try:
                writeback_service.apply(document, reviewer, review["reviewedAt"])
            except Exception:
                save_documents(documents, data_path)
                raise
        save_documents(documents, data_path)
        return document


def retry_metadata_writeback(
    data_path: Path,
    document_name: str,
    writeback_service: SharePointWritebackService,
    reviewer: str = "Demo reviewer",
) -> dict:
    with _review_lock:
        documents = load_documents(data_path)
        document = _find_document(documents, document_name)
        state = document.get("sharePointWriteback")
        if not isinstance(state, dict) or state.get("status") not in {"failed", "conflict"}:
            raise ValueError("Only failed or conflicted writebacks can be retried")
        result = writeback_service.apply(document, reviewer, state.get("reviewedAt") or _timestamp())
        save_documents(documents, data_path)
        return result


def _find_document(documents: list[dict], document_name: str) -> dict:
    for document in documents:
        if document.get("documentName") == document_name:
            return document
    raise KeyError(document_name)


def _document_text(source_dir: Path, document_name: str) -> str:
    path = source_dir / document_name
    return extract_text(path) if path.is_file() else ""


def _text(value: object) -> str:
    return str(value).strip() if value is not None and str(value).strip() else "Unknown"


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()
from datetime import datetime, timezone
from hashlib import sha256
from typing import Protocol

from backend.app.services.sharepoint import SharePointClient


class WritebackError(RuntimeError):
    pass


class WritebackConflict(WritebackError):
    pass


class DocumentStore(Protocol):
    def update_fields(self, drive_id: str, item_id: str, fields: dict, etag: str) -> dict:
        ...


class SharePointWritebackService:
    def __init__(self, client: DocumentStore) -> None:
        self.client = client

    def apply(self, document: dict, reviewer: str, reviewed_at: str) -> dict:
        sharepoint = document.get("sharePoint")
        if not isinstance(sharepoint, dict):
            raise WritebackError("Document is missing SharePoint identity metadata")

        existing = document.get("sharePointWriteback")
        if isinstance(existing, dict) and existing.get("status") == "applied":
            return document

        review = document.get("metadataReview") or {}
        fields = review.get("fields") or {}
        proposed = {
            field: field_review.get("value")
            for field, field_review in fields.items()
            if isinstance(field_review, dict) and field_review.get("reviewDecision") in {"accepted", "edited"}
        }
        approved = dict(proposed)
        column_values = self._column_values(sharepoint.get("existingColumns") or {}, approved)
        if not column_values:
            raise WritebackError("No approved metadata columns map to SharePoint fields")

        idempotency_key = _idempotency_key(document.get("documentName", ""), reviewed_at, approved)
        attempt = int(existing.get("attempt", 0)) + 1 if isinstance(existing, dict) else 1
        state = {
            "status": "pending",
            "idempotencyKey": idempotency_key,
            "proposedValues": proposed,
            "approvedValues": approved,
            "reviewer": reviewer,
            "reviewedAt": reviewed_at,
            "attempt": attempt,
            "lastAttemptAt": _timestamp(),
            "error": None,
            "audit": list(existing.get("audit", [])) if isinstance(existing, dict) else [],
        }
        document["sharePointWriteback"] = state
        try:
            result = self.client.update_fields(
                sharepoint["driveId"],
                sharepoint["driveItemId"],
                column_values,
                sharepoint.get("etag") or "*",
            )
        except WritebackConflict as error:
            state.update({"status": "conflict", "error": str(error)})
            raise
        except Exception as error:
            state.update({"status": "failed", "error": str(error)})
            raise WritebackError(str(error)) from error

        state["status"] = "applied"
        state["error"] = None
        state["audit"].extend({
            "column": column,
            "oldValue": (sharepoint.get("existingColumns") or {}).get(column),
            "newValue": value,
            "changedAt": _timestamp(),
            "reviewer": reviewer,
        } for column, value in column_values.items())
        if result.get("etag"):
            sharepoint["etag"] = result["etag"]
        return document

    @staticmethod
    def _column_values(existing_columns: dict, approved: dict) -> dict:
        values = {}
        labels = {
            "businessArea": "Business area",
            "audience": "Audience",
            "language": "Language",
            "author": "Author",
            "countryOfOrigin": "Country of origin",
        }
        for field, value in approved.items():
            candidates = (labels.get(field, field), field)
            column = next((key for key in existing_columns if key.casefold() in {candidate.casefold() for candidate in candidates}), None)
            if column:
                values[column] = value
        return values


def _idempotency_key(document_name: str, reviewed_at: str, approved: dict) -> str:
    payload = f"{document_name}|{reviewed_at}|{sorted(approved.items())}"
    return sha256(payload.encode("utf-8")).hexdigest()


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()
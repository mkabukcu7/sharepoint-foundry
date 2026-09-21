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

    def upload_document(self, file_name: str, content: bytes, folder_name: str) -> dict:
        ...

    def move_item(self, drive_id: str, item_id: str, folder_name: str, etag: str) -> dict:
        ...

    def delete_item(self, drive_id: str, item_id: str, etag: str = "*") -> None:
        ...

    def refresh_item(self, drive_id: str, item_id: str) -> dict:
        ...


class SharePointWritebackService:
    def __init__(
        self,
        client: DocumentStore,
        column_map: dict[str, str] | None = None,
        staging_folder: str = "Staging",
        reviewed_folder: str = "Reviewed",
    ) -> None:
        self.client = client
        self.column_map = column_map or {}
        self.staging_folder = staging_folder
        self.reviewed_folder = reviewed_folder

    def stage(self, document: dict, content: bytes) -> dict:
        document["sharePoint"] = self.client.upload_document(
            document["documentName"],
            content,
            self.staging_folder,
        )
        document["sharePointStage"] = {
            "status": "staged",
            "folder": self.staging_folder,
            "stagedAt": _timestamp(),
        }
        document["sharePointWritebackEnabled"] = True
        return document

    def remove_staged(self, document: dict) -> None:
        sharepoint = document.get("sharePoint")
        if isinstance(sharepoint, dict):
            self.client.delete_item(
                sharepoint["driveId"],
                sharepoint["driveItemId"],
                sharepoint.get("etag") or "*",
            )
        document.pop("sharePoint", None)
        document.pop("sharePointStage", None)
        document.pop("sharePointWritebackEnabled", None)

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
            "metadataApplied": bool(existing.get("metadataApplied")) if isinstance(existing, dict) else False,
            "fileMoved": bool(existing.get("fileMoved")) if isinstance(existing, dict) else False,
            "destinationFolder": self.reviewed_folder,
            "oldValues": (
                dict(existing.get("oldValues", {}))
                if isinstance(existing, dict)
                else {
                    column: (sharepoint.get("existingColumns") or {}).get(column)
                    for column in column_values
                }
            ),
        }
        document["sharePointWriteback"] = state
        try:
            if not state["metadataApplied"]:
                result = self.client.update_fields(
                    sharepoint["driveId"],
                    sharepoint["driveItemId"],
                    column_values,
                    sharepoint.get("listItemEtag")
                    or (sharepoint.get("existingColumns") or {}).get("@odata.etag")
                    or "*",
                )
                state["metadataApplied"] = True
                if result.get("listItemEtag"):
                    sharepoint["listItemEtag"] = result["listItemEtag"]
                if result.get("etag"):
                    sharepoint["etag"] = result["etag"]
                sharepoint.setdefault("existingColumns", {}).update(column_values)
            if not state["fileMoved"]:
                moved = self.client.move_item(
                    sharepoint["driveId"],
                    sharepoint["driveItemId"],
                    self.reviewed_folder,
                    sharepoint.get("etag") or "*",
                )
                state["fileMoved"] = True
                sharepoint["folderPath"] = self.reviewed_folder
                sharepoint["parentReference"] = moved.get("parentReference")
                sharepoint["webUrl"] = moved.get("webUrl", sharepoint.get("webUrl", ""))
                if moved.get("eTag"):
                    sharepoint["etag"] = moved["eTag"]
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
            "oldValue": state["oldValues"].get(column),
            "newValue": value,
            "changedAt": _timestamp(),
            "reviewer": reviewer,
        } for column, value in column_values.items())
        document.pop("sharePointStage", None)
        return document

    def refresh(self, document: dict) -> dict:
        sharepoint = document.get("sharePoint")
        if not isinstance(sharepoint, dict):
            raise WritebackError("Document is missing SharePoint identity metadata")
        current = self.client.refresh_item(
            sharepoint["driveId"],
            sharepoint["driveItemId"],
        )
        for key in ("etag", "listItemEtag", "existingColumns", "parentReference", "webUrl"):
            if current.get(key) is not None:
                sharepoint[key] = current[key]
        state = document.get("sharePointWriteback")
        if isinstance(state, dict):
            state["oldValues"] = {
                column: (sharepoint.get("existingColumns") or {}).get(column)
                for column in state.get("oldValues", {})
            }
        return document

    def _column_values(self, existing_columns: dict, approved: dict) -> dict:
        values = {}
        labels = {
            "businessArea": "Business area",
            "audience": "Audience",
            "language": "Language",
            "author": "Author",
            "countryOfOrigin": "Country of origin",
        }
        for field, value in approved.items():
            configured_column = self.column_map.get(field)
            if configured_column:
                values[configured_column] = value
                continue
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
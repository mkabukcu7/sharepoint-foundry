from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
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

    def delete_item(self, drive_id: str, item_id: str, etag: str) -> None:
        ...

    def archive_item(
        self,
        drive_id: str,
        item_id: str,
        folder_name: str,
        etag: str,
        new_name: str | None = None,
    ) -> dict:
        ...

    def refresh_item(self, drive_id: str, item_id: str) -> dict:
        ...

    def find_version_candidate(self, folder_name: str, file_name: str) -> dict | None:
        ...

    def get_version_history(self, drive_id: str, item_id: str) -> list[dict]:
        ...

    def replace_content(self, drive_id: str, item_id: str, content: bytes, etag: str) -> dict:
        ...


class SharePointWritebackService:
    def __init__(
        self,
        client: DocumentStore,
        column_map: dict[str, str] | None = None,
        staging_folder: str = "Staging",
        reviewed_folder: str = "Reviewed",
        archive_folder: str = "Archive",
    ) -> None:
        self.client = client
        self.column_map = column_map or {}
        self.staging_folder = staging_folder
        self.reviewed_folder = reviewed_folder
        self.archive_folder = archive_folder

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

    def version_candidate(self, document: dict) -> dict | None:
        if document.get("sharePointStage", {}).get("status") != "staged":
            raise WritebackError("Only a staged document can be checked for a possible revision")
        return self.client.find_version_candidate(
            self.reviewed_folder,
            document.get("documentName", ""),
        )

    def set_version_action(self, document: dict, action: str) -> dict:
        if action == "new-document":
            document.pop("sharePointRevision", None)
            return document
        if action != "replace-existing":
            raise ValueError("Unsupported SharePoint version action")
        if document.get("sharePointStage", {}).get("status") != "staged":
            raise WritebackError("Only a staged document can replace an existing SharePoint document")
        source = document.get("sharePoint")
        if not isinstance(source, dict):
            raise WritebackError("Staged document is missing SharePoint identity metadata")
        if not source.get("etag"):
            raise WritebackError("Staged source is missing an ETag required for safe version approval")
        candidate = self.version_candidate(document)
        if candidate is None:
            raise WritebackError(
                f"No same-name document was found in {self.reviewed_folder}; keep this as a new document"
            )
        if (
            candidate.get("driveId") == source.get("driveId")
            and candidate.get("driveItemId") == source.get("driveItemId")
        ):
            raise WritebackError("The staged item cannot be its own replacement target")
        if not candidate.get("etag") or not candidate.get("driveItemId"):
            raise WritebackError("SharePoint replacement candidate is missing identity or ETag data")
        if not candidate.get("currentVersion"):
            raise WritebackError("SharePoint current version could not be verified; replacement was not planned")
        document["sharePointRevision"] = {
            "action": "replace-existing",
            "status": "pending-approval",
            "plannedAt": _timestamp(),
            "expectedEtag": candidate["etag"],
            "sourceExpectedEtag": source.get("etag"),
            "target": candidate,
        }
        return document

    def remove_staged(self, document: dict) -> None:
        sharepoint = document.get("sharePoint")
        if isinstance(sharepoint, dict):
            etag = sharepoint.get("etag")
            if not etag:
                current = self.client.refresh_item(
                    sharepoint["driveId"],
                    sharepoint["driveItemId"],
                )
                etag = current.get("etag")
                if not etag:
                    raise WritebackError("SharePoint item is missing an ETag required for safe cleanup")
                sharepoint["etag"] = etag
            self.client.delete_item(
                sharepoint["driveId"],
                sharepoint["driveItemId"],
                etag,
            )
        document.pop("sharePoint", None)
        document.pop("sharePointStage", None)
        document.pop("sharePointWritebackEnabled", None)

    def apply(
        self,
        document: dict,
        reviewer: str,
        reviewed_at: str,
        replacement_content: bytes | None = None,
    ) -> dict:
        sharepoint = document.get("sharePoint")
        if not isinstance(sharepoint, dict):
            raise WritebackError("Document is missing SharePoint identity metadata")

        existing = document.get("sharePointWriteback")
        if isinstance(existing, dict) and existing.get("status") == "applied":
            return document
        revision = document.get("sharePointRevision")
        replacing_existing = isinstance(revision, dict) and revision.get("action") == "replace-existing"
        target = revision.get("target") if replacing_existing else sharepoint
        if not isinstance(target, dict):
            raise WritebackError("SharePoint replacement plan is missing its target identity")
        if replacing_existing and (not isinstance(replacement_content, bytes) or not replacement_content):
            raise WritebackError("Approved SharePoint replacement is missing staged document content")

        metadata_applied = bool(existing.get("metadataApplied")) if isinstance(existing, dict) else False
        content_replaced = bool(existing.get("contentReplaced")) if isinstance(existing, dict) else False
        file_moved = (
            True
            if replacing_existing
            else bool(existing.get("fileMoved")) if isinstance(existing, dict) else False
        )
        list_item_etag = (
            target.get("listItemEtag")
            or (target.get("existingColumns") or {}).get("@odata.etag")
        )
        if (
            (not metadata_applied and not list_item_etag)
            or ((not file_moved or (replacing_existing and not content_replaced)) and not target.get("etag"))
        ):
            self.refresh(document)
            target = revision["target"] if replacing_existing else document["sharePoint"]

        review = document.get("metadataReview") or {}
        fields = review.get("fields") or {}
        proposed = {
            field: field_review.get("value")
            for field, field_review in fields.items()
            if isinstance(field_review, dict) and field_review.get("reviewDecision") in {"accepted", "edited"}
        }
        approved = dict(proposed)
        column_values = self._column_values(target.get("existingColumns") or {}, approved)
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
            "metadataApplied": metadata_applied,
            "contentReplaced": content_replaced,
            "fileMoved": file_moved,
            "destinationFolder": self.reviewed_folder,
            "versionRecorded": bool(existing.get("versionRecorded")) if isinstance(existing, dict) else False,
            "stageSourceArchived": bool(existing.get("stageSourceArchived")) if isinstance(existing, dict) else False,
            "archiveFolder": self.archive_folder,
            "stageSourceRetained": bool(existing.get("stageSourceRetained")) if isinstance(existing, dict) else False,
            "oldValues": (
                dict(existing.get("oldValues", {}))
                if isinstance(existing, dict)
                else {
                    column: (target.get("existingColumns") or {}).get(column)
                    for column in column_values
                }
            ),
        }
        document["sharePointWriteback"] = state
        try:
            if replacing_existing and not state["contentReplaced"]:
                current_source = self.client.refresh_item(sharepoint["driveId"], sharepoint["driveItemId"])
                if (
                    not revision.get("sourceExpectedEtag")
                    or current_source.get("etag") != revision.get("sourceExpectedEtag")
                ):
                    raise WritebackConflict(
                        "Staged source changed since version review; refresh classification and approve a new plan"
                    )
                current = self.client.refresh_item(target["driveId"], target["driveItemId"])
                if current.get("etag") != revision.get("expectedEtag"):
                    raise WritebackConflict(
                        "SharePoint item changed since version review; refresh and approve a new plan"
                    )
                self._update_identity(target, current)
                replaced = self.client.replace_content(
                    target["driveId"],
                    target["driveItemId"],
                    replacement_content,
                    target["etag"],
                )
                self._update_identity(target, replaced)
                state["contentReplaced"] = True
            if not state["metadataApplied"]:
                list_item_etag = (
                    target.get("listItemEtag")
                    or (target.get("existingColumns") or {}).get("@odata.etag")
                )
                if not list_item_etag:
                    raise WritebackError(
                        "SharePoint list item is missing an ETag required for safe metadata update"
                    )
                result = self.client.update_fields(
                    target["driveId"],
                    target["driveItemId"],
                    column_values,
                    list_item_etag,
                )
                state["metadataApplied"] = True
                self._update_identity(target, result)
                target.setdefault("existingColumns", {}).update(column_values)
            if not replacing_existing and not state["fileMoved"]:
                drive_etag = target.get("etag")
                if not drive_etag:
                    raise WritebackError("SharePoint item is missing an ETag required for safe move")
                moved = self.client.move_item(
                    target["driveId"],
                    target["driveItemId"],
                    self.reviewed_folder,
                    drive_etag,
                )
                state["fileMoved"] = True
                target["folderPath"] = self.reviewed_folder
                target["parentReference"] = moved.get("parentReference")
                target["webUrl"] = moved.get("webUrl", target.get("webUrl", ""))
                if moved.get("eTag"):
                    target["etag"] = moved["eTag"]
            if not state["versionRecorded"]:
                versions = self.client.get_version_history(target["driveId"], target["driveItemId"])
                latest = max(
                    versions,
                    key=lambda version: str(version.get("lastModifiedDateTime") or ""),
                    default=None,
                )
                if not latest or not latest.get("id"):
                    raise WritebackError("SharePoint did not report a version after approved write-back")
                if (
                    replacing_existing
                    and latest["id"] == revision["target"].get("currentVersion")
                ):
                    raise WritebackError(
                        "SharePoint content was replaced, but no new library version was reported; "
                        "the staged source was retained and not archived"
                    )
                state["sharePointVersion"] = latest
                target["version"] = latest["id"]
                state["versionRecorded"] = True
            if replacing_existing and not state["stageSourceArchived"] and not state["stageSourceRetained"]:
                try:
                    archived = self.client.archive_item(
                        sharepoint["driveId"],
                        sharepoint["driveItemId"],
                        self.archive_folder,
                        revision["sourceExpectedEtag"],
                        _archive_name(sharepoint.get("fileName") or document.get("documentName", "")),
                    )
                    state["stageSourceArchived"] = True
                    state["archivedAs"] = archived.get("name")
                    state["archiveWebUrl"] = archived.get("webUrl", "")
                except WritebackConflict as error:
                    state["stageSourceRetained"] = True
                    state["stageSourceCleanupError"] = str(error)
        except WritebackConflict as error:
            state.update({"status": "conflict", "error": str(error)})
            raise
        except Exception as error:
            state.update({"status": "failed", "error": str(error)})
            raise WritebackError(str(error)) from error

        state["status"] = "applied"
        state["error"] = None
        if replacing_existing:
            revision["status"] = "applied"
            revision["source"] = sharepoint
            document["sharePoint"] = target
            document["sharePointStage"] = {
                **(document.get("sharePointStage") or {}),
                "status": "revision-applied",
                "sourceRetained": state["stageSourceRetained"],
                "sourceArchived": state["stageSourceArchived"],
                "archivedAs": state.get("archivedAs"),
                "archiveFolder": self.archive_folder,
            }
        state["audit"].extend({
            "column": column,
            "oldValue": state["oldValues"].get(column),
            "newValue": value,
            "changedAt": _timestamp(),
            "reviewer": reviewer,
        } for column, value in column_values.items())
        if not replacing_existing:
            document.pop("sharePointStage", None)
        return document

    def refresh(self, document: dict) -> dict:
        sharepoint = document.get("sharePoint")
        if not isinstance(sharepoint, dict):
            raise WritebackError("Document is missing SharePoint identity metadata")
        revision = document.get("sharePointRevision")
        target = (
            revision.get("target")
            if isinstance(revision, dict) and revision.get("action") == "replace-existing"
            else sharepoint
        )
        if not isinstance(target, dict):
            raise WritebackError("SharePoint replacement plan is missing its target identity")
        current = self.client.refresh_item(
            target["driveId"],
            target["driveItemId"],
        )
        self._update_identity(target, current)
        state = document.get("sharePointWriteback")
        if isinstance(state, dict):
            state["oldValues"] = {
                column: (target.get("existingColumns") or {}).get(column)
                for column in state.get("oldValues", {})
            }
        return document

    @staticmethod
    def _update_identity(target: dict, current: dict) -> None:
        for key in (
            "etag",
            "listItemEtag",
            "existingColumns",
            "parentReference",
            "webUrl",
            "lastModifiedDateTime",
            "fileName",
        ):
            if current.get(key) is not None:
                target[key] = current[key]

    def _column_values(self, existing_columns: dict, approved: dict) -> dict:
        values = {}
        labels = {
            "businessArea": "Business area",
            "audience": "Audience",
            "language": "Language",
            "author": "Author",
            "countryOfOrigin": "Country of origin",
            "materialType": "Material type",
            "topics": "Topics",
            "businesses": "Businesses",
            "industries": "Industries",
            "geographies": "Geographies",
            "collections": "Collections",
            "languages": "Taxonomy languages",
        }
        for field, value in approved.items():
            column_value = _column_value(value)
            configured_column = self.column_map.get(field)
            if configured_column:
                values[configured_column] = column_value
                continue
            candidates = (labels.get(field, field), field)
            column = next((key for key in existing_columns if key.casefold() in {candidate.casefold() for candidate in candidates}), None)
            if column:
                values[column] = column_value
        return values


def _column_value(value: object) -> str:
    """Multi-value taxonomy selections are written to text columns as a semicolon-separated list."""
    if isinstance(value, list):
        return "; ".join(str(item).strip() for item in value if str(item).strip())
    return value if isinstance(value, str) else ("" if value is None else str(value))


def _archive_name(file_name: str) -> str:
    stem = Path(file_name).stem or "document"
    suffix = Path(file_name).suffix
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stem} (superseded {stamp}){suffix}"


def _idempotency_key(document_name: str, reviewed_at: str, approved: dict) -> str:
    payload = f"{document_name}|{reviewed_at}|{sorted(approved.items())}"
    return sha256(payload.encode("utf-8")).hexdigest()


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()
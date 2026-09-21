from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Protocol
from urllib.parse import quote
import os
from dataclasses import dataclass

import requests
from azure.core.credentials import TokenCredential
from azure.identity import AzureCliCredential, ClientSecretCredential, DefaultAzureCredential

GRAPH_ROOT = "https://graph.microsoft.com/v1.0"
GRAPH_SCOPE = "https://graph.microsoft.com/.default"
SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".pptx"}
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_BATCH_BYTES = 100 * 1024 * 1024


@dataclass(frozen=True)
class SharePointDocument:
    site_id: str
    drive_id: str
    drive_item_id: str
    file_name: str
    web_url: str
    etag: str | None
    created_datetime: str | None
    modified_datetime: str | None
    existing_columns: dict
    owner: dict | None
    author: object | None
    content: bytes
    local_path: Path


class HttpSession(Protocol):
    headers: dict[str, str]

    def get(self, url: str, timeout: int, allow_redirects: bool = True) -> object:
        ...

    def patch(self, url: str, json: dict, headers: dict[str, str], timeout: int) -> object:
        ...

    def post(self, url: str, json: dict, headers: dict[str, str], timeout: int) -> object:
        ...

    def put(self, url: str, data: bytes, headers: dict[str, str], timeout: int) -> object:
        ...

    def delete(self, url: str, headers: dict[str, str], timeout: int) -> object:
        ...


class SharePointClient:
    def __init__(
        self,
        hostname: str,
        site_path: str,
        library_name: str = "Documents",
        folder_path: str = "",
        excluded_folder_names: set[str] | None = None,
        credential: TokenCredential | None = None,
        session: HttpSession | None = None,
    ) -> None:
        self.hostname = hostname.strip().removeprefix("https://").rstrip("/")
        self.site_path = "/" + site_path.strip("/")
        self.library_name = library_name
        self.folder_path = folder_path.strip("/")
        self.excluded_folder_names = {
            name.casefold()
            for name in (excluded_folder_names or set())
        }
        self.credential = credential or _sharepoint_credential()
        self.session = session or requests.Session()

    def download_documents(self, destination: Path) -> list[SharePointDocument]:
        destination.mkdir(parents=True, exist_ok=True)
        token = self.credential.get_token(GRAPH_SCOPE).token
        self.session.headers["Authorization"] = f"Bearer {token}"

        site = self._get_json(self._site_url())
        site_id = site["id"]
        drives = self._get_json(f"{GRAPH_ROOT}/sites/{site_id}/drives").get("value", [])
        drive = next(
            (candidate for candidate in drives if candidate.get("name", "").casefold() == self.library_name.casefold()),
            None,
        )
        if drive is None:
            available = ", ".join(sorted(candidate.get("name", "") for candidate in drives)) or "none"
            raise ValueError(f"SharePoint library '{self.library_name}' was not found. Available libraries: {available}")

        folder_id = self._resolve_folder(drive["id"], self.folder_path) if self.folder_path else "root"
        items = self._drive_items(drive["id"], folder_id)
        supported = sorted(
            (item for item in items if "file" in item and Path(item.get("name", "")).suffix.lower() in SUPPORTED_EXTENSIONS),
            key=lambda item: item["name"].casefold(),
        )
        downloaded = []
        downloaded_names: set[str] = set()
        for item in supported:
            target = destination / Path(item["name"]).name
            if item["name"] in downloaded_names:
                raise ValueError(f"Duplicate SharePoint document name cannot be flattened safely: {target.name}")
            content = self._download(f"{GRAPH_ROOT}/drives/{drive['id']}/items/{item['id']}/content", target)
            fields = item.get("listItem", {}).get("fields", {})
            downloaded.append(SharePointDocument(
                site_id=site_id,
                drive_id=drive["id"],
                drive_item_id=item["id"],
                file_name=item["name"],
                web_url=item.get("webUrl", ""),
                etag=item.get("eTag"),
                created_datetime=item.get("createdDateTime"),
                modified_datetime=item.get("lastModifiedDateTime"),
                existing_columns=fields,
                owner=item.get("createdBy"),
                author=fields.get("Author") or item.get("createdBy"),
                content=content,
                local_path=target,
            ))
            downloaded_names.add(item["name"])
        return downloaded

    def list_folder_documents(self, folder_name: str) -> list[dict]:
        self._authorize()
        _, drive_id = self._site_and_drive()
        folder = self._workflow_folder(drive_id, folder_name)
        return [
            {
                "documentName": item["name"],
                "size": item.get("size"),
                "lastModifiedDateTime": item.get("lastModifiedDateTime"),
                "webUrl": item.get("webUrl", ""),
            }
            for item in sorted(
                (
                    item
                    for item in self._child_items(drive_id, folder["id"])
                    if "file" in item
                    and Path(item.get("name", "")).suffix.lower() in SUPPORTED_EXTENSIONS
                ),
                key=lambda item: item["name"].casefold(),
            )
        ]

    def download_folder_documents(
        self,
        destination: Path,
        folder_name: str,
        document_names: set[str],
    ) -> list[SharePointDocument]:
        if not document_names:
            raise ValueError("Select at least one SharePoint document")
        destination.mkdir(parents=True, exist_ok=True)
        self._authorize()
        site_id, drive_id = self._site_and_drive()
        folder = self._workflow_folder(drive_id, folder_name)
        items = {
            item.get("name", ""): item
            for item in self._child_items(drive_id, folder["id"])
            if "file" in item
        }
        missing = document_names.difference(items)
        if missing:
            raise ValueError(
                f"SharePoint {folder_name} no longer contains: {', '.join(sorted(missing))}"
            )

        downloaded = []
        total_bytes = 0
        for name in sorted(document_names, key=str.casefold):
            item = items[name]
            if Path(name).name != name or Path(name).suffix.lower() not in SUPPORTED_EXTENSIONS:
                raise ValueError(f"Unsupported or invalid SharePoint document: {name}")
            size = item.get("size")
            if isinstance(size, int) and (size <= 0 or size > MAX_FILE_BYTES):
                raise ValueError(f"{name} must be between 1 byte and 20 MB")
            if isinstance(size, int):
                total_bytes += size
                if total_bytes > MAX_BATCH_BYTES:
                    raise ValueError("Selected SharePoint documents exceed 100 MB")
            target = destination / name
            content = self._download(
                f"{GRAPH_ROOT}/drives/{drive_id}/items/{item['id']}/content",
                target,
            )
            if not content or len(content) > MAX_FILE_BYTES:
                target.unlink(missing_ok=True)
                raise ValueError(f"{name} must be between 1 byte and 20 MB")
            details = self._get_json(
                f"{GRAPH_ROOT}/drives/{drive_id}/items/{item['id']}?$expand=listItem($expand=fields)"
            )
            fields = details.get("listItem", {}).get("fields", {})
            downloaded.append(SharePointDocument(
                site_id=site_id,
                drive_id=drive_id,
                drive_item_id=details["id"],
                file_name=details.get("name", name),
                web_url=details.get("webUrl", ""),
                etag=details.get("eTag"),
                created_datetime=details.get("createdDateTime"),
                modified_datetime=details.get("lastModifiedDateTime"),
                existing_columns=fields,
                owner=details.get("createdBy"),
                author=fields.get("Author") or details.get("createdBy"),
                content=content,
                local_path=target,
            ))
        return downloaded

    def _resolve_folder(self, drive_id: str, folder_path: str) -> str:
        current_id = "root"
        for segment in folder_path.split("/"):
            children = self._child_items(drive_id, current_id)
            folder = next(
                (item for item in children if "folder" in item and item.get("name", "").casefold() == segment.casefold()),
                None,
            )
            if folder is None:
                raise ValueError(f"SharePoint folder '{folder_path}' was not found")
            current_id = folder["id"]
        return current_id

    def _site_url(self) -> str:
        if self.site_path == "/":
            return f"{GRAPH_ROOT}/sites/{self.hostname}"
        return f"{GRAPH_ROOT}/sites/{self.hostname}:{quote(self.site_path, safe='/')}"

    def _drive_items(self, drive_id: str, item_id: str = "root") -> list[dict]:
        items = self._child_items(drive_id, item_id)
        descendants = []
        for item in items:
            descendants.append(item)
            if "folder" in item and item.get("name", "").casefold() not in self.excluded_folder_names:
                descendants.extend(self._drive_items(drive_id, item["id"]))
        return descendants

    def _child_items(self, drive_id: str, item_id: str = "root") -> list[dict]:
        if item_id == "root":
            url = f"{GRAPH_ROOT}/drives/{drive_id}/root/children?$expand=listItem($expand=fields)"
        else:
            url = f"{GRAPH_ROOT}/drives/{drive_id}/items/{item_id}/children?$expand=listItem($expand=fields)"
        return self._paged_values(url)

    def _get_json(self, url: str) -> dict:
        response = self.session.get(url, timeout=30)
        response.raise_for_status()
        return response.json()

    def _paged_values(self, url: str) -> list[dict]:
        values = []
        next_url: str | None = url
        while next_url:
            page = self._get_json(next_url)
            values.extend(page.get("value", []))
            next_url = page.get("@odata.nextLink")
        return values

    def _download(self, url: str, target: Path) -> bytes:
        response = self.session.get(url, timeout=120, allow_redirects=True)
        response.raise_for_status()
        temporary_path = None
        try:
            with NamedTemporaryFile("wb", dir=target.parent, prefix=f".{target.name}.", suffix=".tmp", delete=False) as temporary:
                content = response.content
                temporary.write(content)
                temporary_path = Path(temporary.name)
            temporary_path.replace(target)
        finally:
            if temporary_path:
                temporary_path.unlink(missing_ok=True)
        return content

    def update_fields(self, drive_id: str, item_id: str, fields: dict, etag: str) -> dict:
        token = self.credential.get_token(GRAPH_SCOPE).token
        response = self.session.patch(
            f"{GRAPH_ROOT}/drives/{drive_id}/items/{item_id}/listItem/fields",
            json=fields,
            headers={"Authorization": f"Bearer {token}", "If-Match": etag, "Content-Type": "application/json"},
            timeout=30,
        )
        if getattr(response, "status_code", 200) == 412:
            from backend.app.services.writeback import WritebackConflict
            raise WritebackConflict("SharePoint item changed since it was read; refresh before retrying")
        response.raise_for_status()
        payload = response.json()
        current = self.refresh_item(drive_id, item_id)
        current["listItemEtag"] = (
            payload.get("@odata.etag")
            or getattr(response, "headers", {}).get("ETag")
            or current.get("listItemEtag")
        )
        return current

    def refresh_item(self, drive_id: str, item_id: str) -> dict:
        self._authorize()
        details = self._get_json(
            f"{GRAPH_ROOT}/drives/{drive_id}/items/{item_id}?$expand=listItem($expand=fields)"
        )
        fields = details.get("listItem", {}).get("fields", {})
        return {
            "etag": details.get("eTag"),
            "listItemEtag": fields.get("@odata.etag"),
            "existingColumns": fields,
            "parentReference": details.get("parentReference"),
            "webUrl": details.get("webUrl", ""),
        }

    def ensure_folder(self, drive_id: str, folder_name: str) -> dict:
        self._authorize()
        parent_id = self._resolve_folder(drive_id, self.folder_path) if self.folder_path else "root"
        children = self._child_items(drive_id, parent_id)
        existing = next(
            (item for item in children if "folder" in item and item.get("name", "").casefold() == folder_name.casefold()),
            None,
        )
        if existing:
            return existing
        parent_path = "root" if parent_id == "root" else f"items/{parent_id}"
        response = self.session.post(
            f"{GRAPH_ROOT}/drives/{drive_id}/{parent_path}/children",
            json={"name": folder_name, "folder": {}, "@microsoft.graph.conflictBehavior": "fail"},
            headers=self._headers(),
            timeout=30,
        )
        if getattr(response, "status_code", 200) == 409:
            existing = next(
                (
                    item
                    for item in self._child_items(drive_id, parent_id)
                    if "folder" in item and item.get("name", "").casefold() == folder_name.casefold()
                ),
                None,
            )
            if existing:
                return existing
        response.raise_for_status()
        return response.json()

    def _workflow_folder(self, drive_id: str, folder_name: str) -> dict:
        parent_id = self._resolve_folder(drive_id, self.folder_path) if self.folder_path else "root"
        folder = next(
            (
                item
                for item in self._child_items(drive_id, parent_id)
                if "folder" in item and item.get("name", "").casefold() == folder_name.casefold()
            ),
            None,
        )
        if folder is None:
            raise ValueError(f"SharePoint folder '{folder_name}' was not found")
        return folder

    def upload_document(self, file_name: str, content: bytes, folder_name: str) -> dict:
        self._authorize()
        site_id, drive_id = self._site_and_drive()
        folder = self.ensure_folder(drive_id, folder_name)
        existing = next(
            (
                item
                for item in self._child_items(drive_id, folder["id"])
                if item.get("name", "").casefold() == file_name.casefold()
            ),
            None,
        )
        if existing:
            raise ValueError(f"SharePoint {folder_name} already contains '{file_name}'")
        response = self.session.put(
            f"{GRAPH_ROOT}/drives/{drive_id}/items/{folder['id']}:/{quote(file_name, safe='')}:/content",
            data=content,
            headers=self._headers("application/octet-stream"),
            timeout=120,
        )
        response.raise_for_status()
        item = response.json()
        details = self._get_json(
            f"{GRAPH_ROOT}/drives/{drive_id}/items/{item['id']}?$expand=listItem($expand=fields)"
        )
        return {
            "siteId": site_id,
            "driveId": drive_id,
            "driveItemId": details["id"],
            "fileName": details.get("name", file_name),
            "webUrl": details.get("webUrl", ""),
            "etag": details.get("eTag"),
            "listItemEtag": details.get("listItem", {}).get("fields", {}).get("@odata.etag"),
            "existingColumns": details.get("listItem", {}).get("fields", {}),
            "parentReference": details.get("parentReference"),
            "folderPath": folder_name,
        }

    def move_item(self, drive_id: str, item_id: str, folder_name: str, etag: str) -> dict:
        folder = self.ensure_folder(drive_id, folder_name)
        response = self.session.patch(
            f"{GRAPH_ROOT}/drives/{drive_id}/items/{item_id}",
            json={"parentReference": {"id": folder["id"]}},
            headers={**self._headers(), "If-Match": etag},
            timeout=30,
        )
        if getattr(response, "status_code", 200) == 412:
            from backend.app.services.writeback import WritebackConflict
            raise WritebackConflict("SharePoint item changed before it could be moved; refresh before retrying")
        response.raise_for_status()
        result = response.json()
        result["destinationFolder"] = folder_name
        return result

    def delete_item(self, drive_id: str, item_id: str, etag: str = "*") -> None:
        response = self.session.delete(
            f"{GRAPH_ROOT}/drives/{drive_id}/items/{item_id}",
            headers={**self._headers(), "If-Match": etag},
            timeout=30,
        )
        response.raise_for_status()

    def list_columns(self) -> list[dict]:
        self._authorize()
        site_id, drive_id = self._site_and_drive()
        list_resource = self._get_json(f"{GRAPH_ROOT}/drives/{drive_id}/list")
        columns = self._paged_values(f"{GRAPH_ROOT}/sites/{site_id}/lists/{list_resource['id']}/columns")
        return [
            {
                "displayName": column.get("displayName", ""),
                "name": column.get("name", ""),
                "type": next(
                    (
                        field_type
                        for field_type in (
                            "text",
                            "choice",
                            "personOrGroup",
                            "lookup",
                            "term",
                            "number",
                            "dateTime",
                            "boolean",
                        )
                        if field_type in column
                    ),
                    "unknown",
                ),
            }
            for column in columns
            if not column.get("readOnly")
        ]

    def ensure_text_columns(self, columns: list[tuple[str, str, str]]) -> dict[str, str]:
        self._authorize()
        site_id, drive_id = self._site_and_drive()
        list_resource = self._get_json(f"{GRAPH_ROOT}/drives/{drive_id}/list")
        existing = self.list_columns()
        result: dict[str, str] = {}
        for application_field, display_name, internal_name in columns:
            column = next(
                (
                    candidate
                    for candidate in existing
                    if candidate["displayName"].casefold() == display_name.casefold()
                    or candidate["name"].casefold() == internal_name.casefold()
                ),
                None,
            )
            if column is None:
                response = self.session.post(
                    f"{GRAPH_ROOT}/sites/{site_id}/lists/{list_resource['id']}/columns",
                    json={
                        "name": internal_name,
                        "displayName": display_name,
                        "text": {
                            "allowMultipleLines": False,
                            "appendChangesToExistingText": False,
                            "linesForEditing": 0,
                            "maxLength": 255,
                        },
                        "required": False,
                        "enforceUniqueValues": False,
                        "hidden": False,
                        "indexed": False,
                    },
                    headers=self._headers(),
                    timeout=30,
                )
                response.raise_for_status()
                created = response.json()
                column = {
                    "displayName": created["displayName"],
                    "name": created["name"],
                    "type": "text",
                }
                existing.append(column)
            if column["type"] != "text":
                raise ValueError(f"SharePoint column '{display_name}' exists but is not a text column")
            result[application_field] = column["name"]
        return result

    def _site_and_drive(self) -> tuple[str, str]:
        site = self._get_json(self._site_url())
        site_id = site["id"]
        drives = self._get_json(f"{GRAPH_ROOT}/sites/{site_id}/drives").get("value", [])
        drive = next(
            (candidate for candidate in drives if candidate.get("name", "").casefold() == self.library_name.casefold()),
            None,
        )
        if drive is None:
            available = ", ".join(sorted(candidate.get("name", "") for candidate in drives)) or "none"
            raise ValueError(f"SharePoint library '{self.library_name}' was not found. Available libraries: {available}")
        return site_id, drive["id"]

    def _headers(self, content_type: str = "application/json") -> dict[str, str]:
        token = self.credential.get_token(GRAPH_SCOPE).token
        return {"Authorization": f"Bearer {token}", "Content-Type": content_type}

    def _authorize(self) -> None:
        self.session.headers.update(self._headers())


def _sharepoint_credential() -> TokenCredential:
    import os

    mode = os.getenv("SHAREPOINT_CREDENTIAL_MODE", "default").strip().lower()
    if mode == "azure_cli":
        return AzureCliCredential()
    if mode != "default":
        raise ValueError(f"Unsupported SHAREPOINT_CREDENTIAL_MODE: {mode}")
    tenant_id = os.getenv("AZURE_TENANT_ID")
    client_id = os.getenv("AZURE_CLIENT_ID")
    client_secret = os.getenv("AZURE_CLIENT_SECRET")
    if tenant_id and client_id and client_secret:
        return ClientSecretCredential(tenant_id, client_id, client_secret)
    return DefaultAzureCredential()
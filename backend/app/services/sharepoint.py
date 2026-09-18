from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Protocol
from urllib.parse import quote
import os
from dataclasses import dataclass

import requests
from azure.core.credentials import TokenCredential
from azure.identity import ClientSecretCredential, DefaultAzureCredential

GRAPH_ROOT = "https://graph.microsoft.com/v1.0"
GRAPH_SCOPE = "https://graph.microsoft.com/.default"
SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".pptx"}


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


class SharePointClient:
    def __init__(
        self,
        hostname: str,
        site_path: str,
        library_name: str = "Documents",
        folder_path: str = "",
        credential: TokenCredential | None = None,
        session: HttpSession | None = None,
    ) -> None:
        self.hostname = hostname.strip().removeprefix("https://").rstrip("/")
        self.site_path = "/" + site_path.strip("/")
        self.library_name = library_name
        self.folder_path = folder_path.strip("/")
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

    def _resolve_folder(self, drive_id: str, folder_path: str) -> str:
        current_id = "root"
        for segment in folder_path.split("/"):
            children = self._drive_items(drive_id, current_id)
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
        if item_id == "root":
            url = f"{GRAPH_ROOT}/drives/{drive_id}/root/children?$expand=listItem($expand=fields)"
        else:
            url = f"{GRAPH_ROOT}/drives/{drive_id}/items/{item_id}/children?$expand=listItem($expand=fields)"
        items = self._paged_values(url)
        descendants = []
        for item in items:
            descendants.append(item)
            if "folder" in item:
                descendants.extend(self._drive_items(drive_id, item["id"]))
        return descendants

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
        return {"etag": getattr(response, "headers", {}).get("ETag")}


def _sharepoint_credential() -> TokenCredential:
    import os

    tenant_id = os.getenv("AZURE_TENANT_ID")
    client_id = os.getenv("AZURE_CLIENT_ID")
    client_secret = os.getenv("AZURE_CLIENT_SECRET")
    if tenant_id and client_id and client_secret:
        return ClientSecretCredential(tenant_id, client_id, client_secret)
    return DefaultAzureCredential()
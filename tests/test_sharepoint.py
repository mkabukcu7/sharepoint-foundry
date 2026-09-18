import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app.services.sharepoint import SharePointClient, SharePointDocument
from backend.scripts.sync_sharepoint import run_sharepoint_ingestion


class FakeCredential:
    def get_token(self, scope: str) -> object:
        if scope != "https://graph.microsoft.com/.default":
            raise AssertionError(f"Unexpected scope: {scope}")
        return type("AccessToken", (), {"token": "managed-login-token"})()


class FakeResponse:
    def __init__(self, payload: dict | None = None, content: bytes = b"") -> None:
        self.payload = payload
        self.content = content

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload or {}


class FakeSession:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.requested_urls: list[str] = []
        self.patch_calls: list[tuple[str, dict, dict]] = []

    def get(self, url: str, timeout: int, allow_redirects: bool = True) -> FakeResponse:
        self.requested_urls.append(url)
        if url.endswith("/sites/example.sharepoint.com:/sites/Knowledge"):
            return FakeResponse({"id": "site-id"})
        if url.endswith("/sites/site-id/drives"):
            return FakeResponse({"value": [{"id": "drive-id", "name": "Documents"}]})
        if url.startswith("https://graph.microsoft.com/v1.0/drives/drive-id/root/children"):
            return FakeResponse({
                "value": [
                    {
                        "id": "pdf-id",
                        "name": "guide.pdf",
                        "file": {},
                        "webUrl": "https://example.sharepoint.com/guide.pdf",
                        "eTag": '"etag-pdf"',
                        "createdDateTime": "2026-01-02T03:04:05Z",
                        "lastModifiedDateTime": "2026-02-03T04:05:06Z",
                        "createdBy": {"user": {"displayName": "Owner"}},
                        "listItem": {"fields": {"Author": {"LookupValue": "Author"}, "Topic": "Claims"}},
                    },
                    {"id": "text-id", "name": "notes.txt", "file": {}},
                ],
                "@odata.nextLink": "https://graph.microsoft.com/page-2",
            })
        if url == "https://graph.microsoft.com/page-2":
            return FakeResponse({"value": [{"id": "docx-id", "name": "policy.docx", "file": {}}]})
        if url.endswith("/drives/drive-id/items/pdf-id/content"):
            return FakeResponse(content=b"pdf")
        if url.endswith("/drives/drive-id/items/docx-id/content"):
            return FakeResponse(content=b"docx")
        raise AssertionError(f"Unexpected URL: {url}")

    def patch(self, url: str, json: dict, headers: dict[str, str], timeout: int) -> FakeResponse:
        self.patch_calls.append((url, json, headers))
        return FakeResponse()


class SharePointClientTests(unittest.TestCase):
    def test_download_documents_uses_graph_identity_and_pagination(self) -> None:
        session = FakeSession()
        client = SharePointClient(
            hostname="example.sharepoint.com",
            site_path="/sites/Knowledge",
            library_name="Documents",
            credential=FakeCredential(),
            session=session,
        )

        with tempfile.TemporaryDirectory() as directory:
            downloaded = client.download_documents(Path(directory))

            self.assertEqual([document.file_name for document in downloaded], ["guide.pdf", "policy.docx"])
            self.assertEqual((Path(directory) / "guide.pdf").read_bytes(), b"pdf")
            self.assertEqual((Path(directory) / "policy.docx").read_bytes(), b"docx")
            guide = downloaded[0]
            self.assertEqual(guide.site_id, "site-id")
            self.assertEqual(guide.drive_id, "drive-id")
            self.assertEqual(guide.drive_item_id, "pdf-id")
            self.assertEqual(guide.web_url, "https://example.sharepoint.com/guide.pdf")
            self.assertEqual(guide.etag, '"etag-pdf"')
            self.assertEqual(guide.created_datetime, "2026-01-02T03:04:05Z")
            self.assertEqual(guide.modified_datetime, "2026-02-03T04:05:06Z")
            self.assertEqual(guide.existing_columns["Topic"], "Claims")
            self.assertEqual(guide.owner, {"user": {"displayName": "Owner"}})
            self.assertEqual(guide.author, {"LookupValue": "Author"})
            self.assertEqual(guide.content, b"pdf")

        self.assertEqual(session.headers["Authorization"], "Bearer managed-login-token")
        self.assertIn("https://graph.microsoft.com/page-2", session.requested_urls)

    def test_root_site_uses_hostname_endpoint(self) -> None:
        client = SharePointClient(
            hostname="example.sharepoint.com",
            site_path="/",
            credential=FakeCredential(),
            session=FakeSession(),
        )

        self.assertEqual(client._site_url(), "https://graph.microsoft.com/v1.0/sites/example.sharepoint.com")

    def test_update_fields_uses_etag_concurrency(self) -> None:
        session = FakeSession()
        client = SharePointClient(
            hostname="example.sharepoint.com",
            site_path="/sites/Knowledge",
            credential=FakeCredential(),
            session=session,
        )

        client.update_fields("drive-id", "pdf-id", {"Business area": "Claims"}, '"etag-1"')

        url, fields, headers = session.patch_calls[0]
        self.assertTrue(url.endswith("/drives/drive-id/items/pdf-id/listItem/fields"))
        self.assertEqual(fields, {"Business area": "Claims"})
        self.assertEqual(headers["If-Match"], '"etag-1"')

    def test_sync_passes_downloaded_documents_to_foundry_ingestion(self) -> None:
        class Source:
            def download_documents(self, destination: Path) -> list[SharePointDocument]:
                destination.mkdir(parents=True, exist_ok=True)
                path = destination / "guide.pdf"
                path.write_bytes(b"pdf")
                return [SharePointDocument(
                    site_id="site-id",
                    drive_id="drive-id",
                    drive_item_id="item-id",
                    file_name="guide.pdf",
                    web_url="https://example.sharepoint.com/guide.pdf",
                    etag=None,
                    created_datetime=None,
                    modified_datetime=None,
                    existing_columns={},
                    owner=None,
                    author=None,
                    content=b"pdf",
                    local_path=path,
                )]

        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "documents"
            data_path = Path(directory) / "metadata.json"
            with patch("backend.scripts.sync_sharepoint.run_ingestion", return_value=[{"documentName": "guide.pdf"}]) as ingest:
                result = run_sharepoint_ingestion(destination, data_path, Source())
                ingest.assert_called_once_with(destination, data_path, {"guide.pdf"}, replace_existing=True)

        self.assertEqual(result[0]["documentName"], "guide.pdf")
        self.assertEqual(result[0]["sharePoint"]["driveItemId"], "item-id")


if __name__ == "__main__":
    unittest.main()
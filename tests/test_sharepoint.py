import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app.services.sharepoint import SharePointClient, SharePointDocument, _sharepoint_credential
from backend.scripts.sync_sharepoint import run_sharepoint_ingestion


class FakeCredential:
    def get_token(self, scope: str) -> object:
        if scope != "https://graph.microsoft.com/.default":
            raise AssertionError(f"Unexpected scope: {scope}")
        return type("AccessToken", (), {"token": "managed-login-token"})()


class FakeResponse:
    def __init__(
        self,
        payload: dict | None = None,
        content: bytes = b"",
        status_code: int = 200,
        headers: dict | None = None,
    ) -> None:
        self.payload = payload
        self.content = content
        self.status_code = status_code
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload or {}


class FakeSession:
    def __init__(
        self,
        include_reviewed: bool = False,
        include_staging: bool = False,
        duplicate_case_variant: bool = False,
    ) -> None:
        self.headers: dict[str, str] = {}
        self.include_reviewed = include_reviewed
        self.include_staging = include_staging
        self.duplicate_case_variant = duplicate_case_variant
        self.requested_urls: list[str] = []
        self.patch_calls: list[tuple[str, dict, dict]] = []
        self.post_calls: list[tuple[str, dict]] = []
        self.put_calls: list[tuple[str, bytes]] = []
        self.delete_calls: list[str] = []

    def get(self, url: str, timeout: int, allow_redirects: bool = True) -> FakeResponse:
        self.requested_urls.append(url)
        if url.endswith("/sites/example.sharepoint.com:/sites/Knowledge"):
            return FakeResponse({"id": "site-id"})
        if url.endswith("/sites/site-id/drives"):
            return FakeResponse({"value": [{"id": "drive-id", "name": "Documents"}]})
        if url.endswith("/drives/drive-id/list"):
            return FakeResponse({"id": "list-id"})
        if url.endswith("/sites/site-id/lists/list-id/columns"):
            return FakeResponse({"value": [
                {"displayName": "Business Area", "name": "BusinessArea", "text": {}, "readOnly": False},
                {"displayName": "Created", "name": "Created", "dateTime": {}, "readOnly": True},
            ]})
        if url.startswith("https://graph.microsoft.com/v1.0/drives/drive-id/root/children"):
            items = [
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
                ]
            if self.duplicate_case_variant:
                items.append({"id": "pdf-id-2", "name": "GUIDE.PDF", "file": {}})
            if self.include_reviewed:
                items.append({"id": "Reviewed-id", "name": "Reviewed", "folder": {}})
            if self.include_staging:
                items.append({"id": "Staging-id", "name": "Staging", "folder": {}})
            return FakeResponse({
                "value": items,
                "@odata.nextLink": "https://graph.microsoft.com/page-2",
            })
        if url == "https://graph.microsoft.com/page-2":
            return FakeResponse({"value": [{"id": "docx-id", "name": "policy.docx", "file": {}}]})
        if "/items/Staging-id/children" in url:
            return FakeResponse({"value": [{
                "id": "incoming-id",
                "name": "incoming-guide.docx",
                "file": {},
                "size": 42,
                "lastModifiedDateTime": "2026-09-21T12:00:00Z",
                "webUrl": "https://example.sharepoint.com/Staging/incoming-guide.docx",
            }]})
        if "/items/Reviewed-id/children" in url:
            return FakeResponse({"value": []})
        if "/items/incoming-id?$expand=" in url:
            return FakeResponse({
                "id": "incoming-id",
                "name": "incoming-guide.docx",
                "webUrl": "https://example.sharepoint.com/Staging/incoming-guide.docx",
                "eTag": '"incoming-etag"',
                "createdDateTime": "2026-09-20T12:00:00Z",
                "lastModifiedDateTime": "2026-09-21T12:00:00Z",
                "createdBy": {"user": {"displayName": "Owner"}},
                "listItem": {"fields": {"@odata.etag": '"incoming-list-etag"', "BusinessArea": None}},
                "parentReference": {"id": "Staging-id", "name": "Staging"},
            })
        if "/items/staged-id?$expand=" in url:
            return FakeResponse({
                "id": "staged-id",
                "name": "new-guide.pdf",
                "webUrl": "https://example.sharepoint.com/Staging/new-guide.pdf",
                "eTag": '"staged-etag"',
                "listItem": {"fields": {"@odata.etag": '"staged-list-etag"', "BusinessArea": None}},
                "parentReference": {"id": "Staging-id"},
            })
        if "/items/pdf-id?$expand=" in url:
            return FakeResponse({
                "id": "pdf-id",
                "webUrl": "https://example.sharepoint.com/guide.pdf",
                "eTag": '"etag-current"',
                "listItem": {"fields": {"@odata.etag": '"list-etag-updated"', "Business area": "Claims"}},
                "parentReference": {"id": "root"},
            })
        if url.endswith("/drives/drive-id/items/pdf-id/content"):
            return FakeResponse(content=b"pdf")
        if url.endswith("/drives/drive-id/items/docx-id/content"):
            return FakeResponse(content=b"docx")
        if url.endswith("/drives/drive-id/items/incoming-id/content"):
            return FakeResponse(content=b"incoming docx")
        raise AssertionError(f"Unexpected URL: {url}")

    def patch(self, url: str, json: dict, headers: dict[str, str], timeout: int) -> FakeResponse:
        self.patch_calls.append((url, json, headers))
        if url.endswith("/drives/drive-id/items/pdf-id"):
            return FakeResponse({
                "id": "pdf-id",
                "name": "guide.pdf",
                "webUrl": "https://example.sharepoint.com/Reviewed/guide.pdf",
                "eTag": '"etag-moved"',
                "parentReference": {"id": "Reviewed-id"},
            })
        return FakeResponse(headers={"ETag": '"etag-updated"'})

    def post(self, url: str, json: dict, headers: dict[str, str], timeout: int) -> FakeResponse:
        self.post_calls.append((url, json))
        if url.endswith("/sites/site-id/lists/list-id/columns"):
            internal_name = json["displayName"].replace(" ", "")
            return FakeResponse({"displayName": json["displayName"], "name": internal_name, "text": {}})
        return FakeResponse({"id": f"{json['name']}-id", "name": json["name"], "folder": {}})

    def put(self, url: str, data: bytes, headers: dict[str, str], timeout: int) -> FakeResponse:
        self.put_calls.append((url, data))
        return FakeResponse({"id": "staged-id", "name": "new-guide.pdf"})

    def delete(self, url: str, headers: dict[str, str], timeout: int) -> FakeResponse:
        self.delete_calls.append(url)
        return FakeResponse()


class SharePointClientTests(unittest.TestCase):
    def test_sharepoint_credential_supports_explicit_azure_cli_mode(self) -> None:
        with (
            patch.dict("os.environ", {"SHAREPOINT_CREDENTIAL_MODE": "azure_cli"}, clear=True),
            patch("backend.app.services.sharepoint.AzureCliCredential") as credential,
        ):
            selected = _sharepoint_credential()

        self.assertIs(selected, credential.return_value)

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

    def test_download_documents_rejects_case_insensitive_duplicate_names(self) -> None:
        session = FakeSession(duplicate_case_variant=True)
        client = SharePointClient(
            hostname="example.sharepoint.com",
            site_path="/sites/Knowledge",
            library_name="Documents",
            credential=FakeCredential(),
            session=session,
        )

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "Duplicate SharePoint document name"):
                client.download_documents(Path(directory))

    def test_root_site_uses_hostname_endpoint(self) -> None:
        client = SharePointClient(
            hostname="example.sharepoint.com",
            site_path="/",
            credential=FakeCredential(),
            session=FakeSession(),
        )

        self.assertEqual(client._site_url(), "https://graph.microsoft.com/v1.0/sites/example.sharepoint.com")

    def test_download_skips_workflow_folders(self) -> None:
        session = FakeSession(include_reviewed=True)
        client = SharePointClient(
            hostname="example.sharepoint.com",
            site_path="/sites/Knowledge",
            excluded_folder_names={"Staging", "Reviewed"},
            credential=FakeCredential(),
            session=session,
        )

        with tempfile.TemporaryDirectory() as directory:
            downloaded = client.download_documents(Path(directory))

        self.assertEqual([document.file_name for document in downloaded], ["guide.pdf", "policy.docx"])
        self.assertFalse(any("/items/Reviewed-id/children" in url for url in session.requested_urls))

    def test_update_fields_uses_etag_concurrency(self) -> None:
        session = FakeSession()
        client = SharePointClient(
            hostname="example.sharepoint.com",
            site_path="/sites/Knowledge",
            credential=FakeCredential(),
            session=session,
        )

        result = client.update_fields("drive-id", "pdf-id", {"Business area": "Claims"}, '"list-etag-1"')

        url, fields, headers = session.patch_calls[0]
        self.assertTrue(url.endswith("/drives/drive-id/items/pdf-id/listItem/fields"))
        self.assertEqual(fields, {"Business area": "Claims"})
        self.assertEqual(headers["If-Match"], '"list-etag-1"')
        self.assertEqual(result["etag"], '"etag-current"')
        self.assertEqual(result["listItemEtag"], '"etag-updated"')

    def test_list_columns_returns_only_writable_names_and_types(self) -> None:
        client = SharePointClient(
            hostname="example.sharepoint.com",
            site_path="/sites/Knowledge",
            credential=FakeCredential(),
            session=FakeSession(),
        )

        self.assertEqual(
            client.list_columns(),
            [{"displayName": "Business Area", "name": "BusinessArea", "type": "text"}],
        )

    def test_ensure_text_columns_reuses_existing_and_creates_missing_columns(self) -> None:
        session = FakeSession()
        client = SharePointClient(
            hostname="example.sharepoint.com",
            site_path="/sites/Knowledge",
            credential=FakeCredential(),
            session=session,
        )

        mapping = client.ensure_text_columns([
            ("businessArea", "Business Area", "BusinessArea"),
            ("audience", "Metadata Audience", "MetadataAudience"),
        ])

        self.assertEqual(mapping, {
            "businessArea": "BusinessArea",
            "audience": "MetadataAudience",
        })
        created = [payload for url, payload in session.post_calls if url.endswith("/columns")]
        self.assertEqual([payload["displayName"] for payload in created], ["Metadata Audience"])
        self.assertEqual(created[0]["name"], "MetadataAudience")

    def test_upload_creates_staging_folder_and_returns_sharepoint_identity(self) -> None:
        session = FakeSession()
        client = SharePointClient(
            hostname="example.sharepoint.com",
            site_path="/sites/Knowledge",
            credential=FakeCredential(),
            session=session,
        )

        identity = client.upload_document("new-guide.pdf", b"pdf", "Staging")

        self.assertEqual(identity["driveItemId"], "staged-id")
        self.assertEqual(identity["folderPath"], "Staging")
        self.assertEqual(session.post_calls[0][1]["name"], "Staging")
        self.assertIn("/items/Staging-id:/new-guide.pdf:/content", session.put_calls[0][0])

    def test_list_and_download_selected_staging_documents(self) -> None:
        client = SharePointClient(
            hostname="example.sharepoint.com",
            site_path="/sites/Knowledge",
            credential=FakeCredential(),
            session=FakeSession(include_staging=True),
        )

        listed = client.list_folder_documents("Staging")
        with tempfile.TemporaryDirectory() as directory:
            downloaded = client.download_folder_documents(
                Path(directory),
                "Staging",
                {"incoming-guide.docx"},
            )

            self.assertEqual((Path(directory) / "incoming-guide.docx").read_bytes(), b"incoming docx")

        self.assertEqual(listed[0]["documentName"], "incoming-guide.docx")
        self.assertEqual(listed[0]["size"], 42)
        self.assertEqual(downloaded[0].drive_item_id, "incoming-id")
        self.assertEqual(downloaded[0].existing_columns["@odata.etag"], '"incoming-list-etag"')

    def test_download_selected_staging_documents_rejects_missing_name(self) -> None:
        client = SharePointClient(
            hostname="example.sharepoint.com",
            site_path="/sites/Knowledge",
            credential=FakeCredential(),
            session=FakeSession(include_staging=True),
        )

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "no longer contains"):
                client.download_folder_documents(
                    Path(directory),
                    "Staging",
                    {"missing.pdf"},
                )

    def test_move_creates_reviewed_folder_and_updates_parent(self) -> None:
        session = FakeSession()
        client = SharePointClient(
            hostname="example.sharepoint.com",
            site_path="/sites/Knowledge",
            credential=FakeCredential(),
            session=session,
        )

        moved = client.move_item("drive-id", "pdf-id", "Reviewed", '"etag-updated"')

        self.assertEqual(moved["destinationFolder"], "Reviewed")
        self.assertEqual(moved["parentReference"]["id"], "Reviewed-id")
        _, payload, headers = session.patch_calls[-1]
        self.assertEqual(payload, {"parentReference": {"id": "Reviewed-id"}})
        self.assertEqual(headers["If-Match"], '"etag-updated"')

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
import unittest

from backend.app.services.writeback import SharePointWritebackService, WritebackConflict, WritebackError


def document() -> dict:
    return {
        "documentName": "guide.pdf",
        "sharePoint": {
            "driveId": "drive-id",
            "driveItemId": "item-id",
            "etag": '"etag-1"',
            "listItemEtag": '"list-etag-1"',
            "existingColumns": {
                "@odata.etag": '"list-etag-1"',
                "Business area": "Old area",
                "Audience": "Old audience",
            },
        },
        "metadataReview": {
            "fields": {
                "businessArea": {"value": "Claims", "reviewDecision": "edited"},
                "audience": {"value": "Advisor", "reviewDecision": "accepted"},
            }
        },
    }


class FakeStore:
    def __init__(
        self,
        failure: Exception | None = None,
        move_failure: Exception | None = None,
        refresh_result: dict | None = None,
        archive_failure: Exception | None = None,
    ) -> None:
        self.calls: list[tuple[str, str, dict, str]] = []
        self.move_calls: list[tuple[str, str, str, str]] = []
        self.upload_calls: list[tuple[str, bytes, str]] = []
        self.delete_calls: list[tuple[str, str, str]] = []
        self.archive_calls: list[tuple[str, str, str, str, str | None]] = []
        self.refresh_calls: list[tuple[str, str]] = []
        self.replace_calls: list[tuple[str, str, bytes, str]] = []
        self.version_calls: list[tuple[str, str]] = []
        self.failure = failure
        self.move_failure = move_failure
        self.archive_failure = archive_failure
        self.refresh_result = refresh_result

    def update_fields(self, drive_id: str, item_id: str, fields: dict, etag: str) -> dict:
        self.calls.append((drive_id, item_id, fields, etag))
        if self.failure:
            failure = self.failure
            self.failure = None
            raise failure
        if item_id == "target-item":
            return {"etag": '"etag-after-fields"', "listItemEtag": '"list-etag-after-fields"'}
        return {"etag": '"etag-2"', "listItemEtag": '"list-etag-2"'}

    def upload_document(self, file_name: str, content: bytes, folder_name: str) -> dict:
        self.upload_calls.append((file_name, content, folder_name))
        return {
            "driveId": "drive-id",
            "driveItemId": "staged-id",
            "etag": '"staged-etag"',
            "listItemEtag": '"staged-list-etag"',
            "existingColumns": {"Business area": None},
            "folderPath": folder_name,
        }

    def move_item(self, drive_id: str, item_id: str, folder_name: str, etag: str) -> dict:
        self.move_calls.append((drive_id, item_id, folder_name, etag))
        if self.move_failure:
            failure = self.move_failure
            self.move_failure = None
            raise failure
        return {
            "eTag": '"etag-3"',
            "webUrl": "https://example.sharepoint.com/Reviewed/guide.pdf",
            "parentReference": {"id": "reviewed-id"},
        }

    def delete_item(self, drive_id: str, item_id: str, etag: str) -> None:
        self.delete_calls.append((drive_id, item_id, etag))

    def archive_item(
        self,
        drive_id: str,
        item_id: str,
        folder_name: str,
        etag: str,
        new_name: str | None = None,
    ) -> dict:
        self.archive_calls.append((drive_id, item_id, folder_name, etag, new_name))
        if self.archive_failure:
            failure = self.archive_failure
            self.archive_failure = None
            raise failure
        return {
            "name": new_name,
            "webUrl": f"https://example.sharepoint.com/{folder_name}/{new_name}",
            "destinationFolder": folder_name,
        }

    def refresh_item(self, drive_id: str, item_id: str) -> dict:
        self.refresh_calls.append((drive_id, item_id))
        if item_id == "staged-item":
            return {"etag": '"staged-etag"', "listItemEtag": '"staged-list-etag"', "existingColumns": {}}
        if item_id == "target-item":
            return {
                "etag": '"etag-refreshed"',
                "listItemEtag": '"target-list-etag"',
                "existingColumns": {
                    "@odata.etag": '"target-list-etag"',
                    "Business area": "Current area",
                    "Audience": "Current audience",
                },
                "webUrl": "https://example.sharepoint.com/Reviewed/guide.pdf",
            }
        return self.refresh_result or {
            "etag": '"etag-refreshed"',
            "listItemEtag": '"list-etag-refreshed"',
            "existingColumns": {
                "@odata.etag": '"list-etag-refreshed"',
                "Business area": "Current area",
                "Audience": "Current audience",
            },
        }

    def find_version_candidate(self, folder_name: str, file_name: str) -> dict | None:
        self.version_calls.append((folder_name, file_name))
        return {
            "driveId": "drive-id",
            "driveItemId": "target-item",
            "fileName": file_name,
            "etag": '"etag-refreshed"',
            "listItemEtag": '"target-list-etag"',
            "existingColumns": {
                "@odata.etag": '"target-list-etag"',
                "Business area": "Current area",
                "Audience": "Current audience",
            },
            "webUrl": "https://example.sharepoint.com/Reviewed/guide.pdf",
            "folderPath": folder_name,
            "versions": [{"id": "1.0", "lastModifiedDateTime": "2026-09-21T00:00:00Z"}],
            "currentVersion": "1.0",
        }

    def get_version_history(self, drive_id: str, item_id: str) -> list[dict]:
        return [{"id": "2.0", "lastModifiedDateTime": "2026-09-22T00:00:00Z"}]

    def replace_content(self, drive_id: str, item_id: str, content: bytes, etag: str) -> dict:
        self.replace_calls.append((drive_id, item_id, content, etag))
        return {
            "etag": '"etag-after-content"',
            "listItemEtag": '"list-etag-after-content"',
            "existingColumns": {
                "@odata.etag": '"list-etag-after-content"',
                "Business area": "Current area",
                "Audience": "Current audience",
            },
            "webUrl": "https://example.sharepoint.com/Reviewed/guide.pdf",
        }


class WritebackTests(unittest.TestCase):
    def test_apply_records_approved_values_audit_and_is_idempotent(self) -> None:
        store = FakeStore()
        service = SharePointWritebackService(store)
        current = document()

        applied = service.apply(current, "sme@example.com", "2026-09-18T20:00:00+00:00")
        repeated = service.apply(applied, "sme@example.com", "2026-09-18T20:00:00+00:00")

        self.assertEqual(len(store.calls), 1)
        self.assertEqual(len(store.move_calls), 1)
        self.assertEqual(store.calls[0][2], {"Business area": "Claims", "Audience": "Advisor"})
        self.assertEqual(store.calls[0][3], '"list-etag-1"')
        self.assertEqual(repeated["sharePointWriteback"]["status"], "applied")
        self.assertEqual(len(repeated["sharePointWriteback"]["audit"]), 2)
        self.assertEqual(repeated["sharePointWriteback"]["audit"][0]["oldValue"], "Old area")
        self.assertEqual(repeated["sharePoint"]["etag"], '"etag-3"')
        self.assertEqual(repeated["sharePoint"]["folderPath"], "Reviewed")
        self.assertEqual(repeated["sharePoint"]["version"], "2.0")

    def test_staged_source_is_retained_when_archiving_conflicts(self) -> None:
        store = FakeStore(archive_failure=WritebackConflict("Staging item changed"))
        service = SharePointWritebackService(store)
        current = document()
        current["sharePoint"] = {
            "driveId": "drive-id",
            "driveItemId": "staged-item",
            "etag": '"staged-etag"',
            "listItemEtag": '"staged-list-etag"',
            "existingColumns": {},
            "folderPath": "Staging",
        }
        current["sharePointStage"] = {"status": "staged", "folder": "Staging"}
        service.set_version_action(current, "replace-existing")

        applied = service.apply(
            current,
            "sme@example.com",
            "2026-09-18T20:00:00+00:00",
            replacement_content=b"revised content",
        )

        writeback = applied["sharePointWriteback"]
        self.assertEqual(writeback["status"], "applied")
        self.assertFalse(writeback["stageSourceArchived"])
        self.assertTrue(writeback["stageSourceRetained"])
        self.assertIn("Staging item changed", writeback["stageSourceCleanupError"])
        self.assertEqual(store.delete_calls, [])

    def test_archive_folder_is_configurable(self) -> None:
        store = FakeStore()
        service = SharePointWritebackService(store, archive_folder="Superseded")
        current = document()
        current["sharePoint"] = {
            "driveId": "drive-id",
            "driveItemId": "staged-item",
            "etag": '"staged-etag"',
            "listItemEtag": '"staged-list-etag"',
            "existingColumns": {},
            "folderPath": "Staging",
        }
        current["sharePointStage"] = {"status": "staged", "folder": "Staging"}
        service.set_version_action(current, "replace-existing")

        applied = service.apply(
            current,
            "sme@example.com",
            "2026-09-18T20:00:00+00:00",
            replacement_content=b"revised content",
        )

        self.assertEqual(store.archive_calls[0][2], "Superseded")
        self.assertEqual(applied["sharePointStage"]["archiveFolder"], "Superseded")

    def test_approved_revision_replaces_target_content_and_records_sharepoint_version(self) -> None:
        store = FakeStore()
        service = SharePointWritebackService(store)
        current = document()
        current["sharePoint"] = {
            "driveId": "drive-id",
            "driveItemId": "staged-item",
            "etag": '"staged-etag"',
            "listItemEtag": '"staged-list-etag"',
            "existingColumns": {},
            "folderPath": "Staging",
        }
        current["sharePointStage"] = {"status": "staged", "folder": "Staging"}

        service.set_version_action(current, "replace-existing")
        applied = service.apply(
            current,
            "sme@example.com",
            "2026-09-18T20:00:00+00:00",
            replacement_content=b"revised content",
        )

        self.assertEqual(store.version_calls, [("Reviewed", "guide.pdf")])
        self.assertEqual(store.replace_calls, [("drive-id", "target-item", b"revised content", '"etag-refreshed"')])
        self.assertEqual(store.calls[0][1], "target-item")
        self.assertEqual(store.calls[0][3], '"list-etag-after-content"')
        self.assertEqual(store.move_calls, [])
        self.assertEqual(store.delete_calls, [])
        self.assertEqual(len(store.archive_calls), 1)
        archive_call = store.archive_calls[0]
        self.assertEqual(archive_call[:4], ("drive-id", "staged-item", "Archive", '"staged-etag"'))
        self.assertTrue(archive_call[4].startswith("guide (superseded "))
        self.assertTrue(archive_call[4].endswith(".pdf"))
        self.assertEqual(applied["sharePoint"]["driveItemId"], "target-item")
        self.assertEqual(applied["sharePoint"]["version"], "2.0")
        self.assertEqual(applied["sharePointWriteback"]["sharePointVersion"]["id"], "2.0")
        self.assertTrue(applied["sharePointWriteback"]["contentReplaced"])
        self.assertEqual(applied["sharePointRevision"]["status"], "applied")
        self.assertEqual(applied["sharePointRevision"]["source"]["driveItemId"], "staged-item")
        self.assertEqual(applied["sharePointStage"]["status"], "revision-applied")
        self.assertTrue(applied["sharePointStage"]["sourceArchived"])
        self.assertEqual(applied["sharePointStage"]["archiveFolder"], "Archive")
        self.assertFalse(applied["sharePointStage"]["sourceRetained"])

    def test_revision_stops_if_target_etag_changed_after_version_review(self) -> None:
        store = FakeStore()
        service = SharePointWritebackService(store)
        current = document()
        current["sharePoint"] = {
            "driveId": "drive-id",
            "driveItemId": "staged-item",
            "etag": '"staged-etag"',
            "listItemEtag": '"staged-list-etag"',
        }
        current["sharePointStage"] = {"status": "staged", "folder": "Staging"}
        service.set_version_action(current, "replace-existing")
        current["sharePointRevision"]["expectedEtag"] = '"stale-etag"'

        with self.assertRaises(WritebackConflict):
            service.apply(
                current,
                "sme@example.com",
                "2026-09-18T20:00:00+00:00",
                replacement_content=b"revised content",
            )

        self.assertEqual(store.replace_calls, [])
        self.assertEqual(current["sharePointWriteback"]["status"], "conflict")

    def test_etag_conflict_is_recorded(self) -> None:
        service = SharePointWritebackService(FakeStore(WritebackConflict("stale")))
        current = document()

        with self.assertRaises(WritebackConflict):
            service.apply(current, "sme@example.com", "2026-09-18T20:00:00+00:00")

        self.assertEqual(current["sharePointWriteback"]["status"], "conflict")
        self.assertEqual(current["sharePointWriteback"]["error"], "stale")

    def test_conflict_refresh_uses_current_list_and_drive_etags(self) -> None:
        store = FakeStore(WritebackConflict("stale"))
        service = SharePointWritebackService(store)
        current = document()

        with self.assertRaises(WritebackConflict):
            service.apply(current, "sme@example.com", "2026-09-18T20:00:00+00:00")
        service.refresh(current)
        applied = service.apply(current, "sme@example.com", "2026-09-18T20:00:00+00:00")

        self.assertEqual(store.refresh_calls, [("drive-id", "item-id")])
        self.assertEqual(store.calls[-1][3], '"list-etag-refreshed"')
        self.assertEqual(store.move_calls[-1][3], '"etag-2"')
        self.assertEqual(applied["sharePointWriteback"]["oldValues"]["Business area"], "Current area")

    def test_failed_writeback_can_retry(self) -> None:
        store = FakeStore(WritebackError("temporary failure"))
        service = SharePointWritebackService(store)
        current = document()

        with self.assertRaises(WritebackError):
            service.apply(current, "sme@example.com", "2026-09-18T20:00:00+00:00")
        retried = service.apply(current, "sme@example.com", "2026-09-18T20:00:00+00:00")

        self.assertEqual(len(store.calls), 2)
        self.assertEqual(len(store.move_calls), 1)
        self.assertEqual(retried["sharePointWriteback"]["status"], "applied")
        self.assertEqual(retried["sharePointWriteback"]["attempt"], 2)

    def test_move_retry_does_not_repeat_successful_metadata_update(self) -> None:
        store = FakeStore(move_failure=WritebackError("move unavailable"))
        service = SharePointWritebackService(store)
        current = document()

        with self.assertRaises(WritebackError):
            service.apply(current, "sme@example.com", "2026-09-18T20:00:00+00:00")
        retried = service.apply(current, "sme@example.com", "2026-09-18T20:00:00+00:00")

        self.assertEqual(len(store.calls), 1)
        self.assertEqual(len(store.move_calls), 2)
        self.assertTrue(retried["sharePointWriteback"]["metadataApplied"])
        self.assertTrue(retried["sharePointWriteback"]["fileMoved"])

    def test_stage_and_remove_uploaded_document(self) -> None:
        store = FakeStore()
        service = SharePointWritebackService(store, staging_folder="Incoming")
        current = {"documentName": "guide.pdf"}

        service.stage(current, b"pdf")

        self.assertEqual(store.upload_calls, [("guide.pdf", b"pdf", "Incoming")])
        self.assertEqual(current["sharePointStage"]["status"], "staged")
        self.assertTrue(current["sharePointWritebackEnabled"])
        service.remove_staged(current)
        self.assertEqual(store.delete_calls, [("drive-id", "staged-id", '"staged-etag"')])
        self.assertNotIn("sharePoint", current)
        self.assertNotIn("sharePointWritebackEnabled", current)

    def test_remove_staged_refreshes_missing_etag_before_delete(self) -> None:
        store = FakeStore()
        service = SharePointWritebackService(store)
        current = document()
        current["sharePoint"].pop("etag")

        service.remove_staged(current)

        self.assertEqual(store.refresh_calls, [("drive-id", "item-id")])
        self.assertEqual(store.delete_calls, [("drive-id", "item-id", '"etag-refreshed"')])

    def test_remove_staged_refuses_delete_when_refreshed_etag_is_missing(self) -> None:
        store = FakeStore(refresh_result={"listItemEtag": '"list-etag-refreshed"'})
        service = SharePointWritebackService(store)
        current = document()
        current["sharePoint"].pop("etag")

        with self.assertRaisesRegex(WritebackError, "ETag required for safe cleanup"):
            service.remove_staged(current)

        self.assertEqual(store.delete_calls, [])
        self.assertIn("sharePoint", current)

    def test_apply_refreshes_missing_concurrency_tokens(self) -> None:
        store = FakeStore()
        service = SharePointWritebackService(store)
        current = document()
        current["sharePoint"].pop("etag")
        current["sharePoint"].pop("listItemEtag")
        current["sharePoint"]["existingColumns"].pop("@odata.etag")

        service.apply(current, "sme@example.com", "2026-09-18T20:00:00+00:00")

        self.assertEqual(store.refresh_calls, [("drive-id", "item-id")])
        self.assertEqual(store.calls[0][3], '"list-etag-refreshed"')
        self.assertNotEqual(store.move_calls[0][3], "*")


if __name__ == "__main__":
    unittest.main()
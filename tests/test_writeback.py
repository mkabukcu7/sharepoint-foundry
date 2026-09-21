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
    ) -> None:
        self.calls: list[tuple[str, str, dict, str]] = []
        self.move_calls: list[tuple[str, str, str, str]] = []
        self.upload_calls: list[tuple[str, bytes, str]] = []
        self.delete_calls: list[tuple[str, str, str]] = []
        self.refresh_calls: list[tuple[str, str]] = []
        self.failure = failure
        self.move_failure = move_failure
        self.refresh_result = refresh_result

    def update_fields(self, drive_id: str, item_id: str, fields: dict, etag: str) -> dict:
        self.calls.append((drive_id, item_id, fields, etag))
        if self.failure:
            failure = self.failure
            self.failure = None
            raise failure
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

    def refresh_item(self, drive_id: str, item_id: str) -> dict:
        self.refresh_calls.append((drive_id, item_id))
        return self.refresh_result or {
            "etag": '"etag-refreshed"',
            "listItemEtag": '"list-etag-refreshed"',
            "existingColumns": {
                "@odata.etag": '"list-etag-refreshed"',
                "Business area": "Current area",
                "Audience": "Current audience",
            },
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
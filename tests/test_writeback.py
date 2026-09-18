import unittest

from backend.app.services.writeback import SharePointWritebackService, WritebackConflict, WritebackError


def document() -> dict:
    return {
        "documentName": "guide.pdf",
        "sharePoint": {
            "driveId": "drive-id",
            "driveItemId": "item-id",
            "etag": '"etag-1"',
            "existingColumns": {"Business area": "Old area", "Audience": "Old audience"},
        },
        "metadataReview": {
            "fields": {
                "businessArea": {"value": "Claims", "reviewDecision": "edited"},
                "audience": {"value": "Advisor", "reviewDecision": "accepted"},
            }
        },
    }


class FakeStore:
    def __init__(self, failure: Exception | None = None) -> None:
        self.calls: list[tuple[str, str, dict, str]] = []
        self.failure = failure

    def update_fields(self, drive_id: str, item_id: str, fields: dict, etag: str) -> dict:
        self.calls.append((drive_id, item_id, fields, etag))
        if self.failure:
            failure = self.failure
            self.failure = None
            raise failure
        return {"etag": '"etag-2"'}


class WritebackTests(unittest.TestCase):
    def test_apply_records_approved_values_audit_and_is_idempotent(self) -> None:
        store = FakeStore()
        service = SharePointWritebackService(store)
        current = document()

        applied = service.apply(current, "sme@example.com", "2026-09-18T20:00:00+00:00")
        repeated = service.apply(applied, "sme@example.com", "2026-09-18T20:00:00+00:00")

        self.assertEqual(len(store.calls), 1)
        self.assertEqual(store.calls[0][2], {"Business area": "Claims", "Audience": "Advisor"})
        self.assertEqual(store.calls[0][3], '"etag-1"')
        self.assertEqual(repeated["sharePointWriteback"]["status"], "applied")
        self.assertEqual(len(repeated["sharePointWriteback"]["audit"]), 2)
        self.assertEqual(repeated["sharePointWriteback"]["audit"][0]["oldValue"], "Old area")
        self.assertEqual(repeated["sharePoint"]["etag"], '"etag-2"')

    def test_etag_conflict_is_recorded(self) -> None:
        service = SharePointWritebackService(FakeStore(WritebackConflict("stale")))
        current = document()

        with self.assertRaises(WritebackConflict):
            service.apply(current, "sme@example.com", "2026-09-18T20:00:00+00:00")

        self.assertEqual(current["sharePointWriteback"]["status"], "conflict")
        self.assertEqual(current["sharePointWriteback"]["error"], "stale")

    def test_failed_writeback_can_retry(self) -> None:
        store = FakeStore(WritebackError("temporary failure"))
        service = SharePointWritebackService(store)
        current = document()

        with self.assertRaises(WritebackError):
            service.apply(current, "sme@example.com", "2026-09-18T20:00:00+00:00")
        retried = service.apply(current, "sme@example.com", "2026-09-18T20:00:00+00:00")

        self.assertEqual(len(store.calls), 2)
        self.assertEqual(retried["sharePointWriteback"]["status"], "applied")
        self.assertEqual(retried["sharePointWriteback"]["attempt"], 2)


if __name__ == "__main__":
    unittest.main()
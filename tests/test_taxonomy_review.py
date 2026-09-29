import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.services.reviews import (
    ALL_REVIEW_FIELDS,
    REVIEW_FIELDS,
    approve_metadata_review,
    metadata_review,
    resolve_classification_flag,
    unresolved_review_items,
    update_field_review,
)
from backend.app.services.search import SearchOperationError
from backend.app.services.storage import save_documents
from backend.app.services.writeback import _column_value


def _document(**classification) -> dict:
    base = {
        "materialType": {"value": "Blog", "confidence": 0.9, "evidence": "Stated."},
        "topics": [{"value": "2012 Pension Reform", "confidence": 0.9, "evidence": "Stated."}],
        "reviewRequired": False,
        "riskFlags": [],
    }
    base.update(classification)
    return {
        "documentName": "guide.pdf",
        "wtwClassification": base,
        **{field: "Sample value" for field in REVIEW_FIELDS},
    }


class TaxonomyReviewFieldTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        root = Path(self._directory.name)
        self.data_path = root / "metadata.json"
        self.source_dir = root / "documents"
        self.source_dir.mkdir()

    def tearDown(self) -> None:
        self._directory.cleanup()

    def _save(self, document: dict) -> None:
        save_documents([document], self.data_path)

    def _review(self) -> dict:
        from backend.app.services.storage import load_documents

        return load_documents(self.data_path)[0]["metadataReview"]

    def test_taxonomy_fields_are_offered_for_review_with_choices(self) -> None:
        review = metadata_review(_document(), "")

        self.assertEqual(set(review["fields"]), set(ALL_REVIEW_FIELDS))
        topics = review["fields"]["topics"]
        self.assertEqual(topics["kind"], "taxonomy-multi")
        self.assertEqual(topics["value"], ["2012 Pension Reform"])
        self.assertIn("2012 Pension Reform", topics["choices"])
        self.assertTrue(topics["required"])
        self.assertEqual(review["fields"]["materialType"]["kind"], "taxonomy-single")

    def test_editing_a_multi_value_field_validates_against_the_taxonomy(self) -> None:
        self._save(_document())

        updated = update_field_review(
            self.data_path,
            self.source_dir,
            "guide.pdf",
            "topics",
            "edited",
            ["2012 Pension Reform", "162(m) Deduction Limit"],
        )

        self.assertEqual(
            updated["metadataReview"]["fields"]["topics"]["value"],
            ["2012 Pension Reform", "162(m) Deduction Limit"],
        )

    def test_invented_taxonomy_terms_are_refused(self) -> None:
        self._save(_document())

        with self.assertRaisesRegex(ValueError, "Unknown topics term"):
            update_field_review(
                self.data_path,
                self.source_dir,
                "guide.pdf",
                "topics",
                "edited",
                ["Completely Invented Topic"],
            )

    def test_rejecting_a_required_taxonomy_field_blocks_approval(self) -> None:
        self._save(_document())
        for field in ALL_REVIEW_FIELDS:
            update_field_review(self.data_path, self.source_dir, "guide.pdf", field, "accepted")
        update_field_review(self.data_path, self.source_dir, "guide.pdf", "topics", "rejected")

        with self.assertRaisesRegex(ValueError, "Resolve all review fields before approval: Topics"):
            approve_metadata_review(self.data_path, self.source_dir, "guide.pdf", reviewer="Test reviewer")

    def test_accepting_an_empty_required_taxonomy_field_still_blocks_approval(self) -> None:
        self._save(_document(topics=[]))
        for field in ALL_REVIEW_FIELDS:
            update_field_review(self.data_path, self.source_dir, "guide.pdf", field, "accepted")

        with self.assertRaisesRegex(ValueError, "Topics is required"):
            approve_metadata_review(self.data_path, self.source_dir, "guide.pdf", reviewer="Test reviewer")


class ClassificationFlagTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        root = Path(self._directory.name)
        self.data_path = root / "metadata.json"
        self.source_dir = root / "documents"
        self.source_dir.mkdir()

    def tearDown(self) -> None:
        self._directory.cleanup()

    def _resolve_all_fields(self) -> None:
        for field in ALL_REVIEW_FIELDS:
            update_field_review(self.data_path, self.source_dir, "guide.pdf", field, "accepted")

    def test_review_required_blocks_approval_until_acknowledged(self) -> None:
        save_documents([_document(reviewRequired=True)], self.data_path)
        self._resolve_all_fields()

        with self.assertRaisesRegex(ValueError, "Resolve all classification flags"):
            approve_metadata_review(self.data_path, self.source_dir, "guide.pdf", reviewer="Test reviewer")

        resolve_classification_flag(
            self.data_path,
            self.source_dir,
            "guide.pdf",
            "review-required",
            "Ada Reviewer",
            "Checked against the source deck.",
        )
        self._resolve_all_fields()
        approved = approve_metadata_review(self.data_path, self.source_dir, "guide.pdf", reviewer="Test reviewer")

        self.assertEqual(approved["metadataReview"]["status"], "approved")
        flag = approved["metadataReview"]["classificationFlags"][0]
        self.assertEqual(flag["status"], "resolved")
        self.assertEqual(flag["resolvedBy"], "Ada Reviewer")

    def test_risk_flags_each_require_resolution(self) -> None:
        save_documents([_document(riskFlags=["client names", "pricing"])], self.data_path)
        self._resolve_all_fields()
        review = metadata_review_of(self.data_path)

        risk_ids = sorted(flag["id"] for flag in review["classificationFlags"] if flag["type"] == "risk")
        self.assertEqual(risk_ids, ["risk:client names", "risk:pricing"])

        resolve_classification_flag(
            self.data_path, self.source_dir, "guide.pdf", risk_ids[0], "Ada Reviewer"
        )
        self._resolve_all_fields()
        with self.assertRaisesRegex(ValueError, "Resolve all classification flags"):
            approve_metadata_review(self.data_path, self.source_dir, "guide.pdf", reviewer="Test reviewer")

    def test_derived_flags_cannot_be_acknowledged_away(self) -> None:
        save_documents([_document(topics=[])], self.data_path)

        with self.assertRaisesRegex(ValueError, "clears itself"):
            resolve_classification_flag(
                self.data_path,
                self.source_dir,
                "guide.pdf",
                "missing-required:topics",
                "Ada Reviewer",
            )

    def test_correcting_an_unknown_term_clears_its_flag(self) -> None:
        save_documents([_document(topics=[{"value": "Legacy Retired Term", "confidence": 0.4}])], self.data_path)
        review = metadata_review_of(self.data_path)
        self.assertIn(
            "unknown-term:topics:legacy retired term",
            [flag["id"] for flag in review["classificationFlags"]],
        )

        update_field_review(
            self.data_path, self.source_dir, "guide.pdf", "topics", "edited", ["2012 Pension Reform"]
        )
        review = metadata_review_of(self.data_path)

        self.assertEqual(
            [flag for flag in review["classificationFlags"] if flag["type"] == "unknown-term"], []
        )

    def test_unknown_terms_cannot_simply_be_accepted(self) -> None:
        save_documents([_document(topics=[{"value": "Legacy Retired Term", "confidence": 0.4}])], self.data_path)

        with self.assertRaisesRegex(ValueError, "outside the taxonomy"):
            update_field_review(self.data_path, self.source_dir, "guide.pdf", "topics", "accepted")

    def test_unresolved_items_report_fields_and_flags_separately(self) -> None:
        save_documents([_document(reviewRequired=True)], self.data_path)
        review = metadata_review_of(self.data_path)

        unresolved = unresolved_review_items(review)

        self.assertIn("Topics", unresolved["fields"])
        self.assertTrue(any("requiring human review" in flag for flag in unresolved["flags"]))


class ColumnValueTests(unittest.TestCase):
    def test_multi_value_selections_are_joined_for_text_columns(self) -> None:
        self.assertEqual(_column_value(["Automotive", "Charities and Nonprofits"]), "Automotive; Charities and Nonprofits")

    def test_empty_selection_clears_the_column(self) -> None:
        self.assertEqual(_column_value([]), "")

    def test_single_values_are_unchanged(self) -> None:
        self.assertEqual(_column_value("Blog"), "Blog")


def metadata_review_of(data_path: Path) -> dict:
    from backend.app.services.storage import load_documents

    document = load_documents(data_path)[0]
    return metadata_review(document, "")


if __name__ == "__main__":
    unittest.main()



class ReviewerAttributionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        root = Path(self._directory.name)
        self.data_path = root / "metadata.json"
        self.source_dir = root / "documents"
        self.source_dir.mkdir()

    def tearDown(self) -> None:
        self._directory.cleanup()

    def _resolve_all(self) -> None:
        save_documents([_document()], self.data_path)
        for field in ALL_REVIEW_FIELDS:
            update_field_review(self.data_path, self.source_dir, "guide.pdf", field, "accepted")
    def test_approval_without_a_reviewer_name_is_refused(self) -> None:
        self._resolve_all()

        with self.assertRaises(ValueError) as caught:
            approve_metadata_review(self.data_path, self.source_dir, "guide.pdf")

        self.assertIn("reviewer name is required", str(caught.exception))

    def test_a_blank_reviewer_name_is_refused(self) -> None:
        self._resolve_all()

        with self.assertRaises(ValueError):
            approve_metadata_review(self.data_path, self.source_dir, "guide.pdf", reviewer="   ")

    def test_the_named_reviewer_is_recorded_against_the_approval(self) -> None:
        self._resolve_all()

        approved = approve_metadata_review(
            self.data_path, self.source_dir, "guide.pdf", reviewer="  Dana Reviewer  "
        )

        self.assertEqual(approved["metadataReview"]["reviewedBy"], "Dana Reviewer")


class RiskFlagShapeTests(unittest.TestCase):
    def test_object_risk_flags_produce_readable_ids_and_messages(self) -> None:
        document = _document(
            riskFlags=[{"flag": "internal-only content", "evidence": "Marked internal in the header."}]
        )

        review = metadata_review(document, "")
        flag = [item for item in review["classificationFlags"] if item["type"] == "risk"][0]

        self.assertEqual(flag["id"], "risk:internal-only content")
        self.assertNotIn("{", flag["id"])
        self.assertNotIn("{", flag["message"])
        self.assertIn("internal-only content", flag["message"])
        self.assertIn("Marked internal in the header.", flag["message"])

    def test_string_risk_flags_still_work(self) -> None:
        review = metadata_review(_document(riskFlags=["pricing"]), "")
        flag = [item for item in review["classificationFlags"] if item["type"] == "risk"][0]

        self.assertEqual(flag["id"], "risk:pricing")
        self.assertNotIn("Evidence:", flag["message"])

    def test_an_object_risk_flag_can_be_resolved_by_its_id(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        data_path = root / "metadata.json"
        source_dir = root / "documents"
        source_dir.mkdir()
        save_documents(
            [_document(riskFlags=[{"flag": "pricing", "evidence": "Rate card included."}])], data_path
        )

        updated = resolve_classification_flag(
            data_path, source_dir, "guide.pdf", "risk:pricing", "Ada Reviewer", "Checked."
        )

        flag = [item for item in updated["metadataReview"]["classificationFlags"] if item["type"] == "risk"][0]
        self.assertEqual(flag["status"], "resolved")
        self.assertEqual(flag["resolvedBy"], "Ada Reviewer")


class IndexWithdrawalOnRevocationTests(unittest.TestCase):
    """Approval is frozen into each indexed chunk, so revoking it must withdraw them.

    Waiting for the next full reindex would leave un-approved content answerable.
    """

    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        root = Path(self._directory.name)
        self.data_path = root / "metadata.json"
        self.source_dir = root / "documents"
        self.source_dir.mkdir()
        self.client = TestClient(app)
        self.removed: list[tuple[str, int]] = []

        service = self

        class FakeSearch:
            def remove_document(self, name: str, known_chunk_count: int = 0) -> int:
                service.removed.append((name, known_chunk_count))
                return known_chunk_count

        self.fake_search = FakeSearch

    def _save_approved(self) -> None:
        document = _document()
        document["metadataReview"] = {"status": "approved", "reviewedBy": "Ada Reviewer"}
        document["searchIndex"] = {"status": "indexed", "chunks": 3}
        save_documents([document], self.data_path)

    def _patch(self):
        return (
            patch("backend.app.main.DATA_PATH", self.data_path),
            patch("backend.app.main.SAMPLE_DOCS", self.source_dir),
            patch("backend.app.main.ApprovedKnowledgeSearch", self.fake_search),
        )

    def test_editing_a_field_withdraws_the_document_from_the_answer_index(self) -> None:
        self._save_approved()
        first, second, third = self._patch()
        with first, second, third:
            response = self.client.patch(
                "/api/documents/guide.pdf/review",
                json={"field": "author", "decision": "edited", "value": "Grace Hopper"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.removed, [("guide.pdf", 3)])
        self.assertEqual(response.json()["searchIndex"]["status"], "withdrawn")

    def test_resolving_a_flag_withdraws_the_document_from_the_answer_index(self) -> None:
        document = _document(reviewRequired=True)
        document["metadataReview"] = {"status": "approved", "reviewedBy": "Ada Reviewer"}
        document["searchIndex"] = {"status": "indexed", "chunks": 2}
        save_documents([document], self.data_path)
        first, second, third = self._patch()
        with first, second, third:
            response = self.client.post(
                "/api/documents/guide.pdf/review/flags/resolve",
                json={"flagId": "review-required", "reviewer": "Ada Reviewer", "note": "Checked."},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.removed, [("guide.pdf", 2)])

    def test_a_document_that_was_never_indexed_is_not_withdrawn(self) -> None:
        document = _document()
        document["metadataReview"] = {"status": "approved", "reviewedBy": "Ada Reviewer"}
        save_documents([document], self.data_path)
        first, second, third = self._patch()
        with first, second, third:
            response = self.client.patch(
                "/api/documents/guide.pdf/review",
                json={"field": "author", "decision": "edited", "value": "Grace Hopper"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.removed, [])

    def test_a_failed_withdrawal_is_recorded_rather_than_hidden(self) -> None:
        self._save_approved()

        class BrokenSearch:
            def remove_document(self, name: str, known_chunk_count: int = 0) -> int:
                raise SearchOperationError("index unreachable")

        with (
            patch("backend.app.main.DATA_PATH", self.data_path),
            patch("backend.app.main.SAMPLE_DOCS", self.source_dir),
            patch("backend.app.main.ApprovedKnowledgeSearch", BrokenSearch),
        ):
            response = self.client.patch(
                "/api/documents/guide.pdf/review",
                json={"field": "author", "decision": "edited", "value": "Grace Hopper"},
            )

        self.assertEqual(response.status_code, 200)
        status = response.json()["searchIndex"]
        self.assertEqual(status["status"], "withdrawal-failed")
        self.assertIn("index unreachable", status["error"])

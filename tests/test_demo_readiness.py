import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app.services.storage import load_documents, save_documents
from backend.app.services import storage
from backend.scripts.demo_preflight import preflight
from backend.scripts.reset_demo import reset_demo
from backend.scripts import sanitize_demo_metadata as sanitizer
from backend.scripts.seed_sample_documents import write_pdf


class DemoReadinessTests(unittest.TestCase):
    def test_generated_metadata_falls_back_to_sanitized_template(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime_path = root / "extracted-metadata.json"
            template_path = root / "demo-metadata.json"
            save_documents([{"documentName": "guide.pdf"}], template_path)

            with (
                patch.object(storage, "DATA_PATH", runtime_path),
                patch.object(storage, "DEMO_DATA_PATH", template_path),
            ):
                documents = storage.load_documents(runtime_path)

            self.assertEqual(documents, [{"documentName": "guide.pdf"}])

    def test_sanitizer_removes_runtime_sharepoint_fields_from_template_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime_path = root / "extracted-metadata.json"
            template_path = root / "demo-metadata.json"
            original = [{
                "documentName": "guide.pdf",
                "sharePoint": {"driveItemId": "item-id"},
                "sharePointStage": {"status": "staged"},
                "sharePointWritebackEnabled": True,
            }]
            save_documents(original, runtime_path)

            with (
                patch.object(sanitizer, "DATA_PATH", runtime_path),
                patch.object(sanitizer, "DEMO_DATA_PATH", template_path),
            ):
                count, removed = sanitizer.sanitize_demo_metadata()

            self.assertEqual((count, removed), (1, 3))
            self.assertEqual(load_documents(runtime_path), original)
            self.assertEqual(load_documents(template_path), [{"documentName": "guide.pdf"}])

    def test_preflight_accepts_consistent_demo_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_dir = root / "documents"
            source_dir.mkdir()
            write_pdf(source_dir / "guide.pdf", "Guide. Author: Sample Author.")
            data_path = root / "metadata.json"
            save_documents([{
                "documentName": "guide.pdf",
                "metadataLink": "/api/documents/guide.pdf",
                "wtwClassification": {"reviewRequired": False},
                "metadataReview": {"status": "needs-review"},
            }], data_path)

            with patch.dict("os.environ", {"AI_PROVIDER": "mock"}):
                self.assertEqual(preflight(data_path, source_dir), [])

    def test_preflight_reports_missing_source_document(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_dir = root / "documents"
            source_dir.mkdir()
            data_path = root / "metadata.json"
            save_documents([{
                "documentName": "missing.pdf",
                "metadataLink": "/api/documents/missing.pdf",
                "wtwClassification": {"reviewRequired": True},
                "metadataReview": {"status": "needs-review"},
            }], data_path)

            with patch.dict("os.environ", {"AI_PROVIDER": "mock"}):
                self.assertIn("Missing source document: missing.pdf", preflight(data_path, source_dir))

    def test_preflight_requires_column_map_when_writeback_is_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_dir = root / "documents"
            source_dir.mkdir()
            write_pdf(source_dir / "guide.pdf", "Guide.")
            data_path = root / "metadata.json"
            save_documents([{
                "documentName": "guide.pdf",
                "metadataLink": "/api/documents/guide.pdf",
                "wtwClassification": {"reviewRequired": False},
                "metadataReview": {"status": "needs-review"},
            }], data_path)

            with patch.dict(
                "os.environ",
                {
                    "AI_PROVIDER": "mock",
                    "SHAREPOINT_WRITEBACK_ENABLED": "true",
                    "SHAREPOINT_HOSTNAME": "example.sharepoint.com",
                    "SHAREPOINT_COLUMN_MAP": "",
                },
                clear=True,
            ):
                errors = preflight(data_path, source_dir)

            self.assertIn("SHAREPOINT_COLUMN_MAP must be a non-empty JSON object", errors)

    def test_reset_restores_original_values_and_curates_approved_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_dir = root / "documents"
            source_dir.mkdir()
            write_pdf(
                source_dir / "guide.pdf",
                "Guide. Business area: Claims. Audience: Advisor. Language: English. "
                "Author: Sample Author. Country of origin: Canada.",
            )
            data_path = root / "metadata.json"
            save_documents([{
                "documentName": "guide.pdf",
                "businessArea": "Edited value",
                "audience": "Advisor",
                "language": "English",
                "author": "Sample Author",
                "countryOfOrigin": "Canada",
                "metadataReview": {
                    "fields": {
                        "businessArea": {
                            "originalValue": "Claims",
                        },
                    },
                },
            }], data_path)

            reset_demo(data_path, source_dir)
            document = load_documents(data_path)[0]

            self.assertEqual(document["businessArea"], "Claims")
            self.assertEqual(document["metadataReview"]["status"], "approved")
            self.assertNotIn("sharePointWriteback", document)

    def test_reset_refuses_transient_sharepoint_workflow_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_dir = root / "documents"
            source_dir.mkdir()
            write_pdf(source_dir / "guide.pdf", "Guide.")
            data_path = root / "metadata.json"
            save_documents([{
                "documentName": "guide.pdf",
                "sharePointWritebackEnabled": True,
                "sharePointStage": {"status": "staged"},
            }], data_path)

            with self.assertRaisesRegex(ValueError, "complete or clean up"):
                reset_demo(data_path, source_dir)


if __name__ == "__main__":
    unittest.main()

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.services.ai_providers import MockAIProvider, _parse_agent_json, _parse_dynamic_json
from backend.app.services.extractors import extract_author, extract_labeled_value, extract_text
from backend.app.services.lifecycle import lifecycle_metadata
from backend.app.services.storage import load_documents, save_documents
from backend.scripts.seed_sample_documents import SAMPLES, slug


EXPECTED_AGENT_FIELDS = {
    "summary",
    "themes",
    "suggestedTags",
    "language",
    "author",
    "sentiment",
    "businessArea",
    "audience",
    "metadataCategory",
    "countryOfOrigin",
    "customMetadata",
}


class MetadataProviderTests(unittest.TestCase):
    def test_sample_sources_have_diverse_grounded_metadata(self) -> None:
        paths = [
            Path("sample-documents") / f"{index:02d}-{slug(sample[0])}{['.pdf', '.docx', '.pptx'][index % 3]}"
            for index, sample in enumerate(SAMPLES, start=1)
        ]
        records = [(path, extract_text(path)) for path in paths]

        self.assertEqual(sum(extract_author(path, text) is not None for path, text in records), 9)
        self.assertEqual(len({extract_labeled_value(text, "Language") for _, text in records}), 5)
        self.assertEqual(len({extract_labeled_value(text, "Sentiment") for _, text in records}), 3)
        self.assertGreaterEqual(len({extract_labeled_value(text, "Country of origin") for _, text in records}), 7)

    def test_lifecycle_status_uses_grounded_review_and_approval_fields(self) -> None:
        self.assertEqual(
            lifecycle_metadata("Last reviewed age days: 220. Approval status: Pending."),
            {"reviewStatus": "Needs Review", "approvalStatus": "Pending", "recencyDays": 220},
        )
        self.assertEqual(
            lifecycle_metadata("Mentions approval without a labeled status."),
            {"reviewStatus": "Not Recorded", "approvalStatus": "Not Recorded", "recencyDays": None},
        )

    def test_sample_pdf_extracts_document_text(self) -> None:
        text = extract_text(Path("sample-documents/03-claims-intake-procedure.pdf"))

        self.assertIn("Claims Intake Procedure", text)
        self.assertFalse(text.startswith("%PDF"))

    def test_mock_provider_matches_agent_contract(self) -> None:
        result = MockAIProvider().analyze(
            "claims-procedure.pdf",
            "Claims procedure. Author: Sample Author. Language: Spanish. Sentiment: Positive. Country of origin: Canada. Claim type: Disability.",
            ("Claim type", "Identify the type of claim"),
        )

        self.assertEqual(set(result), EXPECTED_AGENT_FIELDS)
        self.assertEqual(result["author"], "Sample Author")
        self.assertEqual(result["language"], "Spanish")
        self.assertEqual(result["sentiment"], "Positive")
        self.assertEqual(result["countryOfOrigin"], "Canada")
        self.assertEqual(result["customMetadata"], {"Claim type": "Disability"})
        self.assertEqual(result["metadataCategory"], "Procedure")

    def test_agent_json_rejects_missing_fields(self) -> None:
        with self.assertRaisesRegex(ValueError, "missing fields"):
            _parse_agent_json(json.dumps({"summary": "Incomplete"}))

    def test_dynamic_json_maps_only_requested_custom_property(self) -> None:
        result = _parse_dynamic_json(
            json.dumps({"countryOfOrigin": "Canada", "customValue": "Disability", "ignored": "value"}),
            "Claim type",
        )

        self.assertEqual(result, {"countryOfOrigin": "Canada", "customMetadata": {"Claim type": "Disability"}})

    def test_dynamic_json_accepts_nested_numeric_custom_value(self) -> None:
        result = _parse_dynamic_json(
            json.dumps({"countryOfOrigin": "Unknown", "customValue": {"Any field": 311}}),
            "Any field",
        )

        self.assertEqual(result["customMetadata"], {"Any field": "311"})

    def test_agent_json_filters_extra_fields(self) -> None:
        payload = {field: [] if field in {"themes", "suggestedTags"} else {} if field == "customMetadata" else "Unknown" for field in EXPECTED_AGENT_FIELDS}
        payload["extraGovernanceField"] = "ignored"

        self.assertEqual(set(_parse_agent_json(json.dumps(payload))), EXPECTED_AGENT_FIELDS)


class StorageTests(unittest.TestCase):
    def test_documents_round_trip(self) -> None:
        documents = [{"documentName": "example.pdf", "summary": "Example"}]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "metadata.json"
            save_documents(documents, path)

            self.assertEqual(load_documents(path), documents)


class DocumentRouteTests(unittest.TestCase):
    client = TestClient(app)

    def test_sample_document_link_resolves(self) -> None:
        response = self.client.get("/api/documents/01-beneficiary-change-form-guide.docx")

        self.assertEqual(response.status_code, 200)

    def test_documents_include_lifecycle_metadata(self) -> None:
        response = self.client.get("/api/documents")
        claims = next(document for document in response.json() if document["documentName"] == "03-claims-intake-procedure.pdf")

        self.assertEqual(claims["reviewStatus"], "Stale")
        self.assertEqual(claims["approvalStatus"], "Review Required")
        self.assertEqual(claims["recencyDays"], 410)
        self.assertEqual(claims["countryOfOrigin"], "Canada")
        self.assertEqual(claims["customMetadata"], {})

    def test_missing_document_returns_not_found(self) -> None:
        self.assertEqual(self.client.get("/api/documents/missing.pdf").status_code, 404)

    def test_upload_rejects_unsupported_file(self) -> None:
        response = self.client.post(
            "/api/documents/upload",
            files=[("files", ("notes.txt", b"not supported", "text/plain"))],
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Unsupported", response.json()["detail"])

    def test_upload_processes_supported_batch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source_dir = Path(directory) / "documents"
            data_path = Path(directory) / "metadata.json"
            processed = [{"documentName": "guide.pdf"}]
            with (
                patch("backend.app.main.SAMPLE_DOCS", source_dir),
                patch("backend.app.main.DATA_PATH", data_path),
                patch("backend.scripts.ingest.run_ingestion", return_value=processed) as run_ingestion,
            ):
                response = self.client.post(
                    "/api/documents/upload",
                    files=[("files", ("guide.pdf", b"%PDF sample", "application/pdf"))],
                )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["uploaded"], ["guide.pdf"])
            self.assertEqual((source_dir / "guide.pdf").read_bytes(), b"%PDF sample")
            run_ingestion.assert_called_once_with(source_dir, data_path, {"guide.pdf"}, None)

    def test_upload_passes_custom_property_request(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source_dir = Path(directory) / "documents"
            data_path = Path(directory) / "metadata.json"
            with (
                patch("backend.app.main.SAMPLE_DOCS", source_dir),
                patch("backend.app.main.DATA_PATH", data_path),
                patch("backend.scripts.ingest.run_ingestion", return_value=[]) as run_ingestion,
            ):
                response = self.client.post(
                    "/api/documents/upload",
                    data={"custom_property_name": "Claim type", "custom_property_instruction": "Identify the claim type"},
                    files=[("files", ("claim.pdf", b"%PDF sample", "application/pdf"))],
                )

            self.assertEqual(response.status_code, 200)
            run_ingestion.assert_called_once_with(
                source_dir, data_path, {"claim.pdf"}, ("Claim type", "Identify the claim type")
            )

    def test_upload_rejects_incomplete_custom_property_request(self) -> None:
        response = self.client.post(
            "/api/documents/upload",
            data={"custom_property_name": "Claim type"},
            files=[("files", ("claim.pdf", b"%PDF sample", "application/pdf"))],
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("provided together", response.json()["detail"])

    def test_failed_upload_restores_documents_and_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source_dir = Path(directory) / "documents"
            source_dir.mkdir()
            document_path = source_dir / "guide.pdf"
            document_path.write_bytes(b"original document")
            data_path = Path(directory) / "metadata.json"
            data_path.write_bytes(b"original metadata")

            def fail_processing(*args: object) -> None:
                data_path.write_bytes(b"partial metadata")
                raise RuntimeError("provider unavailable")

            with (
                patch("backend.app.main.SAMPLE_DOCS", source_dir),
                patch("backend.app.main.DATA_PATH", data_path),
                patch("backend.scripts.ingest.run_ingestion", side_effect=fail_processing),
            ):
                response = self.client.post(
                    "/api/documents/upload",
                    files=[("files", ("guide.pdf", b"replacement document", "application/pdf"))],
                )

            self.assertEqual(response.status_code, 500)
            self.assertEqual(document_path.read_bytes(), b"original document")
            self.assertEqual(data_path.read_bytes(), b"original metadata")


if __name__ == "__main__":
    unittest.main()
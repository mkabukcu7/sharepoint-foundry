import argparse
from pathlib import Path

from backend.app.services.extractors import extract_text
from backend.app.services.reviews import metadata_review
from backend.app.services.storage import DATA_PATH, SAMPLE_DOCS, load_documents, save_documents


BASELINE_REVIEWER = "Demo baseline"
BASELINE_REVIEWED_AT = "2026-09-18T00:00:00+00:00"


def reset_demo(
    data_path: Path = DATA_PATH,
    source_dir: Path = SAMPLE_DOCS,
    approved_documents: int = 1,
) -> list[dict]:
    documents = load_documents(data_path)
    if not documents:
        raise ValueError(f"No demo metadata found at {data_path}")

    for document in documents:
        if (
            document.get("sharePointWritebackEnabled") is True
            or document.get("sharePointStage")
            or document.get("sharePointWriteback")
        ):
            raise ValueError(
                f"Cannot reset while '{document.get('documentName', 'unknown')}' has SharePoint workflow state; "
                "complete or clean up that item first"
            )
        stored_review = document.get("metadataReview")
        stored_fields = stored_review.get("fields", {}) if isinstance(stored_review, dict) else {}
        for field, field_review in stored_fields.items():
            if isinstance(field_review, dict) and field_review.get("originalValue") is not None:
                document[field] = field_review["originalValue"]
        document.pop("metadataReview", None)
        source_path = source_dir / document["documentName"]
        if not source_path.is_file():
            raise FileNotFoundError(f"Missing demo source document: {source_path}")
        document["metadataReview"] = metadata_review(document, extract_text(source_path))

    for document in documents[:approved_documents]:
        review = document["metadataReview"]
        for field_review in review["fields"].values():
            field_review["support"] = "confirmed"
            field_review["reviewDecision"] = "accepted"
            field_review["decidedAt"] = BASELINE_REVIEWED_AT
        review["status"] = "approved"
        review["reviewedBy"] = BASELINE_REVIEWER
        review["reviewedAt"] = BASELINE_REVIEWED_AT

    save_documents(documents, data_path)
    return documents


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Restore deterministic metadata review state for the demo.")
    parser.add_argument(
        "--approved-documents",
        type=int,
        default=1,
        help="Number of leading catalog documents to mark approved in the baseline.",
    )
    args = parser.parse_args()
    if args.approved_documents < 0:
        parser.error("--approved-documents cannot be negative")
    reset = reset_demo(approved_documents=args.approved_documents)
    print(f"Reset {len(reset)} documents; {args.approved_documents} approved baseline record(s)")

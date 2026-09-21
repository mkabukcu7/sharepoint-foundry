import json
import os
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv

from backend.app.services.storage import DATA_PATH, SAMPLE_DOCS, load_documents


SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".pptx"}
load_dotenv()


def preflight(data_path: Path = DATA_PATH, source_dir: Path = SAMPLE_DOCS) -> list[str]:
    errors: list[str] = []
    documents = load_documents(data_path)
    provider = os.getenv("AI_PROVIDER", "mock").strip().lower()
    if not documents:
        return [f"No metadata records found at {data_path}"]

    names: set[str] = set()
    for index, document in enumerate(documents, start=1):
        name = document.get("documentName")
        if not isinstance(name, str) or not name:
            errors.append(f"Record {index} has no documentName")
            continue
        if name.casefold() in names:
            errors.append(f"Duplicate metadata record: {name}")
        names.add(name.casefold())
        if Path(name).name != name or Path(name).suffix.lower() not in SUPPORTED_EXTENSIONS:
            errors.append(f"Unsupported or invalid document name: {name}")
        if not (source_dir / name).is_file():
            errors.append(f"Missing source document: {name}")
        expected_link = f"/api/documents/{quote(name)}"
        if document.get("metadataLink") != expected_link:
            errors.append(f"Invalid metadata link for {name}: expected {expected_link}")
        classification = document.get("wtwClassification")
        if provider == "foundry_wtw" and not isinstance(classification, dict):
            errors.append(f"Missing WTW classification for {name}")
        elif isinstance(classification, dict) and not isinstance(classification.get("reviewRequired"), bool):
            errors.append(f"Invalid WTW review state for {name}")
        review = document.get("metadataReview")
        if not isinstance(review, dict) or review.get("status") not in {"needs-review", "approved"}:
            errors.append(f"Invalid metadata review state for {name}")

    source_names = {
        path.name.casefold()
        for path in source_dir.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
    }
    for name in sorted(source_names - names):
        errors.append(f"Source document has no metadata record: {name}")

    if provider == "foundry":
        for variable in ("FOUNDRY_PROJECT_ENDPOINT", "FOUNDRY_AGENT_NAME", "FOUNDRY_AGENT_VERSION"):
            if not os.getenv(variable):
                errors.append(f"Missing Foundry setting: {variable}")
    elif provider == "foundry_wtw":
        for variable in (
            "FOUNDRY_PROJECT_ENDPOINT",
            "FOUNDRY_CLASSIFIER_AGENT_NAME",
            "FOUNDRY_CLASSIFIER_AGENT_VERSION",
        ):
            if not os.getenv(variable):
                errors.append(f"Missing Foundry classifier setting: {variable}")
        taxonomy_path = Path(os.getenv("FOUNDRY_TAXONOMY_PATH", "taxonomy/controlled-terms.json"))
        if not taxonomy_path.is_file():
            errors.append(f"Missing controlled taxonomy: {taxonomy_path}")
    elif provider != "mock":
        errors.append(f"Unsupported AI_PROVIDER: {provider}")

    writeback_enabled = os.getenv("SHAREPOINT_WRITEBACK_ENABLED", "").strip().lower() in {"1", "true", "yes"}
    if writeback_enabled:
        for variable in ("SHAREPOINT_HOSTNAME",):
            if not os.getenv(variable):
                errors.append(f"Missing SharePoint write-back setting: {variable}")
        raw_column_map = os.getenv("SHAREPOINT_COLUMN_MAP", "")
        try:
            column_map = json.loads(raw_column_map)
        except json.JSONDecodeError:
            column_map = None
        if not isinstance(column_map, dict) or not column_map:
            errors.append("SHAREPOINT_COLUMN_MAP must be a non-empty JSON object")
        elif not all(isinstance(field, str) and isinstance(column, str) and column.strip() for field, column in column_map.items()):
            errors.append("SHAREPOINT_COLUMN_MAP contains an invalid field or column name")
        for variable, default in (
            ("SHAREPOINT_STAGING_FOLDER_NAME", "Staging"),
            ("SHAREPOINT_REVIEWED_FOLDER_NAME", "Reviewed"),
        ):
            folder_name = os.getenv(variable, default).strip()
            if not folder_name or "/" in folder_name or "\\" in folder_name:
                errors.append(f"{variable} must be a single SharePoint folder name")
    return errors


if __name__ == "__main__":
    failures = preflight()
    if failures:
        print("Demo preflight failed:")
        for failure in failures:
            print(f"  - {failure}")
        raise SystemExit(1)
    documents = load_documents()
    approved = sum(document.get("metadataReview", {}).get("status") == "approved" for document in documents)
    taxonomy_review = sum(
        document.get("wtwClassification", {}).get("reviewRequired") is True
        for document in documents
    )
    print(
        f"Demo preflight passed: {len(documents)} documents, "
        f"{approved} approved metadata record(s), {taxonomy_review} taxonomy review item(s)"
    )
    provider = os.getenv("AI_PROVIDER", "mock").strip().lower()
    writeback_enabled = os.getenv("SHAREPOINT_WRITEBACK_ENABLED", "").strip().lower() in {"1", "true", "yes"}
    print(f"Effective mode: AI_PROVIDER={provider}, SharePoint write-back enabled={writeback_enabled}")

import argparse
from pathlib import Path
from urllib.parse import quote

from backend.app.models.document import DocumentMetadata
from backend.app.services.ai_providers import get_provider
from backend.app.services.extractors import extract_author, extract_labeled_value, extract_text
from backend.app.services.lifecycle import lifecycle_metadata
from backend.app.services.storage import DATA_PATH, SAMPLE_DOCS, load_documents, save_documents


def run_ingestion(
    source_dir: Path = SAMPLE_DOCS,
    data_path: Path = DATA_PATH,
    document_names: set[str] | None = None,
    custom_property: tuple[str, str] | None = None,
) -> list[dict]:
    provider = get_provider()
    documents = [] if document_names is None else [
        document for document in load_documents(data_path)
        if document.get("documentName") not in document_names
    ]
    for path in sorted(source_dir.iterdir()):
        if path.suffix.lower() not in {".pdf", ".docx", ".pptx"}:
            continue
        if document_names is not None and path.name not in document_names:
            continue
        text = extract_text(path)
        ai = provider.analyze(path.name, text, custom_property)
        ai["author"] = extract_author(path, text) or "Unknown"
        ai["language"] = extract_labeled_value(text, "Language") or ai["language"]
        ai["sentiment"] = extract_labeled_value(text, "Sentiment") or ai["sentiment"]
        ai["countryOfOrigin"] = extract_labeled_value(text, "Country of origin") or ai["countryOfOrigin"]
        if custom_property:
            name, _ = custom_property
            labeled_value = extract_labeled_value(text, name)
            if labeled_value:
                ai["customMetadata"][name] = labeled_value
        doc = DocumentMetadata(
            documentName=path.name,
            fileType=path.suffix.lower().lstrip(".").upper(),
            metadataLink=f"/api/documents/{quote(path.name)}",
            sourcePath=f"sample-documents/{path.name}",
            extractedCharacters=len(text),
            **lifecycle_metadata(text),
            **ai,
        )
        documents.append(doc.model_dump())
    documents.sort(key=lambda document: document["documentName"].lower())
    save_documents(documents, data_path)
    return documents
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Extract document metadata for the Knowledge Metadata Agent MVP.")
    parser.add_argument("--source", type=Path, default=SAMPLE_DOCS, help="Folder containing PDF, DOCX, and PPTX files.")
    parser.add_argument("--output", type=Path, default=DATA_PATH, help="Metadata JSON output path.")
    args = parser.parse_args()
    docs = run_ingestion(args.source, args.output)
    print(f"Ingested {len(docs)} documents")

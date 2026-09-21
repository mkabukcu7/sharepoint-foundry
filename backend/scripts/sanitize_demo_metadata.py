import json

from backend.app.services.storage import DATA_PATH, DEMO_DATA_PATH, load_documents


RUNTIME_KEYS = {
    "sharePoint",
    "sharePointStage",
    "sharePointWriteback",
    "sharePointWritebackEnabled",
}


def sanitize_demo_metadata() -> tuple[int, int]:
    if not DATA_PATH.is_file():
        raise FileNotFoundError(f"Generated metadata was not found: {DATA_PATH}")
    documents = load_documents(DATA_PATH)
    removed = 0
    for document in documents:
        for key in RUNTIME_KEYS:
            if key in document:
                removed += 1
                document.pop(key)
    text = json.dumps(documents, indent=2) + "\n"
    DEMO_DATA_PATH.write_text(text, encoding="utf-8")
    return len(documents), removed


if __name__ == "__main__":
    document_count, removed_count = sanitize_demo_metadata()
    print(
        f"Sanitized {document_count} documents; "
        f"removed {removed_count} runtime SharePoint block(s)"
    )

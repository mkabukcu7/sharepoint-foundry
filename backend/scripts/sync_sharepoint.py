import os
import shutil
import tempfile
from pathlib import Path

from dotenv import load_dotenv

from backend.app.services.sharepoint import SharePointClient
from backend.app.services.storage import DATA_PATH, SAMPLE_DOCS, save_documents
from backend.scripts.ingest import run_ingestion

load_dotenv()


def run_sharepoint_ingestion(
    destination: Path = SAMPLE_DOCS,
    data_path: Path = DATA_PATH,
    client: SharePointClient | None = None,
) -> list[dict]:
    source = client or SharePointClient(
        hostname=_required_env("SHAREPOINT_HOSTNAME"),
        site_path=os.getenv("SHAREPOINT_SITE_PATH", "/"),
        library_name=os.getenv("SHAREPOINT_LIBRARY_NAME", "Documents"),
        folder_path=os.getenv("SHAREPOINT_FOLDER_PATH", ""),
    )
    with tempfile.TemporaryDirectory(prefix="sharepoint-sync-") as staging:
        downloaded = source.download_documents(Path(staging))
        if not downloaded:
            return []
        staged_paths = [document.local_path for document in downloaded]
        for existing in destination.iterdir() if destination.exists() else []:
            if existing.is_file() and existing.suffix.lower() in {".pdf", ".docx", ".pptx"}:
                existing.unlink()
        destination.mkdir(parents=True, exist_ok=True)
        for staged_path in staged_paths:
            shutil.copy2(staged_path, destination / staged_path.name)
    documents = run_ingestion(
        destination,
        data_path,
        {path.name for path in staged_paths},
        replace_existing=True,
    )
    metadata_by_name = {document.file_name: document for document in downloaded}
    for document in documents:
        source = metadata_by_name.get(document["documentName"])
        if source:
            document["sharePoint"] = {
                "siteId": source.site_id,
                "driveId": source.drive_id,
                "driveItemId": source.drive_item_id,
                "fileName": source.file_name,
                "webUrl": source.web_url,
                "etag": source.etag,
                "createdDateTime": source.created_datetime,
                "modifiedDateTime": source.modified_datetime,
                "existingColumns": source.existing_columns,
                "owner": source.owner,
                "author": source.author,
            }
    save_documents(documents, data_path)
    return documents


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


if __name__ == "__main__":
    documents = run_sharepoint_ingestion()
    print(f"Downloaded and analyzed {len(documents)} SharePoint documents")
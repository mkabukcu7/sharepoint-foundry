import argparse
import os

from dotenv import load_dotenv

from backend.app.services.sharepoint import SharePointClient


def main() -> None:
    parser = argparse.ArgumentParser(description="List files in a configured SharePoint workflow folder.")
    parser.add_argument("folder")
    args = parser.parse_args()
    load_dotenv()

    client = SharePointClient(
        hostname=os.environ["SHAREPOINT_HOSTNAME"],
        site_path=os.getenv("SHAREPOINT_SITE_PATH", "/"),
        library_name=os.getenv("SHAREPOINT_LIBRARY_NAME", "Documents"),
        folder_path=os.getenv("SHAREPOINT_FOLDER_PATH", ""),
    )
    client._authorize()
    _, drive_id = client._site_and_drive()
    folder = next(
        (
            item
            for item in client._child_items(drive_id, "root")
            if "folder" in item and item.get("name", "").casefold() == args.folder.casefold()
        ),
        None,
    )
    if folder is None:
        raise ValueError(f"SharePoint folder '{args.folder}' was not found")
    files = [item for item in client._child_items(drive_id, folder["id"]) if "file" in item]
    print(f"SharePoint folder: {folder.get('webUrl', args.folder)}")
    print(f"Files: {len(files)}")
    for item in files:
        print(f"- {item['name']} | {item.get('webUrl', '')}")


if __name__ == "__main__":
    main()

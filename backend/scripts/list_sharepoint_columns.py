import os

from dotenv import load_dotenv

from backend.app.services.sharepoint import SharePointClient


def main() -> None:
    load_dotenv()
    client = SharePointClient(
        hostname=os.environ["SHAREPOINT_HOSTNAME"],
        site_path=os.getenv("SHAREPOINT_SITE_PATH", "/"),
        library_name=os.getenv("SHAREPOINT_LIBRARY_NAME", "Documents"),
        folder_path=os.getenv("SHAREPOINT_FOLDER_PATH", ""),
    )
    columns = client.list_columns()
    print("Writable SharePoint columns:")
    for column in sorted(columns, key=lambda item: item["displayName"].casefold()):
        print(f"  {column['displayName']} | internal={column['name']} | type={column['type']}")


if __name__ == "__main__":
    main()

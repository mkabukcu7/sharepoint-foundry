import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

ROOT = Path(__file__).resolve().parents[3]
DATA_PATH = ROOT / "data" / "extracted-metadata.json"
DEMO_DATA_PATH = ROOT / "data" / "demo-metadata.json"
SAMPLE_DOCS = ROOT / "sample-documents"


def load_documents(path: Path = DATA_PATH) -> list[dict]:
    if not path.exists():
        if path == DATA_PATH and DEMO_DATA_PATH.exists():
            path = DEMO_DATA_PATH
        else:
            return []
    return json.loads(path.read_text(encoding="utf-8"))


def save_documents(documents: list[dict], path: Path = DATA_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as temporary:
            temporary.write(json.dumps(documents, indent=2))
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, path)
    finally:
        if temporary_path:
            temporary_path.unlink(missing_ok=True)

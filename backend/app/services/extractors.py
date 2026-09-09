import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree


CORE_CREATOR = "{http://purl.org/dc/elements/1.1/}creator"


def extract_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return _extract_docx(path)
    if suffix == ".pptx":
        return _extract_pptx(path)
    if suffix == ".pdf":
        return _extract_simple_pdf(path)
    return path.read_text(encoding="utf-8", errors="ignore")


def extract_author(path: Path, text: str) -> str | None:
    explicit_author = extract_labeled_value(text, "Author")
    if explicit_author:
        return explicit_author
    if path.suffix.lower() not in {".docx", ".pptx"}:
        return None
    with zipfile.ZipFile(path) as zf:
        try:
            root = ElementTree.fromstring(zf.read("docProps/core.xml"))
        except KeyError:
            return None
    creator = root.find(CORE_CREATOR)
    return creator.text.strip() if creator is not None and creator.text else None


def extract_labeled_value(text: str, label: str) -> str | None:
    match = re.search(rf"{re.escape(label)}:\s*([^.]+)", text, re.IGNORECASE)
    return match.group(1).strip() if match else None


def _xml_text(xml: bytes) -> str:
    root = ElementTree.fromstring(xml)
    return " ".join(node.text.strip() for node in root.iter() if node.text and node.text.strip())


def _extract_docx(path: Path) -> str:
    with zipfile.ZipFile(path) as zf:
        names = [name for name in zf.namelist() if name.startswith("word/") and name.endswith(".xml")]
        return " ".join(_xml_text(zf.read(name)) for name in names)


def _extract_pptx(path: Path) -> str:
    with zipfile.ZipFile(path) as zf:
        names = [name for name in zf.namelist() if name.startswith("ppt/slides/slide") and name.endswith(".xml")]
        return " ".join(_xml_text(zf.read(name)) for name in names)


def _extract_simple_pdf(path: Path) -> str:
    raw = path.read_bytes().decode("latin-1", errors="ignore")
    parts = re.findall(r"\((.*?)\)\s*Tj", raw)
    for array in re.findall(r"\[(.*?)\]\s*TJ", raw, flags=re.DOTALL):
        parts.extend(re.findall(r"\((.*?)\)", array))
    if parts:
        return " ".join(part.replace("\\\\(", "(").replace("\\\\)", ")") for part in parts)
    raise ValueError(f"Unsupported PDF text format: {path.name}")

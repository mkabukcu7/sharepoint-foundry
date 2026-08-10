import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree


def extract_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return _extract_docx(path)
    if suffix == ".pptx":
        return _extract_pptx(path)
    if suffix == ".pdf":
        return _extract_simple_pdf(path)
    return path.read_text(encoding="utf-8", errors="ignore")


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
    parts = re.findall(r"\((.*?)\)\\s*Tj", raw)
    if parts:
        return " ".join(part.replace("\\\\(", "(").replace("\\\\)", ")") for part in parts)
    return re.sub(r"\\s+", " ", raw)


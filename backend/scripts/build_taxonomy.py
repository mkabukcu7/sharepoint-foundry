import json
import re
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree


WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
SOURCE = Path("taxonomy/WTW_Intranet_Taxonomy_Reference.docx")
OUTPUT = Path("taxonomy/controlled-terms.json")
HEADINGS = ("Material Type", "Businesses", "Cross Business", "Geographies", "Other Geographies", "Languages", "Industries", "Topics")


def text(element: ElementTree.Element) -> str:
    return "".join(node.text or "" for node in element.iter(f"{WORD_NS}t")).strip()


def paragraphs(root: ElementTree.Element) -> list[str]:
    return [value for value in (text(node) for node in root.iter(f"{WORD_NS}p")) if value]


def table_rows(root: ElementTree.Element) -> list[list[str]]:
    result = []
    for table in root.iter(f"{WORD_NS}tbl"):
        rows = []
        for row in table.findall(f"{WORD_NS}tr"):
            rows.append([text(cell) for cell in row.findall(f"{WORD_NS}tc")])
        result.append(rows)
    return result


def entry(value: str, description: str = "", **extra: object) -> dict:
    active = not bool(re.search(r"outdated|deprecated|should not be used", description, re.IGNORECASE))
    return {"value": value.strip(), "description": description.strip(), "active": active, **extra}


def section(lines: list[str], heading: str, next_headings: tuple[str, ...]) -> list[str]:
    start = next((index for index, line in enumerate(lines) if re.match(rf"^{re.escape(heading)}\s*\(", line)), None)
    if start is None:
        return []
    end = next((index for index in range(start + 1, len(lines)) if any(re.match(rf"^{re.escape(next_heading)}\s*\(", lines[index]) for next_heading in next_headings)), len(lines))
    return lines[start + 1:end]


def flat_section(lines: list[str], heading: str, next_headings: tuple[str, ...]) -> list[dict]:
    return [entry(line) for line in section(lines, heading, next_headings) if line and not line.startswith("Term")]


def main() -> None:
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else SOURCE
    output = Path(sys.argv[2]) if len(sys.argv) > 2 else OUTPUT
    with zipfile.ZipFile(source) as archive:
        root = ElementTree.fromstring(archive.read("word/document.xml"))
    lines = paragraphs(root)
    tables = table_rows(root)
    material_rows = next((rows for rows in tables if rows and rows[0][:2] == ["Term", "Description"]), [])
    collections_rows = next((rows for rows in tables if rows and rows[0][:1] == ["Collection"]), [])
    series_rows = next((rows for rows in tables if rows and rows[0][:1] == ["Series"]), [])
    topics_rows = next((rows for rows in tables if rows and rows[0][:1] == ["Topic"]), [])
    categories = {
        "materialTypes": [entry(row[0], row[1]) for row in material_rows[1:] if len(row) >= 2 and row[0]],
        "businesses": _businesses(lines),
        "crossBusinesses": flat_section(lines, "Cross Business", ("Geographies",)),
        "geographies": _geographies(lines),
        "otherGeographies": _grid_section(lines, "Other Geographies", ("Languages",)),
        "languages": _grid_section(lines, "Languages", ("Industries",)),
        "industries": _hierarchy_section(lines, "Industries", ("Collections", "Series", "Topics")),
        "collections": [_row_entry(row, "Collection") for row in collections_rows[1:] if row and row[0]],
        "series": [_row_entry(row, "Series") for row in series_rows[1:] if row and row[0]],
        "topics": [_row_entry(row, "Topic") for row in topics_rows[1:] if row and row[0]],
    }
    output.write_text(json.dumps({"source": str(source), "categories": categories}, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({category: len(values) for category, values in categories.items()}, indent=2))


def _businesses(lines: list[str]) -> list[dict]:
    values = section(lines, "Businesses", ("Cross Business",))
    result = []
    pending = None
    for line in values:
        match = re.match(r"^(.*?)\s+\[(.*?)\]$", line)
        if match:
            label, code = match.groups()
            if label.startswith("(") and pending:
                result[-1]["code"] = code
                continue
            parts = [part.strip() for part in label.split(">")]
            result.append(entry(parts[-1], parent=" > ".join(parts[:-1]), code=code, path=parts))
            pending = result[-1]
            continue
        if line.startswith("(") and pending:
            continue
        if not line.startswith("["):
            parts = [part.strip() for part in line.split(">")]
            pending = entry(parts[-1], parent=" > ".join(parts[:-1]), path=parts)
            result.append(pending)
    return result


def _geographies(lines: list[str]) -> list[dict]:
    values = section(lines, "Geographies", ("Other Geographies",))
    result = []
    for line in values:
        parts = re.split(r"\s{2,}", line, maxsplit=1)
        path = parts[0].split(">")
        result.append(entry(path[-1], parent=" > ".join(path[:-1]), countries=([country.strip() for country in parts[1].split(",")] if len(parts) == 2 else [])))
    return result


def _grid_section(lines: list[str], heading: str, next_headings: tuple[str, ...]) -> list[dict]:
    return flat_section(lines, heading, next_headings)


def _hierarchy_section(lines: list[str], heading: str, next_headings: tuple[str, ...]) -> list[dict]:
    result = []
    for line in section(lines, heading, next_headings):
        depth = len(line) - len(line.lstrip("›"))
        result.append(entry(line.lstrip("› "), depth=depth))
    return result


def _row_entry(row: list[str], key: str) -> dict:
    names = [key, "businesses", "geographies", "notes"]
    values = {name: value.strip() for name, value in zip(names, row) if value.strip()}
    if key in values:
        values["value"] = values[key]
    return values


if __name__ == "__main__":
    main()
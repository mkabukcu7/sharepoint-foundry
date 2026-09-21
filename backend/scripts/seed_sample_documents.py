import os
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "sample-documents"

SAMPLES = [
    {
        "title": "Example Company Benefits Overview FAQ",
        "area": "Benefits",
        "category": "FAQ",
        "theme": "Benefits Navigation",
        "author": "Sample Author 10",
        "language": "English",
        "sentiment": "Positive",
        "approval": "Approved",
        "age": 42,
        "extension": ".pdf",
        "body": "Frequently asked questions for employees looking for benefits forms, eligibility guidance, and common plan resources.",
    },
    {
        "title": "Claims Submission Procedure",
        "area": "Claims",
        "category": "Procedure",
        "theme": "Claims Intake",
        "author": "Sample Author 11",
        "language": "English",
        "sentiment": "Neutral",
        "approval": "Pending",
        "age": 255,
        "extension": ".pdf",
        "body": "Step by step claims intake workflow for internal operations teams and SME review.",
    },
    {
        "title": "Advisor Knowledge Playbook",
        "area": "Advisor Experience",
        "category": "Guide",
        "theme": "Advisor Enablement",
        "author": "Sample Author 12",
        "language": "English",
        "sentiment": "Positive",
        "approval": "Approved",
        "age": 92,
        "extension": ".docx",
        "body": "Advisor guidance for finding knowledge articles, forms, and client servicing resources.",
    },
    {
        "title": "Legacy Policy Servicing Notes",
        "area": "Policy Servicing",
        "category": "Reference",
        "theme": "Legacy Content",
        "author": "Unassigned",
        "language": "English",
        "sentiment": "Negative",
        "approval": "Not Recorded",
        "age": 640,
        "extension": ".docx",
        "body": "Older policy servicing notes that may conflict with current navigation and should be reviewed before use.",
    },
    {
        "title": "Plan Pricing Reference",
        "area": "Pricing",
        "category": "Reference",
        "theme": "Pricing",
        "author": "Finance Operations",
        "language": "English",
        "sentiment": "Neutral",
        "approval": "Restricted",
        "age": 120,
        "extension": ".pptx",
        "body": "Confidential pricing, fee, and rate card guidance for internal review only.",
    },
    {
        "title": "Privacy Compliance Checklist",
        "area": "Compliance",
        "category": "Checklist",
        "theme": "Privacy",
        "author": "Compliance Office",
        "language": "English",
        "sentiment": "Neutral",
        "approval": "Approved",
        "age": 375,
        "extension": ".pdf",
        "body": "Regulatory privacy checklist covering retention, confidential data handling, and non-public information.",
    },
    {
        "title": "Spanish Benefits Quick Guide",
        "area": "Benefits",
        "category": "Guide",
        "theme": "Multilingual Support",
        "author": "Employee Experience",
        "language": "Spanish",
        "sentiment": "Positive",
        "approval": "Approved",
        "age": 60,
        "extension": ".docx",
        "body": "Spanish-language benefits overview for employees who need quick navigation support.",
    },
    {
        "title": "Retirement Education Article",
        "area": "Retirement",
        "category": "Education",
        "theme": "Financial Education",
        "author": "Knowledge Team",
        "language": "English",
        "sentiment": "Positive",
        "approval": "Approved",
        "age": 188,
        "extension": ".pptx",
        "body": "Educational content explaining retirement options, rollover forms, and advisor handoff paths.",
    },
    {
        "title": "Contact Center Escalation Matrix",
        "area": "Operations",
        "category": "Matrix",
        "theme": "Escalation",
        "author": "Operations SME",
        "language": "English",
        "sentiment": "Neutral",
        "approval": "Pending",
        "age": 330,
        "extension": ".pdf",
        "body": "Escalation matrix for unresolved servicing questions, handoffs, and support ownership.",
    },
    {
        "title": "Knowledge Metadata Library Model",
        "area": "Knowledge Management",
        "category": "Taxonomy",
        "theme": "Metadata Library",
        "author": "Information Architecture",
        "language": "English",
        "sentiment": "Neutral",
        "approval": "Approved",
        "age": 15,
        "extension": ".docx",
        "body": "Simulated metadata library structure with columns for theme, language, author, sentiment, business area, review status, and approval status.",
    },
]


def seed_documents(target: Path = DOCS) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for index, sample in enumerate(SAMPLES, start=1):
        text = (
            f"{sample['title']}. "
            f"Business area: {sample['area']}. "
            f"Metadata category: {sample['category']}. "
            f"Theme: {sample['theme']}. "
            f"Author: {sample['author']}. "
            f"Language: {sample['language']}. "
            f"Sentiment: {sample['sentiment']}. "
            f"Approval status: {sample['approval']}. "
            f"Last reviewed age days: {sample['age']}. "
            f"{sample['body']}"
        )
        path = target / f"{index:02d}-{slug(sample['title'])}{sample['extension']}"
        if sample["extension"] == ".pdf":
            write_pdf(path, text)
        elif sample["extension"] == ".docx":
            write_docx(path, text)
        else:
            write_pptx(path, sample["title"], text)


def main() -> None:
    seed_documents()


def slug(value: str) -> str:
    return value.lower().replace(" ", "-").replace("/", "-")


def write_pdf(path: Path, text: str) -> None:
    safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    content = f"%PDF-1.4\n1 0 obj <<>> endobj\n2 0 obj << /Length {len(safe) + 48} >> stream\nBT /F1 12 Tf 72 720 Td ({safe}) Tj ET\nendstream endobj\ntrailer << /Root 1 0 R >>\n%%EOF"
    path.write_bytes(content.replace("\n", os.linesep).encode("latin-1", errors="ignore"))


def write_docx(path: Path, text: str) -> None:
    document_xml = f'<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>{escape(text)}</w:t></w:r></w:p></w:body></w:document>'
    with ZipFile(path, "w", ZIP_DEFLATED) as zf:
        write_archive_entry(zf, "[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        write_archive_entry(zf, "word/document.xml", document_xml)


def write_pptx(path: Path, title: str, text: str) -> None:
    slide_xml = f'<?xml version="1.0" encoding="UTF-8"?><p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>{escape(title)}</a:t></a:r></a:p><a:p><a:r><a:t>{escape(text)}</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>'
    with ZipFile(path, "w", ZIP_DEFLATED) as zf:
        write_archive_entry(zf, "[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/><Override PartName="/ppt/slides/slide1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/></Types>')
        write_archive_entry(zf, "ppt/slides/slide1.xml", slide_xml)


def write_archive_entry(archive: ZipFile, name: str, content: str) -> None:
    info = ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
    info.compress_type = ZIP_DEFLATED
    archive.writestr(info, content)


def escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


if __name__ == "__main__":
    main()

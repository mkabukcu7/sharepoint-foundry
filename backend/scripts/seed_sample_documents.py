from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "sample-documents"

SAMPLES = [
    ("Beneficiary Change Form Guide", "Policy Servicing", "Customer", 45, "Explains beneficiary change forms, identity checks, and policyholder next steps."),
    ("Advisor Portal Search Playbook", "Advisor Experience", "Advisor", 130, "Advisor content for finding client policy documents, service options, and education resources."),
    ("Claims Intake Procedure", "Claims", "Internal Operations", 410, "Internal claims intake workflow with SME review expectations and stale process notes."),
    ("Retirement Rollover FAQ", "Retirement", "Customer", 220, "Customer FAQ for retirement rollover options, tax forms, and advisor handoff."),
    ("Pricing Exception Matrix", "Pricing", "Internal Operations", 30, "Confidential pricing and fee guidance for exceptions, commission notes, and internal approval."),
    ("Privacy and Compliance Overview", "Compliance", "Internal Operations", 500, "Regulatory privacy content covering data use, retention, and non-public customer information."),
    ("Life Insurance Product Basics", "Insurance", "Customer", 80, "Educational content for life insurance policies, beneficiaries, and common forms."),
    ("Contact Center Escalation Guide", "Policy Servicing", "Internal Operations", 370, "Support escalation workflow for policy servicing and unresolved customer issues."),
    ("Investment Education Landing Page", "Wealth", "Customer", 160, "Educational investment concepts and advisor-connect calls to action."),
    ("Tax Forms Knowledge Article", "Forms", "Customer", 395, "Guidance for common tax forms, document download paths, and seasonal servicing questions."),
    ("Advisor Compliance Checklist", "Compliance", "Advisor", 250, "Advisor-facing compliance checklist for approved language and review history."),
    ("Death Claims Customer Journey", "Claims", "Customer", 25, "Customer journey for death claims, required forms, and compassionate service guidance."),
]


def main() -> None:
    DOCS.mkdir(parents=True, exist_ok=True)
    for i, (title, area, audience, age_days, body) in enumerate(SAMPLES, start=1):
        ext = [".pdf", ".docx", ".pptx"][i % 3]
        owner = ["Madi Smith", "Richard Talbot", "Zahil John", "Unassigned"][i % 4]
        text = f"{title}. Business area: {area}. Audience: {audience}. Content owner: {owner}. Last reviewed age days: {age_days}. {body}"
        path = DOCS / f"{i:02d}-{slug(title)}{ext}"
        if path.exists():
            continue
        if ext == ".pdf":
            write_pdf(path, text)
        elif ext == ".docx":
            write_docx(path, text)
        else:
            write_pptx(path, title, text)


def slug(value: str) -> str:
    return value.lower().replace(" ", "-").replace("/", "-")


def write_pdf(path: Path, text: str) -> None:
    safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    content = f"%PDF-1.4\n1 0 obj <<>> endobj\n2 0 obj << /Length {len(safe) + 48} >> stream\nBT /F1 12 Tf 72 720 Td ({safe}) Tj ET\nendstream endobj\ntrailer << /Root 1 0 R >>\n%%EOF"
    path.write_bytes(content.encode("latin-1", errors="ignore"))


def write_docx(path: Path, text: str) -> None:
    document_xml = f'<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>{escape(text)}</w:t></w:r></w:p></w:body></w:document>'
    with ZipFile(path, "w", ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        zf.writestr("word/document.xml", document_xml)


def write_pptx(path: Path, title: str, text: str) -> None:
    slide_xml = f'<?xml version="1.0" encoding="UTF-8"?><p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>{escape(title)}</a:t></a:r></a:p><a:p><a:r><a:t>{escape(text)}</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>'
    with ZipFile(path, "w", ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/><Override PartName="/ppt/slides/slide1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/></Types>')
        zf.writestr("ppt/slides/slide1.xml", slide_xml)


def escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


if __name__ == "__main__":
    main()


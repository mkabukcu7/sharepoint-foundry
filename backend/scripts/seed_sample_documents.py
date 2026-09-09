from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "sample-documents"

SAMPLES = [
    ("Beneficiary Change Form Guide", "Policy Servicing", "Customer", 45, "English", "United States", "Sample Author 01", "Positive", "Approved", "Explains identity checks and beneficiary updates. Clear examples help policyholders prepare complete forms on the first attempt."),
    ("Advisor Portal Search Playbook", "Advisor Experience", "Advisor", 130, "Spanish", "Spain", "Sample Author 02", "Positive", "Approved", "Guia para encontrar documentos de polizas y recursos educativos. Los ejemplos muestran busquedas rapidas para atender mejor a cada cliente."),
    ("Claims Intake Procedure", "Claims", "Internal Operations", 410, "English", "Canada", None, "Negative", "Review Required", "The intake queue has repeated handoff delays and several stale process notes. Operations teams need an updated escalation path before the next release."),
    ("Retirement Rollover FAQ", "Retirement", "Customer", 220, "French", "France", "Sample Author 03", "Neutral", "Pending", "Reponses aux questions sur les transferts de retraite et les formulaires fiscaux. Le client peut demander un rendez-vous avec un conseiller."),
    ("Pricing Exception Matrix", "Pricing", "Internal Operations", 30, "English", "United Kingdom", "Sample Author 04", "Neutral", "Approved", "Defines fee exception bands, evidence requirements, and approval limits. Teams must document the business rationale before submitting a request."),
    ("Privacy and Compliance Overview", "Compliance", "Internal Operations", 500, "German", "Germany", "Sample Author 05", "Negative", "Expired", "Uberblick uber Datenschutz, Aufbewahrung und vertrauliche Kundendaten. Veraltete Verweise mussen vor der weiteren Nutzung aktualisiert werden."),
    ("Life Insurance Product Basics", "Insurance", "Customer", 80, "Portuguese", "Brazil", "Sample Author 06", "Positive", "Approved", "Apresenta conceitos de seguro de vida, beneficiarios e formularios comuns. Exemplos simples ajudam familias a comparar opcoes com confianca."),
    ("Contact Center Escalation Guide", "Policy Servicing", "Internal Operations", 370, "English", "Canada", None, "Negative", "Review Required", "Unresolved service cases are exceeding response targets. The guide identifies escalation checkpoints but requires updated ownership assignments."),
    ("Investment Education Landing Page", "Wealth", "Customer", 160, "Spanish", "Mexico", "Sample Author 07", "Positive", "Approved", "Contenido educativo sobre diversificacion, riesgo y objetivos financieros. Invita al cliente a conversar con un asesor antes de tomar decisiones."),
    ("Tax Forms Knowledge Article", "Forms", "Customer", 395, "French", "Canada", "Sample Author 08", "Neutral", "Review Required", "Guide des formulaires fiscaux courants et des chemins de telechargement. Certaines dates saisonnieres doivent etre confirmees avant publication."),
    ("Advisor Compliance Checklist", "Compliance", "Advisor", 250, "English", "India", "Sample Author 09", "Neutral", "Pending", "Lists required disclosures, approved phrases, and evidence checks. Advisors should record completion before sharing recommendations."),
    ("Death Claims Customer Journey", "Claims", "Customer", 25, "English", "United States", None, "Positive", "Approved", "Provides compassionate guidance for required forms and next steps. Families receive a clear timeline and a dedicated support contact."),
]


def main() -> None:
    DOCS.mkdir(parents=True, exist_ok=True)
    for i, (title, area, audience, age_days, language, country, author, sentiment, approval, body) in enumerate(SAMPLES, start=1):
        ext = [".pdf", ".docx", ".pptx"][i % 3]
        owner = ["Content Owner 01", "Content Owner 02", "Content Owner 03", "Unassigned"][i % 4]
        author_field = f" Author: {author}." if author else ""
        text = f"{title}. Business area: {area}. Audience: {audience}. Content owner: {owner}.{author_field} Language: {language}. Country of origin: {country}. Sentiment: {sentiment}. Approval status: {approval}. Last reviewed age days: {age_days}. {body}"
        path = DOCS / f"{i:02d}-{slug(title)}{ext}"
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


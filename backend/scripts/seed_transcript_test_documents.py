from pathlib import Path

from backend.scripts.seed_sample_documents import write_docx, write_pdf, write_pptx

ROOT = Path(__file__).resolve().parents[2]
TEST_DOCS = ROOT / "test-documents"
METADATA_LIBRARY = ROOT / "data" / "metadata-library-context.json"

TEST_SAMPLES = [
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


def main() -> None:
    TEST_DOCS.mkdir(parents=True, exist_ok=True)
    for index, sample in enumerate(TEST_SAMPLES, start=1):
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
        path = TEST_DOCS / f"{index:02d}-{slug(sample['title'])}{sample['extension']}"
        if sample["extension"] == ".pdf":
            write_pdf(path, text)
        elif sample["extension"] == ".docx":
            write_docx(path, text)
        else:
            write_pptx(path, sample["title"], text)
    METADATA_LIBRARY.write_text(
        """{
    "libraryName": "Example Company Knowledge Metadata Library",
  "columns": [
    "Document Name",
    "Theme",
    "Business Area",
    "Language",
    "Author",
    "Sentiment",
    "Metadata Category",
    "Review Status",
    "Approval Status",
    "Last Reviewed Age Days"
  ],
  "demoPurpose": "Simulates the metadata library structure discussed in the meeting transcript."
}
""",
        encoding="utf-8",
    )


def slug(value: str) -> str:
    return value.lower().replace(" ", "-").replace("/", "-")


if __name__ == "__main__":
    main()

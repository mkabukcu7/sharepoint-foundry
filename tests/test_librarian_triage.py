from datetime import datetime, timedelta, timezone

from backend.app.services.librarian_triage import TriageSettings, triage_staging
from backend.app.services.taxonomy import Taxonomy


NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
SETTINGS = TriageSettings(confidence_threshold=0.7, review_sla_days=5)

TAXONOMY = Taxonomy(
    {
        "categories": {
            "materialTypes": [{"value": "Report", "active": True}],
            "topics": [{"value": "Skills", "active": True}],
            "businesses": [{"value": "Work & Rewards", "active": True}],
            "industries": [],
            "geographies": [{"value": "Global", "active": True}],
            "collections": [],
            "languages": [{"value": "English", "active": True}],
        }
    }
)

GOOD_SUMMARY = (
    "A practical overview of the global skills framework, covering job architecture, "
    "capability mapping and the rollout sequence for participating business units."
)


def _document(
    name: str = "Skills Overview.pptx",
    staged_days_ago: int = 1,
    classification: dict | None = None,
    review_decisions: str = "accepted",
) -> dict:
    staged_at = (NOW - timedelta(days=staged_days_ago)).isoformat().replace("+00:00", "Z")
    fields = {
        key: {"label": key, "value": "Set", "reviewDecision": review_decisions}
        for key in ("businessArea", "audience", "language", "author", "countryOfOrigin")
    }
    return {
        "documentName": name,
        "sharePointStage": {"status": "staged", "stagedAt": staged_at},
        "metadataReview": {"status": "needs-review", "fields": fields},
        "wtwClassification": classification
        if classification is not None
        else {
            "summary": GOOD_SUMMARY,
            "materialType": {"value": "Report", "confidence": 0.92, "evidence": "Cover page"},
            "topics": [{"value": "Skills", "confidence": 0.88, "evidence": "Section 2"}],
            "languages": [{"value": "English", "confidence": 0.99, "evidence": "Body text"}],
            "reviewRequired": False,
            "riskFlags": [],
        },
    }


def _triage(documents: list[dict]) -> dict:
    return triage_staging(documents, TAXONOMY, SETTINGS, NOW)


def test_clean_document_is_ready_for_approval() -> None:
    report = _triage([_document()])

    item = report["items"][0]
    assert item["disposition"] == "ready-for-approval"
    assert item["blockers"] == []
    assert report["counts"]["readyForApproval"] == 1
    assert "only after that approval" in item["recommendedAction"]


def test_only_staged_documents_are_triaged() -> None:
    published = _document(name="Published.docx")
    published["sharePointStage"] = {"status": "revision-applied"}

    report = _triage([_document(), published, {"documentName": "No stage info.docx"}])

    assert [item["documentName"] for item in report["items"]] == ["Skills Overview.pptx"]


def test_tags_outside_the_taxonomy_are_flagged() -> None:
    document = _document(
        classification={
            "summary": GOOD_SUMMARY,
            "materialType": {"value": "Report", "confidence": 0.9, "evidence": "Cover"},
            "topics": [{"value": "Invented Topic", "confidence": 0.95, "evidence": "None"}],
            "reviewRequired": False,
            "riskFlags": [],
        }
    )

    item = _triage([document])["items"][0]

    assert item["disposition"] == "needs-attention"
    assert item["tags"]["unknown"][0]["value"] == "Invented Topic"
    assert any("outside the controlled taxonomy" in blocker for blocker in item["blockers"])


def test_low_confidence_tags_are_flagged() -> None:
    document = _document(
        classification={
            "summary": GOOD_SUMMARY,
            "materialType": {"value": "Report", "confidence": 0.41, "evidence": "Unclear"},
            "topics": [{"value": "Skills", "confidence": 0.9, "evidence": "Section 2"}],
            "reviewRequired": False,
            "riskFlags": [],
        }
    )

    item = _triage([document])["items"][0]

    assert item["disposition"] == "needs-attention"
    assert item["confidence"]["lowest"] == 0.41
    assert item["confidence"]["belowThreshold"][0]["field"] == "materialType"


def test_missing_and_overlong_summaries_are_flagged() -> None:
    short = _document(name="Short.docx", classification={"summary": "Too short.", "reviewRequired": False})
    absent = _document(name="Absent.docx", classification={"summary": "", "reviewRequired": False})

    items = {item["documentName"]: item for item in _triage([short, absent])["items"]}

    assert any("too short" in issue for issue in items["Short.docx"]["summary"]["issues"])
    assert items["Absent.docx"]["summary"]["issues"] == ["No summary was generated."]


def test_required_classification_fields_must_be_present() -> None:
    document = _document(
        classification={"summary": GOOD_SUMMARY, "reviewRequired": False, "riskFlags": []}
    )

    item = _triage([document])["items"][0]

    assert item["tags"]["missingRequiredFields"] == ["materialType", "topics"]


def test_risk_flags_and_review_required_block_approval() -> None:
    document = _document(
        classification={
            "summary": GOOD_SUMMARY,
            "materialType": {"value": "Report", "confidence": 0.95, "evidence": "Cover"},
            "topics": [{"value": "Skills", "confidence": 0.95, "evidence": "Section 2"}],
            "reviewRequired": True,
            "riskFlags": ["Client names present"],
        }
    )

    item = _triage([document])["items"][0]

    assert item["disposition"] == "needs-attention"
    assert item["riskFlags"] == ["Client names present"]
    assert any("requiring review" in blocker for blocker in item["blockers"])


def test_unresolved_review_fields_block_approval() -> None:
    item = _triage([_document(review_decisions="pending")])["items"][0]

    assert item["disposition"] == "needs-attention"
    assert item["review"]["unresolvedFields"]
    assert any("Unresolved review fields" in blocker for blocker in item["blockers"])


def test_overdue_reviews_are_chased_and_ranked_first() -> None:
    fresh = _document(name="Fresh.docx", staged_days_ago=0)
    overdue = _document(name="Overdue.docx", staged_days_ago=9, review_decisions="pending")

    report = _triage([fresh, overdue])

    assert report["counts"]["overdue"] == 1
    assert report["items"][0]["documentName"] == "Overdue.docx"
    assert report["items"][0]["review"]["state"] == "overdue"
    assert "Overdue by 4 day(s)" in report["items"][0]["recommendedAction"]


def test_document_approaching_the_sla_is_marked_due() -> None:
    item = _triage([_document(staged_days_ago=4, review_decisions="pending")])["items"][0]

    assert item["review"]["state"] == "due"


def test_missing_taxonomy_is_reported_rather_than_assumed() -> None:
    report = triage_staging([_document()], None, SETTINGS, NOW)

    item = report["items"][0]
    assert item["tags"]["validated"] is False
    assert item["disposition"] == "needs-attention"
    assert any("taxonomy is unavailable" in blocker for blocker in item["blockers"])


def test_files_in_staging_without_a_record_are_surfaced() -> None:
    staged_files = [
        {"documentName": "Never Classified.pptx", "lastModifiedDateTime": "2026-09-20T00:00:00Z"},
        {"documentName": "Skills Overview.pptx", "lastModifiedDateTime": "2026-09-27T00:00:00Z"},
    ]

    report = triage_staging([_document()], TAXONOMY, SETTINGS, NOW, staged_files=staged_files)

    items = {item["documentName"]: item for item in report["items"]}
    assert report["stagingReconciled"] is True
    assert report["counts"]["notClassified"] == 1
    assert items["Never Classified.pptx"]["source"] == "staging-folder"
    assert items["Never Classified.pptx"]["blockers"] == [
        "This file is in Staging but has not been classified yet."
    ]
    assert items["Never Classified.pptx"]["review"]["state"] == "overdue"
    assert items["Skills Overview.pptx"]["source"] == "record"


def test_record_without_a_matching_staging_file_is_flagged_as_drift() -> None:
    report = triage_staging([_document()], TAXONOMY, SETTINGS, NOW, staged_files=[])

    item = report["items"][0]
    assert item["disposition"] == "needs-attention"
    assert any("not in the Staging folder" in blocker for blocker in item["blockers"])


def test_reconciliation_is_skipped_when_the_listing_is_unavailable() -> None:
    report = triage_staging([_document()], TAXONOMY, SETTINGS, NOW, staged_files=None)

    assert report["stagingReconciled"] is False
    assert report["items"][0]["disposition"] == "ready-for-approval"


def test_settings_are_read_from_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("LIBRARIAN_CONFIDENCE_THRESHOLD", "0.9")
    monkeypatch.setenv("LIBRARIAN_REVIEW_SLA_DAYS", "3")

    settings = TriageSettings.from_environment()

    assert settings.confidence_threshold == 0.9
    assert settings.review_sla_days == 3


def test_invalid_settings_fall_back_to_defaults(monkeypatch) -> None:
    monkeypatch.setenv("LIBRARIAN_CONFIDENCE_THRESHOLD", "not-a-number")
    monkeypatch.setenv("LIBRARIAN_REVIEW_SLA_DAYS", "9999")

    settings = TriageSettings.from_environment()

    assert settings.confidence_threshold == 0.7
    assert settings.review_sla_days == 5

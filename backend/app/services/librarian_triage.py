"""Knowledge Librarian intake triage over the Staging folder.

The job is deliberately read-only. It inspects each staged document, checks the
generated summary, validates every proposed tag against the controlled taxonomy,
reviews classification confidence, and ranks the result so a human reviewer knows
what to approve first. Publishing, archiving and staged-source removal stay behind
the existing approval gate in `writeback.apply`.

It also chases outstanding reviews: anything waiting longer than the configured
service level is reported as due or overdue.
"""

import os
from dataclasses import dataclass
from datetime import datetime, timezone

from backend.app.services.reviews import ALL_REVIEW_FIELDS, metadata_review
from backend.app.services.taxonomy import Taxonomy
from backend.app.services.wtw_classifier import CLASSIFICATION_FIELDS

DEFAULT_CONFIDENCE_THRESHOLD = 0.7
DEFAULT_REVIEW_SLA_DAYS = 5
MIN_SUMMARY_CHARACTERS = 80
MAX_SUMMARY_CHARACTERS = 1200
REQUIRED_CLASSIFICATION_FIELDS = ("materialType", "topics")


@dataclass(frozen=True)
class TriageSettings:
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD
    review_sla_days: int = DEFAULT_REVIEW_SLA_DAYS

    @classmethod
    def from_environment(cls) -> "TriageSettings":
        return cls(
            confidence_threshold=_float_env(
                "LIBRARIAN_CONFIDENCE_THRESHOLD", DEFAULT_CONFIDENCE_THRESHOLD, 0.0, 1.0
            ),
            review_sla_days=int(_float_env("LIBRARIAN_REVIEW_SLA_DAYS", DEFAULT_REVIEW_SLA_DAYS, 1, 365)),
        )


def triage_staging(
    documents: list[dict],
    taxonomy: Taxonomy | None = None,
    settings: TriageSettings | None = None,
    now: datetime | None = None,
    staged_files: list[dict] | None = None,
) -> dict:
    """Assess everything in Staging and return a ranked reviewer worklist.

    `staged_files` is the live SharePoint Staging listing. When supplied it is
    reconciled against the stored classification records so that files sitting in
    Staging without a record are surfaced instead of silently ignored.
    """
    settings = settings or TriageSettings()
    moment = now or datetime.now(timezone.utc)

    records = {
        str(document.get("documentName", "")).casefold(): document
        for document in documents
        if _is_staged(document)
    }
    items = [
        _assess_document(document, taxonomy, settings, moment)
        for document in records.values()
    ]

    if staged_files is not None:
        present = {str(entry.get("documentName", "")).casefold() for entry in staged_files}
        for entry in staged_files:
            key = str(entry.get("documentName", "")).casefold()
            if key and key not in records:
                items.append(_unclassified_item(entry, settings, moment))
        for item in items:
            if item["documentName"].casefold() not in present and item["source"] == "record":
                item["blockers"].append(
                    "The stored record says this document is staged, but it is not in the Staging folder."
                )
                item["disposition"] = "needs-attention"
                item["recommendedAction"] = (
                    "Reconcile the record with SharePoint: the file is no longer in Staging."
                )

    items.sort(key=lambda item: (-item["priority"], item["documentName"].casefold()))
    return {
        "generatedAt": moment.isoformat().replace("+00:00", "Z"),
        "confidenceThreshold": settings.confidence_threshold,
        "reviewSlaDays": settings.review_sla_days,
        "stagingReconciled": staged_files is not None,
        "counts": {
            "staged": len(items),
            "readyForApproval": sum(1 for item in items if item["disposition"] == "ready-for-approval"),
            "needsAttention": sum(1 for item in items if item["disposition"] == "needs-attention"),
            "notClassified": sum(1 for item in items if item["source"] == "staging-folder"),
            "overdue": sum(1 for item in items if item["review"]["state"] == "overdue"),
            "dueSoon": sum(1 for item in items if item["review"]["state"] == "due"),
        },
        "items": items,
    }


def _unclassified_item(entry: dict, settings: TriageSettings, moment: datetime) -> dict:
    """A file sitting in Staging that has never been classified by the app."""
    waiting = _parse_time(entry.get("lastModifiedDateTime"))
    waiting_days = max(0, (moment - waiting).days) if waiting else None
    state = (
        "overdue"
        if waiting_days is not None and waiting_days >= settings.review_sla_days
        else "due"
        if waiting_days is not None and waiting_days >= settings.review_sla_days - 1
        else "pending"
    )
    review = {
        "state": state,
        "status": "not-started",
        "unresolvedFields": sorted(ALL_REVIEW_FIELDS.values()),
        "waitingDays": waiting_days,
        "slaDays": settings.review_sla_days,
        "reviewedBy": None,
        "reviewedAt": None,
    }
    blockers = ["This file is in Staging but has not been classified yet."]
    return {
        "documentName": str(entry.get("documentName", "")),
        "source": "staging-folder",
        "disposition": "needs-attention",
        "priority": _priority(review, {"belowThreshold": []}, [], blockers),
        "summary": {"text": "", "characters": 0, "issues": ["No summary has been generated."]},
        "tags": {"accepted": [], "unknown": [], "missingRequiredFields": [], "validated": False},
        "confidence": {
            "threshold": settings.confidence_threshold,
            "lowest": None,
            "scored": 0,
            "belowThreshold": [],
        },
        "review": review,
        "riskFlags": [],
        "blockers": blockers,
        "webUrl": entry.get("webUrl", ""),
        "recommendedAction": (
            "Import and classify this file so its summary, tags and confidence can be reviewed."
        ),
    }


def _is_staged(document: dict) -> bool:
    stage = document.get("sharePointStage")
    return isinstance(stage, dict) and stage.get("status") == "staged"


def _assess_document(
    document: dict,
    taxonomy: Taxonomy | None,
    settings: TriageSettings,
    moment: datetime,
) -> dict:
    classification = document.get("wtwClassification")
    classification = classification if isinstance(classification, dict) else {}
    blockers: list[str] = []

    summary = _check_summary(document, classification, blockers)
    tags = _check_tags(classification, taxonomy, blockers)
    confidence = _check_confidence(classification, settings, blockers)
    review = _check_review(document, settings, moment, blockers)
    risk_flags = [str(flag) for flag in classification.get("riskFlags", []) if str(flag).strip()]

    if classification.get("reviewRequired") is True:
        blockers.append("The classifier marked this document as requiring review.")
    if risk_flags:
        blockers.append(f"Risk flags raised: {', '.join(risk_flags)}.")
    if not classification:
        blockers.append("No taxonomy classification has been generated for this document.")

    disposition = "ready-for-approval" if not blockers else "needs-attention"
    return {
        "documentName": document.get("documentName", ""),
        "source": "record",
        "disposition": disposition,
        "priority": _priority(review, confidence, risk_flags, blockers),
        "summary": summary,
        "tags": tags,
        "confidence": confidence,
        "review": review,
        "riskFlags": risk_flags,
        "blockers": blockers,
        "recommendedAction": _recommended_action(disposition, review, blockers),
    }


def _check_summary(document: dict, classification: dict, blockers: list[str]) -> dict:
    text = str(classification.get("summary") or document.get("summary") or "").strip()
    issues: list[str] = []
    if not text:
        issues.append("No summary was generated.")
    elif len(text) < MIN_SUMMARY_CHARACTERS:
        issues.append(f"Summary is only {len(text)} characters; it is too short to be useful.")
    elif len(text) > MAX_SUMMARY_CHARACTERS:
        issues.append(f"Summary is {len(text)} characters; it exceeds the {MAX_SUMMARY_CHARACTERS} character limit.")
    blockers.extend(issues)
    return {"text": text, "characters": len(text), "issues": issues}


def _check_tags(classification: dict, taxonomy: Taxonomy | None, blockers: list[str]) -> dict:
    accepted: list[dict] = []
    unknown: list[dict] = []
    missing: list[str] = []

    for field, category in CLASSIFICATION_FIELDS.items():
        for candidate in _candidates(classification.get(field)):
            value = candidate.get("value")
            if value is None or not str(value).strip():
                continue
            entry = {
                "field": field,
                "value": str(value),
                "confidence": _confidence_of(candidate),
                "evidence": str(candidate.get("evidence", "")).strip(),
            }
            if taxonomy is not None and not _in_taxonomy(taxonomy, category, str(value)):
                unknown.append(entry)
            else:
                accepted.append(entry)
        if field in REQUIRED_CLASSIFICATION_FIELDS and not _has_value(classification.get(field)):
            missing.append(field)

    if unknown:
        blockers.append(
            "Tags outside the controlled taxonomy: "
            + ", ".join(f"{entry['field']}='{entry['value']}'" for entry in unknown)
            + "."
        )
    if missing:
        blockers.append(f"Required classification fields are empty: {', '.join(missing)}.")
    if taxonomy is None:
        blockers.append("The controlled taxonomy is unavailable, so tags could not be validated.")

    return {
        "accepted": accepted,
        "unknown": unknown,
        "missingRequiredFields": missing,
        "validated": taxonomy is not None,
    }


def _check_confidence(classification: dict, settings: TriageSettings, blockers: list[str]) -> dict:
    scores: list[dict] = []
    for field in CLASSIFICATION_FIELDS:
        for candidate in _candidates(classification.get(field)):
            if candidate.get("value") is None:
                continue
            scores.append(
                {
                    "field": field,
                    "value": str(candidate.get("value")),
                    "confidence": _confidence_of(candidate),
                }
            )
    below = [score for score in scores if score["confidence"] < settings.confidence_threshold]
    if below:
        blockers.append(
            f"Confidence below {settings.confidence_threshold:.2f}: "
            + ", ".join(f"{score['field']}='{score['value']}' ({score['confidence']:.2f})" for score in below)
            + "."
        )
    lowest = min((score["confidence"] for score in scores), default=None)
    return {
        "threshold": settings.confidence_threshold,
        "lowest": lowest,
        "scored": len(scores),
        "belowThreshold": below,
    }


def _check_review(
    document: dict,
    settings: TriageSettings,
    moment: datetime,
    blockers: list[str],
) -> dict:
    review = document.get("metadataReview")
    review = review if isinstance(review, dict) else metadata_review(document, "")
    fields = review.get("fields") if isinstance(review.get("fields"), dict) else {}
    unresolved = [
        ALL_REVIEW_FIELDS.get(key, key)
        for key, entry in fields.items()
        if not isinstance(entry, dict) or entry.get("reviewDecision") not in {"accepted", "edited"}
    ]
    if unresolved:
        blockers.append(f"Unresolved review fields: {', '.join(sorted(unresolved))}.")

    waiting_days = _waiting_days(document, review, moment)
    if review.get("status") == "approved" and not unresolved:
        state = "approved"
    elif waiting_days is None:
        state = "pending"
    elif waiting_days >= settings.review_sla_days:
        state = "overdue"
    elif waiting_days >= settings.review_sla_days - 1:
        state = "due"
    else:
        state = "pending"

    return {
        "state": state,
        "status": review.get("status", "needs-review"),
        "unresolvedFields": sorted(unresolved),
        "waitingDays": waiting_days,
        "slaDays": settings.review_sla_days,
        "reviewedBy": review.get("reviewedBy"),
        "reviewedAt": review.get("reviewedAt"),
    }


def _waiting_days(document: dict, review: dict, moment: datetime) -> int | None:
    stage = document.get("sharePointStage")
    started = _parse_time(stage.get("stagedAt")) if isinstance(stage, dict) else None
    if started is None:
        started = _parse_time(review.get("reviewedAt"))
    if started is None:
        return None
    return max(0, (moment - started).days)


def _priority(review: dict, confidence: dict, risk_flags: list[str], blockers: list[str]) -> int:
    score = 0
    if review["state"] == "overdue":
        score += 100
    elif review["state"] == "due":
        score += 60
    score += min(int((review["waitingDays"] or 0)), 30)
    if risk_flags:
        score += 40
    score += 5 * len(confidence.get("belowThreshold", []))
    if not blockers:
        score += 10
    return score


def _recommended_action(disposition: str, review: dict, blockers: list[str]) -> str:
    if disposition == "ready-for-approval":
        return (
            "Summary, tags and confidence all pass. Queue for reviewer approval; "
            "publishing, archiving and staged cleanup run only after that approval."
        )
    if review["state"] == "overdue":
        return (
            f"Overdue by {(review['waitingDays'] or 0) - review['slaDays']} day(s) past the "
            f"{review['slaDays']}-day service level. Chase the reviewer, then resolve: {blockers[0]}"
        )
    return f"Hold in Staging until resolved: {blockers[0]}"


def _candidates(raw: object) -> list[dict]:
    if isinstance(raw, dict):
        return [raw]
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    return []


def _has_value(raw: object) -> bool:
    return any(
        candidate.get("value") is not None and str(candidate.get("value")).strip()
        for candidate in _candidates(raw)
    )


def _confidence_of(candidate: dict) -> float:
    value = candidate.get("confidence")
    return float(value) if isinstance(value, (int, float)) else 0.0


def _in_taxonomy(taxonomy: Taxonomy, category: str, value: str) -> bool:
    try:
        choices = taxonomy.choices(category)
    except (KeyError, AttributeError, ValueError):
        return False
    return any(str(choice).casefold() == value.casefold() for choice in choices)


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _float_env(name: str, default: float, minimum: float, maximum: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if minimum <= value <= maximum else default

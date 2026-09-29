import os
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock

from backend.app.services.extractors import extract_labeled_value, extract_text
from backend.app.services.storage import load_documents, save_documents
from backend.app.services.taxonomy import Taxonomy, TaxonomyError
from backend.app.services.writeback import SharePointWritebackService


REVIEW_FIELDS = {
    "businessArea": "Business area",
    "audience": "Audience",
    "language": "Language",
    "author": "Author",
    "countryOfOrigin": "Country of origin",
}

TAXONOMY_REVIEW_FIELDS: dict[str, dict] = {
    "materialType": {"label": "Material type", "category": "materialTypes", "multi": False},
    "topics": {"label": "Topics", "category": "topics", "multi": True},
    "businesses": {"label": "Businesses", "category": "businesses", "multi": True},
    "industries": {"label": "Industries", "category": "industries", "multi": True},
    "geographies": {"label": "Geographies", "category": "geographies", "multi": True},
    "collections": {"label": "Collections", "category": "collections", "multi": True},
    "languages": {"label": "Taxonomy languages", "category": "languages", "multi": True},
}

REQUIRED_TAXONOMY_FIELDS = ("materialType", "topics")
ALL_REVIEW_FIELDS = {**REVIEW_FIELDS, **{key: spec["label"] for key, spec in TAXONOMY_REVIEW_FIELDS.items()}}
REVIEW_DECISIONS = {"accepted", "edited", "rejected"}
# Reentrant so a caller already holding the lock can nest a read-modify-write.
_review_lock = RLock()


def review_lock():
    """The lock guarding read-modify-write cycles over the document catalog.

    Every writer must hold it. Saving a catalog snapshot that was read before a
    concurrent approval would otherwise silently discard that approval.
    """
    return _review_lock


def _load_taxonomy() -> Taxonomy | None:
    path = Path(os.getenv("FOUNDRY_TAXONOMY_PATH", "taxonomy/controlled-terms.json"))
    try:
        return Taxonomy.from_file(path)
    except (OSError, ValueError):
        return None


def _candidates(raw: object) -> list[dict]:
    if raw is None:
        return []
    items = raw if isinstance(raw, list) else [raw]
    candidates = []
    for item in items:
        if isinstance(item, dict):
            candidates.append(item)
        elif item is not None and str(item).strip():
            candidates.append({"value": str(item).strip()})
    return [item for item in candidates if item.get("value") is not None and str(item["value"]).strip()]


def _normalize_taxonomy_value(value: object, multi: bool) -> list[str] | str:
    if multi:
        if value is None:
            return []
        items = value if isinstance(value, list) else [part for part in str(value).split(";")]
        seen: list[str] = []
        for item in items:
            text = str(item).strip()
            if text and text not in seen:
                seen.append(text)
        return seen
    if isinstance(value, list):
        return str(value[0]).strip() if value else ""
    return str(value).strip() if value is not None else ""


def metadata_review(document: dict, text: str, taxonomy: Taxonomy | None = None) -> dict:
    stored = document.get("metadataReview") if isinstance(document.get("metadataReview"), dict) else {}
    stored_fields = stored.get("fields") if isinstance(stored.get("fields"), dict) else {}
    fields = {}
    for key, label in REVIEW_FIELDS.items():
        value = _text(document.get(key))
        labeled_value = extract_labeled_value(text, label)
        support = "grounded" if labeled_value else "missing" if value == "Unknown" else "inferred"
        existing = stored_fields.get(key) if isinstance(stored_fields.get(key), dict) else {}
        fields[key] = {
            "label": label,
            "kind": "text",
            "value": _text(existing.get("value")) if existing else value,
            "originalValue": _text(existing.get("originalValue")) if existing else value,
            "support": existing.get("support") if existing.get("support") in {"grounded", "inferred", "missing", "confirmed"} else support,
            "evidence": existing.get("evidence") or (f"{label}: {labeled_value}" if labeled_value else "No explicit labeled evidence found."),
            "reviewDecision": existing.get("reviewDecision") if existing.get("reviewDecision") in REVIEW_DECISIONS else "pending",
            "decidedAt": existing.get("decidedAt"),
        }

    if taxonomy is None:
        taxonomy = _load_taxonomy()
    fields.update(_taxonomy_fields(document, stored_fields, taxonomy))
    flags = _classification_flags(document, fields, stored, taxonomy)

    return {
        "status": stored.get("status") if stored.get("status") in {"needs-review", "approved"} else "needs-review",
        "reviewedBy": stored.get("reviewedBy"),
        "reviewedAt": stored.get("reviewedAt"),
        "taxonomyAvailable": taxonomy is not None,
        "fields": fields,
        "classificationFlags": flags,
    }


def _taxonomy_fields(document: dict, stored_fields: dict, taxonomy: Taxonomy | None) -> dict:
    classification = document.get("wtwClassification")
    classification = classification if isinstance(classification, dict) else {}
    fields = {}
    for key, spec in TAXONOMY_REVIEW_FIELDS.items():
        multi = spec["multi"]
        candidates = _candidates(classification.get(key))
        suggested = [str(candidate["value"]).strip() for candidate in candidates]
        original = _normalize_taxonomy_value(suggested, multi)
        existing = stored_fields.get(key) if isinstance(stored_fields.get(key), dict) else {}
        value = _normalize_taxonomy_value(existing.get("value"), multi) if existing else original
        choices = taxonomy.choices(spec["category"]) if taxonomy is not None else []
        allowed = {choice.casefold() for choice in choices}
        present = value if multi else ([value] if value else [])
        unknown = [item for item in present if taxonomy is not None and item.casefold() not in allowed]
        confidences = [
            float(candidate["confidence"])
            for candidate in candidates
            if isinstance(candidate.get("confidence"), (int, float))
        ]
        evidence = "; ".join(
            str(candidate.get("evidence", "")).strip()
            for candidate in candidates
            if str(candidate.get("evidence", "")).strip()
        )
        decision = existing.get("reviewDecision") if existing.get("reviewDecision") in REVIEW_DECISIONS else "pending"
        if decision in {"accepted", "edited"}:
            support = "confirmed"
        elif decision == "rejected":
            support = "missing"
        elif not present:
            support = "missing"
        else:
            support = "grounded" if evidence else "inferred"
        fields[key] = {
            "label": spec["label"],
            "kind": "taxonomy-multi" if multi else "taxonomy-single",
            "category": spec["category"],
            "value": value,
            "originalValue": original,
            "choices": choices,
            "unknownValues": unknown,
            "required": key in REQUIRED_TAXONOMY_FIELDS,
            "confidence": min(confidences) if confidences else None,
            "support": support,
            "evidence": evidence or "No taxonomy evidence was recorded for this field.",
            "reviewDecision": decision,
            "decidedAt": existing.get("decidedAt"),
        }
    return fields


def _risk_flag_parts(flag: object) -> tuple[str, str]:
    """Split a risk flag into label and evidence.

    The classifier emits risk flags either as plain strings or as
    ``{"flag": ..., "evidence": ...}`` objects. Stringifying the object form
    would leak a Python repr into flag ids and reviewer-facing messages.
    """
    if isinstance(flag, dict):
        label = str(flag.get("flag") or flag.get("name") or flag.get("risk") or "").strip()
        evidence = str(flag.get("evidence") or "").strip()
        return label, evidence
    return str(flag).strip(), ""


def _classification_flags(document: dict, fields: dict, stored: dict, taxonomy: Taxonomy | None) -> list[dict]:
    classification = document.get("wtwClassification")
    classification = classification if isinstance(classification, dict) else {}
    stored_flags = stored.get("classificationFlags") if isinstance(stored.get("classificationFlags"), list) else []
    resolved = {
        entry.get("id"): entry
        for entry in stored_flags
        if isinstance(entry, dict) and entry.get("status") == "resolved"
    }

    derived: list[dict] = []
    if classification.get("reviewRequired") is True:
        derived.append({
            "id": "review-required",
            "type": "review-required",
            "acknowledgeOnly": True,
            "message": "The classifier marked this document as requiring human review.",
        })
    for flag in classification.get("riskFlags", []) or []:
        label, evidence = _risk_flag_parts(flag)
        if not label:
            continue
        message = f"Risk flag raised by the classifier: {label}."
        if evidence:
            message = f"{message} Evidence: {evidence}"
        derived.append({
            "id": f"risk:{label.casefold()}",
            "type": "risk",
            "acknowledgeOnly": True,
            "message": message,
        })
    for key, spec in TAXONOMY_REVIEW_FIELDS.items():
        field = fields.get(key, {})
        for value in field.get("unknownValues", []):
            derived.append({
                "id": f"unknown-term:{key}:{value.casefold()}",
                "type": "unknown-term",
                "acknowledgeOnly": False,
                "message": f"{spec['label']} contains '{value}', which is not an active taxonomy term. Edit the field to a valid term.",
            })
        if key in REQUIRED_TAXONOMY_FIELDS and not field.get("value"):
            derived.append({
                "id": f"missing-required:{key}",
                "type": "missing-required",
                "acknowledgeOnly": False,
                "message": f"{spec['label']} is required and is currently empty.",
            })
    if taxonomy is None:
        derived.append({
            "id": "taxonomy-unavailable",
            "type": "taxonomy-unavailable",
            "acknowledgeOnly": False,
            "message": (
                "The controlled taxonomy could not be loaded, so tags cannot be "
                "validated. Restore the taxonomy before approving this document."
            ),
        })

    flags = []
    for entry in derived:
        previous = resolved.get(entry["id"]) if entry["acknowledgeOnly"] else None
        flags.append({
            **entry,
            "status": "resolved" if previous else "pending",
            "resolvedBy": previous.get("resolvedBy") if previous else None,
            "resolvedAt": previous.get("resolvedAt") if previous else None,
            "note": previous.get("note") if previous else None,
        })
    return flags


def update_field_review(
    data_path: Path,
    source_dir: Path,
    document_name: str,
    field: str,
    decision: str,
    value: str | list[str] | None = None,
) -> dict:
    if field not in ALL_REVIEW_FIELDS:
        raise ValueError("Unsupported review field")
    if decision not in REVIEW_DECISIONS:
        raise ValueError("Unsupported review decision")
    taxonomy_spec = TAXONOMY_REVIEW_FIELDS.get(field)
    if taxonomy_spec is None:
        edited_value = (value or "").strip() if isinstance(value, str) or value is None else ""
        if decision == "edited" and not edited_value:
            raise ValueError("Edited value is required")
    else:
        edited_value = _normalize_taxonomy_value(value, taxonomy_spec["multi"])
        if decision == "edited" and not edited_value:
            raise ValueError("Edited value is required")

    with _review_lock:
        documents = load_documents(data_path)
        document = _find_document(documents, document_name)
        text = _document_text(source_dir, document_name)
        taxonomy = _load_taxonomy()
        review = metadata_review(document, text, taxonomy)
        field_review = review["fields"][field]

        if taxonomy_spec is not None:
            if decision == "edited":
                candidates = edited_value if taxonomy_spec["multi"] else [edited_value]
                if taxonomy is None:
                    raise ValueError("The controlled taxonomy is unavailable, so taxonomy fields cannot be edited")
                try:
                    canonical = taxonomy.validate(taxonomy_spec["category"], candidates)
                except TaxonomyError as error:
                    raise ValueError(str(error)) from error
                edited_value = canonical if taxonomy_spec["multi"] else canonical[0]
                field_review["value"] = edited_value
                field_review["support"] = "confirmed"
            elif decision == "rejected":
                field_review["value"] = [] if taxonomy_spec["multi"] else ""
                field_review["support"] = "missing"
            else:
                if field_review.get("unknownValues"):
                    raise ValueError(
                        f"{field_review['label']} contains terms outside the taxonomy; edit the field instead of accepting it"
                    )
                field_review["support"] = "confirmed"
        elif decision == "edited":
            document[field] = edited_value
            field_review["value"] = edited_value
            field_review["support"] = "confirmed"
        elif decision == "rejected":
            document[field] = "Unknown"
            field_review["value"] = "Unknown"
            field_review["support"] = "missing"
        else:
            field_review["support"] = "confirmed"

        field_review["reviewDecision"] = decision
        field_review["decidedAt"] = _timestamp()
        review["status"] = "needs-review"
        review["reviewedBy"] = None
        review["reviewedAt"] = None
        document["metadataReview"] = review
        # Recompute so derived flags (unknown terms, missing required fields) reflect this edit.
        document["metadataReview"] = metadata_review(document, text, taxonomy)
        save_documents(documents, data_path)
        return document


def resolve_classification_flag(
    data_path: Path,
    source_dir: Path,
    document_name: str,
    flag_id: str,
    reviewer: str,
    note: str | None = None,
) -> dict:
    reviewer = (reviewer or "").strip()
    if not reviewer:
        raise ValueError("A reviewer name is required to resolve a classification flag")
    with _review_lock:
        documents = load_documents(data_path)
        document = _find_document(documents, document_name)
        text = _document_text(source_dir, document_name)
        taxonomy = _load_taxonomy()
        review = metadata_review(document, text, taxonomy)
        flag = next((entry for entry in review["classificationFlags"] if entry.get("id") == flag_id), None)
        if flag is None:
            raise KeyError(flag_id)
        if not flag.get("acknowledgeOnly"):
            raise ValueError(
                "This flag clears itself once the underlying field is corrected; edit the field instead"
            )
        flag["status"] = "resolved"
        flag["resolvedBy"] = reviewer
        flag["resolvedAt"] = _timestamp()
        flag["note"] = (note or "").strip() or None
        review["status"] = "needs-review"
        review["reviewedBy"] = None
        review["reviewedAt"] = None
        document["metadataReview"] = review
        save_documents(documents, data_path)
        return document


def unresolved_review_items(review: dict) -> dict:
    fields = [
        field.get("label", key)
        for key, field in (review.get("fields") or {}).items()
        if field.get("reviewDecision") not in {"accepted", "edited"}
    ]
    flags = [
        flag.get("message", flag.get("id", ""))
        for flag in (review.get("classificationFlags") or [])
        if flag.get("status") != "resolved"
    ]
    return {"fields": fields, "flags": flags}


def approve_metadata_review(
    data_path: Path,
    source_dir: Path,
    document_name: str,
    writeback_service: SharePointWritebackService | None = None,
    reviewer: str = "",
) -> dict:
    reviewer = (reviewer or "").strip()
    if not reviewer:
        raise ValueError("A reviewer name is required to approve a document")
    with _review_lock:
        documents = load_documents(data_path)
        document = _find_document(documents, document_name)
        review = metadata_review(document, _document_text(source_dir, document_name))
        unresolved = unresolved_review_items(review)
        if unresolved["fields"]:
            raise ValueError(f"Resolve all review fields before approval: {', '.join(unresolved['fields'])}")
        if unresolved["flags"]:
            raise ValueError(f"Resolve all classification flags before approval: {'; '.join(unresolved['flags'])}")
        review["status"] = "approved"
        review["reviewedBy"] = reviewer
        review["reviewedAt"] = _timestamp()
        document["metadataReview"] = review
        document["approvedTaxonomy"] = {
            key: review["fields"][key]["value"]
            for key in TAXONOMY_REVIEW_FIELDS
            if key in review["fields"]
        }
        if writeback_service and document.get("sharePoint") and document.get("sharePointWritebackEnabled") is True:
            try:
                revision = document.get("sharePointRevision")
                replacement_content = None
                if isinstance(revision, dict) and revision.get("action") == "replace-existing":
                    replacement_path = source_dir / document_name
                    if not replacement_path.is_file():
                        raise ValueError("Approved SharePoint replacement is missing its staged source file")
                    replacement_content = replacement_path.read_bytes()
                writeback_service.apply(
                    document,
                    reviewer,
                    review["reviewedAt"],
                    replacement_content=replacement_content,
                )
            except Exception:
                save_documents(documents, data_path)
                raise
        save_documents(documents, data_path)
        return document


def get_sharepoint_version_candidate(
    data_path: Path,
    document_name: str,
    writeback_service: SharePointWritebackService,
) -> dict | None:
    with _review_lock:
        document = _find_document(load_documents(data_path), document_name)
        candidate = writeback_service.version_candidate(document)
        if candidate is None:
            return None
        return {
            "fileName": candidate.get("fileName"),
            "webUrl": candidate.get("webUrl"),
            "currentVersion": candidate.get("currentVersion"),
            "lastModifiedDateTime": candidate.get("lastModifiedDateTime"),
            "versions": candidate.get("versions", []),
        }


def update_version_action(
    data_path: Path,
    document_name: str,
    action: str,
    writeback_service: SharePointWritebackService,
) -> dict:
    with _review_lock:
        documents = load_documents(data_path)
        document = _find_document(documents, document_name)
        review = document.get("metadataReview") or {}
        previous_writeback = document.get("sharePointWriteback")
        if review.get("status") == "approved":
            if not isinstance(previous_writeback, dict) or previous_writeback.get("status") != "conflict":
                raise ValueError("Version action cannot change after approval")
            review["status"] = "needs-review"
            review["reviewedBy"] = None
            review["reviewedAt"] = None
            document["metadataReview"] = review
            document.pop("sharePointWriteback", None)
        try:
            updated = writeback_service.set_version_action(document, action)
        except Exception:
            save_documents(documents, data_path)
            raise
        save_documents(documents, data_path)
        return updated


def retry_metadata_writeback(
    data_path: Path,
    source_dir: Path,
    document_name: str,
    writeback_service: SharePointWritebackService,
    reviewer: str = "",
) -> dict:
    with _review_lock:
        documents = load_documents(data_path)
        document = _find_document(documents, document_name)
        state = document.get("sharePointWriteback")
        if not isinstance(state, dict) or state.get("status") not in {"failed", "conflict"}:
            raise ValueError("Only failed or conflicted writebacks can be retried")
        # A retry re-applies an existing approval, so it is attributed to the
        # reviewer who approved it rather than to whoever triggered the retry.
        review = document.get("metadataReview")
        recorded = review.get("reviewedBy") if isinstance(review, dict) else None
        reviewer = (reviewer or "").strip() or (recorded or "").strip()
        if not reviewer:
            raise ValueError("This document has no recorded approver; approve it again before retrying")
        revision = document.get("sharePointRevision")
        if (
            state.get("status") == "conflict"
            and isinstance(revision, dict)
            and revision.get("action") == "replace-existing"
        ):
            raise ValueError(
                "SharePoint revision target changed; retrieve current version details, update the plan, and approve again"
            )
        try:
            if state.get("status") == "conflict":
                writeback_service.refresh(document)
            replacement_content = None
            if isinstance(revision, dict) and revision.get("action") == "replace-existing":
                replacement_path = source_dir / document_name
                if not replacement_path.is_file():
                    raise ValueError("Approved SharePoint replacement is missing its staged source file")
                replacement_content = replacement_path.read_bytes()
            return writeback_service.apply(
                document,
                reviewer,
                state.get("reviewedAt") or _timestamp(),
                replacement_content=replacement_content,
            )
        finally:
            save_documents(documents, data_path)


def _find_document(documents: list[dict], document_name: str) -> dict:
    for document in documents:
        if document.get("documentName") == document_name:
            return document
    raise KeyError(document_name)


def _document_text(source_dir: Path, document_name: str) -> str:
    path = source_dir / document_name
    return extract_text(path) if path.is_file() else ""


def _text(value: object) -> str:
    return str(value).strip() if value is not None and str(value).strip() else "Unknown"


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()
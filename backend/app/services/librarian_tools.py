"""Read and approval-gated write capabilities for the local Knowledge Librarian chat.

Reads are executed automatically and returned as untrusted data. Writes are never
executed by the model: the app builds a change plan from the live SharePoint state,
a human approves it in the UI, and only then does the app call SharePoint with the
ETag captured at plan time.
"""

import json
import os
import re
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from uuid import uuid4

from backend.app.services.sharepoint import SharePointClient
from backend.app.services.writeback import WritebackConflict

WRITABLE_FIELDS: dict[str, str] = {
    "businessArea": "Business area",
    "audience": "Audience",
    "language": "Language",
    "author": "Author",
    "countryOfOrigin": "Country of origin",
}

MAX_FIELD_VALUE_LENGTH = 200
MAX_EXCERPT_CHARS = 700
MAX_EXCERPT_BUDGET = 2800
PROPOSAL_TTL_SECONDS = 30 * 60
MAX_PROPOSALS = 200


class LibrarianToolError(RuntimeError):
    """A capability could not be used, for example because it is not configured."""


class LibrarianToolConflict(LibrarianToolError):
    """The document changed after the plan was reviewed."""


@dataclass
class ChangeProposal:
    proposalId: str
    sessionId: str
    documentName: str
    folder: str
    driveId: str
    driveItemId: str
    expectedEtag: str
    webUrl: str
    changes: list[dict[str, str]]
    columnValues: dict[str, str]
    createdAt: float = field(default_factory=time.monotonic)

    def public(self) -> dict:
        return {
            "proposalId": self.proposalId,
            "documentName": self.documentName,
            "folder": self.folder,
            "webUrl": self.webUrl,
            "changes": self.changes,
        }


def staging_folder() -> str:
    return os.getenv("SHAREPOINT_STAGING_FOLDER_NAME", "Staging").strip() or "Staging"


def reviewed_folder() -> str:
    return os.getenv("SHAREPOINT_REVIEWED_FOLDER_NAME", "Reviewed").strip() or "Reviewed"


def writes_enabled() -> bool:
    return os.getenv("SHAREPOINT_WRITEBACK_ENABLED", "").strip().lower() in {"1", "true", "yes"}


def build_client() -> SharePointClient:
    hostname = os.getenv("SHAREPOINT_HOSTNAME", "").strip()
    if not hostname:
        raise LibrarianToolError("SHAREPOINT_HOSTNAME is not configured, so library access is unavailable")
    return SharePointClient(
        hostname=hostname,
        site_path=os.getenv("SHAREPOINT_SITE_PATH", "/"),
        library_name=os.getenv("SHAREPOINT_LIBRARY_NAME", "Documents"),
        folder_path=os.getenv("SHAREPOINT_FOLDER_PATH", ""),
    )


def _column_name(existing_columns: dict, field_name: str) -> str | None:
    configured = os.getenv("SHAREPOINT_COLUMN_MAP", "")
    if configured:
        try:
            mapping = json.loads(configured)
        except ValueError:
            mapping = {}
        if isinstance(mapping, dict):
            column = mapping.get(field_name)
            if isinstance(column, str) and column.strip():
                return column.strip()
    candidates = {WRITABLE_FIELDS[field_name].casefold(), field_name.casefold()}
    return next((key for key in existing_columns if key.casefold() in candidates), None)


def list_library_documents(client: SharePointClient, folder: str) -> list[dict]:
    return client.list_folder_documents(folder)


def library_overview(client: SharePointClient) -> dict:
    overview: dict = {"folders": {}}
    for folder in (staging_folder(), reviewed_folder()):
        try:
            overview["folders"][folder] = list_library_documents(client, folder)
        except Exception as error:  # noqa: BLE001 - surfaced to the model as a limitation
            overview["folders"][folder] = {"error": f"Unavailable: {type(error).__name__}"}
    return overview


def document_overview(client: SharePointClient, document_name: str, folder: str) -> dict:
    candidate = client.find_version_candidate(folder, document_name)
    if candidate is None:
        return {"found": False, "documentName": document_name, "folder": folder}
    return {
        "found": True,
        "documentName": candidate.get("fileName", document_name),
        "folder": folder,
        "webUrl": candidate.get("webUrl", ""),
        "lastModifiedDateTime": candidate.get("lastModifiedDateTime"),
        "currentVersion": candidate.get("currentVersion"),
        "versions": candidate.get("versions", []),
        "metadata": _readable_metadata(candidate.get("existingColumns", {})),
    }


_INTERNAL_COLUMN_PREFIXES = ("_", "@", "odata")
_INTERNAL_COLUMNS = {
    "ContentType",
    "FileLeafRef",
    "FileDirRef",
    "ID",
    "GUID",
    "Attachments",
    "Edit",
    "LinkFilename",
    "AppAuthor",
    "AppEditor",
}


def _readable_metadata(columns: dict) -> dict:
    return {
        key: value
        for key, value in columns.items()
        if isinstance(value, (str, int, float, bool))
        and not key.startswith(_INTERNAL_COLUMN_PREFIXES)
        and key not in _INTERNAL_COLUMNS
    }


def find_referenced_documents(message: str, overview: dict) -> list[tuple[str, str]]:
    """Return (folder, documentName) pairs whose names appear in the message."""
    lowered = message.casefold()
    matches: list[tuple[str, str]] = []
    for folder, items in overview.get("folders", {}).items():
        if not isinstance(items, list):
            continue
        for item in items:
            name = item.get("documentName", "")
            if not name:
                continue
            stem = Path(name).stem
            if name.casefold() in lowered or (len(stem) >= 6 and stem.casefold() in lowered):
                matches.append((folder, name))
    return matches[:3]


def build_metadata_proposal(
    client: SharePointClient,
    session_id: str,
    document_name: str,
    folder: str,
    requested_changes: dict[str, str],
) -> ChangeProposal:
    if not writes_enabled():
        raise LibrarianToolError(
            "SharePoint write-back is disabled (SHAREPOINT_WRITEBACK_ENABLED), so I can only propose changes verbally"
        )
    cleaned: dict[str, str] = {}
    for name, value in requested_changes.items():
        if name not in WRITABLE_FIELDS:
            raise LibrarianToolError(f"'{name}' is not an editable metadata field")
        if not isinstance(value, str) or not value.strip():
            raise LibrarianToolError(f"A non-empty value is required for {WRITABLE_FIELDS[name]}")
        if len(value.strip()) > MAX_FIELD_VALUE_LENGTH:
            raise LibrarianToolError(f"Value for {WRITABLE_FIELDS[name]} is too long")
        cleaned[name] = value.strip()
    if not cleaned:
        raise LibrarianToolError("No editable metadata changes were identified")

    candidate = client.find_version_candidate(folder, document_name)
    if candidate is None:
        raise LibrarianToolError(f"'{document_name}' was not found in {folder}")
    etag = candidate.get("listItemEtag") or candidate.get("etag")
    if not etag or not candidate.get("driveItemId") or not candidate.get("driveId"):
        raise LibrarianToolError("SharePoint did not return the identity and ETag needed for a safe change plan")

    existing = candidate.get("existingColumns", {})
    changes: list[dict[str, str]] = []
    column_values: dict[str, str] = {}
    for name, value in cleaned.items():
        column = _column_name(existing, name)
        if not column:
            raise LibrarianToolError(
                f"No SharePoint column is configured for {WRITABLE_FIELDS[name]}; configure SHAREPOINT_COLUMN_MAP"
            )
        current = existing.get(column)
        current_text = "" if current is None else str(current)
        if current_text == value:
            continue
        changes.append(
            {
                "field": name,
                "label": WRITABLE_FIELDS[name],
                "column": column,
                "currentValue": current_text,
                "proposedValue": value,
            }
        )
        column_values[column] = value
    if not changes:
        raise LibrarianToolError("The approved metadata already matches the requested values; no change is needed")

    return ChangeProposal(
        proposalId=str(uuid4()),
        sessionId=session_id,
        documentName=candidate.get("fileName", document_name),
        folder=folder,
        driveId=candidate["driveId"],
        driveItemId=candidate["driveItemId"],
        expectedEtag=etag,
        webUrl=candidate.get("webUrl", ""),
        changes=changes,
        columnValues=column_values,
    )


def execute_metadata_proposal(
    client: SharePointClient,
    proposal: ChangeProposal,
    reviewer: str,
) -> dict:
    """Re-check live state, then apply the approved change."""
    if not writes_enabled():
        raise LibrarianToolError("SharePoint write-back is disabled; the approved change was not executed")
    if not reviewer.strip():
        raise LibrarianToolError("An approving reviewer name is required")

    current = client.refresh_item(proposal.driveId, proposal.driveItemId)
    live_etag = current.get("listItemEtag") or current.get("etag")
    if live_etag != proposal.expectedEtag:
        raise LibrarianToolConflict(
            f"'{proposal.documentName}' changed after the plan was reviewed. Ask for a refreshed plan before approving."
        )

    try:
        result = client.update_fields(
            proposal.driveId,
            proposal.driveItemId,
            proposal.columnValues,
            proposal.expectedEtag,
        )
    except WritebackConflict as error:
        raise LibrarianToolConflict(str(error)) from error

    applied = result.get("existingColumns", {})
    unverified = [
        change["label"]
        for change in proposal.changes
        if str(applied.get(change["column"], "")) != change["proposedValue"]
    ]
    versions = client.get_version_history(proposal.driveId, proposal.driveItemId)
    latest = max(versions, key=lambda item: str(item.get("lastModifiedDateTime") or ""), default=None)
    return {
        "status": "partial" if unverified else "applied",
        "documentName": proposal.documentName,
        "folder": proposal.folder,
        "webUrl": proposal.webUrl,
        "reviewer": reviewer.strip(),
        "changes": proposal.changes,
        "unverifiedFields": unverified,
        "sharePointVersion": latest.get("id") if latest else None,
    }


class ProposalStore:
    def __init__(self) -> None:
        self._items: OrderedDict[str, ChangeProposal] = OrderedDict()
        self._lock = Lock()

    def add(self, proposal: ChangeProposal) -> None:
        with self._lock:
            self._expire()
            if len(self._items) >= MAX_PROPOSALS:
                self._items.popitem(last=False)
            self._items[proposal.proposalId] = proposal

    def pop(self, proposal_id: str, session_id: str) -> ChangeProposal | None:
        with self._lock:
            self._expire()
            proposal = self._items.get(proposal_id)
            if proposal is None or proposal.sessionId != session_id:
                return None
            del self._items[proposal_id]
            return proposal

    def _expire(self) -> None:
        now = time.monotonic()
        for key in [
            key
            for key, proposal in self._items.items()
            if now - proposal.createdAt > PROPOSAL_TTL_SECONDS
        ]:
            del self._items[key]


CHANGE_INTENT = re.compile(
    r"(?i)\b(set|change|chang(?:e|ing)|update|correct|fix|assign|re-?tag|re-?classif\w*|mark|rename\s+the\s+field)\b"
)

TRIAGE_INTENT = re.compile(
    r"(?i)\b(triage|intake|staging|staged|backlog|worklist|work\s+list|queue|"
    r"due|overdue|chase|chasing|pending\s+review|awaiting\s+review|ready\s+to\s+publish|"
    r"confidence|low\s+confidence|what\s+should\s+i\s+review)\b"
)


def looks_like_change_request(message: str) -> bool:
    return bool(CHANGE_INTENT.search(message))


def looks_like_triage_request(message: str) -> bool:
    return bool(TRIAGE_INTENT.search(message))


KNOWLEDGE_INTENT = re.compile(
    r"\b(what|which|who|when|where|why|how|explain|describe|summar\w*|tell me|"
    r"according to|guidance|policy|policies|procedure|process|rule|require\w*|eligib\w*|"
    r"definition|define|does the|do we|is there|are there)\b",
    re.IGNORECASE,
)


def looks_like_knowledge_question(message: str) -> bool:
    """A question to answer from approved content, rather than a maintenance instruction."""
    if looks_like_triage_request(message) or looks_like_change_request(message):
        return False
    return bool(KNOWLEDGE_INTENT.search(message)) or message.strip().endswith("?")


def search_approved_knowledge(question: str, top: int = 5) -> dict:
    """Retrieve citations from the approved-content index only.

    Returns a structured result rather than raising so a chat turn can still
    proceed, and degrade honestly, when search is not configured.
    """
    from backend.app.services.search import (
        ApprovedKnowledgeSearch,
        SearchConfigurationError,
        SearchOperationError,
    )

    try:
        results = ApprovedKnowledgeSearch().query(question, top)
    except SearchConfigurationError as error:
        return {"available": False, "reason": f"Approved-knowledge search is not configured: {error}"}
    except (SearchOperationError, OSError, ValueError) as error:
        return {"available": False, "reason": f"Approved-knowledge search failed: {error}"}

    citations = []
    budget = MAX_EXCERPT_BUDGET
    for result in results:
        excerpt = result.get("content", "")[:MAX_EXCERPT_CHARS]
        if len(excerpt) > budget:
            excerpt = excerpt[:budget]
        budget -= len(excerpt)
        citations.append(
            {
                "documentName": result["citation"]["documentName"],
                "sourceUrl": result["citation"]["sourceUrl"],
                "section": result["citation"]["chunkNumber"],
                "sourceVersion": result["citation"]["sourceVersion"],
                "score": result.get("rerankerScore") or result.get("score"),
                "excerpt": excerpt,
            }
        )
        if budget <= 0:
            break
    return {
        "available": True,
        "index": os.getenv("SEARCH_INDEX_NAME", "wtw-approved-knowledge"),
        "scope": "human-approved, published documents only",
        "matchCount": len(citations),
        "citations": citations,
    }


def run_intake_triage() -> dict:
    """Run the librarian's read-only Staging triage over the stored documents."""
    from backend.app.services.librarian_triage import TriageSettings, triage_staging
    from backend.app.services.storage import DATA_PATH, load_documents
    from backend.app.services.taxonomy import Taxonomy

    documents = load_documents(DATA_PATH)
    taxonomy_path = Path(os.getenv("FOUNDRY_TAXONOMY_PATH", "taxonomy/controlled-terms.json"))
    try:
        taxonomy = Taxonomy.from_file(taxonomy_path)
    except (OSError, ValueError):
        taxonomy = None

    staged_files = None
    try:
        staged_files = list_library_documents(build_client(), staging_folder())
    except (LibrarianToolError, OSError, ValueError):
        staged_files = None

    return triage_staging(
        documents,
        taxonomy,
        TriageSettings.from_environment(),
        staged_files=staged_files,
    )

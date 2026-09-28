import logging
import os
import time
from collections import OrderedDict
from pathlib import Path
from threading import Lock
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from azure.core.exceptions import AzureError
from openai import APITimeoutError, BadRequestError, OpenAIError

from backend.app.services import librarian_tools
from backend.app.services.chat_guardrails import (
    MAX_QUESTION_LENGTH,
    screen_answer,
    screen_question,
    wrap_untrusted,
)
from backend.app.services.knowledge_librarian import (
    KnowledgeLibrarianConfigurationError,
    KnowledgeLibrarianProvider,
)
from backend.app.services.librarian_tools import (
    LibrarianToolConflict,
    LibrarianToolError,
    ProposalStore,
)

ROOT = Path(__file__).resolve().parents[2]
logger = logging.getLogger(__name__)

SESSION_TTL_SECONDS = 30 * 60
MAX_SESSIONS = 100
MAX_HISTORY_MESSAGES = 20
MAX_MESSAGE_LENGTH = MAX_QUESTION_LENGTH
RATE_LIMIT_MESSAGES = 20
RATE_LIMIT_WINDOW_SECONDS = 5 * 60

app = FastAPI(title="Local Knowledge Librarian Chat")


class ChatMessage(BaseModel):
    sessionId: str = Field(min_length=1, max_length=36)
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_LENGTH)


class ProposalDecision(BaseModel):
    sessionId: str = Field(min_length=1, max_length=36)
    reviewer: str = Field(min_length=1, max_length=120)


class ChatSessions:
    def __init__(self) -> None:
        self._sessions: OrderedDict[str, tuple[float, list[dict[str, str]]]] = OrderedDict()
        self._requests: dict[str, list[float]] = {}
        self._lock = Lock()

    def check_rate_limit(self, session_id: str) -> None:
        now = time.monotonic()
        with self._lock:
            recent = [
                stamp
                for stamp in self._requests.get(session_id, [])
                if now - stamp < RATE_LIMIT_WINDOW_SECONDS
            ]
            if len(recent) >= RATE_LIMIT_MESSAGES:
                raise HTTPException(
                    status_code=429,
                    detail="Too many messages in a short period. Wait a moment before asking again.",
                )
            recent.append(now)
            self._requests[session_id] = recent

    def create(self) -> str:
        now = time.monotonic()
        session_id = str(uuid4())
        with self._lock:
            self._expire(now)
            if len(self._sessions) >= MAX_SESSIONS:
                raise HTTPException(
                    status_code=503,
                    detail="Local chat is at session capacity. Try again after an inactive session expires.",
                )
            self._sessions[session_id] = (now, [])
        return session_id

    def get_history(self, session_id: str) -> list[dict[str, str]]:
        now = time.monotonic()
        with self._lock:
            self._expire(now)
            session = self._sessions.get(session_id)
            if session is None:
                raise HTTPException(status_code=404, detail="Chat session expired or not found. Start a new chat.")
            self._sessions.move_to_end(session_id)
            self._sessions[session_id] = (now, session[1])
            return [message.copy() for message in session[1]]

    def append_turn(
        self,
        session_id: str,
        user_message: str,
        assistant_message: str,
    ) -> None:
        now = time.monotonic()
        with self._lock:
            self._expire(now)
            session = self._sessions.get(session_id)
            if session is None:
                raise HTTPException(status_code=404, detail="Chat session expired or not found. Start a new chat.")
            history = [
                *session[1],
                {"role": "user", "content": user_message},
                {"role": "assistant", "content": assistant_message},
            ][-MAX_HISTORY_MESSAGES:]
            self._sessions.move_to_end(session_id)
            self._sessions[session_id] = (now, history)

    def _expire(self, now: float) -> None:
        expired = [
            session_id
            for session_id, (last_active, _) in self._sessions.items()
            if now - last_active > SESSION_TTL_SECONDS
        ]
        for session_id in expired:
            del self._sessions[session_id]
            self._requests.pop(session_id, None)


sessions = ChatSessions()
proposals = ProposalStore()


@app.get("/", response_class=HTMLResponse)
def chat_page() -> str:
    return (ROOT / "frontend" / "librarian-chat.html").read_text(encoding="utf-8")


@app.get("/api/health")
def health() -> dict[str, object]:
    return {
        "status": "ok",
        "agent": "knowledge-librarian-agent",
        "readEnabled": bool(os.getenv("SHAREPOINT_HOSTNAME", "").strip()),
        "writeEnabled": librarian_tools.writes_enabled(),
    }


@app.post("/api/chat/sessions")
def create_chat_session() -> dict[str, str]:
    return {"sessionId": sessions.create()}


def _session_id(raw: str) -> str:
    try:
        return str(UUID(raw))
    except ValueError as error:
        raise HTTPException(status_code=400, detail="Invalid chat session ID") from error


def _gather_context(session_id: str, message: str) -> tuple[list[str], dict | None, dict | None]:
    """Read library data and, when clearly requested, build a change plan."""
    context: list[str] = []
    knowledge = None
    if librarian_tools.looks_like_knowledge_question(message):
        knowledge = librarian_tools.search_approved_knowledge(message)
        context.append(wrap_untrusted("approved_knowledge", knowledge))

    try:
        client = librarian_tools.build_client()
    except LibrarianToolError as error:
        context.append(
            wrap_untrusted("library_capability", {"available": False, "reason": str(error)})
        )
        return context, None, knowledge

    overview = librarian_tools.library_overview(client)
    context.append(wrap_untrusted("library_contents", overview))

    if librarian_tools.looks_like_triage_request(message):
        try:
            context.append(
                wrap_untrusted("intake_triage", _triage_digest(librarian_tools.run_intake_triage()))
            )
        except (LibrarianToolError, OSError, ValueError) as error:
            context.append(wrap_untrusted("intake_triage", {"available": False, "reason": str(error)}))

    referenced = librarian_tools.find_referenced_documents(message, overview)
    details = []
    for folder, name in referenced:
        try:
            details.append(librarian_tools.document_overview(client, name, folder))
        except (LibrarianToolError, ValueError) as error:
            details.append({"documentName": name, "folder": folder, "error": str(error)})
    if details:
        context.append(wrap_untrusted("document_details", details))

    proposal_payload = None
    if referenced and librarian_tools.looks_like_change_request(message):
        proposal_payload = _build_proposal(client, session_id, message, overview, context)
    return context, proposal_payload, knowledge


def _build_proposal(
    client,
    session_id: str,
    message: str,
    overview: dict,
    context: list[str],
) -> dict | None:
    names = [
        item["documentName"]
        for items in overview.get("folders", {}).values()
        if isinstance(items, list)
        for item in items
        if item.get("documentName")
    ]
    try:
        request = KnowledgeLibrarianProvider().extract_change_request(message, names)
    except KnowledgeLibrarianConfigurationError:
        return None
    if not request:
        return None

    folder = next(
        (
            folder_name
            for folder_name, items in overview.get("folders", {}).items()
            if isinstance(items, list)
            and any(item.get("documentName") == request["documentName"] for item in items)
        ),
        librarian_tools.reviewed_folder(),
    )
    try:
        proposal = librarian_tools.build_metadata_proposal(
            client,
            session_id,
            request["documentName"],
            folder,
            request["changes"],
        )
    except LibrarianToolError as error:
        context.append(
            wrap_untrusted("change_plan_blocked", {"requested": request, "reason": str(error)})
        )
        return None
    proposals.add(proposal)
    context.append(wrap_untrusted("pending_change_plan", proposal.public()))
    return proposal.public()


def _is_content_filter(error: BadRequestError) -> bool:
    body = getattr(error, "body", None)
    if isinstance(body, dict):
        if body.get("code") == "content_filter":
            return True
        inner = body.get("innererror")
        if isinstance(inner, dict) and inner.get("code") == "ContentFiltered":
            return True
    return "content_filter" in str(error)


BASE_ANSWER_POLICY = (
    "Answer only from the data above and the conversation. "
    "Do not claim any SharePoint change has been made; a reviewer must approve the plan first."
)


def _answer_policy(knowledge: dict | None) -> str:
    """Knowledge questions must be answered from approved content, or not at all."""
    if knowledge is None:
        return BASE_ANSWER_POLICY
    if not knowledge.get("available"):
        return (
            BASE_ANSWER_POLICY
            + " The approved-knowledge index is unavailable, so you cannot answer this question "
            "from approved sources. Say that approved-content search is unavailable, state the "
            "reason given in approved_knowledge, and do not answer from library listings, "
            "file names or your own knowledge."
        )
    if not knowledge.get("citations"):
        return (
            BASE_ANSWER_POLICY
            + " No approved document matched this question. Say that you found no approved "
            "evidence, and do not answer from file names, folder listings or your own knowledge. "
            "Suggest the document may not be published yet."
        )
    return (
        BASE_ANSWER_POLICY
        + " Answer this question only from the approved_knowledge excerpts. Cite each claim with "
        "the document name and its section number. If the excerpts do not contain the answer, say "
        "so rather than inferring it. Do not use library listings or your own knowledge as evidence."
    )


@app.post("/api/chat/messages")
def send_chat_message(request: ChatMessage) -> dict[str, object]:
    session_id = _session_id(request.sessionId)
    history = sessions.get_history(session_id)
    sessions.check_rate_limit(session_id)

    verdict = screen_question(request.message)
    if not verdict.allowed:
        if verdict.category in {"empty", "too-long"}:
            raise HTTPException(status_code=400, detail=verdict.message)
        return {"reply": verdict.message, "proposal": None, "guardrail": verdict.category}

    message = request.message.strip()
    context, proposal, knowledge = _gather_context(session_id, message)
    grounded = "\n\n".join(
        [
            message,
            *context,
            _answer_policy(knowledge),
        ]
    )
    prompt_messages = [*history, {"role": "user", "content": grounded}]
    try:
        response = KnowledgeLibrarianProvider().answer(prompt_messages)
    except KnowledgeLibrarianConfigurationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except APITimeoutError as error:
        logger.warning("Knowledge Librarian request timed out")
        raise HTTPException(
            status_code=504,
            detail="The librarian took too long to respond. Ask a narrower question, or retry.",
        ) from error
    except BadRequestError as error:
        if _is_content_filter(error):
            logger.warning("Knowledge Librarian prompt was blocked by the content filter")
            return {
                "reply": (
                    "The Azure OpenAI content filter blocked this request before it reached the "
                    "librarian, so I have no answer to give. This usually happens when a large "
                    "amount of source text is quoted into one prompt. Ask a narrower question, or "
                    "name the document you want me to look at."
                ),
                "proposal": None,
                "guardrail": "content-filter",
                "citations": [],
            }
        logger.exception("Knowledge Librarian rejected the request")
        raise HTTPException(
            status_code=502,
            detail="Knowledge Librarian rejected the request. Check the agent configuration, then retry.",
        ) from error
    except (AzureError, OpenAIError, RuntimeError, ValueError) as error:
        logger.exception("Knowledge Librarian request failed")
        raise HTTPException(
            status_code=502,
            detail="Knowledge Librarian could not respond. Check Foundry connectivity and credentials, then retry.",
        ) from error

    reply = screen_answer(response, executed=False)
    sessions.append_turn(session_id, message, reply)
    return {
        "reply": reply,
        "proposal": proposal,
        "guardrail": None,
        "citations": (knowledge or {}).get("citations", []),
    }


@app.post("/api/chat/proposals/{proposal_id}/approve")
def approve_proposal(proposal_id: str, decision: ProposalDecision) -> dict[str, object]:
    session_id = _session_id(decision.sessionId)
    proposal = proposals.pop(proposal_id, session_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail="Change plan expired or not found. Ask for a refreshed plan.")
    try:
        client = librarian_tools.build_client()
        result = librarian_tools.execute_metadata_proposal(client, proposal, decision.reviewer)
    except LibrarianToolConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except LibrarianToolError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except (AzureError, RuntimeError) as error:
        logger.exception("Approved SharePoint change failed")
        raise HTTPException(status_code=502, detail="SharePoint did not accept the approved change.") from error

    summary = _execution_summary(result)
    sessions.append_turn(session_id, f"Approved change plan {proposal_id}", summary)
    return {"result": result, "summary": summary}


@app.post("/api/chat/proposals/{proposal_id}/reject")
def reject_proposal(proposal_id: str, decision: ProposalDecision) -> dict[str, str]:
    session_id = _session_id(decision.sessionId)
    proposal = proposals.pop(proposal_id, session_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail="Change plan expired or not found.")
    summary = f"Change plan for {proposal.documentName} was rejected by {decision.reviewer.strip()}. Nothing was changed."
    sessions.append_turn(session_id, f"Rejected change plan {proposal_id}", summary)
    return {"summary": summary}


MAX_TRIAGE_ITEMS_IN_PROMPT = 10


def _triage_digest(report: dict) -> dict:
    """Trim the triage report so grounding stays small enough to answer promptly."""
    items = report.get("items", [])
    return {
        "generatedAt": report.get("generatedAt"),
        "confidenceThreshold": report.get("confidenceThreshold"),
        "reviewSlaDays": report.get("reviewSlaDays"),
        "stagingReconciled": report.get("stagingReconciled"),
        "counts": report.get("counts", {}),
        "truncated": len(items) > MAX_TRIAGE_ITEMS_IN_PROMPT,
        "items": [
            {
                "documentName": item.get("documentName"),
                "source": item.get("source"),
                "disposition": item.get("disposition"),
                "reviewState": item.get("review", {}).get("state"),
                "waitingDays": item.get("review", {}).get("waitingDays"),
                "unresolvedFields": item.get("review", {}).get("unresolvedFields", []),
                "lowestConfidence": item.get("confidence", {}).get("lowest"),
                "unknownTags": [tag.get("value") for tag in item.get("tags", {}).get("unknown", [])],
                "riskFlags": item.get("riskFlags", []),
                "blockers": item.get("blockers", []),
                "recommendedAction": item.get("recommendedAction"),
            }
            for item in items[:MAX_TRIAGE_ITEMS_IN_PROMPT]
        ],
    }


@app.get("/api/librarian/triage")
def intake_triage() -> dict:
    """Read-only intake job: assess Staging and chase reviews that are due."""
    try:
        return librarian_tools.run_intake_triage()
    except LibrarianToolError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except (OSError, ValueError) as error:
        logger.exception("Intake triage failed")
        raise HTTPException(status_code=500, detail="Intake triage could not be completed.") from error


def _execution_summary(result: dict) -> str:
    applied = ", ".join(
        f"{change['label']}: '{change['currentValue']}' -> '{change['proposedValue']}'"
        for change in result["changes"]
    )
    lines = [
        f"Completed | {result['documentName']} in {result['folder']} updated by {result['reviewer']}.",
        f"Applied | {applied}",
        f"SharePoint version | {result['sharePointVersion'] or 'not reported by SharePoint'}",
    ]
    if result["unverifiedFields"]:
        lines.append(
            "Items requiring review | SharePoint did not confirm: " + ", ".join(result["unverifiedFields"])
        )
    return "\n".join(lines)

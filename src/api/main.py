"""FastAPI app exposing the chatbot.

Provides a chat endpoint plus a health probe and serves the static frontend
stub. The agent call is lazy so the app module imports without Azure; the
heavy client is constructed on first request.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

logger = logging.getLogger("chatbot.api")

app = FastAPI(title="Your Enterprise Workplace Assistant", version="0.1.0")

_agent = None


def get_agent():
    global _agent
    if _agent is None:
        from ..agent.agent import WorkplaceAgent

        _agent = WorkplaceAgent()
    return _agent


class Citation(BaseModel):
    title: str = ""
    source_url: str = ""
    snippet: str = ""


class ChatRequest(BaseModel):
    message: str
    thread_id: Optional[str] = None


class ChatResponse(BaseModel):
    answer: str
    citations: List[Citation] = []
    degraded: bool = False
    thread_id: Optional[str] = None


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    if not req.message or not req.message.strip():
        raise HTTPException(status_code=400, detail="message is required")
    try:
        answer = get_agent().ask(req.message)
    except Exception:
        # Never surface raw system/error messages (architecture §6), but do log
        # the full traceback so failures are traceable for troubleshooting (§4).
        logger.exception("chat request failed; returning graceful degradation")
        from ..agent.degradation import FailureMode, resolve

        d = resolve(FailureMode.SOURCE_UNAVAILABLE)
        return ChatResponse(answer=d.message, degraded=True, thread_id=req.thread_id)
    return ChatResponse(answer=answer, thread_id=req.thread_id)


if FRONTEND_DIR.exists():
    app.mount("/app", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(str(FRONTEND_DIR / "index.html"))

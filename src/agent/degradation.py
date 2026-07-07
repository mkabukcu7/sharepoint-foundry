"""Graceful degradation logic (pure, no Azure dependencies).

Single source of truth for the fallback behaviors and user-facing copy from
architecture §6. The agent calls :func:`resolve` with a `FailureMode` (and
optionally the result of confidence gating) to obtain the exact response copy
plus a structured trace tag — never raw system messages.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class FailureMode(str, Enum):
    TOOL_FAILURE = "tool_failure"
    SOURCE_UNAVAILABLE = "source_unavailable"
    MODEL_TIMEOUT = "model_timeout"
    LOW_CONFIDENCE = "low_confidence"
    UNSUPPORTED_MODALITY = "unsupported_modality"
    IDENTITY_PASSTHROUGH_FAILED = "identity_passthrough_failed"


class Action(str, Enum):
    RETRY_THEN_SEARCH_ONLY = "retry_then_search_only"
    USE_CACHED_OR_ABSTAIN = "use_cached_or_abstain"
    REDUCE_CONTEXT_THEN_MINI = "reduce_context_then_mini"
    ABSTAIN_OR_CLARIFY = "abstain_or_clarify"
    ROUTE_TEXT_ONLY = "route_text_only"
    REFUSE_OR_NARROW = "refuse_or_narrow"


# Exact user-facing copy from docs/02-architecture.md §6.
_COPY = {
    FailureMode.TOOL_FAILURE: (
        "I couldn't reach the live analytics source right now. I can answer "
        "from indexed documentation, but may miss the latest dashboard values."
    ),
    FailureMode.SOURCE_UNAVAILABLE: (
        "The SharePoint source appears temporarily unavailable. I can try "
        "again, or use previously indexed content if that's acceptable."
    ),
    FailureMode.MODEL_TIMEOUT: "",  # concise partial answer produced upstream
    FailureMode.LOW_CONFIDENCE: (
        "I found related content, but not enough evidence to answer "
        "confidently. Want me to narrow this to a specific policy, team, or "
        "date range?"
    ),
    FailureMode.UNSUPPORTED_MODALITY: (
        "This recording hasn't been fully processed yet. I can search "
        "available transcript segments, but coverage may be incomplete."
    ),
    FailureMode.IDENTITY_PASSTHROUGH_FAILED: (
        "I can't confirm your access to that content right now, so I won't "
        "retrieve it."
    ),
}

_ACTION = {
    FailureMode.TOOL_FAILURE: Action.RETRY_THEN_SEARCH_ONLY,
    FailureMode.SOURCE_UNAVAILABLE: Action.USE_CACHED_OR_ABSTAIN,
    FailureMode.MODEL_TIMEOUT: Action.REDUCE_CONTEXT_THEN_MINI,
    FailureMode.LOW_CONFIDENCE: Action.ABSTAIN_OR_CLARIFY,
    FailureMode.UNSUPPORTED_MODALITY: Action.ROUTE_TEXT_ONLY,
    FailureMode.IDENTITY_PASSTHROUGH_FAILED: Action.REFUSE_OR_NARROW,
}


@dataclass
class Degradation:
    mode: FailureMode
    action: Action
    message: str
    # A security refusal must never silently widen scope (architecture §6/§7).
    is_refusal: bool
    # Whether the agent may still attempt a reduced/partial answer.
    allow_partial: bool
    trace_tag: str


def resolve(mode: FailureMode) -> Degradation:
    """Map a failure mode to its degradation behavior and user-facing copy."""
    action = _ACTION[mode]
    is_refusal = mode is FailureMode.IDENTITY_PASSTHROUGH_FAILED
    allow_partial = mode in {
        FailureMode.TOOL_FAILURE,
        FailureMode.MODEL_TIMEOUT,
        FailureMode.UNSUPPORTED_MODALITY,
        FailureMode.SOURCE_UNAVAILABLE,
    }
    return Degradation(
        mode=mode,
        action=action,
        message=_COPY[mode],
        is_refusal=is_refusal,
        allow_partial=allow_partial,
        trace_tag=f"degradation:{mode.value}",
    )

"""Query routing logic (pure, no Azure dependencies).

Implements architecture §4.5 (query pipeline) and §4.6 (structured-data vs
unstructured RAG). Three responsibilities:

1. Modality / intent classification of the inbound turn.
2. Hard routing rules: live-metric questions go to the Tableau tool, never to
   free-text RAG; on conflict, structured data wins.
3. Confidence gating: decide whether retrieved evidence is strong enough to
   synthesize an answer, or whether we must abstain / clarify (degradation).

All thresholds default to the architecture values but can be overridden so the
caller can wire in `Settings`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, List, Optional, Sequence


class Lane(str, Enum):
    """Resolved grounding lane for a turn."""

    TABLEAU = "tableau"          # live structured metric → tool call
    RAG = "rag"                  # curated index hybrid retrieval
    SHAREPOINT_LIVE = "sharepoint_live"  # secure OBO live SharePoint fetch
    SMALLTALK = "smalltalk"      # greeting / meta, no grounding needed


class Modality(str, Enum):
    TEXT = "text"
    TABLE = "table"
    AUDIO_VIDEO = "audio_video"
    IMAGE = "image"


# Phrases that indicate the user wants a *current numeric value* — these must be
# answered by the Tableau tool (value + grain + period + refresh + source),
# never computed from retrieved narrative text (architecture §4.6).
_METRIC_PATTERNS = [
    r"\bcurrent\b.*\b(value|number|count|total|rate|figure)\b",
    r"\b(how many|how much)\b",
    r"\btop\s+\d+\b",
    r"\b(this|last)\s+(week|month|quarter|year|day)\b",
    r"\b(year[-\s]?over[-\s]?year|month[-\s]?over[-\s]?month|yoy|mom)\b",
    r"\bcompare(d)?\s+to\b",
    r"\b(trend|growth|decline)\b",
    r"\b(kpi|metric|dashboard value|actuals?)\b.*\b(value|now|today|latest)\b",
    r"\blatest\b.*\b(number|value|figure|reading)\b",
]

# Phrases that are *about* a dashboard/metric but want explanation, ownership,
# lineage or definition → curated RAG, not a live tool call.
_RAG_ABOUT_PATTERNS = [
    r"\bwhat does\b.*\b(mean|measure|represent)\b",
    r"\b(definition|defined|glossary)\b",
    r"\bwho owns\b",
    r"\b(lineage|source of truth|how is .* calculated)\b",
    r"\bsummariz(e|ing)\b",
    r"\bexplain\b",
]

_SMALLTALK_PATTERNS = [
    r"^\s*(hi|hello|hey|thanks|thank you|good (morning|afternoon|evening))\b",
    r"^\s*(who are you|what can you do|help)\s*\??\s*$",
]

_AUDIO_VIDEO_HINTS = [
    r"\b(recording|video|webinar|town\s?hall|meeting)\b",
    r"\b(said|mentioned)\b.*\b(in the (call|video|recording))\b",
    r"\bat\s+\d{1,2}:\d{2}\b",  # timestamp reference
]


def _matches_any(text: str, patterns: Iterable[str]) -> bool:
    return any(re.search(p, text, flags=re.IGNORECASE) for p in patterns)


def detect_modality(text: str) -> Modality:
    """Best-effort modality hint for an inbound text turn."""
    if _matches_any(text, _AUDIO_VIDEO_HINTS):
        return Modality.AUDIO_VIDEO
    if _matches_any(text, [r"\btable\b", r"\bspreadsheet\b", r"\bcolumn\b", r"\brow\b"]):
        return Modality.TABLE
    return Modality.TEXT


@dataclass
class RouteDecision:
    lane: Lane
    modality: Modality
    reason: str
    # When True the live metric rule fired and overrode any RAG signal.
    structured_override: bool = False


def route(text: str) -> RouteDecision:
    """Decide which grounding lane should handle a turn.

    Hard rule (architecture §4.6): if the turn asks for a *current metric
    value*, route to Tableau even when it also mentions a dashboard by name.
    Structured wins on conflict.
    """
    stripped = (text or "").strip()
    if not stripped:
        return RouteDecision(Lane.SMALLTALK, Modality.TEXT, "empty input")

    if _matches_any(stripped, _SMALLTALK_PATTERNS) and len(stripped.split()) <= 8:
        return RouteDecision(Lane.SMALLTALK, Modality.TEXT, "greeting/meta")

    modality = detect_modality(stripped)

    wants_metric = _matches_any(stripped, _METRIC_PATTERNS)
    wants_about = _matches_any(stripped, _RAG_ABOUT_PATTERNS)

    if wants_metric:
        # Structured wins even if the turn also looks "about" a dashboard.
        return RouteDecision(
            Lane.TABLEAU,
            Modality.TABLE,
            "live metric value requested",
            structured_override=wants_about,
        )

    if wants_about:
        return RouteDecision(Lane.RAG, modality, "definition/explanation/lineage")

    return RouteDecision(Lane.RAG, modality, "default curated retrieval")


# --------------------------------------------------------------------------- #
# Confidence gating
# --------------------------------------------------------------------------- #
@dataclass
class Evidence:
    """A single retrieved/reranked chunk with a normalized [0,1] score."""

    chunk_id: str
    score: float
    content: str = ""
    source_url: str = ""
    modality: str = "text"


@dataclass
class ConfidenceResult:
    sufficient: bool
    top_score: float
    kept: List[Evidence] = field(default_factory=list)
    reason: str = ""


def assess_confidence(
    evidence: Sequence[Evidence],
    *,
    min_score: float = 0.30,
    min_results: int = 1,
    rerank_top_k: int = 4,
) -> ConfidenceResult:
    """Gate retrieved evidence before synthesis.

    Returns sufficient=False when there is no evidence or the best score is
    below ``min_score`` — the caller must then abstain or ask a clarifying
    question rather than hallucinate (architecture §6, low-confidence row).
    """
    if not evidence:
        return ConfidenceResult(False, 0.0, [], "no evidence retrieved")

    ranked = sorted(evidence, key=lambda e: e.score, reverse=True)
    top = ranked[0].score
    kept = [e for e in ranked if e.score >= min_score][:rerank_top_k]

    if len(kept) < min_results:
        return ConfidenceResult(False, top, kept, "top score below confidence floor")

    return ConfidenceResult(True, top, kept, "sufficient grounding")

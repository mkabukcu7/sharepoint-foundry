"""Claim-level citation enforcement (pure, no Azure dependencies).

Implements architecture §4.5 / §15 F10: an answer to a *grounded* turn must cite
sources that were actually retrieved. This module validates that the citations
present in a synthesized answer map back to the retrieved evidence, and flags any
source referenced in the answer that was **not** retrieved (a fabricated
citation). The agent uses the outcome to abstain / degrade rather than surface an
ungrounded answer.

Two citation shapes are recognized:

* **URL citations** — any http(s) URL in the answer text, matched against the
  ``source_url`` of retrieved evidence.
* **Bracket markers** — ``[1]`` / ``[2]`` style references, valid only when the
  index maps to a retrieved evidence item.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Sequence

from .routing import Evidence

_URL_RE = re.compile(r"https?://[^\s<>\])\"']+", re.IGNORECASE)
_MARKER_RE = re.compile(r"\[(\d{1,2})\]")


def _normalize_url(url: str) -> str:
    return url.strip().rstrip(".,);]").rstrip("/").lower()


def extract_url_citations(text: str) -> List[str]:
    """Return the normalized http(s) URLs referenced in an answer."""
    return [_normalize_url(u) for u in _URL_RE.findall(text or "")]


def extract_marker_citations(text: str) -> List[int]:
    """Return the 1-based ``[n]`` marker indices referenced in an answer."""
    return [int(m) for m in _MARKER_RE.findall(text or "")]


@dataclass
class CitationResult:
    grounded: bool
    coverage: float
    matched_sources: List[str] = field(default_factory=list)
    fabricated_sources: List[str] = field(default_factory=list)
    reason: str = ""


def validate_citations(
    answer: str,
    evidence: Sequence[Evidence],
    *,
    require_citation: bool = True,
) -> CitationResult:
    """Validate that an answer's citations map to retrieved evidence.

    Args:
        answer: The synthesized answer text.
        evidence: The evidence chunks that were actually retrieved for the turn.
        require_citation: When True (grounded turns), an answer with no valid
            citation is treated as *not grounded*. When False (e.g. smalltalk,
            refusals), citations are not required.

    Returns:
        A :class:`CitationResult`. ``grounded`` is False when a citation is
        required but none of the answer's citations map to retrieved evidence,
        or when the answer cites a source that was never retrieved.
    """
    evidence_urls = {_normalize_url(e.source_url) for e in evidence if e.source_url}
    n_evidence = len(list(evidence))

    url_citations = extract_url_citations(answer)
    marker_citations = extract_marker_citations(answer)

    matched: List[str] = []
    fabricated: List[str] = []
    for url in url_citations:
        if url in evidence_urls:
            matched.append(url)
        else:
            fabricated.append(url)

    valid_markers = [m for m in marker_citations if 1 <= m <= n_evidence]
    invalid_markers = [m for m in marker_citations if m < 1 or m > n_evidence]

    has_any_citation = bool(url_citations or marker_citations)
    has_valid_citation = bool(matched or valid_markers)

    total_refs = len(matched) + len(fabricated) + len(valid_markers) + len(invalid_markers)
    good_refs = len(matched) + len(valid_markers)
    coverage = (good_refs / total_refs) if total_refs else 0.0

    if not require_citation:
        return CitationResult(True, coverage, matched, fabricated, "citation not required")

    if fabricated or invalid_markers:
        return CitationResult(
            False, coverage, matched, fabricated,
            "answer cites sources that were not retrieved",
        )

    if not has_any_citation:
        return CitationResult(False, 0.0, matched, fabricated, "answer contains no citations")

    if not has_valid_citation:
        return CitationResult(False, coverage, matched, fabricated, "no citation maps to retrieved evidence")

    return CitationResult(True, coverage, matched, fabricated, "citations grounded in evidence")

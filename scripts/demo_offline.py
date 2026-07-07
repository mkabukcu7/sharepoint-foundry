"""Offline demo of the agent's decision logic — no Azure required.

Shows the pure, unit-tested "brains" of the Multimodal Workplace Chatbot:
intent routing, confidence gating, graceful-degradation copy, and the
claim-level citation guard. Run during a walkthrough to make the design
tangible without any cloud dependency:

    python scripts/demo_offline.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.agent.routing import route, assess_confidence, Evidence
from src.agent.degradation import resolve, FailureMode
from src.agent.citations import validate_citations


def _hdr(t: str) -> None:
    print("\n" + t)
    print("-" * len(t))


def main() -> None:
    _hdr("1) INTENT ROUTING  (structured wins over free-text RAG)")
    for q in [
        "What is our PTO policy?",
        "How many claims were filed last month?",
        "Summarize the Q2 town hall recording",
        "hi there",
    ]:
        d = route(q)
        print(f"  {q!r:48} -> {d.lane.value:15} ({d.reason})")

    _hdr("2) CONFIDENCE GATE  (weak evidence -> abstain, don't hallucinate)")
    weak = [Evidence("c1", 0.12, source_url="https://intranet/a")]
    r = assess_confidence(weak)
    print(f"  top score 0.12 -> sufficient={r.sufficient}  ({r.reason})")

    _hdr("3) GRACEFUL DEGRADATION  (user-friendly copy, never a raw error)")
    for mode in (FailureMode.LOW_CONFIDENCE, FailureMode.SOURCE_UNAVAILABLE,
                 FailureMode.IDENTITY_PASSTHROUGH_FAILED):
        print(f"  {mode.value:26}: {resolve(mode).message}")

    _hdr("4) CITATION GUARD  (a source not retrieved is blocked)")
    ev = [Evidence("c1", 0.9, source_url="https://contoso.sharepoint.com/it/pto")]
    good = "PTO accrues monthly. Source: https://contoso.sharepoint.com/it/pto"
    bad = "PTO accrues monthly. Source: https://totally-made-up.example/xyz"
    for label, ans in (("grounded answer", good), ("fabricated citation", bad)):
        res = validate_citations(ans, ev)
        print(f"  {label:20} -> grounded={res.grounded}  fabricated={res.fabricated_sources}")


if __name__ == "__main__":
    main()

"""Business-focused evaluation runner.

Wraps ``azure-ai-evaluation`` quality evaluators (Groundedness, Relevance,
Retrieval) against the golden business-scenario dataset and enforces the
slice-aware thresholds from architecture §9/§15. SDK imported lazily; the
dataset loader and threshold-gate logic are import-safe.

CLI:
    python -m src.evaluation.evaluate --dataset src/evaluation/datasets/golden.jsonl
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

DEFAULT_DATASET = Path(__file__).parent / "datasets" / "golden.jsonl"

# Thresholds (architecture §9 / §15).
THRESHOLDS = {
    "groundedness": 4.2,
    "relevance": 4.0,
    "retrieval": 4.0,
}

# Slice-aware thresholds keyed by business scenario (architecture §9 / §15).
# Structured/metric answers and permission handling are held to a higher bar;
# out-of-scope and source-unavailable slices are judged on correct abstention
# (relevance of the fallback) rather than groundedness of a synthesized answer.
SLICE_THRESHOLDS: Dict[str, Dict[str, float]] = {
    "policy_qa": {"groundedness": 4.3, "relevance": 4.0, "retrieval": 4.0},
    "recording_summary": {"groundedness": 4.0, "relevance": 4.0, "retrieval": 3.8},
    "tableau_metric": {"groundedness": 4.5, "relevance": 4.2, "retrieval": 4.0},
    "cross_source_synthesis": {"groundedness": 4.2, "relevance": 4.0, "retrieval": 4.0},
    "out_of_scope": {"relevance": 4.0},
    "permission_restricted": {"groundedness": 4.5, "relevance": 4.2},
    "source_unavailable": {"relevance": 4.0},
}


@dataclass
class SliceGateResult:
    scenario: str
    n: int
    gates: "List[GateResult]"
    passed: bool


@dataclass
class GateResult:
    metric: str
    mean_score: float
    threshold: float
    passed: bool


def load_dataset(path: Path) -> List[dict]:
    rows: List[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def gate(scores: Dict[str, float], thresholds: Optional[Dict[str, float]] = None) -> List[GateResult]:
    """Compare mean metric scores to thresholds. Pure logic for unit testing."""
    thresholds = thresholds or THRESHOLDS
    results: List[GateResult] = []
    for metric, threshold in thresholds.items():
        mean = scores.get(metric, 0.0)
        results.append(GateResult(metric, mean, threshold, mean >= threshold))
    return results


def gate_slices(
    per_row: List[dict],
    slice_thresholds: Optional[Dict[str, Dict[str, float]]] = None,
) -> List[SliceGateResult]:
    """Gate per-row scores grouped by ``scenario`` slice (pure logic).

    Each row must contain a ``scenario`` key plus metric scores. Only the
    metrics named in a slice's threshold map are gated for that slice, so
    abstention slices (out_of_scope, source_unavailable) aren't penalized on
    groundedness. Scenarios without a configured slice fall back to the global
    ``THRESHOLDS``.
    """
    slice_thresholds = slice_thresholds or SLICE_THRESHOLDS
    grouped: Dict[str, List[dict]] = {}
    for row in per_row:
        grouped.setdefault(row.get("scenario", "unknown"), []).append(row)

    results: List[SliceGateResult] = []
    for scenario, rows in grouped.items():
        thresholds = slice_thresholds.get(scenario, THRESHOLDS)
        means: Dict[str, float] = {}
        for metric in thresholds:
            vals = [float(r.get(metric, 0.0) or 0.0) for r in rows]
            means[metric] = sum(vals) / len(vals) if vals else 0.0
        gates = gate(means, thresholds)
        results.append(
            SliceGateResult(
                scenario=scenario,
                n=len(rows),
                gates=gates,
                passed=all(g.passed for g in gates),
            )
        )
    return results


def run_evaluation(
    answer_fn: Callable[[str], dict],
    dataset_path: Path = DEFAULT_DATASET,
    *,
    project_endpoint: Optional[str] = None,
) -> dict:
    """Run azure-ai-evaluation over the dataset.

    ``answer_fn(query) -> {"response": str, "context": str}`` produces the
    chatbot answer + retrieved context for each row. Returns aggregate scores
    and per-row results, plus the threshold gate outcome.
    """
    from azure.ai.evaluation import (
        GroundednessEvaluator,
        RelevanceEvaluator,
        RetrievalEvaluator,
    )
    from .agent_config import build_model_config

    cfg = build_model_config(project_endpoint)
    groundedness = GroundednessEvaluator(cfg)
    relevance = RelevanceEvaluator(cfg)
    retrieval = RetrievalEvaluator(cfg)

    rows = load_dataset(dataset_path)
    per_row: List[dict] = []
    sums = {"groundedness": 0.0, "relevance": 0.0, "retrieval": 0.0}
    for row in rows:
        produced = answer_fn(row["query"])
        response = produced.get("response", "")
        context = produced.get("context", "")
        g = groundedness(query=row["query"], response=response, context=context)
        r = relevance(query=row["query"], response=response, context=context)
        rt = retrieval(query=row["query"], context=context)
        scores = {
            "groundedness": g.get("groundedness", 0.0),
            "relevance": r.get("relevance", 0.0),
            "retrieval": rt.get("retrieval", 0.0),
        }
        for k, v in scores.items():
            sums[k] += float(v or 0.0)
        per_row.append({"id": row["id"], "scenario": row.get("scenario", "unknown"), **scores})

    n = max(len(rows), 1)
    means = {k: v / n for k, v in sums.items()}
    return {
        "means": means,
        "gate": gate(means),
        "slice_gate": gate_slices(per_row),
        "rows": per_row,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run business-scenario evaluation")
    parser.add_argument("--dataset", type=str, default=str(DEFAULT_DATASET))
    args = parser.parse_args()

    from ..agent.agent import WorkplaceAgent

    agent = WorkplaceAgent()

    def answer_fn(query: str) -> dict:
        return {"response": agent.ask(query), "context": ""}

    result = run_evaluation(answer_fn, Path(args.dataset))
    print(json.dumps(result["means"], indent=2))
    for g in result["gate"]:
        status = "PASS" if g.passed else "FAIL"
        print(f"[{status}] {g.metric}: {g.mean_score:.2f} (threshold {g.threshold})")

    print("\nPer-scenario slice gates:")
    for sg in result["slice_gate"]:
        status = "PASS" if sg.passed else "FAIL"
        detail = ", ".join(f"{g.metric} {g.mean_score:.2f}/{g.threshold}" for g in sg.gates)
        print(f"[{status}] {sg.scenario} (n={sg.n}): {detail}")


if __name__ == "__main__":
    main()

from pathlib import Path

from src.evaluation.evaluate import (
    SLICE_THRESHOLDS,
    gate,
    gate_slices,
    load_dataset,
    THRESHOLDS,
    DEFAULT_DATASET,
)


def test_gate_pass_and_fail():
    results = gate({"groundedness": 4.5, "relevance": 3.5, "retrieval": 4.1})
    by_metric = {r.metric: r for r in results}
    assert by_metric["groundedness"].passed is True
    assert by_metric["relevance"].passed is False
    assert by_metric["retrieval"].passed is True


def test_thresholds_match_architecture():
    assert THRESHOLDS["groundedness"] == 4.2
    assert THRESHOLDS["relevance"] == 4.0


def test_golden_dataset_has_seven_scenarios():
    rows = load_dataset(DEFAULT_DATASET)
    ids = [r["id"] for r in rows]
    assert ids == [f"BS-{i}" for i in range(1, 8)]
    for r in rows:
        assert {"id", "scenario", "query", "expected_lane"} <= set(r.keys())


def test_slice_thresholds_cover_all_dataset_scenarios():
    rows = load_dataset(DEFAULT_DATASET)
    scenarios = {r["scenario"] for r in rows}
    assert scenarios <= set(SLICE_THRESHOLDS.keys())


def test_gate_slices_groups_and_gates_per_scenario():
    per_row = [
        {"id": "1", "scenario": "tableau_metric", "groundedness": 4.6, "relevance": 4.3, "retrieval": 4.1},
        {"id": "2", "scenario": "tableau_metric", "groundedness": 4.4, "relevance": 4.2, "retrieval": 4.0},
        {"id": "3", "scenario": "out_of_scope", "relevance": 4.5},
    ]
    results = {r.scenario: r for r in gate_slices(per_row)}
    assert results["tableau_metric"].n == 2
    assert results["tableau_metric"].passed is True
    # out_of_scope only gates relevance (abstention slice)
    assert [g.metric for g in results["out_of_scope"].gates] == ["relevance"]
    assert results["out_of_scope"].passed is True


def test_gate_slices_fails_when_metric_below_slice_threshold():
    per_row = [
        {"id": "1", "scenario": "tableau_metric", "groundedness": 4.0, "relevance": 4.3, "retrieval": 4.1},
    ]
    result = gate_slices(per_row)[0]
    assert result.passed is False


def test_permission_restricted_held_to_higher_bar():
    assert SLICE_THRESHOLDS["permission_restricted"]["groundedness"] == 4.5


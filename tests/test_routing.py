from src.agent.routing import (
    Lane,
    Modality,
    Evidence,
    route,
    detect_modality,
    assess_confidence,
)


def test_live_metric_routes_to_tableau():
    d = route("What is the current value of the loss ratio this month?")
    assert d.lane is Lane.TABLEAU


def test_top_n_routes_to_tableau():
    d = route("Show me the top 5 regions by premium this quarter")
    assert d.lane is Lane.TABLEAU


def test_definition_routes_to_rag():
    d = route("What does the combined ratio dashboard mean?")
    assert d.lane is Lane.RAG


def test_structured_wins_on_conflict():
    # Mentions a dashboard ("what does ... mean" RAG signal) AND asks for a
    # current value (metric signal). Structured must win.
    d = route("What does the loss ratio dashboard mean and what is its current value this month?")
    assert d.lane is Lane.TABLEAU
    assert d.structured_override is True


def test_greeting_is_smalltalk():
    assert route("hi there").lane is Lane.SMALLTALK


def test_empty_is_smalltalk():
    assert route("   ").lane is Lane.SMALLTALK


def test_detect_audio_video_modality():
    assert detect_modality("What did the CEO say in the town hall recording?") is Modality.AUDIO_VIDEO


def test_confidence_insufficient_when_empty():
    r = assess_confidence([], min_score=0.3)
    assert r.sufficient is False
    assert r.top_score == 0.0


def test_confidence_below_floor():
    ev = [Evidence("c1", 0.2), Evidence("c2", 0.1)]
    r = assess_confidence(ev, min_score=0.3)
    assert r.sufficient is False
    assert r.reason == "top score below confidence floor"


def test_confidence_sufficient_and_reranked():
    ev = [Evidence(f"c{i}", 0.9 - i * 0.1) for i in range(8)]
    r = assess_confidence(ev, min_score=0.3, rerank_top_k=4)
    assert r.sufficient is True
    assert len(r.kept) == 4
    assert r.kept[0].score >= r.kept[-1].score


def test_confidence_insufficient_when_below_min_results():
    # One chunk clears the score floor, but min_results demands two: the gate
    # must abstain rather than answer from a single weak-corroboration chunk.
    ev = [Evidence("c1", 0.9)]
    r = assess_confidence(ev, min_score=0.3, min_results=2)
    assert r.sufficient is False
    assert r.top_score == 0.9
    assert len(r.kept) == 1


def test_metric_without_about_signal_has_no_override():
    d = route("How many claims were filed this week?")
    assert d.lane is Lane.TABLEAU
    assert d.structured_override is False

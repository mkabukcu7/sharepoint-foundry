from src.agent.config import get_settings, reset_settings_cache


def test_defaults_load():
    reset_settings_cache()
    s = get_settings()
    assert s.search_index_name == "workplace-knowledge"
    assert s.retrieval_top_k == 8
    assert s.rerank_top_k == 4
    assert abs(s.low_confidence_min_score - 0.30) < 1e-9
    assert abs(s.groundedness_min - 4.2) < 1e-9


def test_env_override(monkeypatch):
    monkeypatch.setenv("RETRIEVAL_TOP_K", "12")
    monkeypatch.setenv("SEARCH_INDEX_NAME", "custom-index")
    reset_settings_cache()
    s = get_settings()
    assert s.retrieval_top_k == 12
    assert s.search_index_name == "custom-index"
    reset_settings_cache()

from src.agent.citations import (
    extract_marker_citations,
    extract_url_citations,
    validate_citations,
)
from src.agent.routing import Evidence


def _ev(url, score=0.9):
    return Evidence(chunk_id=url, score=score, source_url=url)


def test_extract_url_citations_normalizes_and_strips_trailing_punct():
    text = "See https://Contoso.sharepoint.com/sites/it/Policy.aspx). Also (http://x.io/a)."
    urls = extract_url_citations(text)
    assert "https://contoso.sharepoint.com/sites/it/policy.aspx" in urls
    assert "http://x.io/a" in urls


def test_extract_marker_citations():
    assert extract_marker_citations("Answer [1] and also [2].") == [1, 2]
    assert extract_marker_citations("no markers") == []


def test_valid_url_citation_is_grounded():
    ev = [_ev("https://contoso.sharepoint.com/sites/it/policy.aspx")]
    answer = "PTO accrues monthly. Source: https://contoso.sharepoint.com/sites/it/policy.aspx"
    result = validate_citations(answer, ev)
    assert result.grounded is True
    assert result.matched_sources


def test_fabricated_url_citation_is_not_grounded():
    ev = [_ev("https://contoso.sharepoint.com/sites/it/policy.aspx")]
    answer = "PTO accrues monthly. Source: https://evil.example.com/made-up"
    result = validate_citations(answer, ev)
    assert result.grounded is False
    assert result.fabricated_sources == ["https://evil.example.com/made-up"]


def test_no_citation_when_required_is_not_grounded():
    ev = [_ev("https://contoso.sharepoint.com/a")]
    result = validate_citations("An answer with no source.", ev)
    assert result.grounded is False
    assert "no citations" in result.reason


def test_citation_not_required_passes():
    result = validate_citations("Hi there!", [], require_citation=False)
    assert result.grounded is True


def test_valid_marker_within_evidence_range():
    ev = [_ev("https://a"), _ev("https://b")]
    result = validate_citations("Per the handbook [2], remote work is allowed.", ev)
    assert result.grounded is True


def test_invalid_marker_out_of_range_is_not_grounded():
    ev = [_ev("https://a")]
    result = validate_citations("See [5] for details.", ev)
    assert result.grounded is False

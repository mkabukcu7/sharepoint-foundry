from src.agent.degradation import FailureMode, Action, resolve


def test_identity_failure_is_refusal_and_never_widens():
    d = resolve(FailureMode.IDENTITY_PASSTHROUGH_FAILED)
    assert d.is_refusal is True
    assert d.allow_partial is False
    assert d.action is Action.REFUSE_OR_NARROW
    assert "won't" in d.message.lower()


def test_tool_failure_allows_search_only_partial():
    d = resolve(FailureMode.TOOL_FAILURE)
    assert d.is_refusal is False
    assert d.allow_partial is True
    assert d.action is Action.RETRY_THEN_SEARCH_ONLY
    assert "indexed documentation" in d.message


def test_low_confidence_clarifies():
    d = resolve(FailureMode.LOW_CONFIDENCE)
    assert d.action is Action.ABSTAIN_OR_CLARIFY
    assert "not enough evidence" in d.message


def test_every_mode_has_trace_tag_and_action():
    for mode in FailureMode:
        d = resolve(mode)
        assert d.trace_tag == f"degradation:{mode.value}"
        assert d.action is not None


def test_source_unavailable_copy():
    d = resolve(FailureMode.SOURCE_UNAVAILABLE)
    assert "SharePoint source appears temporarily unavailable" in d.message

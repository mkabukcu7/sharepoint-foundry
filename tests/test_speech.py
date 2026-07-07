from src.ingestion.extractors.speech import SpeechExtractor


def test_parse_result_json_builds_segments():
    result = {
        "recognizedPhrases": [
            {
                "offsetInSeconds": 1.0,
                "durationInSeconds": 2.5,
                "speaker": 1,
                "nBest": [{"display": "Hello team."}],
            },
            {
                "offsetInSeconds": 4.0,
                "durationInSeconds": 1.0,
                "speaker": 2,
                "nBest": [{"lexical": "yes"}],
            },
        ]
    }
    segments = SpeechExtractor.parse_result_json(result)
    assert len(segments) == 2
    assert segments[0].start == 1.0
    assert segments[0].end == 3.5
    assert segments[0].text == "Hello team."
    assert segments[0].speaker == "1"
    assert segments[1].text == "yes"


def test_parse_result_json_empty():
    assert SpeechExtractor.parse_result_json({}) == []


def test_parse_phrases_prefers_display_over_lexical():
    phrases = [{"nBest": [{"display": "D", "lexical": "l"}], "offsetInSeconds": 0}]
    segments = SpeechExtractor._parse_phrases(phrases)
    assert segments[0].text == "D"

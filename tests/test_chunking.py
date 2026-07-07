from src.ingestion.chunking import (
    chunk_document,
    chunk_by_heading,
    chunk_transcript,
    chunk_table_records,
    estimate_tokens,
    TranscriptSegment,
)


def _para(n_sentences):
    return " ".join(f"This is sentence number {i} with some filler words here." for i in range(n_sentences))


def test_chunk_document_respects_target_size():
    text = _para(400)
    chunks = chunk_document(text, "doc1", target_tokens=200, overlap_ratio=0.15)
    assert len(chunks) > 1
    for c in chunks:
        # allow a small slack for the sentence that crosses the boundary
        assert c.token_estimate <= 300
        assert c.parent_doc_id == "doc1"
        assert c.modality == "text"


def test_chunk_ids_are_sequential_and_unique():
    chunks = chunk_document(_para(200), "doc2", target_tokens=150)
    ids = [c.chunk_id for c in chunks]
    assert len(ids) == len(set(ids))
    assert ids == [f"doc2::{i:04d}" for i in range(len(ids))]


def test_chunk_document_overlap_creates_continuity():
    chunks = chunk_document(_para(120), "doc3", target_tokens=120, overlap_ratio=0.2)
    if len(chunks) >= 2:
        first_words = set(chunks[0].content.split())
        second_words = set(chunks[1].content.split())
        assert first_words & second_words  # shared overlap tokens


def test_invalid_overlap_raises():
    try:
        chunk_document("a. b.", "d", overlap_ratio=0.6)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_chunk_by_heading_sets_section_metadata():
    text = "INTRODUCTION:\nThis is the intro body sentence one. And two.\n" \
           "SCOPE:\nThis policy applies to all employees. It covers everything."
    chunks = chunk_by_heading(text, "pol1", target_tokens=500)
    headings = {c.metadata.get("section_heading") for c in chunks}
    assert "INTRODUCTION" in headings
    assert "SCOPE" in headings


def test_chunk_transcript_respects_time_bounds():
    segs = [TranscriptSegment(start=i * 10.0, end=(i + 1) * 10.0, text=f"line {i}", speaker="A") for i in range(20)]
    chunks = chunk_transcript(segs, "vid1", min_seconds=30, max_seconds=90)
    assert len(chunks) >= 2
    for c in chunks:
        dur = c.metadata["timestamp_end"] - c.metadata["timestamp_start"]
        assert dur <= 90.0
        assert c.modality == "audio_video"


def test_chunk_transcript_breaks_on_speaker_change():
    segs = [
        TranscriptSegment(0, 20, "alpha", "A"),
        TranscriptSegment(20, 40, "beta", "A"),
        TranscriptSegment(40, 60, "gamma", "B"),
    ]
    chunks = chunk_transcript(segs, "vid2", min_seconds=30, max_seconds=90)
    speakers = {c.metadata["speaker"] for c in chunks}
    assert "A" in speakers and "B" in speakers


def test_chunk_table_records_one_per_row():
    records = [{"kpi": "loss_ratio", "value": 0.62}, {"kpi": "premium", "value": 100}]
    chunks = chunk_table_records(records, "tab1")
    assert len(chunks) == 2
    assert chunks[0].metadata["table_json"]["kpi"] == "loss_ratio"
    assert chunks[0].modality == "table"


def test_estimate_tokens_monotonic():
    assert estimate_tokens("one two three") < estimate_tokens("one two three four five six")

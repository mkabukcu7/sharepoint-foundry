"""Modality-aware chunking (pure, no Azure dependencies).

Implements architecture §4.3:

| Modality            | Chunking                                            |
|---------------------|-----------------------------------------------------|
| Narrative docs      | 600–1,000 tokens, 10–20% overlap                    |
| Policies/procedures | by heading/section first                            |
| Audio/video         | time-bounded semantic segments (30–90s)             |
| Tables / BI metadata| small semantic records (not giant blobs)            |

Token counts use a lightweight word-based approximation so the module has no
heavy dependency; the real pipeline can swap in a tokenizer. Each chunk carries
the navigation/freshness metadata used by the AI Search index (§4.4).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence


@dataclass
class Chunk:
    content: str
    modality: str
    parent_doc_id: str
    chunk_index: int
    token_estimate: int
    metadata: Dict[str, object] = field(default_factory=dict)

    @property
    def chunk_id(self) -> str:
        return f"{self.parent_doc_id}::{self.chunk_index:04d}"


def estimate_tokens(text: str) -> int:
    """Rough token estimate (~0.75 words/token → 1.33 tokens/word)."""
    words = len(text.split())
    return int(round(words * 1.33))


def _split_sentences(text: str) -> List[str]:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p for p in (s.strip() for s in parts) if p]


def chunk_document(
    text: str,
    parent_doc_id: str,
    *,
    target_tokens: int = 800,
    overlap_ratio: float = 0.15,
    metadata: Optional[Dict[str, object]] = None,
) -> List[Chunk]:
    """Chunk narrative text to ~600–1,000 tokens with 10–20% overlap.

    Splits on sentence boundaries so chunks stay coherent; carries a sliding
    overlap window between consecutive chunks.
    """
    if not 0 <= overlap_ratio < 0.5:
        raise ValueError("overlap_ratio must be in [0, 0.5)")
    base_meta = dict(metadata or {})
    sentences = _split_sentences(text)
    if not sentences:
        return []

    overlap_tokens = int(target_tokens * overlap_ratio)
    chunks: List[Chunk] = []
    cur: List[str] = []
    cur_tokens = 0
    idx = 0

    def flush(carry: List[str]) -> List[str]:
        nonlocal idx, cur_tokens
        content = " ".join(cur).strip()
        if content:
            chunks.append(
                Chunk(
                    content=content,
                    modality="text",
                    parent_doc_id=parent_doc_id,
                    chunk_index=idx,
                    token_estimate=estimate_tokens(content),
                    metadata=dict(base_meta),
                )
            )
            idx += 1
        # Build overlap carry from the tail of the just-flushed chunk.
        carry_sentences: List[str] = []
        carry_tokens = 0
        for s in reversed(cur):
            t = estimate_tokens(s)
            if carry_tokens + t > overlap_tokens:
                break
            carry_sentences.insert(0, s)
            carry_tokens += t
        return carry_sentences

    for sentence in sentences:
        t = estimate_tokens(sentence)
        if cur and cur_tokens + t > target_tokens:
            carry = flush(cur)
            cur = list(carry)
            cur_tokens = sum(estimate_tokens(s) for s in cur)
        cur.append(sentence)
        cur_tokens += t

    if cur:
        # Final flush without producing a new overlap carry.
        content = " ".join(cur).strip()
        if content:
            chunks.append(
                Chunk(
                    content=content,
                    modality="text",
                    parent_doc_id=parent_doc_id,
                    chunk_index=idx,
                    token_estimate=estimate_tokens(content),
                    metadata=dict(base_meta),
                )
            )
    return chunks


_HEADING_RE = re.compile(r"^(#{1,6}\s+.*|[A-Z0-9][A-Z0-9 \-]{3,}:?)$", re.MULTILINE)


def chunk_by_heading(
    text: str,
    parent_doc_id: str,
    *,
    target_tokens: int = 800,
    overlap_ratio: float = 0.15,
    metadata: Optional[Dict[str, object]] = None,
) -> List[Chunk]:
    """Chunk policies/procedures by heading first, then size within sections."""
    base_meta = dict(metadata or {})
    matches = list(_HEADING_RE.finditer(text))
    if not matches:
        return chunk_document(
            text,
            parent_doc_id,
            target_tokens=target_tokens,
            overlap_ratio=overlap_ratio,
            metadata=base_meta,
        )

    sections: List[tuple] = []
    for i, m in enumerate(matches):
        heading = m.group().lstrip("#").strip().rstrip(":")
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        if body:
            sections.append((heading, body))

    chunks: List[Chunk] = []
    idx = 0
    for heading, body in sections:
        meta = dict(base_meta)
        meta["section_heading"] = heading
        sub = chunk_document(
            body,
            parent_doc_id,
            target_tokens=target_tokens,
            overlap_ratio=overlap_ratio,
            metadata=meta,
        )
        for c in sub:
            c.chunk_index = idx
            idx += 1
            chunks.append(c)
    return chunks


@dataclass
class TranscriptSegment:
    start: float  # seconds
    end: float
    text: str
    speaker: str = ""


def chunk_transcript(
    segments: Sequence[TranscriptSegment],
    parent_doc_id: str,
    *,
    min_seconds: float = 30.0,
    max_seconds: float = 90.0,
    metadata: Optional[Dict[str, object]] = None,
) -> List[Chunk]:
    """Group ASR segments into 30–90s semantic chunks.

    Boundaries are forced at a speaker change or once max_seconds is reached;
    a new chunk is only finalized after min_seconds of content.
    """
    base_meta = dict(metadata or {})
    chunks: List[Chunk] = []
    idx = 0
    buf: List[TranscriptSegment] = []

    def buf_duration() -> float:
        return (buf[-1].end - buf[0].start) if buf else 0.0

    def flush() -> None:
        nonlocal idx
        if not buf:
            return
        content = " ".join(s.text.strip() for s in buf).strip()
        if not content:
            buf.clear()
            return
        meta = dict(base_meta)
        meta.update(
            {
                "timestamp_start": buf[0].start,
                "timestamp_end": buf[-1].end,
                "speaker": buf[0].speaker,
            }
        )
        chunks.append(
            Chunk(
                content=content,
                modality="audio_video",
                parent_doc_id=parent_doc_id,
                chunk_index=idx,
                token_estimate=estimate_tokens(content),
                metadata=meta,
            )
        )
        idx += 1
        buf.clear()

    for seg in segments:
        speaker_change = bool(buf) and seg.speaker and seg.speaker != buf[-1].speaker
        if speaker_change and buf_duration() >= min_seconds:
            flush()
        buf.append(seg)
        if buf_duration() >= max_seconds:
            flush()
    flush()
    return chunks


def chunk_table_records(
    records: Sequence[Dict[str, object]],
    parent_doc_id: str,
    *,
    metadata: Optional[Dict[str, object]] = None,
) -> List[Chunk]:
    """Emit one small semantic record per KPI/row (never one giant blob)."""
    base_meta = dict(metadata or {})
    chunks: List[Chunk] = []
    for idx, rec in enumerate(records):
        parts = [f"{k}: {v}" for k, v in rec.items()]
        content = "; ".join(parts)
        meta = dict(base_meta)
        meta["table_json"] = dict(rec)
        chunks.append(
            Chunk(
                content=content,
                modality="table",
                parent_doc_id=parent_doc_id,
                chunk_index=idx,
                token_estimate=estimate_tokens(content),
                metadata=meta,
            )
        )
    return chunks

"""Ingestion pipeline orchestrator.

Routes a source by modality through the right extractor → chunker → embedder →
indexer. Designed so the chunking stage is fully exercised by unit tests while
Azure-dependent stages stay lazy.

CLI:
    python -m src.ingestion.pipeline --ensure-index
    python -m src.ingestion.pipeline --source document --path ./file.pdf --doc-id d1
    python -m src.ingestion.pipeline --source transcript --url https://.../video.mp4 --doc-id v1
"""
from __future__ import annotations

import argparse
import datetime as _dt
from typing import Dict, List, Optional, Sequence

from .chunking import (
    Chunk,
    chunk_by_heading,
    chunk_document,
    chunk_table_records,
    chunk_transcript,
)


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def chunk_to_document(
    chunk: Chunk,
    *,
    embedding: Optional[List[float]] = None,
    acl_groups: Optional[Sequence[str]] = None,
    source_system: str = "",
    source_url: str = "",
    business_domain: str = "",
) -> Dict[str, object]:
    """Map a Chunk + embedding into an AI Search index document."""
    meta = chunk.metadata or {}
    doc: Dict[str, object] = {
        "chunk_id": chunk.chunk_id,
        "parent_doc_id": chunk.parent_doc_id,
        "title": str(meta.get("section_heading", "")),
        "content": chunk.content,
        "modality": chunk.modality,
        "source_system": source_system,
        "source_url": source_url,
        "business_domain": business_domain,
        "section_heading": str(meta.get("section_heading", "")),
        "speaker": str(meta.get("speaker", "")),
        "ingested_utc": _now_iso(),
        "is_deleted": False,
        "acl_groups": list(acl_groups or ["public"]),
    }
    if "timestamp_start" in meta:
        doc["timestamp_start"] = float(meta["timestamp_start"])
        doc["timestamp_end"] = float(meta["timestamp_end"])
    if embedding is not None:
        doc["content_vector"] = embedding
    return doc


class Pipeline:
    def __init__(self):
        self._indexer = None
        self._embedder = None

    def _get_indexer(self):
        if self._indexer is None:
            from .indexer import Indexer

            self._indexer = Indexer()
        return self._indexer

    def _embed(self, text: str) -> List[float]:
        if self._embedder is None:
            from ..agent.tools.search_tool import SearchTool

            self._embedder = SearchTool()
        return self._embedder._embed(text)  # noqa: SLF001 - reuse embedding path

    def ensure_index(self) -> None:
        self._get_indexer().ensure_index()

    def ingest_document(self, file_bytes: bytes, doc_id: str, *, policy: bool = False, **kw) -> int:
        from .extractors.document_intelligence import DocumentExtractor

        extracted = DocumentExtractor().extract(file_bytes)
        chunker = chunk_by_heading if policy else chunk_document
        chunks = chunker(extracted.text, doc_id)
        return self._index_chunks(chunks, **kw)

    def ingest_transcript(self, media_url: str, doc_id: str, **kw) -> int:
        from .extractors.speech import SpeechExtractor

        segments = SpeechExtractor().transcribe(media_url)
        chunks = chunk_transcript(segments, doc_id)
        return self._index_chunks(chunks, source_url=media_url, **kw)

    def ingest_tableau_metadata(self, doc_id: str = "tableau-kpis", **kw) -> int:
        from .extractors.tableau import TableauMetadataExtractor

        records = TableauMetadataExtractor().list_kpi_records()
        chunks = chunk_table_records(records, doc_id)
        return self._index_chunks(chunks, source_system="tableau", **kw)

    def _index_chunks(self, chunks: List[Chunk], **kw) -> int:
        docs = [chunk_to_document(c, embedding=self._embed(c.content), **kw) for c in chunks]
        if not docs:
            return 0
        return self._get_indexer().upload(docs)


def main() -> None:
    parser = argparse.ArgumentParser(description="Multimodal ingestion pipeline")
    parser.add_argument("--ensure-index", action="store_true")
    parser.add_argument("--source", choices=["document", "policy", "transcript", "tableau"])
    parser.add_argument("--path", type=str, help="local file path (document/policy)")
    parser.add_argument("--url", type=str, help="media URL (transcript)")
    parser.add_argument("--doc-id", type=str, default="doc")
    args = parser.parse_args()

    pipeline = Pipeline()
    if args.ensure_index:
        pipeline.ensure_index()
        print(f"Index ensured.")
    if args.source in {"document", "policy"} and args.path:
        with open(args.path, "rb") as f:
            n = pipeline.ingest_document(f.read(), args.doc_id, policy=(args.source == "policy"))
        print(f"Indexed {n} chunks from {args.path}")
    elif args.source == "transcript" and args.url:
        n = pipeline.ingest_transcript(args.url, args.doc_id)
        print(f"Indexed {n} transcript chunks from {args.url}")
    elif args.source == "tableau":
        n = pipeline.ingest_tableau_metadata()
        print(f"Indexed {n} Tableau KPI metadata records")


if __name__ == "__main__":
    main()

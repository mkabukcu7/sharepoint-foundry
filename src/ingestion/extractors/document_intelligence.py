"""Document text/layout extraction via Azure AI Document Intelligence.

Returns clean text plus structural metadata (headings, page ranges, tables) for
the chunker. SDK imported lazily.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class ExtractedDoc:
    text: str
    page_count: int = 0
    tables: List[dict] = field(default_factory=list)
    headings: List[str] = field(default_factory=list)


class DocumentExtractor:
    def __init__(self, endpoint: Optional[str] = None):
        from ...agent.config import get_settings

        self._endpoint = endpoint or get_settings().doc_intelligence_endpoint
        self._client = None

    def _get_client(self):
        if self._client is None:
            from azure.ai.documentintelligence import DocumentIntelligenceClient
            from azure.identity import DefaultAzureCredential

            self._client = DocumentIntelligenceClient(
                endpoint=self._endpoint, credential=DefaultAzureCredential()
            )
        return self._client

    def extract(self, file_bytes: bytes) -> ExtractedDoc:
        client = self._get_client()
        from azure.ai.documentintelligence.models import AnalyzeDocumentRequest

        poller = client.begin_analyze_document(
            "prebuilt-layout", AnalyzeDocumentRequest(bytes_source=file_bytes)
        )
        result = poller.result()
        text = result.content or ""
        headings = [
            p.content
            for p in (result.paragraphs or [])
            if getattr(p, "role", None) in {"title", "sectionHeading"}
        ]
        tables = []
        for t in result.tables or []:
            tables.append(
                {
                    "row_count": t.row_count,
                    "column_count": t.column_count,
                    "cells": [
                        {"row": c.row_index, "col": c.column_index, "content": c.content}
                        for c in t.cells
                    ],
                }
            )
        return ExtractedDoc(
            text=text,
            page_count=len(result.pages or []),
            tables=tables,
            headings=headings,
        )

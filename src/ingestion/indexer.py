"""Azure AI Search index management and document upload.

Defines the index schema from architecture §4.4 (core / navigation / freshness /
security fields, vector + semantic config) and upserts chunks. SDK imported
lazily so the schema can be inspected without Azure installed.
"""
from __future__ import annotations

from typing import List, Optional, Sequence

from .chunking import Chunk

VECTOR_DIMENSIONS = 3072  # text-embedding-3-large


def build_index_schema(index_name: str):
    """Return a SearchIndex object describing the workplace-knowledge index."""
    from azure.search.documents.indexes.models import (
        HnswAlgorithmConfiguration,
        SearchableField,
        SearchField,
        SearchFieldDataType,
        SearchIndex,
        SemanticConfiguration,
        SemanticField,
        SemanticPrioritizedFields,
        SemanticSearch,
        SimpleField,
        VectorSearch,
        VectorSearchProfile,
    )

    fields = [
        SimpleField(name="chunk_id", type=SearchFieldDataType.String, key=True),
        SimpleField(name="parent_doc_id", type=SearchFieldDataType.String, filterable=True),
        SearchableField(name="title", type=SearchFieldDataType.String),
        SearchableField(name="content", type=SearchFieldDataType.String),
        SearchField(
            name="content_vector",
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            searchable=True,
            vector_search_dimensions=VECTOR_DIMENSIONS,
            vector_search_profile_name="default",
        ),
        # Navigation / provenance
        SimpleField(name="source_system", type=SearchFieldDataType.String, filterable=True, facetable=True),
        SimpleField(name="source_url", type=SearchFieldDataType.String),
        SimpleField(name="modality", type=SearchFieldDataType.String, filterable=True, facetable=True),
        SimpleField(name="business_domain", type=SearchFieldDataType.String, filterable=True, facetable=True),
        SimpleField(name="language", type=SearchFieldDataType.String, filterable=True),
        SearchableField(name="section_heading", type=SearchFieldDataType.String),
        SimpleField(name="page_start", type=SearchFieldDataType.Int32, filterable=True),
        SimpleField(name="page_end", type=SearchFieldDataType.Int32, filterable=True),
        SimpleField(name="timestamp_start", type=SearchFieldDataType.Double, filterable=True),
        SimpleField(name="timestamp_end", type=SearchFieldDataType.Double, filterable=True),
        SimpleField(name="speaker", type=SearchFieldDataType.String, filterable=True),
        # Freshness
        SimpleField(name="last_modified_utc", type=SearchFieldDataType.DateTimeOffset, filterable=True, sortable=True),
        SimpleField(name="ingested_utc", type=SearchFieldDataType.DateTimeOffset, filterable=True, sortable=True),
        SimpleField(name="source_etag", type=SearchFieldDataType.String),
        SimpleField(name="is_deleted", type=SearchFieldDataType.Boolean, filterable=True),
        # Security (query-time ACL trimming)
        SearchField(
            name="acl_groups",
            type=SearchFieldDataType.Collection(SearchFieldDataType.String),
            filterable=True,
        ),
        SimpleField(name="sensitivity_label", type=SearchFieldDataType.String, filterable=True),
        SimpleField(name="pii_flag", type=SearchFieldDataType.Boolean, filterable=True),
        SimpleField(name="tenant_id", type=SearchFieldDataType.String, filterable=True),
    ]

    vector_search = VectorSearch(
        algorithms=[HnswAlgorithmConfiguration(name="hnsw")],
        profiles=[VectorSearchProfile(name="default", algorithm_configuration_name="hnsw")],
    )
    semantic = SemanticSearch(
        configurations=[
            SemanticConfiguration(
                name="default",
                prioritized_fields=SemanticPrioritizedFields(
                    title_field=SemanticField(field_name="title"),
                    content_fields=[SemanticField(field_name="content")],
                    keywords_fields=[SemanticField(field_name="section_heading")],
                ),
            )
        ]
    )
    return SearchIndex(
        name=index_name,
        fields=fields,
        vector_search=vector_search,
        semantic_search=semantic,
    )


class Indexer:
    def __init__(self, endpoint: Optional[str] = None, index_name: Optional[str] = None):
        from ..agent.config import get_settings

        s = get_settings()
        self._endpoint = endpoint or s.search_endpoint
        self._index = index_name or s.search_index_name

    def _credential(self):
        from azure.identity import DefaultAzureCredential

        return DefaultAzureCredential()

    def ensure_index(self) -> None:
        from azure.search.documents.indexes import SearchIndexClient

        client = SearchIndexClient(endpoint=self._endpoint, credential=self._credential())
        client.create_or_update_index(build_index_schema(self._index))

    def upload(self, documents: Sequence[dict]) -> int:
        from azure.search.documents import SearchClient

        client = SearchClient(
            endpoint=self._endpoint, index_name=self._index, credential=self._credential()
        )
        result = client.merge_or_upload_documents(documents=list(documents))
        return sum(1 for r in result if r.succeeded)

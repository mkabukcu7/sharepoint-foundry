"""Curated knowledge retrieval tool over Azure AI Search.

Performs ``vector_semantic_hybrid`` retrieval (BM25 + vector + semantic ranker)
with **query-time ACL filtering** from the caller's Entra group claims
(architecture §4.4/§4.5). Azure SDKs are imported lazily.
"""
from __future__ import annotations

from typing import List, Optional, Sequence

from ..routing import Evidence


def _build_acl_filter(acl_groups: Optional[Sequence[str]]) -> Optional[str]:
    """Build an OData security-trimming filter from the user's group claims."""
    if not acl_groups:
        # No claims → restrict to content explicitly marked public.
        return "acl_groups/any(g: g eq 'public')"
    quoted = " or ".join(f"g eq '{g}'" for g in acl_groups)
    public = "g eq 'public'"
    return f"acl_groups/any(g: {quoted} or {public})"


class SearchTool:
    def __init__(self, endpoint: Optional[str] = None, index_name: Optional[str] = None):
        from ..config import get_settings

        s = get_settings()
        self._endpoint = endpoint or s.search_endpoint
        self._index = index_name or s.search_index_name
        self._embedding_deployment = s.embedding_model_deployment
        self._top_k = s.retrieval_top_k
        self._rerank_k = s.rerank_top_k
        self._client = None

    def _get_client(self):
        if self._client is None:
            from azure.search.documents import SearchClient
            from azure.identity import DefaultAzureCredential

            self._client = SearchClient(
                endpoint=self._endpoint,
                index_name=self._index,
                credential=DefaultAzureCredential(),
            )
        return self._client

    def _embed(self, text: str) -> List[float]:
        from azure.ai.inference import EmbeddingsClient  # type: ignore
        from azure.identity import DefaultAzureCredential

        # In Foundry, embeddings are typically called via the project's models
        # endpoint; adjust to your deployment. This is illustrative.
        client = EmbeddingsClient(
            endpoint=self._endpoint, credential=DefaultAzureCredential()
        )
        resp = client.embed(input=[text], model=self._embedding_deployment)
        return resp.data[0].embedding

    def search(
        self,
        query: str,
        *,
        acl_groups: Optional[Sequence[str]] = None,
        modality: Optional[str] = None,
        top_k: Optional[int] = None,
    ) -> List[Evidence]:
        """Hybrid retrieval with security trimming. Returns ranked Evidence."""
        client = self._get_client()
        from azure.search.documents.models import VectorizedQuery

        k = top_k or self._top_k
        filter_expr = _build_acl_filter(acl_groups)
        if modality:
            mod_clause = f"modality eq '{modality}'"
            filter_expr = f"({filter_expr}) and {mod_clause}" if filter_expr else mod_clause

        vector = VectorizedQuery(
            vector=self._embed(query), k_nearest_neighbors=k, fields="content_vector"
        )
        results = client.search(
            search_text=query,
            vector_queries=[vector],
            query_type="semantic",
            semantic_configuration_name="default",
            filter=filter_expr,
            top=k,
            select=["chunk_id", "content", "source_url", "modality"],
        )
        evidence: List[Evidence] = []
        for r in results:
            score = r.get("@search.reranker_score") or r.get("@search.score") or 0.0
            # Reranker scores are 0–4; normalize to [0,1] for confidence gating.
            norm = min(float(score) / 4.0, 1.0) if score and score > 1 else float(score)
            evidence.append(
                Evidence(
                    chunk_id=r.get("chunk_id", ""),
                    score=norm,
                    content=r.get("content", ""),
                    source_url=r.get("source_url", ""),
                    modality=r.get("modality", "text"),
                )
            )
        return evidence[: self._rerank_k]

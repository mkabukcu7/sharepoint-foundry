"""Typed application settings.

Reads the configuration contract defined in `.env.example`. Uses
pydantic-settings when available, otherwise falls back to a small
os.environ-backed shim so that pure-logic modules and unit tests can
import `get_settings()` without the optional dependency installed.
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Optional

try:  # pragma: no cover - exercised indirectly
    from pydantic import Field
    from pydantic_settings import BaseSettings, SettingsConfigDict

    _HAVE_PYDANTIC = True
except Exception:  # pragma: no cover
    _HAVE_PYDANTIC = False


if _HAVE_PYDANTIC:

    class Settings(BaseSettings):
        model_config = SettingsConfigDict(
            env_file=".env", env_file_encoding="utf-8", extra="ignore"
        )

        # Foundry
        foundry_project_endpoint: str = Field("", alias="FOUNDRY_PROJECT_ENDPOINT")
        agent_model_deployment: str = Field("gpt-4.1", alias="AGENT_MODEL_DEPLOYMENT")
        triage_model_deployment: str = Field(
            "gpt-4.1-mini", alias="TRIAGE_MODEL_DEPLOYMENT"
        )
        vision_model_deployment: str = Field("gpt-4o", alias="VISION_MODEL_DEPLOYMENT")
        embedding_model_deployment: str = Field(
            "text-embedding-3-large", alias="EMBEDDING_MODEL_DEPLOYMENT"
        )

        # Search
        search_endpoint: str = Field("", alias="SEARCH_ENDPOINT")
        search_index_name: str = Field("workplace-knowledge", alias="SEARCH_INDEX_NAME")

        # Supporting cognitive services
        content_safety_endpoint: str = Field("", alias="CONTENT_SAFETY_ENDPOINT")
        doc_intelligence_endpoint: str = Field("", alias="DOC_INTELLIGENCE_ENDPOINT")
        speech_endpoint: str = Field("", alias="SPEECH_ENDPOINT")
        speech_region: str = Field("", alias="SPEECH_REGION")

        # Storage
        storage_account_url: str = Field("", alias="STORAGE_ACCOUNT_URL")
        storage_container: str = Field("ingestion", alias="STORAGE_CONTAINER")

        # Grounding sources
        sharepoint_site_url: str = Field("", alias="SHAREPOINT_SITE_URL")
        # Indexed SharePoint knowledge source (agentic retrieval)
        azure_openai_endpoint: str = Field("", alias="AZURE_OPENAI_ENDPOINT")
        sharepoint_connection_string: str = Field("", alias="SHAREPOINT_CONNECTION_STRING")
        sharepoint_ks_name: str = Field(
            "sharepoint-indexed-ks", alias="SHAREPOINT_KS_NAME"
        )
        sharepoint_container_name: str = Field(
            "defaultSiteLibrary", alias="SHAREPOINT_CONTAINER_NAME"
        )
        sharepoint_ks_query: str = Field("", alias="SHAREPOINT_KS_QUERY")
        sharepoint_ingestion_permissions: str = Field(
            "", alias="SHAREPOINT_INGESTION_PERMISSIONS"
        )
        knowledge_base_name: str = Field(
            "workplace-knowledge-base", alias="KNOWLEDGE_BASE_NAME"
        )
        tableau_server_url: str = Field("", alias="TABLEAU_SERVER_URL")
        tableau_site_id: str = Field("", alias="TABLEAU_SITE_ID")
        tableau_api_version: str = Field("3.21", alias="TABLEAU_API_VERSION")
        tableau_datasource_luid: str = Field("", alias="TABLEAU_DATASOURCE_LUID")
        tableau_measure_field: str = Field("", alias="TABLEAU_MEASURE_FIELD")
        tableau_date_field: str = Field("", alias="TABLEAU_DATE_FIELD")
        tableau_source_workbook: str = Field("", alias="TABLEAU_SOURCE_WORKBOOK")

        # Thresholds (architecture §9, §15)
        groundedness_min: float = Field(4.2, alias="GROUNDEDNESS_MIN")
        relevance_min: float = Field(4.0, alias="RELEVANCE_MIN")
        retrieval_top_k: int = Field(8, alias="RETRIEVAL_TOP_K")
        rerank_top_k: int = Field(4, alias="RERANK_TOP_K")
        low_confidence_min_score: float = Field(0.30, alias="LOW_CONFIDENCE_MIN_SCORE")
        max_evidence_tokens: int = Field(6000, alias="MAX_EVIDENCE_TOKENS")

else:  # Lightweight fallback used when pydantic-settings isn't installed.

    def _f(name: str, default: float) -> float:
        try:
            return float(os.environ.get(name, default))
        except (TypeError, ValueError):
            return default

    def _i(name: str, default: int) -> int:
        try:
            return int(os.environ.get(name, default))
        except (TypeError, ValueError):
            return default

    class Settings:  # type: ignore[no-redef]
        def __init__(self) -> None:
            self.foundry_project_endpoint = os.environ.get("FOUNDRY_PROJECT_ENDPOINT", "")
            self.agent_model_deployment = os.environ.get("AGENT_MODEL_DEPLOYMENT", "gpt-4.1")
            self.triage_model_deployment = os.environ.get(
                "TRIAGE_MODEL_DEPLOYMENT", "gpt-4.1-mini"
            )
            self.vision_model_deployment = os.environ.get(
                "VISION_MODEL_DEPLOYMENT", "gpt-4o"
            )
            self.embedding_model_deployment = os.environ.get(
                "EMBEDDING_MODEL_DEPLOYMENT", "text-embedding-3-large"
            )
            self.search_endpoint = os.environ.get("SEARCH_ENDPOINT", "")
            self.search_index_name = os.environ.get("SEARCH_INDEX_NAME", "workplace-knowledge")
            self.content_safety_endpoint = os.environ.get("CONTENT_SAFETY_ENDPOINT", "")
            self.doc_intelligence_endpoint = os.environ.get("DOC_INTELLIGENCE_ENDPOINT", "")
            self.speech_endpoint = os.environ.get("SPEECH_ENDPOINT", "")
            self.speech_region = os.environ.get("SPEECH_REGION", "")
            self.storage_account_url = os.environ.get("STORAGE_ACCOUNT_URL", "")
            self.storage_container = os.environ.get("STORAGE_CONTAINER", "ingestion")
            self.sharepoint_site_url = os.environ.get("SHAREPOINT_SITE_URL", "")
            self.azure_openai_endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "")
            self.sharepoint_connection_string = os.environ.get(
                "SHAREPOINT_CONNECTION_STRING", ""
            )
            self.sharepoint_ks_name = os.environ.get(
                "SHAREPOINT_KS_NAME", "sharepoint-indexed-ks"
            )
            self.sharepoint_container_name = os.environ.get(
                "SHAREPOINT_CONTAINER_NAME", "defaultSiteLibrary"
            )
            self.sharepoint_ks_query = os.environ.get("SHAREPOINT_KS_QUERY", "")
            self.sharepoint_ingestion_permissions = os.environ.get(
                "SHAREPOINT_INGESTION_PERMISSIONS", ""
            )
            self.knowledge_base_name = os.environ.get(
                "KNOWLEDGE_BASE_NAME", "workplace-knowledge-base"
            )
            self.tableau_server_url = os.environ.get("TABLEAU_SERVER_URL", "")
            self.tableau_site_id = os.environ.get("TABLEAU_SITE_ID", "")
            self.tableau_api_version = os.environ.get("TABLEAU_API_VERSION", "3.21")
            self.tableau_datasource_luid = os.environ.get("TABLEAU_DATASOURCE_LUID", "")
            self.tableau_measure_field = os.environ.get("TABLEAU_MEASURE_FIELD", "")
            self.tableau_date_field = os.environ.get("TABLEAU_DATE_FIELD", "")
            self.tableau_source_workbook = os.environ.get("TABLEAU_SOURCE_WORKBOOK", "")
            self.groundedness_min = _f("GROUNDEDNESS_MIN", 4.2)
            self.relevance_min = _f("RELEVANCE_MIN", 4.0)
            self.retrieval_top_k = _i("RETRIEVAL_TOP_K", 8)
            self.rerank_top_k = _i("RERANK_TOP_K", 4)
            self.low_confidence_min_score = _f("LOW_CONFIDENCE_MIN_SCORE", 0.30)
            self.max_evidence_tokens = _i("MAX_EVIDENCE_TOKENS", 6000)


@lru_cache(maxsize=1)
def get_settings() -> "Settings":
    """Return a cached Settings instance."""
    return Settings()


def reset_settings_cache() -> None:
    """Clear the cached settings (useful in tests after changing env)."""
    get_settings.cache_clear()

"""Unit tests for the indexed SharePoint knowledge source payload builders.

Pure-logic only — no Azure dependency.
"""
import pytest

from src.ingestion.sharepoint_knowledge_source import (
    build_indexed_sharepoint_knowledge_source,
    build_knowledge_base,
)


def _base_kwargs(**overrides):
    kwargs = dict(
        name="sharepoint-indexed-ks",
        connection_string="SharePointOnlineEndpoint=https://contoso.sharepoint.com/sites/pgr-it;ApplicationId=app;TenantId=tid",
        aoai_endpoint="https://foundry.openai.azure.com",
    )
    kwargs.update(overrides)
    return kwargs


def test_minimal_payload_shape():
    ks = build_indexed_sharepoint_knowledge_source(**_base_kwargs())
    assert ks["name"] == "sharepoint-indexed-ks"
    assert ks["kind"] == "indexedSharePoint"
    params = ks["indexedSharePointParameters"]
    assert params["containerName"] == "defaultSiteLibrary"
    assert params["query"] is None
    ing = params["ingestionParameters"]
    assert ing["contentExtractionMode"] == "minimal"
    assert ing["ingestionPermissionOptions"] == []
    # Image verbalization disabled by default -> no chat completion model.
    assert ing["disableImageVerbalization"] is True
    assert ing["chatCompletionModel"] is None


def test_embedding_model_uses_managed_identity_no_key():
    ks = build_indexed_sharepoint_knowledge_source(**_base_kwargs())
    emb = ks["indexedSharePointParameters"]["ingestionParameters"]["embeddingModel"]
    assert emb["kind"] == "azureOpenAI"
    p = emb["azureOpenAIParameters"]
    assert p["resourceUri"] == "https://foundry.openai.azure.com"
    assert p["deploymentId"] == "text-embedding-3-large"
    # No apiKey / authIdentity => search service managed identity is used.
    assert "apiKey" not in p
    assert "authIdentity" not in p


def test_query_scoping_and_permissions_included():
    ks = build_indexed_sharepoint_knowledge_source(
        **_base_kwargs(
            query="includeLibrary=https://contoso.sharepoint.com/sites/pgr-it/Policies",
            ingestion_permission_options=["userIds", "groupIds"],
            container_name="allSiteLibraries",
        )
    )
    params = ks["indexedSharePointParameters"]
    assert params["containerName"] == "allSiteLibraries"
    assert "Policies" in params["query"]
    assert params["ingestionParameters"]["ingestionPermissionOptions"] == [
        "userIds",
        "groupIds",
    ]


def test_image_verbalization_requires_vision_model():
    with pytest.raises(ValueError):
        build_indexed_sharepoint_knowledge_source(
            **_base_kwargs(disable_image_verbalization=False)
        )


def test_image_verbalization_adds_chat_completion_model():
    ks = build_indexed_sharepoint_knowledge_source(
        **_base_kwargs(
            disable_image_verbalization=False,
            vision_deployment="gpt-4o",
            vision_model="gpt-4o",
        )
    )
    ing = ks["indexedSharePointParameters"]["ingestionParameters"]
    assert ing["disableImageVerbalization"] is False
    assert ing["chatCompletionModel"]["azureOpenAIParameters"]["deploymentId"] == "gpt-4o"


@pytest.mark.parametrize("missing", ["name", "connection_string", "aoai_endpoint"])
def test_required_fields_validated(missing):
    kwargs = _base_kwargs()
    kwargs[missing] = ""
    with pytest.raises(ValueError):
        build_indexed_sharepoint_knowledge_source(**kwargs)


def test_knowledge_base_includes_reference_source_data():
    kb = build_knowledge_base(
        name="workplace-knowledge-base",
        knowledge_source_names=["sharepoint-indexed-ks"],
    )
    assert kb["name"] == "workplace-knowledge-base"
    assert kb["knowledgeSources"] == [
        {"name": "sharepoint-indexed-ks", "includeReferenceSourceData": True}
    ]


def test_knowledge_base_requires_a_source():
    with pytest.raises(ValueError):
        build_knowledge_base(name="kb", knowledge_source_names=[])

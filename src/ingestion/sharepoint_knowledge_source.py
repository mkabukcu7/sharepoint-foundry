"""Indexed SharePoint knowledge source for Azure AI Search agentic retrieval.

Implements the *indexed SharePoint knowledge source* described in
https://learn.microsoft.com/azure/search/agentic-knowledge-source-how-to-sharepoint-indexed

This is the **curated / indexed** SharePoint lane. Unlike the live Foundry
SharePoint tool (``src/agent/agent.py`` -> ``SharepointTool``), which grounds at
query time via the Microsoft 365 Copilot Retrieval API, this lane *ingests*
SharePoint content into an Azure AI Search agentic-retrieval pipeline. Creating
the knowledge source auto-generates a data source, skillset, indexer, and index.

Why it matters for this project:

* **Folder / library scoping (include-only)** is expressed through the
  ``query`` parameter, so you can restrict ingestion to specific document
  libraries or folders (the control the live tool does not offer).
* **Document-level permission enforcement** is expressed through
  ``ingestionPermissionOptions`` plus passing the user's token at query time.

Design notes (consistent with the rest of the repo):

* The payload builders (:func:`build_indexed_sharepoint_knowledge_source` and
  :func:`build_knowledge_base`) are **pure functions** and fully unit-tested
  with no Azure dependency.
* :class:`SharePointKnowledgeSourceClient` performs the REST calls lazily using
  ``DefaultAzureCredential`` (RBAC/keyless), matching the local-auth-disabled
  posture of the landing zone. It targets the ``2026-05-01-preview`` REST API.

CLI::

    python -m src.ingestion.sharepoint_knowledge_source --create
    python -m src.ingestion.sharepoint_knowledge_source --status
    python -m src.ingestion.sharepoint_knowledge_source --create-knowledge-base
    python -m src.ingestion.sharepoint_knowledge_source --list
    python -m src.ingestion.sharepoint_knowledge_source --delete
"""
from __future__ import annotations

import argparse
import json
from typing import Any, Dict, List, Optional, Sequence

# Data-plane AAD scope for Azure AI Search (keyless / RBAC auth).
SEARCH_SCOPE = "https://search.azure.com/.default"
DEFAULT_API_VERSION = "2026-05-01-preview"
DEFAULT_CONTAINER = "defaultSiteLibrary"


def _azure_openai_model(
    *, resource_uri: str, deployment_id: str, model_name: str
) -> Dict[str, Any]:
    """Azure OpenAI model reference using the search service managed identity.

    ``apiKey`` and ``authIdentity`` are intentionally omitted so Azure AI Search
    authenticates to the Foundry / Azure OpenAI resource with its own
    system-assigned managed identity (Cognitive Services User role).
    """
    return {
        "kind": "azureOpenAI",
        "azureOpenAIParameters": {
            "resourceUri": resource_uri,
            "deploymentId": deployment_id,
            "modelName": model_name,
        },
    }


def build_indexed_sharepoint_knowledge_source(
    *,
    name: str,
    connection_string: str,
    aoai_endpoint: str,
    embedding_deployment: str = "text-embedding-3-large",
    embedding_model: str = "text-embedding-3-large",
    container_name: str = DEFAULT_CONTAINER,
    query: Optional[str] = None,
    description: str = "",
    disable_image_verbalization: bool = True,
    vision_deployment: Optional[str] = None,
    vision_model: Optional[str] = None,
    ingestion_permission_options: Optional[Sequence[str]] = None,
    content_extraction_mode: str = "minimal",
) -> Dict[str, Any]:
    """Build the JSON body for an indexed SharePoint knowledge source.

    Args:
        name: Knowledge source name (also seeds the generated object names).
        connection_string: SharePoint indexer connection string (see the
            SharePoint indexer prerequisites). Store the secret form in Key Vault.
        aoai_endpoint: Azure OpenAI / Foundry resource URI used for embeddings
            (and optionally image verbalization).
        embedding_deployment/embedding_model: embedding model deployment + name.
        container_name: SharePoint container. ``defaultSiteLibrary`` targets the
            default document library; ``allSiteLibraries`` targets all libraries.
        query: Include-only scoping. Restricts ingestion to specific libraries /
            folders (SharePoint indexer ``query`` syntax). ``None`` = whole
            container.
        disable_image_verbalization: when ``False`` a chat-completion model is
            required to verbalize document-embedded images.
        vision_deployment/vision_model: chat-completion model for image
            verbalization (required only when verbalization is enabled).
        ingestion_permission_options: e.g. ``["userIds", "groupIds"]`` to ingest
            SharePoint ACLs for query-time permission enforcement.
        content_extraction_mode: ``minimal`` (default) or a richer mode.
    """
    if not name:
        raise ValueError("name is required")
    if not connection_string:
        raise ValueError("connection_string is required")
    if not aoai_endpoint:
        raise ValueError("aoai_endpoint is required for the embedding model")

    ingestion: Dict[str, Any] = {
        "identity": None,
        "embeddingModel": _azure_openai_model(
            resource_uri=aoai_endpoint,
            deployment_id=embedding_deployment,
            model_name=embedding_model,
        ),
        "chatCompletionModel": None,
        "disableImageVerbalization": disable_image_verbalization,
        "ingestionSchedule": None,
        "ingestionPermissionOptions": list(ingestion_permission_options or []),
        "contentExtractionMode": content_extraction_mode,
    }

    if not disable_image_verbalization:
        if not (vision_deployment and vision_model):
            raise ValueError(
                "vision_deployment and vision_model are required when "
                "image verbalization is enabled"
            )
        ingestion["chatCompletionModel"] = _azure_openai_model(
            resource_uri=aoai_endpoint,
            deployment_id=vision_deployment,
            model_name=vision_model,
        )

    return {
        "name": name,
        "kind": "indexedSharePoint",
        "description": description,
        "encryptionKey": None,
        "indexedSharePointParameters": {
            "connectionString": connection_string,
            "containerName": container_name,
            "query": query,
            "ingestionParameters": ingestion,
        },
    }


def build_knowledge_base(
    *,
    name: str,
    knowledge_source_names: Sequence[str],
    description: str = "",
) -> Dict[str, Any]:
    """Build a knowledge base that references one or more knowledge sources.

    For indexed SharePoint knowledge sources, ``includeReferenceSourceData`` is
    set to ``True`` so the source document URL flows into citations (required by
    the docs).
    """
    if not knowledge_source_names:
        raise ValueError("at least one knowledge source name is required")
    return {
        "name": name,
        "description": description,
        "knowledgeSources": [
            {"name": ks, "includeReferenceSourceData": True}
            for ks in knowledge_source_names
        ],
    }


class SharePointKnowledgeSourceClient:
    """Thin REST client for knowledge-source / knowledge-base management.

    Uses ``DefaultAzureCredential`` (RBAC) — no admin key. The caller needs the
    **Search Service Contributor** and **Search Index Data Contributor** roles.
    """

    def __init__(
        self,
        endpoint: Optional[str] = None,
        *,
        api_version: str = DEFAULT_API_VERSION,
    ):
        from ..agent.config import get_settings

        s = get_settings()
        self._endpoint = (endpoint or s.search_endpoint).rstrip("/")
        self._api_version = api_version
        self._credential = None

    def _token(self) -> str:
        if self._credential is None:
            from azure.identity import DefaultAzureCredential

            self._credential = DefaultAzureCredential()
        return self._credential.get_token(SEARCH_SCOPE).token

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token()}",
            "Content-Type": "application/json",
        }

    def _url(self, path: str) -> str:
        sep = "&" if "?" in path else "?"
        return f"{self._endpoint}/{path}{sep}api-version={self._api_version}"

    def create_or_update_knowledge_source(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        import requests

        name = payload["name"]
        resp = requests.put(
            self._url(f"knowledgesources/{name}"),
            headers=self._headers(),
            data=json.dumps(payload),
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json() if resp.content else {}

    def get_knowledge_source(self, name: str) -> Dict[str, Any]:
        import requests

        resp = requests.get(self._url(f"knowledgesources/{name}"), headers=self._headers(), timeout=30)
        resp.raise_for_status()
        return resp.json()

    def get_status(self, name: str) -> Dict[str, Any]:
        import requests

        resp = requests.get(
            self._url(f"knowledgesources/{name}/status"), headers=self._headers(), timeout=30
        )
        resp.raise_for_status()
        return resp.json()

    def list_knowledge_sources(self) -> List[Dict[str, Any]]:
        import requests

        resp = requests.get(
            self._url("knowledgesources?$select=name,kind"), headers=self._headers(), timeout=30
        )
        resp.raise_for_status()
        return resp.json().get("value", [])

    def delete_knowledge_source(self, name: str) -> None:
        import requests

        resp = requests.delete(
            self._url(f"knowledgesources/{name}"), headers=self._headers(), timeout=60
        )
        resp.raise_for_status()

    def create_or_update_knowledge_base(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        import requests

        name = payload["name"]
        resp = requests.put(
            self._url(f"knowledgebases/{name}"),
            headers=self._headers(),
            data=json.dumps(payload),
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json() if resp.content else {}


def _payload_from_settings(args: argparse.Namespace) -> Dict[str, Any]:
    from ..agent.config import get_settings

    s = get_settings()
    return build_indexed_sharepoint_knowledge_source(
        name=s.sharepoint_ks_name,
        connection_string=s.sharepoint_connection_string,
        aoai_endpoint=s.azure_openai_endpoint or s.foundry_project_endpoint,
        embedding_deployment=s.embedding_model_deployment,
        embedding_model=s.embedding_model_deployment,
        container_name=s.sharepoint_container_name,
        query=s.sharepoint_ks_query or None,
        description="Indexed SharePoint knowledge source (pgr-it).",
        disable_image_verbalization=not args.verbalize_images,
        vision_deployment=s.vision_model_deployment,
        vision_model=s.vision_model_deployment,
        ingestion_permission_options=(
            [o.strip() for o in s.sharepoint_ingestion_permissions.split(",") if o.strip()]
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Manage the indexed SharePoint knowledge source"
    )
    parser.add_argument("--create", action="store_true", help="create/update the knowledge source")
    parser.add_argument("--status", action="store_true", help="print ingestion status")
    parser.add_argument("--list", action="store_true", help="list knowledge sources")
    parser.add_argument("--delete", action="store_true", help="delete the knowledge source")
    parser.add_argument(
        "--create-knowledge-base",
        action="store_true",
        help="create/update the knowledge base referencing the source",
    )
    parser.add_argument(
        "--verbalize-images",
        action="store_true",
        help="enable image verbalization (requires a chat-completion deployment)",
    )
    parser.add_argument("--print-payload", action="store_true", help="print the payload and exit")
    args = parser.parse_args()

    from ..agent.config import get_settings

    s = get_settings()

    if args.print_payload:
        print(json.dumps(_payload_from_settings(args), indent=2))
        return

    client = SharePointKnowledgeSourceClient()

    if args.create:
        result = client.create_or_update_knowledge_source(_payload_from_settings(args))
        created = result.get("indexedSharePointParameters", {}).get("createdResources", {})
        print(f"Knowledge source '{s.sharepoint_ks_name}' created/updated.")
        if created:
            print("Generated objects:", json.dumps(created, indent=2))
    if args.create_knowledge_base:
        kb = build_knowledge_base(
            name=s.knowledge_base_name,
            knowledge_source_names=[s.sharepoint_ks_name],
            description="Workplace knowledge base (indexed SharePoint).",
        )
        client.create_or_update_knowledge_base(kb)
        print(f"Knowledge base '{s.knowledge_base_name}' created/updated.")
    if args.status:
        print(json.dumps(client.get_status(s.sharepoint_ks_name), indent=2))
    if args.list:
        for ks in client.list_knowledge_sources():
            print(f"  - {ks.get('name')} ({ks.get('kind')})")
    if args.delete:
        client.delete_knowledge_source(s.sharepoint_ks_name)
        print(f"Knowledge source '{s.sharepoint_ks_name}' deleted.")


if __name__ == "__main__":
    main()

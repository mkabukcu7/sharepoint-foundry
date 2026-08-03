# Configuration Reference

All runtime configuration is supplied through environment variables. Locally,
copy [`.env.example`](../.env.example) to `.env` and fill in the values. In
deployed environments the same variables are injected from Key Vault via the
managed identity — **never commit a real `.env` file**.

`scripts/deploy.ps1` / `scripts/deploy.sh` write most of these automatically
from the Bicep deployment outputs.

## Foundry (Azure AI Foundry) project

| Variable | Example | Description |
| --- | --- | --- |
| `FOUNDRY_PROJECT_ENDPOINT` | `https://<account>.services.ai.azure.com/api/projects/<project>` | Foundry project endpoint |
| `AGENT_MODEL_DEPLOYMENT` | `gpt-4o` | Primary chat/reasoning model deployment |
| `TRIAGE_MODEL_DEPLOYMENT` | `gpt-4o` | Lightweight triage/routing model (optional) |
| `VISION_MODEL_DEPLOYMENT` | `gpt-4o` | Multimodal (image) model deployment |
| `EMBEDDING_MODEL_DEPLOYMENT` | `text-embedding-3-large` | Embedding model deployment |

> The defaults in `.env.example` reference the architecture-target models
> (`gpt-4.1` family). The **verified, broadly-available** set used by the deploy
> scripts is `gpt-4o` + `text-embedding-3-large`. Align these with the model
> deployments you actually created (see `infra/main.parameters.json`).

## Azure AI Search (curated RAG lane)

| Variable | Example | Description |
| --- | --- | --- |
| `SEARCH_ENDPOINT` | `https://<name>.search.windows.net` | Search service endpoint |
| `SEARCH_INDEX_NAME` | `workplace-knowledge` | Index name (created by ingestion CLI) |
| `SEARCH_CONNECTION_ID` | *(Foundry connection id)* | Connection used by the Agent Service AI Search tool |

## Cognitive services (ingestion)

| Variable | Example | Description |
| --- | --- | --- |
| `CONTENT_SAFETY_ENDPOINT` | `https://<name>.cognitiveservices.azure.com` | Azure AI Content Safety endpoint |
| `DOC_INTELLIGENCE_ENDPOINT` | `https://<name>.cognitiveservices.azure.com` | Document Intelligence endpoint |
| `SPEECH_ENDPOINT` | `https://<region>.api.cognitive.microsoft.com` | Azure AI Speech (batch transcription) |
| `SPEECH_REGION` | `eastus2` | Speech resource region |

## Storage (ingestion staging)

| Variable | Example | Description |
| --- | --- | --- |
| `STORAGE_ACCOUNT_URL` | `https://<name>.blob.core.windows.net` | Blob endpoint for staging |
| `STORAGE_CONTAINER` | `ingestion` | Container name |

## SharePoint grounding (live lane)

| Variable | Example | Description |
| --- | --- | --- |
| `SHAREPOINT_SITE_URL` | `https://<YourEnterprise>.sharepoint.com/sites/<page>` | Grounding site |
| `SHAREPOINT_CONNECTION_ID` | *(Foundry connection id)* | Managed SharePoint tool connection (OBO identity) |

> Requires a Microsoft 365 Copilot license and same-tenant user-identity auth.
> Scope is include-only by the connection URL; there is no deny-list. See
> `docs/DEPLOYMENT.md §7` and `docs/02-architecture.md §4`.

## SharePoint (Indexed) Knowledge Source (curated lane)

Ingests SharePoint content into an Azure AI Search agentic-retrieval pipeline
(data source + skillset + indexer + index). Managed by
`src/ingestion/sharepoint_knowledge_source.py`. See
[Create a SharePoint (Indexed) Knowledge Source](https://learn.microsoft.com/azure/search/agentic-knowledge-source-how-to-sharepoint-indexed).

| Variable | Example | Description |
| --- | --- | --- |
| `AZURE_OPENAI_ENDPOINT` | `https://<account>.openai.azure.com` | Azure OpenAI / Foundry resource URI for the embedding (and optional image) model. Falls back to `FOUNDRY_PROJECT_ENDPOINT`. |
| `SHAREPOINT_CONNECTION_STRING` | `SharePointOnlineEndpoint=…;ApplicationId=…;TenantId=…` | SharePoint indexer connection string (store secret form in Key Vault) |
| `SHAREPOINT_KS_NAME` | `sharepoint-indexed-ks` | Knowledge source name (seeds generated object names) |
| `SHAREPOINT_CONTAINER_NAME` | `defaultSiteLibrary` | `defaultSiteLibrary` or `allSiteLibraries` |
| `SHAREPOINT_KS_QUERY` | `includeLibrary=…/Policies` | **Include-only folder/library scoping** (empty = whole container) |
| `SHAREPOINT_INGESTION_PERMISSIONS` | `userIds,groupIds` | ACL ingestion for query-time permission enforcement (empty = none) |
| `KNOWLEDGE_BASE_NAME` | `workplace-knowledge-base` | Knowledge base that references the knowledge source(s) |

> **Include/exclude control:** unlike the live SharePoint tool, this lane lets
> you scope ingestion to specific libraries/folders via `SHAREPOINT_KS_QUERY`
> and enforce document-level permissions via `SHAREPOINT_INGESTION_PERMISSIONS`
> (pass the user token at query time). Requires the `2026-05-01-preview` Search
> REST API and the SharePoint indexer prerequisites (Entra app registration).

## Tableau (structured-data lane)

| Variable | Example | Description |
| --- | --- | --- |
| `TABLEAU_SERVER_URL` | `https://tableau.example.com` | Tableau Server/Cloud URL |
| `TABLEAU_SITE_ID` | `analytics` | Tableau site id |
| `TABLEAU_API_VERSION` | `3.21` | REST API version |
| `TABLEAU_PAT_NAME` | `chatbot-pat` | Personal Access Token name (store secret in Key Vault) |
| `TABLEAU_PAT_SECRET` | *(secret)* | PAT secret — Key Vault reference in deployed environments |
| `TABLEAU_DATASOURCE_LUID` | *(GUID)* | Published datasource LUID queried via the VizQL Data Service (required for the Tableau lane) |
| `TABLEAU_MEASURE_FIELD` | `Sales` | Measure field aggregated for the metric (defaults to the requested metric name) |
| `TABLEAU_DATE_FIELD` | `Order Date` | Date dimension used for time-grain filtering |
| `TABLEAU_SOURCE_WORKBOOK` | `RevenueWB` | Source workbook name surfaced in citations (defaults to the datasource LUID) |

## Behavior / thresholds

These map to `docs/02-architecture.md §9 & §15` and the evaluation gates.

| Variable | Default | Description |
| --- | --- | --- |
| `GROUNDEDNESS_MIN` | `4.2` | Minimum groundedness score to answer |
| `RELEVANCE_MIN` | `4.0` | Minimum relevance score |
| `RETRIEVAL_TOP_K` | `8` | Candidates retrieved before rerank |
| `RERANK_TOP_K` | `4` | Candidates kept after rerank |
| `LOW_CONFIDENCE_MIN_SCORE` | `0.30` | Below this, return a graceful low-confidence fallback |
| `MAX_EVIDENCE_TOKENS` | `6000` | Token budget for retrieved evidence |

## Observability

| Variable | Example | Description |
| --- | --- | --- |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | `InstrumentationKey=…` | App Insights connection for tracing/logging |
| `KEYVAULT_URI` | `https://<name>.vault.azure.net` | Key Vault used for secret references |

## Authentication model

The solution uses `DefaultAzureCredential` everywhere:

- **Local dev:** `az login` (or a managed identity when running on Azure).
- **Deployed:** the user-assigned managed identity provisioned by `infra/`.

All data-plane services deploy with local authentication disabled, so **no keys
or connection strings with embedded secrets are used** — access is via Entra
RBAC only.

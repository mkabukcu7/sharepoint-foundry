# Infrastructure (Bicep)

Reference landing zone for the Multimodal Workplace Chatbot, implementing
`docs/02-architecture.md`. Private-by-default; managed-identity / RBAC only
(local auth keys disabled on Foundry, Search, and Storage).

## Resources

| Module | Resource | Purpose |
| --- | --- | --- |
| `ai-foundry.bicep` | AI Services account + Foundry project + model deployments | Agent runtime; `gpt-4o` (chat/vision) + `text-embedding-3-large` (see `main.parameters.json`) |
| `search.bicep` | Azure AI Search (semantic + vector) | Curated RAG index `workplace-knowledge` |
| `storage.bicep` | Storage account + `ingest` container | Raw multimodal source landing zone |
| `keyvault.bicep` | Key Vault (RBAC, purge protection) | Secrets (e.g. Tableau PAT, SharePoint connection) |
| `monitoring.bicep` | Log Analytics + Application Insights | Tracing, logging, evaluation telemetry |
| `identity.bicep` | User-assigned managed identity | App workload identity for all data-plane access |

## Deploy

The simplest path is the repo-root script, which resolves your object id
safely and writes `.env` from the outputs:

```powershell
./scripts/deploy.ps1 -ResourceGroup rg-pgr-chatbot -Location eastus2
```

Equivalent manual command:

```powershell
az group create -n rg-pgr-chatbot -l eastus2
az deployment group create `
  -g rg-pgr-chatbot `
  -f infra/main.bicep `
  -p infra/main.parameters.json `
  -p deployerPrincipalId=<your-object-id>
```

> Getting your object id via `az ad signed-in-user show` can fail with a
> Conditional-Access (CAE) challenge. The deploy scripts read the `oid` claim
> from an ARM access token instead — see
> [`docs/DEPLOYMENT.md`](../docs/DEPLOYMENT.md) §5 & §9.

### Parameters

See [`docs/DEPLOYMENT.md §5`](../docs/DEPLOYMENT.md) for the full parameter
table (`baseName`, `location`, `searchLocation`, `searchSku`,
`privateByDefault`, `deployerPrincipalId`, `modelDeployments`) and the model
quota / Search capacity pre-checks.

> **Production note:** This template provisions service resources and RBAC but
> intentionally omits the hub-spoke network, private endpoints, Private DNS
> zones, and Azure Policy described in architecture §13.2 / §15. Layer those on
> via your enterprise landing zone before any production rollout. Set
> `privateByDefault=false` only for an isolated dev sandbox.

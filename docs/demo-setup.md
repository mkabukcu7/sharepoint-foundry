# Demo setup and handoff runbook

Everything needed to stand this up from scratch and reproduce the demo:
Azure resources, permissions, configuration, and the run order.

There are two tiers. **Tier 1 runs with no Azure access at all** and is enough to
show classification, review, approval and the catalog. **Tier 2** adds live
Foundry classification, SharePoint write-back, and grounded chat answers.

Start with Tier 1 and confirm it works before provisioning anything.

---

## Tier 1 — offline demo (no Azure, no cost)

Requires only Python 3.12 or later.

```powershell
git clone <repository-url>
cd sharepoint-foundry
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r backend\requirements-dev.txt

python -m backend.scripts.reset_demo
python -m backend.scripts.demo_preflight
python -m pytest
python -m uvicorn backend.app.main:app --reload --port 8000
```

Expected, and verified on a clean clone:

| Step | Expected output |
|---|---|
| `reset_demo` | `Reset 10 documents; 1 approved baseline record(s)` |
| `demo_preflight` | `Demo preflight passed: 10 documents, 1 approved metadata record(s), 10 taxonomy review item(s)` and `AI_PROVIDER=mock` |
| `pytest` | `203 passed` |
| `GET /api/documents` | HTTP 200, 10 documents |

With no `.env` present the app defaults to `AI_PROVIDER=mock`, which produces
deterministic metadata locally. Nothing calls Azure.

The chat service is separate and runs on its own port:

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.app.librarian_chat:app --host 127.0.0.1 --port 8010
```

Chat needs Tier 2 to answer from documents, because retrieval requires the
search index and embeddings.

---

## Tier 2 — Azure resources required

### Resource summary

| Resource | Type / SKU | Purpose | Notes |
|---|---|---|---|
| Resource group | — | Holds everything below | |
| Azure AI Search | `Microsoft.Search/searchServices`, **basic** | Stores approved-content chunks and serves grounded answers | **Semantic ranker must be enabled** — see below |
| Azure AI Foundry (AI Services) | `Microsoft.CognitiveServices/accounts`, kind `AIServices`, **S0** | Hosts the prompt agents and the embedding model | |
| Entra app registration | — | SharePoint access via Microsoft Graph | Only needed for SharePoint ingestion/write-back |
| SharePoint Online site | — | Document library with `Staging` / `Reviewed` / archive folders | Existing tenant site |

The reference environment is:

- Search: `basic`, 1 replica, 1 partition, semantic tier `free`, `disableLocalAuth: true`, **East US**
- AI Services: `S0`, **East US 2**

The two do not have to share a region, and in the reference environment they do
not.

### Model deployments

Deploy both on the AI Services resource:

| Deployment | Model | Why |
|---|---|---|
| `gpt-5-mini` | `gpt-5-mini` (2025-08-07) | Classification, extraction and librarian reasoning |
| `text-embedding-3-small` | `text-embedding-3-small` v1 | Vector embeddings for retrieval |

`text-embedding-3-small` produces **1536 dimensions**, which must match
`SEARCH_VECTOR_DIMENSIONS`. If you deploy a different embedding model, change
both together — a mismatch is rejected at query time rather than silently
returning bad results.

### Semantic ranker is not optional

The librarian decides whether it has enough evidence from the **semantic
reranker score**. Hybrid search scores sit in a narrow band whether or not a
passage is relevant, so they cannot support abstention.

If semantic ranking is off, the app raises rather than answering from unranked
evidence. Enable the semantic ranker on the Search service; the `free` tier is
sufficient for demo volumes.

### Provisioning

```powershell
az deployment sub create `
  --location <region> `
  --template-file infra\main.bicep `
  --parameters infra\main.parameters.json `
  --parameters applicationPrincipalId=<object-id>
```

The resource group must already exist — the template references it as
`existing`. `infra\main.parameters.json` carries reference-environment names;
**change them for a new deployment**, since the Search service name must be
globally unique.

`applicationPrincipalId` is intentionally not present in the checked-in
parameters file. It is a required deployment parameter, and the service is
deployed with `disableLocalAuth: true` (no API keys). **Deploying without the
application principal now fails before provisioning instead of producing a
Search service that nothing can authenticate to.** Pass the object ID as shown
above or assign the roles manually.

---

## Permissions

### Azure RBAC — on the Search service

Both roles are required, on the identity the app runs as:

| Role | Role definition ID | Why |
|---|---|---|
| Search Index Data Contributor | `8ebe5a00-799e-43f5-93ac-243d3dce84a7` | Read and write chunk documents |
| Search Service Contributor | `7ca78c08-252a-4471-8644-bb5ff32d4ba0` | The app calls `create_or_update_index` at startup. This is schema management, not a document operation — Data Contributor alone returns **403** here |

Assign manually if you did not pass `applicationPrincipalId`:

```powershell
$scope = az search service show -n <search-name> -g <rg> --query id -o tsv
az role assignment create --assignee <principal> --role "Search Index Data Contributor" --scope $scope
az role assignment create --assignee <principal> --role "Search Service Contributor" --scope $scope
```

### Azure RBAC — on the AI Services resource

The identity needs **Cognitive Services User** (or broader) to call the agents
and the embedding endpoint. In the reference environment this is inherited from
a subscription-level Owner assignment rather than granted explicitly, so a
least-privilege deployment must add it deliberately.

### Microsoft Graph — SharePoint

Only needed for SharePoint ingestion and write-back.

The validated least-privilege configuration is Graph **application** permission
`Sites.Selected`, plus a `write` grant on the target site only.

Granting that site-level permission requires an administrator holding
`Sites.FullControl.All`. The connector **cannot** elevate its own permission.

`SHAREPOINT_CREDENTIAL_MODE=azure_cli` works only if the CLI token carries the
required delegated `Sites.*` / `Files.*` scopes. A normal `az login` does not
necessarily include them, so use `default` with the app registration's client
credentials for anything reproducible.

### Identity model — read this before handing over

The reference environment authenticates as a **signed-in user**
(`FOUNDRY_CREDENTIAL_MODE=azure_cli`), and the Search roles above are assigned
to that *user*, not to a service principal.

Consequences for whoever receives this:

- Everything stops working the moment that `az login` session expires, and it
  fails in several places at once.
- The roles are attached to an individual account. A new owner must assign the
  same roles to their own identity, or to a service principal, before anything
  works.

For any shared or repeatable environment, create a service principal, assign the
roles to it, and set `FOUNDRY_CREDENTIAL_MODE=default`.

---

## Configuration

Copy `.env.example` to `.env` and fill it in. `.env` is gitignored and must
never be committed — it holds a client secret in the reference environment.

Minimum for **Tier 2 chat with grounded answers**:

```dotenv
AI_PROVIDER=foundry_wtw
FOUNDRY_PROJECT_ENDPOINT=https://<resource>.services.ai.azure.com/api/projects/<project>
FOUNDRY_CLASSIFIER_AGENT_NAME=wtw-metadata-classifier
FOUNDRY_CLASSIFIER_AGENT_VERSION=2
FOUNDRY_LIBRARIAN_AGENT_NAME=knowledge-librarian-agent
FOUNDRY_LIBRARIAN_AGENT_VERSION=2
FOUNDRY_EXTRACTION_MODEL=gpt-5-mini
FOUNDRY_EMBEDDING_MODEL=text-embedding-3-small
FOUNDRY_TAXONOMY_PATH=taxonomy/controlled-terms.json

SEARCH_ENDPOINT=https://<search-name>.search.windows.net
SEARCH_INDEX_NAME=wtw-approved-knowledge
SEARCH_VECTOR_DIMENSIONS=1536
SEARCH_USE_SEMANTIC_RANKER=true
SEARCH_MIN_RERANKER_SCORE=1.9
```

Two settings that are easy to get wrong:

- **`FOUNDRY_TAXONOMY_PATH` is a relative path.** It resolves against the current
  working directory, so running from anywhere other than the repository root
  loads nothing — and taxonomy validation is skipped rather than failing loudly.
  Always start the app from the repository root.
- **`SEARCH_MIN_RERANKER_SCORE` controls abstention.** Lowering it toward `0`
  makes the librarian answer from weak evidence. Leave it at `1.9` unless you
  are deliberately tuning.

### Three separate Foundry agents

These are distinct prompt agents and must not be collapsed into one:

| Agent | Role |
|---|---|
| `wtw-metadata-classifier` | Controlled-taxonomy classification |
| `sharepoint-foundry-agent` | General metadata extraction |
| `knowledge-librarian-agent` | Chat, triage and grounded answers |

### Customer taxonomy

Place the taxonomy source at
`taxonomy\WTW_Intranet_Taxonomy_Reference.docx`, then:

```powershell
python -m backend.scripts.build_taxonomy
```

The whole `taxonomy\` directory is gitignored. Neither the source document nor
the generated terms may be committed. The **test suite does not need it** — it
uses a synthetic fixture instead.

---

## Reproducing the full demo

1. **Prepare.** Run from the repository root, with `az login` current.

   ```powershell
   python -m backend.scripts.reset_demo
   python -m backend.scripts.demo_preflight
   python -m backend.scripts.demo_foundry_check
   ```

   `demo_foundry_check` makes one live classification call and is the fastest way
   to prove credentials and agent configuration are good.

2. **Start both services.** They are separate processes on separate ports.

   ```powershell
   python -m uvicorn backend.app.main:app --port 8000
   python -m uvicorn backend.app.librarian_chat:app --host 127.0.0.1 --port 8010
   ```

   Run each so it survives the session that launched it. Detached background
   processes started from a terminal that later closes will die with it.

3. **Verify before presenting.**

   ```powershell
   curl http://localhost:8000/api/documents
   curl http://127.0.0.1:8010/api/health
   ```

4. **Demo flow.** Catalog and filtering → review queue with evidence → correct a
   tag and approve as a named reviewer → show the document become answerable in
   chat with citations → ask something unsupported and show it abstain.

Approval indexes the document immediately, so the newly approved document is
answerable in chat without a reindex step. Revoking approval removes it just as
immediately.

### Reference environment state

At last verification: 175 chunk documents in `wtw-approved-knowledge`; 12 files
in `sample-documents` locally, of which 10 are tracked.

The two untracked files are customer material and do not travel with the
repository. A fresh clone therefore has a **smaller answerable corpus** than the
machine the demo was rehearsed on. Approve at least one document after cloning
so chat has something to cite.

---

## Chat service is not authenticated

`backend.app.librarian_chat` binds to loopback and refuses non-local callers.
Its health endpoint reports `callerAuthentication: none`, which is accurate:
there is **no per-user authorization**, and it does not filter answers by the
asking user's SharePoint permissions.

It is safe as a single-operator local demo. Do not expose it to a network or put
it in front of real users without adding authentication and per-user
trimming. `LIBRARIAN_CHAT_ALLOW_REMOTE_UNAUTHENTICATED` removes the loopback
guard and should stay unset.

---

## Findings resolved in this branch

| Issue | Impact |
|---|---|
| Empty `applicationPrincipalId` and incorrect example region | The principal is now required before deployment, and the example uses `eastus` |
| Taxonomy loading could be acknowledged as resolved | Approval is blocked until the taxonomy is restored; the unavailable-taxonomy flag cannot be acknowledged as if validation had happened |
| Failed stale-chunk deletion lost the recorded chunk count | Failed index operations preserve a high-water chunk count, so retries continue deleting the potentially citable ids |

These fixes are covered by focused tests. The deployment template still requires
an operator to replace the explicit placeholder values in
`infra\main.parameters.json` and pass the application principal object ID.

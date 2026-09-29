# Knowledge Metadata Agent MVP

Proof-of-concept repository showing AI-powered metadata extraction and tagging for an enterprise document library.

The MVP ingests PDF, Word, and PowerPoint documents from local samples or SharePoint, extracts their text, uses a Microsoft Foundry prompt agent to generate metadata, and presents a filterable catalog.

## What this demonstrates

- Automated text extraction from representative enterprise documents
- AI-generated summaries, themes, tags, language, author, sentiment, business area, audience, and metadata category
- Filtering by themes, tags, language, author, and sentiment
- Grouping by category, business area, language, author, or sentiment
- Knowledge-area rollups for review status, recorded approval, and review recency
- Evidence-backed metadata review with accept, edit, reject, and approval decisions
- Country-of-origin extraction with filtering and grouping
- Optional free-form metadata extraction during batch upload
- Configurable catalog column for standard or free-form metadata fields
- Controlled WTW taxonomy classification with confidence, evidence, and risk flags
- Optional human-approved metadata write-back to SharePoint
- Direct links from metadata records to their source demo documents
- Pluggable Microsoft Foundry and deterministic mock providers

## Repository structure

```text
knowledge-agent-mvp/
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── models/
│   │   └── services/
│   ├── requirements.txt
│   └── scripts/
├── frontend/
│   └── static-demo.html
├── sample-documents/
├── data/
│   └── demo-metadata.json
└── docs/
	└── architecture.md
```

## Quick start

The bundled dashboard requires only Python 3.12 or later.

From the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r backend\requirements.txt
python -m backend.scripts.reset_demo
python -m backend.scripts.demo_preflight
python -m uvicorn backend.app.main:app --reload --port 8000
```

Open:

- API: http://localhost:8000/api/documents
- Metadata catalog: http://localhost:8000/

The catalog UI is `frontend\static-demo.html` and is served directly by FastAPI. No separate frontend toolchain is required.

`data\demo-metadata.json` is a sanitized, tracked baseline. `Demo: Reset` copies that baseline into the generated local runtime file `data\extracted-metadata.json`. The runtime file is ignored by Git because SharePoint ingestion adds tenant-specific site IDs, item IDs, URLs, ETags, and identity payloads.

To regenerate local metadata from the bundled sample files without cloud access:

```powershell
$env:AI_PROVIDER='mock'
python -m backend.scripts.ingest
python -m backend.scripts.reset_demo
```

To regenerate controlled-taxonomy metadata, configure `AI_PROVIDER=foundry_wtw` and the Foundry settings below before running the same ingestion command. Review the generated runtime file locally; never force-add it to Git.

Maintainers can refresh the tracked public template from a reviewed runtime file:

```powershell
python -m backend.scripts.sanitize_demo_metadata
```

That command removes SharePoint identities and workflow state while writing `data\demo-metadata.json`. It does not modify the local runtime file.

The repository also includes visible VS Code tasks:

- `Demo: Reset` restores one approved metadata example and nine pending examples.
- `Demo: Preflight` validates metadata, source files, provider settings, and optional write-back settings.
- `Demo: Test` runs the complete unit test suite.
- `Demo: Run` starts the demo using the provider configured in `.env`.
- `Demo: Check live Foundry` performs one controlled-taxonomy classification call.
- `Demo: Run offline fallback` starts deterministic mock mode only when live Foundry is unavailable.

## Customer taxonomy data

Place the customer-provided taxonomy source at `taxonomy\WTW_Intranet_Taxonomy_Reference.docx` locally before running `python -m backend.scripts.build_taxonomy`. The entire `taxonomy\` directory is ignored by Git; neither the source document nor generated controlled terms should be committed or uploaded to the repository.

## Microsoft Foundry

Create a local `.env` file with the Foundry project and prompt-agent reference:

```dotenv
AI_PROVIDER=foundry
FOUNDRY_PROJECT_ENDPOINT=https://<resource>.services.ai.azure.com/api/projects/<project>
FOUNDRY_AGENT_NAME=<agent-name>
FOUNDRY_AGENT_VERSION=<version>
FOUNDRY_EXTRACTION_MODEL=gpt-5-mini
```

Authenticate locally with Azure CLI. The application uses `DefaultAzureCredential` and does not store Azure credentials. Set `AI_PROVIDER=mock` to run with deterministic local metadata generation instead.

For controlled WTW taxonomy classification, configure the classifier agent and local taxonomy:

```dotenv
AI_PROVIDER=foundry_wtw
FOUNDRY_PROJECT_ENDPOINT=https://<resource>.services.ai.azure.com/api/projects/<project>
FOUNDRY_CLASSIFIER_AGENT_NAME=<classifier-agent-name>
FOUNDRY_CLASSIFIER_AGENT_VERSION=<version>
FOUNDRY_TAXONOMY_PATH=taxonomy/controlled-terms.json
FOUNDRY_EXTRACTION_MODEL=gpt-5-mini
```

The Knowledge Librarian is a separate prompt agent. It does not replace the
metadata or WTW classifier agents. It can retrieve a candidate Reviewed-file
version history when invoked with a document name; matching filenames are
presented as candidates and are not treated as proof of document identity:

```dotenv
FOUNDRY_PROJECT_ENDPOINT=https://<resource>.services.ai.azure.com/api/projects/<project>
FOUNDRY_LIBRARIAN_AGENT_NAME=knowledge-librarian-agent
FOUNDRY_LIBRARIAN_AGENT_VERSION=2
FOUNDRY_LIBRARIAN_MODEL=gpt-5-mini
FOUNDRY_LIBRARIAN_PROMPT_PATH=prompts/knowledge-librarian-agent.md
```

The librarian can only perform actions exposed by configured tools and should
abstain when an authorized, approved source or required configuration is not
available.

### Local librarian chat

Run the standalone local chat service on port `8010`, separately from the
metadata app:

```powershell
.venv\Scripts\python.exe -m uvicorn backend.app.librarian_chat:app --host 127.0.0.1 --port 8010
```

Open `http://127.0.0.1:8010`. The service uses the librarian agent configured
above and keeps conversation turns in process memory for up to 30 minutes.

#### The chat has no caller authentication

The service reads the approved library with the application identity and trusts
the reviewer name a caller supplies. Anyone who can reach it can therefore read
every approved document regardless of their own SharePoint rights, and can record
an approval under another person's name. Delegated on-behalf-of authentication
with permission trimming is the fix; it is not implemented yet.

Until it is, the service refuses any request that does not come from loopback and
reports `"callerAuthentication": "none"` on `/api/health`. Do not remove that
guard to make a demo reachable. If you genuinely need a remote caller in an
isolated environment, accept the risk explicitly:

```powershell
$env:LIBRARIAN_CHAT_ALLOW_REMOTE_UNAUTHENTICATED = "true"
```

#### Read capabilities

Every turn is grounded with live SharePoint data read through Microsoft Graph:

- The contents of the configured `Staging` and `Reviewed` folders.
- For any document named in the message: its approved metadata columns, web URL,
  last-modified timestamp, and SharePoint version history.

Retrieved data is wrapped in `trust="untrusted-data"` delimiters so document text
is treated as information, never as instructions. When `SHAREPOINT_HOSTNAME` is
not configured the chat reports the limitation instead of guessing.

#### Answering from approved content only

When a turn looks like a knowledge question rather than a maintenance
instruction, the chat queries the Azure AI Search index of approved documents and
grounds the answer in the retrieved excerpts. The index filter is
`approved eq true and reviewStatus eq 'approved'`, so unreviewed or rejected
material can never be cited.

Answers carry citations (document name, section, SharePoint version) which are
rendered beneath the reply and returned in the `citations` field of the API
response.

The librarian abstains rather than guessing. Weak hits are discarded using the
semantic reranker score, and when nothing clears `SEARCH_MIN_RERANKER_SCORE`
(default `1.9`) the librarian is instructed to say it found no approved evidence
and is forbidden from answering from file names, folder listings, or its own
knowledge.

This requires semantic ranking. Hybrid search scores barely move between a
relevant and an irrelevant chunk, so they cannot drive abstention; the reranker's
0-4 score can. `SEARCH_USE_SEMANTIC_RANKER` is therefore on by default, and the
Bicep template provisions the service with semantic search enabled. For an
existing service:

```powershell
az search service update --name <search-service> --resource-group <rg> --semantic-search free
```

If a hit arrives without a reranker score, relevance is unknown rather than
acceptable, so the librarian treats it as no evidence instead of answering from
something nobody has judged. A score of `0.0` is a real score, not a missing one,
and is treated as weak evidence -- the librarian abstains quietly rather than
reporting a search outage. If search is not configured or fails, the chat still
responds, reports that approved-content search is unavailable, and declines to
answer the question from other sources.

Triage requests and metadata change requests skip retrieval, so the maintenance
conversation is unaffected.

#### Write capability (approval gated)

The model never executes a change. When a message clearly requests a metadata
change to a known document, the app builds a change plan from the live SharePoint
state and returns it to the UI:

1. The plan lists each editable field with its current and proposed value and
   captures the item ETag.
2. A reviewer types their name and clicks **Approve**.
3. The app re-reads the item, refuses with HTTP 409 if the ETag changed, applies
   the update with `If-Match`, verifies the written values, and reports the
   version SharePoint assigned.

Editable fields in the chat are limited to business area, audience, language,
author and country of origin, mapped through `SHAREPOINT_COLUMN_MAP`. Taxonomy
fields are deliberately excluded from the chat write path; they are edited in
the reviewer panel, where selections are constrained to the controlled
vocabulary. Writes require
`SHAREPOINT_WRITEBACK_ENABLED=true`. Permissions, sharing links, sensitivity
labels, retention and records settings are never changed, and documents are
never deleted or overwritten by this chat.

#### Guardrails

Question side (applied before the agent is called):

- Message length cap and a per-session rate limit (20 messages per 5 minutes).
- Refusal of prompt-injection attempts, requests for credentials, keys or
  tokens, and requests to change permissions/labels/retention or delete content.

Content side (applied before the reply is shown):

- Secret-shaped values are redacted from both retrieved data and replies.
- Replies that would disclose the agent's internal instructions are replaced.
- Claims that a change was made are annotated when nothing was executed.

Permissions are not yet trimmed to the signed-in user, so run this against a
library whose audience matches everyone who can reach the local port.

#### Intake triage (the librarian's standing job)

`GET /api/librarian/triage`, or the **Run intake triage** button in the chat,
runs a read-only pass over Staging. It reconciles the live Staging folder with
the stored classification records and, for each item, checks:

- **Summary** — present, and between 80 and 1200 characters.
- **Tags** — every proposed value exists in the controlled taxonomy, and the
  required fields (`materialType`, `topics`) are populated. Invented terms are
  reported rather than accepted.
- **Confidence** — every scored tag is compared against
  `LIBRARIAN_CONFIDENCE_THRESHOLD` (default `0.7`).
- **Review state** — unresolved review fields, plus how long the item has been
  waiting against `LIBRARIAN_REVIEW_SLA_DAYS` (default `5`).
- **Risk** — classifier `riskFlags` and `reviewRequired`.

Each item is returned with `disposition` (`ready-for-approval` or
`needs-attention`), an explicit `blockers` list, and a `priority` used to rank
the worklist so overdue and risky items surface first. Files sitting in Staging
that have never been classified appear with the blocker "This file is in Staging
but has not been classified yet"; records whose file has left Staging are
flagged as drift.

The job never writes. `ready-for-approval` only means the item is safe to put in
front of a reviewer — publishing, archiving and staged-source removal still run
through the existing approval gate.

#### Publish, archive and remove

On approval, `writeback.apply()` performs the lifecycle:

1. Apply approved metadata columns with `If-Match`.
2. For a new document, move it Staging → `Reviewed`. For an approved revision,
   replace the existing file's content in place.
3. Read version history and verify SharePoint recorded a new version.
4. For a revision, move the staged original into
   `SHAREPOINT_ARCHIVE_FOLDER_NAME` (default `Archive`), renamed to
   `<name> (superseded <timestamp>)<ext>`, which removes it from Staging while
   keeping it recoverable.

If archiving conflicts, the staged original is retained in place and reported
via `stageSourceRetained` rather than being lost.

## Approval, attribution and the search index

Approval requires a named reviewer. `POST /api/documents/{name}/review/approve`
takes `{"reviewer": "<name>"}` and rejects a missing or blank name, so an
approval can never be recorded anonymously. The name is stored as `reviewedBy`
alongside `reviewedAt`. A writeback retry reuses the recorded approver rather
than attributing the change to whoever triggered the retry.

Approving a document also indexes it immediately, so it becomes answerable in the
librarian chat without a separate reindex. The approval response carries a
`searchIndex` field reporting the outcome; if search is unconfigured or indexing
fails, the approval still stands and the reason is reported.

Index membership is maintained in both directions:

- Losing approval **removes** the document's chunks, at the moment approval is
  revoked rather than at the next reindex. Editing a reviewed field or resolving
  a classification flag both return a document to `needs-review`, and each
  withdraws it from the answer index straight away.
- Re-indexing a new version **replaces** the previous one. Chunk ids are keyed on
  document and position rather than version, so versions overwrite in place
  instead of accumulating.
- Revoking approval **while indexing is in flight** wins. Indexing embeds before
  it uploads, so a revocation can complete in between; the upload would otherwise
  restore citable chunks afterwards. Indexing captures an approval token first
  and discards its own write if approval changed meanwhile.

Deletions are computed from known chunk counts rather than discovered by
querying, because Azure AI Search indexes asynchronously and a just-written
document is not immediately searchable. Those known ids are deleted first, and a
query sweep runs afterwards only to reconcile leftovers, so a failing sweep can
never leave revoked content in the index.

If you change the chunk-id scheme, purge the index and rebuild — chunks written
under the old scheme can no longer be addressed and would linger as orphans.

## SharePoint ingestion

Configure the SharePoint source with non-secret settings:

```dotenv
SHAREPOINT_HOSTNAME=<tenant>.sharepoint.com
SHAREPOINT_SITE_PATH=/
SHAREPOINT_LIBRARY_NAME=Documents
SHAREPOINT_FOLDER_PATH=
SHAREPOINT_CREDENTIAL_MODE=default
SHAREPOINT_WRITEBACK_ENABLED=true
SHAREPOINT_STAGING_FOLDER_NAME=Staging
SHAREPOINT_REVIEWED_FOLDER_NAME=Reviewed
SHAREPOINT_COLUMN_MAP={"businessArea":"BusinessArea","audience":"MetadataAudience","language":"MetadataLanguage","author":"MetadataAuthor","countryOfOrigin":"CountryofOrigin"}
```

Sign in to the SharePoint tenant through Azure CLI using the managed-environment account. Add `--allow-no-subscriptions` when the account has Microsoft 365 access but no Azure subscription:

```powershell
az login --tenant <tenant-id> --allow-no-subscriptions
python -m backend.scripts.sync_sharepoint
```

The sync command obtains a Microsoft Graph token through `DefaultAzureCredential`, recursively downloads supported files from the selected library into `sample-documents`, and sends only those files through the existing Foundry metadata pipeline. It excludes the configured `Staging` and `Reviewed` workflow folders so completed files are not re-ingested.

The generated `data\extracted-metadata.json` contains live SharePoint identities and is intentionally ignored. Create it locally by running either `python -m backend.scripts.reset_demo`, `python -m backend.scripts.ingest`, or `python -m backend.scripts.sync_sharepoint`.

Create text columns in the document library for the metadata you want to write. `SHAREPOINT_COLUMN_MAP` maps application field names to the columns' **internal SharePoint names**, which can differ from their display names. Avoid mapping `author` to SharePoint's built-in Author lookup column; use a dedicated text column such as `MetadataAuthor`.

Alongside the five free-text fields, the seven controlled-taxonomy fields can be
mapped and written: `materialType`, `topics`, `businesses`, `industries`,
`geographies`, `collections` and `languages`. Multi-value selections are written
to plain text columns as a semicolon-separated list, for example
`Automotive; Charities and Nonprofits`, so no multi-choice column type is
required.

#### Taxonomy review and approval

Classification and publication cover the same fields. The reviewer panel shows
every taxonomy field as an editable selection restricted to the active
controlled vocabulary, so a reviewer can correct the classifier but cannot
invent a term — an unknown value is rejected with `Unknown <category> term`.

Approval is blocked until both of the following are true:

1. Every field, free-text and taxonomy, has been accepted or edited.
2. Every classification flag is resolved.

Flags come in two kinds. Acknowledgement flags — `reviewRequired` and each
classifier `riskFlag` — must be explicitly resolved by a named reviewer through
`POST /api/documents/{name}/review/flags/resolve`, optionally with a note.
Derived flags — a term outside the taxonomy, or an empty required field
(`materialType`, `topics`) — cannot be acknowledged away and clear themselves
once the underlying field is corrected.

On approval the accepted taxonomy is written to the mapped SharePoint columns
and also recorded on the document as `approvedTaxonomy`, leaving the original
classifier output in `wtwClassification` intact for accuracy measurement.

Write-back is disabled unless `SHAREPOINT_WRITEBACK_ENABLED=true`. When enabled:

1. The presenter must explicitly select `Stage this upload in SharePoint`; the checkbox is off by default.
2. Only selected uploads are processed locally and uploaded to the configured `Staging` folder.
3. Approval writes accepted or edited values to the mapped SharePoint columns.
   Multi-value taxonomy selections are joined with `; `.
4. The same drive item is moved into `Reviewed`; the app does not create a duplicate.
5. The UI records the destination, SharePoint URL, and actual version returned by SharePoint.

When a staged upload may revise an existing Reviewed file, the UI fetches that
file's SharePoint version history. A reviewer must explicitly confirm the
revision action; a filename match alone is never sufficient. After approval,
the app rechecks both items' ETags, replaces the existing file's content in
place, writes approved metadata, and verifies that SharePoint reported a new
version. The staging source is deleted only after version verification. A
stale target stops for refreshed review; a staging cleanup conflict is
reported and leaves the source in place.

Existing catalog records and uploads made without the checkbox can never enter the write-back path, even when the global capability is enabled.

The **SharePoint connectors** panel provides the inverse workflow for files already placed in `Staging`:

1. Select **Refresh Staging** to list supported PDF, DOCX, and PPTX files.
2. Select only the files intended for the demonstration.
3. Select **Import selected**. The app downloads those files for analysis and preserves each existing SharePoint drive-item identity.
4. Review and approve metadata in the catalog. Approval updates the same SharePoint item and moves it from `Staging` to `Reviewed`.

Files already present in the local catalog are shown but cannot be selected. The server re-resolves every selected filename inside configured `Staging`; the browser cannot submit an arbitrary drive, item, or folder.

The folders are created beneath `SHAREPOINT_FOLDER_PATH`, or at the library root when that setting is empty. Existing folders are reused case-insensitively. A duplicate file name in `Staging` is rejected rather than overwritten.

The connector identity needs Microsoft Graph read/write access to the target site. The validated least-privilege configuration uses Graph application permission `Sites.Selected` plus a `write` grant on the demo site only. An administrator identity with Graph application permission `Sites.FullControl.All` is required to create or change that site grant; the connector cannot elevate its own permission.

Use `SHAREPOINT_CREDENTIAL_MODE=default` for the connector's service principal or managed identity. `azure_cli` mode works only when the Azure CLI client token has the required Graph `Sites.*` or `Files.*` delegated scopes; a normal Azure CLI sign-in does not necessarily include them.

## Demo rehearsal

Run these commands before presenting:

```powershell
python -m backend.scripts.reset_demo
python -m backend.scripts.demo_preflight
python -m unittest discover -s tests -v
python -m uvicorn backend.app.main:app --reload --port 8000
```

Use `AI_PROVIDER=mock` for a deterministic offline presentation. Use `AI_PROVIDER=foundry_wtw` only when demonstrating live ingestion, and verify Azure CLI authentication before the session. Enable SharePoint write-back only for a planned live write-back segment.

## Demo storyline

1. Add representative documents to `sample-documents`.
2. Start with the approved baseline record, then contrast it with records needing metadata review.
3. Inspect the WTW controlled classification, confidence, evidence, review requirement, and risk flags.

The tracked 10-document corpus is the single source for the demo and automated tests. Recreate it deterministically with:

```powershell
python -m backend.scripts.seed_sample_documents
```
4. Optionally upload a document to run live extraction through Foundry.
5. Optionally name a custom property and describe what the model should extract during upload.
6. Review country, recency, review status, and recorded approval by knowledge area.
7. Open the review queue and inspect grounded, inferred, or missing support for key metadata fields.
8. Accept or edit each field, then approve the completed metadata record.
9. Upload a new file and show it appear in SharePoint `Staging`.
10. If planned and enabled, approve it, show the mapped metadata columns, and show the same file moved to `Reviewed`.
11. Filter or group the catalog to demonstrate metadata-driven discovery.
12. Open a document detail view and follow its source-document or SharePoint link.

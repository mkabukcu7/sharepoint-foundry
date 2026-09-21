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

Write-back is disabled unless `SHAREPOINT_WRITEBACK_ENABLED=true`. When enabled:

1. The presenter must explicitly select `Stage this upload in SharePoint`; the checkbox is off by default.
2. Only selected uploads are processed locally and uploaded to the configured `Staging` folder.
3. Approval writes accepted or edited values to the mapped SharePoint columns.
4. The same drive item is moved into `Reviewed`; the app does not create a duplicate.
5. The UI records the destination and SharePoint URL. Metadata or move failures remain visible and retryable.

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

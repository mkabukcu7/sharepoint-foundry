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
│   └── extracted-metadata.json
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
python -m backend.scripts.seed_sample_documents
python -m backend.scripts.ingest
python -m uvicorn backend.app.main:app --reload --port 8000
```

Open:

- API: http://localhost:8000/api/documents
- Metadata catalog: http://localhost:8000/

The catalog UI is `frontend\static-demo.html` and is served directly by FastAPI. No separate frontend toolchain is required.

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

## SharePoint ingestion

Configure the SharePoint source with non-secret settings:

```dotenv
SHAREPOINT_HOSTNAME=<tenant>.sharepoint.com
SHAREPOINT_SITE_PATH=/
SHAREPOINT_LIBRARY_NAME=Documents
```

Sign in to the SharePoint tenant through Azure CLI using the managed-environment account. Add `--allow-no-subscriptions` when the account has Microsoft 365 access but no Azure subscription:

```powershell
az login --tenant <tenant-id> --allow-no-subscriptions
python -m backend.scripts.sync_sharepoint
```

The sync command obtains a Microsoft Graph token through `DefaultAzureCredential`, recursively downloads supported files from the selected library into `sample-documents`, and sends only those files through the existing Foundry metadata pipeline. The account or application identity needs Microsoft Graph read access to the target site, such as `Sites.Read.All` or a site-scoped `Sites.Selected` grant.

## Demo storyline

1. Add representative documents to `sample-documents`.
2. Run ingestion to extract text and generate metadata through Foundry.
3. Optionally name a custom property and describe what the model should extract during upload.
4. Review country, recency, review status, and recorded approval by knowledge area.
5. Open the review queue and inspect grounded, inferred, or missing support for key metadata fields.
6. Accept or edit each field, then approve the completed metadata record.
7. Browse summaries, themes, classifications, and suggested tags.
8. Filter or group the catalog to demonstrate metadata-driven discovery.
9. Open a document detail view and follow its source-document link.


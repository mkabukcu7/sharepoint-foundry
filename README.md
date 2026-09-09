# Knowledge Metadata Agent MVP

Proof-of-concept repository showing AI-powered metadata extraction and tagging for an enterprise document library.

The MVP ingests 12 representative PDF, Word, and PowerPoint documents, extracts their text, uses a Microsoft Foundry prompt agent to generate metadata, and presents a filterable catalog. The demo does not require SharePoint.

## What this demonstrates

- Automated text extraction from representative enterprise documents
- AI-generated summaries, themes, tags, language, author, sentiment, business area, audience, and metadata category
- Filtering by themes, tags, language, author, and sentiment
- Grouping by category, business area, language, author, or sentiment
- Knowledge-area rollups for review status, recorded approval, and review recency
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

## Demo storyline

1. Add representative documents to `sample-documents`.
2. Run ingestion to extract text and generate metadata through Foundry.
3. Optionally name a custom property and describe what the model should extract during upload.
4. Review country, recency, review status, and recorded approval by knowledge area.
5. Browse summaries, themes, classifications, and suggested tags.
6. Filter or group the catalog to demonstrate metadata-driven discovery.
7. Open a document detail view and follow its source-document link.


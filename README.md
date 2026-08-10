# SharePoint Knowledge Agent Transparency Dashboard MVP

Proof-of-concept repository for an internal Microsoft-style demo showing AI-powered document intelligence for SharePoint knowledge management.

The MVP ingests sample PDF, Word, and PowerPoint documents, extracts content and metadata, generates mock AI summaries/tags, applies governance rules, and exposes a dashboard focused on transparency, freshness, ownership, and review status.

## What this demonstrates

- Automated metadata extraction from SharePoint-style knowledge documents
- AI-generated document summaries, topics, business areas, audience, and suggested tags
- Freshness detection: Current, Needs Review, and Stale
- Risk classification and Human Review Required flags
- Governance dashboard with stale content, SME accountability, and transparency score
- Pluggable AI provider design: mock, Azure OpenAI, or OpenAI

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
│   ├── package.json
│   ├── src/
│   └── static-demo.html
├── sample-documents/
├── data/
│   └── extracted-metadata.json
├── docs/
│   └── architecture.md
└── docker-compose.yml
```

## Quick start

### Backend

```powershell
cd C:\workspace\knowledge-agent-mvp
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r backend\requirements.txt
python backend\scripts\seed_sample_documents.py
python backend\scripts\ingest.py
uvicorn backend.app.main:app --reload --port 8000
```

Open:

- API: http://localhost:8000/api/documents
- Governance summary: http://localhost:8000/api/governance
- Static demo: http://localhost:8000/

### Frontend React source

The React + TypeScript source is in `frontend\src`. This environment does not include Node.js, so the repo also includes `frontend\static-demo.html`, served by FastAPI, for an immediately viewable demo.

If Node.js is installed:

```powershell
cd C:\workspace\knowledge-agent-mvp\frontend
npm install
npm run dev
```

## AI providers

The default provider is `mock`, which requires no external services and uses deterministic heuristics for local demo use.

Set `AI_PROVIDER=azure_openai` or `AI_PROVIDER=openai` when wiring a real model. Provider classes are intentionally pluggable in `backend\app\services\ai_providers.py`.

## Demo storyline

1. Drop SharePoint-exported documents into `sample-documents`.
2. Run ingestion to extract text and generate metadata.
3. Review the dashboard for stale/high-risk content and missing ownership.
4. Open a document detail view to see AI summary, tags, risk flags, and review history.
5. Use the governance dashboard to show transparency score and SME accountability.


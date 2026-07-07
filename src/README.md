# src — Reference Implementation

See the repository [README](../README.md) for the full layout, quick start, and
architecture mapping. Source modules:

- `agent/` — Foundry orchestrator agent, tools, prompts, routing, safety, degradation
- `ingestion/` — multimodal extractors, chunking, AI Search indexer
- `evaluation/` — Foundry built-in Evaluations harness + golden dataset
- `api/` — FastAPI chat backend
- `frontend/` — static web chat UI (stub)

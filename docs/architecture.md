# Architecture

## Purpose

The Knowledge Metadata Agent MVP demonstrates automated extraction, summarization, tagging, and classification for a representative enterprise document library.

## Logical architecture

```mermaid
flowchart LR
    A[SharePoint or Local Documents] --> B[Document Ingestion]
    B --> C[Text and Metadata Extraction]
    C --> D[Pluggable AI Provider]
    D --> E[Structured Metadata Validation]
    E --> F[(extracted-metadata.json)]
    F --> G[FastAPI API]
    G --> H[Static Metadata Catalog]
```

## Components

| Layer | MVP implementation | Production analogue |
|---|---|---|
| Data | SharePoint document library through Graph, with `sample-documents` as the local staging folder | Direct SharePoint processing or event-driven ingestion |
| Extraction | Python OpenXML/PDF text extraction | Graph, Azure AI Document Intelligence, custom parsers |
| AI | Microsoft Foundry prompt agent with mock fallback | Managed Foundry agent and model deployment |
| Metadata | Themes, tags, language, author, sentiment, category, lifecycle signals | SharePoint managed metadata or Purview taxonomy |
| Storage | `data/extracted-metadata.json` | Azure Storage, Cosmos DB, Fabric/OneLake |
| API | FastAPI | Container Apps, App Service, AKS |
| UI | Filterable static catalog served by FastAPI | Enterprise catalog or SharePoint-integrated app |

## Metadata contract

- Concise summary
- Themes and suggested tags
- Language and explicitly identified author
- Sentiment, business area, audience, and metadata category
- Grounded review status, recorded approval status, and review recency
- Country of origin and user-requested custom metadata
- Field-level support, source evidence, and human review decisions for key metadata
- Application-generated link to the source demo document

The configured prompt agent produces the stable metadata contract. Dynamic country and custom-property extraction uses the project model named by `FOUNDRY_EXTRACTION_MODEL`, defaulting to `gpt-5-mini`. Explicit document labels take precedence over generated values.

Review status and recency are derived from an explicit `Last reviewed age days` field. Approval is populated only from an explicit `Approval status` field; otherwise it remains `Not Recorded`.

Metadata review is separate from document lifecycle status. The MVP projects review state onto legacy records, records accept/edit/reject decisions for business area, audience, language, author, and country of origin, and persists approved records to the local JSON store.

## MVP boundaries

This is not production software. SharePoint files are staged locally and metadata is stored in JSON. Incremental synchronization, approval workflows, write-back to SharePoint, and production storage are deferred beyond the MVP.


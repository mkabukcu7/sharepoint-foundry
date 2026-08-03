# Experiment Specification — Multimodal Workplace Chatbot on Microsoft Foundry

| | |
|---|---|
| **Experiment Title** | Multimodal Workplace Chatbot Experiment on Microsoft Foundry |
| **Owning Team** | AI4Ops Enablement |
| **Sponsoring Org** | Your Enterprise — IT |
| **Document Type** | Experiment Specification |
| **Status** | Draft for review |
| **Version** | 1.0 |
| **Related Docs** | `02-architecture.md`, `03-project-plan.md` |

---

## 1. Executive Summary

The AI4Ops Enablement team will design and build a **Modern Multimodal Workplace Chatbot** on **Microsoft Foundry (Azure AI Foundry)** to evaluate the platform's capabilities and limitations across the full AI solution lifecycle — **development, testing, deployment, and hosting**.

The experiment simulates real-world enterprise scenarios by grounding the chatbot on diverse internal sources — **text documents, audio/video content, and structured data (e.g., Tableau reports)** surfaced through a **SharePoint** site. The chatbot is assessed on its ability to deliver **accurate, context-aware, and cited** responses that meet business-user needs, while remaining reliable under failure conditions through **robust error handling and graceful degradation**.

The team will also establish a **business-focused evaluation foundation** and an initial **governance and production-readiness foundation**. Insights will guide the definition of governance standards, architectural patterns, and operational-readiness criteria required to support enterprise-scale production conversational AI.

## 2. Goals & Non-Goals

### 2.1 Goals
- Validate Microsoft Foundry across the **end-to-end lifecycle** (build → test → deploy → host).
- Demonstrate **multi-source, multimodal grounding** (text, audio, video, structured data) sourced from SharePoint.
- Prove **reliability** through error handling and graceful degradation.
- Establish a **repeatable, business-focused evaluation** approach (preferring Foundry built-in Evaluations).
- Produce a **governance and production-readiness foundation** for enterprise rollout.

### 2.2 Non-Goals
- Production rollout to all business units (this is an experiment / proof of value).
- Replacing existing enterprise search or BI tools.
- Building net-new data platforms; the experiment reuses existing SharePoint and Tableau assets.
- Formal certification/compliance sign-off (the experiment produces the *foundation* and recommendations, not final approvals).

## 3. Scope

### 3.1 In Scope
- A conversational agent built on **Foundry Agent Service** with multimodal model support.
- Grounding connectors to **SharePoint** (`https://<YourEnterprise>.sharepoint.com/sites/<page>`) and a multimodal ingestion pipeline.
- Processing of **text, audio, video, and structured (Tableau) data** into a grounded knowledge layer.
- A defined set of **business scenarios and test cases** with success criteria.
- Error-handling, observability, and graceful-degradation mechanisms.
- Governance considerations and a high-level production-readiness checklist.

### 3.2 Out of Scope
- Custom mobile applications (web/Teams surface only for the experiment).
- Real-time voice telephony / IVR integration.
- Fine-tuning of foundation models (prompt + RAG approach preferred for the experiment).

## 4. Stakeholders & Roles

| Role | Responsibility |
|---|---|
| Experiment Sponsor | Funds the experiment, owns business outcomes, approves go/no-go |
| AI4Ops Enablement Lead | Overall delivery accountability |
| AI / Agent Engineer | Builds the Foundry agent, prompts, tools, orchestration |
| Data / Knowledge Engineer | Builds ingestion & grounding pipeline (SharePoint, multimodal, Tableau) |
| Cloud / Platform Engineer | Provisions Azure landing zone, networking, identity, hosting |
| Security & Responsible AI Specialist | Identity, data protection, RAI, governance |
| QA / Evaluation Lead | Defines scenarios, runs Foundry Evaluations, reports findings |
| Business SMEs | Provide use cases, validate answer quality |

## 5. Personas & Representative Use Cases

| Persona | Need | Example query |
|---|---|---|
| IT Employee | Find policy/procedure answers fast | "What's the process to request elevated access to the data platform?" |
| New Hire | Onboard via training media | "Summarize the onboarding video for the data engineering team." |
| Business Analyst | Pull a metric from a report | "What was the claims-processing SLA trend last quarter per the Tableau ops report?" |
| Team Lead | Cross-source synthesis | "Combine the Q3 retro doc and the town-hall recording into 5 key risks." |

## 6. Functional Requirements

| ID | Requirement |
|---|---|
| FR-1 | The chatbot SHALL accept natural-language queries via a chat interface (Web / Microsoft Teams). |
| FR-2 | The chatbot SHALL ground answers in Contoso IT SharePoint content and return **citations** to source material. |
| FR-3 | The chatbot SHALL process and retrieve from **text** documents (e.g., DOCX, PDF, PPTX, pages). |
| FR-4 | The chatbot SHALL process **audio/video** content (transcription + indexing) for grounding. |
| FR-5 | The chatbot SHALL answer questions over **structured data** (e.g., Tableau report metrics). |
| FR-6 | The chatbot SHALL honor **user-level permissions** (security trimming) so users only see content they're entitled to. |
| FR-7 | The chatbot SHALL detect **low-confidence / ungrounded** responses and respond with an appropriate fallback. |
| FR-8 | The chatbot SHALL provide **user-friendly fallback responses** when data sources are unavailable or a query cannot be answered. |
| FR-9 | The chatbot SHALL **log** interactions and traces for troubleshooting, monitoring, and evaluation. |
| FR-10 | The chatbot SHALL apply **content safety** guardrails on input and output. |

## 7. Non-Functional Requirements

| ID | Category | Requirement / Target (experiment) |
|---|---|---|
| NFR-1 | Performance | P50 response < 5 s; P95 < 12 s for grounded text queries. |
| NFR-2 | Reliability | No unhandled crashes; graceful degradation on dependency failure. |
| NFR-3 | Security | Entra ID auth; identity passthrough; secrets in Key Vault; encryption in transit/at rest. |
| NFR-4 | Privacy | No exposure of PII/PHI beyond a user's existing entitlements. |
| NFR-5 | Observability | End-to-end tracing + metrics + structured logs. |
| NFR-6 | Responsible AI | Content Safety, groundedness checks, AI-use disclosure, citations. |
| NFR-7 | Maintainability | Infrastructure-as-Code; repeatable, environment-promotable deployments. |
| NFR-8 | Cost | Tracked per-scenario token/compute cost; documented for rollout sizing. |

## 8. Acceptance Criteria

> Mirrors the experiment brief. Each item is testable and mapped to deliverables/evidence.

### AC-1 — Multi-Source Grounding
- The chatbot integrates the necessary internal enterprise data source (e.g., SharePoint such as `https://<YourEnterprise>.sharepoint.com/sites/<page>`).
- The chatbot demonstrates the ability to process multiple data formats (text, audio, video, structured data such as Tableau reports).
- Defined business scenarios are executed and tested with expected output quality.

### AC-2 — Foundry Capability Validation
- The chatbot is successfully built, tested, and deployed using Microsoft Foundry services.
- The solution demonstrates end-to-end lifecycle coverage (development → deployment → hosting).
- Key limitations, constraints, and observations of Foundry are documented and reviewed with stakeholders.

### AC-3 — Error Handling & Graceful Degradation
- The chatbot handles invalid inputs and system errors without crashing or exposing raw system messages.
- User-friendly fallback responses are provided when: data sources are unavailable; queries cannot be answered; model confidence is low.
- Logging and traceability are implemented for troubleshooting and monitoring.

### AC-4 — Business-Focused Testing Approach
- An efficient testing approach is explored (built-in Evaluations preferred), including defined business scenarios/test cases and success criteria (response accuracy, relevance, user satisfaction).
- Test execution results are recorded and analyzed.
- Findings and improvement recommendations are documented and shared.

### AC-5 — Governance & Production Readiness Foundation
- Initial governance considerations documented: data access and security controls; Responsible AI considerations (bias, transparency, compliance).
- A high-level production-readiness checklist is created covering: deployment approach; monitoring and observability; scaling considerations.
- Key recommendations for enterprise rollout are summarized for stakeholders.

## 9. Business Scenarios & Success Criteria

| Scenario ID | Description | Modality | Success criteria |
|---|---|---|---|
| BS-1 | Policy / procedure Q&A from SharePoint docs | Text | Groundedness ≥ 4/5, correct citation, relevance ≥ 4/5 |
| BS-2 | Summarize a training/town-hall recording | Audio/Video | Faithful summary, key points captured, source-linked |
| BS-3 | Retrieve a metric/trend from a Tableau report | Structured | Correct value/trend, cites report, no fabrication |
| BS-4 | Cross-source synthesis (doc + recording) | Multimodal | Coherent synthesis, all sources cited |
| BS-5 | Out-of-scope / unknown query | N/A | Graceful "I don't know" + guidance, no hallucination |
| BS-6 | Permission-restricted content | Text | User without rights does NOT receive restricted content |
| BS-7 | Data source unavailable (fault injection) | N/A | Friendly fallback, no raw error, logged trace |

**Evaluation metrics (Foundry built-in Evaluations + business KPIs):** Groundedness, Relevance, Coherence, Fluency, Retrieval quality, Response completeness, plus business KPIs: task success rate, citation accuracy, deflection/self-service rate, and user satisfaction (CSAT/thumbs).

## 10. Deliverables

1. Working multimodal chatbot deployed on Microsoft Foundry.
2. Architecture document (`02-architecture.md`) reviewed by specialists, AI Architect, and Cloud Solution Architect.
3. Project plan (`03-project-plan.md`).
4. Evaluation results, findings, and recommendations.
5. Governance considerations + production-readiness checklist.

## 11. Assumptions, Dependencies & Constraints

- **Assumptions:** SharePoint site and Tableau reports are accessible; an Azure subscription with Foundry quota is available; SMEs are available to validate answers.
- **Dependencies:** Entra ID tenant; SharePoint/M365 permissions; Tableau API access; Azure AI Foundry model quota/region availability.
- **Constraints:** Some Foundry capabilities (e.g., SharePoint tool, multimodal grounding) may be in **preview**; insurance-sector data-handling rules apply.

## 12. Risks (summary)

| Risk | Impact | Mitigation |
|---|---|---|
| Preview-feature limitations/changes | Med | Validate early; document constraints; have fallback (custom RAG) |
| Multimodal extraction accuracy (audio/video/Tableau) | Med | Use Content Understanding/Speech; human-validate golden set |
| Over-permissioned retrieval / data leakage | High | Identity passthrough + security trimming + RAI review |
| Hallucination / low groundedness | High | Grounded-only prompts, groundedness eval gate, citations |
| Cost/quota overrun | Med | Track per-scenario cost; choose right-sized models |

## 13. Out-of-Experiment Follow-ups
- Productionization (multi-region, autoscale, DR), broader source onboarding, fine-tuning evaluation, and formal compliance certification.

---
*Detailed risks and mitigations are expanded in the Architecture Document (`02-architecture.md`).*

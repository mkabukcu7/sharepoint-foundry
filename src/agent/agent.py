"""Hosted Foundry agent: create, configure tools, and run turns.

Uses ``azure-ai-projects`` / ``azure-ai-agents`` (imported lazily). The agent is
configured with the curated AI Search knowledge tool, the SharePoint grounding
tool (OBO identity passthrough), and a Tableau function tool. Pure routing,
confidence, and degradation logic live in sibling modules and are unit-tested
without Azure.

CLI:
    python -m src.agent.agent --create     # create/update the agent definition
    python -m src.agent.agent --ask "..."  # one-shot question (smoke test)
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Optional

PROMPT_PATH = Path(__file__).parent / "prompts" / "system_prompt.md"


def load_system_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


class WorkplaceAgent:
    """Thin lifecycle wrapper around a Foundry hosted agent definition."""

    AGENT_NAME = "workplace-multimodal-assistant"

    def __init__(self, project_endpoint: Optional[str] = None, *, safety_client=None):
        from .config import get_settings

        s = get_settings()
        self._endpoint = project_endpoint or s.foundry_project_endpoint
        self._model = s.agent_model_deployment
        self._settings = s
        self._client = None
        self._safety = safety_client
        self._safety_resolved = safety_client is not None

    def _get_safety(self):
        """Lazily build a SafetyClient when Content Safety is configured.

        Returns ``None`` when no endpoint is set so the agent still runs in
        environments without Content Safety (the screen is then a no-op).
        """
        if not self._safety_resolved:
            self._safety_resolved = True
            if self._settings.content_safety_endpoint:
                try:
                    from .safety import SafetyClient

                    self._safety = SafetyClient(self._settings.content_safety_endpoint)
                except Exception:
                    self._safety = None
        return self._safety

    def screen_input(self, question: str):
        """Run Content Safety moderation + Prompt Shields on the user turn.

        Returns ``None`` when the turn is allowed (or safety is not configured),
        otherwise a user-facing refusal string. Never raises: a safety-service
        failure fails open with a trace rather than breaking the turn.
        """
        safety = self._get_safety()
        if safety is None:
            return None
        try:
            moderation = safety.screen_input(question)
            shield = safety.shield_prompt(question)
        except Exception:
            return None
        if not moderation.allowed or not shield.allowed:
            return (
                "I can't help with that request. Let me know if there's "
                "something work-related I can assist with instead."
            )
        return None

    def _get_client(self):
        if self._client is None:
            from azure.ai.projects import AIProjectClient
            from azure.identity import DefaultAzureCredential

            self._client = AIProjectClient(
                endpoint=self._endpoint, credential=DefaultAzureCredential()
            )
        return self._client

    def _tool_definitions(self):
        """Build the tool set: AI Search, SharePoint (OBO), Tableau function."""
        from azure.ai.agents.models import (
            AzureAISearchTool,
            FunctionTool,
        )

        tools = []
        # Curated knowledge index (security-trimmed at query time).
        if self._settings.search_endpoint:
            tools.append(
                AzureAISearchTool(
                    index_connection_id=os.environ.get("SEARCH_CONNECTION_ID", ""),
                    index_name=self._settings.search_index_name,
                )
            )

        # Live SharePoint grounding via the managed SharePoint tool. Uses
        # on-behalf-of identity passthrough so users only see content they are
        # authorized to access (architecture §4.1 / §7).
        sharepoint_conn = os.environ.get("SHAREPOINT_CONNECTION_ID", "")
        if sharepoint_conn:
            try:
                from azure.ai.agents.models import SharepointTool

                tools.append(SharepointTool(connection_id=sharepoint_conn))
            except ImportError:
                # SharePoint tool not available in this SDK build; skip.
                pass

        # Tableau live-metric function tool (executed server-side by us).
        def query_tableau_metric(metric: str, period: str, grain: str = "monthly") -> str:
            """Return the current value of a Tableau metric with provenance."""
            from .tools.tableau_tool import TableauTool

            try:
                result = TableauTool().metric(metric, period, grain=grain)
                return result.citation()
            except Exception as exc:  # noqa: BLE001 — graceful degradation
                from .degradation import FailureMode, resolve

                return resolve(FailureMode.TOOL_FAILURE).message

        tools.append(FunctionTool({query_tableau_metric}))
        return tools

    def create_or_update(self) -> str:
        """Create the agent definition on the Foundry project. Returns agent id."""
        client = self._get_client()
        agent = client.agents.create_agent(
            model=self._model,
            name=self.AGENT_NAME,
            instructions=load_system_prompt(),
            tools=[t for tool in self._tool_definitions() for t in getattr(tool, "definitions", [tool])],
        )
        return agent.id

    @staticmethod
    def _evidence_from_message(message) -> list:
        """Best-effort extraction of retrieved sources from a Foundry message.

        Reads URL-citation annotations (pure ``getattr`` guards) so the agent can
        cross-check the answer's citations against what was actually retrieved.
        Returns an empty list when the SDK build exposes no annotations.
        """
        from .routing import Evidence

        evidence: list = []
        annotations = getattr(message, "url_citation_annotations", None) or []
        for ann in annotations:
            citation = getattr(ann, "url_citation", None)
            url = getattr(citation, "url", "") if citation else getattr(ann, "url", "")
            if url:
                evidence.append(Evidence(chunk_id=url, score=1.0, source_url=url))
        return evidence

    def ask(self, question: str, *, agent_id: Optional[str] = None) -> str:
        """Run a single turn against the agent and return the final text.

        Applies input safety screening before the model call and conservative
        claim-level citation validation after it, degrading gracefully rather
        than surfacing blocked or ungrounded content (architecture §4.5/§6).
        """
        refusal = self.screen_input(question)
        if refusal is not None:
            return refusal

        client = self._get_client()
        thread = client.agents.threads.create()
        client.agents.messages.create(thread_id=thread.id, role="user", content=question)
        run = client.agents.runs.create_and_process(
            thread_id=thread.id, agent_id=agent_id or self.create_or_update()
        )
        if run.status == "failed":
            return f"[run failed] {getattr(run, 'last_error', '')}"
        messages = client.agents.messages.list(thread_id=thread.id)
        for m in messages:
            if m.role == "assistant" and m.text_messages:
                answer = m.text_messages[-1].text.value
                return self._validate_answer(question, answer, m)
        return ""

    def _validate_answer(self, question: str, answer: str, message) -> str:
        """Cross-check the answer's citations against retrieved evidence.

        Conservative: only degrades when the answer cites a source that was
        *not* retrieved (a fabricated citation) — the highest-signal grounding
        failure — so well-grounded answers are never suppressed.
        """
        from .citations import validate_citations
        from .routing import Lane, route

        lane = route(question).lane
        if lane is Lane.SMALLTALK:
            return answer
        evidence = self._evidence_from_message(message)
        if not evidence:
            return answer
        result = validate_citations(answer, evidence, require_citation=True)
        if not result.grounded and result.fabricated_sources:
            from .degradation import FailureMode, resolve

            return resolve(FailureMode.LOW_CONFIDENCE).message
        return answer


def main() -> None:
    parser = argparse.ArgumentParser(description="Workplace multimodal agent")
    parser.add_argument("--create", action="store_true", help="create/update the agent")
    parser.add_argument("--ask", type=str, help="ask a one-shot question")
    args = parser.parse_args()

    agent = WorkplaceAgent()
    if args.create:
        agent_id = agent.create_or_update()
        print(f"Agent ready: {agent_id}")
    if args.ask:
        print(agent.ask(args.ask))
    if not args.create and not args.ask:
        parser.print_help()


if __name__ == "__main__":
    main()

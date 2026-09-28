import os
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.app.services.knowledge_librarian import (
    DEFAULT_AGENT_NAME,
    DEFAULT_AGENT_VERSION,
    KnowledgeLibrarianConfigurationError,
    KnowledgeLibrarianProvider,
    load_librarian_prompt,
)


def test_librarian_prompt_contains_governance_boundaries() -> None:
    prompt = load_librarian_prompt()

    assert "Never invent taxonomy terms" in prompt
    assert "Obtain explicit approval" in prompt
    assert "Search only content the requesting user is authorized to access." in prompt
    assert "If evidence is insufficient, say so" in prompt
    assert "verified SharePoint version information" in prompt


def test_librarian_defaults_are_separate_from_existing_agent_names() -> None:
    assert DEFAULT_AGENT_NAME == "knowledge-librarian-agent"
    assert DEFAULT_AGENT_VERSION == "2"
    assert DEFAULT_AGENT_NAME not in {"sharepoint-foundry-agent", "wtw-metadata-classifier"}


def test_librarian_prompt_path_reports_missing_file(tmp_path: Path) -> None:
    with patch.dict(os.environ, {"FOUNDRY_LIBRARIAN_PROMPT_PATH": str(tmp_path / "missing.md")}):
        with pytest.raises(KnowledgeLibrarianConfigurationError, match="Unable to read"):
            load_librarian_prompt()


def test_librarian_retrieves_sharepoint_version_history(tmp_path: Path) -> None:
    candidate = {
        "fileName": "guide.pdf",
        "webUrl": "https://example.sharepoint.com/Reviewed/guide.pdf",
        "currentVersion": "2.0",
        "lastModifiedDateTime": "2026-09-22T05:55:00Z",
        "versions": [{"id": "2.0", "modifiedBy": "Reviewer"}],
    }
    with (
        patch.dict(os.environ, {"SHAREPOINT_HOSTNAME": "example.sharepoint.com"}),
        patch("backend.app.services.knowledge_librarian.SharePointClient") as client_type,
    ):
        client_type.return_value.find_version_candidate.return_value = candidate
        result = KnowledgeLibrarianProvider.retrieve_version_information("guide.pdf")

    assert result["candidateFound"] is True
    assert result["currentVersion"] == "2.0"
    assert result["versions"] == candidate["versions"]
    client_type.return_value.find_version_candidate.assert_called_once_with("Reviewed", "guide.pdf")


def test_librarian_includes_retrieved_version_context_in_agent_request() -> None:
    class Responses:
        input = ""

        def create(self, model: str, input: str, extra_body: dict) -> object:
            self.input = input
            return type("Response", (), {"output_text": "Current version is 2.0."})()

    provider = KnowledgeLibrarianProvider.__new__(KnowledgeLibrarianProvider)
    provider.model = "gpt-5-mini"
    provider.agent_name = DEFAULT_AGENT_NAME
    provider.agent_version = DEFAULT_AGENT_VERSION
    provider.client = type("Client", (), {"responses": Responses()})()
    with patch.object(
        KnowledgeLibrarianProvider,
        "retrieve_version_information",
        return_value={"candidateFound": True, "currentVersion": "2.0", "versions": [{"id": "2.0"}]},
    ):
        result = provider.answer("What is the current version?", "guide.pdf")

    assert result == "Current version is 2.0."
    assert "<sharepoint_version_information>" in provider.client.responses.input
    assert '"currentVersion": "2.0"' in provider.client.responses.input


def test_librarian_forwards_local_chat_history() -> None:
    class Responses:
        input = None

        def create(self, model: str, input: object, extra_body: dict) -> object:
            self.input = input
            return type("Response", (), {"output_text": "I will use the conversation context."})()

    provider = KnowledgeLibrarianProvider.__new__(KnowledgeLibrarianProvider)
    provider.model = "gpt-5-mini"
    provider.agent_name = DEFAULT_AGENT_NAME
    provider.agent_version = DEFAULT_AGENT_VERSION
    provider.client = type("Client", (), {"responses": Responses()})()
    history = [
        {"role": "user", "content": "We are discussing version history."},
        {"role": "assistant", "content": "Understood."},
        {"role": "user", "content": "What should happen after approval?"},
    ]

    result = provider.answer(history)

    assert result == "I will use the conversation context."
    assert provider.client.responses.input == history

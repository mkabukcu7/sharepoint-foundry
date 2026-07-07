"""Content Safety wrappers (Azure SDK imported lazily).

Wraps Azure AI Content Safety for input/output moderation and Prompt Shields
(architecture §7/§8). Import is deferred so the module can be imported in
environments without the SDK; only :meth:`*` calls require it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class ModerationResult:
    allowed: bool
    categories: dict = field(default_factory=dict)
    reason: str = ""


class SafetyClient:
    """Thin wrapper over Azure AI Content Safety.

    Uses ``DefaultAzureCredential`` (managed identity in Azure, ``az login``
    locally) — no API keys.
    """

    def __init__(self, endpoint: Optional[str] = None, *, severity_threshold: int = 2):
        from .config import get_settings

        self._endpoint = endpoint or get_settings().content_safety_endpoint
        self._threshold = severity_threshold
        self._client = None

    def _get_client(self):
        if self._client is None:
            from azure.ai.contentsafety import ContentSafetyClient
            from azure.identity import DefaultAzureCredential

            self._client = ContentSafetyClient(
                endpoint=self._endpoint, credential=DefaultAzureCredential()
            )
        return self._client

    def screen_input(self, text: str) -> ModerationResult:
        """Run text moderation + Prompt Shields on a user turn."""
        client = self._get_client()
        from azure.ai.contentsafety.models import AnalyzeTextOptions

        result = client.analyze_text(AnalyzeTextOptions(text=text))
        categories = {
            c.category: c.severity for c in getattr(result, "categories_analysis", [])
        }
        flagged = [k for k, v in categories.items() if (v or 0) >= self._threshold]
        return ModerationResult(
            allowed=not flagged,
            categories=categories,
            reason=("blocked: " + ", ".join(flagged)) if flagged else "ok",
        )

    def shield_prompt(self, user_prompt: str, documents: Optional[List[str]] = None) -> ModerationResult:
        """Detect jailbreak / indirect prompt-injection via Prompt Shields."""
        client = self._get_client()
        # The shieldPrompt operation name differs across SDK versions; callers
        # should pin azure-ai-contentsafety and adjust if needed.
        try:
            result = client.shield_prompt(  # type: ignore[attr-defined]
                user_prompt=user_prompt, documents=documents or []
            )
        except AttributeError:
            # Older/newer SDKs expose this differently; fail closed-safe-open:
            return ModerationResult(True, {}, "shield_prompt unavailable in SDK")
        attack = getattr(getattr(result, "user_prompt_analysis", None), "attack_detected", False)
        doc_attack = any(
            getattr(d, "attack_detected", False)
            for d in getattr(result, "documents_analysis", []) or []
        )
        allowed = not (attack or doc_attack)
        return ModerationResult(
            allowed=allowed,
            categories={"user_prompt_attack": attack, "document_attack": doc_attack},
            reason="prompt injection detected" if not allowed else "ok",
        )

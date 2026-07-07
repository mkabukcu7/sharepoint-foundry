"""Model configuration helper for azure-ai-evaluation evaluators.

Builds the Azure OpenAI model config used by AI-assisted evaluators. Imports
are lazy so the evaluation package stays import-safe without Azure installed.
"""
from __future__ import annotations

from typing import Optional


def build_model_config(project_endpoint: Optional[str] = None):
    """Return an AzureOpenAIModelConfiguration for evaluator judge calls."""
    from azure.ai.evaluation import AzureOpenAIModelConfiguration
    from ..agent.config import get_settings

    s = get_settings()
    endpoint = project_endpoint or s.foundry_project_endpoint
    return AzureOpenAIModelConfiguration(
        azure_endpoint=endpoint,
        azure_deployment=s.agent_model_deployment,
    )

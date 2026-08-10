import os
import re
from abc import ABC, abstractmethod


class AIProvider(ABC):
    @abstractmethod
    def analyze(self, document_name: str, text: str) -> dict:
        raise NotImplementedError


class MockAIProvider(AIProvider):
    topic_map = {
        "claims": "Claims",
        "advisor": "Advisor Experience",
        "retirement": "Retirement",
        "policy": "Policy Servicing",
        "beneficiary": "Policy Servicing",
        "tax": "Forms",
        "pricing": "Pricing",
        "privacy": "Compliance",
        "compliance": "Compliance",
        "investment": "Wealth",
        "life insurance": "Insurance",
    }

    def analyze(self, document_name: str, text: str) -> dict:
        normalized = text.lower()
        topics = sorted({topic for keyword, topic in self.topic_map.items() if keyword in normalized}) or ["Knowledge Management"]
        contains_pricing = any(term in normalized for term in ["pricing", "fee", "commission", "rate card"])
        contains_confidential = any(term in normalized for term in ["confidential", "restricted", "non-public", "customer ssn"])
        business_area = _first_match(normalized, {
            "Claims": ["claim", "death claim"],
            "Retirement": ["retirement", "pension", "401k"],
            "Wealth": ["wealth", "investment", "advisor"],
            "Insurance": ["life insurance", "policy", "beneficiary"],
            "Compliance": ["privacy", "compliance", "regulatory"],
        }, "Enterprise Knowledge")
        audience = _first_match(normalized, {
            "Customer": ["customer", "policyholder", "beneficiary"],
            "Advisor": ["advisor", "broker"],
            "Internal Operations": ["sme", "operations", "internal"],
        }, "Internal Operations")
        risk = "High Risk" if contains_pricing or contains_confidential or "regulatory" in normalized else ("External" if audience == "Customer" else "Internal")
        summary = _summary(text)
        return {
            "summary": summary,
            "topics": topics,
            "businessArea": business_area,
            "audience": audience,
            "documentRisk": risk,
            "containsPricing": contains_pricing,
            "containsConfidentialInfo": contains_confidential,
            "suggestedTags": sorted(set(topics + [business_area, audience, risk])),
        }


class AzureOpenAIProvider(AIProvider):
    def analyze(self, document_name: str, text: str) -> dict:
        raise RuntimeError("Azure OpenAI provider is a pluggable placeholder for the MVP. Use AI_PROVIDER=mock for local demos.")


class OpenAIProvider(AIProvider):
    def analyze(self, document_name: str, text: str) -> dict:
        raise RuntimeError("OpenAI provider is a pluggable placeholder for the MVP. Use AI_PROVIDER=mock for local demos.")


def get_provider() -> AIProvider:
    provider = os.getenv("AI_PROVIDER", "mock").lower()
    if provider == "azure_openai":
        return AzureOpenAIProvider()
    if provider == "openai":
        return OpenAIProvider()
    return MockAIProvider()


def _summary(text: str) -> str:
    cleaned = re.sub(r"\\s+", " ", text).strip()
    if len(cleaned) <= 280:
        return cleaned
    return cleaned[:277].rsplit(" ", 1)[0] + "..."


def _first_match(text: str, options: dict[str, list[str]], default: str) -> str:
    for label, keywords in options.items():
        if any(keyword in text for keyword in keywords):
            return label
    return default


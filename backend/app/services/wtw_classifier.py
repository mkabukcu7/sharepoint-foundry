import json
from dataclasses import dataclass
from pathlib import Path

from backend.app.services.taxonomy import Taxonomy, TaxonomyError


@dataclass(frozen=True)
class ClassificationEvidence:
    value: str
    confidence: float
    evidence: str


CLASSIFICATION_FIELDS = {
    "materialType": "materialTypes",
    "topics": "topics",
    "businesses": "businesses",
    "industries": "industries",
    "geographies": "geographies",
    "collections": "collections",
    "languages": "languages",
}


class WTWClassifier:
    def __init__(self, taxonomy: Taxonomy) -> None:
        self.taxonomy = taxonomy

    def prompt(self, document_name: str, text: str) -> str:
        allowed = {
            category: self.taxonomy.choices(category)
            for category in CLASSIFICATION_FIELDS.values()
        }
        return (
            "You are WTW Metadata Classifier. Classify the supplied document only against the active "
            "controlled taxonomy below. Never invent terms. Abstain when evidence is insufficient. "
            "Return JSON only. Every selected value must include confidence from 0 to 1 and a concise "
            "evidence quote or explanation grounded in the document. Set reviewRequired true when any "
            "required field is uncertain, missing, or risky. Flag external publication, client names, "
            "pricing, personal data, or internal-only content in riskFlags.\n\n"
            f"Allowed taxonomy values:\n{json.dumps(allowed, ensure_ascii=False)}\n\n"
            "Output schema:\n"
            '{"title":"", "summary":"", "materialType":{"value":null,"confidence":0,"evidence":""},'
            '"topics":[],"businesses":[],"industries":[],"geographies":[],"collections":[],'
            '"languages":[],"reviewRequired":true,"riskFlags":[]}\n\n'
            f"Document name: {document_name}\n<document_text>\n{text}\n</document_text>"
        )

    def validate(self, result: dict) -> dict:
        self.taxonomy.validate_result(result)
        for field in CLASSIFICATION_FIELDS:
            raw = result.get(field, [])
            if raw is None:
                continue
            candidates = [raw] if isinstance(raw, dict) else raw
            if isinstance(candidates, dict):
                candidates = [candidates]
            for candidate in candidates:
                if not isinstance(candidate, dict):
                    raise TaxonomyError(f"{field} candidates must contain value, confidence, and evidence")
                if candidate.get("value") is None:
                    continue
                confidence = candidate.get("confidence")
                if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
                    raise TaxonomyError(f"{field} confidence must be between 0 and 1")
                if not str(candidate.get("evidence", "")).strip():
                    raise TaxonomyError(f"{field} evidence is required")
        if not isinstance(result.get("reviewRequired"), bool):
            raise TaxonomyError("reviewRequired must be boolean")
        if not isinstance(result.get("riskFlags", []), list):
            raise TaxonomyError("riskFlags must be a list")
        return result


def load_classifier(path: Path) -> WTWClassifier:
    return WTWClassifier(Taxonomy.from_file(path))
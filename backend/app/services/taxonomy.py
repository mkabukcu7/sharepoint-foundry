import json
from pathlib import Path


class TaxonomyError(ValueError):
    pass


class Taxonomy:
    def __init__(self, data: dict) -> None:
        self.data = data
        self.categories = data.get("categories", {})
        self._terms = {
            category: {entry["value"].casefold(): entry for entry in entries}
            for category, entries in self.categories.items()
        }

    @classmethod
    def from_file(cls, path: Path) -> "Taxonomy":
        return cls(json.loads(path.read_text(encoding="utf-8")))

    def choices(self, category: str, active_only: bool = True) -> list[str]:
        entries = self.categories.get(category, [])
        return [entry["value"] for entry in entries if not active_only or entry.get("active", True)]

    def validate(self, category: str, values: list[str], active_only: bool = True) -> list[str]:
        validated = []
        for value in values:
            entry = self._terms.get(category, {}).get(value.casefold())
            if entry is None:
                raise TaxonomyError(f"Unknown {category} term: {value}")
            if active_only and not entry.get("active", True):
                raise TaxonomyError(f"Deprecated {category} term: {value}")
            validated.append(entry["value"])
        return validated

    def validate_result(self, result: dict) -> dict:
        mappings = {
            "materialType": "materialTypes",
            "topics": "topics",
            "businesses": "businesses",
            "industries": "industries",
            "geographies": "geographies",
            "collections": "collections",
            "languages": "languages",
        }
        validated = dict(result)
        for output_field, category in mappings.items():
            raw = result.get(output_field, [])
            if raw is None:
                continue
            candidates = [raw] if isinstance(raw, (str, dict)) else raw
            values = [item.get("value") if isinstance(item, dict) else item for item in candidates]
            values = [value for value in values if value is not None]
            self.validate(category, values)
        return validated
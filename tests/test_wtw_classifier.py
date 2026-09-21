import unittest

from backend.app.services.taxonomy import TaxonomyError
from backend.app.services.wtw_classifier import WTWClassifier
from backend.app.services.taxonomy import Taxonomy


class WTWClassifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.classifier = WTWClassifier(Taxonomy({
            "categories": {
                "materialTypes": [{"value": "Training Material", "active": True}],
                "topics": [{"value": "Career Framework", "active": True}],
                "businesses": [{"value": "Work & Rewards", "active": True}],
                "industries": [{"value": "Technology", "active": True}],
                "geographies": [{"value": "United States", "active": True}],
                "collections": [{"value": "Skills", "active": True}],
                "languages": [{"value": "English", "active": True}],
            }
        }))

    def test_prompt_contains_only_active_controlled_values(self) -> None:
        prompt = self.classifier.prompt("guide.docx", "Career framework training")

        self.assertIn("Career Framework", prompt)
        self.assertIn("Never invent terms", prompt)
        self.assertIn("confidence", prompt)
        self.assertIn("evidence", prompt)

    def test_valid_result_is_accepted(self) -> None:
        result = {
            "title": "Career guide",
            "summary": "A training guide.",
            "materialType": {"value": "Training Material", "confidence": 0.9, "evidence": "Training guide"},
            "topics": [{"value": "Career Framework", "confidence": 0.8, "evidence": "Career framework"}],
            "businesses": [],
            "industries": [],
            "geographies": [],
            "collections": [],
            "languages": [{"value": "English", "confidence": 1, "evidence": "English text"}],
            "reviewRequired": False,
            "riskFlags": [],
        }

        self.assertEqual(self.classifier.validate(result), result)

    def test_validation_returns_canonical_taxonomy_values(self) -> None:
        result = {
            "materialType": {"value": "training material", "confidence": 0.9, "evidence": "Training guide"},
            "topics": [{"value": "career framework", "confidence": 0.8, "evidence": "Career framework"}],
            "businesses": [],
            "industries": [],
            "geographies": [],
            "collections": [],
            "languages": [{"value": "english", "confidence": 1, "evidence": "English text"}],
            "reviewRequired": False,
            "riskFlags": [],
        }

        validated = self.classifier.validate(result)

        self.assertEqual(validated["materialType"]["value"], "Training Material")
        self.assertEqual(validated["topics"][0]["value"], "Career Framework")
        self.assertEqual(validated["languages"][0]["value"], "English")

    def test_invented_term_and_invalid_confidence_are_rejected(self) -> None:
        result = {
            "materialType": {"value": "Invented", "confidence": 1.2, "evidence": "none"},
            "topics": [], "businesses": [], "industries": [], "geographies": [], "collections": [], "languages": [],
            "reviewRequired": True, "riskFlags": [],
        }

        with self.assertRaises(TaxonomyError):
            self.classifier.validate(result)


if __name__ == "__main__":
    unittest.main()
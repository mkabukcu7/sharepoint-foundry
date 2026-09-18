import unittest
from pathlib import Path

from backend.app.services.taxonomy import Taxonomy, TaxonomyError


class TaxonomyTests(unittest.TestCase):
    taxonomy = Taxonomy.from_file(Path("taxonomy/controlled-terms.json"))

    def test_extracted_categories_match_reference_counts(self) -> None:
        self.assertEqual(len(self.taxonomy.categories["materialTypes"]), 40)
        self.assertEqual(len(self.taxonomy.choices("materialTypes")), 37)
        self.assertEqual(len(self.taxonomy.choices("crossBusinesses")), 14)
        self.assertEqual(len(self.taxonomy.choices("geographies")), 17)
        self.assertEqual(len(self.taxonomy.choices("otherGeographies")), 25)
        self.assertEqual(len(self.taxonomy.choices("languages")), 39)
        self.assertEqual(len(self.taxonomy.choices("industries")), 85)
        self.assertEqual(len(self.taxonomy.choices("collections")), 144)
        self.assertEqual(len(self.taxonomy.choices("series")), 232)
        self.assertEqual(len(self.taxonomy.choices("topics")), 1225)

    def test_deprecated_material_terms_are_not_active_choices(self) -> None:
        terms = {entry["value"]: entry for entry in self.taxonomy.categories["materialTypes"]}

        self.assertFalse(terms["Advertising"]["active"])
        self.assertNotIn("Advertising", self.taxonomy.choices("materialTypes"))
        with self.assertRaisesRegex(TaxonomyError, "Deprecated"):
            self.taxonomy.validate("materialTypes", ["Advertising"])

    def test_business_hierarchy_is_preserved(self) -> None:
        entry = next(item for item in self.taxonomy.categories["businesses"] if item["value"] == "Horizons Practice")

        self.assertEqual(entry["parent"], "New Business Ventures LOB")
        self.assertEqual(entry["path"], ["New Business Ventures LOB", "Horizons Practice"])

    def test_classifier_values_must_be_controlled(self) -> None:
        with self.assertRaisesRegex(TaxonomyError, "Unknown topics term"):
            self.taxonomy.validate("topics", ["Invented Topic"])


if __name__ == "__main__":
    unittest.main()
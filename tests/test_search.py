import unittest
from types import SimpleNamespace

from backend.app.services import search
from backend.app.services.search import (
    ApprovedKnowledgeSearch,
    SearchSettings,
    build_search_index,
    chunk_text,
    is_search_eligible,
)


class FakeEmbeddings:
    def create(self, model: str, input: list[str]) -> object:
        return SimpleNamespace(
            data=[SimpleNamespace(embedding=[float(index), 1.0, 0.0]) for index, _ in enumerate(input)]
        )


class FakeOpenAI:
    embeddings = FakeEmbeddings()


class FakeSearchClient:
    """A small in-memory stand-in that upserts, deletes and filters like the real index."""

    def __init__(self) -> None:
        self.documents = []
        self.options = None
        self.query_options = None
        self.lag = False

    def upload_documents(self, documents: list[dict]) -> None:
        for document in documents:
            self.documents = [item for item in self.documents if item["id"] != document["id"]]
            self.documents.append(document)

    def delete_documents(self, documents: list[dict]) -> None:
        removed = {document["id"] for document in documents}
        self.documents = [item for item in self.documents if item["id"] not in removed]

    def search(self, **options: object) -> list[dict]:
        self.options = options
        filter_text = str(options.get("filter") or "")
        if filter_text.startswith("sourceDocument eq "):
            if self.lag:
                return []
            literal = filter_text[len("sourceDocument eq ") :].strip()
            wanted = literal[1:-1].replace("''", "'")
            return [item for item in self.documents if item["sourceDocument"] == wanted]
        self.query_options = options
        return [
            {
                "@search.score": 1.0,
                "content": item["content"],
                "sourceDocument": item["sourceDocument"],
                "sourceUrl": item["sourceUrl"],
                "sourceVersion": item["sourceVersion"],
                "chunkNumber": item["chunkNumber"],
            }
            for item in self.documents
        ]


class FakeIndexClient:
    def create_or_update_index(self, index: object) -> object:
        return index


class SearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = SearchSettings(
            endpoint="https://search.example.net",
            index_name="approved",
            embedding_model="embedding-model",
            vector_dimensions=3,
        )
        self.search_client = FakeSearchClient()
        self.search = ApprovedKnowledgeSearch(
            settings=self.settings,
            credential=object(),
            embedding_client=FakeOpenAI(),
            index_client=FakeIndexClient(),
            search_client=self.search_client,
        )

    def test_index_definition_contains_approval_and_vector_fields(self) -> None:
        index = build_search_index(self.settings)
        fields = {field.name: field for field in index.fields}

        self.assertIn("contentVector", fields)
        self.assertIn("approved", fields)
        self.assertEqual(fields["contentVector"].vector_search_dimensions, 3)

    def test_chunk_text_has_overlap(self) -> None:
        chunks = chunk_text("abcdefghij", size=6, overlap=2)

        self.assertEqual(chunks, ["abcdef", "efghij"])

    def test_only_approved_documents_are_eligible(self) -> None:
        self.assertFalse(is_search_eligible({"metadataReview": {"status": "needs-review"}}))
        self.assertTrue(is_search_eligible({"metadataReview": {"status": "approved"}}))
        self.assertFalse(
            is_search_eligible(
                {
                    "metadataReview": {"status": "approved"},
                    "sharePointWritebackEnabled": True,
                    "sharePointWriteback": {"status": "failed"},
                }
            )
        )

    def test_index_and_query_use_hybrid_search_and_citations(self) -> None:
        document = {
            "documentName": "guide.pdf",
            "title": "Guide",
            "metadataReview": {"status": "approved"},
            "wtwClassification": {"topics": [{"value": "Benefits"}]},
            "metadataLink": "/api/documents/guide.pdf",
        }

        self.search.ensure_index()
        self.assertEqual(self.search.index_document(document, "Approved answer evidence."), 1)
        results = self.search.query("What is approved?", top=1)

        self.assertEqual(len(self.search_client.documents), 1)
        self.assertEqual(self.search_client.documents[0]["approved"], True)
        self.assertEqual(results[0]["citation"]["documentName"], "guide.pdf")
        self.assertEqual(self.search_client.query_options["filter"], "approved eq true and reviewStatus eq 'approved'")
        self.assertEqual(len(self.search_client.query_options["vector_queries"]), 1)

    def test_index_uses_sharepoint_version_label_for_citations(self) -> None:
        document = {
            "documentName": "guide.pdf",
            "metadataReview": {"status": "approved"},
            "sharePoint": {
                "version": "2.0",
                "etag": '"verified-etag"',
                "webUrl": "https://example.sharepoint.com/Reviewed/guide.pdf",
            },
        }

        self.search.index_document(document, "Approved updated content.")

        self.assertEqual(self.search_client.documents[0]["sourceVersion"], "2.0")


class RelevanceThresholdTests(unittest.TestCase):
    def test_weak_reranker_hits_are_dropped_so_the_librarian_can_abstain(self) -> None:
        matches = [
            {"rerankerScore": 2.7, "content": "relevant"},
            {"rerankerScore": 1.6, "content": "noise"},
        ]

        kept = search._relevant_only(matches)

        self.assertEqual([match["content"] for match in kept], ["relevant"])

    def test_all_weak_hits_yield_no_evidence(self) -> None:
        matches = [{"rerankerScore": 1.1}, {"rerankerScore": 0.9}]

        self.assertEqual(search._relevant_only(matches), [])

    def test_results_without_reranker_scores_are_kept(self) -> None:
        matches = [{"rerankerScore": None, "score": 0.03}]

        self.assertEqual(search._relevant_only(matches), matches)


class IndexLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = SearchSettings(
            endpoint="https://search.example.net",
            index_name="approved",
            embedding_model="embedding-model",
            vector_dimensions=3,
        )
        self.search_client = FakeSearchClient()
        self.search = ApprovedKnowledgeSearch(
            settings=self.settings,
            credential=object(),
            embedding_client=FakeOpenAI(),
            index_client=FakeIndexClient(),
            search_client=self.search_client,
        )

    def _document(self, version: str) -> dict:
        return {
            "documentName": "policy.docx",
            "metadataReview": {"status": "approved"},
            "sharePoint": {"version": version, "webUrl": "https://example.sharepoint.com/policy.docx"},
        }

    def test_reindexing_a_new_version_replaces_the_superseded_one(self) -> None:
        first = self._document("2.0")
        self.search.index_document(first, "Old guidance says thirty days.")
        second = self._document("3.0")
        second["searchIndex"] = {"chunks": 1}
        self.search.index_document(second, "New guidance says sixty days.")

        versions = {item["sourceVersion"] for item in self.search_client.documents}
        self.assertEqual(versions, {"3.0"})
        self.assertEqual(len(self.search_client.documents), 1)
        self.assertNotIn(
            "Old guidance says thirty days.",
            [item["content"] for item in self.search_client.documents],
        )

    def test_supersession_survives_indexing_lag(self) -> None:
        """A just-written chunk is not yet queryable, so purging must not rely on search."""
        self.search.index_document(self._document("2.0"), "Old guidance says thirty days.")
        self.search_client.lag = True
        second = self._document("3.0")
        second["searchIndex"] = {"chunks": 1}
        self.search.index_document(second, "New guidance says sixty days.")
        self.search_client.lag = False

        self.assertEqual(len(self.search_client.documents), 1)
        self.assertEqual(self.search_client.documents[0]["sourceVersion"], "3.0")

    def test_a_shrunken_document_leaves_no_orphan_chunks(self) -> None:
        long_document = self._document("1.0")
        chunks = self.search.index_document(long_document, "Approved guidance. " * 300)
        self.assertGreater(chunks, 1)

        shorter = self._document("2.0")
        shorter["searchIndex"] = {"chunks": chunks}
        self.search.index_document(shorter, "Now much shorter.")

        self.assertEqual(len(self.search_client.documents), 1)
        self.assertEqual(self.search_client.documents[0]["sourceVersion"], "2.0")

    def test_withdrawing_a_document_removes_every_chunk(self) -> None:
        chunks = self.search.index_document(self._document("2.0"), "Approved guidance. " * 200)
        self.assertGreater(chunks, 1)

        removed = self.search.remove_document("policy.docx", known_chunk_count=chunks)

        self.assertGreater(removed, 0)
        self.assertEqual(self.search_client.documents, [])

    def test_withdrawal_works_even_when_the_index_lags(self) -> None:
        chunks = self.search.index_document(self._document("2.0"), "Approved guidance. " * 200)
        self.search_client.lag = True

        self.search.remove_document("policy.docx", known_chunk_count=chunks)

        self.search_client.lag = False
        self.assertEqual(self.search_client.documents, [])

    def test_removing_an_unindexed_document_is_a_no_op(self) -> None:
        self.assertEqual(self.search.remove_document("never-indexed.docx"), 0)

    def test_removal_only_touches_the_named_document(self) -> None:
        self.search.index_document(self._document("2.0"), "Policy content.")
        other = self._document("1.0")
        other["documentName"] = "handbook.docx"
        self.search.index_document(other, "Handbook content.")

        self.search.remove_document("policy.docx", known_chunk_count=1)

        remaining = {item["sourceDocument"] for item in self.search_client.documents}
        self.assertEqual(remaining, {"handbook.docx"})

    def test_document_names_with_quotes_do_not_break_the_filter(self) -> None:
        document = self._document("1.0")
        document["documentName"] = "reviewer's notes.docx"
        self.search.index_document(document, "Quoted name content.")

        self.assertEqual(self.search.remove_document("reviewer's notes.docx"), 1)
        self.assertEqual(self.search_client.documents, [])

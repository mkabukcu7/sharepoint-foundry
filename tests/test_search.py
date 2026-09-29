import os
import unittest
from types import SimpleNamespace
from unittest import mock

from azure.core.exceptions import HttpResponseError, ServiceRequestError

from backend.app.services import search
from backend.app.services.search import (
    ApprovedKnowledgeSearch,
    SearchOperationError,
    SearchSettings,
    _chunk_id,
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


class _FailingPager:
    """Mimics the SDK pager raising only once iteration begins."""

    def __iter__(self):
        raise HttpResponseError("search service failed while paging")
        yield  # pragma: no cover - never reached, marks this a generator


class FakeSearchClient:
    """A small in-memory stand-in that upserts, deletes and filters like the real index."""

    def __init__(self, reranker_score: float | None = 3.0) -> None:
        self.documents = []
        self.options = None
        self.query_options = None
        self.lag = False
        self.fail_search = False
        self.fail_iteration = False
        # The real service returns this whenever semantic ranking is on, and the
        # librarian judges evidence by it.
        self.reranker_score = reranker_score
        # The real service reports per-key batch failures in the returned results
        # rather than by raising, so the fake has to be able to do the same.
        self.fail_delete_keys: set[str] = set()
        self.fail_upload_keys: set[str] = set()

    def _results(self, ids: list[str], failing: set[str]) -> list[SimpleNamespace]:
        return [
            SimpleNamespace(
                key=chunk_id,
                succeeded=chunk_id not in failing,
                status_code=200 if chunk_id not in failing else 503,
                error_message=None if chunk_id not in failing else "service overloaded",
            )
            for chunk_id in ids
        ]

    def upload_documents(self, documents: list[dict]) -> list[SimpleNamespace]:
        for document in documents:
            if document["id"] in self.fail_upload_keys:
                continue
            self.documents = [item for item in self.documents if item["id"] != document["id"]]
            self.documents.append(document)
        return self._results([d["id"] for d in documents], self.fail_upload_keys)

    def delete_documents(self, documents: list[dict]) -> list[SimpleNamespace]:
        ids = [document["id"] for document in documents]
        removed = {chunk_id for chunk_id in ids if chunk_id not in self.fail_delete_keys}
        self.documents = [item for item in self.documents if item["id"] not in removed]
        return self._results(ids, self.fail_delete_keys)

    def search(self, **options: object) -> list[dict]:
        self.options = options
        if self.fail_search:
            raise ServiceRequestError("search service unreachable")
        filter_text = str(options.get("filter") or "")
        if filter_text.startswith("sourceDocument eq "):
            if self.lag:
                return []
            literal = filter_text[len("sourceDocument eq ") :].strip()
            wanted = literal[1:-1].replace("''", "'")
            return [item for item in self.documents if item["sourceDocument"] == wanted]
        self.query_options = options
        if self.fail_iteration:
            return _FailingPager()
        return [
            {
                "@search.score": 1.0,
                "@search.reranker_score": self.reranker_score,
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

        kept = search._relevant_only(matches, semantic_enabled=True)

        self.assertEqual([match["content"] for match in kept], ["relevant"])

    def test_all_weak_hits_yield_no_evidence(self) -> None:
        matches = [{"rerankerScore": 1.1}, {"rerankerScore": 0.9}]

        self.assertEqual(search._relevant_only(matches, semantic_enabled=True), [])

    def test_results_without_reranker_scores_are_not_treated_as_evidence(self) -> None:
        """An unscored hit means relevance is unknown, not that the hit is good.

        Keeping these would let the librarian answer from evidence nobody has
        judged, because hybrid scores cannot tell relevant from irrelevant.
        """
        matches = [{"rerankerScore": None, "score": 0.03}]

        with self.assertRaises(search.SearchOperationError) as caught:
            search._relevant_only(matches, semantic_enabled=True)

        self.assertIn("reranker", str(caught.exception).lower())

    def test_disabled_semantic_ranking_refuses_to_judge_relevance(self) -> None:
        matches = [{"rerankerScore": None, "score": 0.03}]

        with self.assertRaises(search.SearchOperationError) as caught:
            search._relevant_only(matches, semantic_enabled=False)

        self.assertIn("SEARCH_USE_SEMANTIC_RANKER", str(caught.exception))

    def test_no_matches_needs_no_reranker(self) -> None:
        self.assertEqual(search._relevant_only([], semantic_enabled=False), [])

    def test_semantic_ranking_is_on_unless_explicitly_disabled(self) -> None:
        """Defaulting this off would silently remove abstention on a fresh deploy."""
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertTrue(search._semantic_ranking_enabled())
        with mock.patch.dict(os.environ, {"SEARCH_USE_SEMANTIC_RANKER": "false"}, clear=True):
            self.assertFalse(search._semantic_ranking_enabled())

    def test_a_zero_reranker_score_is_weak_evidence_not_a_missing_score(self) -> None:
        """0.0 is falsy but meaningful: the passage is irrelevant, not unscored."""
        self.assertEqual(search._reranker_score({"@search.reranker_score": 0.0}), 0.0)

        matches = [{"rerankerScore": 0.0, "content": "irrelevant"}]

        # Weak evidence abstains quietly; a missing score would raise instead and
        # be reported to the user as a search outage.
        self.assertEqual(search._relevant_only(matches, semantic_enabled=True), [])

    def test_the_camel_case_score_is_only_a_fallback_for_a_missing_value(self) -> None:
        self.assertEqual(search._reranker_score({"@search.rerankerScore": 2.4}), 2.4)
        self.assertIsNone(search._reranker_score({}))


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

    def test_known_chunks_are_deleted_even_when_the_sweep_fails(self) -> None:
        """The sweep is a backstop. A failing sweep must not leave revoked content citable."""
        chunks = self.search.index_document(self._document("2.0"), "Approved guidance. " * 200)
        self.assertGreater(chunks, 1)
        self.search_client.fail_search = True

        removed = self.search.remove_document("policy.docx", known_chunk_count=chunks)

        self.search_client.fail_search = False
        self.assertEqual(removed, chunks)
        self.assertEqual(self.search_client.documents, [])

    def test_a_failing_sweep_is_reported_when_nothing_is_known_to_delete(self) -> None:
        """With no recorded chunk count the sweep is the only source of ids, so silence would hide the failure."""
        self.search_client.fail_search = True

        with self.assertRaises(search.SearchOperationError):
            self.search.remove_document("policy.docx")

    def test_query_failures_surface_as_search_operation_errors(self) -> None:
        """The chat degrades on SearchOperationError; a raw SDK error would crash the turn."""
        self.search_client.fail_search = True

        with self.assertRaises(search.SearchOperationError):
            self.search.query("What is approved?")

    def test_query_failures_during_iteration_are_also_wrapped(self) -> None:
        """Results are a lazy pager, so the failure can arrive after the call returns."""
        self.search_client.fail_iteration = True

        with self.assertRaises(search.SearchOperationError):
            self.search.query("What is approved?")

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


class PartialBatchFailureTests(unittest.TestCase):
    """Azure Search reports per-key batch failures in the results, not by raising."""

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

    def _document(self) -> dict:
        return {
            "documentName": "policy.docx",
            "metadataReview": {"status": "approved"},
            "sharePoint": {"version": "2.0", "webUrl": "https://example/policy.docx"},
        }

    def test_a_failed_chunk_deletion_is_not_reported_as_a_successful_withdrawal(self) -> None:
        """Counting a failed delete as removed leaves un-approved content citable."""
        self.search.index_document(self._document(), "Guidance. " * 400)
        indexed = len(self.search_client.documents)
        self.assertGreater(indexed, 0)

        surviving = self.search_client.documents[0]["id"]
        self.search_client.fail_delete_keys = {surviving}

        with self.assertRaises(SearchOperationError) as caught:
            self.search.remove_document("policy.docx", known_chunk_count=indexed)

        self.assertIn("policy.docx", str(caught.exception))
        # The chunk really is still there, which is exactly why this must raise.
        self.assertTrue(any(item["id"] == surviving for item in self.search_client.documents))

    def test_a_partial_upload_does_not_report_a_full_index(self) -> None:
        document = self._document()
        chunks = chunk_text("Guidance. " * 400)
        self.assertGreater(len(chunks), 1)
        self.search_client.fail_upload_keys = {_chunk_id("policy.docx", 1)}

        with self.assertRaises(SearchOperationError):
            self.search.index_document(document, "Guidance. " * 400)

    def test_a_partial_upload_leaves_no_half_written_document_behind(self) -> None:
        """Ids are position-keyed, so a partial upload mixes two versions."""
        self.search.index_document(self._document(), "Old guidance. " * 400)
        self.assertGreater(len(self.search_client.documents), 0)

        second = self._document()
        second["sharePoint"]["version"] = "3.0"
        second["searchIndex"] = {"chunks": len(self.search_client.documents)}
        self.search_client.fail_upload_keys = {_chunk_id("policy.docx", 1)}

        with self.assertRaises(SearchOperationError):
            self.search.index_document(second, "New guidance. " * 400)

        self.assertEqual(self.search_client.documents, [])

    def test_a_clean_batch_is_still_counted_as_removed(self) -> None:
        self.search.index_document(self._document(), "Guidance. " * 400)
        indexed = len(self.search_client.documents)

        removed = self.search.remove_document("policy.docx", known_chunk_count=indexed)

        self.assertEqual(removed, indexed)
        self.assertEqual(self.search_client.documents, [])

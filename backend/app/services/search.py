import os
from collections.abc import Iterable
from dataclasses import dataclass
from hashlib import sha256
from typing import Any
from urllib.parse import urlparse

from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    HnswAlgorithmConfiguration,
    SearchableField,
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    SemanticConfiguration,
    SemanticField,
    SemanticPrioritizedFields,
    SemanticSearch,
    SimpleField,
    VectorSearch,
    VectorSearchProfile,
)
from azure.search.documents.models import VectorizedQuery
from azure.core.exceptions import HttpResponseError, ServiceRequestError
from azure.identity import AzureCliCredential, DefaultAzureCredential, get_bearer_token_provider


class SearchConfigurationError(ValueError):
    pass


def _as_count(value: object) -> int:
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


class SearchOperationError(RuntimeError):
    """A search operation failed.

    ``chunks_left_behind`` reports how many position-keyed chunk ids the failed
    operation may have left in the index. Callers record it so a later
    withdrawal still knows which ids to delete; without it a failure would erase
    the only record of them and leave superseded content citable.
    """

    def __init__(self, *args: object, chunks_left_behind: int = 0) -> None:
        super().__init__(*args)
        self.chunks_left_behind = max(int(chunks_left_behind or 0), 0)


@dataclass(frozen=True)
class SearchSettings:
    endpoint: str
    index_name: str
    embedding_model: str
    vector_dimensions: int

    @classmethod
    def from_environment(cls) -> "SearchSettings":
        endpoint = os.getenv("SEARCH_ENDPOINT", "").strip()
        if not endpoint:
            raise SearchConfigurationError("SEARCH_ENDPOINT is required for Azure AI Search")
        raw_dimensions = os.getenv("SEARCH_VECTOR_DIMENSIONS", "1536").strip()
        try:
            dimensions = int(raw_dimensions)
        except ValueError as error:
            raise SearchConfigurationError("SEARCH_VECTOR_DIMENSIONS must be an integer") from error
        if dimensions <= 0:
            raise SearchConfigurationError("SEARCH_VECTOR_DIMENSIONS must be greater than zero")
        return cls(
            endpoint=endpoint.rstrip("/"),
            index_name=os.getenv("SEARCH_INDEX_NAME", "wtw-approved-knowledge").strip(),
            embedding_model=os.getenv("FOUNDRY_EMBEDDING_MODEL", "text-embedding-3-small").strip(),
            vector_dimensions=dimensions,
        )


def build_search_index(settings: SearchSettings) -> SearchIndex:
    vector_type = SearchFieldDataType.Collection(SearchFieldDataType.Single)
    return SearchIndex(
        name=settings.index_name,
        fields=[
            SimpleField(name="id", type=SearchFieldDataType.String, key=True, filterable=True),
            SearchableField(name="title", type=SearchFieldDataType.String, searchable=True),
            SearchableField(name="content", type=SearchFieldDataType.String, searchable=True),
            SimpleField(name="sourceDocument", type=SearchFieldDataType.String, filterable=True, facetable=True),
            SimpleField(name="sourceUrl", type=SearchFieldDataType.String),
            SimpleField(name="sourceVersion", type=SearchFieldDataType.String, filterable=True),
            SimpleField(name="approved", type=SearchFieldDataType.Boolean, filterable=True, facetable=True),
            SimpleField(name="reviewStatus", type=SearchFieldDataType.String, filterable=True, facetable=True),
            SimpleField(name="writebackStatus", type=SearchFieldDataType.String, filterable=True, facetable=True),
            SimpleField(name="chunkNumber", type=SearchFieldDataType.Int32, filterable=True, sortable=True),
            SearchField(
                name="contentVector",
                type=vector_type,
                searchable=True,
                vector_search_dimensions=settings.vector_dimensions,
                vector_search_profile_name="content-vector-profile",
            ),
            SearchField(
                name="topics",
                type=SearchFieldDataType.Collection(SearchFieldDataType.String),
                searchable=True,
                filterable=True,
                facetable=True,
            ),
            SearchField(
                name="businesses",
                type=SearchFieldDataType.Collection(SearchFieldDataType.String),
                searchable=True,
                filterable=True,
                facetable=True,
            ),
            SearchField(
                name="industries",
                type=SearchFieldDataType.Collection(SearchFieldDataType.String),
                searchable=True,
                filterable=True,
                facetable=True,
            ),
            SearchField(
                name="geographies",
                type=SearchFieldDataType.Collection(SearchFieldDataType.String),
                searchable=True,
                filterable=True,
                facetable=True,
            ),
        ],
        vector_search=VectorSearch(
            algorithms=[HnswAlgorithmConfiguration(name="content-hnsw")],
            profiles=[
                VectorSearchProfile(
                    name="content-vector-profile",
                    algorithm_configuration_name="content-hnsw",
                )
            ],
        ),
        semantic_search=SemanticSearch(
            configurations=[
                SemanticConfiguration(
                    name="semantic-config",
                    prioritized_fields=SemanticPrioritizedFields(
                        title_field=SemanticField(field_name="title"),
                        content_fields=[SemanticField(field_name="content")],
                        keywords_fields=[SemanticField(field_name="topics")],
                    ),
                )
            ]
        ),
    )


def is_search_eligible(document: dict) -> bool:
    review = document.get("metadataReview")
    if not isinstance(review, dict) or review.get("status") != "approved":
        return False
    if document.get("sharePointWritebackEnabled") is True:
        writeback = document.get("sharePointWriteback")
        return isinstance(writeback, dict) and writeback.get("status") == "applied"
    return True


def chunk_text(text: str, size: int = 1200, overlap: int = 150) -> list[str]:
    if size <= overlap:
        raise ValueError("Chunk size must be greater than overlap")
    normalized = " ".join(text.split())
    if not normalized:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(normalized):
        end = min(start + size, len(normalized))
        chunks.append(normalized[start:end])
        if end == len(normalized):
            break
        start = end - overlap
    return chunks


DEFAULT_MIN_RERANKER_SCORE = 1.9


def _reranker_score(result: dict) -> float | None:
    """Read the semantic reranker score from a search result.

    The SDK returns ``@search.reranker_score`` (snake_case); an earlier
    camelCase read silently yielded ``None`` and disabled ranking with no error,
    so both spellings are accepted.

    A genuine score of ``0.0`` is falsy but meaningful — it means the passage is
    irrelevant, not that ranking was unavailable — so this falls back only when
    the value is missing.
    """
    score = result.get("@search.reranker_score")
    if score is None:
        score = result.get("@search.rerankerScore")
    return score


def _semantic_ranking_enabled() -> bool:
    """Semantic ranking is on unless an operator explicitly turns it off.

    Abstention depends on the reranker score, so defaulting this off would make a
    fresh deployment answer from weak evidence without anyone choosing that.
    """
    return os.getenv("SEARCH_USE_SEMANTIC_RANKER", "true").strip().lower() in {"1", "true", "yes"}


def _relevant_only(matches: list[dict], semantic_enabled: bool) -> list[dict]:
    """Drop weak hits so an unanswerable question yields no evidence.

    Hybrid RRF scores sit in a narrow band (~0.03) whether or not a chunk is
    relevant, so they cannot support abstention. Only the semantic reranker score
    (0-4) separates supported from unsupported questions.

    A missing reranker score therefore means relevance is unknown, not that the
    hit is good. Returning unscored hits would let the librarian answer from
    evidence nobody has judged, so this raises and the caller degrades to its
    "search unavailable" path instead.
    """
    if not matches:
        return []
    if not semantic_enabled:
        raise SearchOperationError(
            "Semantic ranking is disabled, so retrieved passages cannot be scored for "
            "relevance. Set SEARCH_USE_SEMANTIC_RANKER=true; without it weak and strong "
            "evidence are indistinguishable."
        )
    if not any(match.get("rerankerScore") is not None for match in matches):
        raise SearchOperationError(
            "The search service returned no reranker scores, so relevance could not be "
            "judged. Check that semantic ranking is enabled on the search service."
        )
    try:
        threshold = float(os.getenv("SEARCH_MIN_RERANKER_SCORE", DEFAULT_MIN_RERANKER_SCORE))
    except ValueError:
        threshold = DEFAULT_MIN_RERANKER_SCORE
    return [
        match
        for match in matches
        if match.get("rerankerScore") is not None and match["rerankerScore"] >= threshold
    ]


class ApprovedKnowledgeSearch:
    def __init__(
        self,
        settings: SearchSettings | None = None,
        credential: Any | None = None,
        embedding_client: Any | None = None,
        index_client: Any | None = None,
        search_client: Any | None = None,
    ) -> None:
        self.settings = settings or SearchSettings.from_environment()
        self.credential = credential or _credential()
        self.embedding_client = embedding_client or _foundry_openai_client()
        self.index_client = index_client or SearchIndexClient(
            endpoint=self.settings.endpoint,
            credential=self.credential,
        )
        self.search_client = search_client or SearchClient(
            endpoint=self.settings.endpoint,
            index_name=self.settings.index_name,
            credential=self.credential,
        )

    def ensure_index(self) -> SearchIndex:
        return self.index_client.create_or_update_index(build_search_index(self.settings))

    def chunk_ids_for(self, document_name: str) -> list[str]:
        """Return the chunk ids currently visible in the index for a document.

        Azure AI Search indexes asynchronously, so this lags recent writes. Use it
        for reconciliation sweeps, never to decide what a just-completed write
        should supersede.
        """
        try:
            results = self.search_client.search(
                search_text="*",
                filter=f"sourceDocument eq '{_escape_odata(document_name)}'",
                select=["id"],
                top=1000,
            )
            return [result["id"] for result in results]
        except (HttpResponseError, ServiceRequestError) as error:
            raise SearchOperationError(f"Could not list indexed chunks for {document_name}: {error}") from error

    def remove_document(self, document_name: str, known_chunk_count: int = 0) -> int:
        """Delete a document's chunks from the index.

        Approval status is frozen into each chunk at index time, so withdrawing a
        document from the library must also withdraw its chunks.

        ``known_chunk_count`` is the chunk count recorded when the document was
        last indexed. Those ids are deleted first because they are deterministic
        and immune to indexing lag. The query sweep that follows is only a
        reconciliation backstop, so its failure must not prevent the deletion.
        """
        known = [_chunk_id(document_name, number) for number in range(max(known_chunk_count, 0))]
        removed: set[str] = set()
        if known:
            try:
                self._delete_ids(document_name, known)
            except SearchOperationError as error:
                raise SearchOperationError(
                    str(error), chunks_left_behind=len(known)
                ) from error
            removed.update(known)
        try:
            extra = [chunk_id for chunk_id in self.chunk_ids_for(document_name) if chunk_id not in removed]
        except SearchOperationError:
            if not known:
                raise
            return len(removed)
        if extra:
            try:
                self._delete_ids(document_name, extra)
            except SearchOperationError as error:
                raise SearchOperationError(
                    str(error), chunks_left_behind=len(known) + len(extra)
                ) from error
            removed.update(extra)
        return len(removed)

    def _delete_ids(self, document_name: str, ids: list[str]) -> int:
        if not ids:
            return 0
        try:
            results = self.search_client.delete_documents(
                documents=[{"id": chunk_id} for chunk_id in ids]
            )
        except (HttpResponseError, ServiceRequestError) as error:
            raise SearchOperationError(f"Could not remove {document_name} from the index: {error}") from error
        failures = _failed_keys(results)
        if failures:
            # A batch can report per-key failures without raising. Counting those
            # as removed would record a revocation as complete while the chunks
            # are still citable.
            raise SearchOperationError(
                f"Could not remove {len(failures)} of {len(ids)} chunks for "
                f"{document_name}: {_describe_failures(failures)}"
            )
        return len(ids)

    def index_document(self, document: dict, text: str) -> int:
        if not is_search_eligible(document):
            return 0
        chunks = chunk_text(text)
        if not chunks:
            raise SearchOperationError(f"Cannot index empty document: {document.get('documentName', 'unknown')}")
        vectors = self._embed(chunks)
        classification = document.get("wtwClassification")
        classification = classification if isinstance(classification, dict) else {}
        approved_taxonomy = document.get("approvedTaxonomy")
        approved_taxonomy = approved_taxonomy if isinstance(approved_taxonomy, dict) else {}
        sharepoint = document.get("sharePoint")
        sharepoint = sharepoint if isinstance(sharepoint, dict) else {}
        writeback = document.get("sharePointWriteback")
        writeback = writeback if isinstance(writeback, dict) else {}
        source_version = str(
            sharepoint.get("version")
            or sharepoint.get("etag")
            or document.get("modifiedDateTime")
            or "local"
        )
        source_url = str(sharepoint.get("webUrl") or document.get("metadataLink") or "")
        records = [
            {
                "id": _chunk_id(document["documentName"], number),
                "title": document.get("title") or document["documentName"],
                "content": chunk,
                "contentVector": vector,
                "sourceDocument": document["documentName"],
                "sourceUrl": source_url,
                "sourceVersion": source_version,
                "approved": True,
                "reviewStatus": "approved",
                "writebackStatus": str(writeback.get("status") or "local-approved"),
                "chunkNumber": number,
                "topics": _facet_values(approved_taxonomy, classification, "topics"),
                "businesses": _facet_values(approved_taxonomy, classification, "businesses"),
                "industries": _facet_values(approved_taxonomy, classification, "industries"),
                "geographies": _facet_values(approved_taxonomy, classification, "geographies"),
            }
            for number, (chunk, vector) in enumerate(zip(chunks, vectors))
        ]
        try:
            results = self.search_client.upload_documents(documents=records)
        except Exception as error:
            # A request-level failure has an uncertain outcome, so retain every
            # attempted position id for a later withdrawal.
            raise SearchOperationError(
                f"Could not upload {len(records)} chunks for {document['documentName']}: {error}",
                chunks_left_behind=len(records),
            ) from error
        failures = _failed_keys(results)
        if failures:
            # Ids are position-keyed, so a partial upload has already overwritten
            # part of any previous version: the index now holds a mix of two
            # versions. Remove what was written rather than leave inconsistent
            # guidance citable, then report the failure so it can be retried.
            rollback, left_behind = self._rollback_partial_upload(
                document["documentName"], len(records)
            )
            raise SearchOperationError(
                f"Could not index {len(failures)} of {len(records)} chunks for "
                f"{document['documentName']}: {_describe_failures(failures)}.{rollback}",
                chunks_left_behind=left_behind,
            )
        # Chunk ids are position-keyed, so the upload above overwrites the previous
        # version in place. Only a shrunken document leaves a tail behind, and its
        # ids are computed rather than queried because indexing lags writes.
        previous = document.get("searchIndex")
        previous = previous if isinstance(previous, dict) else {}
        previous_count = max(
            _as_count(previous.get("indexedChunks")),
            _as_count(previous.get("chunks")),
        )
        stale = [
            _chunk_id(document["documentName"], number)
            for number in range(len(records), previous_count)
        ]
        try:
            self._delete_ids(document["documentName"], stale)
        except SearchOperationError as error:
            # The new version is in place, but the old tail is not. Report the
            # full extent so the recorded watermark still covers those ids.
            raise SearchOperationError(
                str(error), chunks_left_behind=max(previous_count, len(records))
            ) from error
        return len(records)

    def _rollback_partial_upload(self, document_name: str, count: int) -> tuple[str, int]:
        """Best-effort removal of a half-written document; never masks the cause.

        Returns the note to append to the error and how many chunk ids may still
        be in the index afterwards.
        """
        try:
            self._delete_ids(document_name, [_chunk_id(document_name, n) for n in range(count)])
        except SearchOperationError as error:
            return (
                f" The partially written chunks could not be removed either: {error}",
                count,
            )
        return " The partially written chunks were removed.", 0

    def query(self, question: str, top: int = 5) -> list[dict]:
        vectors = self._embed([question])
        vector_query = VectorizedQuery(
            vector=vectors[0],
            k_nearest_neighbors=max(top * 2, 10),
            fields="contentVector",
        )
        use_semantic = _semantic_ranking_enabled()
        search_options = {
            "search_text": question,
            "vector_queries": [vector_query],
            "filter": "approved eq true and reviewStatus eq 'approved'",
            "top": top,
            "query_type": "semantic" if use_semantic else "simple",
        }
        if use_semantic:
            search_options["semantic_configuration_name"] = "semantic-config"
        # Results are a lazy pager, so service and network failures can surface
        # during iteration rather than at the call. Both are wrapped here so the
        # caller sees SearchOperationError and degrades instead of crashing.
        try:
            results = self.search_client.search(**search_options)
            matches = [
                {
                    "score": result.get("@search.score"),
                    "rerankerScore": _reranker_score(result),
                    "content": result.get("content", ""),
                    "citation": {
                        "documentName": result.get("sourceDocument", ""),
                        "sourceUrl": result.get("sourceUrl", ""),
                        "chunkNumber": result.get("chunkNumber"),
                        "sourceVersion": result.get("sourceVersion", ""),
                    },
                }
                for result in results
            ]
        except (HttpResponseError, ServiceRequestError) as error:
            raise SearchOperationError(f"Approved-knowledge search failed: {error}") from error
        return _relevant_only(matches, use_semantic)

    def _embed(self, inputs: list[str]) -> list[list[float]]:
        try:
            response = self.embedding_client.embeddings.create(
                model=self.settings.embedding_model,
                input=inputs,
            )
        except Exception as error:
            raise SearchOperationError(f"Embedding generation failed: {error}") from error
        data = getattr(response, "data", None)
        if not isinstance(data, list) or len(data) != len(inputs):
            raise SearchOperationError("Embedding response did not contain one vector per input")
        vectors = [item.embedding for item in data]
        if any(len(vector) != self.settings.vector_dimensions for vector in vectors):
            raise SearchOperationError(
                f"Embedding dimensions do not match SEARCH_VECTOR_DIMENSIONS={self.settings.vector_dimensions}"
            )
        return vectors


def _credential() -> Any:
    if os.getenv("FOUNDRY_CREDENTIAL_MODE", "default").strip().lower() == "azure_cli":
        return AzureCliCredential()
    return DefaultAzureCredential()


def _embedding_endpoint() -> str:
    """Embeddings are served by the account, not the project-scoped path."""
    configured = os.getenv("FOUNDRY_EMBEDDING_ENDPOINT", "").strip()
    if configured:
        return configured.rstrip("/")
    endpoint = os.getenv("FOUNDRY_PROJECT_ENDPOINT", "").strip()
    if not endpoint:
        raise SearchConfigurationError(
            "FOUNDRY_PROJECT_ENDPOINT or FOUNDRY_EMBEDDING_ENDPOINT is required for embeddings"
        )
    parsed = urlparse(endpoint)
    if not parsed.scheme or not parsed.netloc:
        raise SearchConfigurationError("FOUNDRY_PROJECT_ENDPOINT must be an absolute https URL")
    return f"{parsed.scheme}://{parsed.netloc}"


def _foundry_openai_client() -> Any:
    from openai import AzureOpenAI

    token_provider = get_bearer_token_provider(
        _credential(), "https://cognitiveservices.azure.com/.default"
    )
    return AzureOpenAI(
        azure_endpoint=_embedding_endpoint(),
        azure_ad_token_provider=token_provider,
        api_version=os.getenv("FOUNDRY_EMBEDDING_API_VERSION", "2024-10-21").strip(),
    )


def _escape_odata(value: str) -> str:
    """Escape a string literal for an OData filter."""
    return value.replace("'", "''")


def _chunk_id(document_name: str, chunk_number: int) -> str:
    """Identify a chunk by document and position only.

    Deliberately excludes the version: a re-index must overwrite the previous
    version's chunks in place. Including the version made ids diverge, so old
    and new guidance sat in the index together, both flagged approved.
    """
    digest = sha256(f"{document_name}::{chunk_number}".encode("utf-8")).hexdigest()
    return digest


def _failed_keys(results: object) -> list[tuple[str, str]]:
    """Collect per-key failures from a batch indexing response.

    Azure AI Search reports partial batch failures in the returned results
    rather than by raising, so a batch that "succeeded" can still have left
    chunks behind. Each entry is (key, reason).
    """
    if not isinstance(results, Iterable):
        return []
    failures: list[tuple[str, str]] = []
    for result in results:
        succeeded = getattr(result, "succeeded", None)
        if succeeded is None and isinstance(result, dict):
            succeeded = result.get("succeeded", result.get("status"))
        if succeeded:
            continue
        key = getattr(result, "key", None)
        status_code = getattr(result, "status_code", None)
        message = getattr(result, "error_message", None)
        if isinstance(result, dict):
            key = key or result.get("key")
            status_code = status_code or result.get("statusCode")
            message = message or result.get("errorMessage")
        failures.append((str(key or "unknown"), str(message or status_code or "unknown error")))
    return failures


def _describe_failures(failures: list[tuple[str, str]], limit: int = 3) -> str:
    shown = ", ".join(f"{key} ({reason})" for key, reason in failures[:limit])
    if len(failures) > limit:
        shown += f", and {len(failures) - limit} more"
    return shown


def _facet_values(approved_taxonomy: dict, classification: dict, field: str) -> list[str]:
    """Prefer reviewer-approved taxonomy; fall back to raw classifier output."""
    if field in approved_taxonomy:
        approved = approved_taxonomy.get(field)
        if isinstance(approved, list):
            return [str(value).strip() for value in approved if str(value).strip()]
        if approved is not None and str(approved).strip():
            return [str(approved).strip()]
        return []
    return _classification_values(classification, field)


def _classification_values(classification: dict, field: str) -> list[str]:
    values = classification.get(field, [])
    if not isinstance(values, list):
        return []
    return [
        str(candidate.get("value", "")).strip()
        for candidate in values
        if isinstance(candidate, dict) and str(candidate.get("value", "")).strip()
    ]

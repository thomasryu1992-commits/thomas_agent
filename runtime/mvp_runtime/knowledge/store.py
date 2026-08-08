"""The knowledge base's durable store: append-only JSONL, per-machine, schema-validated.

Shaped like the runtime's other local stores (``working_memory``, ``task_registry``) and for
the same reasons — a knowledge base is machine state, not shared source, so it lives under
the mounted ``.runtime_governance_state/`` and never enters the image or a commit.

**Why this is not working_memory.** The reuse-first rule says check for an existing owner
before minting a store, and there are two plausible ones. Neither fits:

- ``working_memory`` holds *candidates the agent proposed about how to work*, on a
  governance ladder (CANDIDATE -> VALIDATED -> CORE) with a promotion door Thomas controls
  and a 7-day expiry. A document the operator hands in is on no ladder: it is not a claim
  the runtime made, promoting it would mean nothing, and expiring last month's analysis
  after a week would delete the corpus this exists to accumulate.
- ``operational_knowledge`` holds *validated operating truth* with a review window,
  confidence, and an environment signature. A stored document asserts none of that. It says
  "this text was supplied, from this source, at this time" — and conflating a source
  document with a validated conclusion is the exact confusion the review window exists to
  prevent.

So: a third store, a closed schema of its own, and a docstring saying why, which is the
price of minting one.

**Append-only with latest-wins.** A re-ingest of the same source appends a new row and marks
the old one superseded rather than rewriting it. The sequence of rows for one source is the
document's revision history, which is the question ("what did the 08-04 analysis say before
it was updated?") that a rewriting store cannot answer at all.

**The index cache.** Building the retrieval index means chunking and tokenizing the whole
corpus. A door is long-lived and answers many questions against a store that changes rarely,
so the built index is cached against the store file's signature (size + mtime) — the same
idiom, and the same fail-closed property, as ``schema_cache``: any change to the file misses
the cache, so a stale index cannot be served.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Mapping

from runtime.read_only_kernel.integrity import short_id
from runtime.read_only_kernel.schema_validation import RuntimeSchemaError

from .. import jsonl, schema_cache, timeutil
from ..errors import KnowledgeBlocked
from ..filelock import locked
from ..paths import repo_root as _repo_root
from .index import LexicalRetriever, Retriever, build_passages

KNOWLEDGE_REL = ".runtime_governance_state/knowledge"
DOCUMENTS_FILE = "documents.jsonl"
_DOCUMENTS_LOCK = ".documents.lock"

SCHEMA_VERSION = "knowledge_document.v0.1"
DOCUMENT_ID_PREFIX = "kdoc"

SOURCE_TYPES = ("pdf", "text", "markdown")
SENSITIVITIES = ("PUBLIC", "INTERNAL", "PRIVATE", "SENSITIVE", "RESTRICTED")
DEFAULT_SENSITIVITY = "INTERNAL"

# Ceilings, so a door cannot be used to fill a disk one call at a time.
MAX_TEXT_CHARS = 4 * 1024 * 1024
MAX_TAGS = 32


class KnowledgeStore:
    """Append-only JSONL store of ingested documents, rooted at a directory."""

    def __init__(self, root: Path):
        self._root = Path(root)
        self._index_cache: tuple[tuple[Any, ...], Retriever] | None = None

    @classmethod
    def default(cls, root: Path | None = None) -> "KnowledgeStore":
        return cls((root if root is not None else _repo_root()) / KNOWLEDGE_REL)

    @property
    def root(self) -> Path:
        return self._root

    @property
    def path(self) -> Path:
        return self._root / DOCUMENTS_FILE

    # ---- writes -------------------------------------------------------------------

    def add(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """Validate and append one document; return the stored record.

        Held under the documents lock for the whole read-modify-append: the duplicate check
        reads the store and the append writes it, and two ingests of the same document
        racing between those two steps would both find no duplicate and both store one.
        """
        validate_document(record)
        with self._lock():
            existing = self._read_unlocked()
            duplicate = _find_by_hash(existing, record["content_sha256"])
            if duplicate is not None:
                return {
                    **duplicate,
                    "_duplicate_of": duplicate["document_id"],
                }
            rows: list[Mapping[str, Any]] = [dict(record)]
            superseded = _find_live_by_source(existing, record["source"])
            if superseded is not None:
                # The old row is not edited — it is re-appended with the marker set, so the
                # original stays byte-identical in the file above it. Latest-wins on read
                # means the marked copy is the one that counts.
                rows.insert(0, {**superseded, "superseded_by": record["document_id"]})
            jsonl.append_lines(
                self.path, rows,
                write_code="KNOWLEDGE_WRITE_FAILED", label="knowledge documents",
            )
        self._index_cache = None
        return dict(record)

    # ---- reads --------------------------------------------------------------------

    def read_all(self) -> list[dict[str, Any]]:
        """Every stored row, oldest first. Fail-closed on a corrupt store."""
        with self._lock():
            return self._read_unlocked()

    def live_documents(self) -> list[dict[str, Any]]:
        """The current view: latest row per ``document_id``, superseded rows dropped.

        This is what a query searches. A superseded document is still on disk and still
        readable by :meth:`read_all` — it is out of the *answer*, not out of the record.
        """
        latest: dict[str, dict[str, Any]] = {}
        for row in self.read_all():
            document_id = row.get("document_id")
            if isinstance(document_id, str) and document_id:
                latest[document_id] = row
        return [row for row in latest.values() if not row.get("superseded_by")]

    def retriever(self) -> Retriever:
        """The retrieval index over the live documents, rebuilt only when the store changed."""
        signature = self._signature()
        if self._index_cache is not None and self._index_cache[0] == signature:
            return self._index_cache[1]
        retriever = LexicalRetriever()
        retriever.index(build_passages(self.live_documents()))
        self._index_cache = (signature, retriever)
        return retriever

    def stats(self) -> dict[str, Any]:
        """Counts an operator can check before trusting an answer that cites this store."""
        live = self.live_documents()
        return {
            "documents": len(live),
            "chars": sum(int(row.get("chars", 0)) for row in live),
            "sources": len({row.get("source") for row in live}),
            "oldest_ingested_at_utc": min((r["ingested_at_utc"] for r in live), default=None),
            "newest_ingested_at_utc": max((r["ingested_at_utc"] for r in live), default=None),
        }

    # ---- internals ----------------------------------------------------------------

    def _read_unlocked(self) -> list[dict[str, Any]]:
        return jsonl.read_objects(
            self.path, read_code="KNOWLEDGE_UNREADABLE", label="knowledge documents",
        )

    def _signature(self) -> tuple[Any, ...]:
        try:
            stat = self.path.stat()
        except OSError:
            return ("absent",)
        return (stat.st_size, stat.st_mtime_ns)

    def _lock(self):
        return locked(
            self._root / _DOCUMENTS_LOCK,
            code="KNOWLEDGE_LOCK_FAILED",
            label="knowledge documents",
        )


def build_document(
    *,
    title: str,
    source: str,
    text: str,
    source_type: str = "text",
    ingested_by: str,
    now: str | None = None,
    document_date: str | None = None,
    data_sensitivity: str = DEFAULT_SENSITIVITY,
    tags: list[str] | None = None,
    page_count: int | None = None,
    extraction: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate the caller's inputs and build one storable document record. Fail-closed.

    The id seed is the content hash plus the source, not the timestamp: re-ingesting the same
    text from the same source is the *same document*, and giving it a new id every time would
    make the duplicate check the only thing standing between the store and one row per call.
    """
    title = _required_text(title, "title", "KNOWLEDGE_TITLE_REQUIRED", limit=500)
    source = _required_text(source, "source", "KNOWLEDGE_SOURCE_REQUIRED", limit=1000)
    ingested_by = _required_text(
        ingested_by, "ingested_by", "KNOWLEDGE_INGESTED_BY_REQUIRED", limit=200,
    )

    if not isinstance(text, str) or not text.strip():
        raise KnowledgeBlocked(
            "KNOWLEDGE_TEXT_REQUIRED", "a document needs non-empty text to be worth storing",
        )
    text = text.strip()
    if len(text) > MAX_TEXT_CHARS:
        raise KnowledgeBlocked(
            "KNOWLEDGE_TEXT_TOO_LARGE",
            f"document text is {len(text)} characters, over the {MAX_TEXT_CHARS} ceiling",
        )

    if source_type not in SOURCE_TYPES:
        raise KnowledgeBlocked(
            "KNOWLEDGE_SOURCE_TYPE_INVALID",
            f"source_type must be one of {list(SOURCE_TYPES)}; got {source_type!r}",
        )
    if data_sensitivity not in SENSITIVITIES:
        raise KnowledgeBlocked(
            "KNOWLEDGE_SENSITIVITY_INVALID",
            f"data_sensitivity must be one of {list(SENSITIVITIES)}; got {data_sensitivity!r}",
        )

    clean_tags = _clean_tags(tags)
    document_date = _optional_timestamp(document_date)
    content_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()

    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "document_id": short_id(DOCUMENT_ID_PREFIX, {"sha256": content_sha256, "source": source}),
        "title": title,
        "source": source,
        "source_type": source_type,
        "document_date": document_date,
        "ingested_at_utc": now or timeutil.utc_now_iso(),
        "ingested_by": ingested_by,
        "data_sensitivity": data_sensitivity,
        "text": text,
        "chars": len(text),
        "content_sha256": content_sha256,
        "page_count": page_count,
        "extraction": dict(extraction) if extraction else None,
        "tags": clean_tags,
    }
    validate_document(record)
    return record


def validate_document(record: Mapping[str, Any]) -> None:
    """Validate a record against its closed schema before it is persisted (fail-closed).

    Resolved against the *repo* root, never the store's root — schemas are committed source;
    the store's root is per-machine state."""
    try:
        schema_cache.validate_against_schema(
            record,
            _repo_root() / "schemas" / f"{SCHEMA_VERSION}.schema.json",
            "knowledge_document",
        )
    except RuntimeSchemaError as exc:
        raise KnowledgeBlocked("KNOWLEDGE_RECORD_INVALID", str(exc)) from exc


def _required_text(value: Any, field: str, code: str, *, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise KnowledgeBlocked(code, f"a document requires a non-empty {field}")
    text = " ".join(value.split())
    if len(text) > limit:
        raise KnowledgeBlocked(code, f"{field} exceeds {limit} characters")
    return text


def _clean_tags(tags: Any) -> list[str]:
    if tags is None:
        return []
    if not isinstance(tags, (list, tuple)):
        raise KnowledgeBlocked("KNOWLEDGE_TAGS_INVALID", "tags must be a list of strings")
    cleaned: list[str] = []
    for tag in tags:
        if not isinstance(tag, str) or not tag.strip():
            raise KnowledgeBlocked("KNOWLEDGE_TAGS_INVALID", "every tag must be a non-empty string")
        value = " ".join(tag.split())[:100]
        if value not in cleaned:
            cleaned.append(value)
    if len(cleaned) > MAX_TAGS:
        raise KnowledgeBlocked(
            "KNOWLEDGE_TAGS_INVALID", f"at most {MAX_TAGS} tags; got {len(cleaned)}",
        )
    return sorted(cleaned)


def _optional_timestamp(value: Any) -> str | None:
    """Normalize an optional caller-supplied date to the runtime's fixed UTC form.

    A bare ``YYYY-MM-DD`` is accepted and read as midnight UTC — an operator filing a daily
    analysis types a date, not an instant, and refusing that would be pedantry that costs the
    field its usefulness. Anything else must already be RFC3339.
    """
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise KnowledgeBlocked(
            "KNOWLEDGE_DOCUMENT_DATE_INVALID", "document_date must be a non-empty string or null",
        )
    text = value.strip()
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        text = f"{text}T00:00:00Z"
    try:
        return timeutil.format_iso(timeutil.parse_iso(text))
    except ValueError as exc:
        raise KnowledgeBlocked(
            "KNOWLEDGE_DOCUMENT_DATE_INVALID",
            f"document_date {value!r} is not an RFC3339 UTC timestamp or a YYYY-MM-DD date",
        ) from exc


def _find_by_hash(rows: list[dict[str, Any]], content_sha256: str) -> dict[str, Any] | None:
    for row in reversed(rows):
        if row.get("content_sha256") == content_sha256 and not row.get("superseded_by"):
            return row
    return None


def _find_live_by_source(rows: list[dict[str, Any]], source: str) -> dict[str, Any] | None:
    """The newest non-superseded row for ``source``, if any.

    Source, not title: the title is prose the caller may reword between filings, while the
    source is the thing being re-filed. Keying supersession on the title would leave two live
    copies of one document the first time someone fixed a typo in it.
    """
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        document_id = row.get("document_id")
        if isinstance(document_id, str) and document_id:
            latest[document_id] = row
    for row in reversed(list(latest.values())):
        if row.get("source") == source and not row.get("superseded_by"):
            return row
    return None

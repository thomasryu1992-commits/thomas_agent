"""R3 Read-only Tool — web search (foundation).

A read-only web-search tool abstraction plus an executor that records the use as
tamper-evident evidence: tool id/version/class, the query and its input hash, the
returned hits with their **source**, an output hash, latency, and the read-only scope.
Fail-closed on an empty query, tool error/timeout, or a result that leaves the read-only
scope. This is the tool analog of the model provider: ``MockSearchTool`` is deterministic
and network-free, so the tool path can be built and tested before the real search adapter
and the tool-execution/network gate exist.

A real web-search adapter (network) and the full governed tool_request flow / tool
activation are subsequent, gated increments — nothing here performs a network call or
enables a registry tool.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from runtime.read_only_kernel import integrity

from .errors import ToolBlocked, ToolError

SEARCH_TOOL_ID = "search.readonly"
SEARCH_TOOL_VERSION = "0.1.0"
SEARCH_TOOL_CLASS = "read"
MAX_QUERY_CHARS = 2000


@dataclass
class SearchHit:
    title: str
    url: str
    snippet: str
    source: str


@dataclass
class SearchResult:
    query: str
    hits: list[SearchHit]
    tool_id: str = SEARCH_TOOL_ID
    tool_version: str = SEARCH_TOOL_VERSION
    latency_ms: int = 0


@runtime_checkable
class SearchTool(Protocol):
    tool_id: str
    tool_version: str

    def search(self, query: str, *, max_results: int, timeout_seconds: int) -> SearchResult: ...


class MockSearchTool:
    """Deterministic, network-free search tool for tests and pre-gate pipeline runs."""

    tool_id = SEARCH_TOOL_ID
    tool_version = SEARCH_TOOL_VERSION

    def search(self, query: str, *, max_results: int, timeout_seconds: int) -> SearchResult:
        hits = [
            SearchHit(
                title=f"Mock result {i + 1} for: {query[:40]}",
                url=f"https://example.invalid/mock/{i + 1}",
                snippet="Deterministic mock snippet; not a real search result.",
                source="mock.search",
            )
            for i in range(min(max_results, 3))
        ]
        return SearchResult(query=query, hits=hits, latency_ms=0)


def _require_query(query: Any) -> str:
    if not isinstance(query, str) or not query.strip():
        raise ToolBlocked("EMPTY_QUERY", "search query must be a non-empty string")
    if len(query) > MAX_QUERY_CHARS:
        raise ToolBlocked("QUERY_TOO_LONG", f"search query exceeds {MAX_QUERY_CHARS} characters")
    return query


def run_search(
    query: str,
    *,
    tool: SearchTool,
    now: str,
    max_results: int = 5,
    timeout_seconds: int = 10,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Run one read-only search. Returns ``(hits, tool_use_record)``.

    ``hits`` is a list of ``{title, url, snippet, source}`` for downstream use; the
    record captures the tool identity, the query + input hash, the results + output hash,
    latency, and the read-only scope. Fails closed (``ToolBlocked``) on an invalid query,
    a tool error/timeout, or a result that is not read-only.
    """
    query = _require_query(query)
    try:
        result = tool.search(query, max_results=max_results, timeout_seconds=timeout_seconds)
    except (ToolError, TimeoutError) as exc:
        raise ToolBlocked("TOOL_ERROR", str(exc)) from exc

    hits = [
        {"title": h.title, "url": h.url, "snippet": h.snippet, "source": h.source}
        for h in result.hits
        if isinstance(h, SearchHit) and h.url and h.source
    ]
    input_sha256 = integrity.sha256_record({"tool_id": tool.tool_id, "query": query})
    output_sha256 = integrity.sha256_record({"hits": hits})
    record = {
        "tool_id": tool.tool_id,
        "tool_version": tool.tool_version,
        "tool_class": SEARCH_TOOL_CLASS,
        "operation": "search",
        "query": query,
        "input_sha256": input_sha256,
        "result_count": len(hits),
        "sources": sorted({h["source"] for h in hits}),
        "output_sha256": output_sha256,
        "latency_ms": int(result.latency_ms),
        "read_only": True,
        "external_action": False,
        "created_at": now,
    }
    return hits, record

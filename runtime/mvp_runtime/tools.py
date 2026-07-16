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

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from runtime.read_only_kernel import integrity

from . import safety_gate, timeutil
from .errors import ToolBlocked, ToolError
from .safety_gate import NETWORK_ACCESS, Authorization

SEARCH_TOOL_ID = "search.readonly"
SEARCH_TOOL_VERSION = "0.1.0"
SEARCH_TOOL_CLASS = "read"
MAX_QUERY_CHARS = 2000

# Opting into the real network-backed search tool + its backend. Like the model
# provider, the env var alone is NOT sufficient: the Safety-Flag Gate must authorize
# network_access before a network-capable tool is ever built (see select_search_tool).
SEARCH_TOOL_ENV = "MVP_SEARCH_TOOL"
BRAVE_SEARCH = "brave_search"
# A read-only search crosses the network but never invokes a model, so it needs only
# the network_access safety flag (not model_invocation).
_NETWORK_FLAGS = (NETWORK_ACCESS,)


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
    network_egress = False  # deterministic, in-process; no outbound call

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
        # Whether this search crossed the network boundary (mock=False, real adapter=True).
        "network_egress": bool(getattr(tool, "network_egress", False)),
        "created_at": now,
    }
    return hits, record


def select_search_tool(*, now: str | None = None, root: Path | None = None) -> SearchTool:
    """Choose the read-only search tool — the enforced Safety-Flag Gate chokepoint.

    Defaults to the deterministic, network-free ``MockSearchTool`` (no gate needed; it
    performs no network I/O). A real network-backed tool is returned ONLY when both (a)
    the caller opts in via ``MVP_SEARCH_TOOL=brave_search`` AND (b) the Safety-Flag Gate
    authorizes ``network_access`` against a local, integrity-checked activation record.
    The env var alone is NOT sufficient: with no valid activation this fails closed
    (:class:`SafetyGateBlocked`) rather than silently opening a network path.

    This is the search analog of ``providers.select_provider``, and both CLIs select
    their search tool through it (``cli.py``, ``operator_cli.py``).
    """
    choice = os.environ.get(SEARCH_TOOL_ENV, "").strip().lower()
    if choice != BRAVE_SEARCH:
        return MockSearchTool()

    # Opted into a network-capable tool — must pass the gate before it is even built.
    authorization = safety_gate.authorize(
        _NETWORK_FLAGS, provider_id=BRAVE_SEARCH, now=now or timeutil.utc_now_iso(), root=root
    )
    return WebSearchTool(authorization=authorization)


class WebSearchTool:
    """Real web search via the Brave Search API (read-only).

    Makes an outbound HTTPS GET and therefore requires the Safety-Flag Gate to be open
    (explicit Thomas approval + a versioned governance update enabling network_access +
    audit). Inert until its API-key env var is set and it is explicitly selected —
    nothing here runs on the default MVP path (which uses ``MockSearchTool``). The backend
    is swappable: only ``_ENDPOINT``/``_parse`` and the provider id are Brave-specific.

    Secret handling: the API key is read from ``api_key_env`` **by name** at call time and
    sent in a request header — never stored, logged, or echoed in an error. The query goes
    in the URL query string (that is the API contract for search); no secret ever does.
    """

    tool_id = SEARCH_TOOL_ID
    tool_version = "0.1.0-brave"
    provider_id = BRAVE_SEARCH
    network_egress = True  # makes an outbound HTTPS call — recorded as network egress
    _ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
    _MAX_COUNT = 20  # Brave's per-request result cap

    def __init__(
        self,
        *,
        api_key_env: str = "BRAVE_SEARCH_API_KEY",
        authorization: Authorization | None = None,
    ):
        self._api_key_env = api_key_env  # the NAME of the env var, never the value
        # Egress authorization from the Safety-Flag Gate. Without it, search() refuses to
        # open a socket — so a directly-constructed tool cannot bypass the gate.
        self._authorization = authorization

    def search(self, query: str, *, max_results: int, timeout_seconds: int) -> SearchResult:
        # Chokepoint: re-verify authorization at the moment of egress (defense in depth).
        safety_gate.assert_authorization(
            self._authorization,
            required_flags=_NETWORK_FLAGS,
            provider_id=self.provider_id,
            now=timeutil.utc_now_iso(),
        )
        api_key = os.environ.get(self._api_key_env)
        if not api_key:
            raise ToolError("NO_API_KEY", f"environment variable {self._api_key_env} is not set")

        count = max(1, min(int(max_results), self._MAX_COUNT))
        params = urllib.parse.urlencode({"q": query, "count": count})
        request = urllib.request.Request(
            f"{self._ENDPOINT}?{params}",
            method="GET",
            headers={"Accept": "application/json", "X-Subscription-Token": api_key},
        )
        try:
            with urllib.request.urlopen(request, timeout=int(timeout_seconds)) as response:
                raw = response.read().decode("utf-8")
        except (TimeoutError, urllib.error.URLError):
            # Deliberately generic — never echo the URL or key.
            raise ToolError("TOOL_TRANSPORT", "search request failed or timed out") from None

        return self._parse(query, raw, count)

    def _parse(self, query: str, raw: str, count: int) -> SearchResult:
        try:
            data: dict[str, Any] = json.loads(raw)
            results = data["web"]["results"]
        except (KeyError, IndexError, ValueError, TypeError):
            raise ToolError("MALFORMED_RESULT", "search backend returned an unparseable response") from None
        if not isinstance(results, list):
            raise ToolError("MALFORMED_RESULT", "search backend returned an unparseable response")

        hits: list[SearchHit] = []
        for item in results[:count]:
            if not isinstance(item, dict):
                continue
            url = item.get("url")
            if not isinstance(url, str) or not url:
                continue
            hits.append(SearchHit(
                title=str(item.get("title", "")),
                url=url,
                snippet=str(item.get("description", "")),
                source=self.provider_id,
            ))
        return SearchResult(query=query, hits=hits, tool_version=self.tool_version, latency_ms=0)

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
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

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
TAVILY_SEARCH = "tavily_search"
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
    the caller opts in via ``MVP_SEARCH_TOOL=brave_search`` or ``MVP_SEARCH_TOOL=
    tavily_search`` AND (b) the Safety-Flag Gate authorizes ``network_access`` against
    that backend's own local, integrity-checked activation record. The env var alone is
    NOT sufficient: with no valid activation this fails closed
    (:class:`SafetyGateBlocked`) rather than silently opening a network path.

    This is the search analog of ``providers.select_provider``, and both CLIs select
    their search tool through it (``cli.py``, ``operator_cli.py``). The shared
    ``safety_gate.select_gated`` enforces the ordering: no network-capable tool is
    constructed until the gate has opened. One backend at a time — a search failover
    chain was considered and deliberately not built (search degrades instead; see
    ``degraded_search_record``).
    """
    if os.environ.get(SEARCH_TOOL_ENV, "").strip().lower() == TAVILY_SEARCH:
        return safety_gate.select_gated(
            env_var=SEARCH_TOOL_ENV,
            opt_in_value=TAVILY_SEARCH,
            flags=_NETWORK_FLAGS,
            provider_id=TAVILY_SEARCH,
            default_factory=MockSearchTool,
            gated_factory=lambda authorization: TavilySearchTool(authorization=authorization),
            now=now,
            root=root,
        )
    return safety_gate.select_gated(
        env_var=SEARCH_TOOL_ENV,
        opt_in_value=BRAVE_SEARCH,
        flags=_NETWORK_FLAGS,
        provider_id=BRAVE_SEARCH,
        default_factory=MockSearchTool,
        gated_factory=lambda authorization: WebSearchTool(authorization=authorization),
        now=now,
        root=root,
    )


def degraded_search_record(tool: SearchTool, query: str, reason_code: str, *, now: str) -> dict[str, Any]:
    """The tool_use record for a search whose backend failed — recorded, never silent.

    Search is enrichment, not the task: a quota-exhausted or unreachable backend must not
    block the analysis it merely decorates (the R7.2 triage-degradation precedent, and the
    explicit Thomas decision behind the Tavily rollout: exhausting the free tier degrades
    to a no-evidence month, it never becomes a paid call or a dead agent). The record has
    the same shape as a successful ``run_search`` record — zero hits — plus ``degraded``
    and the failure's ``reason_code``, so the ledger and the audit trail say exactly why
    this run analyzed without live evidence."""
    return {
        "tool_id": getattr(tool, "tool_id", SEARCH_TOOL_ID),
        "tool_version": getattr(tool, "tool_version", SEARCH_TOOL_VERSION),
        "tool_class": SEARCH_TOOL_CLASS,
        "operation": "search",
        "query": query,
        "input_sha256": integrity.sha256_record({"tool_id": getattr(tool, "tool_id", SEARCH_TOOL_ID), "query": query}),
        "result_count": 0,
        "sources": [],
        "output_sha256": integrity.sha256_record({"hits": []}),
        "latency_ms": 0,
        "read_only": True,
        "external_action": False,
        # Capability, as in run_search: the failed attempt was made by a network-capable
        # tool even though no successful egress happened.
        "network_egress": bool(getattr(tool, "network_egress", False)),
        "degraded": True,
        "degraded_reason_code": reason_code,
        "created_at": now,
    }


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
        # Measure the real round trip; a hard-coded 0 is a metric that is only ever wrong
        # (see providers.HostedProvider.generate). Monotonic, so a clock adjustment
        # mid-call cannot produce a negative duration.
        started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=int(timeout_seconds)) as response:
                raw = response.read().decode("utf-8")
        except (TimeoutError, urllib.error.URLError):
            # Deliberately generic — never echo the URL or key.
            raise ToolError("TOOL_TRANSPORT", "search request failed or timed out") from None
        latency_ms = int((time.monotonic() - started) * 1000)

        return self._parse(query, raw, count, latency_ms=latency_ms)

    def _parse(self, query: str, raw: str, count: int, *, latency_ms: int = 0) -> SearchResult:
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
        return SearchResult(query=query, hits=hits, tool_version=self.tool_version, latency_ms=latency_ms)


class TavilySearchTool:
    """Real web search via the Tavily Search API (read-only).

    The free backend chosen 2026-07-21 after Brave dropped its free tier for new users:
    Tavily's Researcher plan is recurring free (1,000 credits/month, no payment method),
    which keeps the "free hosted APIs only" locked decision intact. The response returns
    each result's title/url/content snippet, which maps directly onto ``SearchHit``.

    Same gate posture as :class:`WebSearchTool`: outbound HTTPS POST, so the Safety-Flag
    Gate must be open for the ``tavily_search`` provider; inert until its API-key env var
    is set and it is explicitly selected. Secret handling: the API key is read from
    ``api_key_env`` **by name** at call time and sent in the Authorization header — never
    stored, logged, or echoed in an error. The query travels in the request body.
    """

    tool_id = SEARCH_TOOL_ID
    tool_version = "0.1.0-tavily"
    provider_id = TAVILY_SEARCH
    network_egress = True  # makes an outbound HTTPS call — recorded as network egress
    _ENDPOINT = "https://api.tavily.com/search"
    _MAX_COUNT = 20  # Tavily's per-request result cap

    def __init__(
        self,
        *,
        api_key_env: str = "TAVILY_API_KEY",
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
        body = json.dumps({"query": query, "max_results": count}).encode("utf-8")
        request = urllib.request.Request(
            self._ENDPOINT,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        )
        started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=int(timeout_seconds)) as response:
                raw = response.read().decode("utf-8")
        except (TimeoutError, urllib.error.URLError):
            # Deliberately generic — never echo the URL or key. A quota-exhausted 4xx
            # lands here too; the pipeline degrades the run rather than blocking it.
            raise ToolError("TOOL_TRANSPORT", "search request failed or timed out") from None
        latency_ms = int((time.monotonic() - started) * 1000)

        return self._parse(query, raw, count, latency_ms=latency_ms)

    def _parse(self, query: str, raw: str, count: int, *, latency_ms: int = 0) -> SearchResult:
        try:
            data: dict[str, Any] = json.loads(raw)
            results = data["results"]
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
                snippet=str(item.get("content", "")),
                source=self.provider_id,
            ))
        return SearchResult(query=query, hits=hits, tool_version=self.tool_version, latency_ms=latency_ms)

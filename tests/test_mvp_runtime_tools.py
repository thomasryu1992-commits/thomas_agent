"""R3 read-only search tool tests.

MockSearchTool needs no network. The real ``WebSearchTool`` is behind the Safety-Flag
Gate: search() refuses to open a socket without a valid Authorization, and
select_search_tool() fails closed unless a local activation record authorizes the
network capability. The HTTP path is exercised with a supplied Authorization and a fully
mocked urlopen (no real network)."""

from __future__ import annotations

import json
import urllib.error

import pytest

from runtime.mvp_runtime.errors import SafetyGateBlocked, ToolBlocked, ToolError
from runtime.mvp_runtime import safety_gate
from runtime.mvp_runtime.safety_gate import NETWORK_ACCESS, Authorization, build_activation_record
from runtime.mvp_runtime.tools import (
    SEARCH_TOOL_ENV,
    MockSearchTool,
    SearchResult,
    TavilySearchTool,
    WebSearchTool,
    degraded_search_record,
    run_search,
    select_search_tool,
)

NOW = "2026-07-15T09:00:00Z"

API_ENV = "BRAVE_SEARCH_API_KEY"

# A granted egress authorization (as select_search_tool would produce after the gate passes).
_AUTH = Authorization(
    flags=(NETWORK_ACCESS,),
    provider_id="brave_search",
    activation_sha256="sha256:test",
    expires_at="2999-01-01T00:00:00Z",
    evidence_ref=".runtime_governance_state/evidence.md",
)


class _ErrorTool:
    tool_id, tool_version = "search.readonly", "0.1.0"

    def search(self, query, *, max_results, timeout_seconds):
        raise ToolError("BOOM", "search backend unavailable")


class _TimeoutTool:
    tool_id, tool_version = "search.readonly", "0.1.0"

    def search(self, query, *, max_results, timeout_seconds):
        raise TimeoutError("deadline exceeded")


def test_search_returns_hits_and_evidence_record():
    hits, record = run_search("구독형 반려동물 사료 시장", tool=MockSearchTool(), now=NOW)
    assert hits and all({"title", "url", "snippet", "source"} <= set(h) for h in hits)
    assert record["tool_id"] == "search.readonly" and record["tool_class"] == "read"
    assert record["read_only"] is True and record["external_action"] is False
    # The record carries the hits themselves (what output_sha256 hashes): the ledger used
    # to hold only the hash, so an analysis citing [S1] could never be resolved back.
    assert record["hits"] == hits
    assert record["input_sha256"].startswith("sha256:") and record["output_sha256"].startswith("sha256:")
    assert record["result_count"] == len(hits)
    assert record["sources"] == ["mock.search"]


def test_deterministic():
    a = run_search("q", tool=MockSearchTool(), now=NOW)
    b = run_search("q", tool=MockSearchTool(), now=NOW)
    assert a == b


@pytest.mark.parametrize("bad", ["", "   ", None])
def test_empty_query_blocks(bad):
    with pytest.raises(ToolBlocked) as exc:
        run_search(bad, tool=MockSearchTool(), now=NOW)
    assert exc.value.reason_code == "EMPTY_QUERY"


def test_overlong_query_blocks():
    with pytest.raises(ToolBlocked) as exc:
        run_search("x" * 2001, tool=MockSearchTool(), now=NOW)
    assert exc.value.reason_code == "QUERY_TOO_LONG"


def test_tool_error_fails_closed():
    with pytest.raises(ToolBlocked) as exc:
        run_search("q", tool=_ErrorTool(), now=NOW)
    assert exc.value.reason_code == "TOOL_ERROR"


def test_tool_timeout_fails_closed():
    with pytest.raises(ToolBlocked) as exc:
        run_search("q", tool=_TimeoutTool(), now=NOW)
    assert exc.value.reason_code == "TOOL_ERROR"


def test_max_results_bounds_hits():
    hits, _ = run_search("q", tool=MockSearchTool(), now=NOW, max_results=1)
    assert len(hits) == 1


def test_mock_evidence_records_no_network_egress():
    _, record = run_search("q", tool=MockSearchTool(), now=NOW)
    assert record["network_egress"] is False


# --- Safety-Flag Gate wiring in select_search_tool --------------------------

def test_select_search_tool_defaults_to_mock(monkeypatch):
    monkeypatch.delenv(SEARCH_TOOL_ENV, raising=False)
    assert isinstance(select_search_tool(), MockSearchTool)


def test_select_real_tool_without_activation_fails_closed(monkeypatch, tmp_path):
    # Opting in via the env var alone must NOT open a network path — no activation record.
    monkeypatch.setenv(SEARCH_TOOL_ENV, "brave_search")
    with pytest.raises(SafetyGateBlocked) as exc:
        select_search_tool(now="2026-07-15T00:00:00Z", root=tmp_path)
    assert exc.value.reason_code == "ACTIVATION_MISSING"


def test_select_real_tool_with_activation_returns_web_tool(monkeypatch, tmp_path):
    state = tmp_path / ".runtime_governance_state"
    state.mkdir()
    evidence_rel = ".runtime_governance_state/search_gate_approval.md"
    (tmp_path / evidence_rel).write_text("operator decision evidence", encoding="utf-8")
    record = build_activation_record(
        flags=[NETWORK_ACCESS],
        provider_id="brave_search",
        activated_at="2026-07-01T00:00:00Z",
        expires_at="2026-12-31T23:59:59Z",
        evidence_ref=evidence_rel,
        authority_level="P1",
    )
    path = safety_gate.activation_path(tmp_path, "brave_search")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record), encoding="utf-8")

    monkeypatch.setenv(SEARCH_TOOL_ENV, "brave_search")
    tool = select_search_tool(now="2026-07-15T00:00:00Z", root=tmp_path)
    assert isinstance(tool, WebSearchTool)


# --- Egress self-guard + HTTP parsing in WebSearchTool ----------------------

def test_web_search_without_authorization_fails_closed(monkeypatch):
    monkeypatch.setenv(API_ENV, "test-key-not-real")
    with pytest.raises(SafetyGateBlocked) as exc:
        WebSearchTool().search("q", max_results=5, timeout_seconds=10)
    assert exc.value.reason_code == "NOT_AUTHORIZED"


def test_web_search_no_api_key_fails_closed(monkeypatch):
    monkeypatch.delenv(API_ENV, raising=False)
    with pytest.raises(ToolError) as exc:
        WebSearchTool(authorization=_AUTH).search("q", max_results=5, timeout_seconds=10)
    assert exc.value.reason_code == "NO_API_KEY"


class _FakeResp:
    def __init__(self, payload: str):
        self._payload = payload.encode("utf-8")

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _patch_urlopen(monkeypatch, payload_or_exc):
    def fake_urlopen(request, timeout):
        if isinstance(payload_or_exc, Exception):
            raise payload_or_exc
        return _FakeResp(payload_or_exc)
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)


_BRAVE_RESPONSE = json.dumps({
    "web": {"results": [
        {"title": "Result A", "url": "https://example.com/a", "description": "snippet a"},
        {"title": "Result B", "url": "https://example.com/b", "description": "snippet b"},
    ]},
})


def test_web_search_happy_path_parses_hits(monkeypatch):
    monkeypatch.setenv(API_ENV, "test-key-not-real")
    _patch_urlopen(monkeypatch, _BRAVE_RESPONSE)
    result = WebSearchTool(authorization=_AUTH).search("시장 조사", max_results=5, timeout_seconds=10)
    assert isinstance(result, SearchResult)
    assert [h.url for h in result.hits] == ["https://example.com/a", "https://example.com/b"]
    assert all(h.source == "brave_search" for h in result.hits)


def test_web_search_integrates_with_run_search_evidence(monkeypatch):
    monkeypatch.setenv(API_ENV, "k")
    _patch_urlopen(monkeypatch, _BRAVE_RESPONSE)
    hits, record = run_search("q", tool=WebSearchTool(authorization=_AUTH), now=NOW)
    assert record["network_egress"] is True
    assert record["sources"] == ["brave_search"]
    assert record["result_count"] == len(hits) == 2


def test_web_search_transport_error_fails_closed_without_leaking(monkeypatch):
    monkeypatch.setenv(API_ENV, "secret-value")
    _patch_urlopen(monkeypatch, urllib.error.URLError("connection refused"))
    with pytest.raises(ToolError) as exc:
        WebSearchTool(authorization=_AUTH).search("x", max_results=5, timeout_seconds=5)
    assert exc.value.reason_code == "TOOL_TRANSPORT"
    assert "secret-value" not in str(exc.value)  # the key must never leak


def test_web_search_malformed_response_fails_closed(monkeypatch):
    monkeypatch.setenv(API_ENV, "k")
    _patch_urlopen(monkeypatch, '{"unexpected": "shape"}')
    with pytest.raises(ToolError) as exc:
        WebSearchTool(authorization=_AUTH).search("x", max_results=5, timeout_seconds=5)
    assert exc.value.reason_code == "MALFORMED_RESULT"


# --- TavilySearchTool (the free backend chosen 2026-07-21) --------------------

TAVILY_API_ENV = "TAVILY_API_KEY"

_TAVILY_AUTH = Authorization(
    flags=(NETWORK_ACCESS,),
    provider_id="tavily_search",
    activation_sha256="sha256:test",
    expires_at="2999-01-01T00:00:00Z",
    evidence_ref=".runtime_governance_state/evidence.md",
)

_TAVILY_RESPONSE = json.dumps({
    "query": "시장 조사",
    "results": [
        {"title": "Result A", "url": "https://example.com/a", "content": "본문 요약 a", "score": 0.9},
        {"title": "Result B", "url": "https://example.com/b", "content": "본문 요약 b", "score": 0.7},
    ],
})


def test_tavily_without_authorization_fails_closed(monkeypatch):
    monkeypatch.setenv(TAVILY_API_ENV, "test-key-not-real")
    with pytest.raises(SafetyGateBlocked) as exc:
        TavilySearchTool().search("q", max_results=5, timeout_seconds=10)
    assert exc.value.reason_code == "NOT_AUTHORIZED"


def test_tavily_no_api_key_fails_closed(monkeypatch):
    monkeypatch.delenv(TAVILY_API_ENV, raising=False)
    with pytest.raises(ToolError) as exc:
        TavilySearchTool(authorization=_TAVILY_AUTH).search("q", max_results=5, timeout_seconds=10)
    assert exc.value.reason_code == "NO_API_KEY"


def test_tavily_happy_path_parses_hits_including_korean(monkeypatch):
    monkeypatch.setenv(TAVILY_API_ENV, "test-key-not-real")
    _patch_urlopen(monkeypatch, _TAVILY_RESPONSE)
    result = TavilySearchTool(authorization=_TAVILY_AUTH).search("시장 조사", max_results=5, timeout_seconds=10)
    assert isinstance(result, SearchResult)
    assert [h.url for h in result.hits] == ["https://example.com/a", "https://example.com/b"]
    assert result.hits[0].snippet == "본문 요약 a"     # Tavily's `content` maps to snippet
    assert all(h.source == "tavily_search" for h in result.hits)


def test_tavily_transport_error_fails_closed_without_leaking(monkeypatch):
    monkeypatch.setenv(TAVILY_API_ENV, "secret-value")
    _patch_urlopen(monkeypatch, urllib.error.URLError("connection refused"))
    with pytest.raises(ToolError) as exc:
        TavilySearchTool(authorization=_TAVILY_AUTH).search("x", max_results=5, timeout_seconds=5)
    assert exc.value.reason_code == "TOOL_TRANSPORT"
    assert "secret-value" not in str(exc.value)


def test_tavily_malformed_response_fails_closed(monkeypatch):
    monkeypatch.setenv(TAVILY_API_ENV, "k")
    _patch_urlopen(monkeypatch, '{"unexpected": "shape"}')
    with pytest.raises(ToolError) as exc:
        TavilySearchTool(authorization=_TAVILY_AUTH).search("x", max_results=5, timeout_seconds=5)
    assert exc.value.reason_code == "MALFORMED_RESULT"


def test_select_tavily_without_activation_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setenv(SEARCH_TOOL_ENV, "tavily_search")
    with pytest.raises(SafetyGateBlocked) as exc:
        select_search_tool(now="2026-07-15T00:00:00Z", root=tmp_path)
    assert exc.value.reason_code == "ACTIVATION_MISSING"


def test_select_tavily_with_activation_returns_tavily_tool(monkeypatch, tmp_path):
    state = tmp_path / ".runtime_governance_state"
    state.mkdir()
    evidence_rel = ".runtime_governance_state/search_gate_approval.md"
    (tmp_path / evidence_rel).write_text("operator decision evidence", encoding="utf-8")
    record = build_activation_record(
        flags=[NETWORK_ACCESS],
        provider_id="tavily_search",
        activated_at="2026-07-01T00:00:00Z",
        expires_at="2026-12-31T23:59:59Z",
        evidence_ref=evidence_rel,
        authority_level="P1",
    )
    path = safety_gate.activation_path(tmp_path, "tavily_search")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record), encoding="utf-8")

    monkeypatch.setenv(SEARCH_TOOL_ENV, "tavily_search")
    tool = select_search_tool(now="2026-07-15T00:00:00Z", root=tmp_path)
    assert isinstance(tool, TavilySearchTool)


# --- degraded search record (search is enrichment, not the task) --------------

def test_degraded_search_record_shape():
    record = degraded_search_record(MockSearchTool(), "시장 조사", "TOOL_ERROR", now=NOW)
    assert record["degraded"] is True
    assert record["degraded_reason_code"] == "TOOL_ERROR"
    assert record["result_count"] == 0 and record["sources"] == []
    assert record["read_only"] is True and record["external_action"] is False
    # Same identity fields as a successful run_search record, so readers see one shape.
    _, success = run_search("시장 조사", tool=MockSearchTool(), now=NOW)
    assert set(record) - set(success) == {"degraded", "degraded_reason_code"}

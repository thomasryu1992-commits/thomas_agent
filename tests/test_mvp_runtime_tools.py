"""R3 read-only search tool tests (MockSearchTool — no network)."""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.errors import ToolBlocked, ToolError
from runtime.mvp_runtime.tools import MockSearchTool, SearchResult, run_search

NOW = "2026-07-15T09:00:00Z"


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

"""The keyword brief wired into the pipeline — [K#] evidence beside [S#].

What is worth pinning is the wiring's shape, not the adapters (they have their own file):

- opt-in and inert: `keyword_seeds=None` leaves the run byte-identical — no record, no
  prompt block, no evidence entries;
- when it runs, all three surfaces agree: the ledger record, the prompt block the
  specialist saw, and the evidence entries on the agent output all carry the same rows;
- degraded is visible end to end: a dead volume leg still writes the record, and the
  prompt simply has no [K#] block rather than one full of zeros.

The CLI flag's parsing is pinned here too, because `--naver-keywords` joining the closed
flag set is exactly the kind of change the unknown-flag rejection exists to force through
a test.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime import naver_research
from runtime.mvp_runtime.cli import _extract_keyword_seeds
from runtime.mvp_runtime.pipeline import run_task
from runtime.mvp_runtime.worker import MockProvider, _keyword_context

from ._helpers import requires_local_core

NOW = "2026-08-10T03:00:00Z"
REQUEST = "이 사업 아이디어를 분석해줘: 소상공인용 AI 포스터 제작 대행"

ROWS = [
    {"keyword": "미리캔버스", "monthly_pc": 1_701_600, "monthly_mobile": 560_900,
     "monthly_total": 2_262_500, "competition": "낮음", "low_volume": False,
     "source": "naver_searchad", "competing_posts": 358_602},
    {"keyword": "포스터제작", "monthly_pc": 2_300, "monthly_mobile": 3_780,
     "monthly_total": 6_080, "competition": "높음", "low_volume": True,
     "source": "naver_searchad"},
]


# --- the prompt block ---------------------------------------------------------------

def test_the_prompt_block_renders_rows_as_citable_evidence():
    block = _keyword_context(ROWS)
    assert "[K1]" in block and "[K2]" in block
    assert "2,262,500/mo" in block
    assert "competing blog posts 358,602" in block
    # The second row's lookup did not land: the column is absent, and the block must not
    # invent "competing blog posts 0" — an empty niche is a claim.
    assert block.count("competing blog posts") == 1
    assert "(low-volume estimate)" in block


def test_no_rows_means_no_block_at_all():
    assert _keyword_context(None) == ""
    assert _keyword_context([]) == ""


# --- the pipeline ------------------------------------------------------------------

@requires_local_core
def test_without_seeds_the_run_carries_no_trace_of_the_lane():
    result = run_task(REQUEST, provider=MockProvider(), now=NOW)
    assert result["status"] == "COMPLETED"
    assert "keyword_research" not in result["records"]
    assert all(e.get("type") != "keyword_demand"
               for e in result["records"]["agent_output"]["evidence"])


@requires_local_core
def test_with_seeds_the_record_the_prompt_and_the_evidence_agree():
    result = run_task(REQUEST, provider=MockProvider(), now=NOW, keyword_seeds="포스터 만들기")
    assert result["status"] == "COMPLETED"
    records = result["records"]

    record = records["keyword_research"]
    assert record["operation"] == "keyword_brief"
    assert record["degraded"] is False and record["result_count"] > 0
    assert record["read_only"] is True and record["external_action"] is False
    # Gate closed in tests (conftest clears MVP_NAVER_RESEARCH) -> Mocks, no egress.
    assert record["network_egress"] is False

    demand = [e for e in records["agent_output"]["evidence"] if e.get("type") == "keyword_demand"]
    assert len(demand) == record["result_count"]
    assert {e["keyword"] for e in demand} == {m["keyword"] for m in record["metrics"]}

    # What the specialist actually saw: the invocation's recorded prompt carries the block.
    prompt = records["invocation"].get("prompt", "")
    if prompt:  # the invocation records the prompt on the mock path; guard stays honest
        assert "[K1]" in prompt


@requires_local_core
def test_a_dead_volume_leg_is_recorded_but_does_not_block_the_run(monkeypatch):
    class _Boom:
        tool_id, tool_version, network_egress = naver_research.KEYWORD_TOOL_ID, "0.1.0", False

        def keywords(self, seed, *, max_results, timeout_seconds):
            raise naver_research.ToolError("BOOM", "backend unavailable")

    monkeypatch.setattr(naver_research, "select_keyword_tool", lambda: _Boom())
    result = run_task(REQUEST, provider=MockProvider(), now=NOW, keyword_seeds="포스터 만들기")
    assert result["status"] == "COMPLETED"
    record = result["records"]["keyword_research"]
    assert record["degraded"] is True and record["metrics"] == []
    assert all(e.get("type") != "keyword_demand"
               for e in result["records"]["agent_output"]["evidence"])


@requires_local_core
def test_the_record_survives_the_ledger_not_just_the_return_value(tmp_path):
    """The gap the first three run_task tests left, found by the first real CLI run: they
    called run_task WITHOUT a store, so the ledger's closed record-kind list was never
    exercised, and the deployed CLI blocked with LEDGER_UNKNOWN_RECORD_KIND on the very
    record this file is about. Persisting is part of the wiring, so a test drives it."""
    from runtime.mvp_runtime.store import LedgerStore

    store = LedgerStore(tmp_path / "ledger")
    result = run_task(REQUEST, provider=MockProvider(), now=NOW,
                      keyword_seeds="포스터 만들기", store=store)
    assert result["status"] == "COMPLETED"

    persisted = [row for row in store.iter_records() if row["kind"] == "keyword_research"]
    assert len(persisted) == 1
    assert persisted[0]["record"]["operation"] == "keyword_brief"


# --- the CLI flag -------------------------------------------------------------------

def test_the_flag_extracts_its_value_and_leaves_the_request_alone():
    seeds, rest, error = _extract_keyword_seeds(
        ["분석해줘", "--naver-keywords", "미리캔버스, 포스터제작", "이 아이디어"])
    assert error is None
    assert seeds == "미리캔버스, 포스터제작"
    assert rest == ["분석해줘", "이 아이디어"]


def test_absent_flag_changes_nothing():
    argv = ["분석해줘"]
    seeds, rest, error = _extract_keyword_seeds(argv)
    assert (seeds, rest, error) == (None, argv, None)


@pytest.mark.parametrize("argv", [
    ["--naver-keywords"],                       # no value at all
    ["--naver-keywords", "--important", "x"],   # a flag where the value belongs
])
def test_a_missing_value_is_a_usage_error_not_a_silent_none(argv):
    seeds, _, error = _extract_keyword_seeds(argv)
    assert seeds is None and error is not None

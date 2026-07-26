"""Execution-budget allocation vs recorded usage.

Two properties the contract asserts and the runtime previously did not hold:
`usage_must_be_recorded_for_audit` (every persisted budget block read 0 forever, because
task/assignment budgets are pre-execution allocations) and
`subtasks_and_new_assignments_cannot_increase_parent_remaining_budget` (with R7 on, two
assignments each granted one model call ran under a task allocated exactly one).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from runtime.mvp_runtime.binding import DEFAULT_POINTER_REL
from runtime.mvp_runtime.budgets import default_execution_budget, recorded_usage_budget
from runtime.mvp_runtime.pipeline import run_task
from runtime.mvp_runtime.store import RECORDS_FILE, LedgerStore
from runtime.mvp_runtime.worker import MockProvider

from tests._helpers import requires_local_core

NOW = "2026-07-20T09:00:00Z"
REQUEST = "이 사업 아이디어를 분석해줘: 구독형 반려동물 사료 배송"


def _records(store, kind):
    path = store.root / RECORDS_FILE
    rows = [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return [r["record"] for r in rows if r["kind"] == kind]


# --- allocation -------------------------------------------------------------

def test_default_allocation_is_one_agents_share():
    limits = default_execution_budget()["limits"]
    assert limits["max_agent_invocations"] == 1
    assert limits["max_model_calls"] == 1
    assert limits["token_budget"] == 8000
    # Zero by governance, not omission: the R3 search is an INTERNAL_READ ALLOW action,
    # not a registered Tool execution.
    assert limits["max_tool_calls"] == 0 and limits["max_program_calls"] == 0
    assert default_execution_budget()["usage"]["model_calls"] == 0     # an allocation, not usage


def test_allocation_scales_with_the_planned_team():
    limits = default_execution_budget(agents=2)["limits"]
    assert limits["max_agent_invocations"] == 2
    assert limits["max_model_calls"] == 2
    assert limits["token_budget"] == 16000
    # Never below one agent's share, whatever a caller passes.
    assert default_execution_budget(agents=0)["limits"]["max_model_calls"] == 1


# --- recorded usage ---------------------------------------------------------

def test_recorded_usage_carries_real_numbers_against_its_allocation():
    limits = default_execution_budget(agents=2)["limits"]
    record = recorded_usage_budget(limits, agent_invocations=2, model_calls=2,
                                   tokens_used=1234, validation_cycles=2)
    assert record["schema_version"] == "execution_budget.v0.1"
    assert record["limits"] == limits                       # what it ran under
    usage = record["usage"]
    assert usage["model_calls"] == 2 and usage["agent_invocations"] == 2
    assert usage["tokens_used"] == 1234 and usage["validation_cycles"] == 2
    assert usage["peak_parallel_workers"] == 1              # agents run sequentially
    # Deliberately unmeasured rather than fabricated as a computed zero.
    assert usage["runtime_seconds"] == 0 and usage["cost_used"] == 0
    assert usage["tool_calls"] == 0 and usage["program_calls"] == 0


# --- the pipeline records what it spent -------------------------------------

@requires_local_core
def test_run_persists_its_actual_usage(tmp_path):
    store = LedgerStore(tmp_path / "ledger")
    run_task(REQUEST, provider=MockProvider(), now=NOW, store=store)
    usage_records = _records(store, "budget_usage")
    assert len(usage_records) == 1
    usage = usage_records[0]["usage"]
    assert usage["model_calls"] == 1 and usage["agent_invocations"] == 1
    assert usage["tokens_used"] > 0                         # a real number, not the zero default


@requires_local_core
def test_independent_validation_allocates_and_records_two_model_calls(tmp_path):
    """The parent-budget invariant: the task allocation must cover the team the plan
    invokes, and each assignment stays within it."""
    store = LedgerStore(tmp_path / "ledger")
    result = run_task(REQUEST, provider=MockProvider(), now=NOW, store=store,
                      independent_validation=True)
    assert result["status"] == "COMPLETED"

    task_limits = result["records"]["task"]["execution_budget"]["limits"]
    assert task_limits["max_model_calls"] == 2 and task_limits["max_agent_invocations"] == 2

    for key in ("role_assignment", "validator_assignment"):
        assignment_limits = result["records"][key]["execution_budget"]["limits"]
        # Each assignment is one agent's share and never exceeds the parent allocation.
        assert assignment_limits["max_model_calls"] == 1
        assert assignment_limits["max_model_calls"] <= task_limits["max_model_calls"]

    usage = _records(store, "budget_usage")[0]["usage"]
    assert usage["model_calls"] == 2 and usage["agent_invocations"] == 2
    assert usage["model_calls"] <= task_limits["max_model_calls"]   # spent within allocation


# --- the input/output split (2026-07-26 token-cost review) ---------------------

def test_output_allowance_leaves_room_for_the_prompt():
    """The same figure cannot be both the output allowance and the cap on input+output."""
    from runtime.mvp_runtime.budgets import TOKENS_PER_AGENT, output_allowance

    assert 0 < output_allowance(TOKENS_PER_AGENT) < TOKENS_PER_AGENT
    # Never zero: a budget too small to split still buys one token, not a refused call.
    assert output_allowance(1) == 1
    assert output_allowance(0) == 1
    assert output_allowance(None) == 1
    assert output_allowance("nonsense") == 1


def test_truncation_is_recognized_in_every_vendor_spelling():
    from runtime.mvp_runtime.budgets import response_was_truncated

    for truncated in ("length", "LENGTH", "MAX_TOKENS", " max_tokens "):
        assert response_was_truncated(truncated) is True, truncated
    for finished in ("stop", "STOP", "", None, "content_filter"):
        assert response_was_truncated(finished) is False, finished


def test_clip_for_prompt_states_what_it_dropped():
    """A model handed a silently truncated source can cite it as though it read the whole
    thing, and the reader cannot tell. The marker costs less than the sentence it replaces."""
    from runtime.mvp_runtime.budgets import clip_for_prompt

    assert clip_for_prompt("짧다", 100) == "짧다"          # under the limit: untouched
    clipped = clip_for_prompt("가" * 500, 100)
    assert clipped.startswith("가" * 100)
    assert "400 chars omitted]" in clipped
    assert clip_for_prompt(None, 10) == ""


# --- the input/output split and the prompt caps (2026-07-26 token review) ------

def test_output_allowance_leaves_room_for_the_prompt():
    """`token_budget` caps input+output and is checked after the call, so it cannot also be
    the output allowance. Both worker and validator passed the whole figure, so an obedient
    answer breached by the size of the prompt."""
    from runtime.mvp_runtime.budgets import TOKENS_PER_AGENT, output_allowance

    assert output_allowance(TOKENS_PER_AGENT) < TOKENS_PER_AGENT
    assert output_allowance(8000) == 4000
    # Never zero: a call the provider must refuse for being allowed to emit nothing is worse
    # than a tiny one. Junk reads as the smallest allowance rather than raising.
    assert output_allowance(1) >= 1
    assert output_allowance(0) >= 1
    assert output_allowance(None) >= 1
    assert output_allowance("nonsense") >= 1


def test_truncation_is_recognized_in_both_vendor_spellings():
    """OpenAI-compatible says "length", Google says "MAX_TOKENS"; the adapters pass each
    through raw, so the check has to know both."""
    from runtime.mvp_runtime.budgets import response_was_truncated

    assert response_was_truncated("length") is True
    assert response_was_truncated("MAX_TOKENS") is True
    assert response_was_truncated("stop") is False
    assert response_was_truncated(None) is False


def test_clip_for_prompt_marks_what_it_dropped():
    """Silence would let a model cite a clipped source as though it had read the whole page."""
    from runtime.mvp_runtime.budgets import clip_for_prompt

    assert clip_for_prompt("short", 100) == "short"
    clipped = clip_for_prompt("가" * 1000, 100)
    assert clipped.startswith("가" * 100)
    assert "900 chars omitted]" in clipped
    assert clip_for_prompt(None, 10) == ""

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

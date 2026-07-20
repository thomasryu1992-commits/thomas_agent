"""Single source for the MVP ``execution_budget.v0.1`` allocations and usage.

The task record (intake) and the role assignment (planner) must carry the *same*
budget — a task capped at one model call assigned a role that thinks it may make
more is a silent governance drift. Both previously built a byte-identical dict; they
now call one factory, so a cap can only change in one place.

Caps are conservative: one model call **per planned agent**, no tools/programs.
Free-tier provider => ``cost_budget`` 0, with ``cost_currency`` a required 3-letter
placeholder (per CLAUDE.md), never a spend authorization.

**Allocation vs usage.** ``default_execution_budget`` is an *allocation*, built before
execution, so its ``usage`` block is necessarily zero — that is correct for the task and
assignment records, which are immutable planning evidence. What actually happened is
:func:`recorded_usage_budget`, built after the run from the real counters and persisted as
its own ledger record; the contract's ``usage_must_be_recorded_for_audit`` invariant is
about that record, and before it existed nothing in the ledger ever carried a non-zero
usage number.
"""

from __future__ import annotations

from typing import Any

# Per-agent allocation. A task's allocation scales with the number of agents its plan
# includes (R7 adds the independent validator), because the contract forbids an assignment
# from exceeding the parent task's remaining budget: two assignments granted one model call
# each under a task allocated exactly one is precisely the
# ``subtasks_and_new_assignments_cannot_increase_parent_remaining_budget`` breach.
MODEL_CALLS_PER_AGENT = 1
TOKENS_PER_AGENT = 8000


def default_execution_budget(*, agents: int = 1) -> dict[str, Any]:
    """A fresh ``execution_budget.v0.1`` allocation with zeroed usage.

    ``agents`` is the number of agents the plan will invoke under this budget: 1 for a task
    the specialist handles alone, 2 when R7's independent validator also runs. An
    *assignment* is always a single agent's share, so callers building an assignment leave
    it at 1 and only the task-level allocation scales.
    """
    agents = max(1, int(agents))
    return {
        "schema_version": "execution_budget.v0.1",
        "limits": {
            "max_agent_invocations": agents,
            "max_model_calls": MODEL_CALLS_PER_AGENT * agents,
            # Zero by governance, not by omission: the R3 read-only search is an
            # INTERNAL_READ ALLOW action, not a registered Tool execution (TOOL_REGISTRY
            # keeps `search.readonly` disabled and R3 deliberately avoided `tool_request`).
            # So it consumes no tool-call budget, and `recorded_usage_budget` counts
            # tool_calls the same way — registered Tool executions only.
            "max_tool_calls": 0,
            "max_program_calls": 0,
            "max_revision_cycles": 1,
            "max_validation_cycles": 1,
            "max_retry_count": 1,
            "max_parallel_workers": 1,
            "max_runtime_seconds": 120,
            "token_budget": TOKENS_PER_AGENT * agents,
            "cost_budget": 0,
            "cost_currency": "USD",
        },
        "usage": _zero_usage(),
    }


def _zero_usage() -> dict[str, Any]:
    return {
        "agent_invocations": 0,
        "model_calls": 0,
        "tool_calls": 0,
        "program_calls": 0,
        "revision_cycles": 0,
        "validation_cycles": 0,
        "retry_count": 0,
        "peak_parallel_workers": 0,
        "runtime_seconds": 0,
        "tokens_used": 0,
        "cost_used": 0,
        "cost_currency": "USD",
    }


def recorded_usage_budget(
    limits: dict[str, Any],
    *,
    agent_invocations: int,
    model_calls: int,
    tokens_used: int,
    validation_cycles: int = 1,
) -> dict[str, Any]:
    """What the run actually spent, against the allocation it ran under.

    Satisfies ``usage_must_be_recorded_for_audit``: the task and assignment records are
    allocations built before execution, so their zeroed usage can never answer "what did
    this run spend?". The pipeline persists this as its own ledger record.

    Deliberately **not** recorded: ``runtime_seconds`` and ``cost_used``. Wall-clock would
    make every run's records differ byte-for-byte and the MVP's determinism definition is
    pipeline-determinism plus recorded replay (per-call latency lives on the invocation
    records, which is where a slow provider is actually diagnosable). Cost is not metered
    at all on a free tier whose ``cost_budget`` is 0 — reporting a computed 0 would claim a
    measurement nobody took. ``tool_calls``/``program_calls`` stay 0 for the reason the
    limits do: the read-only search is not a registered Tool execution.
    """
    usage = _zero_usage()
    usage.update({
        "agent_invocations": int(agent_invocations),
        "model_calls": int(model_calls),
        "validation_cycles": int(validation_cycles),
        "peak_parallel_workers": 1,   # the MVP runs agents sequentially, never in parallel
        "tokens_used": int(tokens_used),
        "cost_currency": str(limits.get("cost_currency", "USD")),
    })
    return {
        "schema_version": "execution_budget.v0.1",
        "limits": dict(limits),
        "usage": usage,
    }

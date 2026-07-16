"""Single source for the MVP ``execution_budget.v0.1`` defaults.

The task record (intake) and the role assignment (planner) must carry the *same*
budget — a task capped at one model call assigned a role that thinks it may make
more is a silent governance drift. Both previously built a byte-identical dict; they
now call one factory, so a cap can only change in one place.

Caps are conservative: one specialist model call, no tools/programs. Free-tier
provider => ``cost_budget`` 0, with ``cost_currency`` a required 3-letter
placeholder (per CLAUDE.md), never a spend authorization.
"""

from __future__ import annotations

from typing import Any


def default_execution_budget() -> dict[str, Any]:
    """A fresh ``execution_budget.v0.1`` dict with the MVP default caps and zeroed usage."""
    return {
        "schema_version": "execution_budget.v0.1",
        "limits": {
            "max_agent_invocations": 1,
            "max_model_calls": 1,
            "max_tool_calls": 0,
            "max_program_calls": 0,
            "max_revision_cycles": 1,
            "max_validation_cycles": 1,
            "max_retry_count": 1,
            "max_parallel_workers": 1,
            "max_runtime_seconds": 120,
            "token_budget": 8000,
            "cost_budget": 0,
            "cost_currency": "USD",
        },
        "usage": {
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
        },
    }

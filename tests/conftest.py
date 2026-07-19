"""Shared test configuration.

The suite must behave identically on a bare CI checkout and on the operator's machine —
the one with real safety-flag activations and gate env vars exported. Without this
isolation, a test that exercises a real entry point (the CLI, the operator loop) would
select the *capable* implementation there: live model calls burning quota, real network
egress, real workspace writes. Same class of leak as the ledger-pollution fix (cd520b5),
closed at the suite root instead of per test file.
"""

from __future__ import annotations

import pytest

# Every Safety-Flag Gate opt-in env var. Tests that exercise a gate opt-in set the var
# themselves via monkeypatch.setenv, which applies after this autouse teardown-safe clear.
_GATE_ENV_VARS = (
    "MVP_HOSTED_PROVIDER",
    "MVP_SEARCH_TOOL",
    "MVP_OPERATOR_CHANNEL",
    "MVP_WORKSPACE_WRITER",
    "MVP_APPROVAL_CONSUMPTION",
)


@pytest.fixture(autouse=True)
def _isolate_safety_gate_env(monkeypatch):
    """No test inherits the operator's gate opt-ins from the environment."""
    for var in _GATE_ENV_VARS:
        monkeypatch.delenv(var, raising=False)

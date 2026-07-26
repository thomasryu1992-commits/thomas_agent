"""Coverage gate: every CLI that writes governed state refuses a host-side root run.

`state_guard` shipped with one adopter, `activate_safety_flag.py`. The other state-writing
CLIs kept the failure it was written for: run on the host as root, they succeed, print a happy
summary, and leave files under `.runtime_governance_state/` that the uid-10001 service can
never write again — surfacing later, in a different process, with nothing pointing back.

It is not hypothetical for the money path. On 2026-07-26 a `binance_futures_account`
activation, a `live_trading` activation, a registered budget and a canary registry row were all
written root-owned from a host shell, and a separate session had to find them and `chown` them
back. `activate_safety_flag.py` gained the guard from that incident; the four other writers in
the same session's path did not.

The list below is the gate. Adding a CLI that writes state means adding it here, which forces
the question to be answered at authoring time rather than discovered on a broken service.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from runtime.mvp_runtime.errors import PersistenceError

ROOT = Path(__file__).resolve().parents[1]

# Every script that writes under .runtime_governance_state/, and where its guard sits.
GUARDED = {
    "activate_safety_flag": "the per-provider safety-flag grant (the original adopter)",
    "place_canary_order": "the canary registry row and the order's audit event",
    "register_live_trading_budget": "the self-hashed live-trading budget record",
    "promote_memory_candidate": "the working-memory promotion and its audit event",
    "promote_strategy_candidates": "the approval store and the active strategy pool",
    "register_program_candidate": "the approval store and the program registry entry",
    "import_crypto_history": "imported outcomes, counterfactuals and the candidate pool",
}

# Writers whose guard is deliberately NOT at the top of main, because they have a read-only
# mode that must stay runnable from anywhere. Present so the placement reads as a decision.
GUARDED_AT_THE_WRITE_POINT = {
    "promote_strategy_candidates": "--list only reads",
    "register_program_candidate": "--list only reads",
    "import_crypto_history": "the default run is a dry-run report",
}


def _guard_call_lines(name: str) -> list[int]:
    """Line numbers where the guard is actually CALLED, imports excluded.

    The distinction is load-bearing and was found by mutation-testing this file: deleting the
    call while leaving the import in place passed a plain substring check. A gate that an
    unused import satisfies is not a gate.
    """
    lines = (ROOT / "scripts" / f"{name}.py").read_text(encoding="utf-8").splitlines()
    return [
        i for i, line in enumerate(lines)
        if "assert_not_foreign_root_run(" in line and not line.lstrip().startswith(("from ", "import "))
    ]


@pytest.mark.parametrize("name", sorted(GUARDED))
def test_the_script_calls_the_guard(name):
    assert _guard_call_lines(name), (
        f"scripts/{name}.py writes {GUARDED[name]} but never calls "
        "state_guard.assert_not_foreign_root_run; a host-side root run would leave that "
        "state unwritable by the service"
    )


@pytest.mark.parametrize("name", sorted(GUARDED_AT_THE_WRITE_POINT))
def test_a_read_only_mode_is_not_blocked_by_the_guard(name):
    """The guard must sit after the read-only branch, not before it.

    Asserted positionally: a listing that refuses to run because the state directory belongs
    to the service would be a guard that costs more than the failure it prevents.
    """
    lines = (ROOT / "scripts" / f"{name}.py").read_text(encoding="utf-8").splitlines()
    guard_line = _guard_call_lines(name)[0]
    read_only_exit = next(
        i for i, line in enumerate(lines)
        if ("args.list" in line or "args.confirm" in line) and i < guard_line
    )
    assert read_only_exit < guard_line, (
        f"scripts/{name}.py guards before its read-only branch ({GUARDED_AT_THE_WRITE_POINT[name]})"
    )


def test_the_canary_guard_fires_before_the_venue(monkeypatch, capsys):
    """The one placement that cannot be got wrong: for a real order, "before" is the property.

    A guard that refuses after `submit_and_reconcile` would be a refusal printed next to a
    filled position — worse than no guard, because the operator would trust the refusal.
    """
    pco = importlib.import_module("scripts.place_canary_order")
    sent: list[object] = []

    def boom(root=None, **kwargs):
        raise PersistenceError("STATE_FOREIGN_ROOT_RUN", "state belongs to uid 10001")

    monkeypatch.setattr(pco, "assert_not_foreign_root_run", boom)
    monkeypatch.setattr(pco.live_execution, "submit_and_reconcile",
                        lambda *a, **k: sent.append(1) or {})

    exit_code = pco.main(["--symbol", "BTCUSDT", "--quantity", "0.001", "--notional", "65"])

    assert exit_code != 0
    assert sent == [], "the guard must refuse before the order reaches the venue"
    assert "STATE_FOREIGN_ROOT_RUN" in capsys.readouterr().err


def test_the_budget_guard_fires_before_the_record_is_written(monkeypatch, capsys, tmp_path):
    """Same property for the budget: no record, not a root-owned one."""
    reg = importlib.import_module("scripts.register_live_trading_budget")
    written: list[object] = []

    def boom(root=None, **kwargs):
        raise PersistenceError("STATE_FOREIGN_ROOT_RUN", "state belongs to uid 10001")

    monkeypatch.setattr(reg, "assert_not_foreign_root_run", boom)
    monkeypatch.setattr(reg.live_budget, "write_registered_budget",
                        lambda *a, **k: written.append(1))

    exit_code = reg.main(["--registered-by", "thomas", "--root", str(tmp_path)])

    assert exit_code != 0
    assert written == []
    assert "STATE_FOREIGN_ROOT_RUN" in capsys.readouterr().err

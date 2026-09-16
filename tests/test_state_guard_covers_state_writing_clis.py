"""Coverage gate: every CLI that writes governed state refuses a host-side root run.

`state_guard` shipped with one adopter, `activate_safety_flag.py`. The other state-writing
CLIs kept the failure it was written for: run on the host as root, they succeed, print a happy
summary, and leave files under `.runtime_governance_state/` that the uid-10001 service can
never write again — surfacing later, in a different process, with nothing pointing back.

It is not hypothetical for the money path. On 2026-07-26 a `binance_futures_account`
activation, a `live_trading` activation, a registered budget and a canary registry row were all
written root-owned from a host shell, and a separate session had to find them and `chown` them
back. `activate_safety_flag.py` gained the guard from that incident; the four other writers in
the same session's path did not. (That original adopter was removed with the grant machinery
on 2026-08-30 — the incident and the rule it minted outlive the script.)

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
    "register_live_trading_budget": "the self-hashed live-trading budget record",
    "promote_memory_candidate": "the working-memory promotion and its audit event",
    "promote_strategy_candidates": "the approval store and the active strategy pool",
    "register_program_candidate": "the approval store and the program registry entry",
    "import_crypto_history": "imported outcomes, counterfactuals and the candidate pool",
    "run_slippage_probe": "the probe plan store, the approval store, and the probe order's audit event",
    "disarm_live_strategies": "the active strategy pool's live tier and the control ledger",
    "run_signed_testnet_cycle": "the testnet venue's order counter and its evidence registry",
}

# Writers whose guard is deliberately NOT at the top of main, because they have a read-only
# mode that must stay runnable from anywhere. Present so the placement reads as a decision.
GUARDED_AT_THE_WRITE_POINT = {
    "promote_strategy_candidates": "--list only reads",
    "disarm_live_strategies": "--list only reads",
    "run_signed_testnet_cycle": "--list and --plan only read",
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


def test_the_probe_guard_fires_before_the_venue(monkeypatch, capsys, tmp_path):
    """The one placement that cannot be got wrong: for a real order, "before" is the property.

    A guard that refuses after `submit_and_reconcile` would be a refusal printed next to a
    filled position — worse than no guard, because the operator would trust the refusal.

    Written for the canary door and moved to `run_slippage_probe --fire` when that door was
    removed (2026-09-15, PR1r): the probe is the operator's one remaining real-order CLI, and
    without this only the structural "the call exists" check above covered it, which says
    nothing about order. The probe guards twice — at the top of `main` for every writing verb
    and again as step 0 of `run_fire` — so both are driven here: the second is what still
    stands if the first is ever moved into the verbs.
    """
    probe_cli = importlib.import_module("scripts.run_slippage_probe")
    sent: list[object] = []

    def boom(root=None, **kwargs):
        raise PersistenceError("STATE_FOREIGN_ROOT_RUN", "state belongs to uid 10001")

    monkeypatch.setattr(probe_cli, "assert_not_foreign_root_run", boom)
    monkeypatch.setattr(probe_cli.live_execution, "submit_and_reconcile",
                        lambda *a, **k: sent.append(1) or {})

    exit_code = probe_cli.main(["--fire", "--symbol", "BTCUSDT", "--root", str(tmp_path)])

    assert exit_code != 0
    assert sent == [], "the guard must refuse before the order reaches the venue"
    assert "STATE_FOREIGN_ROOT_RUN" in capsys.readouterr().err

    with pytest.raises(PersistenceError):
        probe_cli.run_fire(root=tmp_path, symbol="BTCUSDT")
    assert sent == [], "run_fire's own guard must refuse before the order reaches the venue"


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

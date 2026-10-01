"""What the crypto lane does today where the refactor plan found it surprising, pinned as it is (crypto
refactor plan PR-05; ``docs/proposals/CRYPTO_REFACTOR_AND_MODULARIZATION_PLAN_V0.1.md`` §O).

These are characterization tests. Each one states current behaviour, including two behaviours the plan
reported as findings. None of them is an endorsement:

- **S-1** (Thomas decision 2026-09-15, kept by D-4 2026-09-30). ``/kill`` and ``/pause`` drop the
  trading fire, so the live leg, where settle/protect/time-exit/reconcile live, does not run. A HARD
  halt keeps the fire and the leg running. If this changes, it is a decision, and this test is where
  it shows.
- **S-3** (D-3 2026-09-30, fixed by PR-S3). The promotion door reads the pool without its lock and
  installs the whole file, so a LIVE-tier disarm that landed in between was undone. The install now
  names the read it was built from and is refused when the file has changed. This test pinned the old
  behaviour and was flipped with the fix.
- **the two streak counters** (§K). ``guards`` and ``live_allowance`` say they share a rule but read
  different fields and treat probes differently. Any consolidation must start from these differences.
- **the cycle's stage order.** Paper, the observers, the report, the live allowance, the live leg,
  then the lifecycle. A refactor that moves a stage (PR-11) must keep this order or change this pin
  on purpose.
- **the stage ladder is read only by the live doors.** A cycle with live trading off never reads the
  execution stage, so READ_ONLY, SHADOW and PAPER run the paper plane identically (§B-3).
- **the cycle's reason codes come out in stage order.** A cycle record lists what went wrong in the
  order the stages ran: collection, the pool, the limits, the histories, the live leg. The status line
  and the ledger read that list as written, so a stage extracted into its own function (PR-11) has to
  append where it did.
"""

from __future__ import annotations

import json

import pytest

from runtime.mvp_runtime import control
from runtime.mvp_runtime.control import ACTIVE, ControlState, ControlStore
from runtime.mvp_runtime.crypto import cycle, feedback, guards, live_allowance
from runtime.mvp_runtime.crypto import pool as pool_store
from runtime.mvp_runtime.crypto.market_data import MARKET_DATA_ENV
from runtime.mvp_runtime.crypto.paper import PAPER_ENV
from runtime.mvp_runtime.crypto.vocabulary import LIVE_TRADING_ENV
from runtime.mvp_runtime.errors import ToolError
from runtime.mvp_runtime.scheduler import KIND_CRYPTO, ScheduleStore, build_schedule, run_due
from runtime.mvp_runtime.store import LedgerStore

CREATED = "2026-07-22T11:00:00Z"
FIRE = "2026-07-22T13:00:00Z"
NOW = "2026-08-09T12:00:00Z"


def _spec(sid="S1"):
    return {
        "schema_version": "strategy_spec.v1",
        "strategy_id": sid, "strategy_version": "1.0", "strategy_family": "breakout",
        "symbol_scope": ["BTCUSDT"], "timeframe": "1d", "direction": "long",
        "entry_rules": {"operator": "AND",
                        "conditions": [{"feature": "close", "comparison": ">", "value": 0.0}]},
        "exit_rules": {"stop_model": "atr", "stop_atr": 1.5, "target_atr": 2.0, "max_holding_bars": 10},
        "risk_constraints": {"max_risk_per_trade_R": 1.0},
    }


def _live_entry(sid="S1"):
    return {"strategy_id": sid, "candidate_id": "c1", "status": "PAPER_ACTIVE", "strategy_spec": _spec(sid),
            "strategy_rule_hash": "h1", "generation_id": "GEN-1",
            pool_store.LIVE_TIER_FIELD: pool_store.LIVE_TIER_LIVE}


def _write_pool(root, *entries):
    d = root / ".runtime_governance_state" / "crypto"
    d.mkdir(parents=True, exist_ok=True)
    (d / "active_strategy_pool.json").write_text(json.dumps({"active_strategies": list(entries)}),
                                                 encoding="utf-8")


def _fire(root, *, control_store):
    store = ScheduleStore(root)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.add(build_schedule(kind=KIND_CRYPTO, request="BTCUSDT 1d", interval_seconds=900,
                             created_by="op", now=CREATED))
    return run_due(store, now=FIRE, control_store=control_store, ledger=LedgerStore(root / "ledger"),
                   repo_root=root)


@pytest.fixture
def dry(monkeypatch):
    """The mock collector and the dry paper store, and live trading off, as on a machine without opt-ins."""
    for var in (MARKET_DATA_ENV, PAPER_ENV, LIVE_TRADING_ENV):
        monkeypatch.delenv(var, raising=False)


def _record_calls(monkeypatch, module, names, calls):
    for name in names:
        original = getattr(module, name)

        def recorder(*args, _name=name, _original=original, **kwargs):
            calls.append(_name)
            return _original(*args, **kwargs)

        monkeypatch.setattr(module, name, recorder)


# --- S-1: kill and pause stop position management, a HARD halt does not ------------------------------

@pytest.mark.parametrize("command", ["kill", "pause"])
def test_a_kill_or_pause_drops_the_trading_fire_and_the_live_leg_never_runs(tmp_path, monkeypatch, dry, command):
    calls: list[str] = []
    _record_calls(monkeypatch, cycle, ["run_live_leg"], calls)
    control_store = ControlStore(tmp_path)
    control.apply_command(control_store, command, actor="op", now=CREATED)

    summary = _fire(tmp_path, control_store=control_store)

    assert summary["fired"] == 0 and summary["skipped"] == 1
    assert summary["results"][0]["status"] == "skipped_not_active"
    assert calls == []   # no settle, protect, time exit or reconcile this fire (S-1)


def test_a_hard_halt_keeps_the_trading_fire_and_the_live_leg_running(tmp_path, monkeypatch, dry):
    calls: list[str] = []
    _record_calls(monkeypatch, cycle, ["run_live_leg"], calls)
    control_store = ControlStore(tmp_path)
    control_store.save(ControlState(mode=ACTIVE, updated_by="op", updated_at=CREATED, reason="r",
                                    trading_armed=False, halt_level=control.HALT_HARD))

    summary = _fire(tmp_path, control_store=control_store)

    assert summary["fired"] == 1
    assert calls == ["run_live_leg"]


# --- S-3, fixed by PR-S3: the door's install is refused after a disarm lands between its read and its write

def test_an_install_built_from_an_earlier_read_is_refused_after_a_disarm_in_between(tmp_path):
    """The door's own sequence: `scripts/promote_strategy_candidates.py` reads the pool without the lock,
    builds the new pool from that read, and `pool_state.install_active_pool` replaces the file under the
    lock. The cycle's disarm writes in between.

    Until PR-S3 the install wrote over it and the entry was LIVE again. Now the install names the read
    it was built from, finds the file is no longer that one, and writes nothing."""
    _write_pool(tmp_path, _live_entry())
    read_by_the_door, digest = pool_store.load_active_pool_with_digest(tmp_path)

    assert pool_store.disarm_live_tier(["S1"], root=tmp_path, now=NOW, reasons=["allowance"]) == 1
    disarmed = pool_store.load_active_pool(tmp_path)["active_strategies"][0]
    assert pool_store.entry_live_tier(disarmed) == pool_store.LIVE_TIER_OBSERVATION

    with pytest.raises(ToolError) as refused:
        pool_store.install_active_pool({"pool_version": "active_strategy_pool.v1", "stage": "paper",
                                        "active_strategies": read_by_the_door["active_strategies"],
                                        "updated_by": "op", "updated_at": NOW},
                                       root=tmp_path, expected_digest=digest)

    assert refused.value.reason_code == pool_store.STRATEGY_POOL_CHANGED
    after = pool_store.load_active_pool(tmp_path)["active_strategies"][0]
    assert pool_store.entry_live_tier(after) == pool_store.LIVE_TIER_OBSERVATION   # the disarm stands


# --- the two streak counters (§K) --------------------------------------------------------------------

def test_the_pool_breaker_does_not_count_a_probe_and_the_allowance_does():
    rows = [{"pnl_r": -1.0, "result_R": -1.0},
            {"pnl_r": -1.0, "result_R": -1.0, "is_probe": True}]
    assert guards._consecutive_losses(rows) == 1
    assert live_allowance._consecutive_losses(rows) == 2


def test_the_pool_breaker_reads_net_r_and_the_allowance_reads_the_row_as_written():
    """A row that is a small gross win and a net loss: the two counters disagree about it."""
    rows = [{"pnl_r": -0.1, "result_R": 0.02}]
    assert guards._consecutive_losses(rows) == 1
    assert live_allowance._consecutive_losses(rows) == 0


def test_one_streak_rule_with_each_counters_own_reading():
    """Refactor plan §K-1: one rule, `outcome_math.consecutive_losses`. A skipped row neither adds to the
    streak nor ends it; NaN is not a loss and ends it."""
    from runtime.mvp_runtime.crypto.outcome_math import consecutive_losses

    rows = [{"r": -1.0}, {"r": 2.0}, {"r": -1.0}, {"r": 5.0, "skip": True}, {"r": -0.5}]
    assert consecutive_losses(rows, r_of=lambda row: row["r"]) == 1
    assert consecutive_losses(rows, r_of=lambda row: row["r"], skip=lambda row: row.get("skip")) == 2
    assert consecutive_losses([{"r": -1.0}, {"r": float("nan")}], r_of=lambda row: row["r"]) == 0
    assert consecutive_losses([], r_of=lambda row: row["r"]) == 0


def test_a_row_without_its_r_raises_at_the_pool_breaker_and_ends_the_allowances_streak():
    """What a missing R means stayed each counter's own through the merge: the breaker reads a
    normalised row and fails loudly; the allowance reads the ledger as written and stops counting."""
    for row in ({"result_R": None}, {"pnl_r": None, "result_R": None}):
        with pytest.raises((KeyError, TypeError)):
            guards._consecutive_losses([{"pnl_r": -1.0}, row])
    assert live_allowance._consecutive_losses([{"result_R": -1.0}, {"result_R": None}]) == 0
    assert live_allowance._consecutive_losses([{"result_R": -1.0}, {"result_R": "x"}]) == 0
    assert live_allowance._consecutive_losses([{"result_R": "-1"}, {"result_R": -2.0}]) == 2


# --- the cycle's stage order ---------------------------------------------------------------------------

def test_the_cycle_runs_its_stages_in_this_order(tmp_path, monkeypatch, dry):
    calls: list[str] = []
    _record_calls(monkeypatch, cycle, ["run_paper_update", "run_counterfactual_update", "run_forward_book_update",
                                       "evaluate_live_allowance", "run_live_leg", "run_lifecycle"], calls)
    _record_calls(monkeypatch, feedback, ["run_paper_performance_report"], calls)
    _write_pool(tmp_path, _live_entry())   # a LIVE-tier entry, so the allowance is judged

    assert _fire(tmp_path, control_store=ControlStore(tmp_path))["fired"] == 1

    assert calls == ["run_paper_update", "run_counterfactual_update", "run_forward_book_update",
                     "run_paper_performance_report", "evaluate_live_allowance", "run_live_leg", "run_lifecycle"]


# --- the stage ladder is read only by the live doors ---------------------------------------------------

def test_a_cycle_with_live_trading_off_never_reads_the_execution_stage(tmp_path, monkeypatch, dry):
    from runtime.mvp_runtime.crypto import (
        execution_stage, live_order, live_readiness, live_route, promotion, testnet_execution,
    )

    reads: list[str] = []

    def refuse(*args, **kwargs):
        # Recorded, not only raised: the live leg reports instead of raising, so a raise alone could be
        # swallowed into the cycle record and the test would pass while the stage was read.
        reads.append("read")
        raise AssertionError("the execution stage was read")

    for module in (execution_stage, live_order, live_readiness, live_route, promotion, testnet_execution):
        if hasattr(module, "resolve_execution_stage"):
            monkeypatch.setattr(module, "resolve_execution_stage", refuse)
    _write_pool(tmp_path, _live_entry())

    summary = _fire(tmp_path, control_store=ControlStore(tmp_path))

    assert summary["fired"] == 1
    assert reads == []


# --- the cycle's reason codes come out in stage order ---------------------------------------------------

def _dry_cycle(root, collector):
    from runtime.mvp_runtime.crypto.paper import DryRunPaperStore

    return cycle.run_crypto_cycle(collector=collector, store=DryRunPaperStore(), now=FIRE, root=root,
                                  control_store=ControlStore(root))


def test_a_cycle_where_every_read_fails_lists_the_failures_in_stage_order(tmp_path, dry):
    """The venue is down, the pool is not a pool, the limits and the paper history do not parse. The
    report would name the paper history a second time and does not. The live history is never read,
    because there are no limits to judge it against."""
    from runtime.mvp_runtime.crypto import paper, risk_limits
    from tests.test_mvp_runtime_crypto_cycle import BrokenCollector

    pool_file = pool_store.pool_path(tmp_path)
    pool_file.parent.mkdir(parents=True, exist_ok=True)
    pool_file.write_text(json.dumps({"active_strategies": [{"strategy_spec": {"strategy_id": "x"}}]}),
                         encoding="utf-8")
    risk_limits.limits_path(tmp_path).write_text("{broken", encoding="utf-8")
    (paper.state_dir(tmp_path) / "paper_outcomes.jsonl").write_text("{broken\n", encoding="utf-8")

    record = _dry_cycle(tmp_path, BrokenCollector())

    assert record["reason_codes"] == [
        "MARKET_DATA_DEGRADED", "CROSS_SECTION_DEGRADED", "STRATEGY_POOL_INVALID",
        "CRYPTO_RISK_LIMITS_UNREADABLE", "OUTCOME_HISTORY_UNREADABLE", "LIVE_ROUTING_DISABLED",
    ]


def test_an_unreadable_paper_history_is_listed_before_an_unreadable_live_one(tmp_path, dry):
    """Both histories are read in the guard's stage, the paper one first. The paper leg still routes:
    its verdict is data health alone."""
    from runtime.mvp_runtime.crypto import paper
    from runtime.mvp_runtime.crypto.live_pnl import state_dir as live_state_dir
    from tests.test_mvp_runtime_crypto_cycle import FakeExchangeCollector

    _write_pool(tmp_path, {"strategy_id": "S1", "status": "PAPER_ACTIVE", "champion_score": 0.5,
                           "strategy_spec": _spec()})
    live_state_dir(tmp_path).mkdir(parents=True, exist_ok=True)
    (live_state_dir(tmp_path) / "live_outcomes.jsonl").write_text('{"not":"hashed"}\n', encoding="utf-8")
    paper.state_dir(tmp_path).mkdir(parents=True, exist_ok=True)
    (paper.state_dir(tmp_path) / "paper_outcomes.jsonl").write_text("{broken\n", encoding="utf-8")

    record = _dry_cycle(tmp_path, FakeExchangeCollector())

    assert record["reason_codes"] == ["OUTCOME_HISTORY_UNREADABLE", "LIVE_HISTORY_TAMPERED",
                                      "LIVE_ROUTING_DISABLED"]
    assert (record["verdict_status"], record["paper_verdict_status"]) == ("NO_NEW_POSITION", "ALLOW")


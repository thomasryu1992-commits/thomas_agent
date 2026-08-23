"""Regime-conditional routing — decline a regime a strategy has demonstrated it loses in.

The evidence for this existed before the router could read it. `factory.backtest_spec` computed a
per-regime R total on every candidate and then collapsed it to a count, so the one thing that said
*where* the edge was got thrown away; and the pool entry carried no evidence at all, so the router
had nothing to consult even if it had been kept.

What these tests are mostly about is the **direction the gate fails in**. It can only ever make the
pool trade less, and the cost of being wrong is silent — no record shows the trades that did not
happen — so every uncertain case has to resolve to *admitted*:

- no evidence on the entry (every strategy promoted before this shipped) → admitted;
- a regime the replay never traded → admitted;
- a regime traded on too few closed trades → admitted, **however bad the sign**, because a
  per-regime verdict read off three trades is the overfitting this gate is supposed to guard
  against, wired into live routing;
- a malformed cell → admitted.

Only "traded enough, and lost" declines.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.crypto import factory
from runtime.mvp_runtime.crypto.paper import (
    MIN_REGIME_TRADES_TO_EXCLUDE,
    REGIME_EXCLUDED,
    STATUS_ENTRY_CANDIDATE,
    STATUS_NO_ENTRY,
    regime_admits,
    route_entries,
)
from runtime.mvp_runtime.crypto.strategy import StrategySpec

NOW = "2026-07-30T00:00:00Z"
ENOUGH = MIN_REGIME_TRADES_TO_EXCLUDE

# A spec whose single condition is true on any row carrying a positive close, so these tests
# exercise the REGIME gate rather than the rule evaluator.
_SPEC = {
    "schema_version": "strategy_spec.v1", "strategy_id": "S001", "strategy_version": "1.0",
    "strategy_family": "trend_pullback", "direction": "long", "timeframe": "1h",
    "symbol_scope": ["ETHUSDT"],
    "entry_rules": {"operator": "AND", "conditions": [
        {"feature": "close", "comparison": ">", "value": 0.0},
    ]},
    "exit_rules": {"stop_model": "atr", "stop_atr": 1.0, "target_atr": 2.0, "max_holding_bars": 12},
}


def _entry(regime_evidence=None, *, strategy_id="S001"):
    entry = {
        "strategy_id": strategy_id, "candidate_id": f"cand-{strategy_id}",
        "status": "PAPER_ACTIVE", "champion_score": 0.5,
        "strategy_rule_hash": "hash", "strategy_spec": dict(_SPEC, strategy_id=strategy_id),
    }
    if regime_evidence is not None:
        entry["regime_evidence"] = regime_evidence
    return entry


def _row(regime="TREND_UP"):
    return {"close": 100.0, "atr": 1.0, "market_regime": regime}


def _pool(*entries):
    return {"pool_version": "active_strategy_pool.v1", "active_strategies": list(entries)}


# --- the rule, and the direction it fails in ------------------------------------

def test_absent_evidence_admits():
    """Every strategy promoted before this existed. A filter that silenced the whole installed
    pool on the day it shipped would be a worse error than the one it fixes."""
    assert regime_admits(_entry(), "TREND_UP") == (True, None)
    assert regime_admits(_entry({}), "TREND_UP") == (True, None)


def test_a_regime_the_replay_never_traded_admits():
    evidence = {"TREND_UP": {"trades": ENOUGH, "total_r": -5.0}}
    assert regime_admits(_entry(evidence), "RANGE") == (True, None)


def test_too_few_trades_admits_however_bad_the_sign():
    """The clause that keeps this gate from being the overfitting it guards against."""
    evidence = {"RANGE": {"trades": ENOUGH - 1, "total_r": -99.0}}
    assert regime_admits(_entry(evidence), "RANGE") == (True, None)


def test_enough_trades_and_a_loss_declines():
    evidence = {"RANGE": {"trades": ENOUGH, "total_r": -3.0}}
    assert regime_admits(_entry(evidence), "RANGE") == (False, REGIME_EXCLUDED)


def test_enough_trades_and_a_profit_admits():
    evidence = {"RANGE": {"trades": ENOUGH, "total_r": 0.01}}
    assert regime_admits(_entry(evidence), "RANGE") == (True, None)


def test_exactly_break_even_declines():
    """At or below zero. A regime that returned exactly nothing over ten trades paid for the
    spread and the funding out of the other regimes' profits."""
    evidence = {"RANGE": {"trades": ENOUGH, "total_r": 0.0}}
    assert regime_admits(_entry(evidence), "RANGE") == (False, REGIME_EXCLUDED)


@pytest.mark.parametrize("cell", [
    {"trades": "many", "total_r": -3.0},        # trades not an int
    {"trades": True, "total_r": -3.0},          # bool is not a count
    {"trades": ENOUGH, "total_r": "bad"},       # total not a number
    {"trades": ENOUGH, "total_r": True},
    {"trades": ENOUGH},                         # no total at all
    "not-a-mapping",
])
def test_a_malformed_cell_admits(cell):
    """Fail-open, because this gate only ever declines on a demonstration — and a cell it cannot
    read is not one."""
    assert regime_admits(_entry({"RANGE": cell}), "RANGE") == (True, None)


@pytest.mark.parametrize("regime", [None, "", 42])
def test_an_unreadable_regime_admits(regime):
    evidence = {"RANGE": {"trades": ENOUGH, "total_r": -3.0}}
    assert regime_admits(_entry(evidence), regime) == (True, None)


def test_the_rule_is_derived_not_stored():
    """No ``excluded`` list is written anywhere: the threshold is applied at read time so moving
    it cannot leave stale labels behind. That is the defect ``pool.candidate_quality`` already had
    to fix once, where verdicts written at mint time outlived the rule that produced them."""
    evidence = {"RANGE": {"trades": ENOUGH, "total_r": -3.0}}
    entry = _entry(evidence)
    assert "excluded" not in entry["regime_evidence"]
    assert set(entry["regime_evidence"]["RANGE"]) == {"trades", "total_r"}


# --- through the router ---------------------------------------------------------

def test_an_excluded_match_does_not_enter():
    evidence = {"RANGE": {"trades": ENOUGH, "total_r": -3.0}}
    route = route_entries(
        _pool(_entry(evidence)), _row("RANGE"), symbol="ETHUSDT", timeframe="1h", now=NOW
    )
    assert route["status"] == STATUS_NO_ENTRY
    assert route["regime_excluded_strategy_ids"] == ["S001"]
    assert route["matched_strategy_ids"] == [], "an excluded strategy must not count as matched"


def test_the_same_strategy_enters_in_a_regime_it_earned():
    evidence = {"RANGE": {"trades": ENOUGH, "total_r": -3.0}}
    route = route_entries(
        _pool(_entry(evidence)), _row("TREND_UP"), symbol="ETHUSDT", timeframe="1h", now=NOW
    )
    assert route["status"] == STATUS_ENTRY_CANDIDATE
    assert route["primary_strategy_id"] == "S001"
    assert route["regime_excluded_strategy_ids"] == []


def test_the_record_distinguishes_excluded_from_never_matched():
    """The reason the filter runs on a MATCH rather than before evaluation. A route that entered
    nothing because every match was regime-excluded otherwise reads identically to one where
    nothing matched, and those want different responses from an operator."""
    evidence = {"RANGE": {"trades": ENOUGH, "total_r": -3.0}}
    route = route_entries(
        _pool(_entry(evidence)), _row("RANGE"), symbol="ETHUSDT", timeframe="1h", now=NOW
    )
    evaluation = route["evaluations"][0]
    assert evaluation["matched"] is True, "the rules did fire — the regime declined it"
    assert evaluation["regime_excluded"] == REGIME_EXCLUDED
    assert evaluation["regime"] == "RANGE"
    assert route["regime"] == "RANGE"


def test_an_excluded_strategy_does_not_become_a_supporting_id():
    """`supporting_strategy_ids` rides onto the position and into the outcome record, so a
    regime-excluded strategy appearing there would attribute a trade to a strategy that was
    declined."""
    admitted = _entry({"RANGE": {"trades": ENOUGH, "total_r": 5.0}}, strategy_id="S001")
    declined = _entry({"RANGE": {"trades": ENOUGH, "total_r": -5.0}}, strategy_id="S002")
    route = route_entries(
        _pool(admitted, declined), _row("RANGE"), symbol="ETHUSDT", timeframe="1h", now=NOW
    )
    assert route["status"] == STATUS_ENTRY_CANDIDATE
    assert route["primary_strategy_id"] == "S001"
    assert route["supporting_strategy_ids"] == []
    assert route["regime_excluded_strategy_ids"] == ["S002"]


def test_the_gate_is_one_directional():
    """It can only ever decline. A strategy the existing checks refuse — wrong symbol here — is
    still refused however good its regime evidence is, so this needs no gate of its own."""
    evidence = {"TREND_UP": {"trades": ENOUGH, "total_r": 99.0}}
    route = route_entries(
        _pool(_entry(evidence)), _row("TREND_UP"), symbol="BTCUSDT", timeframe="1h", now=NOW
    )
    assert route["status"] == STATUS_NO_ENTRY
    assert route["evaluations"][0]["unevaluable"]


def test_a_pool_predating_the_gate_routes_exactly_as_before():
    route = route_entries(
        _pool(_entry()), _row("RANGE"), symbol="ETHUSDT", timeframe="1h", now=NOW
    )
    assert route["status"] == STATUS_ENTRY_CANDIDATE
    assert route["regime_excluded_strategy_ids"] == []


# --- the evidence the gate reads -------------------------------------------------

def test_backtest_evidence_records_which_regime_produced_what():
    """The figure the loop always computed and the record always discarded."""
    from runtime.mvp_runtime.crypto.market_data import MockMarketDataCollector, collect_market_data

    snapshot, _ = collect_market_data(
        "ETHUSDT", "1h", collector=MockMarketDataCollector(), now=NOW, limit=400
    )
    minted = next(
        s for n in range(8)
        for s in factory.generate_batch(f"GEN-{n:03d}", seed=n, symbol="ETHUSDT", timeframe="1h")["specs"]
    )
    evidence = factory.backtest_spec(StrategySpec.from_dict(minted), snapshot)
    per_regime = evidence["regime_breakdown"]["per_regime"]
    assert isinstance(per_regime, dict)
    # Consistent with the two fields that were already there, which `robustness` reads.
    assert sorted(per_regime) == evidence["regime_breakdown"]["regimes_traded"]
    assert (sum(1 for cell in per_regime.values() if cell["total_r"] > 0)
            == evidence["regime_breakdown"]["profitable_regime_count"])
    assert sum(cell["trades"] for cell in per_regime.values()) == evidence["closed_count"]
    for cell in per_regime.values():
        assert set(cell) == {"trades", "total_r"}


def test_promotion_copies_the_regime_evidence_onto_the_entry(monkeypatch, tmp_path):
    """The router reads the POOL. Making it look candidates up by ``candidate_id`` instead would
    put a store whose reader deliberately RAISES on damage — because it backs a promotion an
    operator signs — on the path of every 15-minute cycle, so one corrupt line would stop
    routing."""
    from scripts import promote_strategy_candidates as prom

    per_regime = {"TREND_UP": {"trades": 14, "total_r": 6.5},
                  "RANGE": {"trades": ENOUGH, "total_r": -2.0}}
    candidate = {
        "candidate_id": "cand_x", "strategy_id": "S001", "generation_id": "GEN-001",
        "champion_score": 0.71, "strategy_rule_hash": "hash_x",
        "strategy_spec": dict(_SPEC),
        "backtest_evidence": {
            "closed_count": 24, "expectancy": 0.2, "bars_replayed": 12000,
            "robustness": {"holdout_status": "CONFIRMED"},
            "regime_breakdown": {"regimes_traded": sorted(per_regime),
                                 "profitable_regime_count": 1,
                                 "per_regime": per_regime},
        },
    }
    monkeypatch.setattr(prom.pool_store, "read_candidates", lambda root: [candidate])
    monkeypatch.setattr(prom.pool_store, "assert_promotable_cost_basis", lambda records: None)
    monkeypatch.setattr(prom.pool_store, "assert_promotable_evidence_depth", lambda records: None)

    installed: dict = {}
    monkeypatch.setattr(prom.pool_store, "install_active_pool",
                        lambda pool, *, root=None: installed.update(pool) or 1)
    monkeypatch.setattr(prom, "_audit_promotion", lambda *a, **k: None, raising=False)

    prom.run_promotion(
        selectors=["cand_x"], promoted_by="thomas", reason="test", keep_active=False, live_tier="LIVE",
        root=tmp_path, now=NOW, without_approval=True,
        # The fixture is deliberately minimal (24 closes, shallow window); the 5-3 bar is
        # not the subject here — regime evidence propagation is.
        allow_below_entry_bar=True,
    )
    entry = installed["active_strategies"][0]
    assert entry["regime_evidence"] == per_regime

    # And the installed entry is directly usable by the gate — the point of copying it.
    assert regime_admits(entry, "RANGE") == (False, REGIME_EXCLUDED)
    assert regime_admits(entry, "TREND_UP") == (True, None)


def test_a_candidate_without_the_block_promotes_with_no_evidence(monkeypatch, tmp_path):
    """Every candidate minted before `per_regime` existed. It must promote and route exactly as
    it did — absent evidence admits."""
    from scripts import promote_strategy_candidates as prom

    candidate = {
        "candidate_id": "cand_old", "strategy_id": "S001", "generation_id": "GEN-001",
        "champion_score": 0.5, "strategy_rule_hash": "hash_old",
        "strategy_spec": dict(_SPEC),
        "backtest_evidence": {"closed_count": 24, "expectancy": 0.2, "bars_replayed": 12000,
                              "robustness": {"holdout_status": "CONFIRMED"}},
    }
    monkeypatch.setattr(prom.pool_store, "read_candidates", lambda root: [candidate])
    monkeypatch.setattr(prom.pool_store, "assert_promotable_cost_basis", lambda records: None)
    monkeypatch.setattr(prom.pool_store, "assert_promotable_evidence_depth", lambda records: None)
    installed: dict = {}
    monkeypatch.setattr(prom.pool_store, "install_active_pool",
                        lambda pool, *, root=None: installed.update(pool) or 1)
    monkeypatch.setattr(prom, "_audit_promotion", lambda *a, **k: None, raising=False)

    prom.run_promotion(
        selectors=["cand_old"], promoted_by="thomas", reason="test", keep_active=False, live_tier="LIVE",
        root=tmp_path, now=NOW, without_approval=True,
        allow_below_entry_bar=True,
    )
    entry = installed["active_strategies"][0]
    assert entry["regime_evidence"] is None
    assert regime_admits(entry, "RANGE") == (True, None)


# --- through a whole cycle -------------------------------------------------------

def _cycle(tmp_path, regime_evidence):
    """One real cycle over the mock frame, with a spec that matches any positive close."""
    from runtime.mvp_runtime.control import ControlStore
    from runtime.mvp_runtime.crypto import pool as pool_store
    from runtime.mvp_runtime.crypto.cycle import run_crypto_cycle
    from runtime.mvp_runtime.crypto.market_data import MockMarketDataCollector
    from runtime.mvp_runtime.crypto.paper import DryRunPaperStore

    entry = _entry(regime_evidence)
    pool_store.install_active_pool(
        {"pool_version": "active_strategy_pool.v1", "stage": "paper", "active_strategies": [entry]},
        root=tmp_path,
    )
    return run_crypto_cycle(
        collector=MockMarketDataCollector(), store=DryRunPaperStore(), now=NOW,
        symbol="ETHUSDT", timeframe="1h", root=tmp_path, control_store=ControlStore(tmp_path),
    )


def _mock_regime():
    from runtime.mvp_runtime.crypto.features import latest_feature_row
    from runtime.mvp_runtime.crypto.market_data import MockMarketDataCollector, collect_market_data

    snapshot, _ = collect_market_data(
        "ETHUSDT", "1h", collector=MockMarketDataCollector(), now=NOW, limit=120
    )
    return latest_feature_row(snapshot)["market_regime"]


def test_the_cycle_record_and_status_line_name_the_excluded_strategy(tmp_path):
    """Not a count: a strategy excluded on every cycle for a week is a demotion candidate, and a
    count cannot say which one."""
    from runtime.mvp_runtime.crypto.cycle import cycle_status_line

    regime = _mock_regime()
    record = _cycle(tmp_path, {regime: {"trades": ENOUGH, "total_r": -3.0}})
    assert record["route_status"] == STATUS_NO_ENTRY
    assert record["regime_excluded"] == ["S001"]
    assert "regime_excluded=S001" in cycle_status_line(record)


def test_a_cycle_in_a_paid_regime_still_enters(tmp_path):
    from runtime.mvp_runtime.crypto.cycle import cycle_status_line

    regime = _mock_regime()
    record = _cycle(tmp_path, {regime: {"trades": ENOUGH, "total_r": 4.0}})
    assert record["route_status"] == STATUS_ENTRY_CANDIDATE
    assert record["regime_excluded"] == []
    assert "regime_excluded" not in cycle_status_line(record)


def test_a_cycle_with_a_thin_losing_regime_still_enters(tmp_path):
    """One trade below the bar. The gate declines on a demonstration, not on a hint."""
    regime = _mock_regime()
    record = _cycle(tmp_path, {regime: {"trades": ENOUGH - 1, "total_r": -3.0}})
    assert record["route_status"] == STATUS_ENTRY_CANDIDATE
    assert record["regime_excluded"] == []


def test_a_cycle_with_no_regime_evidence_is_unchanged(tmp_path):
    record = _cycle(tmp_path, None)
    assert record["route_status"] == STATUS_ENTRY_CANDIDATE
    assert record["regime_excluded"] == []


# --- the board ------------------------------------------------------------------

def test_the_board_counts_regime_exclusions_as_a_share_of_cycles_read(tmp_path):
    """The same question the demotion reasons answer — why is this not trading — arriving by a
    path that is not a status. A regime-excluded entry stays PAPER_ACTIVE and keeps its routing
    slot, so nothing in the pool says it is inert."""
    import json

    from runtime.mvp_runtime.crypto.dashboard import build_status, render_status_text
    from runtime.mvp_runtime.store import LEDGER_REL, RECORDS_FILE

    ledger = tmp_path / LEDGER_REL
    ledger.mkdir(parents=True, exist_ok=True)
    rows = []
    for i in range(4):
        rows.append(json.dumps({
            "kind": "crypto_cycle",
            "record": {"symbol": "ETHUSDT", "timeframe": "1h", "route_status": STATUS_NO_ENTRY,
                       "regime_excluded": ["S001"] if i < 3 else []},
        }))
    (ledger / RECORDS_FILE).write_text("\n".join(rows) + "\n", encoding="utf-8")

    status = build_status(tmp_path, now=NOW)
    assert status["regime_excluded_cycles"] == {"S001": 3}
    assert status["cycles_read"] == 4
    assert "S001 3/4" in render_status_text(status)


def test_the_board_says_nothing_when_no_strategy_was_excluded(tmp_path):
    """A line that is empty on almost every board teaches its reader to skip it."""
    import json

    from runtime.mvp_runtime.crypto.dashboard import build_status, render_status_text
    from runtime.mvp_runtime.store import LEDGER_REL, RECORDS_FILE

    ledger = tmp_path / LEDGER_REL
    ledger.mkdir(parents=True, exist_ok=True)
    (ledger / RECORDS_FILE).write_text(json.dumps({
        "kind": "crypto_cycle",
        "record": {"symbol": "ETHUSDT", "timeframe": "1h",
                   "route_status": STATUS_ENTRY_CANDIDATE, "regime_excluded": []},
    }) + "\n", encoding="utf-8")

    status = build_status(tmp_path, now=NOW)
    assert status["regime_excluded_cycles"] == {}
    assert "regime 배제" not in render_status_text(status)

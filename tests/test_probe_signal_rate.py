"""The signal probe (2026-09-01).

The tool exists because a backtest trade count did not predict live firing: the pool's
second-busiest backtest (412 trades) opened nothing in 18 live days. So what is pinned
here is the DIAGNOSIS — the three outcomes a probe can return and what separates them —
and the horizon arithmetic that turns a rate into "can this lineage ever confirm".
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scripts.probe_signal_rate as probe
from runtime.mvp_runtime.crypto.forward_confirmation import MIN_HOLDOUT_PERIODS
from runtime.mvp_runtime.crypto.strategy import StrategySpec

SINCE = "2026-08-01T00:00:00Z"


def _spec_dict(**overrides):
    base = {
        "schema_version": "strategy_spec.v1",
        "strategy_id": "S1", "strategy_version": "1.0", "strategy_family": "breakout",
        "symbol_scope": ["BTCUSDT"], "timeframe": "1d", "direction": "long",
        "entry_rules": {"operator": "AND", "conditions": [
            {"feature": "close", "comparison": ">", "value_from": "ma20"},
            {"feature": "adx", "comparison": ">=", "value": 20.0}]},
        "exit_rules": {"stop_model": "atr", "stop_atr": 1.5, "target_atr": 2.0,
                       "max_holding_bars": 10},
        "risk_constraints": {"max_risk_per_trade_R": 1.0},
    }
    base.update(overrides)
    return base


def _entry(**overrides):
    entry = {"strategy_id": "S1-GEN-9", "status": "PAPER_ACTIVE", "champion_score": 0.8,
             "candidate_id": "cand_probe0000000001", "generation_id": "GEN-9",
             "strategy_rule_hash": "", "strategy_spec": _spec_dict()}
    entry.update(overrides)
    return entry


def _bars(n, *, close=105.0, ma20=100.0, adx=25.0, start_day=2):
    rows, candles = [], []
    for i in range(n):
        stamp = "2026-08-%02dT00:00:00Z" % (start_day + i)
        rows.append({"timestamp": stamp, "close": close, "ma20": ma20, "adx": adx, "atr": 2.0})
        candles.append({"open_time": stamp, "open": close - 1, "high": close + 1,
                        "low": close - 2, "close": close, "volume": 10.0, "close_time": stamp})
    return rows, candles


def _probe(entry, rows, candles, timeframe="1d"):
    spec = StrategySpec.from_dict(entry["strategy_spec"])
    return probe.probe_span(entry, spec, rows, candles,
                            symbol="BTCUSDT", timeframe=timeframe, since=SINCE)


def test_a_spec_that_opens_reads_as_firing():
    result = _probe(_entry(), *_bars(6))
    assert result["opens"] > 0
    assert result["verdict"] == probe.FIRES


def test_a_spec_whose_rules_never_match_is_named_stale_not_gated():
    """close below ma20 — the entry rules themselves never fire, whatever the backtest
    said. This is the shape three of the four lineages retired on 2026-09-01 had."""
    rows, candles = _bars(6, close=95.0)
    result = _probe(_entry(), rows, candles)
    assert (result["opens"], result["matched"]) == (0, False)
    assert result["verdict"] == probe.NEVER_MATCHED


def test_a_spec_the_doors_refuse_is_told_apart_from_a_stale_one():
    """The rules match every bar; regime admission refuses every one. Same zero opens,
    different repair — S005-GEN-702's shape, and the reason the two are not one verdict."""
    entry = _entry(regime_evidence={"HIGH_VOLATILITY": {"trades": 40, "total_r": -30.0}})
    rows, candles = _bars(6)
    for row in rows:
        row["market_regime"] = "HIGH_VOLATILITY"
    result = _probe(entry, rows, candles)
    assert (result["opens"], result["matched"]) == (0, True)
    assert result["verdict"] == probe.DOOR_REFUSED


def test_the_horizon_is_whichever_demand_lands_later():
    """A rate can shorten the trade floor's wait and nothing can shorten the slice
    calendar, so the answer is the max — never the friendlier half."""
    fast = probe.confirmation_horizon("4h", opens=30, days=30)     # floor reached early
    assert fast["days_to_confirmable"] == fast["calendar_days"]
    slow = probe.confirmation_horizon("1h", opens=3, days=26)      # rate binds instead
    assert slow["days_to_confirmable"] == slow["days_to_floor"] > slow["calendar_days"]


def test_a_lineage_that_never_opens_has_no_horizon_at_all():
    horizon = probe.confirmation_horizon("4h", opens=0, days=90)
    assert horizon["days_to_confirmable"] is None  # not a large number — an absent one
    assert horizon["opens_per_30d"] == 0.0


def test_the_calendar_demand_tracks_the_forward_width_not_a_copied_number():
    from runtime.mvp_runtime.crypto.forward_confirmation import forward_slice_width_days
    horizon = probe.confirmation_horizon("1d", opens=10, days=30)
    assert horizon["calendar_days"] == round(forward_slice_width_days("1d") * MIN_HOLDOUT_PERIODS)


def test_an_untargeted_timeframe_has_no_calendar_and_says_so():
    horizon = probe.confirmation_horizon("2w", opens=10, days=30)
    assert horizon["calendar_days"] is None and horizon["days_to_confirmable"] is None


# --- the probe must measure the lineage promotion would install ---------------------------

def test_a_candidate_is_replayed_with_the_gating_it_will_actually_have():
    """2026-09-02: the probe read S004-GEN-690 at 36 opens in 60 days and it installed at
    11, because a candidate row carries no ``regime_evidence`` and ``regime_admits`` fails
    OPEN without it. Ranking promotion picks on that number ranks specs by their rules with
    the admission doors switched off."""
    from runtime.mvp_runtime.crypto import pool as pool_store
    candidate = {"candidate_id": "cand_probe0000000002", "generation_id": "GEN-9",
                 "strategy_rule_hash": "", "strategy_spec": _spec_dict(),
                 "backtest_evidence": {
                     "regime_breakdown": {"per_regime": {
                         "HIGH_VOLATILITY": {"trades": 40, "total_r": -30.0}}},
                     "distribution_reference": None}}
    rows, candles = _bars(6)
    for row in rows:
        row["market_regime"] = "HIGH_VOLATILITY"
    # the projection is what the promotion door writes, so the probe sees the same refusal
    assert pool_store.admission_evidence(candidate)["regime_evidence"] == {
        "HIGH_VOLATILITY": {"trades": 40, "total_r": -30.0}}
    result = _probe(candidate, rows, candles)
    assert result["verdict"] == probe.DOOR_REFUSED
    assert result["opens"] == 0


def test_an_entry_that_already_carries_its_evidence_is_not_reconstructed():
    """Probing a POOLED lineage must measure the pool, not a projection of it — the entry's
    own evidence is what routes it today."""
    from runtime.mvp_runtime.crypto import pool as pool_store
    entry = _entry(regime_evidence={"RANGE": {"trades": 5, "total_r": 1.0}},
                   backtest_evidence={"regime_breakdown": {"per_regime": {
                       "RANGE": {"trades": 999, "total_r": -999.0}}}})
    seen = pool_store.as_pool_entry_for_replay(entry)
    assert seen["regime_evidence"] == {"RANGE": {"trades": 5, "total_r": 1.0}}


def test_the_promotion_door_and_the_probe_project_through_one_function():
    """A second copy of the two lookups is how they drift; #743 was the first time one of
    them went missing on the promotion side."""
    import scripts.promote_strategy_candidates as promote
    source = Path(promote.__file__).read_text()
    assert "pool_store.admission_evidence(c)" in source
    assert '"regime_evidence": ((c.get(' not in source  # the inlined copy is gone

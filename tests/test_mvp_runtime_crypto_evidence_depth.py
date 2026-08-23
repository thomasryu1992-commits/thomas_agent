"""The promotion evidence names the WINDOW it was measured over, not just the cost basis.

`backtest_evidence.bars_replayed` has recorded the scored window since the holdout split
landed, and nothing read it. That was tolerable while every candidate saw the same window.
It stopped being tolerable when the factory grew a bar-count floor beneath its calendar
window (`market_data.MIN_FACTORY_BARS` under `FACTORY_DEPTH_DAYS`), taking 1d from 500
collected bars to 2000: re-scoring the same 25 specs at the deeper window moved them from
0 ROBUST / 20 FRAGILE to 12 ROBUST / 12 PROVISIONAL / 1 FRAGILE.

The verdict is therefore partly a statement about the window, and the store holds 1d rows
scored at both depths. This file pins the three things that follow from that: the tier is
measured against the window the factory collects TODAY (so it re-tiers itself when the floor
lands), it ranks below the verdict rather than above it, and it does not refuse — a shallow
verdict is a floor, not an inflated number, so it is attributed rather than blocked.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from runtime.mvp_runtime import timeutil
from runtime.mvp_runtime.crypto import cost, market_data, pool
from runtime.mvp_runtime.crypto.factory import backtest_spec
from runtime.mvp_runtime.crypto.pool import (
    EVIDENCE_DEPTH_RANK_FULL,
    EVIDENCE_DEPTH_RANK_SHALLOW,
    EVIDENCE_DEPTH_RANK_UNRECORDED,
    EVIDENCE_DEPTH_UNRECORDED,
    assert_promotable_evidence_depth,
    candidate_quality,
    current_evidence_depth,
    evidence_depth_of,
    evidence_depth_rank,
    expected_replayed_bars,
    rank_candidates,
)
from runtime.mvp_runtime.crypto.promotion import request_promotion
from runtime.mvp_runtime.crypto.strategy import StrategySpec
from runtime.mvp_runtime.errors import ApprovalBlocked, ToolError

from scripts.promote_strategy_candidates import run_promotion

NOW = timeutil.utc_now_iso()

# What a 1d candidate minted right now is scored over: the collector's target for the
# timeframe, through the factory's holdout split. Read rather than written down, because the
# whole point of the tier is that this number moves.
_TODAY_1D = expected_replayed_bars("1d")


def _current_cost_summary():
    """Evidence scored under the cost model in force.

    The cost tier is the FIRST sort key and refuses at the promotion door, so a fixture with
    a stale basis would decide these tests before depth was ever consulted. Stamping the
    current model keeps each case about its own subject."""
    return {"total_net_r": 10.0, "total_fee_cost_r": 1.0, "total_maker_fee_cost_r": 0.2,
            "total_slippage_cost_r": 0.5, "cost_model": {
                "taker_fee_bps": cost.DEFAULT_TAKER_FEE_BPS,
                "maker_fee_bps": cost.DEFAULT_MAKER_FEE_BPS,
                "slippage_bps": cost.DEFAULT_SLIPPAGE_BPS,
                "stop_slippage_bps": cost.DEFAULT_STOP_SLIPPAGE_BPS,
                "funding_bps_per_interval": cost.DEFAULT_FUNDING_BPS_PER_INTERVAL,
                "funding_source": cost.FUNDING_SOURCE_VENUE,
            }}


def _spec_dict(**overrides):
    base = {
        "schema_version": "strategy_spec.v1",
        "strategy_id": "S1", "strategy_version": "1.0", "strategy_family": "breakout",
        "symbol_scope": ["BTCUSDT"], "timeframe": "1d", "direction": "long",
        "entry_rules": {"operator": "AND",
                        "conditions": [{"feature": "close", "comparison": ">", "value": 0.0}]},
        "exit_rules": {"stop_model": "atr", "stop_atr": 1.5, "target_atr": 2.0,
                       "max_holding_bars": 10},
        "risk_constraints": {"max_risk_per_trade_R": 1.0},
    }
    base.update(overrides)
    return base


def _record(candidate_id="cand_x", *, bars=None, timeframe="1d", verdict="PROVISIONAL",
            cost_summary=None, expectancy=0.35):
    """One candidate row. ``bars=None`` means the field is absent, which is a case under test."""
    evidence = {
        "robustness": {"verdict": verdict, "holdout_status": "CONFIRMED"},
        "closed_count": 40, "win_count": 18,
        "avg_win_R": 2.0, "avg_loss_R": 1.0, "expectancy": expectancy,
        "cost_summary": cost_summary or _current_cost_summary(),
    }
    if bars is not None:
        evidence["bars_replayed"] = bars
    spec = {"strategy_family": "breakout"}
    if timeframe is not None:
        spec["timeframe"] = timeframe
    return {
        "candidate_id": candidate_id,
        "strategy_id": "S001",
        "generation_id": "GEN-001",
        "strategy_spec": spec,
        "champion_score": 0.8,
        "backtest_evidence": evidence,
    }


# --- what the row says about its own window ------------------------------------

def test_a_row_names_the_bars_and_the_calendar_span_it_replayed():
    """Both, because neither alone is the property in question: 2000 bars is 5.5 years at 1d
    and three weeks at 15m, and the regimes an edge has faced are a calendar fact."""
    depth = evidence_depth_of(_record(bars=350))
    assert "350bars" in depth and "_1d_" in depth and "350d" in depth

    faster = evidence_depth_of(_record(bars=350, timeframe="1h"))
    assert "350bars" in faster and "15d" in faster, "the same bar count is a different window"


def test_a_row_with_no_recorded_bar_count_says_so():
    """Reporting today's window would claim a row saw market it was never shown — the rule
    `cost_basis_of` already applies to a missing cost model."""
    assert evidence_depth_of(_record(bars=None)) == EVIDENCE_DEPTH_UNRECORDED
    assert evidence_depth_rank(_record(bars=None)) == EVIDENCE_DEPTH_RANK_UNRECORDED


def test_a_bar_count_with_no_timeframe_is_not_a_window():
    """A count means nothing until you know how long a bar is, so it cannot be tiered."""
    assert evidence_depth_of(_record(bars=350, timeframe=None)) == EVIDENCE_DEPTH_UNRECORDED
    assert evidence_depth_rank(_record(bars=350, timeframe=None)) == EVIDENCE_DEPTH_RANK_UNRECORDED


def test_a_junk_timeframe_reads_as_unknown_rather_than_raising():
    """The store is append-only and durable: a listing must survive a row it cannot parse."""
    assert evidence_depth_rank(_record(bars=350, timeframe="3w")) == EVIDENCE_DEPTH_RANK_UNRECORDED
    assert current_evidence_depth("3w") == EVIDENCE_DEPTH_UNRECORDED


# --- the tier, measured against the window in force today ----------------------

def test_the_tier_is_measured_against_the_window_the_factory_collects_today():
    assert evidence_depth_rank(_record(bars=_TODAY_1D)) == EVIDENCE_DEPTH_RANK_FULL
    assert evidence_depth_rank(_record(bars=_TODAY_1D // 4)) == EVIDENCE_DEPTH_RANK_SHALLOW


def test_the_tier_re_reads_the_factory_window_instead_of_pinning_a_bar_count(monkeypatch):
    """The case this tier was built for: the floor lands, and every stored row is re-tiered
    against the new window WITHOUT the store being touched.

    `backtest_evidence` is durable — deepening the window does not re-score anything, it
    splits the store, exactly as raising the taker default did. So the comparison has to read
    the live collection target the way `current_cost_basis` reads the live fee defaults; a
    bar count written down here would have to be edited in step, and would not be.

    The window is moved at `factory_candle_target` rather than at one of its inputs on
    purpose: the target is a calendar span, a bar floor and a clamp, and this tier must not
    care which of the three is binding. Patching `FACTORY_DEPTH_DAYS` would have proved that
    only while the calendar term was the binding one — which stopped being true at 1d the day
    the bar floor landed."""
    row = _record(bars=_TODAY_1D)
    assert evidence_depth_rank(row) == EVIDENCE_DEPTH_RANK_FULL

    deeper = market_data.factory_candle_target("1d") * 4
    monkeypatch.setattr(market_data, "factory_candle_target", lambda timeframe: deeper)

    assert expected_replayed_bars("1d") > _TODAY_1D, "the window moved"
    assert evidence_depth_rank(row) == EVIDENCE_DEPTH_RANK_SHALLOW, "same row, deeper window"
    assert evidence_depth_of(row) == f"replayed:{_TODAY_1D}bars_1d_{_TODAY_1D}d", (
        "what the row REPLAYED is durable; only its tier moves"
    )


def test_a_collection_gap_is_not_a_shallower_window_but_a_policy_change_is():
    """The tolerance exists to absorb a venue gap or an unclosed final candle, not history.
    The only window change on record is 4x, which no sane tolerance can hide."""
    assert evidence_depth_rank(_record(bars=_TODAY_1D - 2)) == EVIDENCE_DEPTH_RANK_FULL
    assert evidence_depth_rank(_record(bars=_TODAY_1D // 4)) == EVIDENCE_DEPTH_RANK_SHALLOW


def test_a_deeper_window_than_todays_is_not_penalised():
    """A longer window is better supported, not incomparable. Giving depth its own top tier
    would sort every freshly minted candidate beneath the legacy rows the tier exists to flag."""
    assert evidence_depth_rank(_record(bars=_TODAY_1D * 2)) == EVIDENCE_DEPTH_RANK_FULL


def test_the_current_window_is_formatted_by_the_same_function_as_a_stored_one():
    """One formatter, so "what the store holds" and "what the factory collects" cannot drift
    into two spellings of the same window."""
    assert current_evidence_depth("1d") == evidence_depth_of(_record(bars=_TODAY_1D))


def _flat_snapshot(n, timeframe):
    """``n`` well-formed candles at ``timeframe``. Whether they trade is irrelevant here —
    ``bars_replayed`` is the size of the scored slice, not a count of outcomes."""
    step = timedelta(minutes=market_data.TIMEFRAMES[timeframe])
    last_close = timeutil.parse_iso(NOW) - step
    candles = []
    for i in range(n):
        price = 100.0 + (i % 11)
        close_time = last_close - (n - 1 - i) * step
        candles.append({
            "open_time": timeutil.format_iso(close_time - step),
            "open": price, "high": price + 1.5, "low": price - 1.5,
            "close": price, "volume": 10.0 + (i % 7),
            "close_time": timeutil.format_iso(close_time),
        })
    return {"symbol": "BTCUSDT", "timeframe": timeframe, "candles": candles,
            "is_synthetic": False}


@pytest.mark.parametrize("timeframe", ["1d", "4h"])
def test_a_candidate_minted_right_now_is_never_shallow(timeframe):
    """The two sides of the comparison, pinned against a real backtest rather than against
    the arithmetic that relates them.

    `bars_replayed` is written by `factory.backtest_spec` and `expected_replayed_bars` is
    read by the tier, and they meet only if both apply the SAME holdout split to the SAME
    collection target. If they ever disagree the tier marks every fresh candidate SHALLOW on
    day one, which is the one failure that would make it worthless. Two timeframes, because
    the target is per-timeframe — a flat bar count would pass at 1d and fail at 4h."""
    target = market_data.factory_candle_target(timeframe)
    spec = StrategySpec.from_dict(_spec_dict(timeframe=timeframe))
    evidence = backtest_spec(spec, _flat_snapshot(target, timeframe))
    minted = {"strategy_spec": spec.to_dict(), "backtest_evidence": evidence}

    assert evidence["bars_replayed"] == expected_replayed_bars(timeframe)
    assert evidence_depth_rank(minted) == EVIDENCE_DEPTH_RANK_FULL
    assert evidence_depth_of(minted) == current_evidence_depth(timeframe)


def test_the_calendar_span_is_what_makes_two_timeframes_comparable():
    """Why the span is reported next to the bar count: 2100 bars at 4h and 350 at 1d are the
    same 350 days of market, and it is the days that decide which regimes an edge has seen.

    Asserted on rows rather than on today's two windows, because the factory is free to set
    different calendar depths per timeframe — the bar floor already does exactly that at 1d."""
    assert evidence_depth_of(_record(bars=350, timeframe="1d")).endswith("_350d")
    assert evidence_depth_of(_record(bars=2100, timeframe="4h")).endswith("_350d")


def test_the_quality_view_carries_the_window_as_a_field():
    """A property OF the verdict, like the cost basis is a property of the expectancy — so a
    consumer can refuse to compare across windows rather than re-derive it."""
    q = candidate_quality(_record(bars=_TODAY_1D // 4))
    assert q["evidence_depth_rank"] == EVIDENCE_DEPTH_RANK_SHALLOW
    assert "bars" in q["evidence_depth"]


# --- where the tier sits in the ordering ---------------------------------------

def test_depth_breaks_ties_inside_a_verdict_tier():
    """Same verdict, same numbers: the row with more market behind it is the better bet, and
    the ids are chosen so store order alone would have produced the opposite."""
    shallow = _record("cand_a", bars=_TODAY_1D // 4)
    current = _record("cand_z", bars=_TODAY_1D)
    assert [c["candidate_id"] for c in rank_candidates([shallow, current])] == ["cand_z", "cand_a"]


def test_the_depth_tier_never_outranks_the_verdict():
    """The asymmetry against the cost tier, made executable. A cheap basis FLATTERS the
    numbers beside it, so it leads. A shallow window DEPRESSES the verdict the row is already
    sorted by — leading with it would charge one row twice for a single shortfall."""
    shallow_robust = _record("cand_z", bars=_TODAY_1D // 4, verdict="ROBUST")
    deep_fragile = _record("cand_a", bars=_TODAY_1D, verdict="FRAGILE")
    ranked = [c["candidate_id"] for c in rank_candidates([deep_fragile, shallow_robust])]
    assert ranked == ["cand_z", "cand_a"]


def test_the_cost_basis_still_leads_the_depth_tier():
    """Pins the whole key order: a row that never paid the real fee sorts below a shallow row
    that did, however much market it replayed."""
    cheap_but_deep = _record("cand_a", bars=_TODAY_1D, cost_summary={
        "total_net_r": 10.0, "total_fee_cost_r": 1.0, "total_maker_fee_cost_r": 0.0,
        "total_slippage_cost_r": 0.5, "cost_model": {"taker_fee_bps": 2.5, "slippage_bps": 3.0},
    })
    honest_but_shallow = _record("cand_z", bars=_TODAY_1D // 4)
    ranked = [c["candidate_id"] for c in rank_candidates([cheap_but_deep, honest_but_shallow])]
    assert ranked == ["cand_z", "cand_a"]


# --- the operator surface ------------------------------------------------------

def test_the_listing_states_the_window_before_the_numbers(monkeypatch, capsys):
    """Stated above the rows, like the cost basis: a caveat under a screen of figures is one
    nobody reads, and this one changes what a FRAGILE means."""
    from scripts import promote_strategy_candidates as prom

    monkeypatch.setattr(prom.pool_store, "read_candidates", lambda root: [_record(bars=_TODAY_1D)])
    prom.main(["--list"])

    out = capsys.readouterr().out
    assert "replayed" in out
    note_at = out.index("counted")
    row_at = out.index("cand_x")
    assert note_at < row_at, "the window must be stated above the candidate rows"


def test_a_mixed_depth_store_is_flagged_and_the_rows_say_which_they_are(monkeypatch, capsys):
    """The store holds 1d rows scored at both depths. The listing names the split, the tier
    orders on it, and each row carries its own mark — so the warning, the ordering and the
    rows agree instead of each quietly undoing the last."""
    from scripts import promote_strategy_candidates as prom

    monkeypatch.setattr(prom.pool_store, "read_candidates", lambda root: [
        _record("cand_shallow", bars=_TODAY_1D // 4),
        _record("cand_current", bars=_TODAY_1D),
    ])
    prom.main(["--list"])

    out = capsys.readouterr().out
    assert "MIXED WINDOW DEPTHS" in out
    assert "not shown the same market" in out.lower()
    assert "FLOOR" in out, "the direction of the error is the only actionable part"
    shallow_row = next(line for line in out.splitlines() if line.startswith("cand_shallow"))
    current_row = next(line for line in out.splitlines() if line.startswith("cand_current"))
    assert "depth=SHALLOW" in shallow_row
    assert "depth=" not in current_row, "an unremarkable row stays unmarked"


# --- the door: ranked and recorded, not refused --------------------------------

def _seed(tmp_path, *, bars):
    """``bars=None`` seeds a row that records no window — the 41 rows the live store holds."""
    spec = StrategySpec.from_dict(_spec_dict())
    evidence = {"closed_count": 20, "expectancy": 0.5,
                "robustness": {"holdout_status": "CONFIRMED"},
                "cost_summary": _current_cost_summary()}
    if bars is not None:
        evidence["bars_replayed"] = bars
    pool.append_candidates([{
        "strategy_id": spec.strategy_id,
        "strategy_rule_hash": spec.strategy_rule_hash,
        "generation_id": "GEN-001",
        "status": "BACKTESTED",
        "champion_score": 0.5,
        "strategy_spec": spec.to_dict(),
        "backtest_evidence": evidence,
        "evidence_input_sha256": "sha256:test",
        "provenance": "mvp_factory",
    }], root=tmp_path)


def test_shallow_evidence_is_not_refused_at_the_promotion_door(tmp_path):
    """The decision, made executable so it cannot drift into a gate by accident.

    Shallow evidence is not an inflated number — its verdict errs AGAINST the candidate — so
    the cost tier's argument for a refusal does not transfer: the DEPTH gate stays silent on
    SHALLOW and refuses only the unreadable. Since Thomas's 5-3 (2026-08-11) the strictness
    lives one gate over instead: the OBSERVATION entry bar refuses a SHALLOW *entrant* under
    its own reason code and its own audited escape, which is what the escape below isolates —
    this test keeps pinning that no depth refusal fires."""
    _seed(tmp_path, bars=_TODAY_1D // 4)
    run_promotion(selectors=["S1"], promoted_by="Thomas", reason="r",
                  keep_active=False, live_tier="LIVE", root=tmp_path, now=NOW, without_approval=True,
                  allow_below_entry_bar=True)
    assert len(pool.load_active_pool(tmp_path)["active_strategies"]) == 1


def test_the_window_behind_a_promotion_rides_onto_the_ledger(tmp_path):
    """What replaces the refusal. A pool entry outlives the argv that installed it, so "which
    window is this lineage's evidence standing on" has to be answerable later — and here the
    ledger is the ONLY record, because no gate refused it on the way through."""
    _seed(tmp_path, bars=_TODAY_1D // 4)
    summary = run_promotion(selectors=["S1"], promoted_by="Thomas", reason="r",
                            keep_active=False, live_tier="LIVE", root=tmp_path, now=NOW,
                            without_approval=True, allow_below_entry_bar=True)
    assert summary["evidence_depths"] == [f"replayed:{_TODAY_1D // 4}bars_1d_{_TODAY_1D // 4}d"]
    assert summary["evidence_depths"] != [current_evidence_depth("1d")], "the split is legible"


def test_a_promotion_on_todays_window_records_that_too(tmp_path):
    """Recorded whether or not it is remarkable — a field that only appears when something is
    wrong makes its absence ambiguous between "fine" and "not checked"."""
    _seed(tmp_path, bars=_TODAY_1D)
    summary = run_promotion(selectors=["S1"], promoted_by="Thomas", reason="r",
                            keep_active=False, live_tier="LIVE", root=tmp_path, now=NOW,
                            without_approval=True, allow_below_entry_bar=True)
    assert summary["evidence_depths"] == [current_evidence_depth("1d")]
    assert summary["unrecorded_evidence_depth_escape"] is False


# --- ...but a window that cannot be READ is refused ----------------------------
#
# Counted on the live store while the depth floor was being reviewed (PR #339): the pool spans
# four depths already — 25 rows at 1400 bars, 12 at 500, 18 at 350 — and 41 rows record no
# depth at all. Those 41 arrive through the C7 import, which copies an outside pool's entries
# verbatim; `bars_replayed` has been written by every factory row since C8a.

def test_the_door_refuses_evidence_that_records_no_window(tmp_path):
    """Every argument for ranking a SHALLOW row instead of refusing it collapses here.

    "The error runs against the candidate" is a claim about a window you can see. An
    unrecorded one has no readable direction — which is exactly why the cost tier refuses
    `cost_model_unrecorded` rather than ranking it — and it is worse than the cost case,
    because nothing re-windows a candidate: the snapshot behind `evidence_input_sha256` is not
    kept, so no later arithmetic can recover what `expectancy_at` recovers for cost."""
    _seed(tmp_path, bars=None)
    with pytest.raises(SystemExit) as exc:
        run_promotion(selectors=["S1"], promoted_by="Thomas", reason="r",
                      keep_active=False, live_tier="LIVE", root=tmp_path, now=NOW, without_approval=True)
    assert "CANDIDATE_EVIDENCE_DEPTH_UNRECORDED" in str(exc.value)
    assert pool.load_active_pool(tmp_path) == {"active_strategies": []}, "nothing installed"


def test_the_ask_refuses_what_the_install_would_refuse(tmp_path):
    """An ask that cannot execute is worse than no ask: it spends Thomas's answer on a
    promotion the next step was always going to block. Same rule the stale-basis gate uses."""
    _seed(tmp_path, bars=None)
    with pytest.raises(ApprovalBlocked) as exc:
        request_promotion(["S1"], keep_active=False, live_tier="LIVE", now=NOW, candidates_root=tmp_path)
    assert exc.value.reason_code == "CANDIDATE_EVIDENCE_DEPTH_UNRECORDED"


def test_the_unrecorded_depth_escape_promotes_and_is_recorded(tmp_path):
    """An escape that leaves no trace is an escape nobody can audit — and the depth string
    rides along, so the ledger says the window was unreadable rather than merely unremarked."""
    _seed(tmp_path, bars=None)
    summary = run_promotion(selectors=["S1"], promoted_by="Thomas", reason="r",
                            keep_active=False, live_tier="LIVE", root=tmp_path, now=NOW, without_approval=True,
                            allow_unrecorded_evidence_depth=True, allow_below_entry_bar=True)
    assert summary["unrecorded_evidence_depth_escape"] is True
    assert summary["evidence_depths"] == [EVIDENCE_DEPTH_UNRECORDED]
    assert len(pool.load_active_pool(tmp_path)["active_strategies"]) == 1


def test_the_two_escapes_are_separate_keys(tmp_path):
    """Two doors that fail for different reasons must not open with one key: the cost escape
    does not admit an unreadable window, and each is recorded on its own."""
    _seed(tmp_path, bars=None)
    with pytest.raises(SystemExit) as exc:
        run_promotion(selectors=["S1"], promoted_by="Thomas", reason="r",
                      keep_active=False, live_tier="LIVE", root=tmp_path, now=NOW, without_approval=True,
                      allow_stale_cost_basis=True)
    assert "CANDIDATE_EVIDENCE_DEPTH_UNRECORDED" in str(exc.value)


def test_the_refusal_names_the_rows_and_the_escape():
    """A gate that will not say which rows it stopped sends the operator back to the listing."""
    with pytest.raises(ToolError) as exc:
        assert_promotable_evidence_depth([_record("cand_blind", bars=None)])
    assert exc.value.reason_code == "CANDIDATE_EVIDENCE_DEPTH_UNRECORDED"
    assert "cand_blind" in exc.value.reason
    assert "--allow-unrecorded-evidence-depth" in exc.value.reason


def test_a_known_shallow_window_is_never_what_the_gate_stops():
    """The distinction the whole tier turns on, asserted directly on the gate."""
    assert_promotable_evidence_depth([
        _record("cand_shallow", bars=_TODAY_1D // 4),
        _record("cand_current", bars=_TODAY_1D),
    ])


def test_the_listing_names_the_refused_rows(monkeypatch, capsys):
    """The listing and the gate must agree: a row the door will stop has to be visible as such
    before an operator selects it, not discovered at the ask."""
    from scripts import promote_strategy_candidates as prom

    monkeypatch.setattr(prom.pool_store, "read_candidates", lambda root: [
        _record("cand_blind", bars=None),
        _record("cand_current", bars=_TODAY_1D),
    ])
    prom.main(["--list"])

    out = capsys.readouterr().out
    assert "CANDIDATE_EVIDENCE_DEPTH_UNRECORDED" in out
    assert "1 row(s) record NO window" in out
    assert "--allow-unrecorded-evidence-depth" in out

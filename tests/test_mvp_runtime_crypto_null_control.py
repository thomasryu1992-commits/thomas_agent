"""Every gate here judges a spec against ZERO, and zero is not this system's null.

A strategy's R is `stop_atr x ATR` against a fixed-bps round trip, so the number a holdout
returns is dominated by the exit and cost geometry the whole library shares. Measured 2026-08-05
over 12 cells, a RANDOM entry through the same exits returns -0.1274R at 1h and -0.0345R at 1d —
so `expectancy >= 0` is a bar standing three different heights depending on the timeframe.

These pin the parts of the answer that are easy to get quietly wrong: which bars count as out of
sample, that the null can never be mistaken for a strategy, and that "nothing was old enough to
measure" never renders as "nothing beat its null".
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.crypto import factory, null_control
from runtime.mvp_runtime.crypto.strategy import StrategySpec

CANDLES = [{"open_time": f"2026-0{1 + i // 28}-{1 + i % 28:02d}T00:00:00Z"} for i in range(56)]


# --- which bars are honestly out of sample --------------------------------------

def test_the_bar_a_spec_was_minted_in_is_in_sample():
    """Strictly after, not at-or-after. A spec minted during a bar was minted with that bar's
    data already in its scored window, so the bar it was born in cannot be evidence about it.
    Off by one in the direction that costs a bar rather than leaking one."""
    assert null_control.post_mint_index(CANDLES, CANDLES[10]["open_time"]) == 11


def test_a_mint_newer_than_every_bar_measures_nothing():
    """Not an empty edge — an empty window. The caller must be able to tell them apart."""
    assert null_control.post_mint_index(CANDLES, "2099-01-01T00:00:00Z") == len(CANDLES)


def test_an_unstamped_record_measures_nothing():
    """A row without `created_at_utc` has no anchor, and guessing one would put its own scored
    data back in its evaluation window — the exact defect this module exists to avoid."""
    assert null_control.post_mint_index(CANDLES, "") == len(CANDLES)


def test_the_age_floor_is_a_span_not_a_bar_count():
    """The defect this replaced. A bar floor means a different span at every rung: at 120 bars
    the store's oldest spec (13 days) is refused at 4h with 78 bars and admitted at 1h with 312 —
    the same spec, the same evidence, ready on one timeframe and not on another purely by how the
    ladder slices the calendar."""
    days = null_control.MIN_POST_MINT_DAYS
    for timeframe, minutes in (("15m", 15), ("1h", 60), ("4h", 240), ("1d", 1440)):
        assert null_control.min_post_mint_bars(timeframe) == int(days * 1440 / minutes)


def test_an_unknown_timeframe_refuses_to_measure():
    """Fail closed: a typo must not resolve to a bar count that means nothing."""
    assert null_control.min_post_mint_bars("bogus") > 10 ** 6


# --- the null is not a strategy -------------------------------------------------

def test_the_null_feature_is_not_mintable():
    """The property that keeps a null out of the pool by construction rather than by care:
    `NULL_FEATURE` is absent from the validator's vocabulary, so any spec naming it is refused
    at the door every real spec goes through."""
    numeric, categorical = factory.known_features(factory.market_data.BINANCE_FUTURES)
    assert null_control.NULL_FEATURE not in numeric
    assert null_control.NULL_FEATURE not in categorical


def test_a_null_spec_fails_the_validator():
    spec = StrategySpec.from_dict({
        "schema_version": "strategy_spec.v1", "strategy_id": "N001", "strategy_version": "1.0",
        "strategy_family": null_control.NULL_FAMILY, "symbol_scope": ["BTCUSDT"],
        "timeframe": "4h", "direction": "long",
        "entry_rules": {"operator": "AND", "conditions": [
            {"feature": null_control.NULL_FEATURE, "comparison": "<=", "value": 0.1}]},
        "exit_rules": {"stop_model": "atr", "stop_atr": 1.5, "target_atr": 3.0,
                       "max_holding_bars": 24},
        "risk_constraints": {"max_risk_per_trade_R": 1.0},
    })
    verdict = factory.validate_strategy(spec)
    assert verdict["approved_for_backtest"] is False
    assert "BLOCK_UNKNOWN_FEATURE" in verdict["block_reasons"]


# --- unmeasurable is a result, not a silence ------------------------------------

def test_nothing_old_enough_never_reads_as_nothing_beat_its_null():
    """On the day this lands, every spec is too young and that IS the finding. A summary that
    reported `specs_beating_null: 0` would be the same shape as a real negative result."""
    summary = null_control.summarize([
        {"status": "too_young", "post_mint_bars": 4},
        {"status": "too_young", "post_mint_bars": 9},
        {"status": "too_few_trades", "post_mint_bars": 400, "trades": 2},
    ])
    assert summary["measured_count"] == 0
    assert "specs_beating_null" not in summary
    assert summary["statuses"] == {"too_young": 2, "too_few_trades": 1}


def test_the_status_line_says_why_it_measured_nothing():
    record = null_control.build_record(
        [{"symbol": "BTCUSDT", "timeframe": "4h",
          "measurements": [{"status": "too_young", "post_mint_bars": 3}]}],
        now="2026-08-05T00:00:00Z", symbol="BTCUSDT", timeframe="4h")
    line = null_control.status_line(record)
    assert "measured=0" in line and "too_young=1" in line


def test_a_measured_summary_reports_both_arms():
    summary = null_control.summarize([
        {"status": "measured", "edge_vs_null": 0.08, "real_r_per_trade": 0.02,
         "null_r_per_trade_median": -0.06, "post_mint_bars": 400},
        {"status": "measured", "edge_vs_null": -0.03, "real_r_per_trade": -0.09,
         "null_r_per_trade_median": -0.06, "post_mint_bars": 500},
        {"status": "too_young", "post_mint_bars": 8},
    ])
    assert summary["measured_count"] == 2
    assert summary["specs_beating_null"] == 1
    assert summary["median_real_r_per_trade"] == pytest.approx(-0.035)
    assert summary["median_null_r_per_trade"] == pytest.approx(-0.06)
    assert summary["statuses"]["too_young"] == 1


# --- the record grants nothing ---------------------------------------------------

def test_the_record_installs_nothing_and_is_hashed():
    record = null_control.build_record([], now="2026-08-05T00:00:00Z",
                                       symbol="ETHUSDT", timeframe="1h")
    assert record["installation_effect"] == "NONE"
    assert record["record_type"] == null_control.RECORD_TYPE
    assert record["record_sha256"].startswith("sha256:")


def test_the_ledger_kind_is_registered():
    """An unregistered kind is dropped at the append door, so the measurement would accumulate
    nothing and say so nowhere."""
    from runtime.mvp_runtime import store

    assert null_control.LEDGER_KIND in store._RECORD_KINDS


def test_the_scheduler_admits_the_kind():
    from runtime.mvp_runtime import scheduler

    assert scheduler.KIND_NULL_CONTROL in scheduler.KINDS


def test_the_scheduled_fire_replays_the_frame_the_factory_MINED(tmp_path, monkeypatch):
    """The fire must enrich its frame with all five legs, not just the price feeds.

    The defect this pins: the fire attached `attach_feeds` alone, so every htf_*,
    ref_*/rel_strength_*, xs_* and positioning_* column was None down the whole window and
    `measure_spec` answered `unsuppliable` — for good, since the gap was in the collection
    rather than in the spec. 65 of the 798 selected specs in the store read one of those
    columns on 2026-08-08, and they are the non-price families the pool most needs judged.

    Asserted on the frame rather than on a status because `too_young` is checked first and is
    the answer for every cell until ~2026-08-23, which is precisely why this went unseen.

    ETHUSDT because BTCUSDT *is* `REFERENCE_SYMBOL` and the reference leg correctly skips
    itself; 4h because 1d has no higher timeframe to attach.
    """
    from runtime.mvp_runtime import scheduler
    from runtime.mvp_runtime.crypto import features
    from runtime.mvp_runtime.control import ControlStore
    from runtime.mvp_runtime.scheduler import ScheduleStore, build_schedule, run_due

    seen: list[factory.ReplayFrame] = []
    real_build = factory.build_replay_frame
    monkeypatch.setattr(
        factory, "build_replay_frame",
        lambda snapshot, **kw: seen.append(real_build(snapshot, **kw)) or seen[-1],
    )
    schedule = build_schedule(kind=scheduler.KIND_NULL_CONTROL, request="ETHUSDT 4h",
                              interval_seconds=86400, created_by="op", now="2026-07-22T10:00:00Z")
    store = ScheduleStore(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.add(schedule)
    summary = run_due(store, now="2026-07-23T11:00:00Z", control_store=ControlStore(tmp_path),
                      repo_root=tmp_path)

    assert summary["fired"] == 1
    assert seen, "the fire never built a replay frame"
    rows = seen[0].rows
    for column in ("htf_market_regime", "rel_strength_roc_4", "xs_rank_pct"):
        assert any(row.get(column) is not None for row in rows), f"{column} never supplied"
    # The whole vocabulary each leg owns, so a leg that lands half-attached is caught too.
    for group in (features.HTF_COLUMNS, features.REFERENCE_COLUMNS, features.XS_NUMERIC_COLUMNS):
        for column in group:
            assert column in rows[0], f"{column} missing from the frame entirely"

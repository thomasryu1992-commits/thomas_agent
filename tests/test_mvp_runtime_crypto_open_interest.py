"""Open interest — the positioning leg beside funding and liquidations.

Funding says what a position costs to hold; liquidations say what was forcibly closed;
open interest says how much is **outstanding**. It rides the Coinalyze feed the runtime
already authorizes, so this widens what is read, never what may be reached.

The rules under test: raw OI never becomes a mintable threshold (it is venue-scale and
means nothing across symbols), the normalized derivatives do, and an absent or failed
feed leaves every column indeterminate — the liquidation posture, so no oi_* condition
can match on a fabricated value."""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.crypto import cycle, factory, market_data
from runtime.mvp_runtime.crypto.cycle import attach_feeds
from runtime.mvp_runtime.crypto.features import build_feature_rows, latest_feature_row
from runtime.mvp_runtime.crypto.market_data import (
    OPEN_INTEREST_DEGRADED,
    MockMarketDataCollector,
    NoLiquidationFeed,
)
from runtime.mvp_runtime.crypto.strategy import StrategySpec, evaluate_spec
from runtime.mvp_runtime.errors import ToolError

NOW = "2026-07-25T12:00:00Z"

OI_COLUMNS = ("open_interest", "open_interest_change_pct", "open_interest_zscore")


def _candles(n=40):
    return [{
        "open_time": f"2026-06-{d:02d}T00:00:00Z" if d <= 30 else f"2026-07-{d - 30:02d}T00:00:00Z",
        "close_time": f"2026-06-{d + 1:02d}T00:00:00Z" if d < 30 else f"2026-07-{d - 29:02d}T00:00:00Z",
        "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 10.0,
    } for d in range(1, n + 1)]


def _snapshot(oi_series=None, **extra):
    snap = {"symbol": "BTCUSDT", "timeframe": "1d", "candles": _candles(), **extra}
    if oi_series is not None:
        snap["open_interest"] = oi_series
    return snap


def _oi_series(values):
    out = []
    for i, v in enumerate(values, start=1):
        day = i if i <= 30 else i - 30
        month = 6 if i <= 30 else 7
        out.append({"timestamp": f"2026-{month:02d}-{day:02d}T00:00:00Z", "open_interest": float(v)})
    return out


# --- features -----------------------------------------------------------------

def test_normalized_derivatives_are_computed_from_the_series():
    row = latest_feature_row(_snapshot(_oi_series([1000 + 50 * i for i in range(40)])))
    assert row["open_interest"] == pytest.approx(1000 + 50 * 39)
    assert row["open_interest_change_pct"] > 0          # OI still climbing
    assert row["open_interest_zscore"] is not None


def test_change_is_measured_between_READINGS_not_between_bars():
    """The defect this pins. The feed is daily; a context may be 15m/1h/4h. Diffing the
    ALIGNED column bar-over-bar makes the change exactly zero on every bar between two
    daily readings — 23 of 24 on an hourly frame. Measured on a real 12,000-bar ETH 1h
    frame before the fix: the aligned series moved on 4.2% of bars and the squeeze
    conditions intersected on 0 of 12,000, so the families could not fire at all.

    Here: four hourly bars per daily reading. Every bar must carry the last KNOWN daily
    change, not 0.0 for the three bars that merely repeat it."""
    candles = [{
        "open_time": f"2026-07-{d:02d}T{h:02d}:00:00Z",
        "close_time": f"2026-07-{d:02d}T{h + 1:02d}:00:00Z",
        "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 10.0,
    } for d in (2, 3, 4) for h in (0, 1, 2, 3)]
    oi = [{"timestamp": f"2026-07-{d:02d}T00:00:00Z", "open_interest": v}
          for d, v in ((1, 1000.0), (2, 1100.0), (3, 1210.0), (4, 1331.0))]

    rows = build_feature_rows({"symbol": "BTCUSDT", "timeframe": "1h",
                               "candles": candles, "open_interest": oi})
    changes = [r["open_interest_change_pct"] for r in rows]
    assert all(c is not None for c in changes), "a bar between readings lost the change"
    assert all(c == pytest.approx(0.10) for c in changes), changes  # +10% day over day
    assert changes.count(0.0) == 0, "bar-over-bar diffing is back"


def test_a_falling_series_reports_a_negative_change():
    row = latest_feature_row(_snapshot(_oi_series([2000 - 20 * i for i in range(40)])))
    assert row["open_interest_change_pct"] < 0


@pytest.mark.parametrize("snapshot_kwargs,case", [
    ({}, "feed absent"),
    ({"oi_series": []}, "fetch failed: key present, empty"),
])
def test_no_usable_series_leaves_every_column_indeterminate(snapshot_kwargs, case):
    """Never a constant. A fabricated zero would let `change_pct <= x` match on nothing
    — the liquidation_spike_ratio hazard this deliberately does not repeat."""
    snapshot = _snapshot(**snapshot_kwargs) if snapshot_kwargs else _snapshot()
    row = latest_feature_row(snapshot)
    assert all(row[col] is None for col in OI_COLUMNS), case


def _oi_spec(conditions, **overrides):
    base = {
        "schema_version": "strategy_spec.v1",
        "strategy_id": "S1", "strategy_version": "1.0", "strategy_family": "oi_squeeze_long",
        "symbol_scope": ["BTCUSDT"], "timeframe": "1d", "direction": "long",
        "entry_rules": {"operator": "AND", "conditions": conditions},
        "exit_rules": {"stop_model": "atr", "stop_atr": 1.5, "target_atr": 3.0, "max_holding_bars": 12},
        "risk_constraints": {"max_risk_per_trade_R": 1.0},
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize("feature", ["open_interest_change_pct", "open_interest_zscore"])
@pytest.mark.parametrize("comparison,value", [(">", -1e12), ("<", 1e12)])
def test_an_oi_condition_cannot_match_without_the_feed(feature, comparison, value):
    """Both permissive directions: indeterminate satisfies neither."""
    row = latest_feature_row(_snapshot())
    spec = StrategySpec.from_dict(_oi_spec([{"feature": feature, "comparison": comparison, "value": value}]))
    assert evaluate_spec(spec, row).matched is False


# --- factory vocabulary + families --------------------------------------------

def test_normalized_oi_features_are_mintable():
    spec = StrategySpec.from_dict(_oi_spec([
        {"feature": "open_interest_change_pct", "comparison": ">=", "value": 0.03},
        {"feature": "open_interest_zscore", "comparison": ">=", "value": 1.0},
    ]))
    assert factory.validate_strategy(spec)["approved_for_backtest"] is True


def test_raw_open_interest_stays_unmintable():
    """It is a venue-scale quantity: a threshold on it means something different on
    every symbol, and something different again after the venue grows. The row carries
    it as evidence; the factory may not build a rule on it."""
    assert "open_interest" not in factory.NUMERIC_FEATURES
    spec = StrategySpec.from_dict(_oi_spec([
        {"feature": "open_interest", "comparison": ">", "value": 1_000_000.0}]))
    assert "BLOCK_UNKNOWN_FEATURE" in factory.validate_strategy(spec)["block_reasons"]


def test_oi_families_are_present_and_generate_valid_specs():
    families = {t.family for t in factory.templates_for_timeframe("1h")}
    assert factory.OI_FAMILIES <= families
    batch = factory.generate_batch("GEN-001", seed=5, timeframe="1h")
    assert batch["accepted_count"] == batch["requested_count"]
    for spec_dict in batch["specs"]:
        assert factory.validate_strategy(StrategySpec.from_dict(spec_dict))["approved_for_backtest"] is True


# --- the feed has to reach the window being replayed --------------------------

def test_oi_families_drop_where_the_feed_cannot_reach_the_replay_window():
    """1d replays a 2,000-BAR floor, i.e. 2,000 days, against 520 days of feed. Minting there
    scores the family on a window ~74% blind in its own feature — `unsuppliable_features` does
    not catch that, because the column is populated on the newest quarter."""
    for timeframe in ("15m", "1h", "4h"):
        families = {t.family for t in factory.templates_for_timeframe(timeframe)}
        assert factory.OI_FAMILIES <= families, timeframe
    assert not (factory.OI_FAMILIES & {t.family for t in factory.templates_for_timeframe("1d")})


def test_the_oi_gate_reads_the_same_depth_the_fetch_asks_for():
    """The property whose absence made this reachable: the gate and the fetch were two numbers.

    `cycle` asked the feed for its own private 520 and `factory` had no opinion at all, so the
    day `MIN_FACTORY_BARS` pushed 1d past that depth there was nothing to notice it. If a future
    edit moves the fetch, this fails rather than silently re-opening the window."""
    assert cycle._LIQUIDATION_DAYS is market_data.DERIVATIVE_HISTORY_DAYS
    # ...and the gate is that constant against the window, not a hardcoded timeframe list.
    assert factory._oi_feed_reaches("4h") is True
    assert factory._oi_feed_reaches("1d") is False
    assert factory._oi_feed_reaches("nonsense") is False


def test_the_oi_gate_would_reopen_if_the_feed_got_deeper(monkeypatch):
    """Stated as a property of the depth, so nobody has to re-derive which timeframes bind."""
    monkeypatch.setattr(market_data, "DERIVATIVE_HISTORY_DAYS", 2_000)
    assert factory._oi_feed_reaches("1d") is True
    assert factory.OI_FAMILIES <= {t.family for t in factory.templates_for_timeframe("1d")}


# --- the feed seam ------------------------------------------------------------

class _FeedWithOI:
    feed_id = "coinalyze_market_data"

    def liquidation_history(self, symbol, *, days, timeout_seconds):
        return [{"timestamp": "2026-07-01T00:00:00Z", "long_liquidation": 1.0, "short_liquidation": 2.0}]

    def open_interest_history(self, symbol, *, days, timeout_seconds):
        return [{"timestamp": "2026-07-01T00:00:00Z", "open_interest": 1234.0}]


class _FeedWhoseOIFails(_FeedWithOI):
    def open_interest_history(self, symbol, *, days, timeout_seconds):
        raise ToolError("TOOL_TRANSPORT", "open-interest request failed or timed out")


def test_attach_feeds_collects_open_interest_through_the_same_feed(tmp_path):
    # `root` because the feed step also accumulates the hourly series (`oi_store`), and a
    # helper called without one resolves to the REPO's state directory — which on the live
    # server is the volume the containers own. CI's state-dir guard catches it; the test
    # supplying a root is the fix it names.
    snapshot = _snapshot()
    reasons, status = attach_feeds(snapshot, collector=MockMarketDataCollector(),
                                   liquidation_feed=_FeedWithOI(), now=NOW, root=tmp_path)
    assert status["open_interest"] == "ok"
    assert snapshot["open_interest"] and OPEN_INTEREST_DEGRADED not in reasons


def test_a_failed_open_interest_fetch_degrades_only_itself(tmp_path):
    """Liquidations can be fine while OI is not — separate key, separate reason code."""
    snapshot = _snapshot()
    reasons, status = attach_feeds(snapshot, collector=MockMarketDataCollector(),
                                   liquidation_feed=_FeedWhoseOIFails(), now=NOW, root=tmp_path)
    assert status["liquidations"] == "ok" and status["open_interest"] == "degraded"
    assert OPEN_INTEREST_DEGRADED in reasons
    assert snapshot["open_interest"] == []          # series semantics: present, empty
    assert latest_feature_row(snapshot)["open_interest_change_pct"] is None


def test_the_null_feed_reports_open_interest_absent():
    snapshot = _snapshot()
    reasons, status = attach_feeds(snapshot, collector=MockMarketDataCollector(),
                                   liquidation_feed=NoLiquidationFeed(), now=NOW)
    assert status["open_interest"] == "absent"
    assert "open_interest" not in snapshot          # absent ≠ degraded
    assert OPEN_INTEREST_DEGRADED not in reasons


# --- family selectivity: a spec must trade enough to BE judged --------------------

def _entry(family, timeframe="1h"):
    template = next(t for t in factory.templates_for_timeframe(timeframe) if t.family == family)
    return template, template.entry_builder(template.base_params)


def _spec_of(template, conditions, symbol="BTCUSDT"):
    return StrategySpec.from_dict({
        "schema_version": "strategy_spec.v1", "strategy_id": "S1", "strategy_version": "1.0",
        "strategy_family": template.family, "symbol_scope": [symbol],
        "timeframe": template.timeframe, "direction": template.direction,
        "entry_rules": {"operator": "AND", "conditions": conditions},
        # `target_atr` is derived from (stop_atr, reward_risk) since 2026-08-25 — same
        # expression the mint path uses, so this helper keeps building the spec the template
        # would actually produce.
        "exit_rules": {"stop_model": "atr", "stop_atr": template.base_params["stop_atr"],
                       "target_atr": round(template.base_params["stop_atr"]
                                           * template.base_params["reward_risk"], 4),
                       "max_holding_bars": int(template.base_params["max_holding_bars"])},
        "risk_constraints": {"max_risk_per_trade_R": 1.0},
    })


@pytest.mark.parametrize("family,excluded", [
    ("oi_squeeze_long", "TREND_UP"),
    ("oi_squeeze_short", "TREND_DOWN"),
])
def test_squeeze_asks_for_not_yet_trending_rather_than_exactly_RANGE(family, excluded):
    """The premise is "open interest is building before the move", and `== RANGE` was an
    arbitrarily narrow spelling of it — the classifier also emits LOW_VOLATILITY,
    HIGH_VOLATILITY and UNCLEAR, none of which is a confirmed trend either. Measured on
    live frames, the RANGE form fired the full condition on 0.43% of ETHUSDT 1h bars
    (10-15 trades across a 500-day replay), which is below the sample the robustness
    scorer needs: the family was structurally unable to earn ANY verdict, whatever its
    edge. This pins the wording, not a return."""
    _template, conditions = _entry(family)
    regime = next(c for c in conditions if c["feature"] == "market_regime")
    assert (regime["comparison"], regime["value"]) == ("!=", excluded)
    assert not any(c.get("value") == "RANGE" for c in conditions)


@pytest.mark.parametrize("family", ["htf_pullback_long", "htf_pullback_short"])
def test_htf_pullback_drops_the_adx_floor_its_neighbour_already_implies(family):
    """``classify_market_regime`` only returns TREND_UP/TREND_DOWN when adx is already at
    or above its trend threshold, so a separate htf_adx floor overlapped the regime
    condition beside it — little extra selectivity, and it cost a free parameter that the
    robustness score divides the trade count by."""
    from runtime.mvp_runtime.crypto.features import ADX_TREND_THRESHOLD

    template, conditions = _entry(family)
    assert not any(c["feature"] == "htf_adx" for c in conditions)
    assert any(c["feature"] == "htf_market_regime" for c in conditions)
    assert "htf_adx_min" not in template.param_space
    assert ADX_TREND_THRESHOLD > 0  # the implication this removal rests on


@pytest.mark.parametrize("family", [
    "oi_squeeze_long", "oi_squeeze_short", "htf_pullback_long", "htf_pullback_short",
])
def test_loosened_families_still_validate_and_stay_within_the_free_parameter_budget(family):
    """Loosening must not smuggle in degrees of freedom: every literal threshold is one the
    scorer holds against the sample size, so a wider family that added parameters would
    move the bar it was widened to clear."""
    from runtime.mvp_runtime.crypto.robustness import count_free_parameters

    template, conditions = _entry(family)
    spec = _spec_of(template, conditions)
    assert factory.validate_strategy(spec)["approved_for_backtest"] is True
    assert count_free_parameters(spec) <= 6


# --- the funding leg's own gate ------------------------------------------------
#
# Same shape as the OI gate above, against a series with TWO constants behind it. Lives in this
# file because the property under test is the one this file already documents — a feed shorter
# than the replay window is populated on the newest part of it, so `unsuppliable_features`
# cannot see it and the family is retired FRAGILE for a window that had no data in it.

def test_the_funding_gate_reads_the_depth_the_fetch_can_actually_reach():
    """Both constants bind and the SMALLER one wins — the thing a quoted number gets wrong.

    `DEFAULT_FUNDING_RECORDS` is what the fetch asks for and `FUNDING_MAX_PAGES` x
    `FUNDING_ROWS_PER_PAGE` is what the pager can serve. `market_data`'s own comment already
    says both have to move when the window does; this is that sentence made checkable."""
    reach = market_data.funding_history_days()
    assert reach == min(
        market_data.DEFAULT_FUNDING_RECORDS,
        market_data.FUNDING_MAX_PAGES * market_data.FUNDING_ROWS_PER_PAGE,
    ) / market_data.FUNDING_SETTLEMENTS_PER_DAY
    # The cycle asks for the same number the gate judges against — the OI gap in miniature.
    assert cycle._FUNDING_RECORDS is market_data.DEFAULT_FUNDING_RECORDS
    assert factory._funding_feed_reaches("nonsense") is False


def test_the_funding_gate_binds_at_1d_and_only_at_1d():
    """The live defect this closes, pinned as the asymmetry that hid it.

    1h and 4h replay `FACTORY_DEPTH_DAYS` and the funding fetch covers that. 1d replays
    `MIN_FACTORY_BARS` BARS — 2,000 days — which the fetch covers 53% of. Same defect, same
    timeframe, as the one `_oi_feed_reaches` was written for; that fix named only the OI series.
    Closed before it cost a candidate: none exist at 1d because the rotation cursor had not
    reached that block since 1d came back on 2026-08-04."""
    assert market_data.funding_history_days() >= market_data.FACTORY_DEPTH_DAYS
    for timeframe in ("1h", "4h"):
        assert factory._funding_feed_reaches(timeframe) is True
        assert factory.FUNDING_FAMILIES <= {
            t.family for t in factory.templates_for_timeframe(timeframe)
        }, timeframe
    assert factory._funding_feed_reaches("1d") is False
    families_1d = {t.family for t in factory.templates_for_timeframe("1d")}
    assert not (factory.FUNDING_FAMILIES & families_1d)
    assert families_1d, "the non-funding rotation must survive at 1d"


def test_the_funding_gate_closes_when_the_window_outruns_either_constant(monkeypatch):
    """The failure it exists for, reached from BOTH directions independently.

    A window change that lifts the replay past the feed, and a feed change that drops the
    pager under a window that did not move. Either one alone must close the gate — the OI
    version could only be reached one way, and this series has two."""
    # 1. the window moves and the feed does not
    monkeypatch.setattr(market_data, "FACTORY_DEPTH_DAYS", 4_000)
    assert factory._funding_feed_reaches("4h") is False
    assert not (factory.FUNDING_FAMILIES & {t.family for t in factory.templates_for_timeframe("4h")})
    # ...and the rest of the rotation is untouched, so this narrows rather than empties.
    assert {t.family for t in factory.templates_for_timeframe("4h")}
    monkeypatch.undo()

    # 2. the pager shrinks under a window that did not move — the record count is then NOT
    #    what binds, which is the half a single-constant check would miss.
    monkeypatch.setattr(market_data, "FUNDING_MAX_PAGES", 1)
    assert market_data.funding_history_days() < market_data.FACTORY_DEPTH_DAYS
    assert factory._funding_feed_reaches("4h") is False


def test_the_funding_gate_reopens_when_the_feed_catches_up(monkeypatch):
    monkeypatch.setattr(market_data, "FACTORY_DEPTH_DAYS", 4_000)
    assert factory._funding_feed_reaches("1h") is False
    monkeypatch.setattr(market_data, "DEFAULT_FUNDING_RECORDS", 20_000)
    monkeypatch.setattr(market_data, "FUNDING_MAX_PAGES", 40)
    assert factory._funding_feed_reaches("1h") is True
    assert factory.FUNDING_FAMILIES <= {t.family for t in factory.templates_for_timeframe("1h")}

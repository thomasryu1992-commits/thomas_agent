"""The paired OI lookahead measurement (Thomas 2026-10-20): read-only, legacy vs fixed alignment on the
same window, and the reading rule exactly as pre-registered in CRYPTO_ARCHIVE_BACKFILL_V0.1.md."""

from __future__ import annotations

import json

import pytest

from runtime.mvp_runtime.crypto import features, robustness
from runtime.mvp_runtime.crypto.market_data import MockMarketDataCollector, collect_market_data
from runtime.mvp_runtime.crypto.strategy import StrategySpec
from scripts import measure_oi_lookahead as measure

NOW = "2026-10-20T12:00:00Z"


def _spec_dict(timeframe="4h", feature="open_interest_change_pct", scope=("BTCUSDT",)):
    raw = {
        "schema_version": "strategy_spec.v1", "strategy_id": "S-OI", "strategy_version": "1.0",
        "strategy_family": "oi_squeeze_long", "symbol_scope": list(scope), "timeframe": timeframe,
        "direction": "long",
        "entry_rules": {"operator": "AND", "conditions": [{"feature": feature, "comparison": ">", "value": 0.0}]},
        "exit_rules": {"stop_model": "atr", "stop_atr": 1.5, "target_atr": 2.0, "max_holding_bars": 6},
        "risk_constraints": {"max_risk_per_trade_R": 1.0},
    }
    return StrategySpec.from_dict(raw).to_dict()


def _record(cid, **kw):
    return {"candidate_id": cid, "strategy_id": "S-OI", "strategy_spec": _spec_dict(**kw),
            "backtest_evidence": {"holdout": {}}}


def _snapshot(limit=1200):
    """Mock 4h candles plus a daily OI series that flips +6% / -6% day over day."""
    snapshot, _ = collect_market_data("BTCUSDT", "4h", collector=MockMarketDataCollector(), now=NOW, limit=limit)
    days = sorted({c["open_time"][:10] for c in snapshot["candles"]})
    level, events = 1_000_000.0, []
    for i, day in enumerate(days):
        level *= 1.06 if i % 2 else 0.94
        events.append({"timestamp": f"{day}T00:00:00Z", "open_interest": level})
    snapshot["open_interest"] = events
    return snapshot


def test_select_takes_sub_daily_oi_entries_only():
    records = [_record("a"), _record("b", timeframe="1d"), _record("c", feature="rsi"),
               _record("d", timeframe="1h", feature="open_interest_zscore")]
    chosen = measure.select(records, timeframes=frozenset(measure.SUB_DAILY))
    assert [r["candidate_id"] for r in chosen] == ["a", "d"]


def test_the_alignment_switch_is_restored_even_on_error():
    before = features.OI_READING_KNOWN_AFTER_HOURS
    with pytest.raises(RuntimeError):
        with measure.oi_alignment(0):
            assert features.OI_READING_KNOWN_AFTER_HOURS == 0
            raise RuntimeError
    assert features.OI_READING_KNOWN_AFTER_HOURS == before == 24


def test_legacy_and_fixed_alignment_give_different_holdouts_on_the_same_window():
    snapshot = _snapshot()
    spec = StrategySpec.from_dict(_spec_dict())
    legacy = measure.holdout_of(spec, [snapshot], known_after_hours=0)
    fixed = measure.holdout_of(spec, [snapshot], known_after_hours=24)
    assert legacy and fixed and legacy["bars"] == fixed["bars"]          # same window
    assert legacy["closed_count"] > 0 and fixed["closed_count"] > 0
    assert (legacy["period_trades"], legacy["total_R"]) != (fixed["period_trades"], fixed["total_R"])


def test_every_feature_build_of_a_replay_runs_under_the_requested_alignment(monkeypatch):
    """L must be legacy throughout: a rebuild anywhere in the replay at the restored constant would
    make it a mix that the L-vs-F comparison could not see."""
    from runtime.mvp_runtime.crypto import backtest

    seen = []
    real = backtest.build_feature_rows

    def spy(snapshot):
        seen.append(features.OI_READING_KNOWN_AFTER_HOURS)
        return real(snapshot)

    monkeypatch.setattr(backtest, "build_feature_rows", spy)
    measure.holdout_of(StrategySpec.from_dict(_spec_dict()), [_snapshot()], known_after_hours=0)
    assert seen and set(seen) == {0}


def test_the_selection_covers_every_timeframe_the_flag_covers():
    from runtime.mvp_runtime.crypto import market_data

    assert set(measure.SUB_DAILY) == set(market_data.TIMEFRAMES) - {"1d"}


def _block(expectancy, n, periods):
    return {"closed_count": n, "total_R": expectancy * n, "expectancy": expectancy,
            "stdev_r": 0.5, "period_r": periods, "period_trades": [n // len(periods)] * len(periods)}


def test_pooled_periods_are_summed_by_index_not_counted_per_candidate():
    a = _block(0.1, 20, [1.0, -0.5, 1.0, 0.5, 0.0, 0.2, 0.3, 0.1, 0.4, 0.0])
    b = _block(0.2, 20, [0.5, 0.5, -1.0, 1.0, 1.0, 0.0, 0.2, 0.3, 0.1, 0.4])
    pooled = measure.summarize([a, b])
    assert pooled["trades"] == 40 and pooled["mean_net_r"] == pytest.approx(0.15)
    assert pooled["periods_traded"] == 10                      # ten market periods, not twenty
    summed = [x + y for x, y in zip(a["period_r"], b["period_r"])]
    assert pooled["period_mean_r"] == pytest.approx(sum(summed) / 10)


@pytest.mark.parametrize("fixed,legacy_confirmed,kept,expected", [
    ({"mean_net_r": -0.01, "period_lower_95": 0.1, "confirmed": 5}, 8, 5, "VANISHED"),   # mean <= 0
    ({"mean_net_r": 0.2, "period_lower_95": 0.1, "confirmed": 1}, 8, 1, "VANISHED"),     # 0-1 confirmed in F
    ({"mean_net_r": 0.2, "period_lower_95": 0.1, "confirmed": 4}, 8, 4, "KEPT"),         # half of L's stay
    ({"mean_net_r": 0.2, "period_lower_95": 0.1, "confirmed": 3}, 8, 3, "WEAKENED"),     # fewer than half
    ({"mean_net_r": 0.2, "period_lower_95": -0.1, "confirmed": 6}, 8, 6, "WEAKENED"),    # bound not clear
])
def test_the_reading_is_the_pre_registered_rule(fixed, legacy_confirmed, kept, expected):
    assert measure.read({"confirmed": legacy_confirmed}, fixed, kept=kept) == expected


def test_a_run_reports_both_alignments_and_writes_nothing(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(measure, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(measure.pool_state, "read_candidates", lambda root: [_record("cand_oi")])
    monkeypatch.setattr(measure, "_build_snapshot", lambda symbol, timeframe, now, root: _snapshot())
    assert measure.main(["--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert set(report["pooled"]) == {"L", "F"} and report["known_after_hours"] == {"L": 0, "F": 24}
    assert report["reading"] in {"KEPT", "VANISHED", "WEAKENED"}
    assert "confirmed" in report["pooled"]["F"]
    row = report["candidates"][0]
    assert row["candidate_id"] == "cand_oi" and {row["L_status"], row["F_status"]} <= {
        robustness.HOLDOUT_CONFIRMED, robustness.HOLDOUT_UNDERPOWERED, robustness.HOLDOUT_CONTRADICTED,
        robustness.HOLDOUT_INSUFFICIENT}
    assert list(tmp_path.iterdir()) == []                      # read-only: nothing under the root
    assert features.OI_READING_KNOWN_AFTER_HOURS == 24


def test_list_collects_no_market_data(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(measure, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(measure.pool_state, "read_candidates", lambda root: [_record("cand_oi")])
    monkeypatch.setattr(measure, "_build_snapshot", lambda *a, **k: pytest.fail("--list collected data"))
    assert measure.main(["--list"]) == 0
    assert "1 sub-daily OI candidate(s)" in capsys.readouterr().out


def test_a_context_builds_its_frames_once_per_alignment_not_once_per_candidate(monkeypatch, tmp_path, capsys):
    """A 15m frame is 96,000 bars and this runs inside the live scheduler container: three candidates
    in one context must cost two frame builds (L and F), not six."""
    builds = []
    real = measure.build_replay_frame

    def counting(snapshot):
        builds.append(features.OI_READING_KNOWN_AFTER_HOURS)
        return real(snapshot)

    monkeypatch.setattr(measure, "build_replay_frame", counting)
    monkeypatch.setattr(measure, "_repo_root", lambda: tmp_path)
    records = [_record("cand_a"), _record("cand_b", feature="open_interest_zscore"),
               {**_record("cand_c"), "strategy_spec": {**_spec_dict(), "strategy_id": "S-OI-3"}}]
    monkeypatch.setattr(measure.pool_state, "read_candidates", lambda root: records)
    monkeypatch.setattr(measure, "_build_snapshot", lambda symbol, timeframe, now, root: _snapshot())
    assert measure.main(["--json"]) == 0
    assert sorted(builds) == [0, 24]
    assert len(json.loads(capsys.readouterr().out)["candidates"]) == 3


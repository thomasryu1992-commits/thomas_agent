"""The pre-window replay (PRE_WINDOW_REPLAY_V0.1, Thomas 2026-10-23): the fixed population, the window cut,
the twin's construction and the pre-registered reading. Nothing here touches the network or a real lake."""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone

import pytest

from runtime.mvp_runtime.crypto import market_data, null_control
from runtime.mvp_runtime.crypto.strategy import StrategySpec
from scripts.research import lake
from scripts.research import pre_window_replay as pwr

H_MS = 3_600_000


def _spec(feature="rsi", value=55.0, family="trend_pullback", scope=("BTCUSDT",), timeframe="1h"):
    raw = {
        "schema_version": "strategy_spec.v1", "strategy_id": "S-PW", "strategy_version": "1.0",
        "strategy_family": family, "symbol_scope": list(scope), "timeframe": timeframe, "direction": "long",
        "entry_rules": {"operator": "AND", "conditions": [{"feature": feature, "comparison": ">", "value": value}]},
        "exit_rules": {"stop_model": "atr", "stop_atr": 1.5, "target_atr": 2.0, "max_holding_bars": 12},
        "risk_constraints": {"max_risk_per_trade_R": 1.0},
    }
    return StrategySpec.from_dict(raw).to_dict()


def _entry(cid, **kw):
    spec = _spec(**kw)
    null = null_control._null_spec(StrategySpec.from_dict(spec), 0.05).to_dict()
    member = {"candidate_id": cid, "timeframe": spec["timeframe"], "strategy_family": spec["strategy_family"],
              "direction": "long", "symbol_scope": spec["symbol_scope"]}
    return {"member": member, "spec": spec, "cohort_id": "c1",
            "twin": {"null_id": f"null_{cid}", "null_spec": null, "seed": f"c1|{cid}", "parent_candidate_id": cid}}


def _kline(open_ms, close):
    return [open_ms, str(close), str(close * 1.01), str(close * 0.99), str(close), "10", open_ms + H_MS - 1,
            "100", 5, "4", "40", "0"]


@pytest.fixture
def cache(tmp_path, monkeypatch):
    """A lake cache with BTCUSDT 1h candles from 2020-01-01 to 2020-03-05, and the lake selectors installed."""
    start = int(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    rows = [_kline(start + i * H_MS, 100.0 + 10 * math.sin(i / 17.0) + 0.01 * i) for i in range(64 * 24)]
    path = lake.klines_path(tmp_path, "BTCUSDT", "1h")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    for name in ("select_market_data_collector", "select_liquidation_feed"):
        monkeypatch.setattr(market_data, name, getattr(market_data, name))
    monkeypatch.setattr(market_data.MockMarketDataCollector, "collect", market_data.MockMarketDataCollector.collect)
    lake.install(cache=tmp_path, lake=None)
    monkeypatch.setattr(pwr, "WINDOWS", {"1h": (("2020-02", "2020-02-01T00:00:00Z", "2020-03-01T00:00:00Z"),)})
    return tmp_path


def test_the_population_is_fixed_by_feeds_and_twins(monkeypatch, tmp_path):
    members = [
        {"candidate_id": "c_ok", "timeframe": "1h", "strategy_family": "trend_pullback"},
        {"candidate_id": "c_oi", "timeframe": "4h", "strategy_family": "oi_squeeze_long"},
        {"candidate_id": "c_notwin", "timeframe": "1h", "strategy_family": "breakout"},
        {"candidate_id": "c_15m", "timeframe": "15m", "strategy_family": "breakout"},
    ]
    specs = {"c_ok": _spec(), "c_oi": _spec(feature="open_interest_change_pct", value=0.0, timeframe="4h"),
             "c_notwin": _spec(), "c_15m": _spec(timeframe="15m")}
    monkeypatch.setattr(pwr.forward_cohort, "read_cohorts", lambda root: [{"cohort_id": "c1", "members": members}])
    monkeypatch.setattr(pwr, "read_candidates",
                        lambda root: [{"candidate_id": cid, "strategy_spec": s} for cid, s in specs.items()])
    monkeypatch.setattr(pwr.forward_cohort_null, "active_null_records", lambda root: [{"members": [
        {"parent_candidate_id": cid, "null_id": f"null_{cid}", "null_spec": {}, "seed": "s"}
        for cid in ("c_ok", "c_oi", "c_15m")]}])
    assert [e["member"]["candidate_id"] for e in pwr.population(tmp_path)] == ["c_ok"]


def test_a_replay_counts_only_trades_inside_its_window_and_the_twin_is_deterministic(cache, tmp_path):
    entry = _entry("c_rsi")
    rows = pwr.replay(tmp_path, [entry])
    assert [r["status"] for r in rows] == ["replayed"]
    member, twin = rows[0]["member"], rows[0]["twin"]
    assert member and twin, "the fixture must produce trades on both sides, or the window check is vacuous"
    assert all("2020-02-01" <= day < "2020-03-01" for day, _ in member + twin)
    assert pwr.replay(tmp_path, [entry])[0]["twin"] == twin               # seeded coin, same draws
    other = {**entry, "twin": {**entry["twin"], "seed": "another"}}
    assert pwr.replay(tmp_path, [other])[0]["twin"] != twin               # and the seed is what decides them


def test_a_window_without_enough_warmup_is_not_replayed(cache, tmp_path, monkeypatch):
    monkeypatch.setattr(pwr, "WINDOWS", {"1h": (("2020-01", "2020-01-02T00:00:00Z", "2020-01-20T00:00:00Z"),)})
    assert [r["status"] for r in pwr.replay(tmp_path, [_entry("c_early")])] == ["no_window"]


@pytest.mark.parametrize("family,positive,expected", [
    ({"bh_pass": True, "mean_diff_r": 0.2}, 3, "LEAD"),
    ({"bh_pass": True, "mean_diff_r": 0.2}, 2, "NULL"),        # not consistent across enough windows
    ({"bh_pass": True, "mean_diff_r": -0.2}, 0, "ANTI"),
    ({"bh_pass": False, "mean_diff_r": 0.5}, 4, "NULL"),
    ({"bh_pass": None, "mean_diff_r": 0.5}, 4, "NULL"),        # shown, not tested
])
def test_the_reading_is_the_pre_registered_rule(family, positive, expected):
    assert pwr.reading(family, positive) == expected


def test_read_runs_the_decided_stage_and_names_the_overall_result():
    entries = [_entry(f"c{i}") for i in range(4)]
    days = [(datetime(2021, 1, 1) + timedelta(days=d)).strftime("%Y-%m-%d") for d in range(40)]
    rows = [{"candidate_id": e["member"]["candidate_id"], "window": "2021", "status": "replayed",
             "member": [(d, 0.1) for d in days], "twin": [(d, 0.1) for d in days]} for e in entries]
    report = pwr.read(entries, rows, draws=20)
    block = report["1h"]
    assert block["overall"] == "NO_TIMING_EDGE_BEFORE_THE_WINDOW"
    assert all(f["reading"] == "NULL" for f in block["families"])
    assert block["calibration"]["draws"] == 20


def test_outside_the_sandbox_it_refuses(capsys):
    assert pwr.main(["--list"]) != 0
    assert "research sandbox only" in capsys.readouterr().err

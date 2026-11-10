"""The positioning divergence measurement (POSITIONING_DIVERGENCE_V0.1, Thomas 2026-11-10): the archive mapping,
when a reading is known, the stale rule, the fixed specs, and the entry rule. Synthetic data only."""

from __future__ import annotations

import io
import json
import math
import zipfile
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from runtime.mvp_runtime.crypto import feed_assembly, features, market_data, template_space
from runtime.mvp_runtime.crypto.backtest import build_replay_frame
from scripts.research import lake
from scripts.research import positioning_divergence as pd
from scripts.research import pre_window_replay as pwr

H_MS = 3_600_000
HEADER = ("create_time,symbol,sum_open_interest,sum_open_interest_value,count_toptrader_long_short_ratio,"
          "sum_toptrader_long_short_ratio,count_long_short_ratio,sum_taker_long_short_vol_ratio")


def _write_day(metrics, symbol, day, *, top_position=lambda t: 1.0, global_account=lambda t: 1.0, skip=()):
    lines = [HEADER]
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    for k in range(288):
        t = start + timedelta(minutes=5 * k)
        if t in skip:
            continue
        lines.append(f"{t:%Y-%m-%d %H:%M:%S},{symbol},1,1,1.5,{top_position(t)},{global_account(t)},1")
    path = pd.metrics_path(metrics, symbol, day)
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(f"{symbol}-metrics-{day}.csv", "\n".join(lines) + "\n")
    path.write_bytes(buf.getvalue())


def test_the_reading_labelled_t_is_the_archive_row_five_minutes_before_it(tmp_path):
    day = date(2022, 1, 2)
    marker = datetime(2022, 1, 2, 9, 55, tzinfo=timezone.utc)
    _write_day(tmp_path, "BTCUSDT", day, top_position=lambda t: 3.0 if t == marker else 1.0)
    rows = pd.readings(tmp_path, "BTCUSDT", start=day, end=day + timedelta(days=1))
    top = {r["timestamp"]: r for r in rows if r["series"] == "top_position"}
    assert top["2022-01-02T10:00:00Z"]["long_ratio"] == pytest.approx(0.75)      # r / (1 + r), from the 09:55 row
    assert top["2022-01-02T10:00:00Z"]["short_ratio"] == pytest.approx(0.25)
    assert top["2022-01-02T09:00:00Z"]["long_ratio"] == pytest.approx(0.5)
    assert all(r["timestamp"].endswith(":00:00Z") and r["period"] == "1h" for r in rows)
    assert len(top) == 23                         # 01:00..23:00; 00:00 needs the previous day's 23:55 row
    assert {r["series"] for r in rows} == set(pd.SERIES_COLUMNS)
    assert {round(r["long_ratio"], 9) for r in rows if r["series"] == "top_account"} == {0.6}


def test_a_missing_row_or_an_unusable_ratio_makes_no_reading(tmp_path):
    day = date(2022, 1, 2)
    gone = datetime(2022, 1, 2, 4, 55, tzinfo=timezone.utc)
    bad = datetime(2022, 1, 2, 6, 55, tzinfo=timezone.utc)
    _write_day(tmp_path, "BTCUSDT", day, skip={gone}, global_account=lambda t: "" if t == bad else 1.0)
    rows = pd.readings(tmp_path, "BTCUSDT", start=day, end=day + timedelta(days=1))
    paired = pd.paired_labels(rows)
    assert "2022-01-02T05:00:00Z" not in paired and "2022-01-02T07:00:00Z" not in paired
    assert "2022-01-02T06:00:00Z" in paired and len(paired) == 21
    assert pd.readings(tmp_path, "ETHUSDT", start=day, end=day + timedelta(days=1)) == []   # no file, no rows
    _write_day(tmp_path, "SOLUSDT", day, top_position=lambda t: 0.0, global_account=lambda t: -1.0)
    left = pd.readings(tmp_path, "SOLUSDT", start=day, end=day + timedelta(days=1))
    assert {r["series"] for r in left} == {"top_account"}              # a ratio at or below zero is not a reading


def test_the_specs_are_the_librarys_two_families_at_their_base_parameters():
    made = pd.specs("ETHUSDT")
    assert [s.strategy_family for s in made] == list(pd.FAMILIES)
    assert set(pd.FAMILIES) == set(template_space.POSITIONING_FAMILIES)
    by_family = {t.family: t for t in template_space.TEMPLATES}
    for spec in made:
        base = by_family[spec.strategy_family].base_params
        raw = spec.to_dict()
        assert raw["symbol_scope"] == ["ETHUSDT"] and raw["timeframe"] == "1h"
        z = [c for c in raw["entry_rules"]["conditions"] if c["feature"] == "positioning_divergence_zscore"]
        assert len(z) == 1 and abs(z[0]["value"]) == base["divergence_z_min"]
        assert raw["exit_rules"]["stop_atr"] == base["stop_atr"]
        assert raw["exit_rules"]["max_holding_bars"] == base["max_holding_bars"]


def test_a_bar_whose_newest_reading_is_too_old_carries_none():
    t0 = datetime(2022, 1, 2, tzinfo=timezone.utc)
    iso = lambda k: (t0 + timedelta(hours=k)).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
    col = features.POSITIONING_COLUMNS[0]
    frame = SimpleNamespace(candles=[{"open_time": iso(k)} for k in range(10)],
                            rows=[{col: 1.0, "rsi": 50.0} for _ in range(10)])
    cleared = pd.blank_stale(frame, [iso(0), iso(1)])       # readings labelled 00:00 and 01:00, then nothing
    limit = int(feed_assembly.POSITIONING_MAX_AGE_HOURS)
    assert limit == 3
    kept = [k for k in range(10) if frame.rows[k][col] is not None]
    assert kept == [1, 2, 3, 4]              # known from label + 1h; the 01:00 reading is 3h old at the 04:00 bar
    assert cleared == 6 and all(row["rsi"] == 50.0 for row in frame.rows)   # bar 0 (nothing known yet) and 5..9


def _kline(open_ms, close):
    return [open_ms, str(close), str(close * 1.01), str(close * 0.99), str(close), "10", open_ms + H_MS - 1,
            "100", 5, "4", "40", "0"]


@pytest.fixture
def cache(tmp_path, monkeypatch):
    """BTCUSDT 1h candles from 2020-01-01 to 2020-03-05 in a lake cache, the lake selectors installed, and the
    measurement cut to February 2020."""
    start = int(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    rows = [_kline(start + i * H_MS, 100.0 + 10 * math.sin(i / 17.0) + 0.01 * i) for i in range(64 * 24)]
    path = lake.klines_path(tmp_path, "BTCUSDT", "1h")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    for name in ("select_market_data_collector", "select_liquidation_feed"):
        monkeypatch.setattr(market_data, name, getattr(market_data, name))
    monkeypatch.setattr(market_data.MockMarketDataCollector, "collect", market_data.MockMarketDataCollector.collect)
    lake.install(cache=tmp_path, lake=None)
    monkeypatch.setattr(pd, "WINDOWS", (("2020-02", "2020-02-01T00:00:00Z", "2020-03-01T00:00:00Z"),))
    return tmp_path


def _swinging(t):
    """The top cohort's ratio: flat, with a long lean for six hours of every 36."""
    return 2.0 if (int(t.timestamp()) // 3600) % 36 < 6 else 1.0


def _fill(metrics, first, last, **kw):
    day = first
    while day <= last:
        _write_day(metrics, "BTCUSDT", day, **kw)
        day += timedelta(days=1)


def test_it_trades_on_the_archives_readings_and_only_inside_the_window(cache, tmp_path):
    metrics = tmp_path / "archive-metrics"
    _fill(metrics, date(2020, 1, 5), date(2020, 3, 1), top_position=_swinging)
    trades, windows = pd.simulate(tmp_path, metrics, symbols=("BTCUSDT",))
    assert [w["status"] for w in windows] == ["replayed"]
    assert trades, "the fixture must produce trades, or the checks below are vacuous"
    assert all("2020-02-01" <= t["day"] < "2020-03-01" and t["symbol"] == "BTCUSDT" for t in trades)
    assert {t["family"] for t in trades} <= set(pd.FAMILIES)
    assert sum(windows[0]["trades"].values()) == len(trades)
    flat = tmp_path / "flat-metrics"
    _fill(flat, date(2020, 1, 5), date(2020, 3, 1))                     # no divergence at all: nothing to enter on
    assert pd.simulate(tmp_path, flat, symbols=("BTCUSDT",))[0] == []


def test_without_readings_the_window_is_not_replayed(cache, tmp_path):
    trades, windows = pd.simulate(tmp_path, tmp_path / "nothing", symbols=("BTCUSDT",))
    assert trades == [] and [w["status"] for w in windows] == ["unsuppliable"]


def test_a_reading_reaches_a_bar_one_hour_after_its_label(cache, tmp_path):
    metrics = tmp_path / "archive-metrics"
    spike = datetime(2020, 2, 10, 11, 55, tzinfo=timezone.utc)          # the row behind the reading labelled 12:00
    _fill(metrics, date(2020, 1, 5), date(2020, 3, 1), top_position=lambda t: 4.0 if t == spike else 1.0)
    snapshot = pwr._snapshot("BTCUSDT", "1h", end="2020-03-01T00:00:00Z", bars=1500, root=tmp_path)
    snapshot["positioning"] = pd.readings(metrics, "BTCUSDT", start=date(2020, 1, 5), end=date(2020, 3, 1))
    frame = build_replay_frame(snapshot)
    carrying = [str(c["open_time"]) for c, row in zip(frame.candles, frame.rows)
                if row.get("positioning_top_long") == pytest.approx(0.8)]
    assert carrying == ["2020-02-10T13:00:00Z"]                          # known from 12:00 + 1h, for one bar


def test_the_entry_rule_counts_idle_months_as_zero(monkeypatch):
    monkeypatch.setattr(pd, "WINDOWS", (("w", "2022-01-01T00:00:00Z", "2022-07-01T00:00:00Z"),))
    assert pd.span_months() == ["2022-01", "2022-02", "2022-03", "2022-04", "2022-05", "2022-06"]
    trades = [{"symbol": "BTCUSDT", "family": pd.FAMILIES[0], "day": "2022-01-10", "r": 2.0},
              {"symbol": "ETHUSDT", "family": pd.FAMILIES[1], "day": "2022-01-20", "r": 1.0},
              {"symbol": "BTCUSDT", "family": pd.FAMILIES[0], "day": "2022-04-03", "r": -1.0},
              {"symbol": "BTCUSDT", "family": pd.FAMILIES[0], "day": "2023-01-01", "r": 50.0}]   # outside the span
    out = pd.measure(trades)
    monthly = [3.0, 0.0, 0.0, -1.0, 0.0, 0.0]
    mean = sum(monthly) / 6
    sd = math.sqrt(sum((v - mean) ** 2 for v in monthly) / 5)
    assert out["months"] == 6 and out["months_with_trades"] == 2 and out["negative_months"] == 1
    assert out["trades"] == 3 and out["sum_r"] == 2.0 and out["by_symbol"]["BTCUSDT"]["trades"] == 2
    assert out["annual_sharpe"] == pytest.approx(round(mean / sd * math.sqrt(12), 3))
    assert out["years"] == {"2022": 2.0} and out["worst_month"] == {"month": "2022-04", "sum_r": -1.0}
    assert out["by_family"][pd.FAMILIES[1]]["trades"] == 1


@pytest.mark.parametrize("monthly,passes", [([1.0, 1.2, 0.8, 1.1, 0.9, 1.0], True), ([3.0, -3.0, 3.0, -3.0, 3.0, -2.5], False),
                                            ([3.0, -2.0, 3.0, -2.0, 3.0, -2.0], False)])   # annual SR 0.63: under 1.0
def test_the_floor_is_an_annual_sharpe_of_one(monkeypatch, monthly, passes):
    monkeypatch.setattr(pd, "WINDOWS", (("w", "2022-01-01T00:00:00Z", "2022-07-01T00:00:00Z"),))
    trades = [{"symbol": "BTCUSDT", "family": pd.FAMILIES[0], "day": f"2022-{m:02d}-05", "r": r}
              for m, r in enumerate(monthly, start=1)]
    out = pd.measure(trades)
    assert pd.ENTRY_SHARPE == 1.0
    assert out["entry_pass"] is passes and (out["annual_sharpe"] >= 1.0) is passes
    assert pd.measure([])["entry_pass"] is False


def test_coverage_prints_dates_and_counts_and_names_a_short_symbol(tmp_path, monkeypatch):
    monkeypatch.setattr(pd, "WINDOWS", (("w", "2022-02-01T00:00:00Z", "2022-02-11T00:00:00Z"),))
    monkeypatch.setattr(pd, "WARMUP_DAYS", 2)
    day = date(2022, 1, 29)
    while day <= date(2022, 2, 10):
        _write_day(tmp_path, "BTCUSDT", day, top_position=lambda t: 1.2345)
        if day != date(2022, 2, 5):
            _write_day(tmp_path, "ETHUSDT", day)
        day += timedelta(days=1)
    cover = pd.coverage(tmp_path, symbols=("BTCUSDT", "ETHUSDT"))
    btc, eth = cover["symbols"]["BTCUSDT"], cover["symbols"]["ETHUSDT"]
    assert btc["expected_hours"] == 12 * 24 and btc["paired_hours"] == 12 * 24 and btc["missing_share"] == 0
    assert eth["missing_days"] == 1 and eth["missing_days_first"] == ["2022-02-05"]
    assert eth["paired_hours"] == 12 * 24 - 24 and eth["missing_share"] == pytest.approx(24 / 288, abs=1e-4)
    assert pd.short_symbols(cover) == ["ETHUSDT"]                        # 8.3% missing is over the 5% line
    blob = json.dumps(cover)
    assert "long_ratio" not in blob and "0.5524" not in blob and "1.2345" not in blob
    again = pd.coverage(tmp_path, symbols=("BTCUSDT", "ETHUSDT"))["input_digest"]
    _write_day(tmp_path, "BTCUSDT", date(2022, 2, 3), top_position=lambda t: 1.3)
    assert again == cover["input_digest"] != pd.coverage(tmp_path, symbols=("BTCUSDT", "ETHUSDT"))["input_digest"]


def test_it_refuses_outside_the_sandbox(capsys):
    assert pd.main(["load"]) == pd.EXIT_USAGE
    assert "sandbox" in capsys.readouterr().err

"""The metrics basket search (METRICS_BASKET_SEARCH_V0.1, Thomas 2026-11-10): the daily values, the features, the
development cut and the locked span. Synthetic data only."""

from __future__ import annotations

import io
import json
import math
import zipfile
from datetime import date, datetime, timedelta, timezone

import pytest

from scripts.research import basket_search as bs
from scripts.research import cross_section_basket as xsb
from scripts.research import metrics_basket_search as ms

HEADER = ("create_time,symbol,sum_open_interest,sum_open_interest_value,count_toptrader_long_short_ratio,"
          "sum_toptrader_long_short_ratio,count_long_short_ratio,sum_taker_long_short_vol_ratio")
FIRST = date(2022, 9, 1)
SPAN = (date(2023, 2, 1), date(2023, 12, 31))
N = 40


def _rows(day, *, oi=100.0, top=1.0, ls=1.0, taker=1.0, count=288, start=0, value=999.0):
    base = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    return [{"create_time": f"{base + timedelta(minutes=5 * k):%Y-%m-%d %H:%M:%S}", "symbol": "X",
             "sum_open_interest": str(oi), "sum_open_interest_value": str(value),
             "count_toptrader_long_short_ratio": "1.5", "sum_toptrader_long_short_ratio": str(top),
             "count_long_short_ratio": str(ls), "sum_taker_long_short_vol_ratio": str(taker)}
            for k in range(start, start + count)]


def test_a_days_value_is_the_mean_of_its_own_rows_and_a_ratio_becomes_a_share():
    day = date(2023, 5, 7)
    got = ms.daily_values(_rows(day, oi=250.0, top=3.0, ls=1.0, taker=0.25), day)
    assert got == {"oi": 250.0, "ls": pytest.approx(0.5), "top": pytest.approx(0.75), "taker": pytest.approx(0.2)}
    spill = _rows(day, oi=100.0) + _rows(day + timedelta(days=1), oi=9e9)       # the next day's rows are not read
    assert ms.daily_values(spill, day)["oi"] == 100.0
    assert ms.MIN_ROWS == 144
    assert ms.daily_values(_rows(day, count=143), day) == {"oi": None, "ls": None, "top": None, "taker": None}
    partial = _rows(day, count=144) + [{**r, "sum_toptrader_long_short_ratio": ""} for r in _rows(day, count=144, start=144)]
    assert ms.daily_values(partial, day)["top"] == pytest.approx(0.5) and ms.daily_values(partial, day)["oi"] == 100.0
    assert ms.daily_values([{**r, "count_long_short_ratio": "0"} for r in _rows(day)], day)["ls"] is None


def test_open_interest_is_read_in_contracts_never_in_quote_value():
    day = date(2023, 5, 7)
    assert ms.COLUMNS["oi"][0] == "sum_open_interest"
    assert ms.daily_values(_rows(day, value=1.0), day) == ms.daily_values(_rows(day, value=5e9), day)


def _market(*, last: date = SPAN[1], crowded_lose: bool = True):
    """``N`` coins, volume falling by index. Coin ``i``'s all-account long share rises with ``i``; with
    ``crowded_lose`` the crowded coins drift down and the uncrowded up."""
    market, daily = xsb.Market(), {}
    days = [FIRST + timedelta(days=k) for k in range((last - FIRST).days + 1)]
    for i in range(N):
        name = bs.REFERENCE if i == 0 else f"S{i:02d}USDT"
        drift = (19.5 - i) * 0.0003 * (1 if crowded_lose else -1)
        level, bars, rows = 100.0, {}, {}
        for k, day in enumerate(days):
            close = level * math.exp(drift + (0.004 if (k + i) % 2 else -0.004))
            bars[day] = xsb.Bar(open=level, close=close, quote_volume=1000.0 - i, high=max(level, close),
                                low=min(level, close))
            level = close
            rows[day] = {"oi": 1000.0 * (1 + 0.001 * i) ** k, "ls": 0.30 + 0.01 * i, "top": 0.50,
                         "taker": 0.5 + 0.002 * (i % 7)}
        market.bars[name], daily[name] = bars, rows
        market.funding[name] = []
    return market, daily


CROWD_FADE = bs.Candidate(why="test", features=(("ls_level", 1.0),), long_low=True)


def test_the_features_read_the_metrics_and_nothing_about_price():
    market, daily = _market()
    held = ms.cut(market, daily, SPAN[1])
    day, s = date(2023, 6, 25), "S07USDT"
    assert ms.FEATURES["ls_level"](held, s, day, 30) == pytest.approx(0.37)
    assert ms.FEATURES["top_level"](held, s, day, 30) == pytest.approx(0.50)
    assert ms.FEATURES["divergence"](held, s, day, 30) == pytest.approx(0.13)
    assert ms.FEATURES["oi_growth"](held, s, day, 30) == pytest.approx(1.007 ** 30 - 1)
    assert set(ms.FEATURES) == {"oi_growth", "ls_level", "top_level", "divergence", "taker_level"}
    before = {name: fn(held, s, day, 30) for name, fn in ms.FEATURES.items()}
    held.bars[s] = {d: xsb.Bar(b.open * 7, b.close / 3, 1.0, b.high, b.low) for d, b in held.bars[s].items()}
    assert before == {name: fn(held, s, day, 30) for name, fn in ms.FEATURES.items()}
    for k in range(12):                                            # too few days left: no value
        held.metrics[s].pop(day - timedelta(days=2 * k + 1), None)
    assert ms.FEATURES["ls_level"](held, s, day, 30) is None


def test_exploration_cannot_see_past_its_span():
    clean, daily = _market()
    spoiled, late = _market(last=date(2024, 6, 30))
    for s in late:
        for d in late[s]:
            if d > SPAN[1]:
                late[s][d] = {"oi": 1e-9, "ls": 0.999, "top": 0.001, "taker": 0.999}
    a = bs.measure(ms.cut(clean, daily, SPAN[1]), CROWD_FADE, SPAN, ms.FEATURES)
    b = bs.measure(ms.cut(spoiled, late, SPAN[1]), CROWD_FADE, SPAN, ms.FEATURES)
    assert a == b and a["weeks"] > 40
    assert max(max(d) for d in ms.cut(spoiled, late, SPAN[1]).metrics.values()) == SPAN[1]


def test_fading_the_crowd_passes_when_the_crowd_loses_and_fails_when_it_wins():
    market, daily = _market()
    good = bs.measure(ms.cut(market, daily, SPAN[1]), CROWD_FADE, SPAN, ms.FEATURES)
    market, daily = _market(crowded_lose=False)
    bad = bs.measure(ms.cut(market, daily, SPAN[1]), CROWD_FADE, SPAN, ms.FEATURES)
    assert good["pass"] is True and good["months"] == 11 and bad["pass"] is False and bad["annual_sharpe"] < 0


def test_a_candidate_reading_a_thin_2022_column_is_developed_on_the_short_span():
    assert ms.DEV_LONG == (date(2022, 1, 1), date(2024, 12, 31))
    assert ms.DEV_SHORT == (date(2023, 2, 1), date(2024, 12, 31))
    assert ms.CONFIRM == bs.CONFIRM == (date(2025, 1, 1), date(2026, 9, 30)) and ms.DEV_LONG[1] < ms.CONFIRM[0]
    one = lambda *names: bs.Candidate(why="t", features=tuple((n, 1.0) for n in names))  # noqa: E731
    assert ms.dev_span(one("oi_growth")) == ms.DEV_LONG and ms.dev_span(one("oi_growth", "ls_level")) == ms.DEV_LONG
    for name in ("top_level", "divergence", "taker_level"):
        assert ms.dev_span(one(name)) == ms.DEV_SHORT and ms.dev_span(one("oi_growth", name)) == ms.DEV_SHORT


def test_the_registry_stays_inside_the_budget_and_the_feature_list():
    assert ms.BUDGET == 5 and len(ms.CANDIDATES) <= ms.BUDGET
    assert ms.FINAL is None or ms.FINAL in ms.CANDIDATES
    for candidate in ms.CANDIDATES.values():
        assert candidate.features and {name for name, _ in candidate.features} <= set(ms.FEATURES)


def test_the_fetch_plan_covers_the_lookback_before_every_member_month():
    monthly = {date(2024, 2, 1): ["NEW", "OLD"], date(2024, 3, 1): ["OLD"], date(2024, 6, 1): ["NEW"],
               date(2026, 9, 1): ["LATE"]}
    plan = ms.fetch_plan(monthly, end=date(2026, 9, 20))
    # a coin new in February needs January's days: oi_growth on Feb 1 reads Jan 2 (30 days back)
    assert ("NEW", date(2024, 1, 2), date(2024, 2, 29)) in plan
    assert ("NEW", date(2024, 5, 2), date(2024, 6, 30)) in plan          # a gap makes a second span
    assert ("OLD", date(2024, 1, 2), date(2024, 3, 31)) in plan          # adjoining months merge
    assert ("LATE", date(2026, 8, 2), date(2026, 9, 20)) in plan         # nothing past the end
    assert len(plan) == 4 and ms.PLAN_LOOKBACK == 30


def test_build_writes_daily_values_and_load_reads_them_back(tmp_path):
    day = date(2023, 5, 7)
    folder = tmp_path / "archive-metrics" / "metrics" / "ABCUSDT"
    folder.mkdir(parents=True)
    for d, top in ((day, 3.0), (day + timedelta(days=1), 1.0)):
        text = HEADER + "\n" + "\n".join(",".join(r[c] for c in HEADER.split(",")) for r in _rows(d, top=top)) + "\n"
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("x.csv", text)
        (folder / f"ABCUSDT-metrics-{d}.zip").write_bytes(buf.getvalue())
    assert ms.build(tmp_path, say=lambda _: None) == 2
    daily = ms.load_daily(tmp_path)
    assert daily["ABCUSDT"][day]["top"] == pytest.approx(0.75) and daily["ABCUSDT"][day + timedelta(days=1)]["top"] == 0.5
    assert json.loads(ms.derived_path(tmp_path, "ABCUSDT").read_text().splitlines()[0])[0] == "2023-05-07"


def _run(monkeypatch, argv, **patch):
    monkeypatch.setattr(ms.xsb, "_in_sandbox", lambda: True)
    for name, value in patch.items():
        monkeypatch.setattr(ms, name, value)
    return ms.main(argv)


def test_the_locked_span_opens_only_for_the_final_candidate(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(bs, "CONFIRM_SPENT", None)                 # the rule as it stood while the span was unread
    registry = {"M01": CROWD_FADE, "M02": bs.Candidate(why="t", features=(("oi_growth", 1.0),))}
    cache = ["--cache", str(tmp_path)]
    assert _run(monkeypatch, ["confirm", "--candidate", "M01", *cache], CANDIDATES=registry, FINAL=None) == ms.EXIT_USAGE
    assert _run(monkeypatch, ["confirm", "--candidate", "M01", *cache], CANDIDATES=registry, FINAL="M02") == ms.EXIT_USAGE
    assert "read once" in capsys.readouterr().err
    assert _run(monkeypatch, ["explore", "--candidate", "M09", *cache], CANDIDATES=registry) == ms.EXIT_USAGE
    over = {f"M{k:02d}": CROWD_FADE for k in range(1, 7)}
    assert _run(monkeypatch, ["explore", "--candidate", "M01", *cache], CANDIDATES=over) == ms.EXIT_USAGE
    price = {"M01": bs.Candidate(why="t", features=(("vol", 1.0),))}                 # a price feature is not this list
    assert _run(monkeypatch, ["explore", "--candidate", "M01", *cache], CANDIDATES=price) == ms.EXIT_USAGE
    assert _run(monkeypatch, ["confirm", "--candidate", "M02", *cache], CANDIDATES=registry, FINAL="M02") == ms.EXIT_BLOCKED


def test_the_metrics_search_spans_end_before_the_reserved_days(monkeypatch, capsys, tmp_path):
    for span in (ms.DEV_LONG, ms.DEV_SHORT, ms.CONFIRM):
        bs.check_search_span(span)
        assert span[1] < bs.RESERVED_FROM
    argv = ["explore", "--candidate", "M04", "--cache", str(tmp_path)]
    assert _run(monkeypatch, argv) == ms.EXIT_BLOCKED
    capsys.readouterr()
    assert _run(monkeypatch, argv, DEV_LONG=(date(2026, 1, 1), date(2026, 12, 31))) == ms.EXIT_USAGE
    assert "reserved" in capsys.readouterr().err


def test_the_spent_locked_span_refuses_the_final_candidate_as_shipped(monkeypatch, capsys, tmp_path):
    # M04 read the span once on 2026-11-11. With the shipped registry and FINAL, a second confirm is refused.
    assert ms.FINAL in ms.CANDIDATES
    assert _run(monkeypatch, ["confirm", "--candidate", ms.FINAL, "--cache", str(tmp_path)]) == ms.EXIT_USAGE
    assert "spent" in capsys.readouterr().err
    assert _run(monkeypatch, ["explore", "--candidate", ms.FINAL, "--cache", str(tmp_path)]) == ms.EXIT_BLOCKED


def test_it_refuses_outside_the_sandbox_and_lists_anywhere(monkeypatch, capsys):
    monkeypatch.setattr(ms.xsb, "_in_sandbox", lambda: False)
    monkeypatch.setattr(ms, "CANDIDATES", {"M01": CROWD_FADE})
    assert ms.main(["explore", "--candidate", "M01"]) == ms.EXIT_USAGE
    assert "sandbox" in capsys.readouterr().err
    assert ms.main(["list"]) == ms.EXIT_OK
    listed = json.loads(capsys.readouterr().out)
    assert listed["budget"] == 5 and listed["candidates"]["M01"]["development_span"] == ["2022-01-01", "2024-12-31"]

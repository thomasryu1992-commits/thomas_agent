"""The M04 diagnostic (METRICS_BASKET_SEARCH_V0.1 Q5, Thomas 2026-11-11): the split adds back to the engine's rows,
shows nothing when it does not, and prints no judgement. Synthetic data only."""

from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta, timezone

import pytest

from scripts.research import basket_search as bs
from scripts.research import cross_section_basket as xsb
from scripts.research import metrics_basket_diagnostic as md
from scripts.research import metrics_basket_search as ms

FIRST = date(2022, 9, 1)
SPAN = (date(2023, 2, 1), date(2023, 12, 31))
N = 40
GONE = "S39USDT"                         # the least crowded coin: always a long leg, delisted inside the span
LAST_BAR = date(2023, 8, 16)             # a Wednesday, so the leg is force-closed mid-week
HOURLY = "S10USDT"                       # the most crowded coin of the top 30 (a short leg); settles hourly
CANDIDATE = bs.Candidate(why="test", features=(("ls_level", 1.0),), long_low=True)


def _ms(day: date, hour: int) -> int:
    return int(datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc).timestamp() * 1000)


def _market():
    """``N`` coins, volume falling by index, the long share of all accounts rising with it. One coin stops
    trading inside the span; funding is negative for the uncrowded half and one coin settles hourly."""
    market, daily = xsb.Market(), {}
    days = [FIRST + timedelta(days=k) for k in range((SPAN[1] - FIRST).days + 1)]
    for i in range(N):
        name = bs.REFERENCE if i == 0 else f"S{i:02d}USDT"
        level, bars, rows, funding = 100.0, {}, {}, []
        for k, day in enumerate(days):
            if name == GONE and day > LAST_BAR:
                break
            close = level * math.exp((19.5 - i) * 0.0003 + (0.004 if (k + i) % 2 else -0.004))
            bars[day] = xsb.Bar(open=level, close=close, quote_volume=1000.0 + i, high=max(level, close),
                                low=min(level, close))
            level = close
            rows[day] = {"oi": 1000.0, "ls": 0.70 - 0.01 * i, "top": 0.5, "taker": 0.5}
            hours = range(24) if name == HOURLY else (0, 8, 16)
            funding += [(_ms(day, h), (0.0002 if i < N // 2 else -0.0003) / len(hours) * 3) for h in hours]
        market.bars[name], daily[name], market.funding[name] = bars, rows, funding
    return ms.cut(market, daily, SPAN[1])


def test_the_split_adds_back_to_the_engines_rows_day_by_day():
    market = _market()
    legs, sim = md.attribute(market, CANDIDATE, SPAN, ms.FEATURES)
    assert len(sim["daily"]) > 300 and sim["forced_closes"]["count"] == 13      # 12 legs at the span's end, one delisting
    for key, part in (("gross", "price"), ("funding", "funding"), ("cost", "cost")):
        assert sum(r[part] for r in legs) == pytest.approx(sum(row[key] for row in sim["daily"].values()), abs=1e-9)
    report = bs.measure(market, CANDIDATE, SPAN, ms.FEATURES)          # the report the verdict printed
    body = md.diagnose(market, CANDIDATE, SPAN, ms.FEATURES,
                       recorded={k: report[k] for k in ("sum_pct", "funding_pct", "cost_pct", "forced_closes")})
    assert body["totals_pct"]["net"] == pytest.approx(report["sum_pct"], abs=0.011)
    for part in ("funding", "cost"):                   # the same sum in another order: a cent of rounding at most
        assert body["totals_pct"][part] == pytest.approx(report[f"{part}_pct"], abs=0.011)
    assert sum(m["net"] for m in body["by_month_pct"].values()) == pytest.approx(report["sum_pct"], abs=0.2)
    assert sum(c["funding"] for c in body["by_coin_pct"]) == pytest.approx(report["funding_pct"], abs=0.5)
    sides = body["by_side_pct"]
    assert sides["long"]["funding"] > 0 and sides["short"]["funding"] > 0      # longs are paid, shorts receive
    assert sides["long"]["funding"] + sides["short"]["funding"] == pytest.approx(report["funding_pct"], abs=0.02)


def test_a_split_that_does_not_add_back_shows_nothing(monkeypatch, capsys, tmp_path):
    market = _market()
    real = xsb.funding_on
    calls = {"n": 0}

    def drifting(mkt, symbol, day):                    # the engine and the split read funding in turn: make them differ
        calls["n"] += 1
        rate, known = real(mkt, symbol, day)
        return rate + (1e-6 if calls["n"] % 2 else 0.0), known

    monkeypatch.setattr(xsb, "funding_on", drifting)
    with pytest.raises(md.DoesNotReconcile) as exc:
        md.attribute(market, CANDIDATE, SPAN, ms.FEATURES)
    assert "funding" in str(exc.value) and not any(ch.isdigit() for ch in str(exc.value).split(":", 1)[1])
    monkeypatch.setattr(xsb, "funding_on", real)
    with pytest.raises(md.DoesNotReconcile, match="not the recorded run"):
        md.diagnose(market, CANDIDATE, SPAN, ms.FEATURES, recorded={"sum_pct": 1.23})


def test_the_cuts_name_the_delisting_the_settlement_interval_and_the_concentration():
    body = md.diagnose(_market(), CANDIDATE, SPAN, ms.FEATURES)
    near = body["near_last_bar"]
    assert near["forced_closes"]["count"] == 13
    assert near["forced_closes"]["on_the_spans_last_day"] == 12 and near["forced_closes"]["inside_the_span"] == 1
    assert 0 < near["within_7_days"]["leg_days"] <= 8 < near["within_14_days"]["leg_days"] <= 15
    classes = body["by_settlements_per_day"]
    assert set(classes) == {"3", "9+"} and classes["9+"]["leg_days"] > 100          # the hourly coin is a held short
    assert classes["3"]["leg_days"] + classes["9+"]["leg_days"] == body["leg_days"]
    gone = next(c for c in body["by_coin_pct"] if c["symbol"] == GONE)
    assert gone["long_days"] > 100 and gone["short_days"] == 0
    conc = body["funding_concentration"]
    assert conc["paid_pct"] == 0.0 and conc["received_pct"] == pytest.approx(body["totals_pct"]["funding"], abs=0.5)
    assert 0 < conc["top_coins_share_of_net_funding"]["1"] < conc["top_coins_share_of_net_funding"]["5"] < 1
    assert len(conc["top_weeks"]) == md.TOP_WEEKS and 0 < conc["top_weeks_share_of_net_funding"] < 0.5
    assert [md.settlement_class(n) for n in (0, 2, 3, 4, 8, 9, 24)] == ["0-2", "0-2", "3", "4-8", "4-8", "9+", "9+"]


def _flat(value, prefix=""):
    if isinstance(value, dict):
        for k, v in value.items():
            yield from _flat(v, f"{prefix}.{k}")
    elif isinstance(value, list):
        for v in value:
            yield from _flat(v, prefix)
    else:
        yield prefix


def test_it_prints_no_sharpe_no_verdict_and_no_what_if():
    keys = " ".join(_flat(md.diagnose(_market(), CANDIDATE, SPAN, ms.FEATURES))).lower()
    for word in ("sharpe", "pass", "verdict", "ex_funding", "without", "capped"):
        assert word not in keys


def _run(monkeypatch, argv, **patch):
    monkeypatch.setattr(md.xsb, "_in_sandbox", lambda: True)
    for name, value in patch.items():
        monkeypatch.setattr(md, name, value)
    return md.main(argv)


def test_the_door_is_one_candidate_in_the_sandbox_and_the_locked_span_once(monkeypatch, capsys, tmp_path):
    assert md.CANDIDATE == ms.FINAL == "M04" and md.SPANS == {"dev": ms.DEV_LONG, "confirm": bs.CONFIRM}
    assert md.main(["--span", "dev", "--cache", str(tmp_path)]) == md.EXIT_USAGE          # outside the sandbox
    assert "sandbox" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        _run(monkeypatch, ["--span", "dev", "--candidate", "M03"])                         # no candidate flag
    assert _run(monkeypatch, ["--span", "confirm", "--cache", str(tmp_path)], LOCKED_READ="a-run") == md.EXIT_USAGE
    assert "once" in capsys.readouterr().err
    assert _run(monkeypatch, ["--span", "dev", "--cache", str(tmp_path)], LOCKED_READ="a-run") == md.EXIT_BLOCKED
    assert json.dumps(md.RECORDED["confirm"]) == json.dumps(
        {"sum_pct": 163.85, "funding_pct": 78.58, "cost_pct": 11.94, "forced_closes": 13})

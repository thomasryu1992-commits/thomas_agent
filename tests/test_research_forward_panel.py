"""The forward panel (FORWARD_PANEL_V0.1, Thomas 2026-11-12): who is in it, that nothing is computed before the
window has ended, the three readings, and that the report judges nothing. Synthetic data only."""

from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta, timezone

import pytest

from scripts.research import basket_search as bs
from scripts.research import cross_section_basket as xsb
from scripts.research import depth_basket_search as ds
from scripts.research import forward_panel as fp
from scripts.research import metrics_basket_search as ms
from scripts.research import spot_share_search as sp

FIRST = date(2021, 6, 1)
SPAN = (date(2022, 1, 1), date(2022, 12, 31))
N = 40


def _ms(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp() * 1000)


def _held(last: date = SPAN[1]):
    """``N`` coins with bars, funding, metrics, depth values and a spot pair each, cut at ``last``."""
    perp, spot, metrics, depth = xsb.Market(), xsb.Market(), {}, {}
    days = [FIRST + timedelta(days=k) for k in range((last - FIRST).days + 1)]
    for i in range(N):
        name = bs.REFERENCE if i == 0 else f"S{i:02d}USDT"
        level, bars, cash = 100.0, {}, {}
        for k, day in enumerate(days):
            close = level * math.exp((14.5 - i) * 0.0003 + (0.004 if (k + i) % 3 else -0.007) * (1 + i / 60))
            bars[day] = xsb.Bar(open=level, close=close, quote_volume=(1000.0 - i) * (1 + 0.1 * ((k + i) % 5)))
            cash[day] = xsb.Bar(open=level, close=close, quote_volume=(1000.0 - i) * (60 - i) / 40)
            level = close
        perp.bars[name], spot.bars[name] = bars, cash
        perp.funding[name] = [(_ms(day) + 8 * 3_600_000, 0.0) for day in days]
        metrics[name] = {day: {"oi": 1e6 + i, "ls": 0.4, "top": 0.3 + 0.01 * i, "taker": 0.6 - 0.005 * i} for day in days}
        depth[name] = {day: {"imb1": 0.3 - 0.02 * i, "imb5": 0.1, "depth1": 1e6 * (1 + 0.002 * i) ** k}
                       for k, day in enumerate(days)}
    held = ds.cut(perp, depth, last, metrics)
    return sp.attach(held, spot, set(spot.bars), last, {name: {f"{d:%Y-%m}" for d in days} for name in spot.bars})


def test_the_panel_is_the_sixteen_closed_definitions_as_registered_and_nothing_with_a_read_ahead():
    names = [name for name, _, _ in fp.members()]
    assert names == [f"A{i:02d}" for i in range(1, 11)] + ["F01", "F02", "F03", "D01", "D02", "S01"]
    assert not {"M01", "M02", "M03", "M04", "T01", "T02"} & set(names)        # M04, its inputs, the trend book
    assert set(fp.DEVELOPMENT_SHARPE) == set(names)
    assert fp.definitions_fingerprint() == fp.REGISTERED                     # a member edited later fails here
    by_name = {name: candidate for name, candidate, _ in fp.members()}
    assert (by_name["D01"].features, by_name["D01"].lookback, by_name["D01"].long_low) == ((("imbalance_1", 1.0),), 7, False)
    assert (by_name["A01"].long_low, by_name["A01"].inverse_vol) == (True, True)
    assert by_name["F03"].features == (("top_level", 1.0), ("taker_level", 1.0)) and by_name["S01"].lookback == 30
    assert (fp.DEVELOPMENT_SHARPE["D01"], fp.DEVELOPMENT_SHARPE["A06"], fp.DEVELOPMENT_SHARPE["S01"]) == (-1.652, 0.716, 0.455)
    assert fp.WINDOW == (date(2026, 12, 1), date(2027, 11, 30)) and fp.READ_FROM == date(2027, 12, 8)
    assert fp.WINDOW[0] >= bs.RESERVED_FROM                                   # a forward window, never a search span


def test_a_changed_definition_is_seen_by_the_fingerprint(monkeypatch):
    flipped = bs.Candidate(why="x", features=(("imbalance_1", 1.0),), lookback=7, long_low=True)
    monkeypatch.setitem(ds.CANDIDATES, "D01", flipped)
    assert fp.definitions_fingerprint() != fp.REGISTERED
    assert fp.main(["counts"], today=date(2028, 1, 1)) == fp.EXIT_USAGE      # refused before anything is loaded
    monkeypatch.setitem(ds.CANDIDATES, "D01", bs.Candidate(why="another wording", features=(("imbalance_1", 1.0),), lookback=7))
    assert fp.definitions_fingerprint() == fp.REGISTERED                     # the prose is not the definition


def test_the_rank_correlation_uses_average_ranks_and_needs_three_pairs():
    assert fp.spearman([(1, 10), (2, 20), (3, 30)]) == 1.0
    assert fp.spearman([(1, 30), (2, 20), (3, 10)]) == -1.0
    assert fp.spearman([(1, 1), (2, 1), (3, 2), (4, 2)]) == pytest.approx(0.894, abs=1e-3)
    assert fp.spearman([(1, 2), (2, 1)]) is None and fp.spearman([(1, 5), (2, 5), (3, 5)]) is None


def test_the_three_readings_from_known_daily_returns(monkeypatch):
    monkeypatch.setattr(fp, "WINDOW", SPAN)
    days = [SPAN[0] + timedelta(days=k) for k in range(365)]
    order = [name for name, _, _ in fp.members()]
    calls = []

    def simulate(market, signal, monthly, *, start, end, weigher):
        name = order[len(calls)]
        calls.append((start, end))
        if name == "D02":
            return {"daily": {}}                                  # a member that never had a basket
        scale = fp.DEVELOPMENT_SHARPE[name]                       # forward follows the development order exactly
        return {"daily": {d: {"net": scale * 0.001 + (0.002 if d.day % 2 else -0.002) * (1 + d.month / 12)}
                          for d in days}}

    report = fp.read(_held(), simulate=simulate)
    assert calls == [SPAN] * 16 and set(report["members"]) == set(order)
    assert report["members"]["D02"] == {"annual_sharpe": None, "sum_pct": 0.0, "months": 12,
                                        "total_position_annual_pct": 0.0, "development_annual_sharpe": 0.838}
    assert report["members"]["F02"]["annual_sharpe"] > report["members"]["A01"]["annual_sharpe"] > report["members"]["D01"]["annual_sharpe"]
    assert report["development_vs_forward"] == {"rank_correlation": 1.0, "members_compared": 15}
    # the pool is the mean over all sixteen; the member without a basket counts as zero on every day
    drift = sum(v for n, v in fp.DEVELOPMENT_SHARPE.items() if n != "D02") * 0.001 * 365 / 16
    noise = sum((0.002 if d.day % 2 else -0.002) * (1 + d.month / 12) for d in days) * 15 / 16
    assert report["pool"]["sum_pct"] == pytest.approx((drift + noise) * 100, abs=0.01)
    assert report["pool"]["name"] == "POOL-16" and report["pool"]["months"] == 12
    assert report["judgement"] is None and report["display_only"] is True
    flat = json.dumps(report).lower()
    assert all(word not in flat for word in ('"pass"', "verdict", "keep", "screen"))


def test_the_engine_reads_every_member_on_a_synthetic_market(monkeypatch):
    monkeypatch.setattr(fp, "WINDOW", SPAN)
    monkeypatch.setattr(ds, "FILES_FROM", date(2021, 1, 1))       # the synthetic depth "archive" starts before the span
    held = _held()
    report = fp.read(held)
    assert len(report["members"]) == 16 and all(r["months"] == 12 for r in report["members"].values())
    assert all(r["annual_sharpe"] is not None for r in report["members"].values())
    assert report["development_vs_forward"]["members_compared"] == 16
    counted = fp.counts(held, absent=set(), served={})
    assert set(counted["members"]) == set(report["members"]) and counted["unexplained_total"] == 0
    assert counted["members"]["A04"]["weeks_without_a_basket"] == 0 and counted["members"]["A04"]["fewest_ranked"] == 30
    assert counted["members"]["D01"]["unexplained_count"] == 0 and counted["members"]["S01"]["unexplained_count"] == 0
    del held.depth["S03USDT"][date(2022, 6, 20)], held.depth["S03USDT"][date(2022, 6, 21)], held.depth["S03USDT"][date(2022, 6, 22)]
    short = fp.counts(held, absent=set(), served={})
    holes = (short["members"]["D01"]["unexplained_count"], short["members"]["D02"]["unexplained_count"])
    assert holes[0] >= 1 and short["unexplained_total"] == sum(holes)        # a hole nobody named, summed over members


def test_nothing_is_computed_before_the_window_has_ended(monkeypatch, capsys, tmp_path):
    def forbidden(*_a, **_k):
        raise AssertionError("no data may be loaded before the read date")

    for name in ("hold", "read", "counts", "fetch"):
        monkeypatch.setattr(fp, name, forbidden)
    cache = ["--cache", str(tmp_path)]
    monkeypatch.setattr(fp.xsb, "_in_sandbox", lambda: True)
    for day in (date(2026, 11, 12), date(2027, 6, 1), date(2027, 11, 30), date(2027, 12, 7)):
        assert fp.main(["run", *cache], today=day) == fp.EXIT_BLOCKED
        assert fp.NOT_YET in capsys.readouterr().err
        assert fp.main(["counts", *cache], today=day) == fp.EXIT_BLOCKED
    monkeypatch.setattr(fp.xsb, "_in_sandbox", lambda: False)
    assert fp.main(["fetch", *cache], today=date(2027, 6, 1)) == fp.EXIT_BLOCKED
    assert fp.main(["run", *cache], today=date(2028, 1, 1)) == fp.EXIT_USAGE          # outside the sandbox
    assert "sandbox" in capsys.readouterr().err
    monkeypatch.setattr(fp.xsb, "_in_sandbox", lambda: True)
    assert fp.main(["fetch", *cache], today=date(2028, 1, 1)) == fp.EXIT_USAGE        # a host command inside it
    assert fp.main(["list"], today=date(2026, 11, 12)) == fp.EXIT_OK                  # the registration is always shown
    assert json.loads(capsys.readouterr().out)["registered"] == fp.REGISTERED


def test_the_run_needs_the_inputs_the_last_day_and_every_cause_named(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(fp.xsb, "_in_sandbox", lambda: True)
    monkeypatch.setattr(fp, "WINDOW", SPAN)
    monkeypatch.setattr(fp.xsb, "provenance", lambda: {"lake_snapshot": None})
    late, cache = date(2028, 1, 1), ["--cache", str(tmp_path)]
    assert fp.main(["run", *cache], today=late) == fp.EXIT_BLOCKED                    # nothing fetched there
    assert "fetch" in capsys.readouterr().err
    monkeypatch.setattr(fp, "hold", lambda cache: (_held(date(2022, 12, 30)), set(), {}))
    assert fp.main(["run", *cache], today=late) == fp.EXIT_BLOCKED                    # the bars stop a day short
    assert fp.NOT_YET in capsys.readouterr().err
    monkeypatch.setattr(fp, "hold", lambda cache: (_held(), set(), {}))
    monkeypatch.setattr(fp, "counts", lambda market, absent, served: {"unexplained_total": 2, "members": {}})
    assert fp.main(["run", *cache], today=late) == fp.EXIT_BLOCKED
    assert "no named cause" in capsys.readouterr().err
    monkeypatch.undo()
    monkeypatch.setattr(fp.xsb, "_in_sandbox", lambda: True)
    monkeypatch.setattr(fp, "WINDOW", SPAN)
    monkeypatch.setattr(fp.xsb, "provenance", lambda: {"lake_snapshot": None})
    monkeypatch.setattr(fp, "hold", lambda cache: (_held(), set(), {}))
    assert fp.main(["run", *cache], today=late) == fp.EXIT_OK
    report = json.loads(capsys.readouterr().out)
    assert report["spec"] == "FORWARD_PANEL_V0.1" and report["registered"] == fp.REGISTERED
    assert len(report["members"]) == 16 and report["judgement"] is None and report["counts"]["unexplained_total"] == 0


def test_fetch_asks_the_owner_tools_for_the_windows_inputs(monkeypatch, tmp_path):
    monkeypatch.setattr(fp, "WINDOW", SPAN)
    held = _held()
    (tmp_path / "archive").mkdir()
    (tmp_path / sp.SPOT_DIR).mkdir()
    (tmp_path / sp.SPOT_DIR / fp.ba.SYMBOLS_FILENAME).write_text(json.dumps(sorted(held.bars)), encoding="utf-8")
    monkeypatch.setattr(fp.xsb, "read_contract_types", lambda archive: ({}, "now"))
    monkeypatch.setattr(fp.xsb, "load_market", lambda archive, funding=True: held)
    calls, said = [], []
    kw = dict(fetcher=lambda dest, *, symbols, start, end: calls.append(("metrics", dest.name, symbols[0], start, end)),
              fetch_monthly=lambda dest, *, kind, symbols, start, end: calls.append(("funding", kind, len(symbols), start, end)),
              depth_fetch=lambda cache, plan: calls.append(("depth", len(plan), min(a for _, a, _ in plan))) or fp.EXIT_OK,
              spot_fetch=lambda cache, plan: calls.append(("spot", len(plan), min(a for _, a, _ in plan), max(b for _, _, b in plan))) or fp.EXIT_OK,
              build_metrics=lambda cache, say: calls.append(("build", "metrics")),
              build_depth=lambda cache, say: calls.append(("build", "depth")), say=said.append)
    assert fp.fetch(tmp_path, quiet=lambda: False, **kw) == fp.EXIT_OK
    kinds = [c[0] for c in calls]
    assert kinds[0] == "funding" and calls[0][1:] == (xsb.FUNDING_KIND, 30, *SPAN)
    metrics = [c for c in calls if c[0] == "metrics"]
    assert len(metrics) == 30 and {c[1] for c in metrics} == {"archive-metrics"}
    assert min(c[3] for c in metrics) == SPAN[0] - timedelta(days=ms.PLAN_LOOKBACK) and max(c[4] for c in metrics) == SPAN[1]
    assert ("depth", 30, SPAN[0] - timedelta(days=ds.PLAN_LOOKBACK)) in calls
    assert ("spot", 30, date(2021, 9, 1), SPAN[1]) in calls                       # four months before the first member month
    assert kinds[-2:] == ["build", "build"] and "30 member symbol" in said[-1]
    calls.clear()
    assert fp.fetch(tmp_path, quiet=lambda: True, **kw) == fp.EXIT_BLOCKED and calls == []
    monkeypatch.setattr(fp.xsb, "load_market", lambda archive, funding=True: _held(date(2022, 12, 1)))
    assert fp.fetch(tmp_path, quiet=lambda: False, **kw) == fp.EXIT_BLOCKED and fp.NOT_YET in said[-1]

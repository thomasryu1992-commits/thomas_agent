"""The spot share basket search (SPOT_SHARE_SEARCH_V0.1, Thomas 2026-11-12): the feature, the same-asset check, the
named causes, the M04 independence condition and the door. Synthetic data only."""

from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta, timezone

import pytest

from scripts import binance_archive as ba
from scripts.research import basket_search as bs
from scripts.research import cross_section_basket as xsb
from scripts.research import metrics_basket_search as ms
from scripts.research import spot_share_search as sp

FIRST = date(2021, 6, 1)
SPAN = (date(2022, 1, 1), date(2022, 12, 31))
N = 40
HIGH = bs.Candidate(why="t", features=(("spot_share", 1.0),), lookback=30)


def _ms(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp() * 1000)


def _market(*, spot_leads: bool = True, crowded_like_m04: bool | None = None):
    """``N`` perps, volume falling by index, each with a spot pair of the same name. A low index has the HIGH spot
    share and drifts up when ``spot_leads``. With ``crowded_like_m04`` the metrics make M04 buy the same coins
    (True) or the opposite ones (False)."""
    perp, spot, metrics = xsb.Market(), xsb.Market(), {}
    days = [FIRST + timedelta(days=k) for k in range((SPAN[1] - FIRST).days + 1)]
    for i in range(N):
        name = bs.REFERENCE if i == 0 else f"S{i:02d}USDT"
        drift = (14.5 - i) * 0.0004 * (1 if spot_leads else -1)
        level, bars, cash = 100.0, {}, {}
        for k, day in enumerate(days):
            close = level * math.exp(drift + (0.003 if (k + i) % 2 else -0.003))
            bars[day] = xsb.Bar(open=level, close=close, quote_volume=1000.0 - i)
            cash[day] = xsb.Bar(open=level, close=close * 1.001, quote_volume=(1000.0 - i) * (60 - i) / 40)
            level = close
        perp.bars[name], spot.bars[name] = bars, cash
        perp.funding[name] = [(_ms(day) + 8 * 3_600_000, 0.0) for day in days]
        if crowded_like_m04 is not None:
            rank = i if crowded_like_m04 else N - i                 # M04 buys the LOW open-interest growth and long share
            metrics[name] = {day: {"oi": 1e6 * (1 + 0.0001 * rank) ** k, "ls": 0.3 + 0.01 * rank}
                             for k, day in enumerate(days)}
    return perp, spot, metrics


def _held(perp, spot, metrics=None, listing=None, files=None):
    listing = set(spot.bars) if listing is None else listing
    return sp.attach(ms.cut(perp, metrics or {}, SPAN[1]), spot, listing, SPAN[1], files)


def test_the_share_is_spot_over_spot_plus_perp_and_reads_the_decision_day_and_earlier_only():
    perp, spot, _ = _market()
    day, s = date(2022, 6, 26), "S07USDT"
    held = _held(perp, spot)
    ratio = (60 - 7) / 40
    assert sp.FEATURES["spot_share"](held, s, day, 30) == pytest.approx(ratio / (1 + ratio))
    before = sp.FEATURES["spot_share"](held, s, day, 30)
    for later in (day + timedelta(days=1), day + timedelta(days=9)):
        spot.bars[s][later] = xsb.Bar(open=1.0, close=spot.bars[s][later].close, quote_volume=9e12)
    assert sp.FEATURES["spot_share"](_held(perp, spot), s, day, 30) == before
    spot.bars[s][day] = xsb.Bar(open=1.0, close=spot.bars[s][day].close, quote_volume=9e12)   # the day itself counts
    assert sp.FEATURES["spot_share"](_held(perp, spot), s, day, 30) > 0.99
    for k in range(11):                                              # 19 of 30 days left: under the two-thirds floor
        del spot.bars[s][day - timedelta(days=k)]
    assert sp.FEATURES["spot_share"](_held(perp, spot), s, day, 30) is None


def test_a_day_counts_only_when_perp_and_spot_are_the_same_asset():
    assert sp.pair_of("1000PEPEUSDT", {"PEPEUSDT"}) == ("PEPEUSDT", 1000.0)
    assert sp.pair_of("1000SATSUSDT", {"1000SATSUSDT"}) == ("1000SATSUSDT", 1.0)     # the spot carries the prefix too
    assert sp.pair_of("SOLUSDT", {"SOLUSDT"}) == ("SOLUSDT", 1.0) and sp.pair_of("MEWUSDT", {"SOLUSDT"}) is None
    perp, spot, _ = _market()
    day, s = date(2022, 6, 26), "S07USDT"
    perp.bars["1000S07USDT"] = {d: xsb.Bar(open=b.open * 1000, close=b.close * 1000, quote_volume=b.quote_volume)
                                for d, b in perp.bars[s].items()}
    held = _held(perp, spot)
    assert held.spot_of["1000S07USDT"] == (s, 1000.0)
    assert sp.FEATURES["spot_share"](held, "1000S07USDT", day, 30) == pytest.approx(sp.FEATURES["spot_share"](held, s, day, 30))
    for k in range(12):                                              # the ticker turns into another coin
        d = day - timedelta(days=k)
        spot.bars[s][d] = xsb.Bar(open=1.0, close=spot.bars[s][d].close * 0.01, quote_volume=9e12)
    held = _held(perp, spot)
    assert sp.FEATURES["spot_share"](held, s, day, 30) is None       # 18 same-asset days left; the inflated days never count
    assert sp.PRICE_BAND == 0.10
    kept, why = sp._days(held, s, day, 30)
    assert len(kept) == 18 and why["another asset"] == 12


def test_every_unranked_member_gets_a_named_cause_or_is_unexplained():
    perp, spot, _ = _market()
    decision = date(2022, 3, 6)
    span = (date(2022, 3, 1), date(2022, 3, 13))
    listing = set(spot.bars) - {"S01USDT"}                           # S01 has no spot pair
    for k in range(40):                                              # S02's perp was listed 12 days before the decision
        perp.bars["S02USDT"].pop(decision - timedelta(days=12 + k), None)
    for d, bar in list(perp.bars["S02USDT"].items()):               # ... on volume enough to enter the month's top 30
        if date(2022, 2, 1) <= d < date(2022, 3, 1):
            perp.bars["S02USDT"][d] = xsb.Bar(open=bar.open, close=bar.close, quote_volume=bar.quote_volume * 10)
    for d in [d for d in spot.bars["S03USDT"] if d < date(2022, 3, 1)]:
        del spot.bars["S03USDT"][d]                                  # S03's spot months before March are not served
    for d in [d for d in spot.bars["S04USDT"] if d < date(2022, 2, 24)]:
        del spot.bars["S04USDT"][d]                                  # S04's spot pair began on 02-24, inside a served month
    for k in range(15):                                              # S05's ticker is another coin for 15 days
        d = decision - timedelta(days=k)
        spot.bars["S05USDT"][d] = xsb.Bar(open=1.0, close=spot.bars["S05USDT"][d].close * 3, quote_volume=1.0)
    for k in range(3, 16):                                           # S06 and S08 lack bars inside their own range,
        del spot.bars["S06USDT"][decision - timedelta(days=k)]       # in a served month: S06's file is on disk
        del spot.bars["S08USDT"][decision - timedelta(days=k)]       # (the pair was halted), S08's is not
    served = {name: ["2021-12", "2022-01", "2022-02", "2022-03"] for name in spot.bars}
    served["S03USDT"] = ["2022-03"]
    files = {"S06USDT": {"2022-02", "2022-03"}, "S08USDT": {"2022-03"}}
    held = _held(perp, spot, listing=listing, files=files)
    counts = sp.ranked_counts(held, HIGH, span, served)
    assert counts["decisions"] == 1 and counts["members_ranked_side_weeks"] == [[30, 23, 4, 1]]
    assert counts["unranked_by_cause"] == {
        "no spot pair": 1, "the perp is younger than the window": 1, "the archive does not serve the spot month": 1,
        "outside the spot pair's first and last day": 1, "another asset under the same ticker": 1,
        "the spot file on disk holds no bar for the day": 1,
        "UNEXPLAINED + the spot file on disk holds no bar for the day": 1}
    assert counts["unexplained_count"] == 1 and counts["unexplained"] == [("S08USDT", decision.isoformat())]
    blind = sp.ranked_counts(_held(perp, spot, listing=listing), HIGH, span, served)       # no file list: never excused
    assert blind["unexplained_count"] == 2
    clean_perp, clean_spot, _ = _market()
    whole = sp.ranked_counts(_held(clean_perp, clean_spot), HIGH, SPAN, {})
    assert whole["members_ranked_side_weeks"] == [[30, 30, 6, 52]] and whole["unexplained_count"] == 0
    flat = json.dumps(counts).lower()
    assert all(word not in flat for word in ("sharpe", "sum_pct", "net", "pnl"))


def test_a_pass_needs_the_entry_rule_a_sound_neighbour_and_independence_from_m04(monkeypatch):
    monkeypatch.setattr(sp, "DEV", SPAN)
    monkeypatch.setattr(sp, "CANDIDATES", {"S01": HIGH})
    longer = bs.Candidate(why="t", features=(("spot_share", 1.0),), lookback=90)
    monkeypatch.setattr(sp, "NEIGHBOURS", {"S01": {"lookback_90": longer}})
    monkeypatch.setattr(sp, "MIN_COMPARED", 40)
    apart = sp.explore(_held(*_market(crowded_like_m04=False)), "S01")
    assert apart["pass"] is True and apart["fragile"] is False and apart["total_position_annual_pct"] >= 15.0
    assert apart["overlap"]["decisions_compared"] >= 40 and apart["overlap"]["mean_share_of_legs_on_the_references_side"] == 0.0
    assert apart["overlap"]["independent"] is True and apart["forward_slot_may_be_asked"] is True
    same = sp.explore(_held(*_market(crowded_like_m04=True)), "S01")        # the same legs as M04: M04 seen again
    assert same["pass"] is True and same["overlap"]["mean_share_of_legs_on_the_references_side"] == 1.0
    assert same["overlap"]["independent"] is False and same["forward_slot_may_be_asked"] is False
    unknown = sp.explore(_held(*_market()), "S01")                           # no metrics: nothing to compare with
    assert unknown["pass"] is True and unknown["overlap"]["decisions_compared"] == 0
    assert unknown["overlap"]["independent"] is None and unknown["forward_slot_may_be_asked"] is False
    monkeypatch.setattr(sp, "MIN_COMPARED", 500)                             # too few decisions compared: not cleared
    few = sp.explore(_held(*_market(crowded_like_m04=False)), "S01")
    assert few["overlap"]["independent"] is None and few["forward_slot_may_be_asked"] is False
    monkeypatch.setattr(sp, "MIN_COMPARED", 40)
    backwards = bs.Candidate(why="t", features=(("spot_share", 1.0),), lookback=90, long_low=True)
    monkeypatch.setattr(sp, "NEIGHBOURS", {"S01": {"lookback_90": backwards}})
    fragile = sp.explore(_held(*_market(crowded_like_m04=False)), "S01")
    assert fragile["pass"] is True and fragile["fragile"] is True and fragile["forward_slot_may_be_asked"] is False
    bad = sp.explore(_held(*_market(spot_leads=False, crowded_like_m04=False)), "S01")
    assert bad["pass"] is False and bad["forward_slot_may_be_asked"] is False
    tiny = {"annual_sharpe": 2.3, "sum_pct": 4.0, "months": 12, "pass": True}
    monkeypatch.setattr(sp.bs, "measure", lambda *a, **k: dict(tiny))
    assert sp.explore(_held(*_market(crowded_like_m04=False)), "S01")["pass"] is False     # steady but tiny
    assert (sp.OVERLAP_LIMIT, sp.FRAGILE_BELOW) == (0.40, 0.5)


def test_the_overlap_limit_is_a_limit_on_the_share():
    held = _held(*_market(crowded_like_m04=True))
    got = sp.reference_overlap(held, HIGH, SPAN)
    assert got["limit"] == 0.40 and got["chance_level"] == 0.2 and got["reference"] == "M04"
    assert got["decisions_compared"] == 52 and got["minimum_compared"] == sp.MIN_COMPARED == 52
    assert got["mean_share_of_legs_on_the_references_side"] == 1.0 and got["independent"] is False


def _run(monkeypatch, argv, **patch):
    monkeypatch.setattr(sp.xsb, "_in_sandbox", lambda: True)
    for name, value in patch.items():
        monkeypatch.setattr(sp, name, value)
    return sp.main(argv)


def test_the_door_has_one_attempt_the_registered_direction_and_a_pinned_span(monkeypatch, capsys, tmp_path):
    assert sp.BUDGET == 1 and list(sp.CANDIDATES) == ["S01"] and sp.DEV == bs.DEV == (date(2020, 1, 1), date(2024, 12, 31))
    first = sp.CANDIDATES["S01"]
    assert (first.features, first.lookback, first.long_low, first.inverse_vol) == ((("spot_share", 1.0),), 30, False, False)
    shown = sp.NEIGHBOURS["S01"]
    assert set(shown) == {"lookback_90"} and set(sp.NEIGHBOURS) == set(sp.CANDIDATES)
    assert (shown["lookback_90"].features, shown["lookback_90"].lookback, shown["lookback_90"].long_low) == (
        (("spot_share", 1.0),), 90, False)
    bs.check_development_span(sp.DEV)
    with pytest.raises(SystemExit):
        sp.main(["confirm", "--candidate", "S01"])
    cache, real = ["--cache", str(tmp_path)], dict(sp.CANDIDATES)
    assert sp.main(["explore", "--candidate", "S01", *cache]) == sp.EXIT_USAGE              # outside the sandbox
    assert "sandbox" in capsys.readouterr().err
    assert _run(monkeypatch, ["fetch", *cache]) == sp.EXIT_USAGE                             # a host command inside it
    assert "host" in capsys.readouterr().err
    assert _run(monkeypatch, ["explore", "--candidate", "S09", *cache]) == sp.EXIT_USAGE
    over = {"S01": HIGH, "S02": HIGH}
    assert _run(monkeypatch, ["explore", "--candidate", "S01", *cache], CANDIDATES=over) == sp.EXIT_USAGE
    assert "budget" in capsys.readouterr().err
    foreign = {"S01": bs.Candidate(why="t", features=(("oi_growth", 1.0),))}
    assert _run(monkeypatch, ["explore", "--candidate", "S01", *cache], CANDIDATES=foreign) == sp.EXIT_USAGE
    assert _run(monkeypatch, ["explore", "--candidate", "S01", *cache], CANDIDATES=real) == sp.EXIT_BLOCKED   # no archive there
    late = (date(2020, 1, 1), date(2025, 3, 31))
    assert _run(monkeypatch, ["explore", "--candidate", "S01", *cache], DEV=late) == sp.EXIT_USAGE
    assert "forward only" in capsys.readouterr().err
    assert sp.main(["list"]) == sp.EXIT_OK
    listed = json.loads(capsys.readouterr().out)
    assert listed["budget"] == 1 and listed["candidates"]["S01"]["long"] == "high" and listed["overlap_limit"] == 0.40


def test_the_plan_reaches_four_months_before_a_member_month_and_the_fetch_writes_what_the_archive_serves(tmp_path, monkeypatch):
    perp, spot, _ = _market()
    monkeypatch.setattr(sp, "DEV", SPAN)
    plan = sp.fetch_plan(bs.cut(perp, SPAN[1]), set(spot.bars) - {"S01USDT"})
    assert len(plan) == 29 and all(name != "S01USDT" for name, _, _ in plan)       # the top 30, less the one with no pair
    assert {(lo, hi) for _, lo, hi in plan} == {(date(2021, 9, 1), date(2022, 12, 31))}
    calls, said = [], []

    def fetcher(dest, *, kind, symbols, start, end, market):
        calls.append((dest, kind, symbols[0], start, end, market))
        return {symbols[0]: {"kept": 2, "present": 1, "missing": 0}}

    def lister(folder):
        return [], [f"{folder}X-1d-2022-01.zip", f"{folder}X-1d-2022-02.zip", f"{folder}X-1d-2022-02.zip.CHECKSUM"]

    short = plan[:3]
    assert sp.fetch(tmp_path, short, fetcher=fetcher, lister=lister, quiet=lambda: False, say=said.append) == sp.EXIT_OK
    assert [c[1:] for c in calls][0] == ("klines-1d", short[0][0], short[0][1], short[0][2], "spot")
    assert all(c[0] == tmp_path / sp.SPOT_DIR for c in calls) and "kept 6, present 3" in said[-1]
    assert sp.read_served(tmp_path) == {name: ["2022-01", "2022-02"] for name, _, _ in short}
    assert ba.monthly_folder("klines-1d", "ABCUSDT", "spot").endswith("/ABCUSDT/1d/")
    calls.clear()
    assert sp.fetch(tmp_path / "other", short, fetcher=fetcher, lister=lister, quiet=lambda: True, say=said.append) == sp.EXIT_BLOCKED
    assert calls == [] and "quiet window" in said[-1] and sp.read_served(tmp_path / "other") is None


def test_the_fingerprint_follows_the_spot_files_and_the_served_months(tmp_path):
    folder = tmp_path / sp.SPOT_DIR / sp.KLINES / "AUSDT"
    folder.mkdir(parents=True)
    (folder / "AUSDT-1d-2022-01.zip").write_bytes(b"12345")
    one = sp.fingerprint(tmp_path, ["AUSDT"])
    assert sp.fingerprint(tmp_path, ["AUSDT"]) == one and len(one) == 16
    (folder / "AUSDT-1d-2022-02.zip").write_bytes(b"1")
    two = sp.fingerprint(tmp_path, ["AUSDT"])
    sp.derived_dir(tmp_path).mkdir(parents=True)
    (sp.derived_dir(tmp_path) / sp.SERVED_FILENAME).write_text('{"AUSDT": ["2022-01"]}', encoding="utf-8")
    assert len({one, two, sp.fingerprint(tmp_path, ["AUSDT"])}) == 3


def test_the_months_on_disk_are_read_from_the_spot_folder(tmp_path):
    folder = tmp_path / sp.SPOT_DIR / sp.KLINES / "AUSDT"
    folder.mkdir(parents=True)
    for name in ("AUSDT-1d-2022-10.zip", "AUSDT-1d-2022-11.zip", "notes.txt"):
        (folder / name).write_bytes(b"x")
    assert sp.months_on_disk(tmp_path) == {"AUSDT": {"2022-10", "2022-11"}}

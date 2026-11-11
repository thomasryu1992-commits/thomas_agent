"""The depth basket search (DEPTH_BASKET_SEARCH_V0.1, Thomas 2026-11-11): the daily values, the features, the
counts with named causes, one hypothesis per attempt, the host fetch. Synthetic data only."""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone

import pytest

from scripts import binance_archive as ba
from scripts.research import basket_search as bs
from scripts.research import cross_section_basket as xsb
from scripts.research import depth_basket_search as ds
from scripts.research import metrics_basket_search as ms

FIRST = date(2021, 6, 1)
SPAN = (date(2022, 1, 1), date(2022, 12, 31))
N = 40
FOLLOW = bs.Candidate(why="t", features=(("imbalance_1", 1.0),), lookback=7)


def _ms(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp() * 1000)


def _snapshots(day: date, *, bid: float, ask: float, count: int = 1200, wide: float = 3.0):
    base = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    rows = []
    for k in range(count):
        stamp = f"{base + timedelta(seconds=30 * k):%Y-%m-%d %H:%M:%S}"
        for pct, notional in ((-5, bid * wide), (-1, bid), (1, ask), (5, ask * wide * 2)):
            rows.append({"timestamp": stamp, "percentage": str(pct), "depth": "1", "notional": str(notional)})
    return rows


def _market(*, bids_lead: bool = True):
    """``N`` coins, volume falling by index. Coins with thicker bids (low index) drift up when ``bids_lead``."""
    market, depth = xsb.Market(), {}
    days = [FIRST + timedelta(days=k) for k in range((SPAN[1] - FIRST).days + 1)]
    for i in range(N):
        name = bs.REFERENCE if i == 0 else f"S{i:02d}USDT"
        drift = (14.5 - i) * 0.0004 * (1 if bids_lead else -1)
        level, bars, rows = 100.0, {}, {}
        for k, day in enumerate(days):
            close = level * math.exp(drift + (0.003 if (k + i) % 2 else -0.003))
            bars[day] = xsb.Bar(open=level, close=close, quote_volume=1000.0 - i)
            level = close
            rows[day] = {"imb1": 0.3 - 0.02 * i, "imb5": 0.1 - 0.005 * i, "depth1": 1e6 * (1 + 0.002 * i) ** k}
        market.bars[name], depth[name] = bars, rows
        market.funding[name] = [(_ms(day) + 8 * 3_600_000, 0.0) for day in days]
    return market, depth


def test_a_days_value_reads_its_own_rows_and_the_negative_side_is_the_bid():
    day = date(2023, 5, 7)
    got = ds.daily_values(_snapshots(day, bid=300.0, ask=100.0), day)
    assert got["imb1"] == pytest.approx(0.5) and got["depth1"] == pytest.approx(400.0)      # more bids: positive
    assert got["imb5"] == pytest.approx((900.0 - 600.0) / 1500.0)
    spill = _snapshots(day, bid=300.0, ask=100.0) + _snapshots(day + timedelta(days=1), bid=1.0, ask=9e9)
    assert ds.daily_values(spill, day) == got                       # the next day's rows are not this day's
    short = ds.daily_values(_snapshots(day, bid=3.0, ask=1.0, count=999), day)
    assert short == {"imb1": None, "imb5": None, "depth1": None}
    no_ask = [r for r in _snapshots(day, bid=3.0, ask=1.0) if r["percentage"] != "1"]
    one_sided = ds.daily_values(no_ask, day)
    assert one_sided["imb1"] is None and one_sided["depth1"] is None and one_sided["imb5"] is not None


def test_the_features_read_the_decision_day_and_earlier_only():
    market, depth = _market()
    day, s = date(2022, 6, 26), "S07USDT"
    held = ds.cut(market, depth, SPAN[1])
    assert ds.FEATURES["imbalance_1"](held, s, day, 7) == pytest.approx(0.3 - 0.14)
    assert ds.FEATURES["depth_growth"](held, s, day, 30) == pytest.approx(1.014 ** 30 - 1)
    before = {name: fn(held, s, day, 7 if name != "depth_growth" else 30) for name, fn in ds.FEATURES.items()}
    for d in list(held.depth[s]):
        if d > day:
            held.depth[s][d] = {"imb1": -9.0, "imb5": -9.0, "depth1": 1.0}       # the future is rewritten
    assert before == {name: fn(held, s, day, 7 if name != "depth_growth" else 30) for name, fn in ds.FEATURES.items()}
    for k in (0, 2, 4):                                              # three of seven days gone: under two thirds
        held.depth[s].pop(day - timedelta(days=k))
    assert ds.FEATURES["imbalance_1"](held, s, day, 7) is None
    late = ds.cut(market, depth, date(2022, 3, 31))
    assert max(max(v) for v in late.depth.values()) == date(2022, 3, 31)
    assert set(ds.FEATURES) == {"imbalance_1", "imbalance_5", "depth_growth"}


def test_the_counts_name_why_a_member_is_not_ranked(monkeypatch):
    market, depth = _market()
    monkeypatch.setattr(ds, "FILES_FROM", FIRST)                   # the synthetic archive starts with the bars
    decision = date(2022, 3, 6)                                     # the one Sunday decision inside the span below
    for k in range(7):
        depth["S03USDT"].pop(decision - timedelta(days=k))            # the archive never served these
        depth["S04USDT"][decision - timedelta(days=k)] = {"imb1": None, "imb5": None, "depth1": None}
    for k in range(3):
        depth["S05USDT"].pop(decision - timedelta(days=k))            # on no list: nobody knows why
    absent = {ba.daily_name("S03USDT", ba.BOOK_DEPTH, decision - timedelta(days=k)) for k in range(7)}
    held = ds.cut(market, depth, SPAN[1])
    counts = ds.ranked_counts(held, FOLLOW, (date(2022, 3, 1), date(2022, 3, 13)), absent)
    assert counts["decisions"] == 1 and counts["members_ranked_side_weeks"] == [[30, 27, 5, 1]]
    assert counts["unranked_by_cause"] == {"not served": 1, "partial day": 1, "UNEXPLAINED": 1}
    assert counts["unexplained_count"] == 1 and counts["unexplained"] == [("S05USDT", decision.isoformat())]
    monkeypatch.setattr(ds, "FILES_FROM", decision + timedelta(days=1))     # an archive that starts after the decision
    early = ds.ranked_counts(held, FOLLOW, (date(2022, 3, 1), date(2022, 3, 13)), set())
    assert early["unranked_by_cause"] == {"before the archive's first day": 3} and early["unexplained_count"] == 0
    clean, clean_depth = _market()
    whole = ds.ranked_counts(ds.cut(clean, clean_depth, SPAN[1]), FOLLOW, SPAN, set())
    assert whole["members_ranked_side_weeks"] == [[30, 30, 6, 52]] and whole["unexplained_count"] == 0


def test_following_thicker_bids_passes_when_they_lead_and_a_fragile_pass_is_flagged(monkeypatch):
    monkeypatch.setattr(ds, "DEV", SPAN)
    monkeypatch.setattr(ds, "CANDIDATES", {"D01": FOLLOW})
    wide = bs.Candidate(why="t", features=(("imbalance_5", 1.0),), lookback=7)
    monkeypatch.setattr(ds, "NEIGHBOURS", {"D01": {"band_5pct": wide}})
    market, depth = _market()
    good = ds.explore(ds.cut(market, depth, SPAN[1]), "D01")
    assert good["pass"] is True and good["fragile"] is False and good["total_position_annual_pct"] >= 15.0
    assert good["shown_only_neighbours"]["band_5pct"]["annual_sharpe"] >= 1.0
    assert good["overlap"]["chance_level"] == 0.2
    backwards = bs.Candidate(why="t", features=(("imbalance_5", 1.0),), lookback=7, long_low=True)
    monkeypatch.setattr(ds, "NEIGHBOURS", {"D01": {"band_5pct": backwards}})
    assert ds.explore(ds.cut(market, depth, SPAN[1]), "D01")["fragile"] is True     # the neighbour collapses
    market, depth = _market(bids_lead=False)
    bad = ds.explore(ds.cut(market, depth, SPAN[1]), "D01")
    assert bad["pass"] is False and bad["fragile"] is False
    tiny = {"annual_sharpe": 2.3, "sum_pct": 4.0, "months": 12, "pass": True}
    monkeypatch.setattr(ds.bs, "measure", lambda *a, **k: dict(tiny))
    assert ds.explore(ds.cut(market, depth, SPAN[1]), "D01")["pass"] is False       # steady but tiny


def _run(monkeypatch, argv, **patch):
    monkeypatch.setattr(ds.xsb, "_in_sandbox", lambda: True)
    for name, value in patch.items():
        monkeypatch.setattr(ds, name, value)
    return ds.main(argv)


def test_the_door_has_a_budget_of_two_no_confirm_and_a_pinned_span(monkeypatch, capsys, tmp_path):
    assert ds.BUDGET == 2 and len(ds.CANDIDATES) <= ds.BUDGET and ds.DEV == (date(2023, 2, 1), date(2024, 12, 31))
    bs.check_development_span(ds.DEV)
    assert all({n for n, _ in c.features} <= set(ds.FEATURES) for c in ds.CANDIDATES.values())
    with pytest.raises(SystemExit):
        ds.main(["confirm", "--candidate", "D01"])
    cache = ["--cache", str(tmp_path)]
    monkeypatch.setattr(ds, "CANDIDATES", {"D01": FOLLOW})
    assert ds.main(["explore", "--candidate", "D01", *cache]) == ds.EXIT_USAGE              # outside the sandbox
    assert "sandbox" in capsys.readouterr().err
    for command in ("fetch", "build"):                              # and the two host commands inside it
        assert _run(monkeypatch, [command, *cache]) == ds.EXIT_USAGE
        assert "host" in capsys.readouterr().err
    assert _run(monkeypatch, ["explore", "--candidate", "D09", *cache], CANDIDATES={"D01": FOLLOW}) == ds.EXIT_USAGE
    over = {f"D{k:02d}": FOLLOW for k in range(1, 4)}
    assert _run(monkeypatch, ["explore", "--candidate", "D01", *cache], CANDIDATES=over) == ds.EXIT_USAGE
    foreign = {"D01": bs.Candidate(why="t", features=(("oi_growth", 1.0),))}
    assert _run(monkeypatch, ["explore", "--candidate", "D01", *cache], CANDIDATES=foreign) == ds.EXIT_USAGE
    assert _run(monkeypatch, ["explore", "--candidate", "D01", *cache], CANDIDATES={"D01": FOLLOW}) == ds.EXIT_BLOCKED
    late = (date(2023, 2, 1), date(2025, 3, 31))
    assert _run(monkeypatch, ["explore", "--candidate", "D01", *cache], CANDIDATES={"D01": FOLLOW}, DEV=late) == ds.EXIT_USAGE
    assert "forward only" in capsys.readouterr().err


def test_the_fetch_is_resumable_retries_a_network_error_and_stops_in_the_quiet_window(tmp_path):
    plan = [("A", date(2023, 1, 1), date(2023, 1, 3)), ("B", date(2023, 2, 1), date(2023, 2, 1))]
    calls, said, slept = [], [], []

    def flaky(dest, *, symbols, start, end, kind):
        calls.append((symbols[0], kind))
        if len(calls) == 1:
            raise OSError("handshake timed out")
        return {symbols[0]: {"kept": (end - start).days + 1, "present": 0, "missing": 0}}

    assert ds.fetch(tmp_path, plan, fetcher=flaky, quiet=lambda: False, say=said.append, sleep=slept.append) == ds.EXIT_OK
    assert calls == [("A", ba.BOOK_DEPTH), ("A", ba.BOOK_DEPTH), ("B", ba.BOOK_DEPTH)] and slept == [20]
    assert "kept 4" in said[-1]

    def dead(dest, **kw):
        raise OSError("down")

    assert ds.fetch(tmp_path, plan, fetcher=dead, quiet=lambda: False, say=said.append, sleep=slept.append, retries=2) == ds.EXIT_BLOCKED
    assert "failed 2 times" in said[-1]
    calls.clear()
    assert ds.fetch(tmp_path, plan, fetcher=flaky, quiet=lambda: True, say=said.append, sleep=slept.append) == ds.EXIT_BLOCKED
    assert calls == [] and "quiet window" in said[-1]


def test_the_plan_starts_at_the_archives_first_day_and_covers_the_growth_lookback(monkeypatch):
    market, _ = _market()
    monkeypatch.setattr(ds, "DEV", (date(2022, 2, 1), date(2022, 4, 30)))
    monkeypatch.setattr(ds, "FILES_FROM", date(2022, 1, 10))
    plan = ds.fetch_plan(bs.cut(market, date(2022, 4, 30)))
    assert min(first for _, first, _ in plan) == date(2022, 1, 10) and max(last for _, _, last in plan) == date(2022, 4, 30)
    assert ds.PLAN_LOOKBACK == 36 and len({s for s, _, _ in plan}) == 30


def test_build_writes_the_daily_values_and_a_fingerprint_that_moves_with_them(tmp_path):
    import io
    import zipfile
    day = date(2023, 5, 7)
    folder = tmp_path / "archive-depth" / ba.BOOK_DEPTH / "ABCUSDT"
    folder.mkdir(parents=True)
    for d, bid in ((day, 300.0), (day + timedelta(days=1), 100.0)):
        rows = _snapshots(d, bid=bid, ask=100.0)
        text = "timestamp,percentage,depth,notional\n" + "\n".join(
            f"{r['timestamp']},{r['percentage']},{r['depth']},{r['notional']}" for r in rows) + "\n"
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("x.csv", text)
        (folder / ba.daily_name("ABCUSDT", ba.BOOK_DEPTH, d)).write_bytes(buf.getvalue())
    assert ds.build(tmp_path, say=lambda _: None) == 2
    daily = ds.load_daily(tmp_path)
    assert daily["ABCUSDT"][day]["imb1"] == pytest.approx(0.5) and daily["ABCUSDT"][day + timedelta(days=1)]["imb1"] == 0.0
    first = ds.fingerprint(tmp_path)
    (ds.derived_dir(tmp_path) / "ABCUSDT.jsonl").write_text("[]\n")
    assert ds.fingerprint(tmp_path) != first


def test_the_first_attempt_buys_the_thicker_bid_side_and_its_neighbours_only_move_the_band_or_the_window():
    first = ds.CANDIDATES["D01"]
    assert list(ds.CANDIDATES)[0] == "D01"
    assert (first.features, first.lookback, first.long_low, first.inverse_vol) == ((("imbalance_1", 1.0),), 7, False, False)
    shown = ds.NEIGHBOURS["D01"]
    assert set(shown) == {"band_5pct", "lookback_30"}
    assert (shown["band_5pct"].features, shown["band_5pct"].lookback) == ((("imbalance_5", 1.0),), 7)
    assert (shown["lookback_30"].features, shown["lookback_30"].lookback) == ((("imbalance_1", 1.0),), 30)
    assert all(not n.long_low and not n.inverse_vol for n in shown.values())       # the same direction, never flipped
    assert set(ds.NEIGHBOURS) <= set(ds.CANDIDATES)
    # a day whose bids are thicker scores higher: the sign the hypothesis is stated in
    day = date(2024, 3, 3)
    stamp = lambda k: f"{datetime(2024, 3, 3, tzinfo=timezone.utc) + timedelta(seconds=30 * k):%Y-%m-%d %H:%M:%S}"  # noqa: E731
    rows = [{"timestamp": stamp(k), "percentage": str(pct), "depth": "1", "notional": str(n)}
            for k in range(1200) for pct, n in ((-1, 300.0), (1, 100.0), (-5, 300.0), (5, 100.0))]
    assert ds.daily_values(rows, day)["imb1"] == pytest.approx(0.5)


def test_the_second_attempt_buys_the_deepening_book_and_its_neighbour_only_moves_the_gap():
    assert list(ds.CANDIDATES) == ["D01", "D02"] and len(ds.CANDIDATES) == ds.BUDGET
    second = ds.CANDIDATES["D02"]
    assert (second.features, second.lookback, second.long_low, second.inverse_vol) == ((("depth_growth", 1.0),), 30, False, False)
    assert second.lookback == ds.GROWTH_GAP_DAYS
    shown = ds.NEIGHBOURS["D02"]
    assert set(shown) == {"gap_14"}
    assert (shown["gap_14"].features, shown["gap_14"].lookback, shown["gap_14"].long_low) == ((("depth_growth", 1.0),), 14, False)
    assert set(ds.NEIGHBOURS) == set(ds.CANDIDATES)

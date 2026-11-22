from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from scripts.research import composite as cp
from scripts.research import cross_section_basket as xsb
from scripts.research import signal_lab as lab
from scripts.research import strategy_ladder as sl


def test_the_components_are_every_unstruck_variable_with_a_recorded_side():
    registered = lab.variables()
    unstruck = {n for n in registered if not (n in sl.TIER_SWEEP_OUT or lab.no_book(n, cp.TIER))}
    assert set(cp.COMPONENTS) == unstruck - {"top_change_30", "top_position_30"}, "the two have no 31-100 sweep book"
    assert len(cp.COMPONENTS) == 41 and set(cp.COMPONENTS.values()) == {"high", "low"}
    assert not set(cp.COMPONENTS) & set(cp.struck())
    assert all(cp.load_of(registered[n][0]) for n in cp.COMPONENTS), "every component has a load"


def test_the_score_is_the_equal_weight_mean_of_signed_ranks():
    tables = {"premium_7": {"2025-01-05": {"A": 0.2, "B": 0.8}}, "illiquidity_30": {"2025-01-05": {"A": 0.9}}}
    read = cp.composite(tables, ["premium_7", "illiquidity_30"])
    day = date(2025, 1, 5)
    assert read(None, "A", day) == pytest.approx(((1 - 0.2) + 0.9) / 2), "premium_7 is low-side, illiquidity high"
    assert read(None, "B", day) == pytest.approx(((1 - 0.8) + 0.5) / 2), "a missing component counts as the middle"
    assert read(None, "C", day) is None, "no component value: not ranked"


def test_percentiles_share_ties_and_centre_the_ranks():
    assert cp.percentiles({"a": 1.0, "b": 2.0}) == {"a": 0.25, "b": 0.75}
    assert cp.percentiles({"a": 1.0, "b": 1.0, "c": 3.0}) == pytest.approx({"a": 1 / 3, "b": 1 / 3, "c": 5 / 6})


def test_the_counts_keep_components_by_coverage_alone():
    files = [{"span": "confirm", "ranks": {"coverage": {n: 0.9 for n in cp.COMPONENTS}}}]
    files[0]["ranks"]["coverage"]["premium_7"] = 0.79
    out = cp.counts(files)
    assert "premium_7" in out["left_out"] and len(out["kept"]) == 40 and out["stop"]["stops"] is False
    short = [{"span": "confirm", "ranks": {"coverage": {"premium_7": 0.9}}}]
    assert cp.counts(short)["stop"]["stops"] is True, "a component without a file stops the counts"
    with pytest.raises(ValueError):
        cp.counts([{"span": "search", "ranks": {"coverage": {}}}])


def test_the_ranks_files_merge_by_span_and_never_twice():
    a = {"span": "search", "ranks": {"variables": {"premium_7": {}}}}
    b = {"span": "search", "ranks": {"variables": {"premium_7": {}}}}
    with pytest.raises(ValueError):
        cp.merged([a, b], "search")
    with pytest.raises(ValueError):
        cp.merged([a], "confirm")
    assert cp.merged([a], "search") == {"premium_7": {}}


def test_the_struck_ranks_rebuild_the_same_book_order():
    tables = {"momentum_30": {"2021-01-03": {"A": 0.1, "B": 0.5, "C": 0.9}}}
    read = cp.table_read(tables, "momentum_30")
    values = {s: read(None, s, date(2021, 1, 3)) for s in "ABC"}
    assert sorted(values, key=values.get) == ["A", "B", "C"]


def test_the_read_refuses_out_of_order_before_its_date_and_unregistered(monkeypatch, tmp_path):
    assert cp.main(["composite-counts"]) == cp.EXIT_USAGE
    monkeypatch.setattr(cp.xsb, "_in_sandbox", lambda: True)
    monkeypatch.setenv("RESEARCH_CACHE", str(tmp_path))

    def put(name, body):
        path = tmp_path / name
        path.write_text(json.dumps(body))
        return str(path)
    ok = put("c.json", {"counts": {"stop": {"stops": False}, "kept": ["premium_7"]}})
    passed = put("o.json", {"passes": True, "blocked": False})
    blocked = put("b.json", {"passes": False, "blocked": True})
    closed = put("x.json", {"passes": False, "blocked": False})
    assert cp.main(["composite-ranks", "--load", "bars"]) == cp.EXIT_USAGE
    assert cp.main(["composite-overlap"]) == cp.EXIT_USAGE, "needs the counts"
    assert cp.main(["composite-read", "--counts", ok, "--overlap", blocked]) == cp.EXIT_BLOCKED
    assert cp.main(["composite-read", "--counts", ok, "--overlap", closed]) == cp.EXIT_BLOCKED
    assert cp.main(["composite-read", "--counts", ok, "--overlap", passed], today=date(2026, 12, 12)) == cp.EXIT_USAGE
    registered = sl.CONFIRM_READS
    assert registered[3] == {**registered[3], "read": 4, "candidate": "L31"} and registered[3]["released"], \
        "read 4 was registered for L31 and released when the overlap closed it (section 124)"
    assert cp.main(["composite-read", "--counts", ok, "--overlap", passed], today=date(2026, 12, 13)) == cp.EXIT_USAGE, \
        "a released line opens no read, whatever overlap file is handed in"
    live = (*registered[:3], {k: v for k, v in registered[3].items() if k != "released"})      # the line as registered
    monkeypatch.setattr(sl, "CONFIRM_READS", registered[:3])
    assert cp.main(["composite-read", "--counts", ok, "--overlap", passed], today=date(2026, 12, 13)) == cp.EXIT_USAGE, \
        "read 4 must be registered for L31 in CONFIRM_READS first"
    monkeypatch.setattr(sl, "CONFIRM_READS", live)
    monkeypatch.setattr(sl, "CONFIRM_DONE", {**sl.CONFIRM_DONE, 4: "a run"})
    assert cp.main(["composite-read", "--counts", ok, "--overlap", passed], today=date(2026, 12, 13)) == cp.EXIT_USAGE, \
        "a spent read 4 is refused"
    monkeypatch.setattr(sl, "CONFIRM_DONE", {k: v for k, v in sl.CONFIRM_DONE.items() if k != 4})
    assert cp.main(["composite-read", "--counts", ok, "--overlap", passed], today=date(2026, 12, 13)) == cp.EXIT_BLOCKED, \
        "registered and dated: it reaches the cache (empty here)"
    assert cp.main(["composite-ranks", "--load", "bars", "--span", "search"]) == cp.EXIT_BLOCKED


def test_the_read_is_the_fourth_at_its_bar():
    assert cp.READ_NUMBER == 4 and sl.confirm_bar(4) == pytest.approx(1.483, abs=1e-3)
    assert cp.CONFIRM == (date(2025, 1, 1), date(2026, 9, 30))


# --- section 121: the confirm span's loads read only the days its decisions reach --------------------------------

def _flow_world():
    """One perp and its spot pair, bars and flow every day from 2024-01-01 to 2025-03-31, values that never repeat."""
    import random
    rng = random.Random(121)
    start = date(2024, 1, 1)
    days = [start + timedelta(days=k) for k in range((date(2025, 3, 31) - start).days + 1)]
    bars = {d: xsb.Bar(open=100.0, close=100.0 * (1 + rng.uniform(-0.03, 0.03)), quote_volume=rng.uniform(1e6, 2e6))
            for d in days}
    market = xsb.Market(bars={"AUSDT": bars})
    market.trade_flow = {"AUSDT": {d: (rng.uniform(0.3, 0.7), rng.uniform(10, 900)) for d in days}}
    market.spot = {"AUSDT_SPOT": {d: xsb.Bar(open=b.open, close=b.close, quote_volume=b.quote_volume) for d, b in bars.items()}}
    market.spot_of = {"AUSDT": ("AUSDT_SPOT", 1.0)}
    market.spot_flow = {"AUSDT_SPOT": {d: (rng.uniform(0.3, 0.7), rng.uniform(10, 900)) for d in days}}
    return market


def test_the_confirm_flow_floor_leaves_every_flow_variable_as_it_was():
    registered = lab.variables()
    flow = sorted(n for n, (g, _) in registered.items() if g in cp.LOADS["bars"] | cp.LOADS["spot_flow"])
    assert {n for n in cp.COMPONENTS if registered[n][0] in cp.LOADS["bars"] | cp.LOADS["spot_flow"]} <= set(flow), \
        "every variable the two trimmed loads rank, whatever its group, not only the flow groups"
    decisions = [date(2025, 1, 5) + timedelta(days=7 * k) for k in range(12)]
    full = _flow_world()

    def cut(floor):
        market = _flow_world()
        market.trade_flow = {s: cp.trimmed(v, floor, cp.CONFIRM[1]) for s, v in market.trade_flow.items()}
        market.spot_flow = {s: cp.trimmed(v, floor, cp.CONFIRM[1]) for s, v in market.spot_flow.items()}
        return market
    floored = cut(cp.CONFIRM[0] - timedelta(days=cp.FLOW_DAYS))
    for name in flow:
        read = registered[name][1]
        before = [read(full, "AUSDT", d) for d in decisions]
        if registered[name][0] in ("trade_flow", "spot_flow"):
            assert any(v is not None for v in before), f"{name}: the world gives it values"
        assert [read(floored, "AUSDT", d) for d in decisions] == before, name
    alone = cut(cp.CONFIRM[0] - timedelta(days=cp.FLOW_DAYS))
    alone.trade_flow = {}
    alone.spot = {"AUSDT_SPOT": alone.spot["AUSDT_SPOT"]}
    for name in (n for n in flow if registered[n][0] in cp.LOADS["spot_flow"]):
        assert [registered[name][1](alone, "AUSDT", d) for d in decisions] == \
            [registered[name][1](full, "AUSDT", d) for d in decisions], f"{name} reads no perp flow"
    tight = cut(decisions[0] - timedelta(days=200))
    assert any(registered[n][1](tight, "AUSDT", decisions[0]) != registered[n][1](full, "AUSDT", decisions[0])
               for n in flow), "the test sees a floor that is too late"


def _hourly_zips(folder, symbol, first_hour, hours, rng):
    import io
    import zipfile
    by_month: dict[str, list[str]] = {}
    price = 100.0
    for h in range(first_hour, first_hour + hours):
        price *= 1 + rng.gauss(0, 0.004)
        month = hv_month(h)
        by_month.setdefault(month, []).append(f"{h * 3_600_000},{price},0,0,0,0,0,{rng.uniform(1, 50)},0,0,0,0")
    folder.mkdir(parents=True)
    for month, rows in by_month.items():
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as z:
            z.writestr(f"{symbol}-1h-{month}.csv", "open_time,open,h,l,c,v,ct,quote_volume,n,tb,tq,i\n" + "\n".join(rows))
        (folder / f"{symbol}-1h-{month}.zip").write_bytes(buffer.getvalue())


def hv_month(hour: int) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(hour * 3600, tz=timezone.utc).strftime("%Y-%m")


def test_the_confirm_hourly_floor_leaves_the_hourly_values_as_they_were(tmp_path):
    import random
    from scripts.research import hourly_vars as hv
    rng = random.Random(7)
    first = hv.hour_of(date(2024, 10, 1))
    hours = hv.hour_of(date(2025, 3, 1)) - first
    for symbol in (hv.REFERENCE, "AUSDT"):
        _hourly_zips(tmp_path / hv.HOURLY_KIND / symbol, symbol, first, hours, rng)
    decisions = [date(2025, 1, 5) + timedelta(days=7 * k) for k in range(7)]
    span = (date(2024, 10, 1), date(2025, 2, 27))
    whole, _ = hv.build(tmp_path, {"AUSDT": decisions}, span)
    after = hv.hour_of(cp.CONFIRM[0] - timedelta(days=cp.HOURLY_DAYS))
    floored, _ = hv.build(tmp_path, {"AUSDT": decisions}, span, after=after)
    assert set(whole["AUSDT"]) == set(decisions)
    assert floored["AUSDT"].keys() == whole["AUSDT"].keys()
    for day in decisions:
        assert floored["AUSDT"][day] == pytest.approx(whole["AUSDT"][day], rel=1e-9, abs=1e-12), day
    late, _ = hv.build(tmp_path, {"AUSDT": decisions}, span, after=hv.hour_of(decisions[0] - timedelta(days=20)))
    assert decisions[0] not in late.get("AUSDT", {}), "the test sees a floor that is too late"


def _confirm_metrics_zip(path, day, ls):
    import zipfile
    rows = ["create_time,symbol,sum_open_interest,sum_open_interest_value,count_toptrader_long_short_ratio,"
            "sum_toptrader_long_short_ratio,count_long_short_ratio,sum_taker_long_short_vol_ratio"]
    rows += [f"{day.isoformat()} {k // 12:02d}:{(k % 12) * 5:02d}:00,X,100.0,1,1,{ls},{ls},{ls}" for k in range(288)]
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(path.stem + ".csv", "\n".join(rows) + "\n")


def test_the_confirm_positioning_lives_in_its_own_root_and_stops_at_the_confirm_span(tmp_path, monkeypatch, capsys):
    cache, ba = tmp_path, lab.ba
    shared, root = cache / "archive-metrics", cache / cp.CONFIRM_METRICS_ROOT
    d1, d2, late = date(2024, 12, 30), date(2025, 1, 2), date(2026, 10, 1)
    _confirm_metrics_zip(shared / "metrics" / "AUSDT" / ba.daily_name("AUSDT", "metrics", d1), d1, 1.0)
    _confirm_metrics_zip(shared / "metrics" / "AUSDT" / ba.daily_name("AUSDT", "metrics", late), late, 9.0)
    _confirm_metrics_zip(root / "metrics" / "AUSDT" / ba.daily_name("AUSDT", "metrics", d2), d2, 3.0)
    (root / sl.LOWER_METRICS_PLAN).write_text(json.dumps({"held_symbols": ["AUSDT"],
                                                          "spans": [["AUSDT", d2.isoformat(), d2.isoformat()]]}))
    monkeypatch.setattr(cp.xsb, "_in_sandbox", lambda: False)
    assert cp.main(["composite-positioning-digest", "--cache", str(cache)]) == cp.EXIT_OK
    before = tmp_path / "before.json"
    before.write_text(capsys.readouterr().out)
    assert cp.main(["composite-positioning-build", "--cache", str(cache)]) == cp.EXIT_OK
    built = (cache / "derived" / cp.CONFIRM_METRICS_DAILY / "AUSDT.jsonl").read_text().splitlines()
    assert [json.loads(line)[0] for line in built] == [d1.isoformat(), d2.isoformat()], "both roots, nothing after 2026-09-30"
    assert not (cache / "derived" / "metrics-daily").exists(), "the shared daily file is never written"
    assert cp.main(["composite-positioning-seal", "--cache", str(cache), "--before", str(before)]) == cp.EXIT_OK
    assert (root / sl.LOWER_METRICS_COMPLETE).exists()
    assert not (cache / sl.LOWER_METRICS_ROOT).exists(), "round 45's root is not touched"
    assert cp.main(["composite-positioning-plan"]) == cp.EXIT_USAGE, "the plan reads bars: in the sandbox only"


def test_the_overlap_and_the_read_never_touch_an_attached_series(monkeypatch):
    class Poison:
        def __getattr__(self, name):
            raise AssertionError("an attached series was read")
    start, end = date(2021, 6, 1), date(2022, 12, 31)
    days = [start + timedelta(days=k) for k in range((end - start).days + 1)]
    bars = {f"BIG{i:02d}USDT": {d: xsb.Bar(open=100.0, close=100.0, quote_volume=1e9 - i) for d in days} for i in range(30)}
    for i in range(16):
        price, own = 100.0, {}
        for k, d in enumerate(days):
            move = (0.004 if ((k + 4 * i) // 30) % 2 == 0 else -0.004) * (1 + i / 40)
            own[d] = xsb.Bar(open=price, close=price * (1 + move), quote_volume=1e7 - i * 1e4)
            price *= 1 + move
        bars[f"MID{i:02d}USDT"] = own
    row = [(int((start - date(1970, 1, 1)).total_seconds() * 1000) + 3_600_000, 0.0)]
    market = xsb.Market(bars=bars, funding={s: list(row) for s in bars})
    for name in ("trade_flow", "spot_flow", "spot", "spot_of", "hourly_values", "metrics", "premium"):
        setattr(market, name, Poison())
    span = (date(2022, 1, 1), date(2022, 12, 31))
    monkeypatch.setattr(cp, "SEARCH", span)
    monkeypatch.setattr(cp, "CONFIRM", span)
    sundays = [d for d in days if span[0] <= d and d.weekday() == 6]
    tables = {n: {d.isoformat(): {f"MID{i:02d}USDT": (i + 0.5 + (k % 3)) / 19 for i in range(16)}
                  for k, d in enumerate(sundays)} for n in ("premium_7", sl.MID_VARIABLE)}
    assert cp.read_once(market, tables, ["premium_7"])["read"] == cp.READ_NUMBER
    assert "H-R2" in cp.overlap(market, tables, ["premium_7"])["compared"]


def test_the_confirm_positioning_plan_covers_every_members_window(tmp_path):
    start, end = date(2024, 5, 1), cp.CONFIRM[1]
    days = [start + timedelta(days=k) for k in range((end - start).days + 1)]
    bars = {f"BIG{i:02d}USDT": {d: xsb.Bar(open=1.0, close=1.0, quote_volume=1e9 - i) for d in days} for i in range(30)}
    bars.update({f"MID{i:02d}USDT": {d: xsb.Bar(open=1.0, close=1.0, quote_volume=1e7 * (1 + ((d.month + i) % 5)))
                                     for d in days[60 * (i % 3):]} for i in range(12)})
    market = xsb.Market(bars=bars, funding={})
    shared = tmp_path / "archive-metrics"
    held_day = date(2024, 12, 1)
    _confirm_metrics_zip(shared / "metrics" / "MID00USDT" / lab.ba.daily_name("MID00USDT", "metrics", held_day), held_day, 1.0)
    plan = cp.positioning_plan(market, tmp_path)
    asked = set()
    for symbol, first, last in plan["spans"]:
        d = date.fromisoformat(first)
        while d <= date.fromisoformat(last):
            asked.add((symbol, d))
            d += timedelta(days=1)
    assert ("MID00USDT", held_day) not in asked, "a day the shared root holds is not asked again"
    tier = sl.TierSweep(market, cp.CONFIRM)
    pairs = 0
    for day in tier.days:
        for symbol in tier.members(day):
            first_bar = min(market.bars[symbol])
            for k in range(sl.LOWER_METRICS_WINDOW + 1):
                d = day - timedelta(days=k)
                if d >= max(first_bar, sl.METRICS_FIRST_DAY) and d <= end and (symbol, d) != ("MID00USDT", held_day):
                    assert (symbol, d) in asked, (symbol, day, d)
                    pairs += 1
    assert pairs and set(plan["held_symbols"]) == {s for d in tier.days for s in tier.members(d)}
    assert max(d for _, d in asked) <= end, "nothing after the confirm span"

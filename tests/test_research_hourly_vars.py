from __future__ import annotations

import io
import math
import random
import zipfile
from datetime import date, timedelta

import pytest

from scripts.research import cross_section_basket as xsb
from scripts.research import hourly_vars as hv
from scripts.research import signal_lab as lab

DAY = date(2021, 3, 10)
END = hv.window_end(DAY)


def _prices(returns, start=100.0):
    out = [start]
    for r in returns:
        out.append(out[-1] * math.exp(r))
    return out


def _series(first, opens, volumes=None):
    return hv.Series({first + i: (p, (volumes[i] if volumes else 1.0)) for i, p in enumerate(opens) if p is not None})


def _world(coin_from_btc, *, hours=900, seed=3, drop=()):
    rng = random.Random(seed)
    b = [rng.gauss(0, 0.01) for _ in range(hours)]
    first = END - hours + 1
    btc = _series(first, _prices(b))
    r = [coin_from_btc(b, t, rng) for t in range(hours)]
    coin_opens = _prices(r)
    for k in drop:
        coin_opens[k] = None
    coin = _series(first, coin_opens)
    return btc, coin, hv.Reference(btc, btc.first, END)


def _direct(coin, btc, end):
    """The same regression row by row, without prefix sums: the reference value."""
    rows = []
    for h in range(end - hv.WINDOW, end):
        y = coin.ret(h)
        xs = [btc.ret(h - k) for k in range(hv.LAGS + 1)]
        if not math.isnan(y) and not any(math.isnan(x) for x in xs):
            rows.append((y, xs))
    n = len(rows)
    my = sum(y for y, _ in rows) / n
    mx = [sum(x[k] for _, x in rows) / n for k in range(hv.LAGS + 1)]
    cyy = sum((y - my) ** 2 for y, _ in rows)
    cxy = [sum((x[k] - mx[k]) * (y - my) for y, x in rows) for k in range(hv.LAGS + 1)]
    cxx = [[sum((x[j] - mx[j]) * (x[k] - mx[k]) for _, x in rows) for k in range(hv.LAGS + 1)] for j in range(hv.LAGS + 1)]
    beta = hv._solve(cxx, cxy)
    r2_6 = sum(beta[k] * cxy[k] for k in range(hv.LAGS + 1)) / cyy
    r2_0 = cxy[0] ** 2 / (cxx[0][0] * cyy)
    return n, 1 - r2_0 / r2_6, r2_0


def test_the_price_delay_matches_a_direct_regression_and_reads_the_lag_structure():
    btc, late, ref = _world(lambda b, t, rng: 0.4 * b[t] + 0.6 * (b[t - 2] if t >= 2 else 0) + rng.gauss(0, 0.002))
    _, prompt, _ = _world(lambda b, t, rng: b[t] + rng.gauss(0, 0.002))
    d_late, r2_late = hv.delay(late, ref, END)
    _, d_direct, r2_direct = _direct(late, btc, END)
    assert abs(d_late - d_direct) < 1e-9 and abs(r2_late - r2_direct) < 1e-9
    d_prompt, _ = hv.delay(prompt, ref, END)
    assert d_late > 0.5 > 0.05 > d_prompt >= 0, "a coin that follows BTC two hours late has the larger delay"


def test_missing_coin_hours_are_taken_out_of_the_btc_sums_and_too_few_give_no_value():
    btc, coin, ref = _world(lambda b, t, rng: 0.7 * b[t] + 0.3 * (b[t - 1] if t else 0) + rng.gauss(0, 0.003),
                            drop=range(500, 540))
    n, d_direct, r2_direct = _direct(coin, btc, END)
    assert n < hv.WINDOW
    d, r2 = hv.delay(coin, ref, END)
    assert abs(d - d_direct) < 1e-9 and abs(r2 - r2_direct) < 1e-9
    _, sparse, ref2 = _world(lambda b, t, rng: b[t], drop=range(300, 500))
    assert hv.delay(sparse, ref2, END) is None, "under 648 usable hours"


def test_no_bar_after_the_daily_close_moves_a_value():
    btc, coin, ref = _world(lambda b, t, rng: 0.5 * b[t] + 0.5 * (b[t - 3] if t >= 3 else 0) + rng.gauss(0, 0.002),
                            hours=1000)
    before = hv.delay(coin, ref, END), hv.peak_share(coin, END)
    later = {h: (coin.open(h) * (3.0 if h > END else 1.0), 1e9 if h >= END else coin.volume(h))
             for h in range(coin.first, coin.first + len(coin.opens)) if not math.isnan(coin.open(h))}
    moved = hv.Series(later)
    assert (hv.delay(moved, ref, END), hv.peak_share(moved, END)) == before
    assert END == hv.hour_of(DAY + timedelta(days=1)), "the window ends when the decision day's daily bar closes"


def test_the_peak_share_is_the_largest_24_hours_of_the_window():
    volumes = [1.0] * 720
    for k in range(24):
        volumes[k * 30] = 10.0
    coin = _series(END - 720, [100.0] * 720, volumes)
    assert hv.peak_share(coin, END) == pytest.approx(240 / (240 + 696))
    thin = _series(END - 600, [100.0] * 600, [1.0] * 600)
    assert hv.peak_share(thin, END) is None, "under 648 bars"
    assert hv.peak_share(_series(END - 720, [100.0] * 720, [0.0] * 720), END) is None


def test_the_files_are_read_with_their_volume_and_cut_before_the_span_ends(tmp_path):
    folder = tmp_path / hv.HOURLY_KIND / "AUSDT"
    folder.mkdir(parents=True)
    rows = ["open_time,open,high,low,close,volume,close_time,quote_volume,count,tb,tq,ignore"]
    base = hv.hour_of(DAY)
    for h in (100, 101, 102):
        rows.append(f"{(base + h) * hv.HOUR_MS},{h}.0,0,0,0,0,0,{h * 2}.5,0,0,0,0")
    rows.append(f"{(base + 103) * hv.HOUR_MS * 1000},103.0,0,0,0,0,0,7.0,0,0,0,0")   # microseconds
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("AUSDT-1h-2021-01.csv", "\n".join(rows))
    (folder / "AUSDT-1h-2021-01.zip").write_bytes(buffer.getvalue())
    assert hv.read_symbol(folder, before=base + 103) == {base + 100: (100.0, 200.5), base + 101: (101.0, 202.5),
                                                         base + 102: (102.0, 204.5)}
    assert hv.read_symbol(folder, before=base + 104)[base + 103] == (103.0, 7.0)


def _tiers():
    months = [date(2021, m, 1) for m in (1, 2, 3)]
    return {"top30": {m: ["A", "B"] for m in months}, "rank31_100": {m: ["C", "D"] for m in months}}


def _market(values, bars=("A", "B", "C", "D"), days=()):
    market = xsb.Market()
    for s in bars:
        market.bars[s] = {d: object() for d in days}
    market.hourly_values = values
    return market


def test_coverage_counts_the_ranked_cells_and_the_coins_leaving_the_top_hundred():
    days = [date(2021, 1, 4), date(2021, 1, 11), date(2021, 2, 1)]
    tiers = _tiers()
    tiers["top30"][date(2021, 2, 1)] = ["A"]                 # B leaves the top hundred after January
    values = {"A": {d: {"price_delay_30": 0.1} for d in days}, "B": {days[0]: {"price_delay_30": 0.2}},
              "C": {d: {"price_delay_30": 0.3} for d in days}}
    out = hv.coverage(_market(values, days=days), "price_delay_30", tiers, days)
    assert out["top30"]["cells"] == 5 and out["top30"]["share"] == 0.8, "February's top tier is A alone"
    assert out["top30"]["cells_out_next_month"] == 2 and out["top30"]["share_out_next_month"] == 0.5
    assert out["rank31_100"]["share"] == 0.5, "D never has a value"
    march = [date(2021, 3, 1)]                               # the tiers' last month: April is not known
    values_m = {s: {march[0]: {"price_delay_30": 1.0}} for s in "ABCD"}
    edge = hv.coverage(_market(values_m, days=march), "price_delay_30", tiers, march)
    assert edge["top30"]["cells"] == 2
    assert edge["top30"]["cells_out_next_month"] == 0, "the span's last month: leaving is not known, so not counted"
    no_bar = hv.coverage(_market(values, bars=("A",), days=days), "price_delay_30", tiers, days)
    assert no_bar["top30"]["cells"] == 3 and no_bar["rank31_100"]["cells"] == 0, "a member without a bar is not a cell"


def test_persistence_reads_the_same_tier_members_lags_apart():
    days = [date(2021, 1, 4) + timedelta(days=7 * k) for k in range(5)]
    tiers = {"top30": {date(2021, m, 1): [f"S{i}" for i in range(10)] for m in (1, 2)},
             "rank31_100": {date(2021, m, 1): [] for m in (1, 2)}}
    values = {f"S{i}": {d: {"volume_peak_share_30": i + k * 0.01} for k, d in enumerate(days)} for i in range(10)}
    out = hv.persistence(_market(values, bars=(), days=()), "volume_peak_share_30", tiers, days, lab.rank_correlation)
    assert out["top30"]["7d"] == {"decisions": 4, "mean_rank_correlation": 1.0}
    assert out["top30"]["28d"]["decisions"] == 1 and out["rank31_100"]["7d"]["mean_rank_correlation"] is None


def test_the_hourly_candidates_are_ranked_beside_every_struck_variable():
    struck = {n for (t, n) in lab.SPREAD_NO_BOOK if t == "*"}
    assert len(struck) == 9
    for name in lab.HOURLY_CANDIDATES:
        group, _, kin = lab.OVERLAP_CANDIDATES[name]
        assert group == "hourly" and struck <= set(kin)
        assert all(k in lab.variables() or k in lab.OVERLAP_EXTRA_KIN for k in kin)
    assert "r2_btc_30" in lab.OVERLAP_CANDIDATES["price_delay_30"][2]
    assert not set(lab.HOURLY_CANDIDATES) & set(lab.variables()), "not registered before the doors"


def test_the_hourly_doors_command_blocks_without_a_market(monkeypatch, tmp_path):
    monkeypatch.setattr(lab.xsb, "_in_sandbox", lambda: True)
    monkeypatch.setenv("RESEARCH_CACHE", str(tmp_path))
    assert lab.main(["hourly-doors"]) == lab.EXIT_BLOCKED

from __future__ import annotations

import json
import math
import urllib.parse
from datetime import date, datetime, timedelta, timezone

import pytest

from scripts.research import fetch_deribit as fd
from scripts.research import iv_rv_observe as ivrv

DAY = fd.DAY_MS
NOW = int(datetime(2026, 11, 10, 3, 30, tzinfo=timezone.utc).timestamp() * 1000)   # 03:30 UTC, outside 08-09
TODAY = NOW - NOW % DAY


class Clock:
    def __init__(self, t: float):
        self.t = t
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.slept.append(s)
        self.t += s


class Venue:
    """Deribit's DVOL endpoint over ``days`` daily bars (newest last), ``page`` rows per answer, newest first."""

    def __init__(self, first: int, last: int, page: int = 3, status: int = 200, error: bool = False,
                 honours_end: bool = True):
        self.bars = [[t, 1.0, 2.0, 0.5, 1.5] for t in range(first, last + 1, DAY)]
        self.page, self.status, self.error, self.honours_end = page, status, error, honours_end
        self.calls: list[dict[str, str]] = []

    def __call__(self, url: str):
        assert url.startswith(fd.BASE + "get_volatility_index_data?")
        q = dict(urllib.parse.parse_qsl(url.split("?", 1)[1]))
        self.calls.append(q)
        if self.status != 200:
            return self.status, {}, ""
        if self.error:
            return 200, {}, json.dumps({"error": {"code": 1, "message": "x"}})
        end = int(q["end_timestamp"]) if self.honours_end else self.bars[-1][0]
        inside = [b for b in self.bars if int(q["start_timestamp"]) <= b[0] <= end]
        chunk = inside[-self.page:]
        cont = chunk[0][0] - DAY if len(inside) > self.page else None
        return 200, {}, json.dumps({"result": {"data": chunk, "continuation": cont}})


def _pace(venue, t=NOW / 1000):
    clock = Clock(t)
    return fd.Pace(fetch=venue, clock=clock, sleep=clock.sleep), clock


def _times(path):
    return [json.loads(line)[0] for line in path.read_text().splitlines()]


def test_it_pages_back_to_the_start_and_writes_closed_days_oldest_first(tmp_path):
    first = fd._ms(fd.FIRST_DAY) + 23 * DAY
    venue = Venue(first, TODAY, page=4)                     # the venue also returns today's open bar
    pace, clock = _pace(venue)
    path = fd.dvol_path(tmp_path, "BTC")
    n = fd.fill_dvol(pace, path, currency="BTC", now_ms=NOW)
    times = _times(path)
    assert times == sorted(times) and len(set(times)) == len(times)
    assert times[0] == first and times[-1] == TODAY - DAY   # today's bar is not written
    assert n == len(times) == (TODAY - first) // DAY
    assert all(s >= 1.0 - 1e-9 for s in clock.slept) and len(clock.slept) == pace.requests - 1


def test_todays_open_bar_is_dropped_even_when_the_venue_sends_it(tmp_path):
    venue = Venue(TODAY - 5 * DAY, TODAY, page=10, honours_end=False)
    path = fd.dvol_path(tmp_path, "BTC")
    fd.fill_dvol(_pace(venue)[0], path, currency="BTC", now_ms=NOW)
    assert _times(path)[-1] == TODAY - DAY


def test_a_later_run_asks_only_for_days_after_the_newest_cached_bar(tmp_path):
    venue = Venue(TODAY - 10 * DAY, TODAY)
    path = fd.dvol_path(tmp_path, "ETH")
    fd.fill_dvol(_pace(venue)[0], path, currency="ETH", now_ms=NOW - 3 * DAY)
    before = _times(path)
    venue.calls.clear()
    assert fd.fill_dvol(_pace(venue)[0], path, currency="ETH", now_ms=NOW) == 3
    assert int(venue.calls[0]["start_timestamp"]) == before[-1] + DAY
    assert _times(path) == before + [TODAY - 3 * DAY, TODAY - 2 * DAY, TODAY - DAY]
    venue.calls.clear()
    assert fd.fill_dvol(_pace(venue)[0], path, currency="ETH", now_ms=NOW) == 0
    assert venue.calls == []                                  # nothing new closed: no request at all


@pytest.mark.parametrize("venue", [Venue(TODAY - 5 * DAY, TODAY, status=429), Venue(TODAY - 5 * DAY, TODAY, error=True)])
def test_a_refusal_or_an_error_stops_the_run_without_retry(tmp_path, venue):
    pace, _ = _pace(venue)
    with pytest.raises(fd.Stop):
        fd.fill_dvol(pace, fd.dvol_path(tmp_path, "BTC"), currency="BTC", now_ms=NOW)
    assert pace.requests == 1


def test_it_never_asks_inside_the_factory_window(tmp_path):
    venue = Venue(TODAY - 5 * DAY, TODAY)
    pace, _ = _pace(venue, t=(TODAY + 8.5 * 3_600_000) / 1000)
    with pytest.raises(fd.Stop):
        fd.fill_dvol(pace, fd.dvol_path(tmp_path, "BTC"), currency="BTC", now_ms=TODAY + int(8.5 * 3_600_000))
    assert venue.calls == []


def test_a_row_of_another_shape_stops_the_run(tmp_path):
    venue = Venue(TODAY - 5 * DAY, TODAY)
    venue.bars[-2] = venue.bars[-2][:4]
    with pytest.raises(fd.Stop):
        fd.fill_dvol(_pace(venue)[0], fd.dvol_path(tmp_path, "BTC"), currency="BTC", now_ms=NOW)


def test_the_request_carries_no_key_and_goes_to_the_public_host():
    seen = []
    pace = fd.Pace(fetch=lambda url: (seen.append(url), (200, {}, json.dumps({"result": {"data": []}})))[1],
                   clock=Clock(NOW / 1000), sleep=lambda s: None)
    pace.get("get_volatility_index_data", {"currency": "BTC"})
    assert seen[0].startswith("https://www.deribit.com/api/v2/public/")
    assert not any(k in seen[0].lower() for k in ("key", "secret", "token", "signature", "auth"))


def test_main_refuses_a_currency_it_does_not_read(capsys):
    assert fd.main(["--currency", "SOL"]) == 2
    assert "refused" in capsys.readouterr().err


# --- the observation -----------------------------------------------------------------------------------------

D0 = date(2026, 11, 10)


def _closes(start: date, days: int, step: float = 0.01) -> dict[date, float]:
    return {start + timedelta(days=k): 100 * math.exp(step * k) for k in range(days)}


def test_realized_vol_is_annualized_zero_mean_close_to_close_over_thirty_days():
    closes = _closes(D0, 31, step=0.02)
    assert ivrv.realized_vol(closes, D0) == pytest.approx(100 * math.sqrt(365 / 30 * 30 * 0.02 ** 2))
    assert ivrv.realized_vol(_closes(D0, 30), D0) is None        # one close short: pending


def test_only_days_from_the_start_are_valued(monkeypatch):
    asked: list[date] = []
    real = ivrv.realized_vol
    monkeypatch.setattr(ivrv, "realized_vol", lambda closes, day: (asked.append(day), real(closes, day))[1])
    dvol = {D0 + timedelta(days=k): 50.0 + k for k in range(-40, 5)}
    out = ivrv.observe(dvol, _closes(D0 - timedelta(days=60), 100), start=D0)
    assert asked and min(asked) == D0
    assert [r["day"] for r in out["observed"]] == [(D0 + timedelta(days=k)).isoformat() for k in range(5)]
    assert out["before_start"] == {"days": 40, "first": (D0 - timedelta(days=40)).isoformat(),
                                   "last": (D0 - timedelta(days=1)).isoformat()}
    text = json.dumps(out)
    assert "10.0" not in text and "11.0" not in text             # pre-start DVOL closes (50-40, 50-39) never shown


def test_a_day_without_thirty_forward_closes_is_pending_and_the_gap_is_iv_minus_rv():
    index = _closes(D0, 33, step=0.01)
    dvol = {D0 + timedelta(days=k): 40.0 for k in range(5)}
    out = ivrv.observe(dvol, index, start=D0)
    assert out["observed_days"] == 3 and len(out["pending_days"]) == 2
    rv = 100 * math.sqrt(365 / 30 * 30 * 0.01 ** 2)
    assert out["observed"][0]["iv_minus_rv"] == pytest.approx(round(40.0 - rv, 2))
    assert out["mean_iv_minus_rv"] == pytest.approx(round(40.0 - rv, 2))


def test_the_readers_take_the_close_of_each_cached_bar(tmp_path):
    t0 = fd._ms(D0)
    path = fd.dvol_path(tmp_path, "BTC")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps([t0, 1.0, 2.0, 0.5, 1.5]) + "\n")
    ipath = ivrv.lake.derivative_path(tmp_path, "index", "BTCUSDT", "1d")
    ipath.parent.mkdir(parents=True)
    ipath.write_text(json.dumps([t0, "1", "2", "0.5", "1.75", "0"]) + "\n")
    assert ivrv.read_dvol(tmp_path, "BTC") == {D0: 1.5}
    assert ivrv.read_index(tmp_path, "BTCUSDT") == {D0: 1.75}


def test_it_refuses_outside_the_sandbox(monkeypatch, capsys):
    monkeypatch.setattr(ivrv.xsb, "_in_sandbox", lambda: False)
    assert ivrv.main([]) == ivrv.EXIT_USAGE
    assert "sandbox" in capsys.readouterr().err

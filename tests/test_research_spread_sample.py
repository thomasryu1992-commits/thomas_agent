"""The spread observation (SPREAD_OBSERVATION_V0.1, Thomas 2026-11-12): one request a slot, nothing written on a
refusal or an answer it cannot describe, closed days compressed, and a coverage report that names no price.
Synthetic answers only; the network is never touched."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK, EXIT_USAGE
from scripts.research import fetch_public
from scripts.research import spread_sample as ss

DAY = 1_794_441_600                       # 2026-11-12T00:00:00Z
NOON = DAY + 12 * 3600 + 7 * 60           # 12:07 UTC, a cron minute outside the factory window


def _answer(n: int = 3) -> list[dict]:
    # the shape the venue answered with on 2026-11-12 (keys and types taken from the real call)
    return [{"symbol": f"C{i:02d}USDT", "bidPrice": "100.00", "bidQty": "5.5", "askPrice": "100.10", "askQty": "2.0",
             "time": (NOON - 30 * i) * 1000, "lastUpdateId": 12085520781265 + i} for i in range(n)]


class Venue:
    def __init__(self, answer=None, *, status: int = 200, used: int = 5):
        self.answer, self.status, self.used, self.urls = _answer() if answer is None else answer, status, used, []

    def __call__(self, url: str):
        self.urls.append(url)
        body = json.dumps(self.answer) if self.status == 200 else ""
        return self.status, {"X-MBX-USED-WEIGHT-1M": str(self.used)}, body


class Clock:
    def __init__(self, t: float):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        raise AssertionError("a sample never waits")


def _budget(venue, at: float = NOON):
    clock = Clock(at)
    return fetch_public.Budget(fetch=venue, clock=clock, sleep=clock.sleep), clock


def test_one_sample_is_one_request_for_the_whole_venue_and_one_line(tmp_path):
    venue = Venue()
    budget, _ = _budget(venue)
    result = ss.sample(budget, tmp_path)
    assert result == {"written": True, "slot": NOON // 900, "symbols": 3, "used_weight_1m": 5}
    assert venue.urls == ["https://fapi.binance.com/fapi/v1/ticker/bookTicker?"] and budget.requests == 1
    assert list(budget.spent) == [(NOON, 5)] and ss.WEIGHT == 5          # the weight counted is the venue's
    (line,) = ss.read_day(ss.day_path(tmp_path, "2026-11-12"))
    assert (line["t"], line["slot"], line["n"], line["used_weight_1m"]) == ("2026-11-12T12:07:00Z", NOON // 900, 3, 5)
    assert line["rows"][0] == ["C00USDT", "100.00", "5.5", "100.10", "2.0", NOON * 1000]   # the venue's own strings
    assert all(len(row) == 6 for row in line["rows"])                    # lastUpdateId and anything new is not kept


def test_a_slot_already_held_makes_no_request(tmp_path):
    venue = Venue()
    budget, clock = _budget(venue)
    ss.sample(budget, tmp_path)
    clock.t = NOON + 400                                                  # same 15-minute slot (12:00 – 12:15)
    assert ss.sample(budget, tmp_path) == {"written": False, "reason": "slot already held", "slot": NOON // 900}
    assert len(venue.urls) == 1
    clock.t = NOON + 900                                                  # the next slot
    assert ss.sample(budget, tmp_path)["written"] is True
    assert len(venue.urls) == 2 and len(ss.read_day(ss.day_path(tmp_path, "2026-11-12"))) == 2


@pytest.mark.parametrize("venue, at, said, requests", [
    (Venue(), DAY + 8 * 3600 + 22 * 60, "factory window", 0),             # 08:22 UTC: refused before any request
    (Venue(status=429), NOON, "429", 1),                                  # never retried
    (Venue(status=418), NOON, "418", 1),
    (Venue(status=503), NOON, "503", 1),
    (Venue(used=1200), NOON, "1200", 1),                                  # the IP is at half its minute: dropped
    (Venue(answer={"code": -1}), NOON, "non-empty list", 1),
    (Venue(answer=[]), NOON, "non-empty list", 1),
    (Venue(answer=[{"symbol": "AUSDT", "bidPrice": "1", "bidQty": "1", "askPrice": "2", "time": 1}]), NOON, "lacked", 1),
    (Venue(answer=_answer(2) + [{**_answer(1)[0], "askPrice": "n/a"}]), NOON, "non-number", 1),
    (Venue(answer=[{**_answer(1)[0], "symbol": ""}]), NOON, "no symbol", 1),
])
def test_a_refusal_or_an_answer_it_cannot_describe_writes_nothing(tmp_path, venue, at, said, requests):
    budget, _ = _budget(venue, at)
    with pytest.raises(ss.Stop) as stopped:
        ss.sample(budget, tmp_path)
    assert said in str(stopped.value)
    assert len(venue.urls) == requests == budget.requests                 # at most one request on every path
    assert not ss.spread_dir(tmp_path).exists() or not list(ss.spread_dir(tmp_path).iterdir())
    assert budget.fetch is venue                                          # the recorder never outlives the call


def test_closed_days_are_compressed_only_when_the_copy_reads_back(tmp_path):
    budget, clock = _budget(Venue(), NOON - 86400)                        # yesterday
    ss.sample(budget, tmp_path)
    clock.t = NOON
    ss.sample(budget, tmp_path)
    yesterday, today = ss.day_path(tmp_path, "2026-11-11"), ss.day_path(tmp_path, "2026-11-12")
    before = ss.read_day(yesterday)
    assert ss.compress_closed(tmp_path, "2026-11-12") == ["2026-11-11"]
    packed = yesterday.with_suffix(".jsonl.gz")
    assert not yesterday.exists() and today.exists() and ss.read_day(packed) == before
    assert ss.compress_closed(tmp_path, "2026-11-12") == []               # nothing left to do, nothing rewritten
    # a plain file beside a copy that differs is kept, never deleted on trust
    yesterday.write_text('{"t":"x","slot":1,"rows":[]}\n', encoding="utf-8")
    assert ss.compress_closed(tmp_path, "2026-11-12") == [] and yesterday.exists()
    assert ss.read_day(packed) == before
    assert gzip.decompress(packed.read_bytes()).count(b"\n") == 1


def test_the_schedule_is_ninety_two_slots_and_the_cron_line_is_that_schedule():
    assert ss.SAMPLES_PER_DAY == 92 and 8 not in ss.CRON_HOURS and len(ss.CRON_HOURS) == 23
    assert len({(h * 60 + m) // 15 for h in ss.CRON_HOURS for m in ss.CRON_MINUTES}) == 92   # one fire per slot
    line = ss.cron_line(Path("/code"), Path("/logs/sample.log"))
    minutes, hours, *rest = line.split(" ")
    assert minutes == "7,22,37,52" and hours.split(",") == [str(h) for h in ss.CRON_HOURS] and rest[:3] == ["*", "*", "*"]
    assert "cd /code && /usr/bin/flock -n /logs/sample.lock /usr/bin/python3 -m scripts.research.spread_sample sample" in line
    assert line.endswith(">> /logs/sample.log 2>&1")


def test_coverage_names_days_and_counts_and_no_price(tmp_path):
    budget, clock = _budget(Venue(_answer(4)), NOON - 86400)
    for step in range(3):
        clock.t = NOON - 86400 + step * 900
        ss.sample(budget, tmp_path)
    clock.t = NOON
    ss.sample(budget, tmp_path)
    ss.compress_closed(tmp_path, "2026-11-12")
    report = ss.coverage(tmp_path)
    assert report["days"] == {"2026-11-11": {"samples": 3, "of": 92, "symbols_median": 4},
                              "2026-11-12": {"samples": 1, "of": 92, "symbols_median": 4}}
    assert report["days_held"] == 2 and report["days_short_of_full"] == ["2026-11-11", "2026-11-12"]
    flat = json.dumps(report)
    assert "100.00" not in flat and "100.10" not in flat and "C00USDT" not in flat
    # a line that does not parse is skipped, not raised on
    with ss.day_path(tmp_path, "2026-11-12").open("a", encoding="utf-8") as fh:
        fh.write("{not json\n")
    assert ss.coverage(tmp_path)["days"]["2026-11-12"]["samples"] == 1


def test_the_spread_is_in_basis_points_of_the_mid_and_an_empty_or_crossed_quote_has_none():
    assert ss.spread_bps("100.00", "100.10") == pytest.approx(0.10 / 100.05 * 10_000)
    assert ss.spread_bps("0", "100.10") is None and ss.spread_bps("100.10", "100.00") is None
    assert ss.spread_bps("100", "100") is None and ss.spread_bps("x", "1") is None


def test_sample_runs_on_the_host_only_and_reports_a_stop_as_blocked(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(ss.xsb, "_in_sandbox", lambda: True)
    assert ss.main(["sample", "--cache", str(tmp_path)]) == EXIT_USAGE
    assert "host" in capsys.readouterr().err
    monkeypatch.setattr(ss.xsb, "_in_sandbox", lambda: False)
    venue = Venue(status=429)
    refused = _budget(venue)[0]
    monkeypatch.setattr(ss.fetch_public, "Budget", lambda: refused)
    assert ss.main(["sample", "--cache", str(tmp_path)]) == EXIT_BLOCKED
    assert "nothing written" in capsys.readouterr().err and len(venue.urls) == 1
    good = Venue()
    monkeypatch.undo()
    answered = _budget(good)[0]
    monkeypatch.setattr(ss.xsb, "_in_sandbox", lambda: False)
    monkeypatch.setattr(ss.fetch_public, "Budget", lambda: answered)
    assert ss.main(["sample", "--cache", str(tmp_path)]) == EXIT_OK
    assert '"written":true' in capsys.readouterr().out and len(good.urls) == 1
    assert ss.main(["coverage", "--cache", str(tmp_path)]) == EXIT_OK
    assert json.loads(capsys.readouterr().out)["days_held"] == 1
    assert ss.main(["coverage", "--brief", "--cache", str(tmp_path)]) == EXIT_OK
    assert capsys.readouterr().out.strip() == "spread coverage: 1 day(s) held, 1 short of 92: 2026-11-12(1)"
    assert ss.brief({"days": {}, "days_held": 0, "days_short_of_full": []}) == "spread coverage: 0 day(s) held, 0 short of 92"

"""The funding-basis monthly refresh (FUNDING_BASIS_V0.2 §9): the targets, the order of the existing public reads,
pacing, the weight-stop retry, the 429/418 stop, the quiet window. A fake runner stands in for every network call."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from scripts.research import funding_basis as fb
from scripts.research import funding_basis_refresh as rf
from scripts.research import funding_basis_v02 as v

MONTH = "2026-11"
NOON = datetime(2026, 12, 2, 12, 0, tzinfo=timezone.utc)


def _volumes(n: int = 40) -> dict[str, dict[str, float]]:
    return {"2026-10": {f"P{i:02d}USDT": float(100 - i) for i in range(n)},
            "2026-11": {f"P{i:02d}USDT": float(i) for i in range(n)}}


@pytest.fixture
def cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    archive, spot = tmp_path / "archive", tmp_path / "archive-spot"
    archive.mkdir()
    spot.mkdir()
    perps = ["P00USDT", "1000PEPEUSDT", "GOLDUSDT", "NOSPOTUSDT"]
    (archive / "symbols.json").write_text(json.dumps(perps))
    (archive / "contract_types.json").write_text(json.dumps({"contract_types": {"GOLDUSDT": v.TRADIFI}}))
    (spot / "symbols.json").write_text(json.dumps(["P00USDT", "PEPEUSDT", "GOLDUSDT"]))
    monkeypatch.setattr(v, "read_volumes", lambda *a, **k: (_volumes(), {}))
    return tmp_path


class Fake:
    def __init__(self, answers: dict[str, list[tuple[int, str]]] | None = None) -> None:
        self.calls: list[list[str]] = []
        self.answers = answers or {}

    def __call__(self, argv: list[str]) -> tuple[int, str]:
        self.calls.append(argv)
        if "scripts.research.fetch_public" in argv:
            queue = self.answers.get(argv[argv.index("--symbol") + 1])
            if queue:
                return queue.pop(0)
            return 0, "done: 3 request(s), 90 row(s) written"
        return 0, "ok"


def _job(cache: Path, fake: Fake, *, now: datetime = NOON) -> tuple[rf.Refresh, list[float]]:
    slept: list[float] = []
    return rf.Refresh(month=MONTH, cache=cache, run=fake, now=lambda: now, sleep=slept.append,
                      say=lambda _: None), slept


def test_month_bounds_and_an_unfinished_month_is_refused() -> None:
    assert rf.month_bounds("2026-12") == (date(2026, 12, 1), date(2026, 12, 31))
    assert rf.month_bounds("2027-02") == (date(2027, 2, 1), date(2027, 2, 28))
    assert rf.main(["--month", "2026-11"], today=date(2026, 11, 30)) == rf.EXIT_USAGE


def test_targets_are_this_and_last_months_top_by_volume_and_v01s_symbols() -> None:
    got = rf.targets(_volumes(), MONTH)
    top_nov = {f"P{i:02d}USDT" for i in range(10, 40)}               # volume rises with i in November
    top_oct = {f"P{i:02d}USDT" for i in range(0, 30)}
    assert set(got) == top_nov | top_oct | set(fb.SYMBOLS)


def test_spot_pairs_map_the_prefix_and_leave_out_tradfi_and_pairless_perps() -> None:
    perps = ["P00USDT", "1000PEPEUSDT", "GOLDUSDT", "NOSPOTUSDT"]
    assert rf.spot_pairs(perps, {"GOLDUSDT": v.TRADIFI}, {"P00USDT", "PEPEUSDT", "GOLDUSDT"}) == \
        ["P00USDT", "PEPEUSDT"]


def test_the_reads_run_in_order_paced_one_symbol_at_a_time(cache: Path) -> None:
    fake = Fake()
    job, slept = _job(cache, fake)
    assert job.execute() == rf.EXIT_OK
    heads = [c[1] if c[0] != "-m" else "fetch_public" for c in fake.calls]
    assert heads[:6] == ["list-symbols", "list-symbols", "contract-types", "fetch-monthly", "fetch-monthly",
                         "fetch-monthly"]
    perp_klines, spot_klines, funding = fake.calls[3], fake.calls[4], fake.calls[5]
    assert "--symbols" not in perp_klines                            # every perp ranks, so every perp is fetched
    assert spot_klines[spot_klines.index("--symbols") + 1] == "P00USDT,PEPEUSDT"
    wanted = rf.targets(_volumes(), MONTH)
    assert funding[funding.index("--symbols") + 1] == ",".join(wanted)
    assert [c[c.index("--symbol") + 1] for c in fake.calls[6:]] == wanted
    assert slept == [rf.PACE_SECONDS] * (len(wanted) - 1)


def test_a_weight_stop_waits_and_retries(cache: Path) -> None:
    stop = (3, "stopped after 4 request(s): the IP has used 1300 weight this minute; stopping at 1200")
    fake = Fake({"P10USDT": [stop]})
    job, slept = _job(cache, fake)
    assert job.execute() == rf.EXIT_OK
    assert rf.WEIGHT_WAIT_SECONDS in slept and job.failed == []


def test_a_symbol_that_keeps_failing_is_listed_and_the_run_goes_on(cache: Path) -> None:
    stop = (3, "stopped after 4 request(s): the IP has used 1300 weight this minute; stopping at 1200")
    fake = Fake({"P10USDT": [stop] * 3, "P11USDT": [(3, "stopped after 1 request(s): venue answered 400 for x")]})
    job, _ = _job(cache, fake)
    assert job.execute() == rf.EXIT_BLOCKED
    assert job.failed == ["P10USDT", "P11USDT"]
    assert fake.calls[-1][fake.calls[-1].index("--symbol") + 1] == rf.targets(_volumes(), MONTH)[-1]


def test_a_429_or_418_stops_the_whole_run(cache: Path) -> None:
    fake = Fake({"P10USDT": [(3, "stopped after 1 request(s): venue answered 418; stopping without retry")]})
    job, _ = _job(cache, fake)
    with pytest.raises(rf.Halt):
        job.execute()
    assert fake.calls[-1][fake.calls[-1].index("--symbol") + 1] == "P10USDT"


def test_nothing_starts_inside_the_quiet_window(cache: Path) -> None:
    fake = Fake()
    job, _ = _job(cache, fake, now=datetime(2026, 12, 2, 8, 15, tzinfo=timezone.utc))
    with pytest.raises(rf.Halt):
        job.execute()
    assert fake.calls == []


def test_too_few_perps_in_the_months_klines_halts_before_any_derivative_read(cache: Path,
                                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(v, "read_volumes", lambda *a, **k: (_volumes(n=12), {}))
    fake = Fake()
    job, _ = _job(cache, fake)
    with pytest.raises(rf.Halt):
        job.execute()
    assert not any("scripts.research.fetch_public" in c for c in fake.calls)


class Flaky(Fake):
    def __init__(self, fails: int) -> None:
        super().__init__()
        self.fails = fails

    def __call__(self, argv: list[str]) -> tuple[int, str]:
        if "spot" in argv and "fetch-monthly" in argv and self.fails:
            self.calls.append(argv)
            self.fails -= 1
            return 1, "urllib.error.URLError: <urlopen error _ssl.c:1063: The handshake operation timed out>"
        return super().__call__(argv)


def test_an_archive_step_that_times_out_is_retried(cache: Path) -> None:
    job, slept = _job(cache, Flaky(fails=1))
    assert job.execute() == rf.EXIT_OK
    assert slept[0] == rf.ARCHIVE_WAIT_SECONDS


def test_an_archive_step_that_keeps_failing_halts_with_a_resume_note(cache: Path) -> None:
    fake = Flaky(fails=rf.ARCHIVE_RETRIES + 1)
    job, _ = _job(cache, fake)
    with pytest.raises(rf.Halt, match="rerun"):
        job.execute()
    assert not any("scripts.research.fetch_public" in c for c in fake.calls)


def test_an_archive_hole_in_the_month_is_reported(cache: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import timedelta

    first, last = date(2026, 10, 1), date(2026, 11, 30)
    hole = date(2026, 11, 12)
    every = {first + timedelta(days=i) for i in range((last - first).days + 1)}
    monkeypatch.setattr(v, "read_volumes", lambda *a, **k: (_volumes(), {f"P{i:02d}USDT": every - {hole}
                                                                         for i in range(40)}))
    said: list[str] = []
    job = rf.Refresh(month=MONTH, cache=cache, run=Fake(), now=lambda: NOON, sleep=lambda _: None, say=said.append)
    assert job.execute() == rf.EXIT_OK                                     # a warning, not a halt
    assert job.holes == [hole]
    assert any(line.startswith("WARNING archive holes in 2026-11: 2026-11-12") for line in said)

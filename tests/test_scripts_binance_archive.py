"""Step 1 of the archive backfill (Thomas 2026-10-03, R1 and R3): fetch and measure outside the runtime.

Pinned: the script reaches nothing in ``runtime`` and writes nothing under the state directory; a file is
kept only when it matches the archive's own sha256; a day the archive does not serve is remembered, not
re-asked; and the hourly OI is relabelled to the hour it closes, which is how ``oi_store`` labels it, so
a feature built on it cannot read an hour ahead. No test opens a socket: the opener is injected.
"""

from __future__ import annotations

import ast
import hashlib
import io
import json
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts import binance_archive as ba

HEADER = ("create_time,symbol,sum_open_interest,sum_open_interest_value,count_toptrader_long_short_ratio,"
          "sum_toptrader_long_short_ratio,count_long_short_ratio,sum_taker_long_short_vol_ratio")


def _csv(day: date, values) -> str:
    lines = [HEADER]
    start = datetime(day.year, day.month, day.day)
    for i, value in enumerate(values):
        moment = start + timedelta(minutes=5 * i)
        lines.append(f"{moment:%Y-%m-%d %H:%M:%S},BTCUSDT,{value},1,1,1,1,1")
    return "\n".join(lines) + "\n"


def _zip(name: str, text: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name.replace(".zip", ".csv"), text)
    return buffer.getvalue()


class _Archive:
    """An in-memory archive: name -> zip bytes. Anything else is a 404."""

    def __init__(self, files, *, bad_checksum=()):
        self.files = files
        self.bad = set(bad_checksum)
        self.asked: list[str] = []

    def __call__(self, url):
        self.asked.append(url)
        name = url.rsplit("/", 1)[-1]
        if name.endswith(".CHECKSUM"):
            base = name[: -len(".CHECKSUM")]
            if base not in self.files:
                return None
            digest = "0" * 64 if base in self.bad else hashlib.sha256(self.files[base]).hexdigest()
            return f"{digest}  {base}\n".encode()
        return self.files.get(name)


def test_the_script_imports_nothing_from_the_runtime():
    """R3: the archive is not a runtime egress. The script must stay runnable on the host, outside it."""
    tree = ast.parse(Path(ba.__file__).read_text(encoding="utf-8"))
    imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    imported |= {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert not any(name.startswith("runtime") for name in imported), imported
    assert "hmac" not in imported


def test_a_verified_file_is_kept_and_a_second_fetch_asks_nothing(tmp_path):
    day = date(2025, 1, 15)
    name = ba.daily_name("BTCUSDT", ba.METRICS, day)
    archive = _Archive({name: _zip(name, _csv(day, [1.0]))})
    counts = ba.fetch(tmp_path, symbols=["BTCUSDT"], start=day, end=day, opener=archive, pause=0)
    assert counts["BTCUSDT"] == {"kept": 1, "present": 0, "missing": 0}
    assert (tmp_path / ba.METRICS / "BTCUSDT" / name).exists()
    archive.asked.clear()
    again = ba.fetch(tmp_path, symbols=["BTCUSDT"], start=day, end=day, opener=archive, pause=0)
    assert again["BTCUSDT"]["present"] == 1 and archive.asked == []


def test_a_checksum_mismatch_keeps_nothing(tmp_path):
    day = date(2025, 1, 15)
    name = ba.daily_name("BTCUSDT", ba.METRICS, day)
    archive = _Archive({name: _zip(name, _csv(day, [1.0]))}, bad_checksum={name})
    with pytest.raises(ba.ArchiveError):
        ba.fetch(tmp_path, symbols=["BTCUSDT"], start=day, end=day, opener=archive, pause=0)
    assert not list(tmp_path.rglob("*.zip")) and not list(tmp_path.rglob("*.part"))


def test_a_day_the_archive_does_not_serve_is_remembered_not_reasked(tmp_path):
    day = date(2020, 8, 1)
    archive = _Archive({})
    counts = ba.fetch(tmp_path, symbols=["BTCUSDT"], start=day, end=day, opener=archive, pause=0)
    assert counts["BTCUSDT"]["missing"] == 1
    missing = json.loads((tmp_path / ba.MISSING_FILENAME).read_text())
    assert missing == {"BTCUSDT": [ba.daily_name("BTCUSDT", ba.METRICS, day)]}
    archive.asked.clear()
    ba.fetch(tmp_path, symbols=["BTCUSDT"], start=day, end=day, opener=archive, pause=0)
    assert archive.asked == []


def test_funding_files_are_monthly(tmp_path):
    name = ba.monthly_name("BTCUSDT", ba.FUNDING, 2024, 1)
    archive = _Archive({name: _zip(name, "calc_time,funding_interval_hours,last_funding_rate\n")})
    ba.fetch(tmp_path, symbols=["BTCUSDT"], start=date(2024, 1, 10), end=date(2024, 1, 20),
             funding=True, opener=archive, pause=0)
    assert (tmp_path / ba.FUNDING / "BTCUSDT" / name).exists()


def test_the_hourly_reading_is_labelled_with_the_hour_it_closes():
    """The archive's 01:00 reading closes the 00:00 hour; `oi_store` labels that hour 00:00."""
    day = date(2025, 1, 15)
    rows = list(ba.csv.DictReader(io.StringIO(_csv(day, [10.0 + i for i in range(25)]))))
    hourly = ba.hourly_open_interest(rows)
    assert hourly["2025-01-15T00:00:00Z"] == 22.0     # the 01:00 row (index 12)
    assert hourly["2025-01-14T23:00:00Z"] == 10.0     # the 00:00 row closes the previous day's last hour
    assert all(label.endswith(":00:00Z") for label in hourly)


def test_coverage_counts_the_gaps_and_the_replay_depths():
    held = [date(2021, 12, 1) + timedelta(days=i) for i in range(1100) if i != 5]
    cov = ba.coverage(held)
    assert cov["first"] == "2021-12-01" and cov["span_days"] == 1100 and cov["held_days"] == 1099
    assert cov["missing_days"] == ["2021-12-06"]
    assert cov["covers_below_1d"] is True and cov["share_of_1d_replay"] == 0.55


def test_compare_finds_the_alignment_the_shift_already_applies():
    """A store series that is the archive series under the shift reads best at extra offset 0."""
    base = datetime(2026, 9, 20, tzinfo=timezone.utc)
    values = [100.0 + ((i * 37) % 11) for i in range(48)]
    archive = {ba._iso(base + timedelta(hours=i)): v for i, v in enumerate(values)}
    store = dict(archive)
    report = ba.compare(store, archive)
    assert report[0]["change_correlation"] == 1.0 and report[0]["median_level_gap"] == 0.0
    assert (report[1]["change_correlation"] or 0) < 0.9 and (report[-1]["change_correlation"] or 0) < 0.9


def test_measure_reads_the_store_without_writing_it(tmp_path):
    day = date(2026, 9, 20)
    name = ba.daily_name("BTCUSDT", ba.METRICS, day)
    target = tmp_path / "dest" / ba.METRICS / "BTCUSDT" / name
    target.parent.mkdir(parents=True)
    target.write_bytes(_zip(name, _csv(day, [100.0 + i for i in range(288)])))
    store = tmp_path / "open_interest_1h.jsonl"
    rows = [{"symbol": "BTCUSDT", "timestamp": f"2026-09-20T{h:02d}:00:00Z", "open_interest": 100.0 + 12 * (h + 1)}
            for h in range(23)]
    store.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    before = store.read_bytes()
    report = ba.measure(tmp_path / "dest", symbols=["BTCUSDT"], oi_store=store)
    assert store.read_bytes() == before and not list(tmp_path.glob("*.lock"))
    aligned = report["symbols"]["BTCUSDT"]["oi_alignment"]["0"]
    assert aligned["hours"] == 23 and aligned["median_level_gap"] == 0.0

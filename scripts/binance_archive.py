#!/usr/bin/env python3
"""Binance's public futures archive, fetched and measured outside the runtime (step 1 of
``docs/proposals/CRYPTO_ARCHIVE_BACKFILL_V0.1.md``, Thomas 2026-10-03, R1 and R3).

**Why outside the runtime.** R3 decided that the archive is fetched by an operator on the host and that
the runtime reads only files handed to it. ``data.binance.vision`` is therefore not a runtime egress:
this script imports nothing from ``runtime``, opens no gated capability, signs nothing and writes
nothing under ``.runtime_governance_state/``. It reads the OI store file read-only for the comparison.

**What it does.**

- ``fetch`` downloads the daily ``metrics`` files (5-minute open interest and the three long/short
  ratios) and, with ``--funding``, the monthly ``fundingRate`` files, into ``--dest``. Every zip is
  checked against the archive's own ``.CHECKSUM`` (sha256) before it is kept. A day the archive does
  not serve is recorded in ``missing.json``, not retried forever. An existing verified file is
  skipped, so a fetch resumes where it stopped.
- ``export-oi`` writes the fetched hourly OI as ``oi_store``-shaped rows (JSONL on stdout), already
  relabelled to the hour's start, for ``scripts/import_archive_oi.py --confirm`` to append inside the container
  as the service user (step 2). The host never writes the store.
- ``measure`` reads what was fetched and reports, per symbol:
  - the first and last day held and the missing days between them;
  - the span against the replay depths the factory needs (1,000 days below 1d, 2,000 bars at 1d);
  - the hourly OI against ``open_interest_1h.jsonl`` at three alignments.

**The one-hour label.** The archive stamps a 5-minute reading with the moment it was taken, so its
on-the-hour value closes the hour before it. ``oi_store`` labels a row with the hour's START. Measured
2026-10-03 over three days of ETHUSDT: the store's ``t`` matches the archive's ``t+1h`` (hourly-change
correlation 0.94, median level gap 0.09%), and the other two alignments do not (0.04 and 0.11).
:func:`hourly_open_interest` applies that shift, and ``measure`` checks it on every run rather than
trusting this paragraph. Getting it wrong would let a feature read one hour into the future.

Run on the host (stdlib only), e.g.::

    python3 scripts/binance_archive.py fetch --symbols BTCUSDT,ETHUSDT --start 2021-12-01 --dest /root/binance_archive
    python3 scripts/binance_archive.py measure --dest /root/binance_archive \\
        --oi-store /root/thomas_agent/.runtime_governance_state/crypto/open_interest_1h.jsonl
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

try:  # run as `python3 scripts/binance_archive.py`: scripts/ is on sys.path
    from lib.utctime import format_utc
except ImportError:  # imported as `scripts.binance_archive` (the tests)
    from scripts.lib.utctime import format_utc

ARCHIVE_BASE = "https://data.binance.vision/data/futures/um"
DEFAULT_SYMBOLS = ("BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "DOGEUSDT")
METRICS = "metrics"
FUNDING = "fundingRate"
MISSING_FILENAME = "missing.json"
# The archive's on-the-hour reading closes the hour before it; the store labels the hour's start.
LABEL_SHIFT = timedelta(hours=1)
# The factory's replay depths (`market_data.FACTORY_DEPTH_DAYS`, `MIN_FACTORY_BARS` at 1d), stated here
# rather than imported because this script runs outside the runtime. `measure` reports against both.
DEPTH_BELOW_1D_DAYS = 1000
DEPTH_1D_DAYS = 2000
USER_AGENT = "thomas-agent-archive-measure/0.1"
PAUSE_SECONDS = 0.1

Opener = Callable[[str], bytes | None]


class ArchiveError(RuntimeError):
    """A fetched file that cannot be trusted: a checksum that does not match, or a zip that does
    not open."""


# --- names and paths ------------------------------------------------------------------------------

def daily_name(symbol: str, kind: str, day: date) -> str:
    return f"{symbol}-{kind}-{day.isoformat()}.zip"


def monthly_name(symbol: str, kind: str, year: int, month: int) -> str:
    return f"{symbol}-{kind}-{year:04d}-{month:02d}.zip"


def daily_url(symbol: str, kind: str, day: date) -> str:
    return f"{ARCHIVE_BASE}/daily/{kind}/{symbol}/{daily_name(symbol, kind, day)}"


def monthly_url(symbol: str, kind: str, year: int, month: int) -> str:
    return f"{ARCHIVE_BASE}/monthly/{kind}/{symbol}/{monthly_name(symbol, kind, year, month)}"


def local_path(dest: Path, kind: str, symbol: str, name: str) -> Path:
    return dest / kind / symbol / name


# --- fetching -------------------------------------------------------------------------------------

def http_get(url: str, *, timeout: int = 30) -> bytes | None:
    """The body, or None on a 404 (the archive's answer for a day it does not serve)."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT}, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 — fixed https host
            return response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def verify_checksum(payload: bytes, checksum_text: str, name: str) -> None:
    """Raise unless ``payload`` hashes to the sha256 the archive published for ``name``."""
    parts = checksum_text.split()
    if len(parts) < 2 or parts[1] != name:
        raise ArchiveError(f"{name}: the CHECKSUM names {parts[1:2] or 'nothing'}")
    if hashlib.sha256(payload).hexdigest() != parts[0].lower():
        raise ArchiveError(f"{name}: sha256 does not match the published CHECKSUM")


def fetch_one(url: str, target: Path, *, opener: Opener = http_get) -> str:
    """``'kept'``, ``'present'`` (already verified on disk) or ``'missing'`` (the archive has no such
    file). The zip is written only after its checksum matched, through a temporary name."""
    name = target.name
    if target.exists():
        return "present"
    payload = opener(url)
    if payload is None:
        return "missing"
    checksum = opener(url + ".CHECKSUM")
    if checksum is None:
        raise ArchiveError(f"{name}: the archive serves the file but no CHECKSUM")
    verify_checksum(payload, checksum.decode("utf-8", "replace"), name)
    try:
        zipfile.ZipFile(io.BytesIO(payload)).testzip()
    except zipfile.BadZipFile as exc:
        raise ArchiveError(f"{name}: not a zip") from exc
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    partial.write_bytes(payload)
    partial.replace(target)
    return "kept"


def days(start: date, end: date) -> Iterable[date]:
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)


def months(start: date, end: date) -> Iterable[tuple[int, int]]:
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        yield year, month
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)


def fetch(dest: Path, *, symbols: Sequence[str], start: date, end: date, funding: bool = False,
          opener: Opener = http_get, pause: float = PAUSE_SECONDS) -> dict[str, dict[str, int]]:
    """Download every file in range that is not already on disk. Returns per-symbol counts."""
    missing_path = dest / MISSING_FILENAME
    missing: dict[str, list[str]] = json.loads(missing_path.read_text()) if missing_path.exists() else {}
    known_missing = {name for names in missing.values() for name in names}
    counts: dict[str, dict[str, int]] = {}
    for symbol in symbols:
        tally = {"kept": 0, "present": 0, "missing": 0}
        jobs = [(daily_url(symbol, METRICS, d), local_path(dest, METRICS, symbol, daily_name(symbol, METRICS, d)))
                for d in days(start, end)]
        if funding:
            jobs += [(monthly_url(symbol, FUNDING, y, m),
                      local_path(dest, FUNDING, symbol, monthly_name(symbol, FUNDING, y, m)))
                     for y, m in months(start, end)]
        for url, target in jobs:
            if target.name in known_missing:
                tally["missing"] += 1
                continue
            outcome = fetch_one(url, target, opener=opener)
            tally[outcome] += 1
            if outcome == "missing":
                missing.setdefault(symbol, []).append(target.name)
            if outcome == "kept" and pause:
                time.sleep(pause)
        counts[symbol] = tally
    dest.mkdir(parents=True, exist_ok=True)
    missing_path.write_text(json.dumps({k: sorted(v) for k, v in missing.items()}, indent=1))
    return counts


# --- reading what was fetched ---------------------------------------------------------------------

def read_metrics_zip(path: Path) -> list[dict[str, str]]:
    with zipfile.ZipFile(path) as archive:
        text = archive.read(archive.namelist()[0]).decode("utf-8")
    return list(csv.DictReader(io.StringIO(text)))


def _instant(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "")).replace(tzinfo=timezone.utc)


def _iso(moment: datetime) -> str:
    return format_utc(moment)


def hourly_open_interest(rows: Iterable[Mapping[str, str]]) -> dict[str, float]:
    """The on-the-hour ``sum_open_interest`` readings, relabelled to the hour they CLOSE, which is the
    label ``oi_store`` uses (:data:`LABEL_SHIFT`). A row that does not parse is skipped."""
    out: dict[str, float] = {}
    for row in rows:
        try:
            moment = _instant(str(row["create_time"]))
            value = float(row["sum_open_interest"])
        except (KeyError, ValueError):
            continue
        if moment.minute == 0 and moment.second == 0:
            out[_iso(moment - LABEL_SHIFT)] = value
    return out


def coverage(present: Iterable[date]) -> dict[str, Any]:
    """First and last day held, how many days that spans, and which days inside it are absent."""
    held = sorted(set(present))
    if not held:
        return {"first": None, "last": None, "span_days": 0, "held_days": 0, "missing_days": []}
    first, last = held[0], held[-1]
    have = set(held)
    gaps = [d.isoformat() for d in days(first, last) if d not in have]
    span = (last - first).days + 1
    return {"first": first.isoformat(), "last": last.isoformat(), "span_days": span,
            "held_days": len(held), "missing_days": gaps,
            "covers_below_1d": span >= DEPTH_BELOW_1D_DAYS,
            "share_of_1d_replay": round(min(1.0, span / DEPTH_1D_DAYS), 3)}


def read_oi_store(path: Path, symbol: str) -> dict[str, float]:
    """``{hour label: open interest}`` for one symbol, latest row wins, from the store file as written.
    Read-only: no lock, no write, and a line that does not parse is skipped."""
    out: dict[str, float] = {}
    if not path.exists():
        return out
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("symbol") == symbol and isinstance(row.get("open_interest"), (int, float)):
                out[str(row.get("timestamp"))] = float(row["open_interest"])
    return out


def _correlation(a: Sequence[float], b: Sequence[float]) -> float | None:
    if len(a) < 3 or statistics.pstdev(a) == 0 or statistics.pstdev(b) == 0:
        return None
    ma, mb = statistics.mean(a), statistics.mean(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    den = (sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b)) ** 0.5
    return round(num / den, 4)


def compare(store: Mapping[str, float], archive: Mapping[str, float],
            offsets: Sequence[int] = (-1, 0, 1)) -> dict[int, dict[str, Any]]:
    """How the archive series lines up with the store's at each extra hour offset (0 = the shift
    :func:`hourly_open_interest` already applies). For each offset: hours compared, correlation of the
    hour-on-hour changes, and the median relative level gap. The aligned offset should read 0."""
    result: dict[int, dict[str, Any]] = {}
    for offset in offsets:
        shift = timedelta(hours=offset)
        hours = sorted(t for t in store if _iso(_instant(t) + shift) in archive)
        pairs = [(t, _iso(_instant(t) - timedelta(hours=1))) for t in hours]
        pairs = [(t, p) for t, p in pairs if p in store and _iso(_instant(p) + shift) in archive]
        ds = [store[t] - store[p] for t, p in pairs]
        da = [archive[_iso(_instant(t) + shift)] - archive[_iso(_instant(p) + shift)] for t, p in pairs]
        gaps = [abs(store[t] / archive[_iso(_instant(t) + shift)] - 1) for t in hours
                if archive[_iso(_instant(t) + shift)]]
        result[offset] = {"hours": len(hours), "change_correlation": _correlation(ds, da),
                          "median_level_gap": round(statistics.median(gaps), 5) if gaps else None}
    return result


def measure(dest: Path, *, symbols: Sequence[str], oi_store: Path | None) -> dict[str, Any]:
    report: dict[str, Any] = {"label_shift_hours": LABEL_SHIFT.total_seconds() / 3600, "symbols": {}}
    for symbol in symbols:
        folder = dest / METRICS / symbol
        files = sorted(folder.glob(f"{symbol}-{METRICS}-*.zip")) if folder.exists() else []
        held = [date.fromisoformat(p.stem[-10:]) for p in files]   # SYMBOL-metrics-YYYY-MM-DD
        entry: dict[str, Any] = {"metrics": coverage(held)}
        if oi_store is not None and files:
            store = read_oi_store(oi_store, symbol)
            overlap_days = {t[:10] for t in store}
            archive: dict[str, float] = {}
            for path, day in zip(files, held):
                if day.isoformat() in overlap_days or (day - timedelta(days=1)).isoformat() in overlap_days:
                    archive.update(hourly_open_interest(read_metrics_zip(path)))
            entry["oi_alignment"] = {str(k): v for k, v in compare(store, archive).items()}
        report["symbols"][symbol] = entry
    return report


def export_oi_rows(dest: Path, *, symbols: Sequence[str]) -> Iterable[dict[str, Any]]:
    """Every held day's hourly OI as ``oi_store``-shaped rows (``symbol``, ``timestamp`` = the hour's
    START, ``open_interest``), oldest first, one file at a time. What
    ``scripts/import_archive_oi.py`` reads on stdin inside the container (step 2)."""
    for symbol in symbols:
        folder = dest / METRICS / symbol
        for path in sorted(folder.glob(f"{symbol}-{METRICS}-*.zip")) if folder.exists() else []:
            for label, value in sorted(hourly_open_interest(read_metrics_zip(path)).items()):
                yield {"symbol": symbol, "timestamp": label, "open_interest": value}


# --- the command line -----------------------------------------------------------------------------

def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    f = sub.add_parser("fetch", help="download and checksum-verify archive files into --dest")
    f.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    f.add_argument("--start", required=True, type=date.fromisoformat)
    f.add_argument("--end", type=date.fromisoformat, default=None, help="default: yesterday (UTC)")
    f.add_argument("--dest", required=True, type=Path)
    f.add_argument("--funding", action="store_true", help="also the monthly fundingRate files")
    m = sub.add_parser("measure", help="coverage per symbol and OI alignment against the store")
    m.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    m.add_argument("--dest", required=True, type=Path)
    m.add_argument("--oi-store", type=Path, default=None)
    m.add_argument("--json", action="store_true")
    e = sub.add_parser("export-oi", help="hourly OI as oi_store rows (JSONL on stdout), for the importer")
    e.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    e.add_argument("--dest", required=True, type=Path)
    args = parser.parse_args(argv)
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if args.command == "fetch":
        end = args.end or (datetime.now(timezone.utc).date() - timedelta(days=1))
        try:
            counts = fetch(args.dest, symbols=symbols, start=args.start, end=end, funding=args.funding)
        except ArchiveError as exc:
            print(f"REFUSED: {exc}", file=sys.stderr)
            return 2
        for symbol, tally in counts.items():
            print(f"{symbol}: kept {tally['kept']}, already present {tally['present']}, "
                  f"not in the archive {tally['missing']}")
        return 0
    if args.command == "export-oi":
        for row in export_oi_rows(args.dest, symbols=symbols):
            sys.stdout.write(json.dumps(row) + "\n")
        return 0
    report = measure(args.dest, symbols=symbols, oi_store=args.oi_store)
    if args.json:
        print(json.dumps(report, indent=1))
        return 0
    print(f"label shift applied: archive t+{report['label_shift_hours']:.0f}h -> store t")
    for symbol, entry in report["symbols"].items():
        cov = entry["metrics"]
        print(f"{symbol}: {cov['first']} .. {cov['last']}  span {cov['span_days']}d  held {cov['held_days']}  "
              f"missing {len(cov['missing_days'])}  <1d replay covered: {cov.get('covers_below_1d')}  "
              f"1d replay share: {cov.get('share_of_1d_replay')}")
        for offset, row in (entry.get("oi_alignment") or {}).items():
            print(f"    extra offset {int(offset):+d}h: hours {row['hours']}  change corr {row['change_correlation']}  "
                  f"median level gap {row['median_level_gap']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

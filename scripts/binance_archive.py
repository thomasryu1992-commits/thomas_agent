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
- ``list-symbols`` and ``fetch-monthly`` serve the cross-section measurement
  (``docs/proposals/MARKET_NEUTRAL_CROSS_SECTION_V0.1.md``, Thomas 2026-11-04 Q4). The first writes every
  USDT perpetual the archive holds monthly 1d klines for, delisted ones included, from the archive's own
  bucket listing. The second downloads one kind of monthly file (``klines-1d`` or ``fundingRate``), and only
  the months each symbol's listing holds. Same checksum rule, same skip-if-present rule. ``contract-types``
  snapshots each USDT symbol's ``contractType`` from ``fapi/v1/exchangeInfo`` (public, keyless), so the
  measurement can leave out the gold, oil and stock perps listed since 2025-12-11.
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
import re
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ElementTree
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


# --- the archive's own listing, and the monthly files it holds --------------------------------------
#
# MARKET_NEUTRAL_CROSS_SECTION_V0.1 §3 and §8 (Thomas 2026-11-04, Q4): the cross-section universe is rebuilt
# point in time, so it has to include perps that were delisted since. `exchangeInfo` drops some of them
# (SRMUSDT and LUNAUSDT were absent on 2026-11-04) while the archive still serves their files, so the
# symbol list comes from the archive's bucket listing, and each symbol's own listing says which months exist,
# so a month the symbol never traded is never requested.

LISTING_BASE = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
LISTING_PREFIX = "data/futures/um/monthly/"
# CROSS_SECTION_SPOT_HOLDOUT_V0.1: the spot archive (from 2017-08) is the one span of Binance history no crypto
# measurement here has read. Same bucket, same file shape, only klines (spot has no funding).
SPOT_LISTING_PREFIX = "data/spot/monthly/"
MARKETS = {"um": LISTING_PREFIX, "spot": SPOT_LISTING_PREFIX}
KLINES = "klines"
KLINES_INTERVAL = "1d"
SYMBOLS_FILENAME = "symbols.json"
MONTHLY_KINDS = ("klines-1d", FUNDING)
# A USDT-margined perpetual: quarterly contracts carry `_YYMMDD`, and USDC/BUSD pairs are another quote.
_PERP_SYMBOL = re.compile(r"\A[A-Z0-9]{2,20}USDT\Z")
_S3_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"


def listing(prefix: str, *, opener: Opener = http_get, max_pages: int = 100) -> tuple[list[str], list[str]]:
    """``(sub-prefixes, keys)`` under ``prefix`` in the archive's bucket, every page. Raises
    :class:`ArchiveError` when a page does not parse or the listing never ends."""
    prefixes: list[str] = []
    keys: list[str] = []
    marker = ""
    for _ in range(max_pages):
        query = {"prefix": prefix, "delimiter": "/"}
        if marker:
            query["marker"] = marker
        body = opener(f"{LISTING_BASE}?{urllib.parse.urlencode(query)}")
        if body is None:
            raise ArchiveError(f"listing {prefix}: the bucket answered 404")
        try:
            root = ElementTree.fromstring(body)
        except ElementTree.ParseError as exc:
            raise ArchiveError(f"listing {prefix}: not XML") from exc
        page_prefixes = [p.text or "" for p in root.iter(f"{_S3_NS}Prefix")
                         if p.text and p.text != prefix and p.text.startswith(prefix)]
        page_keys = [k.text or "" for k in root.iter(f"{_S3_NS}Key") if k.text]
        prefixes += page_prefixes
        keys += page_keys
        if (root.findtext(f"{_S3_NS}IsTruncated") or "").strip().lower() != "true":
            return sorted(set(prefixes)), sorted(set(keys))
        marker = root.findtext(f"{_S3_NS}NextMarker") or max(page_prefixes + page_keys, default="")
        if not marker:
            raise ArchiveError(f"listing {prefix}: truncated with nothing to continue from")
    raise ArchiveError(f"listing {prefix}: still truncated after {max_pages} pages")


def archive_symbols(*, opener: Opener = http_get, market: str = "um") -> list[str]:
    """Every USDT symbol of ``market`` (``um`` perps, or ``spot`` pairs) the archive holds monthly 1d klines
    for, delisted ones included."""
    prefixes, _ = listing(f"{MARKETS[market]}{KLINES}/", opener=opener)
    names = (p.rstrip("/").rsplit("/", 1)[-1] for p in prefixes)
    return sorted(n for n in names if _PERP_SYMBOL.fullmatch(n))


# The archive listing does not say what a symbol tracks. Since 2025-12-11 Binance also lists perps on gold,
# crude oil and single stocks in the same `<NAME>USDT` form, and `exchangeInfo` tells them apart by
# `contractType`: `TRADIFI_PERPETUAL`, against `PERPETUAL` for a coin. Measured 2026-11-04: 8 of the 96
# symbols that ever entered the top-10 universe were TradFi, all from 2026-04 on. The snapshot is written
# beside the archive because the research sandbox, which reads it, has no network. A delisted symbol is
# absent from it, and the reader keeps an absent symbol as a coin (the measurement's own record says why).
EXCHANGE_INFO_URL = "https://fapi.binance.com/fapi/v1/exchangeInfo"
CONTRACT_TYPES_FILENAME = "contract_types.json"


def contract_types(*, opener: Opener = http_get) -> dict[str, str]:
    """``{symbol: contractType}`` for every USDT symbol ``exchangeInfo`` lists today. Raises
    :class:`ArchiveError` when the answer is missing, not JSON or lists nothing."""
    body = opener(EXCHANGE_INFO_URL)
    if body is None:
        raise ArchiveError("exchangeInfo answered 404")
    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise ArchiveError("exchangeInfo: not JSON") from exc
    rows = payload.get("symbols") if isinstance(payload, dict) else None
    out = {row["symbol"]: row["contractType"] for row in rows or []
           if isinstance(row, dict) and isinstance(row.get("symbol"), str)
           and isinstance(row.get("contractType"), str) and _PERP_SYMBOL.fullmatch(row["symbol"])}
    if not out:
        raise ArchiveError("exchangeInfo: no USDT symbol with a contractType")
    return dict(sorted(out.items()))


def monthly_folder(kind: str, symbol: str, market: str = "um") -> str:
    """The bucket folder holding one symbol's monthly files of ``kind`` (see :data:`MONTHLY_KINDS`)."""
    if kind == "klines-1d":
        return f"{MARKETS[market]}{KLINES}/{symbol}/{KLINES_INTERVAL}/"
    if kind == FUNDING and market == "um":
        return f"{LISTING_PREFIX}{FUNDING}/{symbol}/"
    raise ValueError(f"unknown monthly kind {kind!r}")


def monthly_local(dest: Path, kind: str, symbol: str, name: str) -> Path:
    return dest / kind / symbol / name


def _month_of(name: str) -> tuple[int, int] | None:
    stem = name[:-4] if name.endswith(".zip") else name
    try:
        year, month = int(stem[-7:-3]), int(stem[-2:])
    except ValueError:
        return None
    return (year, month) if stem[-3] == "-" and 1 <= month <= 12 else None


def fetch_monthly(dest: Path, *, kind: str, symbols: Sequence[str], start: date, end: date,
                  opener: Opener = http_get, pause: float = PAUSE_SECONDS, market: str = "um",
                  say: Callable[[str], None] = lambda _line: None) -> dict[str, dict[str, int]]:
    """Every monthly ``kind`` file a symbol's own listing holds inside [start, end], checksum-verified.
    A file already on disk is never asked for again, and a month the listing does not hold is never asked
    for at all. Returns per-symbol counts."""
    counts: dict[str, dict[str, int]] = {}
    lo, hi = (start.year, start.month), (end.year, end.month)
    for number, symbol in enumerate(symbols, 1):
        tally = {"kept": 0, "present": 0, "missing": 0}
        folder = monthly_folder(kind, symbol, market)
        _, keys = listing(folder, opener=opener)
        for key in keys:
            name = key.rsplit("/", 1)[-1]
            month = _month_of(name)
            if not name.endswith(".zip") or month is None or not lo <= month <= hi:
                continue
            outcome = fetch_one(f"https://data.binance.vision/{key}", monthly_local(dest, kind, symbol, name),
                                opener=opener)
            tally[outcome] += 1
            if outcome == "kept" and pause:
                time.sleep(pause)
        counts[symbol] = tally
        say(f"[{number}/{len(symbols)}] {symbol}: kept {tally['kept']}, present {tally['present']}, "
            f"missing {tally['missing']}")
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
    ls = sub.add_parser("list-symbols", help="every USDT perp with monthly 1d klines, delisted included, "
                                             f"written to <dest>/{SYMBOLS_FILENAME}")
    ls.add_argument("--dest", required=True, type=Path)
    ls.add_argument("--market", choices=sorted(MARKETS), default="um")
    ct = sub.add_parser("contract-types", help="each USDT symbol's contractType from exchangeInfo, written to "
                                               f"<dest>/{CONTRACT_TYPES_FILENAME}")
    ct.add_argument("--dest", required=True, type=Path)
    fm = sub.add_parser("fetch-monthly", help="a symbol's monthly files, only the months its listing holds")
    fm.add_argument("--kind", required=True, choices=MONTHLY_KINDS)
    fm.add_argument("--symbols", default=None, help=f"comma list; default: <dest>/{SYMBOLS_FILENAME}")
    fm.add_argument("--start", required=True, type=date.fromisoformat)
    fm.add_argument("--end", required=True, type=date.fromisoformat)
    fm.add_argument("--dest", required=True, type=Path)
    fm.add_argument("--market", choices=sorted(MARKETS), default="um", help="spot holds klines-1d only")
    args = parser.parse_args(argv)
    if args.command == "list-symbols":
        try:
            names = archive_symbols(market=args.market)
        except ArchiveError as exc:
            print(f"REFUSED: {exc}", file=sys.stderr)
            return 2
        args.dest.mkdir(parents=True, exist_ok=True)
        (args.dest / SYMBOLS_FILENAME).write_text(json.dumps(names, indent=0) + "\n", encoding="utf-8")
        print(f"{len(names)} USDT perps in the archive -> {args.dest / SYMBOLS_FILENAME}")
        return 0
    if args.command == "contract-types":
        try:
            kinds = contract_types()
        except ArchiveError as exc:
            print(f"REFUSED: {exc}", file=sys.stderr)
            return 2
        args.dest.mkdir(parents=True, exist_ok=True)
        record = {"source": EXCHANGE_INFO_URL, "fetched_at": format_utc(datetime.now(timezone.utc)),
                  "contract_types": kinds}
        (args.dest / CONTRACT_TYPES_FILENAME).write_text(json.dumps(record, indent=0) + "\n", encoding="utf-8")
        tally: dict[str, int] = {}
        for kind in kinds.values():
            tally[kind] = tally.get(kind, 0) + 1
        print(f"{len(kinds)} USDT symbols {tally} -> {args.dest / CONTRACT_TYPES_FILENAME}")
        return 0
    if args.command == "fetch-monthly":
        if args.symbols:
            wanted = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        else:
            listed = args.dest / SYMBOLS_FILENAME
            if not listed.exists():
                print(f"REFUSED: no {listed}; run list-symbols first or pass --symbols", file=sys.stderr)
                return 2
            wanted = list(json.loads(listed.read_text(encoding="utf-8")))
        try:
            if args.market == "spot" and args.kind != "klines-1d":
                print("REFUSED: the spot archive holds no funding", file=sys.stderr)
                return 2
            counts = fetch_monthly(args.dest, kind=args.kind, symbols=wanted, start=args.start, end=args.end,
                                   market=args.market,
                                   say=print)
        except ArchiveError as exc:
            print(f"REFUSED: {exc}", file=sys.stderr)
            return 2
        kept = sum(t["kept"] for t in counts.values())
        present = sum(t["present"] for t in counts.values())
        print(f"{args.kind}: {len(counts)} symbols, kept {kept}, already present {present}")
        return 0
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

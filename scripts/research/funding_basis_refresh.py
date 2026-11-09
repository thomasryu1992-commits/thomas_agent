"""Monthly data refresh for the funding-basis screens: one command per finished month, paced and resumable.

``docs/proposals/FUNDING_BASIS_V0.2.md`` §9 and ``STRATEGY_SCREEN_GATE_V0.1.md`` §5. After month M ends and its
monthly archive is published, this runs the existing public reads in order. It adds no network read of its own:
every request goes through ``scripts/binance_archive.py`` or ``scripts.research.fetch_public``, which keep their
own rules (checksums, skip-if-present, the IP weight budget, the factory window).

1. The symbol lists: every USDT perp and spot pair in the archive, and each perp's contract type. A perp listed
   this month can rank next month.
2. Every perp's month-M 1d klines. The universe ranks all perps by volume, so a partial fetch would change who is
   a member.
3. Month M's spot 1d klines for the spot pair of every perp (the universe needs last month's spot pair).
4. Month M's funding archive and the derivative series (``fetch_public --timeframe 1d``) for the targets:
   the top :data:`TOP_N` by volume in M (next month's candidates), the top in M − 1 (this month's candidates),
   and the v0.1 screen's eleven symbols. Members are a subset: the top by volume, then those with a spot pair.

Pacing (lessons of the v0.2 fetch, 2026-11-09): one derivative symbol at a time, :data:`PACE_SECONDS` apart; on
the venue's weight stop, wait :data:`WEIGHT_WAIT_SECONDS` and retry up to :data:`WEIGHT_RETRIES` times. A 429 or
418 stops the whole run. Any other failure (a 400 for a delisted perp) is listed at the end and the run goes on;
the exit code is then BLOCKED. No network
step starts in [07:30, 09:00) UTC (the factory window and the sandbox's own refusal); the run stops there and
prints how to resume. Every step is safe to rerun: present files and cached rows are never fetched again.
Symbol lists are passed as arguments, never through a file, so no last line can be dropped.

It runs on the host as ``claude`` (the sandbox has no network) and writes only under the research cache.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path
from typing import Callable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK, EXIT_USAGE  # noqa: E402
from scripts.research import funding_basis as fb  # noqa: E402
from scripts.research import funding_basis_v02 as v  # noqa: E402

TOP_N = v.TOP_N
PACE_SECONDS = 20
WEIGHT_WAIT_SECONDS = 70
WEIGHT_RETRIES = 2
QUIET_UTC = (dtime(7, 30), dtime(9, 0))
WEIGHT_STOP = "weight this minute"            # fetch_public's message when the IP's minute weight is too high
BAN_CODES = (429, 418)                        # fetch_public stops on these without retry; so does the whole run
ARCHIVE = ["scripts/binance_archive.py"]
FETCH_PUBLIC = ["-m", "scripts.research.fetch_public"]

Runner = Callable[[list[str]], tuple[int, str]]


class Halt(Exception):
    """Stop the run here; rerunning resumes."""


def month_bounds(key: str) -> tuple[date, date]:
    first = date(int(key[:4]), int(key[5:]), 1)
    nxt = date(first.year + (first.month == 12), first.month % 12 + 1, 1)
    return first, nxt - timedelta(days=1)


def in_quiet(now: datetime) -> bool:
    return QUIET_UTC[0] <= now.astimezone(timezone.utc).time() < QUIET_UTC[1]


def top_by_volume(volumes: Mapping[str, Mapping[str, float]], month: str, n: int = TOP_N) -> list[str]:
    return [p for p, _ in sorted(volumes.get(month, {}).items(), key=lambda kv: (-kv[1], kv[0]))[:n]]


def targets(volumes: Mapping[str, Mapping[str, float]], month: str) -> list[str]:
    """Derivative and funding targets after month ``month``: its top, last month's top, and v0.1's symbols."""
    return sorted(set(top_by_volume(volumes, month)) | set(top_by_volume(volumes, v.prev_month(month)))
                  | set(fb.SYMBOLS))


def spot_pairs(perps: Sequence[str], contract_types: Mapping[str, str], spot_listing: set[str]) -> list[str]:
    pairs = {v.spot_pair(p, spot_listing) for p in perps if contract_types.get(p) != v.TRADIFI}
    return sorted(p for p in pairs if p is not None)


def default_runner(argv: list[str]) -> tuple[int, str]:
    done = subprocess.run([sys.executable, *argv], cwd=ROOT, capture_output=True, text=True)
    lines = [ln for ln in (done.stdout + done.stderr).splitlines() if ln.strip()]
    return done.returncode, lines[-1] if lines else ""


@dataclass
class Refresh:
    month: str
    cache: Path
    run: Runner = default_runner
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    sleep: Callable[[float], None] = time.sleep
    say: Callable[[str], None] = print
    failed: list[str] = field(default_factory=list)

    @property
    def archive(self) -> Path:
        return self.cache / "archive"

    @property
    def spot_archive(self) -> Path:
        return self.cache / "archive-spot"

    def _guard(self, next_step: str) -> None:
        if in_quiet(self.now()):
            raise Halt(f"inside the quiet window {QUIET_UTC[0]}–{QUIET_UTC[1]} UTC before {next_step}; "
                       "rerun the same command after 09:00 UTC to resume")

    def _step(self, label: str, argv: list[str]) -> None:
        self._guard(label)
        rc, last = self.run(argv)
        self.say(f"{label}: rc={rc} {last}")
        if rc != 0:
            raise Halt(f"{label} failed (rc={rc}): {last}")

    def _monthly(self, kind: str, symbols: Sequence[str], dest: Path, market: str = "um") -> list[str]:
        first, last = month_bounds(self.month)
        argv = [*ARCHIVE, "fetch-monthly", "--kind", kind, "--start", first.isoformat(), "--end",
                last.isoformat(), "--dest", str(dest), "--market", market]
        return argv + (["--symbols", ",".join(symbols)] if symbols else [])

    def derivative(self, symbol: str) -> None:
        for attempt in range(WEIGHT_RETRIES + 1):
            self._guard(f"fetch_public {symbol}")
            rc, last = self.run([*FETCH_PUBLIC, "--cache", str(self.cache), "--symbol", symbol, "--timeframe", "1d"])
            self.say(f"fetch_public {symbol} try {attempt + 1}: rc={rc} {last}")
            if rc == 0:
                return
            if any(f"answered {code}" in last for code in BAN_CODES):
                raise Halt(f"fetch_public {symbol}: {last} — the venue is limiting this IP, the whole run stops")
            if WEIGHT_STOP in last and attempt < WEIGHT_RETRIES:
                self.sleep(WEIGHT_WAIT_SECONDS)
                continue
            break                                    # this symbol failed (e.g. a 400 for a delisted perp)
        self.failed.append(symbol)

    def plan_targets(self) -> list[str]:
        perps = json.loads((self.archive / "symbols.json").read_text())
        contract_types = json.loads((self.archive / "contract_types.json").read_text())["contract_types"]
        volumes, _ = v.read_volumes(self.archive, perps, contract_types)
        if not volumes.get(self.month):
            raise Halt(f"no perp klines for {self.month} in {self.archive}")
        if len(volumes[self.month]) < TOP_N:
            raise Halt(f"klines for {self.month} hold {len(volumes[self.month])} perps, fewer than {TOP_N}")
        return targets(volumes, self.month)

    def execute(self) -> int:
        self._step("perp symbol list", [*ARCHIVE, "list-symbols", "--dest", str(self.archive)])
        self._step("spot symbol list", [*ARCHIVE, "list-symbols", "--market", "spot", "--dest",
                                        str(self.spot_archive)])
        self._step("contract types", [*ARCHIVE, "contract-types", "--dest", str(self.archive)])
        self._step("perp klines (all perps)", self._monthly("klines-1d", (), self.archive))
        perps = json.loads((self.archive / "symbols.json").read_text())
        contract_types = json.loads((self.archive / "contract_types.json").read_text())["contract_types"]
        spot_listing = set(json.loads((self.spot_archive / "symbols.json").read_text()))
        self._step("spot klines", self._monthly("klines-1d", spot_pairs(perps, contract_types, spot_listing),
                                                self.spot_archive, market="spot"))
        wanted = self.plan_targets()
        self.say(f"targets for {self.month}: {len(wanted)} symbols")
        self._step("funding archive", self._monthly("fundingRate", wanted, self.archive))
        for i, symbol in enumerate(wanted):
            if i:
                self.sleep(PACE_SECONDS)
            self.derivative(symbol)
        if self.failed:
            self.say(f"FAILED after retries: {','.join(self.failed)} — rerun the same command to resume")
            return EXIT_BLOCKED
        self.say(f"done: {self.month}, {len(wanted)} derivative symbols")
        return EXIT_OK


def main(argv: list[str] | None = None, *, today: date | None = None) -> int:
    parser = argparse.ArgumentParser(prog="funding_basis_refresh", description=__doc__.splitlines()[0])
    parser.add_argument("--month", required=True, help="YYYY-MM, a finished month")
    parser.add_argument("--cache", type=Path, default=Path.home() / "research" / "cache")
    parser.add_argument("--plan", action="store_true",
                        help="no network: print the targets from what the cache already holds for --month")
    args = parser.parse_args(argv)
    try:
        first, last = month_bounds(args.month)
    except ValueError:
        print(f"--month takes YYYY-MM, not {args.month!r}", file=sys.stderr)
        return EXIT_USAGE
    if (today or datetime.now(timezone.utc).date()) <= last:
        print(f"refused: {args.month} has not ended; its monthly archive does not exist yet", file=sys.stderr)
        return EXIT_USAGE
    job = Refresh(month=args.month, cache=args.cache)
    try:
        if args.plan:
            wanted = job.plan_targets()
            print(json.dumps({"month": args.month, "targets": wanted, "count": len(wanted)}, indent=1))
            return EXIT_OK
        return job.execute()
    except Halt as halt:
        print(f"HALTED: {halt}", file=sys.stderr)
        return EXIT_BLOCKED


if __name__ == "__main__":
    raise SystemExit(main())

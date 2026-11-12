"""Write down every symbol's best bid and ask, once per 15-minute slot. Forward only; a missed slot is gone.

``docs/proposals/SPREAD_OBSERVATION_V0.1.md`` (Thomas 2026-11-12, P1 – P5) and ``RESEARCH_SANDBOX_V0.1.md`` §4.2. The
engine assumes 3 bps of slippage on every coin, the venue keeps no history of its book, and the public archive's
``bookTicker`` files end on 2024-03-30. So the spread of the coins a screened basket actually holds can only be known
for the days this ran.

- **One request per run, on every path.** ``GET /fapi/v1/ticker/bookTicker`` with no symbol answers for the whole
  venue at request weight 5. No key. It goes through ``fetch_public.Budget``, so the sandbox's rules hold: never
  inside the factory window (08:00 – 09:00 UTC), stop without retry at 429 / 418, and a sample whose answer reports
  the IP at half its minute budget is dropped.
- **A slot already held is never asked for again.** The slot is the 15-minute period of the wall clock; the check
  reads the day's file before any request is made.
- **What a line holds:** the sample time, the slot, the IP weight the venue reported, and one row per symbol:
  ``[symbol, bid price, bid qty, ask price, ask qty, the venue's time for that quote (ms)]``, prices and quantities
  as the venue's own strings. Nothing else of the answer is kept. A quote can be minutes old (a halted symbol), which
  is why its own time is kept.
- **An answer this cannot describe writes nothing.** Anything but a non-empty list of rows carrying those fields
  stops the run; a partial sample is never written.
- **Closed days are compressed** (``<day>.jsonl.gz``), after the copy is read back and matches.
- **Display only.** Nothing here computes a return, and no judgement reads it. A measured cost is never applied to a
  recorded sum, and the spread is not a search feature (the days it covers are reserved, ``NEXT_LOCKED_SPAN_V0.1``).

``sample`` runs on the host as the ``claude`` account (the sandbox has no network); ``coverage`` prints dates and
counts only.
"""

from __future__ import annotations

import argparse
import gzip
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK, EXIT_USAGE  # noqa: E402
from scripts.lib.utctime import format_utc  # noqa: E402
from scripts.research import cross_section_basket as xsb  # noqa: E402
from scripts.research import fetch_public  # noqa: E402

SPEC = "SPREAD_OBSERVATION_V0.1"
ENDPOINT = "ticker/bookTicker"
WEIGHT = 5                                # the venue's weight for the call without a symbol (seen: 5, 2026-11-12)
SLOT_SECONDS = 900
FIELDS = ("symbol", "bidPrice", "bidQty", "askPrice", "askQty", "time")
# The cron schedule, and what a full day therefore holds. Hour 8 is the factory window.
CRON_MINUTES = (7, 22, 37, 52)
CRON_HOURS = tuple(h for h in range(24) if not fetch_public.FACTORY_WINDOW_UTC[0] <= h < fetch_public.FACTORY_WINDOW_UTC[1])
SAMPLES_PER_DAY = len(CRON_MINUTES) * len(CRON_HOURS)
Stop = fetch_public.Stop


def spread_dir(cache: Path) -> Path:
    return cache / "spread"


def day_path(cache: Path, day: str) -> Path:
    return spread_dir(cache) / f"{day}.jsonl"


def slot_of(now: float) -> int:
    return int(now // SLOT_SECONDS)


def _moment(now: float) -> datetime:
    return datetime.fromtimestamp(int(now), tz=timezone.utc)


def read_day(path: Path) -> list[dict[str, Any]]:
    """A day's samples, plain or compressed. A line that does not parse is skipped, never raised on."""
    if path.suffix == ".gz":
        text = gzip.decompress(path.read_bytes()).decode("utf-8")
    elif path.exists():
        text = path.read_text(encoding="utf-8")
    else:
        return []
    out = []
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and isinstance(row.get("rows"), list):
            out.append(row)
    return out


def rows_of(answer: Any) -> list[list[Any]]:
    """The kept columns of one answer, or :class:`Stop` when it is not the shape this was written for."""
    if not isinstance(answer, list) or not answer:
        raise Stop("the venue's answer was not a non-empty list")
    rows = []
    for item in answer:
        try:
            row = [item[name] for name in FIELDS]
            float(row[1]), float(row[2]), float(row[3]), float(row[4])
            row[5] = int(row[5])
        except (KeyError, TypeError, ValueError):
            raise Stop("a row of the venue's answer lacked a field or held a non-number") from None
        if not isinstance(row[0], str) or not row[0]:
            raise Stop("a row of the venue's answer had no symbol")
        rows.append(row)
    return rows


def sample(budget: fetch_public.Budget, cache: Path) -> dict[str, Any]:
    """Take this slot's sample unless it is held. Returns what happened; raises :class:`Stop` and writes nothing
    when the budget, the window or the answer says no."""
    now = budget.clock()
    slot, moment = slot_of(now), _moment(now)
    path = day_path(cache, f"{moment:%Y-%m-%d}")
    if any(row.get("slot") == slot for row in read_day(path)):
        return {"written": False, "reason": "slot already held", "slot": slot}
    seen: dict[str, str] = {}
    inner = budget.fetch

    def recording(url: str):
        status, headers, body = inner(url)
        seen.update({k.lower(): v for k, v in headers.items()})
        return status, headers, body

    budget.fetch = recording
    try:
        answer = budget.get(ENDPOINT, {}, weight=WEIGHT)
    finally:
        budget.fetch = inner
    rows = rows_of(answer)
    used = seen.get("x-mbx-used-weight-1m")
    line = {"t": format_utc(moment), "slot": slot, "used_weight_1m": int(used) if used is not None else None,
            "n": len(rows), "rows": rows}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(line, separators=(",", ":")) + "\n")
    return {"written": True, "slot": slot, "symbols": len(rows), "used_weight_1m": line["used_weight_1m"]}


def compress_closed(cache: Path, today: str) -> list[str]:
    """Compress every plain day before ``today``. The plain file goes only after the copy reads back the same."""
    done = []
    for path in sorted(spread_dir(cache).glob("*.jsonl")):
        if path.stem >= today:
            continue
        target = path.with_suffix(".jsonl.gz")
        payload = path.read_bytes()
        if not target.exists():
            target.write_bytes(gzip.compress(payload, compresslevel=9, mtime=0))
        if gzip.decompress(target.read_bytes()) != payload:
            continue                                   # an older copy differs: keep both, report neither
        path.unlink()
        done.append(path.stem)
    return done


def spread_bps(bid: Any, ask: Any) -> float | None:
    """The quoted spread in basis points of the mid; None for a quote that is empty or crossed."""
    try:
        bid, ask = float(bid), float(ask)
    except (TypeError, ValueError):
        return None
    if bid <= 0 or ask <= 0 or bid >= ask:
        return None
    return (ask - bid) / ((ask + bid) / 2) * 10_000


def coverage(cache: Path) -> dict[str, Any]:
    """Per day: how many samples are held against a full day's count, and how many symbols a sample carries."""
    days = {}
    for path in sorted(p for p in spread_dir(cache).glob("*.jsonl*") if p.name.endswith((".jsonl", ".jsonl.gz"))):
        samples = read_day(path)
        counts = [len(row["rows"]) for row in samples]
        days[path.name.split(".")[0]] = {"samples": len(samples), "of": SAMPLES_PER_DAY,
                                          "symbols_median": int(statistics.median(counts)) if counts else 0}
    short = [day for day, body in days.items() if body["samples"] < SAMPLES_PER_DAY]
    return {"spec": SPEC, "days": days, "days_held": len(days), "days_short_of_full": short}


def brief(report: dict[str, Any]) -> str:
    """The coverage on one line: days held, and each short day with its count (the newest ten)."""
    short = [f"{day}({report['days'][day]['samples']})" for day in report["days_short_of_full"]]
    tail = f": {', '.join(short[-10:])}" if short else ""
    return (f"spread coverage: {report['days_held']} day(s) held, {len(short)} short of {SAMPLES_PER_DAY}{tail}")


def cron_line(code: Path, log: Path) -> str:
    """The crontab line, so the schedule the tests count is the schedule that is installed."""
    minutes, hours = ",".join(map(str, CRON_MINUTES)), ",".join(map(str, CRON_HOURS))
    lock = log.parent / "sample.lock"
    return (f"{minutes} {hours} * * * cd {code} && /usr/bin/flock -n {lock} /usr/bin/python3 -m "
            f"scripts.research.spread_sample sample >> {log} 2>&1")


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="spread_sample", description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("sample", "coverage"))
    parser.add_argument("--cache", default=str(Path.home() / "research" / "cache"))
    parser.add_argument("--brief", action="store_true", help="coverage on one line (the monthly refresh reads it)")
    args = parser.parse_args(list(argv) if argv is not None else None)
    cache = Path(args.cache)
    if args.command == "coverage":
        report = coverage(cache)
        print(brief(report) if args.brief else json.dumps(report, indent=1))
        return EXIT_OK
    if xsb._in_sandbox():
        print("refused: sample runs on the host; the sandbox has no network", file=sys.stderr)
        return EXIT_USAGE
    budget = fetch_public.Budget()
    stamp = format_utc(_moment(time.time()))
    try:
        result = sample(budget, cache)
    except Stop as exc:
        print(f"{stamp} stopped after {budget.requests} request(s), nothing written: {exc}", file=sys.stderr)
        return EXIT_BLOCKED
    compressed = compress_closed(cache, f"{_moment(budget.clock()):%Y-%m-%d}")
    print(f"{stamp} {json.dumps(result, separators=(',', ':'))} requests={budget.requests} compressed={len(compressed)}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())

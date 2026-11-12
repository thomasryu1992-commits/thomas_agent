"""Pool overlap: how often the pool's strategies hold a virtual position on one symbol at the same time.

``docs/proposals/POOL_OVERLAP_MEASUREMENT_V0.1.md`` (Thomas 2026-11-12, O1; the pool only, O2). The netting and
risk-budget design does work only when strategies overlap on a symbol, and today's one-position-per-symbol rule
drops a signal exactly then. This counts how often that is, from the pool's per-strategy forward book.

- **What it reads:** :data:`ALLOWED` — the strategy, the symbol, the timeframe, the direction, when the virtual
  position opened and closed, and whether the row was seeded. Nothing else of a row survives the load, so no result,
  price, cost or close reason can reach a count. The forward cohort is never read.
- **What it counts** (fixed in the proposal before this file existed): per symbol, the share of time with 0, 1, 2
  and 3+ positions open; inside the overlapped time, same-direction against mixed, one timeframe against several,
  and the mean net-over-gross; the entries that arrived while another strategy was already in on that symbol; and
  across symbols, the number of open positions against the number of symbol-direction groups. Each is given with
  the seeded rows and without them.
- **A position counts as one, and a strategy counts once.** The book has no quantities. Overlap is between
  different strategies: a strategy's own two rows that touch are counted as one holder and reported apart.
- **"Already open" means opened strictly earlier.** Strategies of one context are stepped in one cycle and share its
  stamp, so entries at the same instant get their own two buckets instead of each counting the other as first.
- **What it cannot see:** the file holds closed positions only, so the last days of each span undercount.
- **No judgement.** The report names no strategy as good or bad and feeds no door.

Runs in the research sandbox, over the lake snapshot it is given.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime import timeutil  # noqa: E402
from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK, EXIT_USAGE  # noqa: E402
from scripts.research import cross_section_basket as xsb  # noqa: E402

SPEC = "POOL_OVERLAP_MEASUREMENT_V0.1"
SOURCE = "forward_outcomes.jsonl"          # the pool's per-strategy forward book, as the lake exports it
ALLOWED = ("candidate_id", "symbol", "timeframe", "direction", "opened_at_utc", "created_at_utc", "seeded")
SIGN = {"LONG": 1, "SHORT": -1}
COUNT_BUCKETS = (0, 1, 2, 3)               # the last bucket is "3 or more"
TOTAL_BUCKETS = (0, 1, 2, 3, 4, 5)         # the last bucket is "5 or more"


def _only_allowed(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """The JSON object with every field outside :data:`ALLOWED` dropped at parse time."""
    return {key: value for key, value in pairs if key in ALLOWED}


def read(lines: Iterable[str]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """The positions — one per row that names a strategy, a symbol, a direction and both times — and what became
    of every row: how many were read, kept, and left out under which name."""
    out, seen = [], Counter()
    for line in lines:
        if not line.strip():
            continue
        seen["rows"] += 1
        try:
            row = json.loads(line, object_pairs_hook=_only_allowed)
        except ValueError:
            seen["left_out_unreadable_line"] += 1
            continue
        if not isinstance(row, dict) or not row.get("candidate_id") or not row.get("symbol"):
            seen["left_out_no_strategy_or_symbol"] += 1
            continue
        sign = SIGN.get(str(row.get("direction")).upper())
        if sign is None:
            seen["left_out_no_direction"] += 1
            continue
        try:
            opened = timeutil.parse_iso(str(row["opened_at_utc"])).timestamp()
            closed = timeutil.parse_iso(str(row["created_at_utc"])).timestamp()
        except (KeyError, ValueError, TypeError):
            seen["left_out_no_open_or_close_time"] += 1
            continue
        if closed < opened:
            seen["left_out_closes_before_it_opens"] += 1
            continue
        seen["kept"] += 1
        seen["kept_without_a_timeframe"] += 0 if row.get("timeframe") else 1
        out.append({"who": str(row["candidate_id"]), "symbol": str(row["symbol"]),
                    "timeframe": str(row["timeframe"]) if row.get("timeframe") else None,
                    "sign": sign, "open": opened, "close": closed, "seeded": bool(row.get("seeded"))})
    return out, dict(sorted(seen.items()))


def load(lines: Iterable[str]) -> list[dict[str, Any]]:
    return read(lines)[0]


def _stamp(ts: float) -> str:
    return timeutil.format_iso(datetime.fromtimestamp(ts, tz=timezone.utc))


def _span(positions: list[dict[str, Any]]) -> list[str] | None:
    """First open to last close — the time every share of these positions is a share of."""
    return [_stamp(min(p["open"] for p in positions)), _stamp(max(p["close"] for p in positions))] if positions else None


def _bucket(count: int, buckets: tuple[int, ...]) -> str:
    return f"{buckets[-1]}+" if count >= buckets[-1] else str(count)


def _shares(seconds: Counter, total: float) -> dict[str, float]:
    return {key: round(value / total, 4) for key, value in sorted(seconds.items())} if total else {}


def _sweep(positions: list[dict[str, Any]]):
    """Yield (duration, the positions open during it) between consecutive event times, first open to last close.
    A strategy is in once per symbol: its second row open at the same time is not another holder."""
    times = sorted({p["open"] for p in positions} | {p["close"] for p in positions})
    ordered = sorted(positions, key=lambda p: p["open"])
    for start, end in zip(times, times[1:]):
        held: dict[tuple[str, str], dict[str, Any]] = {}
        for p in ordered:
            if p["open"] <= start and p["close"] >= end:
                held.setdefault((p["who"], p["symbol"]), p)
        yield end - start, list(held.values())


def symbol_counts(positions: list[dict[str, Any]]) -> dict[str, Any]:
    """Readings 1–5 for one symbol's positions."""
    by_count, overlapped, same, several_timeframes, net_over_gross, peak = Counter(), 0.0, 0.0, 0.0, 0.0, 0
    for duration, held in _sweep(positions):
        by_count[_bucket(len(held), COUNT_BUCKETS)] += duration
        peak = max(peak, len(held))
        if len(held) >= 2:
            overlapped += duration
            net = sum(p["sign"] for p in held)
            same += duration if abs(net) == len(held) else 0.0
            several_timeframes += duration if len({p["timeframe"] for p in held if p["timeframe"]}) > 1 else 0.0
            net_over_gross += duration * abs(net) / len(held)
    total = sum(by_count.values())
    entries = Counter()
    for p in positions:
        others = [q for q in positions if q["who"] != p["who"] and q["open"] < p["open"] < q["close"]]
        together = [q for q in positions if q["who"] != p["who"] and q["open"] == p["open"]]
        if others and all(q["sign"] == p["sign"] for q in others):
            entries["behind_same_direction"] += 1
        elif others and all(q["sign"] != p["sign"] for q in others):
            entries["against_opposite_direction"] += 1
        elif others:
            entries["into_mixed"] += 1
        elif together and all(q["sign"] == p["sign"] for q in together):
            entries["together_same_direction"] += 1
        elif together:
            entries["together_not_same_direction"] += 1
        else:
            entries["alone"] += 1
    own = sum(1 for p in positions for q in positions
              if p is not q and p["who"] == q["who"] and q["open"] <= p["open"] < q["close"])
    kinds = ("alone", "behind_same_direction", "against_opposite_direction", "into_mixed", "together_same_direction",
             "together_not_same_direction")
    late = entries["behind_same_direction"] + entries["against_opposite_direction"] + entries["into_mixed"]
    tied = entries["together_same_direction"] + entries["together_not_same_direction"]
    return {
        "span": _span(positions),
        "strategies": len({p["who"] for p in positions}), "positions": len(positions), "most_open_at_once": peak,
        "share_of_time_by_open_count": _shares(by_count, total),
        "overlapped_share_of_time": round(overlapped / total, 4) if total else None,
        "inside_overlap": {
            "same_direction_share": round(same / overlapped, 4) if overlapped else None,
            "mixed_direction_share": round(1 - same / overlapped, 4) if overlapped else None,
            "several_timeframes_share": round(several_timeframes / overlapped, 4) if overlapped else None,
            "mean_net_over_gross": round(net_over_gross / overlapped, 4) if overlapped else None,
        },
        "entries": {**{k: entries[k] for k in kinds},
                    "share_arriving_while_another_was_open": round(late / len(positions), 4) if positions else None,
                    "share_opening_together_with_another": round(tied / len(positions), 4) if positions else None},
        "one_strategy_overlapping_itself": own,
    }


def portfolio_counts(positions: list[dict[str, Any]]) -> dict[str, Any]:
    """Reading 6: every symbol together — open positions against symbol-direction groups."""
    by_total, by_groups, peak_total, peak_groups = Counter(), Counter(), 0, 0
    for duration, held in _sweep(positions):
        groups = len({(p["symbol"], p["sign"]) for p in held})
        by_total[_bucket(len(held), TOTAL_BUCKETS)] += duration
        by_groups[_bucket(groups, TOTAL_BUCKETS)] += duration
        peak_total, peak_groups = max(peak_total, len(held)), max(peak_groups, groups)
    total = sum(by_total.values())
    return {"share_of_time_by_open_positions": _shares(by_total, total), "most_open_at_once": peak_total,
            "share_of_time_by_symbol_direction_groups": _shares(by_groups, total), "most_groups_at_once": peak_groups}


def measure(positions: list[dict[str, Any]]) -> dict[str, Any]:
    symbols = sorted({p["symbol"] for p in positions})
    return {
        "positions": len(positions), "strategies": len({p["who"] for p in positions}),
        "span": _span(positions),
        "by_symbol": {s: symbol_counts([p for p in positions if p["symbol"] == s]) for s in symbols},
        "all_symbols": portfolio_counts(positions),
    }


def report(positions: list[dict[str, Any]]) -> dict[str, Any]:
    """Reading 7: everything with the seeded rows, and again without them."""
    return {"with_seeded": measure(positions), "without_seeded": measure([p for p in positions if not p["seeded"]]),
            "seeded_positions": sum(1 for p in positions if p["seeded"]), "display_only": True}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pool_overlap", description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("run",))
    parser.add_argument("--lake", type=Path, default=None, help="default: $RESEARCH_LAKE")
    args = parser.parse_args(argv)
    if not xsb._in_sandbox():
        print("refused: runs in the research sandbox only (scripts/research/sandbox_run.sh)", file=sys.stderr)
        return EXIT_USAGE
    lake = args.lake or (Path(os.environ["RESEARCH_LAKE"]) if os.environ.get("RESEARCH_LAKE") else None)
    path = lake / SOURCE if lake else None
    if path is None or not path.is_file():
        print(f"BLOCKED: no {SOURCE} in the lake snapshot", file=sys.stderr)
        return EXIT_BLOCKED
    positions, rows = read(path.read_text(encoding="utf-8").splitlines())
    if not positions:
        print(f"BLOCKED: {SOURCE} holds no readable position", file=sys.stderr)
        return EXIT_BLOCKED
    print(json.dumps({"measured_at": timeutil.utc_now_iso(), "spec": SPEC, "source": SOURCE,
                      "lake_snapshot": lake.resolve().name, "fields_read": list(ALLOWED), "rows": rows,
                      "closed_positions_only": True, **report(positions)},
                     indent=1, sort_keys=True))
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())

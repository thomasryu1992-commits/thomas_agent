#!/usr/bin/env python3
"""Operator tool: drop shadow positions the one-per-context rule would never have opened.

The shadow book holds one position per ``(symbol, timeframe)`` because the book it shadows
does. That rule arrived in #620; the store predates it, so a book filled before then can hold
a context many times over. Measured on this machine 2026-08-08: **16 of 17 open shadows were
ETHUSDT 1d for one strategy**, opened one per 15-minute cycle over 31 hours while a block
persisted, with two distinct entry prices between them.

Left alone they settle, and settling is the harm. `counterfactual.r_values_by_reason` counts
every closed shadow as one observation and `dashboard.sample_verdict` builds an interval over
them, gating a guard's verdict on ``distinguishable_from_zero`` — so sixteen near-identical
rows do not add noise, they shrink the interval on a sample whose true size is one. The fix in
#620 stops NEW duplicates; it cannot reach the ones already open.

Expiry, never settlement: an outcome carries a ``result_R`` and feeds per-reason expectancy,
and nobody priced these. The oldest survives, which is exactly what the rule would have
produced — the first blocked signal opens a shadow and every later one is refused while it is
still open.

    python -m scripts.dedupe_counterfactual_book --list
    python -m scripts.dedupe_counterfactual_book --confirm --operator Thomas \\
        --reason "pre-#620 book: 16 ETHUSDT 1d duplicates of one blocked trade"

Read-only without ``--confirm``. The module form matters: ``python scripts/...`` puts
``scripts/`` on ``sys.path`` instead of the repo root. Run it as the service user
(``docker exec thomas-scheduler``) — ``state_guard`` refuses a foreign root run at the door,
because a root-written book is one the service can no longer write.
"""

from __future__ import annotations

import argparse
import collections
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime import timeutil  # noqa: E402
from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK, EXIT_USAGE  # noqa: E402
from runtime.mvp_runtime.crypto import counterfactual  # noqa: E402
from runtime.mvp_runtime.errors import MvpRuntimeError  # noqa: E402
from runtime.mvp_runtime.events import stamped_event  # noqa: E402
from runtime.mvp_runtime.state_guard import assert_not_foreign_root_run  # noqa: E402
from runtime.mvp_runtime.store import LEDGER_REL, LedgerStore  # noqa: E402

DEDUPE_EVENT_TYPE = "crypto_counterfactual_dedupe_event.v0"


def run_dedupe(
    *, operator: str, reason: str, root: Path | None = None, now: str | None = None,
    persist: bool = True,
) -> dict:
    """Expire the duplicates and record what was expired. Returns the summary.

    The ledger entry is the durable trace and the reason this is a door rather than an edit:
    an expired shadow leaves the book (the book only ever holds OPEN rows), and unlike the
    cycle path there is no cycle record to carry the ids. Written whether or not anything was
    expired — a run that found nothing is evidence too, and the alternative is an operator
    re-running it because the ledger cannot say it already happened.
    """
    now = now or timeutil.utc_now_iso()
    summary = counterfactual.expire_context_duplicates(root=root, now=now, persist=persist)
    summary.update({
        "operator": operator,
        "reason": reason,
        # NONE: this writes no outcome and prices nothing. The shadow book feeds no gate
        # decision, and expiring a row removes a hypothetical rather than a result.
        "outcome_effect": "NONE",
        "created_at": now,
    })
    ledger = LedgerStore((root if root is not None else ROOT) / LEDGER_REL)
    ledger.append_control(stamped_event(DEDUPE_EVENT_TYPE, **summary))
    return summary


def _print_book(root: Path | None) -> None:
    rows = counterfactual.load_open_counterfactuals(root)
    per_context = collections.Counter(
        (str(r.get("symbol") or ""), str(r.get("timeframe") or "")) for r in rows
    )
    duplicates = counterfactual.context_duplicates(rows)
    print(f"open shadows: {len(rows)}")
    for (symbol, timeframe), count in per_context.most_common():
        mark = "  <- duplicated" if count > 1 else ""
        print(f"  {count:3d}  {symbol} {timeframe}{mark}")
    print(f"would expire: {len(duplicates)} (the oldest in each context survives)")
    for row in duplicates:
        print(f"    {row.get('counterfactual_id')}  {row.get('symbol')} "
              f"{row.get('timeframe')}  opened {row.get('opened_at_utc')}  "
              f"{row.get('strategy_id')}  {row.get('block_reasons')}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Expire shadow positions the one-per-context rule would not have opened.")
    parser.add_argument("--list", action="store_true", help="show the book and exit")
    parser.add_argument("--operator", help="operator identity, recorded on the ledger")
    parser.add_argument("--reason", help="why this cleanup is being run (the record)")
    parser.add_argument("--confirm", action="store_true",
                        help="actually expire; without it nothing is written")
    args = parser.parse_args(argv)

    try:
        assert_not_foreign_root_run()
    except MvpRuntimeError as exc:
        print(f"BLOCKED {exc.reason_code}: {exc.reason}", file=sys.stderr)
        return EXIT_BLOCKED

    if args.list:
        try:
            _print_book(None)
        except MvpRuntimeError as exc:
            print(f"BLOCKED {exc.reason_code}: {exc.reason}", file=sys.stderr)
            return EXIT_BLOCKED
        return EXIT_OK

    if not args.confirm:
        print("refused: pass --confirm to expire (or --list to look)", file=sys.stderr)
        return EXIT_USAGE
    if not (args.operator and args.reason):
        print("refused: --operator and --reason are required with --confirm", file=sys.stderr)
        return EXIT_USAGE

    try:
        summary = run_dedupe(operator=args.operator, reason=args.reason)
    except MvpRuntimeError as exc:
        print(f"BLOCKED {exc.reason_code}: {exc.reason}", file=sys.stderr)
        return EXIT_BLOCKED

    print(f"EXPIRED {summary['expired_count']} duplicate shadow(s); "
          f"open {summary['open_before']} -> {summary['open_after']}")
    for row in summary["expired"]:
        print(f"  {row['counterfactual_id']}  {row['symbol']} {row['timeframe']}  "
              f"opened {row['opened_at_utc']}  {row['block_reasons']}")
    print("survivors:")
    for row in summary["kept"]:
        print(f"  {row['counterfactual_id']}  {row['symbol']} {row['timeframe']}  "
              f"opened {row['opened_at_utc']}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())

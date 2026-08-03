"""The operator's reset for the bracket-failure breaker.

    python -m scripts.clear_bracket_breaker --show
    python -m scripts.clear_bracket_breaker --cleared-by thomas \
        --reason "venue returned -2021 order-would-immediately-trigger; stop distance fixed in #NNN"

The breaker counts live entries that filled and could not be protected. Two in a row and it
refuses new entries — see ``live_order.MAX_CONSECUTIVE_BRACKET_FAILURES`` for why two, and
``live_entry.plan_live_entry`` for the door it closes. It does not expire, and the daily order
budget's midnight refill does not touch it, so this script is the only way back apart from a
protective bracket actually resting at the venue.

**Deliberately not automatic.** The breaker exists because a rejection the runtime could not
explain repeated itself and cost fees each time. A reset on a timer would restore exactly that
loop with extra steps; a reset that demands a written reason makes someone look at the venue's
own error first. The reason is stored verbatim on the record.

``--show`` reads and writes nothing and runs anywhere, before the root guard, for the reason the
Gate 0 acknowledgement gives: making the one command whose job is "look before you sign" refuse
on a host shell pushes an operator toward signing without looking.

Writes go through ``select_live_bracket_breaker``, so this needs the live-trading switch the
runtime itself runs under — run it in the container, not on the host:

    docker exec thomas-scheduler python -m scripts.clear_bracket_breaker --show
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from runtime.mvp_runtime import timeutil
from runtime.mvp_runtime.cli_common import force_utf8_io
from runtime.mvp_runtime.crypto.live_order import (
    bracket_breaker_status,
    select_live_bracket_breaker,
)
from runtime.mvp_runtime.errors import MvpRuntimeError
from runtime.mvp_runtime.state_guard import assert_not_foreign_root_run

EXIT_OK = 0
EXIT_REFUSED = 2


def _render(status: dict[str, object]) -> str:
    lines = [
        f"bracket breaker: {'TRIPPED' if status['tripped'] else 'clear'} "
        f"({status['consecutive']}/{status['limit']} consecutive, {status['total']} total)",
    ]
    if status["last_failure_at"]:
        lines.append(
            f"  last failure : {status['last_symbol']} {status['last_status']} "
            f"at {status['last_failure_at']}"
        )
    if status["last_reason_codes"]:
        lines.append(f"  reason codes : {', '.join(str(c) for c in status['last_reason_codes'])}")
    if status["last_error_detail"]:
        lines.append(f"  venue said   : {json.dumps(status['last_error_detail'], ensure_ascii=False)}")
    if status["cleared_at"]:
        lines.append(
            f"  last cleared : {status['cleared_at']} by {status['cleared_by']} "
            f"({status['cleared_reason']})"
        )
    if status["tripped"]:
        lines.append("  live entries are REFUSED until this is cleared.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    force_utf8_io()
    parser = argparse.ArgumentParser(prog="clear_bracket_breaker", description=__doc__)
    parser.add_argument("--show", action="store_true",
                        help="print the breaker's state and write nothing")
    parser.add_argument("--cleared-by", help="operator identity recorded on the reset")
    parser.add_argument("--reason", help="what you found and fixed, in your own words (required)")
    parser.add_argument("--root", type=Path, default=None, help="state root (defaults to the repo)")
    args = parser.parse_args(argv)

    root: Path | None = args.root

    try:
        status = bracket_breaker_status(root)
        if args.show:
            print(_render(status))
            return EXIT_OK

        if not args.cleared_by or not args.reason:
            print("--cleared-by and --reason are both required. Try --show first.", file=sys.stderr)
            return EXIT_REFUSED

        assert_not_foreign_root_run(root)

        if not status["consecutive"]:
            # Not an error: the streak may have ended on its own between looking and clearing.
            # Saying so beats writing a reset for something that had already reset.
            print("nothing to clear — the consecutive count is already zero.")
            return EXIT_OK

        breaker = select_live_bracket_breaker(now=timeutil.utc_now_iso(), root=root)
        breaker.clear(
            actor=args.cleared_by, reason=args.reason, at=timeutil.utc_now_iso(),
        )
    except MvpRuntimeError as exc:
        print(f"BLOCKED: {getattr(exc, 'reason_code', 'UNKNOWN')}: {exc}", file=sys.stderr)
        return EXIT_REFUSED

    print(_render(bracket_breaker_status(root)))
    print("cleared. The next live signal may open a position again.")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())

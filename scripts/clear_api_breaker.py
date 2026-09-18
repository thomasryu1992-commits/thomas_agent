"""The operator's reset for the API error breaker (PR2d-1, Thomas decisions 18 and 27).

    python -m scripts.clear_api_breaker --show
    python -m scripts.clear_api_breaker --cleared-by thomas \
        --reason "venue maintenance 03:00-03:40Z announced; signed calls answer again"

The breaker counts signed calls the venue refused, could not answer, or answered unreadably — the
order adapter's sends and cancels (the write class) and its order, resting-order and account reads
(the read class), each counted on its own. Five in a row in one class and it refuses new live
entries — see ``live_order.MAX_CONSECUTIVE_API_ERRORS`` for the count and ``API_ERROR_REASON_CODES``
/ ``API_ERROR_VENUE_CODES`` for what counts. Closing, settling and protection are never gated by it.

**It does not clear itself.** Once tripped, a successful call does not reopen the door: closes,
settlement and the account keep calling the venue while entries are shut, and a breaker any
success could clear would clear itself on the next pass. This script is the only way back, and it
demands a written reason so that someone looks at the venue first. The reason is stored verbatim.

``--show`` reads and writes nothing and runs anywhere, before the root guard, for the reason the
bracket breaker's own script gives: the command whose job is "look before you sign" must not
refuse on a host shell.

Writes go through ``select_live_api_breaker``, so this needs the live-trading switch the runtime
itself runs under — run it in the container, not on the host:

    docker exec thomas-scheduler python -m scripts.clear_api_breaker --show
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from runtime.mvp_runtime import timeutil
from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK, EXIT_USAGE, force_utf8_io
from runtime.mvp_runtime.crypto.live_order import (
    API_CALL_CLASSES,
    api_breaker_status,
    select_live_api_breaker,
)
from runtime.mvp_runtime.errors import MvpRuntimeError
from runtime.mvp_runtime.state_guard import assert_not_foreign_root_run


def _render(status: dict[str, object]) -> str:
    lines = [
        f"api breaker: {'TRIPPED at ' + str(status['tripped_at']) if status['tripped'] else 'clear'} "
        f"(limit {status['limit']} in a row per class)",
    ]
    for name in API_CALL_CLASSES:
        counts = status[name]  # type: ignore[index]
        line = f"  {name:5}: {counts['consecutive']} in a row, {counts['total']} total"
        if counts["last_failure_at"]:
            venue = f" (venue {counts['last_venue_code']})" if counts["last_venue_code"] is not None else ""
            line += (f"; last {counts['last_call']} {counts['last_reason_code']}{venue} "
                     f"at {counts['last_failure_at']}")
        lines.append(line)
    if status["cleared_at"]:
        lines.append(
            f"  last cleared : {status['cleared_at']} by {status['cleared_by']} "
            f"({status['cleared_reason']})"
        )
    if status["tripped"]:
        lines.append(f"  tripped by the {status['tripped_class']} class; "
                     "live entries are REFUSED until this is cleared.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    force_utf8_io()
    parser = argparse.ArgumentParser(prog="clear_api_breaker", description=__doc__)
    parser.add_argument("--show", action="store_true",
                        help="print the breaker's state and write nothing")
    parser.add_argument("--cleared-by", help="operator identity recorded on the reset")
    parser.add_argument("--reason", help="what you found at the venue, in your own words (required)")
    parser.add_argument("--root", type=Path, default=None, help="state root (defaults to the repo)")
    args = parser.parse_args(argv)

    root: Path | None = args.root

    try:
        status = api_breaker_status(root)
        if args.show:
            print(_render(status))
            return EXIT_OK

        # Stored verbatim, so a blank one is no answer at all.
        actor = (args.cleared_by or "").strip()
        reason = (args.reason or "").strip()
        if not actor or not reason:
            print("--cleared-by and --reason are both required. Try --show first.", file=sys.stderr)
            # USAGE, not BLOCKED: the operator mistyped the command; the runtime refused nothing.
            return EXIT_USAGE

        assert_not_foreign_root_run(root)

        if not status["tripped"] and not status["consecutive"]:
            # Not an error: nothing is counted, so there is nothing to reset.
            print("nothing to clear — the breaker is not tripped and no failure is counted.")
            return EXIT_OK

        breaker = select_live_api_breaker(now=timeutil.utc_now_iso(), root=root)
        at = timeutil.utc_now_iso()
        stored = breaker.clear(actor=actor, reason=reason, at=at)
        after = api_breaker_status(root)
    except MvpRuntimeError as exc:
        print(f"BLOCKED: {getattr(exc, 'reason_code', 'UNKNOWN')}: {exc}", file=sys.stderr)
        return EXIT_BLOCKED

    print(_render(after))
    # Judged by what the clear wrote, not by the counts: a failure the runtime counts right after
    # the reset is not a reset that failed.
    if not (isinstance(stored, dict) and stored.get("cleared_at") == at and stored.get("cleared_by") == actor):
        # The inert breaker writes nothing: with the live-trading switch off here, the reset went
        # nowhere. Say so rather than print "cleared" over a record that did not change.
        print("NOT cleared: nothing was written. Run this where the runtime runs, with its "
              "live-trading switch: docker exec thomas-scheduler python -m scripts.clear_api_breaker",
              file=sys.stderr)
        return EXIT_BLOCKED
    if after["tripped"]:
        print("cleared — and it has tripped again since, on what the venue did right after. Look "
              "again before clearing it a second time.", file=sys.stderr)
        return EXIT_BLOCKED
    print("cleared. The next live signal may open a position again.")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())

"""The venue contract sentinel's record, and an ask on demand (crypto PR4a, Thomas decisions 43-46).

    docker exec thomas-scheduler python -m scripts.venue_contract --show
    docker exec -u 10001 thomas-scheduler python -m scripts.venue_contract --run

The pipeline fire asks the exchange, about hourly (every fire while the last decided answer is a
FAIL), whether what this runtime assumes about it still holds — the traded symbols' listing and
filters, the -4120 that says conditional orders live on the Algo API, the account's position mode
and leverage, and that the validator left no order — and records the last decided answer
(``crypto/venue_contract.py``). A PASS stands six hours, and a mainnet autonomous entry or a probe
is decided only on one that covers its symbol (PR4b).

``--show`` reads and writes nothing and runs anywhere.

``--run`` asks now, exactly as the fire would: signed GETs and ``POST /fapi/v1/order/test`` with
the live order key, which creates no order. It needs the runtime's own environment and user — the
container, as uid 10001 — and it is the operator's to run, not an assistant's (it signs with the
live key). It writes the record and the attempt mark like the fire does.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Mapping

from runtime.mvp_runtime import timeutil
from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK, force_utf8_io
from runtime.mvp_runtime.crypto import venue_contract
from runtime.mvp_runtime.errors import MvpRuntimeError
from runtime.mvp_runtime.state_guard import assert_not_foreign_root_run

# A finding, not a refusal: the command ran and the venue contradicted the runtime.
EXIT_FAIL = 4


def _render_checks(checks: Any) -> list[str]:
    lines = []
    for check in checks or []:
        if not isinstance(check, Mapping):
            continue
        mark = check.get("result")
        detail = f" - {check['detail']}" if check.get("detail") else ""
        lines.append(f"  [{mark:10}] {check.get('check')}{detail}")
    return lines


def render(status: Mapping[str, Any], record: Mapping[str, Any] | None, mark: Mapping[str, Any] | None) -> str:
    if not status["recorded"]:
        head = "venue contract: none recorded"
    else:
        age = status["age_seconds"]
        age_text = f"{int(age // 60)}m old" if isinstance(age, (int, float)) else "age unknown"
        head = (f"venue contract: {status['status']} at {status['verified_at']} ({age_text}, "
                f"{status['contract_version']})")
        if not status["version_current"]:
            head += f" - NOT this code's version ({venue_contract.CONTRACT_VERSION})"
        elif status["stale"]:
            head += f" - STALE (a PASS stands {venue_contract.MAX_AGE_SECONDS // 3600}h)"
        head += " - usable" if status["usable"] else " - not usable: mainnet entries are refused"
    lines = [head]
    if record:
        lines += _render_checks(record.get("checks"))
        lines += [f"  not verified: {item}" for item in record.get("not_verified") or []]
    if mark:
        lines.append(f"last attempt: {mark.get('attempted_at')} - {venue_contract.status_line(mark)}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    force_utf8_io()
    parser = argparse.ArgumentParser(prog="venue_contract", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--show", action="store_true", help="print the record and write nothing (default)")
    parser.add_argument("--run", action="store_true",
                        help="ask the venue now (signed; in the container as uid 10001: "
                             "docker exec -u 10001 thomas-scheduler python -m scripts.venue_contract --run)")
    parser.add_argument("--root", type=Path, default=None, help="state root (defaults to the repo)")
    args = parser.parse_args(argv)
    root: Path | None = args.root
    now = timeutil.utc_now_iso()

    if args.run:
        try:
            assert_not_foreign_root_run(root)
        except MvpRuntimeError as exc:
            print(f"BLOCKED: {getattr(exc, 'reason_code', 'UNKNOWN')}: {exc}", file=sys.stderr)
            return EXIT_BLOCKED
        from runtime.mvp_runtime.crypto.market_data import select_market_data_collector

        line = venue_contract.refresh_verification(
            collector=select_market_data_collector(now=now, root=root), now=now, root=root)
        print(line)

    try:
        record = venue_contract.read_verification(root)
        status = venue_contract.verification_status(root, now=timeutil.utc_now_iso())
    except MvpRuntimeError as exc:
        print(f"BLOCKED: {getattr(exc, 'reason_code', 'UNKNOWN')}: {exc}", file=sys.stderr)
        return EXIT_BLOCKED
    mark = venue_contract.read_refresh_mark(root)
    print(render(status, record, mark))
    if not args.run:
        return EXIT_OK
    # Judged by what this run decided, not by the record: a run that could not ask leaves the last
    # decided record in place, and printing that as this run's answer would claim a check nobody made.
    if not (isinstance(mark, Mapping) and mark.get("attempted_at") == now
            and mark.get("outcome") == venue_contract.OUTCOME_DECIDED):
        print("NOT verified by this run - see the last attempt above.", file=sys.stderr)
        return EXIT_BLOCKED
    return EXIT_OK if mark.get("status") == venue_contract.STATUS_PASS else EXIT_FAIL


if __name__ == "__main__":
    raise SystemExit(main())

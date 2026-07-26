"""The operator's door to event pairing — propose, confirm, list, retire.

    python -m runtime.mvp_runtime.predmarket.pairs_cli propose
    python -m runtime.mvp_runtime.predmarket.pairs_cli confirm \\
        --leg kalshi:KALSHI-TICKER --leg polymarket:TOKEN-ID \\
        --criteria "both settle on the official BLS CPI release for the same month"
    python -m runtime.mvp_runtime.predmarket.pairs_cli add-leg <event_id> --leg kalshi:TICKER \\
        --criteria "this venue resolves on the same release, one day later"
    python -m runtime.mvp_runtime.predmarket.pairs_cli list [--json] [--all]
    python -m runtime.mvp_runtime.predmarket.pairs_cli retire <event_id> --reason "..."

``propose`` reads both venues (through the gate — the mock by default, so this runs on any
machine with no grant), judges every cross-venue pairing deterministically, and prints the
candidates with their score breakdown plus the near-misses. **It confirms nothing.**

The unit is an **event group**, not a pair: one confirmation covers however many venues quote
the event, and ``add-leg`` extends it when a new venue appears. The operator's work therefore
grows with the number of venues, not with the number of pairings between them.

``confirm`` is the only thing that creates a group, and it demands the one input no algorithm
can supply: a note comparing how the venues *resolve* the event. That is the risk the
whole strategy turns on — two markets can ask the same question and settle differently — and
a wrong pair does not error, it manufactures arbitrage forever.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from ..cli_common import EXIT_BLOCKED, EXIT_OK, EXIT_USAGE, force_utf8_io, report_block
from ..errors import MvpRuntimeError
from . import matching, pairs
from .market_data import (
    DEFAULT_MARKET_LIMIT,
    KALSHI,
    POLYMARKET,
    collect_pred_markets,
    degraded_pred_market_record,
    PREDMARKET_DEGRADED,
    PredMarket,
    VenueQuote,
    select_pred_market_collector,
)


def _markets_of(snapshot: dict[str, Any]) -> list[PredMarket]:
    """Rebuild the typed markets the matcher takes from a collection snapshot."""
    rebuilt: list[PredMarket] = []
    for row in snapshot.get("markets") or []:
        quote = row.get("quote") or {}
        rebuilt.append(PredMarket(
            venue=row.get("venue"),
            market_id=row.get("market_id"),
            group_id=row.get("group_id"),
            title=row.get("title") or "",
            close_time=row.get("close_time"),
            status=row.get("status"),
            category=row.get("category"),
            fee_rate_bps=row.get("fee_rate_bps"),
            quote=VenueQuote(
                yes_bid=quote.get("yes_bid"), yes_ask=quote.get("yes_ask"),
                yes_bid_size=quote.get("yes_bid_size"), yes_ask_size=quote.get("yes_ask_size"),
            ),
        ))
    return rebuilt


def _read_venue(venue: str, *, limit: int, now: str) -> tuple[list[PredMarket], str | None]:
    """One venue's markets, or an empty list plus a degrade reason. Never raises.

    A venue being unreadable is not a failed command: proposing from one venue alone yields
    no candidates, which is the honest answer, and the reason is printed rather than hidden.
    """
    collector = select_pred_market_collector(venue)
    try:
        snapshot, _record = collect_pred_markets(venue, collector=collector, now=now, limit=limit)
    except MvpRuntimeError as exc:
        degraded_pred_market_record(collector, venue, PREDMARKET_DEGRADED, now=now)
        return [], exc.reason_code
    return _markets_of(snapshot), None


def _cmd_propose(args: argparse.Namespace) -> int:
    now = pairs.now_iso()
    kalshi, kalshi_error = _read_venue(KALSHI, limit=args.limit, now=now)
    poly, poly_error = _read_venue(POLYMARKET, limit=args.limit, now=now)

    result = matching.generate_candidates(kalshi, poly)
    # Already-paired markets are not proposals — showing them again would invite a duplicate
    # confirmation the store would only refuse.
    taken = pairs.grouped_market_keys(pairs.read_groups())
    result["candidates"] = [
        c for c in result["candidates"]
        if f"{KALSHI}:{c['kalshi_market_id']}" not in taken
        and f"{POLYMARKET}:{c['polymarket_market_id']}" not in taken
    ]

    if args.json:
        sys.stdout.write(json.dumps(
            {**result, "kalshi_error": kalshi_error, "polymarket_error": poly_error},
            ensure_ascii=False, indent=1) + "\n")
        return EXIT_OK

    for venue, error in ((KALSHI, kalshi_error), (POLYMARKET, poly_error)):
        if error:
            sys.stdout.write(f"DEGRADED {venue}: {error} (no candidates from this venue)\n")
    sys.stdout.write(matching.candidate_status_line(result) + "\n")
    for row in result["candidates"]:
        sys.stdout.write(
            f"\nCANDIDATE similarity={row['title_similarity']} "
            f"close_delta_h={row['close_delta_hours']}\n"
            f"  kalshi     : {row['kalshi_market_id']}  {row['kalshi_title']}\n"
            f"  polymarket : {row['polymarket_market_id']}  {row['polymarket_title']}\n"
            f"  shared     : {', '.join(row['shared_tokens']) or '-'}\n"
            f"  confirm    : pairs_cli confirm --leg {KALSHI}:{row['kalshi_market_id']} "
            f"--leg {POLYMARKET}:{row['polymarket_market_id']} --criteria \"...\"\n"
        )
    if args.near_misses and result["near_misses"]:
        sys.stdout.write("\n-- near misses (why the rules refused; the input for fixing them) --\n")
        for row in result["near_misses"][: args.near_miss_limit]:
            sys.stdout.write(
                f"  {row['title_similarity']:>6} {','.join(row['refusals'])}\n"
                f"    {row['kalshi_title']}\n    {row['polymarket_title']}\n"
                f"    unshared: {', '.join(row['unshared_tokens']) or '-'}\n"
            )
    sys.stdout.write("\nNothing above is a group. Confirmation is yours, per event.\n")
    return EXIT_OK


def _parse_leg(spec: str) -> dict[str, str]:
    """``venue:market_id`` — the venue is explicit because a market id alone cannot say
    which book it belongs to, and guessing would put a leg on the wrong venue's fee table."""
    venue, _, market_id = str(spec).partition(":")
    if not venue or not market_id:
        raise MvpRuntimeError("INVALID_LEG", f"--leg must be venue:market_id, got {spec!r}")
    return {"venue": venue.strip().lower(), "market_id": market_id.strip()}


def _cmd_confirm(args: argparse.Namespace) -> int:
    record = pairs.build_event_group(
        legs=[_parse_leg(spec) for spec in args.leg],
        criteria_note=args.criteria,
        confirmed_by=args.by,
        now=pairs.now_iso(),
    )
    stored = pairs.confirm_group(record)
    sys.stdout.write(pairs.group_status_line(stored) + "\n")
    sys.stdout.write(
        "Recorded. This authorizes OBSERVATION of the group, and nothing else — "
        "no paper position, no order.\n"
    )
    return EXIT_OK


def _cmd_add_leg(args: argparse.Namespace) -> int:
    updated = pairs.add_leg(
        args.event_id, _parse_leg(args.leg), criteria_note=args.criteria,
        added_by=args.by, now=pairs.now_iso(),
    )
    sys.stdout.write(pairs.group_status_line(updated) + "\n")
    return EXIT_OK


def _cmd_list(args: argparse.Namespace) -> int:
    rows = pairs.read_groups(include_retired=args.all)
    if args.json:
        sys.stdout.write(json.dumps(rows, ensure_ascii=False, indent=1) + "\n")
        return EXIT_OK
    if not rows:
        sys.stdout.write("no confirmed event groups yet\n")
        return EXIT_OK
    for row in rows:
        sys.stdout.write(pairs.group_status_line(row) + "\n")
        sys.stdout.write(f"  criteria: {row.get('resolution_criteria_note')}\n")
    return EXIT_OK


def _cmd_retire(args: argparse.Namespace) -> int:
    retired = pairs.retire_group(
        args.event_id, reason=args.reason, retired_by=args.by, now=pairs.now_iso()
    )
    sys.stdout.write(pairs.group_status_line(retired) + "\n")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="predmarket-pairs",
        description="Propose, confirm, extend, list and retire cross-venue event groups (PM1).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    propose = sub.add_parser("propose", help="judge cross-venue pairings; confirms nothing")
    propose.add_argument("--limit", type=int, default=DEFAULT_MARKET_LIMIT)
    propose.add_argument("--json", action="store_true")
    propose.add_argument("--near-misses", action="store_true", default=True)
    propose.add_argument("--near-miss-limit", type=int, default=10)
    propose.set_defaults(handler=_cmd_propose)

    confirm = sub.add_parser("confirm", help="record ONE operator-confirmed event group")
    confirm.add_argument(
        "--leg", action="append", required=True, metavar="VENUE:MARKET_ID",
        help="repeat for each venue quoting this event (at least two)",
    )
    confirm.add_argument(
        "--criteria", required=True,
        help="how the venues resolve this event — the check no algorithm can make",
    )
    confirm.add_argument("--by", default="thomas")
    confirm.set_defaults(handler=_cmd_confirm)

    extend = sub.add_parser("add-leg", help="add a new venue's leg to a confirmed group")
    extend.add_argument("event_id")
    extend.add_argument("--leg", required=True, metavar="VENUE:MARKET_ID")
    extend.add_argument("--criteria", required=True, help="how THIS venue resolves the event")
    extend.add_argument("--by", default="thomas")
    extend.set_defaults(handler=_cmd_add_leg)

    listing = sub.add_parser("list", help="confirmed event groups")
    listing.add_argument("--json", action="store_true")
    listing.add_argument("--all", action="store_true", help="include retired pairs")
    listing.set_defaults(handler=_cmd_list)

    retire = sub.add_parser("retire", help="withdraw a group that turned out to be wrong")
    retire.add_argument("event_id")
    retire.add_argument("--reason", required=True)
    retire.add_argument("--by", default="thomas")
    retire.set_defaults(handler=_cmd_retire)
    return parser


def main(argv: list[str] | None = None) -> int:
    force_utf8_io()
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse already reported the usage problem
        return EXIT_USAGE if exc.code else EXIT_OK
    try:
        return int(args.handler(args))
    except MvpRuntimeError as exc:
        return report_block(exc)


if __name__ == "__main__":  # pragma: no cover - entry point
    raise SystemExit(main())

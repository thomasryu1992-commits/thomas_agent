"""The operator's door to PM1 — propose, confirm, list, retire, and read the result.

    python -m runtime.mvp_runtime.predmarket.pairs_cli propose
    python -m runtime.mvp_runtime.predmarket.pairs_cli confirm \\
        --leg kalshi:KALSHI-TICKER --leg polymarket:TOKEN-ID \\
        --criteria "both settle on the official BLS CPI release for the same month"
    python -m runtime.mvp_runtime.predmarket.pairs_cli add-leg <event_id> --leg kalshi:TICKER \\
        --criteria "this venue resolves on the same release, one day later"
    python -m runtime.mvp_runtime.predmarket.pairs_cli list [--json] [--all]
    python -m runtime.mvp_runtime.predmarket.pairs_cli retire <event_id> --reason "..."
    python -m runtime.mvp_runtime.predmarket.pairs_cli report [--json]
    python -m runtime.mvp_runtime.predmarket.pairs_cli resolutions [--json]
    python -m runtime.mvp_runtime.predmarket.pairs_cli sheet [--out sheet.md]

``sheet`` is the one aimed squarely at the bottleneck. The pipeline proposes thirty-odd
pairings every six hours and **none of them becomes data until a human decides two venues
settle the same event the same way** — a judgement that needs the venues' own settlement
text, which discovery already fetches and used to throw away. It prints the candidates with
both texts side by side and a runnable ``confirm`` command per pairing. It compares nothing:
two venues can publish near-identical wording and still settle differently on the same news,
so the reading is made easy and the judgement is left where it belongs.

``report`` is the far end of the same workflow and lives here rather than behind a second
entry point: propose -> confirm -> the scheduler observes -> read what it found. It answers
the three questions PM1 exists for — how often, how large, and **how long** — and says
plainly whether the window it had is the exit artifact or a progress check.

``resolutions`` asks the fourth question, the one the price path structurally cannot: **how
did each leg end, and did the two venues agree?** Every read here is an open-market read —
a settled market does not resolve in it, it disappears from it — so this goes through its own
call, and a group whose venues settled differently is the finding it exists to surface. That
is the risk the whole cross-venue strategy turns on, and it is never resolved automatically:
which venue was *right* is a reading of two settlement texts, the same judgement that
confirmed the group.

``propose`` reads both venues (through the gate — the mock by default, so this runs on any
machine with no grant), **screens out the markets this pipeline cannot use**, judges every
remaining cross-venue pairing deterministically, and prints the candidates with their score
breakdown plus the near-misses. **It confirms nothing.**

The screen is reported, not applied in silence: every run prints how many markets each venue
listed, how many survived, and the reason counts for the rest (``screening.py``). A proposal
list that came back empty because every market was a parlay is a different finding from one
that came back empty because nothing matched, and only that line distinguishes them.

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
from pathlib import Path
from typing import Any, Sequence

from ..cli_common import EXIT_BLOCKED, EXIT_OK, EXIT_USAGE, force_utf8_io, report_block
from ..errors import MvpRuntimeError
from . import matching, observations, pairs, proposals, report, screening
from .market_data import (
    DEFAULT_MARKET_LIMIT,
    DISCOVERY_MARKET_LIMIT,
    VENUES,
    rotation_index,
    collect_pred_markets,
    degraded_pred_market_record,
    is_synthetic_snapshot,
    PREDMARKET_DEGRADED,
    PredMarket,
    SYNTHETIC_SOURCE,
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
            derived_from=(
                tuple(str(x) for x in row["derived_from"])
                if isinstance(row.get("derived_from"), (list, tuple)) else None
            ),
            accepting_orders=row.get("accepting_orders"),
            resolution_rules=row.get("resolution_rules"),
            volume=row.get("volume"),
            quote=VenueQuote(
                yes_bid=quote.get("yes_bid"), yes_ask=quote.get("yes_ask"),
                yes_bid_size=quote.get("yes_bid_size"), yes_ask_size=quote.get("yes_ask_size"),
            ),
        ))
    return rebuilt


def _read_venue(
    venue: str, *, limit: int, now: str, min_horizon_hours: float, root: Path | None = None,
    rotation: int = 0,
) -> tuple[list[PredMarket], dict[str, Any], str | None, set[str]]:
    """One venue's *screenable* markets, its screen result, and any degrade reason.

    Never raises: a venue being unreadable is not a failed command. Proposing from one venue
    alone yields no candidates, which is the honest answer, and the reason is printed rather
    than hidden.

    The horizon is pushed to the venue where it supports one and re-applied here regardless,
    so the printed exclusion counts describe what actually came back rather than what we
    asked for.
    """
    # `root` reaches the Safety-Flag gate, which resolves each venue's grant relative to
    # it. Accepting the argument and not passing it on left the gate falling back to
    # repo-root detection — correct in the deployed container by luck, and untestable
    # against any other state dir.
    collector = select_pred_market_collector(
        venue,
        min_close_time=screening.min_close_iso(now=now, min_horizon_hours=min_horizon_hours),
        root=root,
        rotation=rotation,
    )
    try:
        snapshot, _record = collect_pred_markets(
            venue, collector=collector, now=now, limit=limit, with_quotes=False)
    except MvpRuntimeError as exc:
        degraded_pred_market_record(collector, venue, PREDMARKET_DEGRADED, now=now)
        return [], screening.screen_markets([], now=now), exc.reason_code, set()
    if is_synthetic_snapshot(snapshot):
        # Degraded exactly like an unreadable venue, and this door matters more than the scan's:
        # a confirmation is durable. The mocks deliberately carry the *same titles at different
        # prices*, which is the shape the matcher is built to find — so a both-venues-on-mock
        # propose run yields clean-looking candidates out of fabricated data, and confirming one
        # would write mock ids into the group store, where every later scan re-reads them
        # forever. A rehearsal must not be able to author a confirmed pair.
        #
        # No tail either: the rotation window is a slice of a corpus this run never read.
        degraded_pred_market_record(collector, venue, SYNTHETIC_SOURCE, now=now)
        return [], screening.screen_markets([], now=now), SYNTHETIC_SOURCE, set()
    screened = screening.screen_markets(
        _markets_of(snapshot), now=now, min_horizon_hours=min_horizon_hours
    )
    tail = {f"{venue}:{mid}" for mid in (snapshot.get("tail_market_ids") or [])}
    return list(screened["observable"]), screened, None, tail


def run_discovery(
    *,
    now: str,
    limit: int = DISCOVERY_MARKET_LIMIT,
    min_horizon_hours: float | None = None,
    venues: Sequence[str] | None = None,
    root: Path | None = None,
    rotation: int | None = None,
    with_rules: bool = False,
) -> dict[str, Any]:
    """Read every venue, screen, and judge every cross-venue pairing. Confirms nothing.

    The whole of ``propose`` except the printing, so the scheduled discovery scan and the
    operator's command cannot drift into proposing different things. Returns the matcher's
    result with the screen, the venue errors, and the already-grouped markets removed.
    """
    horizon = screening.MIN_HORIZON_HOURS if min_horizon_hours is None else float(min_horizon_hours)
    wanted = [v for v in (venues or VENUES) if v in VENUES]
    # One rotation index for the whole run, derived from the clock. Every venue advances its
    # window together, so a run is one coherent slice of the corpus rather than three
    # unrelated ones drifting apart.
    turn = rotation_index(now) if rotation is None else int(rotation)

    markets: dict[str, list[PredMarket]] = {}
    screens: dict[str, dict[str, Any]] = {}
    errors: dict[str, str] = {}
    tail_keys: set[str] = set()
    for venue in wanted:
        observable, screen, error, tail = _read_venue(
            venue, limit=limit, now=now, min_horizon_hours=horizon, root=root, rotation=turn)
        markets[venue], screens[venue] = observable, screen
        tail_keys |= tail
        if error:
            errors[venue] = error

    result = matching.generate_candidates(markets)
    # Carried into the result so `--json` and the stored record report it too: a run that
    # judged nothing because every market was screened out is a different finding from one
    # that judged everything and matched nothing, and only the screen tells them apart.
    result["screening"] = {
        venue: {k: v for k, v in screen.items() if k != "observable"}
        for venue, screen in screens.items()
    }
    result["venue_errors"] = errors
    result["rotation"] = turn
    # The fee rates this run actually read, keyed `venue:market_id`.
    #
    # Carried because a candidate outlives the listing that proposed it. Binance states its
    # fee only on the listing and shows 40 markets at a time, so a candidate found at 08:05 can
    # be unconfirmable by lunchtime: the rate is gone and `confirm` has nothing to capture. That
    # is not hypothetical — 11 of 17 Binance candidates aged out of confirmability inside
    # eighteen hours, six of them exact title matches.
    #
    # This is the same read #287 makes at confirmation, taken one step earlier in the pipeline,
    # with `generated_at_utc` on the record saying when. It stays a **fallback**: `confirm`
    # prefers a rate the venue states now, and only reaches for a recorded one when the live
    # listing no longer carries it.
    result["fee_rate_bps"] = {
        f"{venue}:{m.market_id}": float(m.fee_rate_bps)
        for venue, rows in markets.items() for m in rows
        if isinstance(m.fee_rate_bps, (int, float)) and not isinstance(m.fee_rate_bps, bool)
    }
    # Which slice each candidate came from, so "was reading past the head worth it?" is
    # answered by the record rather than argued. `tail` means at least ONE leg would not
    # have been read without rotation — which is exactly the pairing rotation paid for.
    for candidate in result["candidates"] + result["near_misses"]:
        from_tail = (
            f"{candidate['left_venue']}:{candidate['left_market_id']}" in tail_keys
            or f"{candidate['right_venue']}:{candidate['right_market_id']}" in tail_keys
        )
        candidate["discovery_slice"] = "tail" if from_tail else "head"

    if with_rules:
        # Opt-in, and the scheduled scan does not ask. Settlement text is a kilobyte of prose
        # per leg; carrying it into a store written four times a day would add megabytes a
        # year to records nobody reads it from. The sheet asks; the cadence does not.
        rules = {f"{m.venue}:{m.market_id}": m.resolution_rules
                 for venue_markets in markets.values() for m in venue_markets}
        for candidate in result["candidates"]:
            for side in ("left", "right"):
                key = f"{candidate[f'{side}_venue']}:{candidate[f'{side}_market_id']}"
                candidate[f"{side}_resolution_rules"] = rules.get(key)
    # Already-paired markets are not proposals — showing them again would invite a duplicate
    # confirmation the store would only refuse.
    all_groups = pairs.read_groups(root, include_retired=True)
    taken = pairs.grouped_market_keys(
        [g for g in all_groups if g.get("status") != pairs.RETIRED])
    result["candidates"] = [
        c for c in result["candidates"]
        if f"{c['left_venue']}:{c['left_market_id']}" not in taken
        and f"{c['right_venue']}:{c['right_market_id']}" not in taken
    ]

    # A pairing an operator already looked at and rejected is not a new proposal. It is not
    # dropped either — a retirement reason can be about a moment ("the venue was down")
    # rather than about the pairing, so the row survives with the reason attached and the
    # operator can act on it again knowingly. What must not happen is what did: two retired
    # pairings returning to the TOP of the sheet, because a pair retired for quoting 0.73
    # against 0.0015 still scores 1.0 on wording.
    retired = pairs.retired_pairing_reasons(all_groups)
    still_open, previously_retired = [], []
    for candidate in result["candidates"]:
        key = frozenset({
            f"{candidate['left_venue']}:{candidate['left_market_id']}",
            f"{candidate['right_venue']}:{candidate['right_market_id']}",
        })
        detail = retired.get(key)
        if detail is None:
            still_open.append(candidate)
        else:
            previously_retired.append({**candidate, "previously_retired": detail})
    result["candidates"] = still_open
    result["previously_retired"] = previously_retired
    return result


def _cmd_propose(args: argparse.Namespace) -> int:
    now = pairs.now_iso()
    venues = [v for v in (args.venue or VENUES) if v in VENUES]
    result = run_discovery(
        now=now, limit=args.limit, min_horizon_hours=args.min_horizon_hours, venues=venues)
    screens = result["screening"]
    errors = result["venue_errors"]

    if args.json:
        sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=1) + "\n")
        return EXIT_OK

    for venue in venues:
        if venue in errors:
            sys.stdout.write(f"DEGRADED {venue}: {errors[venue]} (no candidates from this venue)\n")
        sys.stdout.write(f"{venue:<11}: {screening.screening_status_line(screens[venue])}\n")
    sys.stdout.write(matching.candidate_status_line(result) + "\n")
    for row in result["candidates"]:
        sys.stdout.write(
            f"\nCANDIDATE similarity={row['title_similarity']} "
            f"close_delta_h={row['close_delta_hours']}\n"
            f"  {row['left_venue']:<10} : {row['left_market_id']}  {row['left_title']}\n"
            f"  {row['right_venue']:<10} : {row['right_market_id']}  {row['right_title']}\n"
            f"  shared     : {', '.join(row['shared_tokens']) or '-'}\n"
            f"  confirm    : pairs_cli confirm "
            f"--leg {row['left_venue']}:{row['left_market_id']} "
            f"--leg {row['right_venue']}:{row['right_market_id']} --criteria \"...\"\n"
        )
    if args.near_misses and result["near_misses"]:
        sys.stdout.write("\n-- near misses (why the rules refused; the input for fixing them) --\n")
        for row in result["near_misses"][: args.near_miss_limit]:
            sys.stdout.write(
                f"  {row['title_similarity']:>6} {','.join(row['refusals'])}\n"
                f"    {row['left_venue']}: {row['left_title']}\n"
                f"    {row['right_venue']}: {row['right_title']}\n"
                f"    unshared: {', '.join(row['unshared_tokens']) or '-'}\n"
            )
        if result["near_miss_truncated"]:
            sys.stdout.write(
                f"  ... {result['near_miss_truncated']} more near-miss(es) not carried "
                f"(of {result['near_miss_total']} total)\n"
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


def _stated_fee_rates(venues: Sequence[str], *, now: str, root: Path | None = None) -> dict[str, float]:
    """``{"venue:market_id": bps}`` for every market a fee rate is known for, live first.

    Two sources, in that precedence: what a discovery run recorded, then what the venue states
    **now** — so a live read always wins and the recorded one only fills what rotation has
    carried away. Both are real reads; they differ only in age, which `generated_at_utc` on the
    proposal record states.

    Read from the **listing** endpoint, which is the only one that carries it. The watch scan
    re-reads a confirmed leg by id — one order-book call, and that response is a price and
    nothing else — so if the rate is not captured here it is never available again, and the
    leg's cost stays unknowable for the life of the group.

    Best-effort on purpose. A venue that will not answer must not make a confirmation
    impossible: the operator has done the judgement this command exists for, and refusing it
    over a transient read would push them to retry until it worked, which teaches the wrong
    reflex about a step whose whole value is deliberation. A leg with no captured rate simply
    behaves as it did before this existed.
    """
    # Rates a discovery run recorded, oldest first, so a live read below overwrites them.
    # This is the half that keeps a candidate confirmable after the listing rotated past it —
    # without it the operator races a window they cannot see, and loses: 11 of 17 Binance
    # candidates aged out inside eighteen hours, six of them exact title matches.
    rates: dict[str, float] = {}
    try:
        for row in proposals.read_proposals(root):
            for key, bps in (row.get("fee_rate_bps") or {}).items():
                if isinstance(bps, (int, float)) and not isinstance(bps, bool):
                    rates[str(key)] = float(bps)
    except MvpRuntimeError:
        pass                                   # no proposals yet, or unreadable: live read only
    for venue in dict.fromkeys(venues):
        try:
            collector = select_pred_market_collector(venue, now=now, root=root)
            snapshot, _ = collect_pred_markets(
                venue, collector=collector, now=now,
                limit=DISCOVERY_MARKET_LIMIT, with_quotes=False,
            )
        except MvpRuntimeError:
            continue
        for row in snapshot.get("markets") or []:
            bps = row.get("fee_rate_bps")
            if isinstance(bps, (int, float)) and not isinstance(bps, bool):
                rates[f"{row.get('venue')}:{row.get('market_id')}"] = float(bps)
    return rates


def _cmd_confirm(args: argparse.Namespace) -> int:
    now = pairs.now_iso()
    legs = [_parse_leg(spec) for spec in args.leg]
    rates = _stated_fee_rates([leg["venue"] for leg in legs], now=now)
    for leg in legs:
        bps = rates.get(f"{leg['venue']}:{leg['market_id']}")
        if bps is not None:
            leg["fee_rate_bps"] = bps
            leg["fee_rate_read_at"] = now
    record = pairs.build_event_group(
        legs=legs,
        criteria_note=args.criteria,
        confirmed_by=args.by,
        now=now,
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


def _cmd_report(args: argparse.Namespace) -> int:
    built = report.build_pm1_report(
        now=pairs.now_iso(), max_gap_seconds=args.max_gap_seconds, since=args.since)
    if args.json:
        sys.stdout.write(json.dumps(built, ensure_ascii=False, indent=1) + "\n")
        return EXIT_OK
    sys.stdout.write(report.render_pm1_report_text(built) + "\n")
    return EXIT_OK


def _cmd_resolutions(args: argparse.Namespace) -> int:
    """Read-only, like `report`. Asks the venues how each confirmed leg ended and prints the
    groups where they disagree first — that disagreement is the risk the whole cross-venue
    strategy turns on, and it is the one finding here that needs an operator."""
    sweep = observations.run_resolution_sweep(now=pairs.now_iso())
    if args.json:
        sys.stdout.write(json.dumps(sweep, ensure_ascii=False, indent=1) + "\n")
        return EXIT_OK
    sys.stdout.write(observations.resolution_status_line(sweep) + "\n")
    for venue, code in sorted((sweep.get("venue_errors") or {}).items()):
        sys.stdout.write(f"  DEGRADED {venue}: {code} (its legs report no settlement)\n")
    for group in sweep.get("groups") or []:
        if not group.get("settled_legs"):
            continue      # nothing has ended here; the sweep's headline already counted it
        flag = "MISMATCH" if group.get("mismatch") else "agreed" if group.get("comparable") else "partial"
        sys.stdout.write(f"\n  {group.get('event_id')}  [{flag}]\n")
        for leg in group.get("legs") or []:
            settlement = leg.get("resolution") or {}
            state = leg.get("unreadable") or settlement.get("outcome") or "not read"
            word = settlement.get("venue_word")
            paid = settlement.get("yes_value")
            sys.stdout.write(
                f"    {leg['venue']:<11} {state:<13} "
                f"yes paid {'?' if paid is None else paid}"
                f"{f'  ({word})' if word else ''}\n")
    sys.stdout.write(
        "\nObservation only. This report authorizes no position and settles no trade.\n")
    return EXIT_OK


def _cmd_sheet(args: argparse.Namespace) -> int:
    now = pairs.now_iso()
    result = run_discovery(now=now, limit=args.limit, with_rules=True)
    sheet = proposals.render_confirmation_sheet(result, now=now)
    if args.out:
        Path(args.out).write_text(sheet, encoding="utf-8")
        sys.stdout.write(f"wrote {len(result['candidates'])} candidate(s) to {args.out}\n")
        # The count alone is not a finding. "wrote 0" reads as a quiet market, and the run
        # that produced it here had simply failed to reach Polymarket eight seconds after a
        # restart — the sheet said so, on its own first page, and the console did not. A
        # module built so that an empty result can never be mistaken for an empty market
        # should not hide that behind a file path.
        for venue, screen in sorted((result.get("screening") or {}).items()):
            sys.stdout.write(f"  {venue:<11}: {screening.screening_status_line(screen)}\n")
        for venue, code in sorted((result.get("venue_errors") or {}).items()):
            sys.stdout.write(f"  DEGRADED {venue}: {code} (no candidates from this venue)\n")
        retired = result.get("previously_retired") or []
        if retired:
            sys.stdout.write(
                f"  {len(retired)} previously-retired pairing(s) held back, listed at the end "
                f"with your reason\n")
        return EXIT_OK
    sys.stdout.write(sheet)
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="predmarket-pairs",
        description="Propose, confirm, extend, list and retire cross-venue event groups (PM1).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    propose = sub.add_parser("propose", help="judge cross-venue pairings; confirms nothing")
    propose.add_argument("--limit", type=int, default=DISCOVERY_MARKET_LIMIT)
    propose.add_argument(
        "--venue", action="append", choices=sorted(VENUES),
        help=("repeat to restrict which venues are read; default is all of them. Every "
              "cross-venue pairing among those read is judged."),
    )
    propose.add_argument("--json", action="store_true")
    propose.add_argument("--near-misses", action="store_true", default=True)
    propose.add_argument("--near-miss-limit", type=int, default=10)
    propose.add_argument(
        "--min-horizon-hours", type=float, default=screening.MIN_HORIZON_HOURS,
        help=("how much life a market must have left to be proposed (default "
              f"{screening.MIN_HORIZON_HOURS}h — long enough to survive your review). "
              "Pass 0 to see everything the venues list."),
    )
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

    reporting = sub.add_parser("report", help="what the observations said: how often, how large, how long")
    reporting.add_argument("--json", action="store_true")
    reporting.add_argument(
        "--since", default=None, metavar="UTC_ISO",
        help="only count observations at or after this instant (e.g. 2026-07-29T04:00:00Z). "
             "The rules that produce a row have changed while rows accumulated — what counted "
             "as a reading, whether a Binance leg could be priced, twice what counted as an "
             "opportunity — and frequency computed across a rule change is a number about two "
             "instruments. Filters; never deletes. The report says on its face that it is bounded")
    reporting.add_argument(
        "--max-gap-seconds", type=float, default=report.DEFAULT_MAX_GAP_SECONDS,
        help=("how long a hole between two readings still counts as one episode (default "
              f"{report.DEFAULT_MAX_GAP_SECONDS}s). Beyond it the episode is cut and marked: "
              "seeing an edge at 09:00 and again at 11:00 is not evidence it was there at 10:00."),
    )
    reporting.set_defaults(handler=_cmd_report)

    settlements = sub.add_parser(
        "resolutions", help="how each confirmed group's legs settled, and where they disagree")
    settlements.add_argument("--json", action="store_true")
    settlements.set_defaults(handler=_cmd_resolutions)

    sheet = sub.add_parser(
        "sheet", help="candidates with both venues' settlement text, ready to confirm")
    sheet.add_argument("--limit", type=int, default=DISCOVERY_MARKET_LIMIT)
    sheet.add_argument("--out", help="write the markdown here instead of stdout")
    sheet.set_defaults(handler=_cmd_sheet)
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

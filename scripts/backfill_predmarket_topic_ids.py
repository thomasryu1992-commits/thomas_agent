"""Backfill the Binance ``topic_id`` on event groups confirmed before it was captured.

Operator step, and a **time-boxed** one. A Binance leg is stored as ``marketId:tokenId``,
everything the order book needs; ``market/detail`` — the only call that reports a settlement —
is keyed by ``marketTopicId``, and no arithmetic gets from one to the other. Groups confirmed
before that id was captured therefore have unreadable settlements, and PM2 holds positions to
settlement.

**The window closes on its own.** ``market/list`` serves only ``REGISTERED`` topics — measured
2026-07-31, none of 400 was past its end date — so the only way to map an old leg back to its
topic is to walk the live listing while the topic is still on it. A topic that rotates off
takes its mapping with it permanently. Run this while the markets are open, not when PM2 wants
the answer.

    docker exec thomas-scheduler python -m scripts.backfill_predmarket_topic_ids --dry-run
    docker exec thomas-scheduler python -m scripts.backfill_predmarket_topic_ids --apply --by thomas

**This grants nothing and trades nothing.** It adds one identifier to a record an operator
already confirmed, and the identifier does not enter ``event_id`` — the id derives from
``venue:market_id`` alone — so every group keeps its identity and every observation already
recorded against it stays attached. That is what makes the backfill safe to run on a live
window rather than a migration.

The walk costs one signed call per listing page plus one per topic, all reads, against
endpoints weighted 200 and sharing a key with the running scan. ``--max-topics`` bounds it;
the default is deliberately modest and the run reports what it did not reach, because a
backfill that silently stopped early is indistinguishable from one that found nothing.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Mapping

from runtime.mvp_runtime.errors import MvpRuntimeError, ToolError
from runtime.mvp_runtime.predmarket import market_data as md
from runtime.mvp_runtime.predmarket import pairs
from runtime.mvp_runtime.state_guard import assert_not_foreign_root_run

DEFAULT_MAX_TOPICS = 400


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="backfill_predmarket_topic_ids",
        description="map confirmed Binance legs back to their market topic, while they are still listed",
    )
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true",
                      help="walk and report, write nothing (run this first)")
    mode.add_argument("--apply", action="store_true", help="write the topic ids onto the groups")
    p.add_argument("--by", default="thomas", help="who ran it, recorded on each updated group")
    p.add_argument("--max-topics", type=int, default=DEFAULT_MAX_TOPICS,
                   help=f"cap on topics to read detail for (default {DEFAULT_MAX_TOPICS})")
    return p.parse_args(argv)


def legs_needing_topic_id(groups: list[Mapping[str, Any]]) -> dict[str, list[str]]:
    """``{event_id: [market_id, ...]}`` for confirmed Binance legs with no ``topic_id``.

    Retired groups are skipped: they are a correction record, and re-reading a pairing an
    operator withdrew would spend signed calls to enrich something nothing observes.
    """
    wanted: dict[str, list[str]] = {}
    for group in groups:
        if group.get("status") != pairs.CONFIRMED:
            continue
        for leg in group.get("legs") or []:
            if leg.get("venue") != md.BINANCE or leg.get("topic_id"):
                continue
            wanted.setdefault(str(group.get("event_id")), []).append(str(leg["market_id"]))
    return wanted


def discover_topic_ids(
    market_ids: set[str], *, max_topics: int, root: Path | None = None
) -> tuple[dict[str, str], int, bool]:
    """Walk the live listing for the topics holding these legs.

    Returns ``(mapping, topics_read, exhausted)``. ``exhausted`` says the walk ran out of
    listing rather than out of budget — the difference between "these legs are not on the
    venue any more" and "we stopped looking", which a caller must not have to guess.

    Stops early once every wanted leg is found: the remaining pages cost signed calls to
    confirm something already known.
    """
    collector = md.select_pred_market_collector(md.BINANCE, root=root)
    if isinstance(collector, md.MockPredMarketCollector):
        raise ToolError(
            md.SYNTHETIC_SOURCE,
            "the safety-flag gate is closed for binance_prediction; a mock's ids must never "
            "be written onto a confirmed group",
        )
    found: dict[str, str] = {}
    topics_read = 0
    offset = 0
    exhausted = False
    while topics_read < max_topics and len(found) < len(market_ids):
        listing = collector._signed_get(
            collector.LIST_PATH, {"limit": collector.PAGE_LIMIT, "offset": offset},
            timeout_seconds=20,
        )
        topics = md.parse_prediction_topics(listing)
        if not topics:
            exhausted = True
            break
        offset += len(topics)
        for topic in topics:
            if topics_read >= max_topics or len(found) == len(market_ids):
                break
            topics_read += 1
            try:
                detail = collector._signed_get(
                    collector.DETAIL_PATH, {"marketTopicId": topic.market_id},
                    timeout_seconds=20,
                )
            except ToolError:
                continue          # one topic failing is not the walk failing
            for row in md.parse_prediction_markets(detail):
                key = f"{row['market_id']}:{row['token_id']}"
                if key in market_ids:
                    found[key] = topic.market_id
    return found, topics_read, exhausted


def apply_topic_ids(
    mapping: Mapping[str, str], *, by: str, now: str, root: Path | None = None
) -> int:
    """Write the discovered ids onto their groups. Returns how many legs were updated.

    Rewrites through the same lock/`write_objects` path `pairs.add_leg` uses, and re-hashes
    each touched record — a stored group that no longer matches its own hash is refused by
    every reader, so the hash is not optional bookkeeping.
    """
    return pairs.set_leg_topic_ids(mapping, updated_by=by, now=now, root=root)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        assert_not_foreign_root_run()
        groups = pairs.read_groups(include_retired=True)
        wanted = legs_needing_topic_id(groups)
        flat = {market_id for ids in wanted.values() for market_id in ids}
        if not flat:
            sys.stdout.write("every confirmed Binance leg already carries a topic id\n")
            return 0
        sys.stdout.write(
            f"{len(flat)} Binance leg(s) across {len(wanted)} group(s) have no topic id\n")
        found, topics_read, exhausted = discover_topic_ids(flat, max_topics=args.max_topics)
        missing = sorted(flat - set(found))
        sys.stdout.write(
            f"read {topics_read} topic(s) "
            f"({'listing exhausted' if exhausted else 'stopped at --max-topics'}); "
            f"mapped {len(found)}, unmapped {len(missing)}\n")
        for market_id in missing:
            # Named, not counted. An unmapped leg is one whose topic has left the listing, and
            # that is permanent — the operator needs to know which groups can never report a
            # settlement, not that some number of them cannot.
            sys.stdout.write(f"  UNMAPPED {market_id}  (topic no longer listed)\n")
        if args.dry_run:
            sys.stdout.write("dry run: nothing written\n")
            return 0
        updated = apply_topic_ids(found, by=args.by, now=pairs.now_iso())
        sys.stdout.write(f"wrote topic ids onto {updated} leg(s)\n")
        return 0
    except MvpRuntimeError as exc:
        sys.stderr.write(f"BLOCKED {exc.reason_code}: {exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

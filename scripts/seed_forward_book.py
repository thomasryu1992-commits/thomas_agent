#!/usr/bin/env python3
"""Operator tool: seed the per-strategy forward book from each lineage's own mint date.

A spec is frozen at mint, so every bar that closed after it is genuine out-of-sample data
for that lineage — evidence that already exists and was being thrown away while the 5-1
forward clock started from zero at install. This walks those bars through the SAME
transition the live cycle runs (``forward_book.walk_seed_span`` -> ``replay_entry_bar``:
same entry doors, same paper-kernel settlement, same accounting basis) stamping
HISTORICAL timestamps into the rows, so slice-based confirmation sees the true calendar
spread and a re-run mints identical settlement ids that the append-side dup check skips —
seeding is idempotent by the same mechanism that makes settlement retries safe.

**The seeded span and the live span partition the calendar.** The walk stops at the live
stream's own ``first_seen_candle``, so no bar can settle in both streams, and running
this AFTER the live cycle has started (the normal case) backfills exactly the history the
live stream never saw. A position still open at that boundary is dropped un-minted — the
live stream owns the present. Book updates happen per lineage in a tiny read-modify-write
UNDER the book lock (``mutate_book``), touching only this lineage's counters and markers,
never a position or the live freshness watermark — a concurrently running scheduler loses
nothing.

The fetch window includes ``_WARMUP_BARS`` extra bars BEFORE the mint so the indicator
stack (the 100-bar percentile windows are the deepest) is warm by the first post-mint
bar; without them the earliest stretch of the span silently seeds nothing. When
``_MAX_SEED_BARS`` binds, the OLDEST part of the span is what is lost (the venue returns
the newest bars) — the newest evidence matters most to a forward clock.

Dry by default: nothing is written without ``--apply``. Writes runtime state, so it runs
in the container as uid 10001, in module form::

    docker exec thomas-scheduler python -m scripts.seed_forward_book --list
    docker exec thomas-scheduler python -m scripts.seed_forward_book --apply

The mint date is the EARLIEST ``created_at_utc`` across candidate-store rows sharing the
lineage's rule hash — a re-score re-measures an unchanged spec, so the original mint is
the honest start of its out-of-sample span.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime import timeutil  # noqa: E402
from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK  # noqa: E402
from runtime.mvp_runtime.errors import MvpRuntimeError, ToolError  # noqa: E402
from runtime.mvp_runtime.state_guard import assert_not_foreign_root_run  # noqa: E402
from runtime.mvp_runtime.crypto import forward_book, market_data, pool as pool_store  # noqa: E402
from runtime.mvp_runtime.crypto.cycle import attach_mining_legs  # noqa: E402
from runtime.mvp_runtime.crypto.factory import build_replay_frame  # noqa: E402
from runtime.mvp_runtime.crypto.paper import OCCUPYING_STATUSES  # noqa: E402
from runtime.mvp_runtime.crypto.state import state_dir  # noqa: E402
from runtime.mvp_runtime.crypto.strategy import StrategySpec  # noqa: E402

# Enough pre-mint bars that every indicator the entry rules can read is warm by the first
# post-mint bar — the deepest consumer is the 100-bar percentile window; 120 leaves slack.
_WARMUP_BARS = 120
# Beyond this the walk truncates at the OLD end (the venue serves the newest bars); the
# newest evidence matters most to a forward clock.
_MAX_SEED_BARS = 4000
_MIN_SEED_BARS = 30


def _first_seen_by_hash(root: Path) -> dict[str, str]:
    first: dict[str, str] = {}
    path = state_dir(root) / "strategy_candidates.jsonl"
    if not path.is_file():
        return first
    for line in path.open(encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        h = row.get("strategy_rule_hash")
        c = str(row.get("created_at_utc") or "")
        if isinstance(h, str) and h and c and (h not in first or c < first[h]):
            first[h] = c
    return first


def _merge_seed_state(book: dict[str, Any], key: str, seed_state: Mapping[str, Any]) -> None:
    """Fold ONE lineage's seed results into the live book — counters and markers only.

    The live cycle owns ``position``, ``last_seen_candle``, ``first_seen_candle`` and the
    cooldown; the seeder must never move any of them. What seeding adds is the history:
    the earlier ``first_tracked_at`` (the mint), the opens the span produced, and the
    latest signal the span saw."""
    existing = book["entries"].get(key)
    if existing is None:
        merged = dict(seed_state)
        merged["position"] = None
        merged["last_seen_candle"] = None  # the live stream starts wherever it starts
        merged["first_seen_candle"] = None
        merged["cooldown_remaining"] = 0
        book["entries"][key] = merged
        return
    first = [t for t in (existing.get("first_tracked_at"), seed_state.get("first_tracked_at"))
             if isinstance(t, str) and t]
    if first:
        existing["first_tracked_at"] = min(first)
    existing["opens_count"] = int(existing.get("opens_count") or 0) + int(seed_state.get("opens_count") or 0)
    signals = [t for t in (existing.get("last_signal_at"), seed_state.get("last_signal_at"))
               if isinstance(t, str) and t]
    if signals:
        existing["last_signal_at"] = max(signals)


def seed_lineage(
    pool_entry: Mapping[str, Any], spec: StrategySpec, symbol: str, *, mint: str, now: str,
    root: Path, collector: Any, apply: bool,
) -> dict[str, Any]:
    """Walk one (lineage, symbol) context from its mint to the live boundary."""
    timeframe = spec.timeframe
    minutes = market_data.TIMEFRAMES.get(timeframe)
    lineage = forward_book.lineage_key(pool_entry)
    report = {"strategy_id": pool_entry.get("strategy_id"), "symbol": symbol,
              "timeframe": timeframe, "mint": mint[:10], "bars": 0, "opens": 0,
              "settled": 0, "skipped": None}
    if not lineage:
        report["skipped"] = "unattributable (sid-only identity)"
        return report
    if not minutes:
        report["skipped"] = "unknown timeframe"
        return report
    key = forward_book.book_key(lineage, symbol, timeframe)

    age_minutes = (timeutil.parse_iso(now) - timeutil.parse_iso(mint)).total_seconds() / 60.0
    span = int(age_minutes / minutes) + 2
    if span < _MIN_SEED_BARS:
        report["skipped"] = f"only {span} bars since mint"
        return report
    bars = min(_MAX_SEED_BARS, span + _WARMUP_BARS)

    live_state = forward_book.load_book(root)["entries"].get(key) or {}
    boundary = live_state.get("first_seen_candle")

    snapshot, _ = market_data.collect_market_data(
        symbol, timeframe, collector=collector, now=now, limit=bars)
    attach_mining_legs(snapshot, collector=collector, timeframe=timeframe, now=now,
                       root=root, candle_target=lambda _tf: bars)
    frame = build_replay_frame(snapshot)
    report["bars"] = len(frame.candles)

    seed_state, rows = forward_book.walk_seed_span(
        pool_entry, spec, frame.rows, frame.candles,
        symbol=symbol, timeframe=timeframe, mint=mint, boundary=boundary)
    report["opens"] = int(seed_state.get("opens_count") or 0)
    report["settled"] = len(rows)
    if apply:
        forward_book._append_outcomes(rows, root=root)
        forward_book.mutate_book(root, now, lambda book: _merge_seed_state(book, key, seed_state))
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="seed_forward_book",
        description="Seed each occupying lineage's forward stream from its own mint date.")
    parser.add_argument("--strategy-id", action="append", dest="strategy_ids", default=None,
                        help="limit to these pool strategy_ids (repeatable)")
    parser.add_argument("--list", action="store_true", help="report what would seed and exit")
    parser.add_argument("--apply", action="store_true", help="write outcomes and book state")
    args = parser.parse_args(argv)

    try:
        assert_not_foreign_root_run(None)
    except MvpRuntimeError as exc:
        print(f"BLOCKED {exc.reason_code}: {exc.reason}", file=sys.stderr)
        return EXIT_BLOCKED

    root = ROOT
    now = timeutil.utc_now_iso()
    pool = pool_store.load_active_pool(root)
    occupying = [e for e in (pool.get("active_strategies") or [])
                 if e.get("status") in OCCUPYING_STATUSES and e.get("strategy_spec")]
    if args.strategy_ids:
        wanted = set(args.strategy_ids)
        occupying = [e for e in occupying if str(e.get("strategy_id")) in wanted]
    first_seen = _first_seen_by_hash(root)
    collector = market_data.select_market_data_collector(now=now, root=root)

    apply = bool(args.apply and not args.list)
    total_rows = 0
    print("%-16s %-10s %-4s %-11s %6s %6s %7s  %s" % (
        "strategy", "symbol", "tf", "mint", "bars", "opens", "settled", "note"))
    for entry in occupying:
        try:
            spec = StrategySpec.from_dict(entry["strategy_spec"])
        except Exception as exc:
            print("%-16s  spec unparseable: %s" % (entry.get("strategy_id"), exc))
            continue
        mint = first_seen.get(str(entry.get("strategy_rule_hash"))) or str(entry.get("promoted_at") or now)
        for symbol in spec.symbol_scope:
            try:
                r = seed_lineage(entry, spec, symbol, mint=mint, now=now, root=root,
                                 collector=collector, apply=apply)
            except (ToolError, MvpRuntimeError) as exc:
                print("%-16s %-10s %-4s  ERR %s" % (
                    entry.get("strategy_id"), symbol, spec.timeframe,
                    getattr(exc, "reason_code", type(exc).__name__)))
                continue
            total_rows += r["settled"]
            print("%-16s %-10s %-4s %-11s %6d %6d %7d  %s" % (
                r["strategy_id"], r["symbol"], r["timeframe"], r["mint"],
                r["bars"], r["opens"], r["settled"], r["skipped"] or ""))
    if apply:
        print(f"\nseeded {total_rows} settled row(s)")
    else:
        print(f"\nDRY RUN — {total_rows} settled row(s) would be written. Re-run with --apply.")
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

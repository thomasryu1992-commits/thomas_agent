"""Re-score candidates whose holdout block predates ``stdev_r``, so they can be judged.

``robustness.holdout_status`` needs a spread to draw an interval, and every block written
before that field existed reads INSUFFICIENT — not because the tail was too thin, but because
the record cannot say how disperse it was. Measured 2026-08-04 on this machine's store: of the
599 rows carrying **at least** ``MIN_HOLDOUT_TRADES``, **533 have no ``stdev_r``**, so "judgeable"
currently means 66 or 599 depending on which question is being asked. That ambiguity is what
this script exists to remove.

**A re-score is not a repair, and the store is built so it cannot be one.** The snapshot behind
``evidence_input_sha256`` is not kept (only its hash), the candidates file is append-only, and
every row is sealed with ``record_sha256`` — so the original evidence can never be amended in
place. What this does instead is replay the **frozen spec** against a CURRENT snapshot and append
a NEW record, which ``derive_candidate_id`` keys to a new lineage id because the evidence window
changed. The source row stays exactly as it was; the new one supersedes it by being judgeable.
This is the same shape as the 2026-07-29 re-score of the ``crypto_ai_system_import`` rows (see
``docs/BUILD_HISTORY.md``), and the record fields below match those 25 rows deliberately.

Two consequences worth stating before running it, because both are easy to want otherwise:

- **The re-scored holdout is a NEW draw, not the old one recovered.** Parameters were mined on
  the source row's in-sample window, which lies entirely before the tail this replay judges, so
  the out-of-sample claim is sound — but the expectancy moves, and on a population whose gross
  edge is centred on zero it moves in both directions.
- **The store grows.** Re-scoring the full 533 takes the file from 1,140 rows to 1,673, each new
  row sharing a ``strategy_rule_hash`` with an existing one. That is legal here — a re-score at a
  different window is explicitly not a semantic duplicate
  (``test_mvp_runtime_crypto_semantic_duplicates``) — but it is not free either.

Dry by default: nothing is written without ``--apply``. This writes runtime state, so it runs in
the container as uid 10001, in module form::

    docker exec thomas-scheduler python -m scripts.rescore_stale_holdout_candidates --list
    docker exec thomas-scheduler python -m scripts.rescore_stale_holdout_candidates \
        --timeframe 1h --timeframe 4h --timeframe 1d --apply
"""

from __future__ import annotations

import argparse
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from runtime.mvp_runtime import timeutil
from runtime.mvp_runtime.errors import ToolBlocked
from runtime.mvp_runtime.paths import repo_root as _repo_root
from runtime.mvp_runtime.crypto import market_data, pool as pool_store, robustness
from runtime.mvp_runtime.crypto.cycle import (
    attach_cross_section,
    attach_feeds,
    attach_htf,
    attach_positioning,
    attach_reference,
)
from runtime.mvp_runtime.crypto.factory import (
    backtest_spec,
    build_replay_frame,
    unsuppliable_features,
)
from runtime.mvp_runtime.crypto.strategy import StrategySpec
from runtime.read_only_kernel import integrity

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_BLOCKED = 3

RESCORE_PROVENANCE = "mvp_rescore"
RESCORE_REASON = (
    "holdout re-scored at the current window; the source candidate's holdout block predates "
    "`stdev_r`, so `robustness.holdout_status` could not draw an interval over it (INSUFFICIENT)"
)


def _holdout_of(record: Mapping[str, Any]) -> Mapping[str, Any] | None:
    holdout = (record.get("backtest_evidence") or {}).get("holdout")
    return holdout if isinstance(holdout, Mapping) and holdout else None


def _lacks_spread(holdout: Mapping[str, Any]) -> bool:
    """The exact condition ``holdout_status`` fails on — restated here, not re-derived.

    A stored ``0.0`` counts as lacking: the factory writes it below two trades, where the
    statistic is undefined, and ``holdout_status`` refuses it for the same reason.
    """
    stdev = holdout.get("stdev_r")
    return not isinstance(stdev, (int, float)) or isinstance(stdev, bool) or stdev <= 0


def select_stale(
    records: Sequence[Mapping[str, Any]],
    *,
    min_holdout_trades: int,
    timeframes: frozenset[str] | None,
    candidate_ids: frozenset[str] | None,
) -> list[dict[str, Any]]:
    """Rows whose holdout is deep enough to judge but carries no spread to judge it with.

    The trade floor is applied here rather than left to the caller because a row below it is
    INSUFFICIENT on its own terms — adding ``stdev_r`` would not change its verdict, so
    re-scoring it buys nothing this script claims to buy.
    """
    out: list[dict[str, Any]] = []
    for record in records:
        if candidate_ids is not None:
            if pool_store.candidate_id(record) not in candidate_ids:
                continue
        holdout = _holdout_of(record)
        if holdout is None:
            continue
        closed = holdout.get("closed_count")
        if not isinstance(closed, (int, float)) or isinstance(closed, bool):
            continue
        if closed < min_holdout_trades:
            continue
        if not _lacks_spread(holdout):
            continue
        spec = record.get("strategy_spec") or {}
        if timeframes is not None and str(spec.get("timeframe")) not in timeframes:
            continue
        out.append(dict(record))
    return out


def _context_of(record: Mapping[str, Any]) -> tuple[str, str] | None:
    """``(symbol, timeframe)`` — the snapshot one spec must be replayed against.

    The symbol lives in ``symbol_scope`` (a list) rather than a scalar field; a spec scoped to
    more than one symbol has no single replay context and is skipped rather than guessed at.
    """
    spec = record.get("strategy_spec") or {}
    scope = spec.get("symbol_scope")
    timeframe = spec.get("timeframe")
    if not isinstance(scope, list) or len(scope) != 1:
        return None
    if not isinstance(scope[0], str) or not scope[0]:
        return None
    if timeframe not in market_data.TIMEFRAMES:
        return None
    return str(scope[0]), str(timeframe)


def _build_snapshot(symbol: str, timeframe: str, *, now: str, root: Path) -> dict[str, Any]:
    """The scheduler's own factory snapshot, feed for feed.

    Mirrors ``scheduler`` KIND_CRYPTO_FACTORY exactly. Any leg dropped here would score the
    families that read it as no-trade specs — which is not a cheap verdict, it is a wrong one.
    """
    collector = market_data.select_market_data_collector(now=now, root=root)
    snapshot, _ = market_data.collect_market_data(
        symbol, timeframe, collector=collector, now=now,
        limit=market_data.factory_candle_target(timeframe),
    )
    attach_feeds(
        snapshot, collector=collector,
        liquidation_feed=market_data.select_liquidation_feed(now=now, root=root),
        now=now, root=root,
    )
    higher = market_data.HIGHER_TIMEFRAME.get(timeframe)
    attach_htf(
        snapshot, collector=collector, now=now,
        limit=market_data.factory_candle_target(higher) if higher else None,
    )
    attach_reference(
        snapshot, collector=collector, now=now,
        limit=market_data.factory_candle_target(timeframe),
    )
    attach_cross_section(
        snapshot, collector=collector, now=now,
        limit=market_data.factory_candle_target(timeframe),
    )
    attach_positioning(snapshot, root=root)
    return snapshot


def rescore_record(
    source: Mapping[str, Any], snapshot: Mapping[str, Any], frame: Any, *, now: str,
) -> dict[str, Any] | None:
    """One re-scored candidate row, or None if this runtime cannot supply the spec's features.

    The starvation guard is the factory's, for the factory's reason: evidence for a spec whose
    features are None on every row is evidence for a trade this runtime cannot reproduce.
    """
    spec = StrategySpec.from_dict(source["strategy_spec"])
    starved = unsuppliable_features(spec, frame.rows)
    if starved:
        return None
    evidence = backtest_spec(spec, snapshot, frame=frame)
    record = {
        "strategy_id": source.get("strategy_id"),
        "strategy_rule_hash": source.get("strategy_rule_hash"),
        # Preserved, not renumbered. A re-score is the same lineage seen again, and the
        # 2026-07-29 rows set this precedent; minting a fresh generation id would make the
        # store's generation sequence a record of re-measurements rather than of search rounds.
        "generation_id": source.get("generation_id"),
        "status": "BACKTESTED",
        "champion_score": evidence["champion_score"],
        "strategy_spec": spec.to_dict(),
        "backtest_evidence": evidence,
        # The NEW window's hash — this is what makes the re-score a distinct candidate_id
        # rather than a collision with the row it came from.
        "evidence_input_sha256": integrity.sha256_record(
            {"candles": snapshot.get("candles") or []}
        ),
        "provenance": RESCORE_PROVENANCE,
        "rescored_from_candidate_id": pool_store.candidate_id(source),
        "rescore_reason": RESCORE_REASON,
        "created_at_utc": now,
    }
    record["candidate_id"] = pool_store.derive_candidate_id(record)
    return record


def _verdict(holdout: Mapping[str, Any] | None) -> str:
    return robustness.holdout_status(holdout)


def _required_stdev(holdout: Mapping[str, Any]) -> float | None:
    """The largest spread at which this row's stored expectancy would still read CONFIRMED.

    Reported in ``--list`` so a run can be scoped by what could actually change: below the
    smallest ``stdev_r`` this store has ever produced, no spread makes the row confirm.
    """
    closed = holdout.get("closed_count")
    expectancy = holdout.get("expectancy")
    if not isinstance(closed, (int, float)) or not isinstance(expectancy, (int, float)):
        return None
    if expectancy <= 0 or closed < 2:
        return None
    return float(expectancy) * math.sqrt(float(closed)) / robustness.CONFIDENCE_Z


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="rescore_stale_holdout_candidates",
        description="Re-score candidates whose holdout block carries no stdev_r.",
    )
    parser.add_argument("--timeframe", action="append", dest="timeframes", default=None,
                        help="restrict to this timeframe (repeatable)")
    parser.add_argument("--candidate-id", action="append", dest="candidate_ids", default=None,
                        help="re-score exactly this candidate (repeatable)")
    parser.add_argument("--min-holdout-trades", type=int, default=robustness.MIN_HOLDOUT_TRADES,
                        help=f"holdout trade floor (default {robustness.MIN_HOLDOUT_TRADES})")
    parser.add_argument("--limit", type=int, default=None,
                        help="re-score at most this many candidates")
    parser.add_argument("--list", action="store_true",
                        help="print the selection and exit; collects no market data")
    parser.add_argument("--apply", action="store_true",
                        help="append the re-scored records (default: dry run, writes nothing)")
    args = parser.parse_args(argv)

    if args.timeframes:
        unknown = [t for t in args.timeframes if t not in market_data.TIMEFRAMES]
        if unknown:
            print(f"unknown timeframe(s): {', '.join(unknown)}", file=sys.stderr)
            return EXIT_USAGE
    if args.min_holdout_trades < 2:
        print("--min-holdout-trades must be at least 2", file=sys.stderr)
        return EXIT_USAGE

    root = _repo_root()
    now = timeutil.utc_now_iso()
    records = pool_store.read_candidates(root)
    stale = select_stale(
        records,
        min_holdout_trades=args.min_holdout_trades,
        timeframes=frozenset(args.timeframes) if args.timeframes else None,
        candidate_ids=frozenset(args.candidate_ids) if args.candidate_ids else None,
    )

    by_context: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    unroutable: list[dict[str, Any]] = []
    for record in stale:
        context = _context_of(record)
        if context is None:
            unroutable.append(record)
            continue
        by_context[context].append(record)

    if args.limit is not None:
        kept: dict[tuple[str, str], list[dict[str, Any]]] = {}
        budget = args.limit
        for context in sorted(by_context):
            if budget <= 0:
                break
            group = by_context[context][:budget]
            kept[context] = group
            budget -= len(group)
        by_context = defaultdict(list, kept)

    total = sum(len(v) for v in by_context.values())
    print(f"stale (holdout >= {args.min_holdout_trades} trades, no stdev_r): {len(stale)}")
    if unroutable:
        print(f"  skipped, no single replay context: {len(unroutable)}")
    print(f"selected for re-score: {total} across {len(by_context)} context(s)")

    if args.list:
        for context in sorted(by_context):
            group = by_context[context]
            reachable = 0
            for record in group:
                holdout = _holdout_of(record) or {}
                required = _required_stdev(holdout)
                if required is not None and required > 0:
                    reachable += 1
            print(f"  {context[0]:10s} {context[1]:4s}  {len(group):4d} rows, "
                  f"{reachable} with a positive stored holdout")
        return EXIT_OK

    if not total:
        print("nothing to do")
        return EXIT_OK

    minted: list[dict[str, Any]] = []
    starved = 0
    for context in sorted(by_context):
        symbol, timeframe = context
        group = by_context[context]
        try:
            snapshot = _build_snapshot(symbol, timeframe, now=now, root=root)
        except ToolBlocked as exc:
            print(f"  {symbol} {timeframe}: BLOCKED ({exc.reason_code}) — {exc}", file=sys.stderr)
            return EXIT_BLOCKED
        frame = build_replay_frame(snapshot)
        confirmed = 0
        for record in group:
            rescored = rescore_record(record, snapshot, frame, now=now)
            if rescored is None:
                starved += 1
                continue
            if _verdict(_holdout_of(rescored)) == robustness.HOLDOUT_CONFIRMED:
                confirmed += 1
            minted.append(rescored)
        print(f"  {symbol:10s} {timeframe:4s}  re-scored {len(group) - starved:4d}  "
              f"CONFIRMED {confirmed}")

    verdicts: dict[str, int] = defaultdict(int)
    for record in minted:
        verdicts[_verdict(_holdout_of(record))] += 1
    print(f"\nre-scored {len(minted)} candidate(s); {starved} skipped as feature-starved")
    for verdict in sorted(verdicts):
        print(f"  {verdict:14s} {verdicts[verdict]}")

    if not args.apply:
        print("\nDRY RUN — nothing written. Re-run with --apply to append these records.")
        return EXIT_OK

    written = pool_store.append_candidates(minted, root=root)
    print(f"\nappended {written} record(s) to the candidate store")
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

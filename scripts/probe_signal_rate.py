#!/usr/bin/env python3
"""Operator tool: does this spec still fire? Replay candidates through the LIVE entry doors.

**Why a backtest trade count cannot answer this.** On 2026-09-01 four pooled lineages were
retired for producing zero virtual opens over 18-34 days each. Their backtest evidence did
not predict it: the worst offender (S005-GEN-833) carried 412 backtest trades — second
most in the pool — and fired zero times in 18 days, while the healthiest survivor
(S003-GEN-821) carried 100 and fired six times in 23. What separates them is not how often
a spec fired in its scored window but whether TODAY's bars still match its rules and clear
the regime, distribution and cost doors the live cycle runs. So this replays the recent
past through exactly those doors — ``forward_book.walk_seed_span``, the same pure walk the
forward seeder uses — and reports what it finds.

The three outcomes are the diagnosis, and they call for different actions:

* ``fires``            — opens happened; the rate says whether its forward clock can close.
* ``door-refused``     — the entry rules matched and something downstream refused every
                         time (regime admission, distribution admission, entry cost,
                         liquidation distance). The spec is alive; its gating is not.
* ``never matched``    — the entry rules did not match a single bar. The spec is stale
                         against the current market, whatever its backtest says.

**A probe is not evidence.** The window is the last ``--days``, mint date ignored, so for
a pooled lineage it deliberately reaches back into bars its spec was fitted on. That is the
right window for "would this fire if I gave it a slot" and the wrong one for "did it earn
an arming" — the forward book is the only place the second question is answered.

Read-only: it fetches candles and computes. It writes nothing, asks for no approval, and
retires nobody — the decision it feeds is which candidates deserve a routing slot.

    docker exec thomas-scheduler python -m scripts.probe_signal_rate --pooled
    docker exec thomas-scheduler python -m scripts.probe_signal_rate --promotable --days 60
    docker exec thomas-scheduler python -m scripts.probe_signal_rate --strategy-id S004-GEN-706
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime import timeutil  # noqa: E402
from runtime.mvp_runtime.cli_common import EXIT_OK, EXIT_USAGE  # noqa: E402
from runtime.mvp_runtime.errors import MvpRuntimeError, ToolError  # noqa: E402
from runtime.mvp_runtime.crypto import forward_book, market_data, pool as pool_store  # noqa: E402
from runtime.mvp_runtime.crypto.cycle import attach_mining_legs  # noqa: E402
from runtime.mvp_runtime.crypto.factory import build_replay_frame  # noqa: E402
from runtime.mvp_runtime.crypto.forward_confirmation import (  # noqa: E402
    MIN_HOLDOUT_PERIODS, forward_slice_width_days, min_forward_trades,
)
from runtime.mvp_runtime.crypto.paper import OCCUPYING_STATUSES  # noqa: E402
from runtime.mvp_runtime.crypto.strategy import StrategySpec  # noqa: E402

# The same warmup the seeder buys, for the same reason: the deepest indicator window is
# 100 bars, so without them the oldest stretch of the probe silently matches nothing.
_WARMUP_BARS = 120
# The venue serves the NEWEST bars, so a truncated probe loses its oldest end — which is
# the right end to lose when the question is "does this spec still fire".
_MAX_PROBE_BARS = 4000
_DEFAULT_DAYS = 90

FIRES = "fires"
DOOR_REFUSED = "door-refused"
NEVER_MATCHED = "never matched"


def probe_span(
    entry: Mapping[str, Any], spec: StrategySpec, rows: Sequence[Mapping[str, Any]],
    candles: Sequence[Mapping[str, Any]], *, symbol: str, timeframe: str, since: str,
) -> dict[str, Any]:
    """Replay [since, now] and say what happened. Pure — the fetch is the caller's.

    ``boundary=None`` because a probe is not a seed: nothing is minted, nothing is merged,
    and the live stream's calendar is not being partitioned against. The walk's own state
    carries the two numbers that matter — did anything match, and did anything open.

    The entry is replayed as ``pool.as_pool_entry_for_replay`` sees it: a candidate row does
    not carry the regime and distribution evidence its admission doors read, and both doors
    fail OPEN without it, so a bare candidate replays with its gating switched off. That is
    not a rounding error — S004-GEN-690 read 36 opens in 60 days as a candidate and installed
    at 11, because its own regime evidence excludes the regime it fires in most."""
    state, minted = forward_book.walk_seed_span(
        pool_store.as_pool_entry_for_replay(entry), spec, rows, candles,
        symbol=symbol, timeframe=timeframe, boundary=None, mint=since)
    opens = int(state.get("opens_count") or 0)
    matched = bool(state.get("last_signal_at"))
    verdict = FIRES if opens else (DOOR_REFUSED if matched else NEVER_MATCHED)
    return {"symbol": symbol, "timeframe": timeframe, "opens": opens, "settled": len(minted),
            "matched": matched, "verdict": verdict, "last_signal_at": state.get("last_signal_at")}


def confirmation_horizon(timeframe: str, opens: int, days: float) -> dict[str, Any]:
    """Could a lineage firing at THIS rate ever close its own forward clock?

    Two independent demands, and the binding one is whichever lands later: the trade floor
    (``min_forward_trades``) at the observed rate, and the calendar the slice test needs
    (``MIN_HOLDOUT_PERIODS`` slices at the forward width) no rate can shorten. A rate of
    zero has no answer and says so."""
    width = forward_slice_width_days(timeframe)
    calendar = None if width is None else width * MIN_HOLDOUT_PERIODS
    floor = min_forward_trades(timeframe)
    per_day = opens / days if days > 0 else 0.0
    trade_days = None if per_day <= 0 else floor / per_day
    binding = None
    if trade_days is not None and calendar is not None:
        binding = max(trade_days, calendar)
    return {"opens_per_30d": round(per_day * 30.0, 2), "trade_floor": floor,
            "days_to_floor": None if trade_days is None else round(trade_days),
            "calendar_days": None if calendar is None else round(calendar),
            "days_to_confirmable": None if binding is None else round(binding)}


def _fetch_frame(symbol: str, timeframe: str, *, bars: int, now: str, root: Path, collector: Any):
    """The seeder's three calls, for the same reason and in the same order."""
    snapshot, _ = market_data.collect_market_data(
        symbol, timeframe, collector=collector, now=now, limit=bars)
    attach_mining_legs(snapshot, collector=collector, timeframe=timeframe, now=now,
                       root=root, candle_target=lambda _tf: bars)
    return build_replay_frame(snapshot)


def probe_entry(
    entry: Mapping[str, Any], spec: StrategySpec, symbol: str, *, days: int, now: str,
    root: Path, collector: Any,
) -> dict[str, Any]:
    minutes = market_data.TIMEFRAMES.get(spec.timeframe)
    if not minutes:
        return {"symbol": symbol, "timeframe": spec.timeframe, "skipped": "unknown timeframe"}
    span_bars = int(days * 1440 / minutes)
    bars = min(_MAX_PROBE_BARS, span_bars + _WARMUP_BARS)
    covered = min(days, (bars - _WARMUP_BARS) * minutes / 1440.0)
    since = timeutil.plus_minutes(now, -int(covered * 1440))
    frame = _fetch_frame(symbol, spec.timeframe, bars=bars, now=now, root=root, collector=collector)
    probed = probe_span(entry, spec, frame.rows, frame.candles,
                        symbol=symbol, timeframe=spec.timeframe, since=since)
    probed["days"] = round(covered, 1)
    probed["truncated"] = covered < days - 0.5
    probed.update(confirmation_horizon(spec.timeframe, probed["opens"], covered))
    return probed


def _select(root: Path, args: argparse.Namespace) -> list[dict[str, Any]]:
    pool = pool_store.load_active_pool(root).get("active_strategies") or []
    occupying = [e for e in pool if e.get("status") in OCCUPYING_STATUSES]
    if args.strategy_ids:
        wanted = set(args.strategy_ids)
        return [e for e in pool if str(e.get("strategy_id")) in wanted]
    if args.promotable:
        held = {str(e.get("candidate_id")) for e in occupying}
        rows = [c for c in pool_store.read_candidates(root)
                if str(c.get("candidate_id")) not in held and c.get("strategy_spec")]
        by_hash: dict[str, dict[str, Any]] = {}
        for row in rows:  # newest scoring of each lineage wins
            by_hash[str(row.get("strategy_rule_hash"))] = row
        ranked = sorted(by_hash.values(), key=lambda c: -float(c.get("champion_score") or 0.0))
        return ranked[:args.limit]
    return occupying


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="probe_signal_rate",
        description="Replay candidates through the live entry doors and report what fires.")
    parser.add_argument("--strategy-id", action="append", dest="strategy_ids", default=None,
                        help="probe these pool strategy_ids (repeatable)")
    parser.add_argument("--pooled", action="store_true", help="probe every occupying entry")
    parser.add_argument("--promotable", action="store_true",
                        help="probe the top unpooled candidates by champion score")
    parser.add_argument("--limit", type=int, default=12, help="how many with --promotable")
    parser.add_argument("--days", type=int, default=_DEFAULT_DAYS, help="lookback window")
    args = parser.parse_args(argv)
    if not (args.strategy_ids or args.pooled or args.promotable):
        print("choose one of --pooled, --promotable or --strategy-id", file=sys.stderr)
        return EXIT_USAGE

    root = ROOT
    now = timeutil.utc_now_iso()
    collector = market_data.select_market_data_collector(now=now, root=root)
    print("%-16s %-9s %-4s %6s %6s %8s %9s %-14s %s" % (
        "strategy", "symbol", "tf", "days", "opens", "per 30d", "confirm?", "verdict", "note"))
    for entry in _select(root, args):
        try:
            spec = StrategySpec.from_dict(entry["strategy_spec"])
        except Exception as exc:
            print("%-16s  spec unparseable: %s" % (entry.get("strategy_id"), exc))
            continue
        label = str(entry.get("strategy_id") or entry.get("candidate_id") or "?")[:16]
        for symbol in spec.symbol_scope:
            try:
                r = probe_entry(entry, spec, symbol, days=args.days, now=now, root=root,
                                collector=collector)
            except (ToolError, MvpRuntimeError) as exc:
                print("%-16s %-9s %-4s  ERR %s" % (
                    label, symbol, spec.timeframe, getattr(exc, "reason_code", type(exc).__name__)))
                continue
            if r.get("skipped"):
                print("%-16s %-9s %-4s  %s" % (label, symbol, spec.timeframe, r["skipped"]))
                continue
            confirmable = r["days_to_confirmable"]
            print("%-16s %-9s %-4s %6.0f %6d %8.2f %9s %-14s %s" % (
                label, symbol, r["timeframe"], r["days"], r["opens"], r["opens_per_30d"],
                "never" if confirmable is None else "%dd" % confirmable, r["verdict"],
                "window truncated" if r["truncated"] else ""))
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

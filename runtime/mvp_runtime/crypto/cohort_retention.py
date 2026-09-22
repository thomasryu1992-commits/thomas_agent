"""The retention stores' cohort sweeps: what the fan-out records for the declared cohort on every
pass (crypto PR7e-6).

`accumulate_positioning_cohort`, `accumulate_open_interest_cohort` and `accumulate_orderbook_cohort`
refresh the positioning, hourly open-interest and order-book stores for every member of the
cross-sectional cohort, unioned with the symbols the pool actually visited (`retention_cohort`). The
rule they share: a retention store's scope cannot be a side effect of routing. A symbol the pool stops
routing, or never routed, must still be recorded, because the vendors serve 30 or 84 days, the venue's
book serves none, and an hour not recorded today is gone for good.

This is market work: each sweep writes a local append-only store through the store's own throttle,
opens no order path, and never raises. It lived in `cycle`, whose fan-out (`run_pool_cycle`) calls these
after its context loop, and which re-exports every name here as the same object.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import oi_store, orderbook_store, positioning_store
from .market_data import CROSS_SECTION_UNIVERSE, MarketDataCollector


def accumulate_positioning_cohort(
    *,
    collector: MarketDataCollector,
    now: str,
    root: Path | None,
    contexts: list[tuple[str, str]],
) -> dict[str, str]:
    """Refresh the positioning store for the DECLARED cohort, not for what the pool traded.

    **The scope of a retention store cannot be a side effect of routing.** Accumulation used
    to ride on ``feed_assembly.attach_feeds(accumulate=True)``, which runs once per *visited*
    context — so the store covered whatever ``cycle.pool_cycle_contexts`` yielded that fire, and a
    symbol leaving the routable set stopped being recorded with nothing saying so. Measured
    2026-08-04 on this host: the fan-out was visiting six contexts across four symbols, and

    - ``BNBUSDT`` had been frozen since 2026-07-31T09:20Z — 31 days recorded, then nothing,
      from the day it dropped out of the routable set while still holding 17 pool entries;
    - ``XRPUSDT`` held **zero** rows and always had, because it is a cohort member that the
      pool has never traded, so no context ever carried it.

    That is fatal in a way an ordinary outage is not: the vendor serves 30 days and the
    factory replays 500, so an hour not recorded today is not late, it is **gone**. And it
    compounds — ``coverage_summary`` reports ``eligible`` as the AND over cells, so one
    permanently-empty member holds the gate shut however long the other five accumulate.

    The cohort is the right scope because :data:`CROSS_SECTION_UNIVERSE` is already declared
    for exactly this reason — "not whichever symbols the pool happens to route today" — and a
    store that must cover the cohort was the one thing still following the pool. Visited
    symbols are unioned in rather than assumed to be a subset, so a pool that grows past the
    cohort keeps its own positioning rather than silently losing it.

    Cost is bounded by the store's own hourly throttle, not by this call: at most one request
    per (symbol, series) per hour whoever asks, so a 15-minute fan-out over six symbols costs
    18 requests an hour and the other three fires return ``skipped_fresh`` having opened no
    socket. Never raises — ``record_positioning`` reports per-series degradation instead, and
    a collection miss must not cost a fire its cycles.

    Runs after the context loop, and after a ``live_halt`` too. The halt exists to stop this
    runtime *acting* on a picture of real money it no longer trusts; this opens no order path,
    writes only a local append-only store, and its data is unrecoverable if skipped — so
    deferring it to the next fire would trade a real loss for no safety.
    """
    return {
        symbol: str(
            positioning_store.record_positioning(
                symbol=symbol, collector=collector, now=now, root=root,
            )["status"]
        )
        for symbol in retention_cohort(contexts)
    }


def retention_cohort(contexts: list[tuple[str, str]]) -> list[str]:
    """The symbols a retention store must cover: the declared cohort, unioned with what the
    pool actually visited. Sorted, so a sweep is deterministic.

    One function because it is one rule. Both accumulating stores answer the same question and
    got different answers when only one of them was fixed — see
    :func:`accumulate_open_interest_cohort`."""
    symbols = {str(symbol).strip().upper() for symbol, _timeframe in contexts}
    symbols.update(CROSS_SECTION_UNIVERSE)
    return sorted(s for s in symbols if s)


def accumulate_open_interest_cohort(
    *,
    liquidation_feed: Any | None,
    now: str,
    root: Path | None,
    contexts: list[tuple[str, str]],
) -> dict[str, str]:
    """Refresh the hourly open-interest store for the DECLARED cohort, not for what the pool
    traded. The positioning sweep's rule, applied to the store that still followed routing.

    :func:`accumulate_positioning_cohort` fixed this for positioning on 2026-08-04 and recorded
    why: a retention store's scope cannot be a side effect of routing, because the vendor serves
    ~84 days of hourly history and the factory replays 500 — an hour not recorded today is not
    late, it is **gone**. The hourly OI store was left on the old footing, riding
    :func:`feed_assembly.attach_feeds`, which runs once per *visited* context.

    Measured on this host 2026-08-04, the same day the positioning sweep landed: the store held
    **10,644 rows across exactly five symbols** — BTC, ETH, SOL, BNB, DOGE — and **XRPUSDT held
    zero and always had**, because it is a cohort member the pool has never routed, so no
    context ever carried it. That is the identical footprint the positioning docstring reports
    for XRP, on the store nobody moved.

    Cost is the store's own hourly throttle, not this call: ``record_intraday_oi`` measures from
    the last ATTEMPT, so widening the scope to six symbols costs at most six vendor requests an
    hour whoever asks, and the other three fires of a 15-minute fan-out return ``skipped_fresh``
    having opened no socket. Never raises — the store is degrade-only and reports per-symbol
    instead, because a collection miss must not cost a fire its cycles.

    Left deliberately ALONGSIDE the ``attach_feeds`` call rather than replacing it: removing
    that write is a separate change (it is the one that makes ``attach_feeds`` a pure read), and
    the throttle means the extra call site costs nothing. Adding coverage first is the half that
    cannot regress anything."""
    return {
        symbol: str(
            oi_store.record_intraday_oi(
                symbol=symbol, feed=liquidation_feed, now=now, root=root,
            )["status"]
        )
        for symbol in retention_cohort(contexts)
    }


def accumulate_orderbook_cohort(
    *,
    collector: Any,
    now: str,
    root: Path | None,
    contexts: list[tuple[str, str]],
) -> dict[str, str]:
    """Snapshot the resting book for the DECLARED cohort. The third store on the same rule.

    Written on the cohort footing from its first line rather than being moved onto it later, and
    that is the whole reason this function exists at all instead of a flag on
    :func:`feed_assembly.attach_feeds`. Both stores above were shipped per-context and both had to be rescued:
    positioning on 2026-08-04 with ``BNBUSDT`` frozen for 31 days and ``XRPUSDT`` permanently
    empty, hourly OI the same day with the identical XRP footprint. The lesson generalises —
    a retention store's scope cannot be a side effect of routing — and the cost of relearning it
    here is strictly higher than it was there: those vendors serve 84 and 30 days, so a symbol
    found late could still be backfilled to the retention wall. This venue serves **nothing**.
    A cohort member missed on the day this ships is a hole in 2029's window that no later run,
    no repair path and no amount of money can close.

    Cost is the store's own 15-minute throttle, not this call: at most one request per symbol per
    period whoever asks, so the cohort costs six requests a fire and 1,152 request-weight a day
    against a 2,400-per-minute cap. Never raises — ``record_orderbook`` reports per-symbol status
    instead, because a collection miss must not cost a fire its cycles.

    Runs after the context loop and after a ``live_halt``, on
    :func:`accumulate_positioning_cohort`'s reasoning: the halt stops this runtime *acting* on a
    picture of real money it no longer trusts, while this opens no order path, writes only a local
    append-only store, and loses its data permanently if skipped.
    """
    return {
        symbol: str(
            orderbook_store.record_orderbook(
                symbol=symbol, collector=collector, now=now, root=root,
            )["status"]
        )
        for symbol in retention_cohort(contexts)
    }

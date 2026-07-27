"""PM1 — which markets are worth looking at at all. Deterministic; drops nothing silently.

The matcher decides whether two markets are the *same event*. This decides, one market at a
time and before any of that, whether a market can take part in the pipeline at all. It exists
because the first deployed scan produced a list that was almost entirely unusable, and none of
it was a bug:

- **Kalshi's open listing is dominated by parlays.** ``KXMVESPORTSMULTIGAMEEXTENDED-…`` markets
  are combinations of nine other markets ("yes Atlas, yes Cruz Azul, yes Leon, …"), quoted
  0.00/0.00. No other venue lists that basket, so it can never be one leg of a cross-venue
  group — and its "title" is a comma-joined list of legs, which is exactly the kind of string
  a token-overlap matcher scores badly and unpredictably against real questions.
- **Binance's listing is dominated by five-minute markets.** ``Bitcoin Up or Down —
  2:30AM-2:35AM ET`` closes before an operator could finish reading the proposal that contains
  it.
- **Polymarket lists markets whose end date has already passed** — observed 2026-07-27 with
  ``active: true, closed: false`` on markets dated 2025-12-31.

**The gate is the pipeline, not the price.** Nothing here judges whether a market is
*interesting*; that is the matcher's and the operator's job. It asks one question: can this
market still be here by the time the process that must precede an order has run? Discovery
proposes, a human confirms, and only then do watch scans observe. A market that closes before
the human step is not a missed opportunity — it was never reachable.

**Venue-asserted facts, not heuristics.** Every exclusion below rests on a field the venue
itself publishes: Kalshi's ``mve_selected_legs`` names the constituent markets, Polymarket's
``enableOrderBook``/``acceptingOrders`` state whether it will take an order. That is the same
rule ``matching.venue_cross_reference`` follows, and for the same reason — a venue describing
its own product outranks anything inferred from its wording. Notably ``market_type`` does
**not** work here: Kalshi reports a nine-leg parlay as ``"binary"``, which is true of its
payoff and useless as a filter.

**Nothing is dropped quietly.** Every exclusion is returned with its reason and counted in the
summary, because "0 candidates" and "0 candidates out of 40, all of them parlays" are
different findings, and a filter that silently produced the first from the second would hide
its own mistakes for as long as it was wrong.

**What this does not fix.** Run over a hundred real rows per venue on 2026-07-27, the gates
removed every Kalshi row (100 parlays), six expired Polymarket rows, and seven five-minute
Binance markets. That is the filter working — and it also shows its limit: it can only judge
the page the venue chose to serve. Which hundred of Kalshi's thousands arrive is the venue's
decision, and improving *that* needs pagination or a series filter, not a better predicate.
This module makes the noise visible and countable; it does not go looking for signal.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from .. import timeutil
from .market_data import PredMarket

SCREENING_VERSION = "predmarket_screening.v0.1"

# How much life a market must have left to be worth *proposing*. A policy choice, stated as
# one, not a measurement dressed up as one: a proposal has to survive long enough for a human
# to read it and answer, and this is the number to change the first time it is wrong.
#
# It is deliberately a discovery-time gate. A watch scan passes ``min_horizon_hours=0`` for an
# already-confirmed group, because a leg with an hour left still owes the report its final
# readings — the horizon question was settled when the operator confirmed it.
MIN_HORIZON_HOURS = 6.0

# Why a market was excluded. Each names one venue-published fact, so a disagreement with the
# filter is a disagreement about that fact rather than about a threshold nobody wrote down.
DERIVED_COMBINATION = "DERIVED_COMBINATION"
HORIZON_TOO_SHORT = "HORIZON_TOO_SHORT"
CLOSE_TIME_UNKNOWN = "CLOSE_TIME_UNKNOWN"
NOT_ACCEPTING_ORDERS = "NOT_ACCEPTING_ORDERS"


def hours_to_close(close_time: Any, *, now: str) -> float | None:
    """Hours from ``now`` until a market closes; ``None`` when either time is unreadable.

    Signed: a market whose close time has already passed returns a negative number rather
    than zero, so an expired listing is visibly expired instead of merely urgent. Polymarket
    serves those, and rounding them up to "closes now" would have hidden it.
    """
    try:
        closes, current = timeutil.parse_iso(str(close_time)), timeutil.parse_iso(str(now))
    except (TypeError, ValueError):
        return None
    return round((closes - current).total_seconds() / 3600.0, 4)


@dataclass(frozen=True)
class Screen:
    """One market's verdict, with the reasoning that produced it."""

    venue: str
    market_id: str
    title: str
    observable: bool
    reasons: tuple[str, ...]
    hours_to_close: float | None
    derived_leg_count: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "screening_version": SCREENING_VERSION,
            "venue": self.venue,
            "market_id": self.market_id,
            "title": self.title,
            "observable": self.observable,
            "reasons": list(self.reasons),
            "hours_to_close": self.hours_to_close,
            "derived_leg_count": self.derived_leg_count,
        }


def screen_market(
    market: PredMarket, *, now: str, min_horizon_hours: float = MIN_HORIZON_HOURS
) -> Screen:
    """Judge one market. Pure: no I/O, no model call, no state.

    Order does not matter — every failing gate is recorded, not just the first. A market that
    is both a parlay and already expired says so twice, and a reviewer asking "why did the
    list empty out?" gets the whole answer from one pass.
    """
    reasons: list[str] = []

    legs = market.derived_from or ()
    if legs:
        reasons.append(DERIVED_COMBINATION)

    remaining = hours_to_close(market.close_time, now=now)
    if remaining is None:
        # Fails closed, unlike `accepting_orders` below. The difference is whether the
        # question stays open: a market that never said when it closes leaves this gate
        # permanently unanswerable, while a market that never said whether it takes orders
        # will be answered by its own book at the next scan.
        reasons.append(CLOSE_TIME_UNKNOWN)
    elif remaining < min_horizon_hours:
        reasons.append(HORIZON_TOO_SHORT)

    if market.accepting_orders is False:
        reasons.append(NOT_ACCEPTING_ORDERS)

    return Screen(
        venue=market.venue,
        market_id=market.market_id,
        title=market.title,
        observable=not reasons,
        reasons=tuple(reasons),
        hours_to_close=remaining,
        derived_leg_count=len(legs),
    )


def screen_markets(
    markets: Iterable[PredMarket],
    *,
    now: str,
    min_horizon_hours: float = MIN_HORIZON_HOURS,
) -> dict[str, Any]:
    """Screen a venue's markets. Returns the survivors **and** what was excluded, with counts.

    The excluded rows are kept rather than counted and thrown away: the report they feed is
    the only place a wrong threshold or a mis-read venue field becomes visible, and a summary
    that said "35 excluded" without saying which 35 would make this filter unfalsifiable.
    """
    observable: list[PredMarket] = []
    excluded: list[Screen] = []
    counts: Counter[str] = Counter()

    for market in markets:
        if not isinstance(market, PredMarket):
            continue
        verdict = screen_market(market, now=now, min_horizon_hours=min_horizon_hours)
        if verdict.observable:
            observable.append(market)
        else:
            excluded.append(verdict)
            counts.update(verdict.reasons)

    return {
        "screening_version": SCREENING_VERSION,
        "screened_count": len(observable) + len(excluded),
        "observable_count": len(observable),
        "excluded_count": len(excluded),
        # By reason, so the shape of the noise is readable at a glance — and so a filter that
        # starts excluding everything announces itself instead of looking like a quiet market.
        "excluded_by_reason": dict(sorted(counts.items())),
        "observable": observable,
        "excluded": [s.as_dict() for s in excluded],
        "min_horizon_hours": min_horizon_hours,
    }


def screening_status_line(result: Mapping[str, Any]) -> str:
    """One ASCII line for the console (Windows consoles are cp949)."""
    reasons = result.get("excluded_by_reason") or {}
    detail = ", ".join(f"{code}={count}" for code, count in reasons.items()) or "-"
    return (
        f"screened {result.get('screened_count')} -> {result.get('observable_count')} observable, "
        f"{result.get('excluded_count')} excluded ({detail})"
    )


def min_close_epoch_seconds(*, now: str, min_horizon_hours: float = MIN_HORIZON_HOURS) -> int | None:
    """The same horizon as a Unix timestamp, for venues that will filter server-side.

    Kalshi's ``/markets`` takes ``min_close_ts`` and Polymarket's Gamma takes
    ``end_date_min``, so the horizon can be applied before the payload is built rather than
    after. That is not only cheaper: a page of a hundred markets that are all about to close
    would otherwise come back, get screened to nothing, and look like a venue with no markets.

    The server-side filter is an optimisation and never the authority — ``screen_market``
    re-checks every row that comes back, so a venue that ignores the parameter (or interprets
    it differently) cannot widen the gate.
    """
    try:
        current = timeutil.parse_iso(str(now))
    except (TypeError, ValueError):
        return None
    return int(current.timestamp() + max(0.0, float(min_horizon_hours)) * 3600.0)


def min_close_iso(*, now: str, min_horizon_hours: float = MIN_HORIZON_HOURS) -> str | None:
    """The same horizon as an ISO-8601 instant, for Gamma's ``end_date_min``."""
    seconds = min_close_epoch_seconds(now=now, min_horizon_hours=min_horizon_hours)
    if seconds is None:
        return None
    return timeutil.format_iso(datetime.fromtimestamp(seconds, tz=timezone.utc))


def observable_markets(
    markets: Sequence[PredMarket], *, now: str, min_horizon_hours: float = MIN_HORIZON_HOURS
) -> list[PredMarket]:
    """Just the survivors, for callers that have already reported the exclusions."""
    return list(screen_markets(markets, now=now, min_horizon_hours=min_horizon_hours)["observable"])


__all__ = [
    "CLOSE_TIME_UNKNOWN",
    "DERIVED_COMBINATION",
    "HORIZON_TOO_SHORT",
    "MIN_HORIZON_HOURS",
    "NOT_ACCEPTING_ORDERS",
    "SCREENING_VERSION",
    "Screen",
    "hours_to_close",
    "min_close_epoch_seconds",
    "min_close_iso",
    "observable_markets",
    "screen_market",
    "screen_markets",
    "screening_status_line",
]

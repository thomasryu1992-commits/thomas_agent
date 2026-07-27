"""Where the daily-loss breaker's number comes from, and what it is worth.

The breaker read `read_live_outcomes`, whose only writer is `live_leg.execute_live_exit` — the
autonomous leg no entry point may import. The canary path is entry-only and its positions are
closed by the operator on the venue, so no closed outcome could ever reach that ledger. The
breaker therefore answered `0.0 USDT, not breached` with total confidence on a day the venue
had realized a loss, and the readiness board rendered that as PASS.

`cycle.py` states the rule this broke, about a different instance of the same failure:
*a breaker that cannot trip is not a breaker.*
"""

from __future__ import annotations

from runtime.mvp_runtime.crypto.live_pnl import (
    LIVE_PNL_NO_SOURCE,
    PNL_SOURCE_LOCAL_LEDGER,
    PNL_SOURCE_VENUE,
    live_risk_snapshot,
)

NOW = "2026-07-26T14:00:00Z"


def test_an_empty_ledger_is_reported_as_no_source_not_as_clear(tmp_path):
    """0.0 because nothing was lost, versus 0.0 because nothing was recorded."""
    snap = live_risk_snapshot(limit_usdt=20.0, root=tmp_path, now=NOW)
    assert snap["daily_realized_pnl_usdt"] == 0.0
    assert snap["history_error"] == LIVE_PNL_NO_SOURCE
    assert snap["pnl_source"] == PNL_SOURCE_LOCAL_LEDGER


def test_the_venue_figure_is_the_authority_when_supplied(tmp_path):
    """It is the truer number: what the venue actually took, fees and funding included."""
    snap = live_risk_snapshot(limit_usdt=20.0, root=tmp_path, now=NOW,
                              venue_realized_pnl_usdt=-0.10)
    assert snap["daily_realized_pnl_usdt"] == -0.10
    assert snap["pnl_source"] == PNL_SOURCE_VENUE
    assert snap["history_error"] is None
    assert snap["daily_loss_limit_breached"] is False


def test_the_breaker_can_now_actually_trip(tmp_path):
    """The property the whole change exists for."""
    snap = live_risk_snapshot(limit_usdt=20.0, root=tmp_path, now=NOW,
                              venue_realized_pnl_usdt=-25.0)
    assert snap["daily_loss_limit_breached"] is True
    assert snap["pnl_source"] == PNL_SOURCE_VENUE


def test_the_limit_boundary_is_inclusive(tmp_path):
    """A loss exactly at the limit trips it. `<=` rather than `<`, so a cap set to the amount
    an operator is willing to lose stops at that amount rather than one tick past it."""
    assert live_risk_snapshot(limit_usdt=20.0, root=tmp_path, now=NOW,
                              venue_realized_pnl_usdt=-20.0)["daily_loss_limit_breached"] is True
    assert live_risk_snapshot(limit_usdt=20.0, root=tmp_path, now=NOW,
                              venue_realized_pnl_usdt=-19.99)["daily_loss_limit_breached"] is False


def test_an_unconfigured_limit_still_reads_as_breached_with_a_venue_figure(tmp_path):
    """"Zero means not configured, never unlimited" has to survive the new source.

    It nearly did not. `breached = configured and realized <= -limit` reads naturally and is
    backwards: it hands an unconfigured limit a clean bill of health on the strength of a
    profitable day, which is precisely the fail-open `daily_loss_limit_breached` forbids.
    A profit is used here deliberately — under the wrong expression this is the case that
    passes.
    """
    for limit in (0.0, None, -5.0):
        snap = live_risk_snapshot(limit_usdt=limit, root=tmp_path, now=NOW,
                                  venue_realized_pnl_usdt=+100.0)
        assert snap["daily_loss_limit_configured"] is False
        assert snap["daily_loss_limit_breached"] is True, f"limit={limit} must read as breached"


def test_a_venue_profit_never_trips_the_breaker(tmp_path):
    snap = live_risk_snapshot(limit_usdt=20.0, root=tmp_path, now=NOW,
                              venue_realized_pnl_usdt=+5.0)
    assert snap["daily_loss_limit_breached"] is False

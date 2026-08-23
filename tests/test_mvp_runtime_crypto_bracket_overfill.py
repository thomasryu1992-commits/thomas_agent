"""A Close-All stop that closed more than the book knew about, priced as a 449x profit.

On 2026-08-21T09:03:43Z the stop-slippage probe settled BTCUSDT `live_position_6e5dff2b178f`
and wrote `live_out_e56cf310dcc07d9c6edd` into `live_outcomes.jsonl`:

    quantity 0.001, entry 77881.30, exit 77708.50, stop 77686.50,
    realized_pnl_usdt  77.5357     result_R  398.02720739

That trade was a LONG stopped out slightly better than its stop. The truth is a **loss** of
0.1728 USDT, -0.887R. The recorded figure is the arithmetic of a close that was twice the size
of the open:

    0.002 * 77708.50  -  0.001 * 77881.30  =  77.5357

to the cent, and 77.5357 / 0.1948 reproduces the stored `result_R` exactly. Of the 28 rows in
that ledger this is the only one that does not satisfy `(exit - entry) * qty` under its own
sign convention; the other 27 match to eight decimals.

**Why the extra 0.001 was there is not what these tests pin.** They pin what the runtime did
with it. The stop leg is a `closePosition` STOP_MARKET, and the venue treats that as Close-All:
it closes whatever is actually open, which `live_leg` documents as the point of the flag
(:39-41). So an over-fill relative to the book is a state the venue can and did produce. Four
and a half minutes earlier the cycle had already halted the symbol with `LIVE_ROUTING_BOOK_DRIFT`
and `live_settled=null` — a quantity disagreement, not a missing position.

What is missing is a quantity invariant on the settlement path that consumes that fill.
`realized_pnl_usdt` prices `exit_quote - entry_quote` from two different sources — the venue's
fill for one side, the book's record for the other — and never asks whether they describe the
same amount of BTC. The other two exit paths do ask, which is the shape of the argument here:

  * `reconcile_order` (the runtime-closed path) refuses on `abs(filled - wanted) > 1e-9`;
  * `exit_fill_from_history` (the fill-history path) refuses a fill that overshoots the
    remaining quantity, and refuses a total that never covers it.

So the invariant is not a new idea in this codebase. It is present twice and absent once, and
the once is the path that produces every `close_reason: stop_loss` sample.

**No fixture here is verbatim from the venue.** The raw algo payload for that settlement was
never persisted — `records.jsonl` and `audit_events.jsonl` carry no mention of the outcome id,
the position id, or either order id, because the probe settles outside the scheduler. The
numbers below are reconstructed from the outcome row, which is the only surviving record, and
the reconstruction is validated by the fact that it reproduces the stored P&L and R exactly
(`test_the_recorded_figure_is_the_overfill_arithmetic`). Field names and shapes follow
`test_mvp_runtime_crypto_algo_settlement.py`, whose payloads *are* verbatim.

The first version of this file asserted the defect, so that it passed against the code as it
stood. It is now inverted: `test_realized_pnl_refuses_an_overfill_against_the_book` pins the
refusal, and the control cases beside it pin that nothing else moved.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.crypto.live_execution import (
    MISMATCH,
    fill_facts,
    normalize_algo_order,
    reconcile_order,
)
from runtime.mvp_runtime.crypto.live_leg import (
    exit_fill_from_history,
    realized_pnl_usdt,
)

# The book: what the runtime believed it held. Reconstructed from the outcome row's
# quantity/entry_price; `entry_quote_usdt` is what the probe stores from the entry fill.
BTC_POSITION = {
    "position_id": "live_position_6e5dff2b178fb482436c",
    "symbol": "BTCUSDT",
    "direction": "LONG",
    "quantity": 0.001,
    "entry_price": 77881.30,
    "entry_quote_usdt": 77.8813,
    "opened_at_utc": "2026-08-21T08:47:37Z",
}

# The stop leg as the algo endpoint reports one, carrying a fill of 0.002 — twice the book.
# `closePosition: True` is the flag that makes this possible: the venue closed what was open,
# not what this runtime intended.
BTC_STOP_OVERFILLED = {
    "algoId": 2000001375858172,
    "algoStatus": "FINISHED",
    "algoType": "CONDITIONAL",
    "actualPrice": "77708.50",
    "actualQty": "0.002",
    "actualType": "MARKET",
    "clientAlgoId": "TAI_BTCUSDT_SL_6e5dff2b178fb48243",
    "closePosition": True,
    "orderType": "STOP_MARKET",
    "quantity": "0.001",
    "reduceOnly": True,
    "side": "SELL",
    "stopPrice": "77686.50",
    "symbol": "BTCUSDT",
    "triggerPrice": "77686.50",
}

RECORDED_PNL = 77.5357
RECORDED_R = 398.02720739
RISK_USDT = 0.1948

TRUE_PNL = -0.1728
TRUE_R = -0.88706366


# --- the arithmetic, so the reconstruction is not taken on trust --------------

def test_the_recorded_figure_is_the_overfill_arithmetic():
    """0.002 out against 0.001 in, to the cent. This is what licenses every fixture above."""
    assert 0.002 * 77708.50 - 0.001 * 77881.30 == pytest.approx(RECORDED_PNL, abs=5e-5)
    assert RECORDED_PNL / RISK_USDT == pytest.approx(RECORDED_R, abs=5e-6)


def test_the_true_result_is_a_loss_slightly_better_than_a_full_stop():
    """A LONG stopped out below its entry cannot be a profit. It filled 22.0 above the stop,
    so it lost less than the 1R the stop was placed to cap — which is what the recorded
    `stop_slippage_bps` of -2.8319 already says, and that field was never wrong."""
    assert (77708.50 - 77881.30) * 0.001 == pytest.approx(TRUE_PNL, abs=5e-9)
    assert TRUE_PNL / RISK_USDT == pytest.approx(TRUE_R, abs=5e-8)
    assert -1.0 < TRUE_R < 0.0
    assert (77686.50 - 77708.50) / 77686.50 * 1e4 == pytest.approx(-2.8319, abs=5e-5)


# --- the defect ---------------------------------------------------------------

def test_realized_pnl_refuses_an_overfill_against_the_book():
    """THE FIX. Before it, this returned +77.5357 — one leg's notional, priced as a result.

    `realized_pnl_usdt` takes the exit quote from the venue and the entry quote from the book
    and subtracts them. When the venue closed twice the book's size the difference is not a
    P&L, and nothing compared `exit_quantity` against `position['quantity']`, so a number came
    back that no caller could tell from a real one.
    """
    facts = fill_facts(normalize_algo_order(BTC_STOP_OVERFILLED))
    assert facts["executed_qty"] == pytest.approx(0.002)
    assert facts["cum_quote"] is None  # algo orders do not report one; it gets derived

    pnl, detail = realized_pnl_usdt(BTC_POSITION, facts)

    assert pnl is None, "an exit twice the book's size must not be priced"
    # The refusal names both numbers, so the operator resolving the OPEN book can see the
    # disagreement instead of rediscovering it.
    assert detail["quantity_mismatch"] == {"book": 0.001, "venue_filled": 0.002}
    assert detail["exit_quantity"] == pytest.approx(2 * BTC_POSITION["quantity"])
    assert detail["entry_quote_usdt"] == pytest.approx(
        BTC_POSITION["quantity"] * BTC_POSITION["entry_price"], abs=5e-5
    )


def test_the_refusal_is_the_documented_unsettleable_path_not_a_new_one():
    """Returning `(None, detail)` is what this function already does for figures it does not
    have, and `settle_venue_closed_position` already knows what that means: leave the book
    OPEN, report `EXIT_UNSETTLEABLE`, let reconciliation refuse new entries on the symbol until
    an operator resolves it. The fix adds a reason to refuse, not a way to fail."""
    missing_price = {"executed_qty": 0.001, "avg_price": None, "cum_quote": None}
    pnl, detail = realized_pnl_usdt(BTC_POSITION, missing_price)
    assert pnl is None                              # the pre-existing refusal
    assert "quantity_mismatch" not in detail        # ...and it is not attributed to size

    pnl, detail = realized_pnl_usdt(
        BTC_POSITION, fill_facts(normalize_algo_order(BTC_STOP_OVERFILLED)))
    assert pnl is None                              # the new one
    assert detail["pnl_source"] == "venue_fills_gross"   # same shape, same keys


def test_a_lot_step_rounding_difference_is_not_a_mismatch():
    """The tolerance mirrors `exit_fill_from_history`: what a lot-step rounding can leave
    behind, not a licence to accept a different size. A float that differs in the last bits
    must still price, or every exit starts refusing."""
    facts = fill_facts(normalize_algo_order(dict(BTC_STOP_OVERFILLED, actualQty="0.001")))
    facts = dict(facts, executed_qty=0.001 + 1e-12)
    pnl, detail = realized_pnl_usdt(BTC_POSITION, facts)
    assert pnl is not None
    assert "quantity_mismatch" not in detail


def test_the_same_fill_at_the_book_quantity_prices_correctly():
    """The control. The function is not broken in general — feed it a close that matches the
    book and it returns the loss. Only the size disagreement is unhandled."""
    matched = dict(BTC_STOP_OVERFILLED, actualQty="0.001", quantity="0.001")
    pnl, detail = realized_pnl_usdt(BTC_POSITION, fill_facts(normalize_algo_order(matched)))
    assert pnl == pytest.approx(TRUE_PNL, abs=5e-6)
    assert pnl / RISK_USDT == pytest.approx(TRUE_R, abs=5e-5)
    assert detail["exit_quantity"] == pytest.approx(BTC_POSITION["quantity"])


# --- the invariant exists twice elsewhere -------------------------------------

def test_the_runtime_closed_path_refuses_the_same_size_disagreement():
    """`reconcile_order` catches exactly this on the path that sends its own exit order. Same
    facts, different door, opposite outcome."""
    intent = {"symbol": "BTCUSDT", "side": "SELL", "quantity": 0.001, "reduce_only": True}
    venue_order = {
        "symbol": "BTCUSDT", "side": "SELL", "status": "FILLED",
        "executedQty": "0.002", "reduceOnly": True,
    }
    status, problems = reconcile_order(intent, venue_order)
    assert status == MISMATCH
    assert any("executedQty" in p for p in problems)


def test_the_fill_history_path_refuses_a_close_that_overshoots_the_book():
    """And so does the third path. A closing fill larger than the position refuses the whole
    answer rather than attributing the surplus to this position."""
    rows = [{
        "side": "SELL",
        "time": 1787389423000,          # after opened_at_utc
        "qty": "0.002",
        "quoteQty": "155.4170",
        "orderId": 2000001375858172,
    }]
    fill, detail = exit_fill_from_history(BTC_POSITION, rows)
    assert fill is None, "an overshooting fill must not be priced as this position's close"
    assert detail is None


def test_the_fill_history_path_accepts_the_matching_close():
    """Control for the one above: the same shape at the book's size settles normally, and to
    the true loss."""
    rows = [{
        "side": "SELL",
        "time": 1787389423000,
        "qty": "0.001",
        "quoteQty": "77.7085",
        "orderId": 2000001375858172,
    }]
    fill, _detail = exit_fill_from_history(BTC_POSITION, rows)
    assert fill is not None
    assert fill["executed_qty"] == pytest.approx(0.001)
    pnl, _ = realized_pnl_usdt(BTC_POSITION, fill)
    assert pnl == pytest.approx(TRUE_PNL, abs=5e-6)

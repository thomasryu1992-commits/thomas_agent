"""Carrying the previous epoch's observed buy, sell and settlement day (PORTFOLIO_CASH_FLOW_LEDGER §11, Thomas
2026-11-07: "11-03 관찰 인정"; scope: all three, checked by code, at Thomas's terminal only).

Epoch 0 saw the trades and the settlement day pass with no residual, but its windows could not name the day (the
order list's settlementDate is null live). The carry is checked against the previous epoch's own windows and the
transaction list's settled sells, written once per epoch, and counted by ``toss_evidence`` like any evidence.
Synthetic ledgers under tmp_path only.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.errors import ToolError
from runtime.mvp_runtime.holdings import cash_flows
from tests._h6d_support import hourly, ledger, order, start_epoch, toss_fire

DAY = "2026-10-10"                   # the KST day the settlement windows end on
TODAY = "2026-10-12"
NOW = "2026-10-12T03:00:00Z"
NO_DATE = order("s9", "SELL", "50000", "2026-10-08T01:00:00Z")      # the live order shape: no settlementDate
SETTLED = {"transactionType": "SELL", "currency": "KRW", "settlementDate": DAY, "settled": True,
           "isCancellation": False, "assetClass": "STOCK", "symbol": "LEAKSYM", "amount": "50000"}


def _epoch_zero(tmp_path, *, day_windows=12, residual_at=None):
    """Epoch 0: an explained buy, an explained sell, then ``day_windows`` windows ending on DAY."""
    toss_fire(tmp_path, 1_000_000, "2026-10-09T10:00:00Z")
    toss_fire(tmp_path, 900_000, "2026-10-09T11:00:00Z", [order("b1", "BUY", "100000", "2026-10-09T10:30:00Z")])
    toss_fire(tmp_path, 1_000_000, "2026-10-09T12:00:00Z", [order("s1", "SELL", "100000", "2026-10-09T11:30:00Z")])
    for i, at in enumerate(hourly("2026-10-09T15:30:00Z", day_windows)):   # the first closes the 12:00Z window
        cash = 1_000_000 + (5_000 if residual_at is not None and i >= residual_at else 0)
        toss_fire(tmp_path, cash, at, [NO_DATE])


def _needs(tmp_path, today=TODAY):
    rows = ledger(tmp_path)
    verdict = cash_flows.toss_semantics(rows, cash_flows.load_state(tmp_path), today_kst=today)[0]
    return verdict, cash_flows.toss_needs(rows, verdict, today_kst=today)


def _carry(tmp_path, transactions=(SETTLED,), confirm=DAY, **kw):
    return cash_flows.carry_evidence(tmp_path, list(transactions), requested_by=kw.pop("by", "thomas"),
                                     reason=kw.pop("reason", "11-03 observation"), confirm=confirm,
                                     interactive=kw.pop("interactive", True), now=NOW)


def test_the_observed_trades_and_settlement_day_carry_and_the_check_passes(tmp_path):
    _epoch_zero(tmp_path)
    start_epoch(tmp_path, now="2026-10-11T00:00:00Z")
    assert _needs(tmp_path) == ("WAITING", ["buy", "sell", "settlement_day"])
    candidate = cash_flows.carry_candidate(ledger(tmp_path), [SETTLED], today_kst=TODAY)
    assert {k: candidate[k] for k in ("epoch", "from_epoch", "settlement_day", "pocket", "buys", "sells",
                                      "day_windows")} == {"epoch": 1, "from_epoch": 0, "settlement_day": DAY,
                                                          "pocket": "KRW", "buys": 1, "sells": 1, "day_windows": 12}
    written = _carry(tmp_path)
    assert written["event"] == "toss_evidence_carried" and written["requested_by"] == "thomas"
    assert _needs(tmp_path) == ("PASS", [])
    assert "LEAKSYM" not in str(ledger(tmp_path)) and written["transaction"].startswith("tx:")


def test_nothing_carries_before_an_epoch_exists(tmp_path):
    _epoch_zero(tmp_path)
    with pytest.raises(ToolError, match="still in epoch 0"):
        cash_flows.carry_candidate(ledger(tmp_path), [SETTLED], today_kst=TODAY)


def test_it_carries_once_per_epoch(tmp_path):
    _epoch_zero(tmp_path)
    start_epoch(tmp_path, now="2026-10-11T00:00:00Z")
    _carry(tmp_path)
    with pytest.raises(ToolError, match="carried once"):
        _carry(tmp_path)


@pytest.mark.parametrize("bad", [
    {**SETTLED, "settled": False},                      # not settled yet
    {**SETTLED, "isCancellation": True},                # a cancellation
    {**SETTLED, "transactionType": "BUY"},              # not a sell
    {**SETTLED, "settlementDate": TODAY},               # today has not passed
])
def test_only_a_settled_sell_before_today_names_a_day(tmp_path, bad):
    _epoch_zero(tmp_path)
    start_epoch(tmp_path, now="2026-10-11T00:00:00Z")
    with pytest.raises(ToolError, match="no settled sell"):
        cash_flows.carry_candidate(ledger(tmp_path), [bad], today_kst=TODAY)


def test_a_day_with_too_few_windows_is_refused_with_its_count(tmp_path):
    _epoch_zero(tmp_path, day_windows=11)
    start_epoch(tmp_path, now="2026-10-11T00:00:00Z")
    with pytest.raises(ToolError, match="11 epoch-0 window"):
        cash_flows.carry_candidate(ledger(tmp_path), [SETTLED], today_kst=TODAY)


def test_a_residual_on_the_day_is_refused(tmp_path):
    _epoch_zero(tmp_path, day_windows=14, residual_at=6)
    start_epoch(tmp_path, now="2026-10-11T00:00:00Z")
    with pytest.raises(ToolError) as exc:
        cash_flows.carry_candidate(ledger(tmp_path), [SETTLED], today_kst=TODAY)
    assert exc.value.reason_code == cash_flows.CARRY_REFUSED and "not clean" in str(exc.value)


def test_the_typed_day_must_match_and_the_terminal_and_names_are_required(tmp_path):
    _epoch_zero(tmp_path)
    start_epoch(tmp_path, now="2026-10-11T00:00:00Z")
    for kw in ({"confirm": "2026-10-09"}, {"interactive": False}, {"by": " "}, {"reason": ""}):
        with pytest.raises(ToolError) as exc:
            _carry(tmp_path, **kw)
        assert exc.value.reason_code == cash_flows.CARRY_REFUSED
    assert not [r for r in ledger(tmp_path) if r["event"] == "toss_evidence_carried"]


def test_a_later_problem_in_the_epoch_voids_what_was_carried(tmp_path):
    _epoch_zero(tmp_path)
    start_epoch(tmp_path, now="2026-10-11T00:00:00Z")
    _carry(tmp_path)
    assert _needs(tmp_path)[0] == "PASS"
    toss_fire(tmp_path, 1_000_000, "2026-10-12T04:00:00Z")
    # +53,000 beside a buy: not round within the fill tolerance, so the residual stays open: a problem window
    toss_fire(tmp_path, 953_000, "2026-10-12T05:00:00Z", [order("b2", "BUY", "100000", "2026-10-12T04:30:00Z")])
    verdict, _needs_now = _needs(tmp_path, today="2026-10-13")
    assert verdict == "MIXED"


def test_carrying_leaves_the_reconciliation_digest_alone():
    """The epoch was started on this digest; carrying evidence is no reconciliation change."""
    assert cash_flows.reconciliation_digest().startswith("40748837")

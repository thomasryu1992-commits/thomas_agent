"""The H6 settlement day read off the Toss transaction list (PORTFOLIO_CASH_FLOW_LEDGER §10, Thomas 2026-11-07, A).

Live, the closed-order list returns ``execution.settlementDate: null`` on every filled order, so the windows never
recorded a settlement. The transaction list carries the date. Synthetic ledgers under tmp_path only; the rows are
shaped like the live probe of 2026-11-07 (a settled KRW STOCK sell, ``YYYY-MM-DD``).
"""

from __future__ import annotations

import json

from runtime.mvp_runtime.errors import ToolError
from runtime.mvp_runtime.holdings import cash_flows
from tests._h6d_support import Toss, hourly, ledger, order, snap, start_epoch, toss_fire

DAY = "2026-10-10"                   # the KST day the windows below end on
AFTER_DAY = "2026-10-11"
NO_DATE = order("s9", "SELL", "50000", "2026-10-08T01:00:00Z")       # the live shape: no settlementDate
SOLD = {"transactionType": "SELL", "marketCountry": "KR", "currency": "KRW", "symbol": "LEAKSYM", "assetClass": "STOCK",
        "isinCode": "KRLEAK000001", "quantity": "1", "filledPrice": "98765", "amount": "98765", "commission": "15",
        "tax": "0", "netAmount": "98750", "exchangeRate": None, "settlementDate": DAY, "settled": True,
        "transactionTime": "2026-10-10T06:30:00+09:00", "isCancellation": False}


def _fires(tmp_path, count, *, transactions=(SOLD,), failing_at=None):
    for i, at in enumerate(hourly("2026-10-09T15:30:00Z", count)):
        answer = ToolError("TOSS_TRANSPORT", "x") if failing_at is not None and i == failing_at else transactions
        toss_fire(tmp_path, 1_000_000, at, [NO_DATE], transactions=answer)


def _evidence(tmp_path, today=AFTER_DAY):
    rows = ledger(tmp_path)
    return cash_flows.toss_evidence(rows, epoch=cash_flows.toss_epoch(rows), today_kst=today)


def _needs(tmp_path, today=AFTER_DAY):
    rows = ledger(tmp_path)
    verdict = cash_flows.toss_semantics(rows, cash_flows.load_state(tmp_path), today_kst=today)[0]
    return verdict, cash_flows.toss_needs(rows, verdict, today_kst=today)


def _windows(tmp_path):
    return [r for r in ledger(tmp_path) if r["event"] == "toss_window_observed"]


def test_a_sell_the_transaction_list_settles_makes_a_settlement_day_where_the_orders_said_nothing(tmp_path):
    start_epoch(tmp_path)
    _fires(tmp_path, 13)                                                  # 12 windows on DAY
    assert all(w["transactions_read"] is True for w in _windows(tmp_path))
    assert all(len(w["settlements"]) == 1 and w["settlements"][0]["pocket"] == "KRW" for w in _windows(tmp_path))
    assert _evidence(tmp_path)["clean_settlement_days"] == [DAY]


def test_without_the_transaction_list_the_same_windows_record_nothing(tmp_path):
    """The defect this fixes: the order list alone, as it answers live."""
    start_epoch(tmp_path)
    _fires(tmp_path, 13, transactions=())
    assert all(w["settlements"] == [] for w in _windows(tmp_path))
    assert _evidence(tmp_path)["clean_settlement_days"] == []


def test_an_unsettled_sale_counts_on_its_expected_day(tmp_path):
    start_epoch(tmp_path)
    _fires(tmp_path, 13, transactions=({**SOLD, "settled": False, "transactionTime": None},))
    assert _evidence(tmp_path)["clean_settlement_days"] == [DAY]


def test_a_buy_a_cancellation_another_day_or_another_currency_is_no_settlement(tmp_path):
    start_epoch(tmp_path)
    others = ({**SOLD, "transactionType": "BUY"}, {**SOLD, "isCancellation": True},
              {**SOLD, "settlementDate": AFTER_DAY}, {**SOLD, "currency": "JPY"},
              {**SOLD, "transactionType": "DEPOSIT", "assetClass": None})
    _fires(tmp_path, 13, transactions=others)
    assert all(w["settlements"] == [] for w in _windows(tmp_path))


def test_a_failed_transaction_read_spoils_its_day_but_not_the_trade_evidence(tmp_path):
    start_epoch(tmp_path, now="2026-10-09T09:00:00Z")
    toss_fire(tmp_path, 1_000_000, "2026-10-09T10:00:00Z")
    toss_fire(tmp_path, 900_000, "2026-10-09T11:00:00Z", [order("b1", "BUY", "100000", "2026-10-09T10:30:00Z")],
              transactions=ToolError("TOSS_TRANSPORT", "x"))
    assert _evidence(tmp_path)["buy_explained"] == 1                       # the order list still explains the fill
    _fires(tmp_path, 15, failing_at=6)
    assert _evidence(tmp_path)["clean_settlement_days"] == []
    rows = ledger(tmp_path)
    diagnosis = cash_flows.settlement_diagnosis(rows, cash_flows.load_state(tmp_path), today_kst=AFTER_DAY)
    day = next(d for d in diagnosis["days"] if d["day"] == DAY)
    assert "transactions_unread" in day["reasons"] and day["clean"] is False


def test_a_truncated_transaction_read_is_unread(tmp_path):
    start_epoch(tmp_path)
    _fires(tmp_path, 13, transactions=([SOLD], True))
    assert all(w["transactions_read"] is False for w in _windows(tmp_path))
    assert _evidence(tmp_path)["clean_settlement_days"] == []


def test_the_epoch_check_passes_on_a_buy_a_sell_and_a_transaction_settlement(tmp_path):
    start_epoch(tmp_path, now="2026-10-09T09:00:00Z")
    toss_fire(tmp_path, 1_000_000, "2026-10-09T10:00:00Z")
    toss_fire(tmp_path, 900_000, "2026-10-09T11:00:00Z", [order("b1", "BUY", "100000", "2026-10-09T10:30:00Z")])
    toss_fire(tmp_path, 1_000_000, "2026-10-09T12:00:00Z", [order("s1", "SELL", "100000", "2026-10-09T11:30:00Z")])
    assert _needs(tmp_path) == ("WAITING", ["settlement_day"])
    _fires(tmp_path, 13)
    assert _needs(tmp_path) == ("PASS", [])


def test_the_ledger_keeps_a_hash_never_the_row(tmp_path):
    start_epoch(tmp_path)
    _fires(tmp_path, 3)
    text = json.dumps(ledger(tmp_path))
    for leak in ("LEAKSYM", "KRLEAK", "98765", "98750", "06:30:00+09:00"):
        assert leak not in text, leak
    assert all(e["order"][0].startswith("tx:") for w in _windows(tmp_path) for e in w["settlements"])


def test_the_read_asks_for_sells_from_the_day_before_the_window_with_no_upper_bound(tmp_path):
    seen = []

    class Recording(Toss):
        def transactions(self, **kw):
            seen.append(kw)
            return [], False

    cash_flows.collect_toss(Recording(), snap(1_000_000), tmp_path, now="2026-10-09T15:30:00Z")
    cash_flows.collect_toss(Recording(), snap(1_000_000), tmp_path, now="2026-10-09T16:30:00Z")
    assert seen == [{"date_from": "2026-10-09", "date_to": None, "transaction_types": ("SELL",)}]


def test_an_unread_list_declines_only_the_income_rule(tmp_path):
    """v3's small inflow needs "nothing settled", which an unread list cannot say; a round deposit does not."""
    failing = ToolError("TOSS_TRANSPORT", "x")
    toss_fire(tmp_path, 1_000_000, "2026-10-09T10:00:00Z")
    toss_fire(tmp_path, 1_000_300, "2026-10-09T11:00:00Z", transactions=failing)            # small inflow
    toss_fire(tmp_path, 1_010_300, "2026-10-09T12:00:00Z", transactions=failing)            # round deposit
    residuals = [r for r in ledger(tmp_path) if r["event"] == "toss_residual"]
    resolved = {r["resolves"]: r["kind"] for r in ledger(tmp_path) if r["event"] == "flow_resolved"}
    assert residuals[0].get("quiet_window_declined") == "transactions_unread"
    assert residuals[0]["source_event_key"] not in resolved
    assert resolved.get(residuals[1]["source_event_key"]) == "deposit"


def test_the_digest_sees_the_transaction_source(monkeypatch):
    before = cash_flows.reconciliation_digest()
    monkeypatch.setattr(cash_flows, "_DIGEST_TRANSACTIONS", ())
    assert cash_flows.reconciliation_digest() != before


# A sale recorded under its expected date that a later read dates to another day (§10's known gap, closed).
EXPECTED = {**SOLD, "settled": False}
LATER = "2026-10-12"


def _revised(tmp_path, *, at="2026-10-10T16:30:00Z", transactions=None):
    """One fire on the next KST day whose read dates the sale to LATER."""
    answer = ({**SOLD, "settlementDate": LATER},) if transactions is None else transactions
    toss_fire(tmp_path, 1_000_000, at, [NO_DATE], transactions=answer)


def _revisions(tmp_path):
    return [r for r in ledger(tmp_path) if r["event"] == "toss_window_amended"]


def test_a_sale_a_later_read_dates_to_another_day_withdraws_the_expected_day(tmp_path):
    start_epoch(tmp_path)
    _fires(tmp_path, 13, transactions=(EXPECTED,))
    assert _evidence(tmp_path)["clean_settlement_days"] == [DAY]
    _revised(tmp_path)
    revisions = _revisions(tmp_path)
    recorded = [w for w in _windows(tmp_path) if w["settlements"]]
    assert len(revisions) == len(recorded) == 12
    assert {(r["kind"], r["day"], r["revised_day"], r["pocket"]) for r in revisions} == {
        ("settlement_revised", DAY, LATER, "KRW")}
    assert {r["amends"] for r in revisions} == {w["source_event_key"] for w in recorded}
    assert _evidence(tmp_path, today="2026-10-13")["clean_settlement_days"] == []
    rows = ledger(tmp_path)
    diagnosis = cash_flows.settlement_diagnosis(rows, cash_flows.load_state(tmp_path), today_kst="2026-10-13")
    assert "settlement_revised" in next(d for d in diagnosis["days"] if d["day"] == DAY)["reasons"]


def test_reading_the_revised_date_again_adds_nothing(tmp_path):
    start_epoch(tmp_path)
    _fires(tmp_path, 13, transactions=(EXPECTED,))
    _revised(tmp_path)
    _revised(tmp_path, at="2026-10-10T17:30:00Z")
    assert len(_revisions(tmp_path)) == 12


def test_a_row_the_read_no_longer_returns_or_an_unread_list_amends_nothing(tmp_path):
    start_epoch(tmp_path)
    _fires(tmp_path, 13, transactions=(EXPECTED,))
    _revised(tmp_path, transactions=())
    _revised(tmp_path, at="2026-10-10T17:30:00Z", transactions=ToolError("TOSS_TRANSPORT", "x"))
    _revised(tmp_path, at="2026-10-10T18:30:00Z", transactions=([{**SOLD, "settlementDate": LATER}], True))
    assert _revisions(tmp_path) == []


def test_another_sale_on_another_day_or_the_same_date_again_is_no_revision(tmp_path):
    start_epoch(tmp_path)
    _fires(tmp_path, 13, transactions=(EXPECTED,))
    others = ({**SOLD, "settlementDate": LATER, "quantity": "2"},            # a different sale
              {**SOLD, "settlementDate": LATER, "isCancellation": True},     # a cancellation row
              {**SOLD, "settlementDate": LATER, "transactionType": "BUY"},
              {**SOLD, "settled": True})                                      # same day, now settled
    _revised(tmp_path, transactions=others)
    assert _revisions(tmp_path) == []
    assert _evidence(tmp_path, today="2026-10-13")["clean_settlement_days"] == [DAY]


def test_the_revision_line_keeps_hashes_and_leaves_the_digest(tmp_path):
    start_epoch(tmp_path)
    _fires(tmp_path, 13, transactions=(EXPECTED,))
    _revised(tmp_path)
    text = json.dumps(_revisions(tmp_path))
    for leak in ("LEAKSYM", "KRLEAK", "98765", "98750", "06:30:00+09:00"):
        assert leak not in text, leak
    assert all(r["order"][0].startswith("tx:") for r in _revisions(tmp_path))
    assert cash_flows.reconciliation_digest().startswith("40748837")

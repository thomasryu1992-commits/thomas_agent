"""H6d-min — Toss window evidence, late evidence, order identity and semantics epochs (scenarios 21-35).

Evidence is recomputed from ``toss_window_observed`` lines per epoch; an order is counted once; a window
with a residual, a truncated read, an unnameable order or a later amendment is no evidence; late evidence
only withdraws; MIXED recovers only on new evidence; an epoch needs a behaviour change, starts WAITING and
keeps every earlier line and exception. No maturity delay or seal (not in this PR). Synthetic only.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.errors import ToolError
from runtime.mvp_runtime.holdings import cash_flows
from tests._h6d_support import (CURRENT_OK, Feed, collect, events, hourly, ledger, order, resolve, set_cutover,
                                start_epoch, toss_fire)

DAY = "2026-10-10"                   # the KST settlement day the windows below end on
AFTER_DAY = "2026-10-11"
SETTLES = order("s9", "SELL", "50000", "2026-10-08T01:00:00Z", settlement=DAY)    # filled before every window


def _evidence(tmp_path, epoch=1, today=AFTER_DAY):
    return cash_flows.toss_evidence(ledger(tmp_path), epoch=epoch, today_kst=today)


def _semantics(tmp_path, today=AFTER_DAY):
    return cash_flows.toss_semantics(ledger(tmp_path), cash_flows.load_state(tmp_path), today_kst=today)[0]


def _buy_and_sell_windows(tmp_path):
    """Epoch 1; an explained buy window, then an explained sell window."""
    start_epoch(tmp_path, now="2026-10-09T09:00:00Z")
    toss_fire(tmp_path, 1_000_000, "2026-10-09T10:00:00Z")
    toss_fire(tmp_path, 900_000, "2026-10-09T11:00:00Z", [order("b1", "BUY", "100000", "2026-10-09T10:30:00Z")])
    toss_fire(tmp_path, 1_000_000, "2026-10-09T12:00:00Z", [order("s1", "SELL", "100000", "2026-10-09T11:30:00Z")])


def _settlement_fires(tmp_path, count, *, krw=1_000_000, residual_at=None):
    for i, at in enumerate(hourly("2026-10-09T15:30:00Z", count)):
        cash = krw + (5_000 if residual_at is not None and i >= residual_at else 0)
        toss_fire(tmp_path, cash, at, [SETTLES])


# 21
def test_an_explained_buy_is_evidence_once(tmp_path):
    _buy_and_sell_windows(tmp_path)
    toss_fire(tmp_path, 1_000_000, "2026-10-09T13:00:00Z", [order("b1", "BUY", "100000", "2026-10-09T10:30:00Z")])
    assert _evidence(tmp_path)["buy_explained"] == 1                                # re-listed later: still once


# 22
def test_an_explained_sell_is_evidence(tmp_path):
    _buy_and_sell_windows(tmp_path)
    assert _evidence(tmp_path)["sell_explained"] == 1


# 23
def test_a_settlement_day_needs_twelve_clean_windows_and_to_have_passed(tmp_path):
    start_epoch(tmp_path)
    _settlement_fires(tmp_path, 12)                                                  # 11 windows
    assert _evidence(tmp_path)["clean_settlement_days"] == []
    toss_fire(tmp_path, 1_000_000, "2026-10-10T03:30:00Z", [SETTLES])               # the 12th
    assert _evidence(tmp_path)["clean_settlement_days"] == [DAY]
    assert _evidence(tmp_path, today=DAY)["clean_settlement_days"] == []            # not before it has passed


# 24
def test_a_residual_on_the_settlement_day_spoils_it(tmp_path):
    start_epoch(tmp_path)
    _settlement_fires(tmp_path, 15, residual_at=5)
    assert _evidence(tmp_path)["clean_settlement_days"] == []


# 25
def test_a_truncated_read_is_no_evidence_and_only_it_may_be_closed_as_read_incomplete(tmp_path):
    set_cutover(tmp_path)
    start_epoch(tmp_path)
    toss_fire(tmp_path, 1_000_000, "2026-10-09T04:00:00Z")
    buy = order("b1", "BUY", "100000", "2026-10-09T04:30:00Z")
    toss_fire(tmp_path, 900_000, "2026-10-09T05:00:00Z", [buy], truncated=True)
    assert _evidence(tmp_path)["buy_explained"] == 0
    toss_fire(tmp_path, 950_000, "2026-10-09T06:00:00Z", truncated=True)              # a residual, truncated
    toss_fire(tmp_path, 990_000, "2026-10-09T07:00:00Z")                              # a residual, complete
    cut, whole = (r["source_event_key"] for r in events(tmp_path, "toss_residual"))
    with pytest.raises(ToolError) as exc:
        resolve(tmp_path, whole, "read_incomplete")
    assert exc.value.reason_code == cash_flows.RESOLVE_KIND
    assert resolve(tmp_path, cut, "read_incomplete")["kind"] == "read_incomplete"


# 26
def test_a_late_order_in_an_explained_window_contradicts_it(tmp_path):
    start_epoch(tmp_path)
    toss_fire(tmp_path, 1_000_000, "2026-10-09T10:00:00Z")
    toss_fire(tmp_path, 1_000_000, "2026-10-09T11:00:00Z")                           # explained: nothing moved
    assert _semantics(tmp_path) == "WAITING"
    late = order("b5", "BUY", "100000", "2026-10-09T10:30:00Z")
    toss_fire(tmp_path, 1_000_000, "2026-10-09T12:00:00Z", [late])
    (amendment,) = events(tmp_path, "toss_window_amended")
    assert amendment["kind"] == "late_fill" and amendment["window_end"] == "2026-10-09T11:00:00Z"
    assert _semantics(tmp_path) == "MIXED"


# 27
def test_a_revised_settlement_date_withdraws_the_day(tmp_path):
    start_epoch(tmp_path)
    _settlement_fires(tmp_path, 14)
    assert _evidence(tmp_path)["clean_settlement_days"] == [DAY]
    moved = order("s9", "SELL", "50000", "2026-10-08T01:00:00Z", settlement="2026-10-12")
    toss_fire(tmp_path, 1_000_000, "2026-10-10T05:30:00Z", [moved])
    assert {a["kind"] for a in events(tmp_path, "toss_window_amended")} == {"settlement_revised"}
    assert _evidence(tmp_path)["clean_settlement_days"] == []


# 28
def test_amendments_are_written_once_however_often_the_evidence_is_reread(tmp_path):
    start_epoch(tmp_path)
    toss_fire(tmp_path, 1_000_000, "2026-10-09T10:00:00Z")
    toss_fire(tmp_path, 1_000_000, "2026-10-09T11:00:00Z")
    late = [order("b5", "BUY", "100000", "2026-10-09T10:30:00Z")]
    for at in ("2026-10-09T12:00:00Z", "2026-10-09T13:00:00Z", "2026-10-09T14:00:00Z"):
        toss_fire(tmp_path, 1_000_000, at, late)
    assert len(events(tmp_path, "toss_window_amended")) == 1
    windows = events(tmp_path, "toss_window_observed")
    assert windows[0]["buy_orders"] == {"KRW": [], "USD": []}                        # the window is never edited


# 29
def test_late_evidence_withdraws_an_earlier_pass(tmp_path):
    _buy_and_sell_windows(tmp_path)
    _settlement_fires(tmp_path, 13)
    assert _semantics(tmp_path) == "PASS"
    late = order("b7", "BUY", "100000", "2026-10-09T10:45:00Z")                        # inside the buy window
    toss_fire(tmp_path, 1_000_000, "2026-10-10T05:30:00Z", [SETTLES, late])
    assert _semantics(tmp_path) == "MIXED"
    assert _evidence(tmp_path)["buy_explained"] == 0


def test_mixed_recovers_only_on_evidence_gathered_after_it(tmp_path):
    """Closing the residual lifts the MIXED; the buy and sell before it do not come back."""
    set_cutover(tmp_path, "2026-10-09T08:00:00Z")
    _buy_and_sell_windows(tmp_path)
    toss_fire(tmp_path, 1_050_000 - 100_000, "2026-10-09T13:00:00Z",
              [order("b8", "BUY", "100000", "2026-10-09T12:30:00Z")])                # a deposit and a buy
    (residual,) = events(tmp_path, "toss_residual")
    resolve(tmp_path, residual["source_event_key"], "deposit")
    _settlement_fires(tmp_path, 14, krw=950_000)
    evidence = _evidence(tmp_path)
    assert evidence["buy_explained"] == 0 and evidence["sell_explained"] == 0
    assert _semantics(tmp_path) == "WAITING"


# --- order identity (checkpoint §2) -----------------------------------------------------------------

BASE = {"side": "BUY", "currency": "KRW", "symbol": "SYN1", "orderedAt": "2026-10-09T10:20:00Z",
        "execution": {"filledAmount": "100000", "filledAt": "2026-10-09T10:30:00Z"}}


@pytest.mark.parametrize("variant", [
    {**BASE, "execution": {**BASE["execution"], "settlementDate": "2026-10-11"}},     # a settlement arrives
    {**BASE, "execution": {**BASE["execution"], "filledAt": "2026-10-09T10:31:00Z"}},  # a re-read moves a fill
    {**BASE, "execution": {**BASE["execution"], "filledAmount": "100000.0"}},
    {**BASE, "status": "CLOSED"},                                                   # a status appears
    {**BASE, "orderId": "late-id"},                                                 # the id appears
])
def test_one_order_keeps_its_identity_across_reads(variant):
    assert cash_flows._same_order(cash_flows.order_identity(BASE)[1], cash_flows.order_identity(variant)[1])


def test_an_id_only_read_still_matches_the_full_read():
    full = {**BASE, "orderId": "o1"}
    bare = {"orderId": "o1", "side": "BUY", "currency": "KRW", "execution": BASE["execution"]}
    assert cash_flows._same_order(cash_flows.order_identity(full)[1], cash_flows.order_identity(bare)[1])
    other = {**BASE, "orderedAt": "2026-10-09T10:21:00Z"}
    assert not cash_flows._same_order(cash_flows.order_identity(BASE)[1], cash_flows.order_identity(other)[1])


@pytest.mark.parametrize("missing", ["symbol", "orderedAt", "side"])
def test_an_order_that_cannot_be_named_is_unstable(missing):
    row = {k: v for k, v in BASE.items() if k != missing}
    assert cash_flows.order_identity(row) == ("unstable", [])


def test_a_window_with_an_unnameable_order_is_no_evidence_and_is_withdrawn_once(tmp_path):
    start_epoch(tmp_path)
    toss_fire(tmp_path, 1_000_000, "2026-10-09T10:00:00Z")
    nameless = order(None, "BUY", "100000", "2026-10-09T10:30:00Z")                  # no id, no symbol
    toss_fire(tmp_path, 900_000, "2026-10-09T11:00:00Z", [nameless])
    (window,) = events(tmp_path, "toss_window_observed")
    assert window["identity_stable"] is False and _evidence(tmp_path)["buy_explained"] == 0
    for at in ("2026-10-09T12:00:00Z", "2026-10-09T13:00:00Z"):
        toss_fire(tmp_path, 900_000, at, [nameless])
    assert [a["kind"] for a in events(tmp_path, "toss_window_amended")] == ["identity_unstable"]
    assert _semantics(tmp_path) != "MIXED"                                          # withdrawn, not a mismatch


def test_a_reread_without_the_id_is_not_a_late_order(tmp_path):
    start_epoch(tmp_path)
    toss_fire(tmp_path, 1_000_000, "2026-10-09T10:00:00Z")
    toss_fire(tmp_path, 900_000, "2026-10-09T11:00:00Z", [{**BASE, "orderId": "o1"}])
    toss_fire(tmp_path, 900_000, "2026-10-09T12:00:00Z", [BASE])                     # the id is gone this time
    assert events(tmp_path, "toss_window_amended") == [] and _evidence(tmp_path)["buy_explained"] == 1


# --- semantics epochs ---------------------------------------------------------------------------------

def _mixed_epoch_zero(tmp_path):
    set_cutover(tmp_path)
    toss_fire(tmp_path, 1_000_000, "2026-10-09T04:00:00Z")
    toss_fire(tmp_path, 950_000, "2026-10-09T05:00:00Z", [order("b1", "BUY", "100000", "2026-10-09T04:30:00Z")])
    assert _semantics(tmp_path) == "MIXED"


# 30 and 35
def test_a_new_epoch_after_mixed_starts_waiting(tmp_path):
    _mixed_epoch_zero(tmp_path)
    row = start_epoch(tmp_path, now="2026-10-09T06:00:00Z")
    assert row["epoch"] == 1 and row["previous_epoch_status"] == "MIXED" and row["legacy_transition"] is True
    assert _semantics(tmp_path) == "WAITING"


# 31
def test_an_epoch_keeps_every_earlier_line_and_exception(tmp_path):
    _mixed_epoch_zero(tmp_path)
    before = ledger(tmp_path)
    exceptions = cash_flows.post_cutover_exceptions(before)
    start_epoch(tmp_path, now="2026-10-09T06:00:00Z")
    after = ledger(tmp_path)
    assert after[:len(before)] == before and cash_flows.post_cutover_exceptions(after) == exceptions == 1
    assert cash_flows.toss_evidence(after, epoch=0, today_kst=AFTER_DAY)["activity_unexplained"] == 1


# 32
@pytest.mark.parametrize("change", [
    {"interactive": False}, {"requested_by": " "}, {"reason": ""}, {"change_ref": "main"}, {"change_ref": ""},
    {"confirm": "00000000"},
])
def test_an_epoch_without_its_operator_conditions_is_refused(tmp_path, change):
    kw = {"requested_by": "thomas", "reason": "r", "change_ref": "#1203",
          "confirm": cash_flows.reconciliation_digest()[:8], "interactive": True, **change}
    with pytest.raises(ToolError) as exc:
        cash_flows.start_semantics_epoch(tmp_path, **kw)
    assert exc.value.reason_code == cash_flows.EPOCH_REFUSED and events(tmp_path, "toss_semantics_epoch_started") == []


# 33
def test_a_code_change_that_leaves_the_output_unchanged_starts_no_epoch(tmp_path, monkeypatch):
    start_epoch(tmp_path)
    monkeypatch.setattr(cash_flows, "reconciliation_fingerprint", lambda: "0" * 16)   # the shape moved
    with pytest.raises(ToolError) as exc:
        start_epoch(tmp_path, ref="#1300")
    assert exc.value.reason_code == cash_flows.EPOCH_REFUSED and "unchanged" in str(exc.value)


# 34
def test_the_legacy_transition_happens_once_and_a_real_change_opens_the_next(tmp_path, monkeypatch):
    start_epoch(tmp_path)
    with pytest.raises(ToolError):
        start_epoch(tmp_path)                                                     # the same output again
    monkeypatch.setattr(cash_flows, "reconciliation_digest", lambda: "abcdef0123456789")
    row = start_epoch(tmp_path, ref="#1300")
    assert row["epoch"] == 2 and row["legacy_transition"] is False
    assert [r["epoch"] for r in events(tmp_path, "toss_semantics_epoch_started")] == [1, 2]


# 35
def test_an_epoch_is_never_a_pass_even_over_passing_counters(tmp_path):
    set_cutover(tmp_path)
    state = cash_flows.load_state(tmp_path)
    state["toss_evidence"] = {"buy_explained": 1, "sell_explained": 1, "activity_unexplained": 0,
                              "settlement_days": {"2026-10-10": {"windows": 20, "unexplained": 0}}}
    cash_flows._save_state(tmp_path, state)
    collect(tmp_path, Feed(), now="2026-10-12T00:00:00Z")                            # imports the counters once
    assert _semantics(tmp_path, today="2026-10-12") == "PASS"
    start_epoch(tmp_path, now="2026-10-12T01:00:00Z")
    assert _semantics(tmp_path, today="2026-10-12") == "WAITING"
    ready = cash_flows.readiness(tmp_path, now="2026-10-12T01:00:00Z", current=CURRENT_OK)
    assert ready["checks"]["toss_cash_semantics"] == "WAITING" and ready["ready"] is False


def test_the_digest_follows_the_reconciliation_not_the_text(tmp_path):
    assert cash_flows.reconciliation_digest() == cash_flows.reconciliation_digest()
    assert len(cash_flows.reconciliation_fingerprint()) == 16

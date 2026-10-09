"""H6d-min — exception resolution (Thomas 2026-10-09, scenarios 1-9 and the terminal commands).

Closing an exception's review is a ``flow_resolved`` line; the original line is never touched; the event
stays HELD; review closure is neither evidence nor accounting eligibility; what each category may be
closed as is fixed; refusals are decided against the ledger. Synthetic fixtures only.
"""

from __future__ import annotations

import json

import pytest

from runtime.mvp_runtime.errors import ToolError
from runtime.mvp_runtime.holdings import cash_flows
from tests._h6d_support import (CUTOVER, Feed, collect, events, ledger, order, pay, resolve, set_cutover, start_epoch,
                                toss_fire)

T1, T2 = "2026-10-09T04:00:00Z", "2026-10-09T05:00:00Z"


def _residual(tmp_path, krw_after, orders=()):
    set_cutover(tmp_path)
    toss_fire(tmp_path, 1_000_000, T1)
    toss_fire(tmp_path, krw_after, T2, orders)
    (row,) = events(tmp_path, "toss_residual")
    return row


def _refused(code, call):
    with pytest.raises(ToolError) as exc:
        call()
    assert exc.value.reason_code == code, exc.value
    return exc.value


def _semantics(tmp_path):
    return cash_flows.toss_semantics(ledger(tmp_path), cash_flows.load_state(tmp_path), today_kst="2026-10-10")[0]


# 1
def test_a_toss_deposit_alone_is_closed_as_a_deposit_and_proves_nothing(tmp_path):
    row = _residual(tmp_path, 1_050_000)
    key = row["source_event_key"]
    assert cash_flows.post_cutover_exceptions(ledger(tmp_path)) == 1 and _semantics(tmp_path) != "MIXED"
    _refused(cash_flows.RESOLVE_KIND, lambda: resolve(tmp_path, key, "withdrawal"))     # the sign says money came in
    resolve(tmp_path, key, "deposit")
    assert cash_flows.post_cutover_exceptions(ledger(tmp_path)) == 0
    assert events(tmp_path, "toss_residual") == [row]                                 # never edited
    life = cash_flows.lifecycle(ledger(tmp_path))[key]
    assert life == {"category": "toss_residual", "review": "CLOSED", "kind": "deposit",
                    "evidence_validated": False, "accounting_eligible": False}
    assert cash_flows.toss_evidence(ledger(tmp_path), epoch=0, today_kst="2026-10-10")["buy_explained"] == 0


# 2
def test_a_deposit_and_a_buy_in_one_window_is_mixed_and_closing_it_is_no_trade_evidence(tmp_path):
    start_epoch(tmp_path)                       # epoch 1: the windows alone decide
    buy = order("b1", "BUY", "100000", "2026-10-09T04:30:00Z")
    row = _residual(tmp_path, 1_000_000 - 100_000 + 50_000, [buy])
    assert _semantics(tmp_path) == "MIXED"
    resolve(tmp_path, row["source_event_key"], "deposit")
    assert _semantics(tmp_path) == "WAITING"                                          # lifted, not passed
    evidence = cash_flows.toss_evidence(ledger(tmp_path), epoch=1, today_kst="2026-10-10")
    assert evidence["buy_explained"] == 0 and evidence["problem_windows"] == 1        # never promoted


# 3
def test_a_dividend_or_interest_is_closed_as_income_never_as_a_fee(tmp_path):
    row = _residual(tmp_path, 1_003_000)
    _refused(cash_flows.RESOLVE_KIND, lambda: resolve(tmp_path, row["source_event_key"], "fee"))
    assert resolve(tmp_path, row["source_event_key"], "income")["kind"] == "income"


# 4
def test_a_second_resolution_of_one_event_is_refused(tmp_path):
    row = _residual(tmp_path, 1_050_000)
    resolve(tmp_path, row["source_event_key"], "deposit")
    _refused(cash_flows.RESOLVE_DUPLICATE, lambda: resolve(tmp_path, row["source_event_key"], "deposit"))
    assert len(events(tmp_path, "flow_resolved")) == 1


# 5
def test_an_unknown_wrong_or_unauthorised_target_is_refused_and_writes_nothing(tmp_path):
    set_cutover(tmp_path)
    collect(tmp_path, Feed(pay=[pay("p1"), pay("p0", at="2026-10-09T02:00:00Z")]), now=T1)
    before = ledger(tmp_path)
    _refused(cash_flows.RESOLVE_NO_TARGET, lambda: resolve(tmp_path, "pay:nope", "deposit"))
    _refused(cash_flows.RESOLVE_INVALID_TARGET, lambda: resolve(tmp_path, f"cutover_set:{CUTOVER}", "deposit"))
    _refused(cash_flows.RESOLVE_NOT_OPEN, lambda: resolve(tmp_path, "pay:p0", "deposit"))       # before the cutover
    _refused(cash_flows.RESOLVE_REFUSED, lambda: resolve(tmp_path, "pay:p1", "deposit", interactive=False))
    _refused(cash_flows.RESOLVE_REFUSED, lambda: resolve(tmp_path, "pay:p1", "deposit", reason=" "))
    _refused(cash_flows.RESOLVE_REFUSED, lambda: resolve(tmp_path, "pay:p1", "deposit", requested_by=""))
    _refused(cash_flows.RESOLVE_KIND, lambda: resolve(tmp_path, "pay:p1", "gift"))
    _refused(cash_flows.RESOLVE_KIND, lambda: resolve(tmp_path, "pay:p1", "withdrawal"))   # the venue said "in"
    assert ledger(tmp_path) == before
    resolve(tmp_path, "pay:p1", "deposit")
    _refused(cash_flows.RESOLVE_INVALID_TARGET, lambda: resolve(tmp_path, "resolved:pay:p1", "deposit"))


# 6
def test_a_binance_look_alike_is_closed_only_as_excluded_and_stays_held(tmp_path):
    set_cutover(tmp_path)
    deposit = {"id": "d2", "coin": "USDT", "amount": "100.5", "status": 1, "insertTime": cash_flows._iso_ms(T1) - 60_000}
    collect(tmp_path, Feed(crypto_deposit=[deposit], pay=[pay("p2", "100.5", at="2026-10-09T03:58:00Z")]), now=T1)
    assert cash_flows.post_cutover_exceptions(ledger(tmp_path)) == 2
    _refused(cash_flows.RESOLVE_KIND, lambda: resolve(tmp_path, "crypto_deposit:d2", "deposit"))
    resolve(tmp_path, "crypto_deposit:d2", "acknowledged_excluded")
    assert cash_flows.effective_status(ledger(tmp_path))["crypto_deposit:d2"] == "HELD"
    assert set(cash_flows.open_exceptions(ledger(tmp_path))) == {"pay:p2"}         # the other side is its own


# 7
def test_a_reversed_event_is_closed_only_as_excluded_and_is_never_eligible(tmp_path):
    set_cutover(tmp_path)
    withdraw = {"id": "w1", "coin": "BTC", "amount": "0.01", "status": 6, "applyTime": "2026-10-09 03:20:00",
                "completeTime": "2026-10-09 03:25:00"}
    collect(tmp_path, Feed(crypto_withdraw=[withdraw]), now=T1)
    assert cash_flows.post_cutover_exceptions(ledger(tmp_path)) == 0               # READY, not an exception
    collect(tmp_path, Feed(crypto_withdraw=[{**withdraw, "status": 5}]), now=T2)
    assert cash_flows.lifecycle(ledger(tmp_path))["crypto_withdraw:w1"]["category"] == "reversed"
    _refused(cash_flows.RESOLVE_KIND, lambda: resolve(tmp_path, "crypto_withdraw:w1", "withdrawal"))
    resolve(tmp_path, "crypto_withdraw:w1", "acknowledged_excluded")
    assert cash_flows.effective_status(ledger(tmp_path))["crypto_withdraw:w1"] == "HELD"
    assert cash_flows.lifecycle(ledger(tmp_path))["crypto_withdraw:w1"]["accounting_eligible"] is False
    assert not [r for r in ledger(tmp_path) if r.get("accounting_status") == "APPLIED"]


# 8
def test_a_malformed_event_is_closed_only_as_excluded(tmp_path):
    set_cutover(tmp_path)
    collect(tmp_path, Feed(crypto_deposit=[{"id": "x", "coin": "USDT", "status": 1}]), now=T1)
    (row,) = events(tmp_path, "malformed_source_event")
    _refused(cash_flows.RESOLVE_KIND, lambda: resolve(tmp_path, row["source_event_key"], "deposit"))
    resolve(tmp_path, row["source_event_key"], "acknowledged_excluded")
    life = cash_flows.lifecycle(ledger(tmp_path))[row["source_event_key"]]
    assert life["category"] == "malformed" and life["evidence_validated"] is False


# 9
def test_a_new_exception_after_a_resolution_is_told_though_the_count_did_not_grow(tmp_path):
    set_cutover(tmp_path)
    collect(tmp_path, Feed(pay=[pay("p1")]), now=T1)
    first = cash_flows.update_readiness(tmp_path, now=T1)
    cash_flows.mark_exceptions_told(tmp_path, token=first["exceptions_token"], now=T1)
    resolve(tmp_path, "pay:p1", "deposit")
    cleared = cash_flows.update_readiness(tmp_path, now=T1)
    assert cleared["exceptions"] == 0 and cleared["exceptions_untold"] is False
    collect(tmp_path, Feed(pay=[pay("p2", "6", at="2026-10-09T04:30:00Z")]), now=T2)
    second = cash_flows.update_readiness(tmp_path, now=T2)
    assert second["exceptions"] == 1 == first["exceptions"]                       # same count as before
    assert second["exceptions_untold"] is True and second["exceptions_new"] == 1


def test_an_outgoing_payment_is_closed_as_a_withdrawal(tmp_path):
    set_cutover(tmp_path)
    collect(tmp_path, Feed(pay=[pay("p3", "-5")]), now=T1)
    _refused(cash_flows.RESOLVE_KIND, lambda: resolve(tmp_path, "pay:p3", "deposit"))
    assert resolve(tmp_path, "pay:p3", "withdrawal")["category"] == "unverified_payment"


def test_a_semantics_mismatch_keeps_the_window_mixed(tmp_path):
    start_epoch(tmp_path)
    row = _residual(tmp_path, 950_000, [order("b2", "BUY", "100000", "2026-10-09T04:30:00Z")])
    resolve(tmp_path, row["source_event_key"], "semantics_mismatch")
    assert cash_flows.post_cutover_exceptions(ledger(tmp_path)) == 0 and _semantics(tmp_path) == "MIXED"


# --- the terminal commands ----------------------------------------------------------------------------

@pytest.fixture
def board(monkeypatch, tmp_path):
    from scripts import holdings_board

    monkeypatch.setattr(holdings_board, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(holdings_board, "assert_not_foreign_root_run", lambda: None)
    monkeypatch.setattr(holdings_board, "_interactive", lambda: True)
    return holdings_board


def test_resolve_needs_a_terminal_by_and_reason(board, monkeypatch, tmp_path, capsys):
    set_cutover(tmp_path)
    collect(tmp_path, Feed(pay=[pay("p1", "123.45", currency="SECRETCOIN")]), now=T1)
    base = ["--resolve", "pay:p1", "--kind", "deposit"]
    monkeypatch.setattr(board, "_interactive", lambda: False)
    assert board.main([*base, "--by", "thomas", "--reason", "r"]) == board.EXIT_BLOCKED
    monkeypatch.setattr(board, "_interactive", lambda: True)
    assert board.main([*base, "--reason", "r"]) == board.EXIT_BLOCKED                 # no --by
    assert board.main([*base, "--by", "thomas"]) == board.EXIT_BLOCKED                # no --reason
    assert events(tmp_path, "flow_resolved") == []
    assert board.main([*base, "--by", "thomas", "--reason", "bank statement"]) == board.EXIT_OK
    assert len(events(tmp_path, "flow_resolved")) == 1
    out = capsys.readouterr().out
    assert "SECRETCOIN" not in out and "123.45" not in out


def test_flows_lists_exceptions_without_amounts(board, tmp_path, capsys):
    set_cutover(tmp_path)
    collect(tmp_path, Feed(pay=[pay("p1", "123.45", currency="SECRETCOIN")]), now=T1)
    assert board.main(["--flows"]) == board.EXIT_OK
    out = capsys.readouterr().out
    assert "1 open" in out and "pay:p1" in out and "allowed: deposit" in out
    assert "SECRETCOIN" not in out and "123.45" not in out
    assert all(set(row) == {"key", "event", "category", "phase", "review", "kind", "allowed", "direction"}
               for row in cash_flows.flows_view(tmp_path))


def test_the_epoch_command_asks_for_the_digest(board, monkeypatch, tmp_path):
    monkeypatch.setattr("builtins.input", lambda _prompt: "wrong")
    args = ["--semantics-epoch", "--change-ref", "#1203", "--by", "thomas", "--reason", "window model"]
    assert board.main(args) == board.EXIT_BLOCKED and events(tmp_path, "toss_semantics_epoch_started") == []
    monkeypatch.setattr("builtins.input", lambda _prompt: cash_flows.reconciliation_digest()[:8])
    assert board.main(args) == board.EXIT_OK
    (row,) = events(tmp_path, "toss_semantics_epoch_started")
    assert row["epoch"] == 1 and row["legacy_transition"] is True and row["change_ref"] == "#1203"
    monkeypatch.setattr(board, "_interactive", lambda: False)
    assert board.main(args) == board.EXIT_BLOCKED


def test_the_resolution_line_carries_the_required_fields_and_no_amount(tmp_path):
    row = _residual(tmp_path, 1_050_000)
    written = resolve(tmp_path, row["source_event_key"], "deposit")
    for name in cash_flows.REQUIRED_FIELDS["flow_resolved"]:
        assert written[name] not in (None, ""), name
    assert written["source_event_key"] == f"resolved:{row['source_event_key']}"
    assert "50000" not in json.dumps(written) and "residual" not in written


# --- cutover consistency (§8) ------------------------------------------------------------------------

def test_a_toss_window_around_the_cutover_is_unknown_never_post_and_still_blocks(tmp_path):
    set_cutover(tmp_path, "2026-10-09T04:30:00Z")
    toss_fire(tmp_path, 1_000_000, "2026-10-09T03:00:00Z")
    toss_fire(tmp_path, 1_010_000, "2026-10-09T04:00:00Z")                          # wholly before: observed only
    toss_fire(tmp_path, 1_020_000, "2026-10-09T05:00:00Z")                          # the boundary falls inside
    toss_fire(tmp_path, 1_030_000, "2026-10-09T06:00:00Z")                          # wholly after
    rows = ledger(tmp_path)
    boundary = cash_flows._iso_ms("2026-10-09T04:30:00Z")
    phases = [cash_flows.event_phase(r, boundary) for r in rows if r["event"] == "toss_residual"]
    assert phases == ["PRE_CUTOVER", "UNKNOWN", "POST_CUTOVER"]
    flows = {r["phase"]: r for r in cash_flows.flows_view(tmp_path)}
    assert set(flows) == {"UNKNOWN", "POST_CUTOVER"}                                # the unknown one blocks too
    resolve(tmp_path, flows["UNKNOWN"]["key"], "deposit")
    assert cash_flows.post_cutover_exceptions(ledger(tmp_path)) == 1


def test_a_binance_event_is_placed_by_the_venues_time_not_when_it_was_read(tmp_path):
    set_cutover(tmp_path, "2026-10-09T04:30:00Z")
    collect(tmp_path, Feed(pay=[pay("early", at="2026-10-09T04:00:00Z"), pay("late", at="2026-10-09T04:40:00Z")]),
            now="2026-10-09T05:00:00Z")                                               # both read after the cutover
    assert set(cash_flows.open_exceptions(ledger(tmp_path))) == {"pay:late"}

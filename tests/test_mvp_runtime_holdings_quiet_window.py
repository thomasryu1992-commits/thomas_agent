"""Toss deposits and withdrawals by rule: quiet_window (HOLDINGS_STATUS_READ_AND_AUTO_FLOWS F1 A, F2; v2 Thomas
2026-10-28).

A window whose KRW change is a round amount is closed as a deposit or withdrawal by the rule, in the write that
records it; anything else stays an exception for Thomas, who can re-close a rule's resolution once. v2: a fill or
settlement in the window no longer excludes it; "round" is the nearest 10,000 KRW with a remainder within 0.1% of
the day's KRW fills plus 100 KRW (at most 1,000 KRW); an open order at either end excludes it. The rule's flows
are told by direction and count only, and its amounts stay on the local ledger. Synthetic fixtures only; no real
holdings file is read.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.errors import ToolError
from runtime.mvp_runtime.holdings import cash_flows
from tests._h6d_support import events, ledger, order, resolve, set_cutover, start_epoch, toss_fire

T0, T1, T2 = "2026-10-09T04:00:00Z", "2026-10-09T05:00:00Z", "2026-10-09T06:00:00Z"
BUY = order("b1", "BUY", "100000", "2026-10-09T04:30:00Z")                         # tolerance 100 + 100 = 200


def _window(tmp_path, krw_after, orders=(), **kw):
    set_cutover(tmp_path)
    toss_fire(tmp_path, 1_000_000, T0)
    toss_fire(tmp_path, krw_after, T1, orders, **kw)
    return ledger(tmp_path)


def _rule_lines(rows):
    return [r for r in rows if r["event"] == "flow_resolved" and r["requested_by"] in cash_flows.RULE_IDS]


def _krw_residual(tmp_path):
    (row,) = [r for r in events(tmp_path, "toss_residual") if r["pocket"] == "KRW"]
    return row


@pytest.mark.parametrize(("after", "kind"), [(1_200_000, "deposit"), (800_000, "withdrawal")])
def test_a_round_change_in_a_quiet_window_is_closed_by_the_rule(tmp_path, after, kind):
    rows = _window(tmp_path, after)
    residual = _krw_residual(tmp_path)
    (line,) = _rule_lines(rows)
    assert line["resolves"] == residual["source_event_key"] and line["kind"] == kind
    assert line["requested_by"] == cash_flows.QUIET_WINDOW_RULE == "rule:quiet_window.v2"
    assert line["evidence_status"] == "NOT_VALIDATED" and line["accounting_eligibility"] == "NOT_ELIGIBLE"
    assert cash_flows.post_cutover_exceptions(rows) == 0
    (view,) = cash_flows.flows_view(tmp_path)
    assert view["review"] == "CLOSED" and view["closed_by"] == "rule"


def test_a_round_deposit_beside_a_buy_is_closed_and_its_remainder_kept_on_the_ledger(tmp_path):
    # +100,123 cash with a 100,000 buy: 200,123 unexplained = 200,000 + a 123 adjustment (tolerance 200).
    rows = _window(tmp_path, 1_100_123, [BUY])
    (line,) = _rule_lines(rows)
    assert line["kind"] == "deposit"
    assert (line["rule_amount_krw"], line["rule_remainder_krw"], line["rule_tolerance_krw"]) == ("200000", "123", "200")
    (view,) = cash_flows.flows_view(tmp_path)
    assert not {"rule_amount_krw", "rule_remainder_krw", "rule_tolerance_krw"} & set(view)


def test_a_rule_closed_window_with_a_fill_is_still_no_trade_evidence(tmp_path):
    start_epoch(tmp_path)
    toss_fire(tmp_path, 1_000_000, T0)
    toss_fire(tmp_path, 1_100_000, T1, [BUY])                                        # a deposit and a buy
    assert len(_rule_lines(ledger(tmp_path))) == 1
    evidence = cash_flows.toss_evidence(ledger(tmp_path), epoch=1, today_kst="2026-10-10")
    assert evidence["buy_explained"] == 0 and evidence["problem_windows"] == 1
    semantics, _why = cash_flows.toss_semantics(ledger(tmp_path), {}, today_kst="2026-10-10")
    assert semantics == "WAITING"                                                   # closed as a flow: not MIXED


def test_a_settlement_in_the_window_no_longer_keeps_it_open(tmp_path):
    sold = order("s1", "SELL", "50000", "2026-10-07T01:00:00Z", settlement="2026-10-09")
    assert len(_rule_lines(_window(tmp_path, 1_200_000, [sold]))) == 1


@pytest.mark.parametrize(("fills", "remainder", "closed"), [
    ("100000", 200, True), ("100000", 201, False),                                  # 100 + 0.1% of 100,000
    ("5000000", 1000, True), ("5000000", 1001, False)])                             # capped at 1,000
def test_the_remainder_may_be_what_the_days_fills_explain_and_no_more(tmp_path, fills, remainder, closed):
    buy = order("b1", "BUY", fills, "2026-10-09T04:30:00Z")
    after = 1_000_000 - int(fills) + 200_000 + remainder
    assert bool(_rule_lines(_window(tmp_path, after, [buy]))) is closed


def test_the_days_fills_count_beyond_the_window(tmp_path):
    # The morning's buy filled two windows earlier; its adjustment lands after the close, beside a deposit.
    morning = order("b0", "BUY", "300000", "2026-10-09T00:30:00Z")
    rows = _window(tmp_path, 1_200_350, [morning])                                  # tolerance 100 + 300 = 400
    (line,) = _rule_lines(rows)
    assert line["rule_tolerance_krw"] == "400"


@pytest.mark.parametrize(("case", "reason"), [
    ("not_round", "not_round"), ("too_small", "too_small"), ("usd_moved", "usd_moved"),
    ("truncated", "orders_truncated"), ("open_at_end", "open_order"), ("open_unread", "open_order"),
    ("unstable", "identity_unstable")])
def test_the_rule_names_the_condition_that_kept_a_krw_residual_open(tmp_path, case, reason):
    rows = _window(tmp_path, *{
        "not_round": (1_203_000,),
        "too_small": (1_003_000,),                                                  # a dividend's size
        "usd_moved": (1_200_000,),
        "truncated": (1_200_000,),
        "open_at_end": (1_200_000,),
        "open_unread": (1_200_000,),
        "unstable": (1_100_000, [order(None, "BUY", "100000", "2026-10-09T04:30:00Z")]),
    }[case], **{"usd_moved": {"usd": 10.0}, "truncated": {"truncated": True},
                "open_at_end": {"open_orders": [{"orderId": "o9"}]},
                "open_unread": {"open_orders": ToolError("TOOL_TRANSPORT", "x")}}.get(case, {}))
    assert _rule_lines(rows) == []
    assert cash_flows.post_cutover_exceptions(rows) >= 1
    krw = _krw_residual(tmp_path)
    assert krw["quiet_window_declined"] == reason
    (view,) = [v for v in cash_flows.flows_view(tmp_path) if v["key"] == krw["source_event_key"]]
    assert view["rule_declined"] == reason and view["rule_declined_derived"] is False


def test_an_order_open_at_the_windows_start_keeps_it_open_too(tmp_path):
    set_cutover(tmp_path)
    toss_fire(tmp_path, 1_000_000, T0, open_orders=[{"orderId": "o9"}])             # pending at the start
    toss_fire(tmp_path, 1_200_000, T1)                                              # gone by the end
    assert _rule_lines(ledger(tmp_path)) == []
    assert _krw_residual(tmp_path)["quiet_window_declined"] == "open_order"


def test_thomas_re_closes_a_rule_resolution_once_and_only_once(tmp_path):
    _window(tmp_path, 1_200_000)
    key = _krw_residual(tmp_path)["source_event_key"]
    written = resolve(tmp_path, key, "income")
    assert written["supersedes"] == f"resolved:{key}" and written["source_event_key"] == f"resolved:{key}:override"
    assert cash_flows.lifecycle(ledger(tmp_path))[key]["kind"] == "income"
    (view,) = cash_flows.flows_view(tmp_path)
    assert view["closed_by"] == "thomas" and view["rule_declined"] is None
    with pytest.raises(ToolError) as again:
        resolve(tmp_path, key, "deposit")
    assert again.value.reason_code == cash_flows.RESOLVE_DUPLICATE


def test_a_quiet_deposit_keeps_the_trade_evidence_gathered_before_it(tmp_path):
    start_epoch(tmp_path, now="2026-10-09T09:00:00Z")
    toss_fire(tmp_path, 1_000_000, "2026-10-09T10:00:00Z")
    toss_fire(tmp_path, 900_000, "2026-10-09T11:00:00Z", [order("b1", "BUY", "100000", "2026-10-09T10:30:00Z")])
    toss_fire(tmp_path, 1_100_000, "2026-10-09T12:00:00Z")                          # +200,000, nothing else
    assert len(_rule_lines(ledger(tmp_path))) == 1
    assert cash_flows.toss_evidence(ledger(tmp_path), epoch=1, today_kst="2026-10-11")["buy_explained"] == 1


def test_the_rule_flows_are_told_once_by_direction_and_count(tmp_path):
    _window(tmp_path, 1_200_000)
    toss_fire(tmp_path, 1_100_000, T2)                                              # a round withdrawal
    token, counts = cash_flows.rule_flows_untold(tmp_path)
    assert token and counts == {"deposit": 1, "withdrawal": 1}
    assert cash_flows.rule_flows_untold(tmp_path)[0] == token                     # undelivered: offered again
    cash_flows.mark_rule_flows_told(tmp_path, token=token, now=T2)
    assert cash_flows.rule_flows_untold(tmp_path) == (None, {})


def test_a_v1_resolution_is_still_the_rules(tmp_path, monkeypatch):
    monkeypatch.setattr(cash_flows, "QUIET_WINDOW_RULE", cash_flows.QUIET_WINDOW_RULE_V1)
    _window(tmp_path, 1_200_000)
    monkeypatch.undo()
    (view,) = cash_flows.flows_view(tmp_path)
    assert view["closed_by"] == "rule" and view["rule_declined"] is None
    assert cash_flows.rule_flows_untold(tmp_path)[1] == {"deposit": 1, "withdrawal": 0}


def test_a_re_closed_flow_is_no_longer_the_rules_to_tell(tmp_path):
    _window(tmp_path, 1_200_000)
    resolve(tmp_path, _krw_residual(tmp_path)["source_event_key"], "income")
    assert cash_flows.rule_flows_untold(tmp_path) == (None, {})


def _verdict(krw, *, fills=0.0, usd=0.0, open_orders=(0, 0)):
    result = {"buy": {}, "sell": {}, "settlements": [], "exchange": False, "identity_stable": True,
              "open": {"KRW": True, "USD": False}, "residual": {"KRW": krw, "USD": usd}}
    return cash_flows.quiet_window_verdict(result, truncated=False, open_orders=open_orders, day_fills_krw=fills)


@pytest.mark.parametrize(("krw", "expected"), [
    (200_000.0, ("deposit", None)), (200_099.0, ("deposit", None)), (200_101.0, (None, "not_round")),
    (-50_000.0, ("withdrawal", None)), (4_999.0, (None, "too_small")), (15_000.0, (None, "not_round")),
    (0.0, (None, "too_small"))])
def test_round_means_the_nearest_10000_within_the_tolerance(krw, expected):
    assert _verdict(krw) == expected


def test_an_unread_open_order_count_declines():
    assert _verdict(200_000.0, open_orders=(None, 0)) == (None, "open_order")
    assert _verdict(200_000.0, open_orders=(0, 2)) == (None, "open_order")


def test_a_usd_change_under_the_open_threshold_still_keeps_the_window_open_and_says_so():
    # The USD pocket opens above 0.01, the rule wants it under 0.005: between the two, only the reason tells.
    assert _verdict(200_000.0, usd=0.007) == (None, "usd_moved")
    assert _verdict(200_000.0, usd=0.004) == ("deposit", None)


def test_a_rule_closed_or_usd_residual_carries_no_reason(tmp_path):
    _window(tmp_path, 1_200_000)                                                    # closed by the rule
    toss_fire(tmp_path, 1_200_000, T2, usd=10.0)                                    # only USD moved
    assert all("quiet_window_declined" not in r for r in events(tmp_path, "toss_residual"))
    assert [v["rule_declined"] for v in cash_flows.flows_view(tmp_path)] == [None, None]


@pytest.mark.parametrize(("after", "reason"), [(1_200_123, "not_round"), (1_200_000, "undetermined"),
                                               (1_100_000, "fill")])
def test_a_line_written_before_the_field_is_derived_by_v1_from_its_window(tmp_path, monkeypatch, after, reason):
    with monkeypatch.context() as m:                                               # a fire before the field existed
        m.setattr(cash_flows, "quiet_window_verdict", lambda result, **_kw: (None, None))
        _window(tmp_path, after, [BUY] if reason == "fill" else ())
    krw = _krw_residual(tmp_path)
    assert "quiet_window_declined" not in krw
    (view,) = cash_flows.flows_view(tmp_path)
    assert view["rule_declined"] == reason and view["rule_declined_derived"] is True


def test_the_flows_command_prints_the_reason_and_no_amount(tmp_path, monkeypatch, capsys):
    from scripts import holdings_board

    monkeypatch.setattr(holdings_board, "state_dir", lambda: tmp_path)
    _window(tmp_path, 1_203_000)
    assert holdings_board.main(["--flows"]) == holdings_board.EXIT_OK
    out = capsys.readouterr().out
    assert "rule: not_round" in out and "(derived)" not in out
    assert "203000" not in out and "203,000" not in out and "3000" not in out


def test_an_unknown_reason_is_refused_before_the_chain():
    row = {"event": "toss_residual", "pocket": "KRW", "quiet_window_declined": "amount_was_200123"}
    with pytest.raises(ToolError) as refused:
        cash_flows._validate(row)
    assert refused.value.reason_code == cash_flows.EVENT_INVALID
    for reason in cash_flows.QUIET_WINDOW_DECLINES:
        cash_flows._validate({**row, "quiet_window_declined": reason})


def test_only_the_days_krw_fills_set_the_tolerance():
    orders = [order("k1", "BUY", "100000", "2026-10-09T01:00:00Z"),
              order("u1", "BUY", "900", "2026-10-09T01:00:00Z", currency="USD"),              # another pocket
              order("k0", "BUY", "700000", "2026-10-07T01:00:00Z")]                         # another day
    assert cash_flows._day_fills_krw(orders, since=T0, until=T1) == 100_000


def test_the_feed_lists_open_orders_with_a_get_for_status_open(monkeypatch):
    from runtime.mvp_runtime.holdings import toss_account

    feed = toss_account.TossHoldingsFeed()
    asked = []
    monkeypatch.setattr(feed, "_resolve_account", lambda **_kw: None)
    monkeypatch.setattr(feed, "_get", lambda path, params, **_kw: asked.append((path, params))
                        or {"orders": [{"orderId": "o1"}, "junk"]})
    assert feed.open_orders() == [{"orderId": "o1"}]
    assert asked == [(toss_account.ORDERS_PATH, {"status": "OPEN"})]
    monkeypatch.setattr(feed, "_get", lambda *_a, **_kw: {"nothing": []})
    with pytest.raises(ToolError) as malformed:
        feed.open_orders()
    assert malformed.value.reason_code == "MALFORMED_RESULT"

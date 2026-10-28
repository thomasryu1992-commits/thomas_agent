"""Toss deposits and withdrawals by rule: quiet_window.v1 (HOLDINGS_STATUS_READ_AND_AUTO_FLOWS F1 A, F2).

A window that holds nothing but a round KRW change is closed as a deposit or withdrawal by the rule, in the write
that records it; anything else stays an exception for Thomas, who can re-close a rule's resolution once. The rule's
flows are told by direction and count only. Synthetic fixtures only; no real holdings file is read.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.errors import ToolError
from runtime.mvp_runtime.holdings import cash_flows
from tests._h6d_support import events, ledger, order, resolve, set_cutover, start_epoch, toss_fire

T0, T1, T2 = "2026-10-09T04:00:00Z", "2026-10-09T05:00:00Z", "2026-10-09T06:00:00Z"


def _window(tmp_path, krw_after, orders=(), **kw):
    set_cutover(tmp_path)
    toss_fire(tmp_path, 1_000_000, T0)
    toss_fire(tmp_path, krw_after, T1, orders, **kw)
    return ledger(tmp_path)


def _rule_lines(rows):
    return [r for r in rows if r["event"] == "flow_resolved" and r["requested_by"] == cash_flows.QUIET_WINDOW_RULE]


@pytest.mark.parametrize(("after", "kind"), [(1_200_000, "deposit"), (800_000, "withdrawal")])
def test_a_round_change_in_a_quiet_window_is_closed_by_the_rule(tmp_path, after, kind):
    rows = _window(tmp_path, after)
    (residual,) = events(tmp_path, "toss_residual")
    (line,) = _rule_lines(rows)
    assert line["resolves"] == residual["source_event_key"] and line["kind"] == kind
    assert line["evidence_status"] == "NOT_VALIDATED" and line["accounting_eligibility"] == "NOT_ELIGIBLE"
    assert cash_flows.post_cutover_exceptions(rows) == 0
    (view,) = cash_flows.flows_view(tmp_path)
    assert view["review"] == "CLOSED" and view["closed_by"] == "rule"


@pytest.mark.parametrize("case", ["not_round", "with_a_buy", "usd_moved", "truncated"])
def test_anything_but_a_quiet_round_krw_change_is_left_to_thomas(tmp_path, case):
    rows = _window(tmp_path, *{
        "not_round": (1_200_123,),
        "with_a_buy": (1_100_000, [order("b1", "BUY", "100000", "2026-10-09T04:30:00Z")]),
        "usd_moved": (1_200_000,),
        "truncated": (1_200_000,),
    }[case], **({"usd": 10.0} if case == "usd_moved" else {"truncated": True} if case == "truncated" else {}))
    assert _rule_lines(rows) == []
    assert cash_flows.post_cutover_exceptions(rows) >= 1


def test_thomas_re_closes_a_rule_resolution_once_and_only_once(tmp_path):
    _window(tmp_path, 1_200_000)
    (residual,) = events(tmp_path, "toss_residual")
    key = residual["source_event_key"]
    written = resolve(tmp_path, key, "income")
    assert written["supersedes"] == f"resolved:{key}" and written["source_event_key"] == f"resolved:{key}:override"
    assert cash_flows.lifecycle(ledger(tmp_path))[key]["kind"] == "income"
    (view,) = cash_flows.flows_view(tmp_path)
    assert view["closed_by"] == "thomas"
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


def test_a_re_closed_flow_is_no_longer_the_rules_to_tell(tmp_path):
    _window(tmp_path, 1_200_000)
    (residual,) = events(tmp_path, "toss_residual")
    resolve(tmp_path, residual["source_event_key"], "income")
    assert cash_flows.rule_flows_untold(tmp_path) == (None, {})


@pytest.mark.parametrize(("krw", "expected"), [(200_000.0, "deposit"), (200_000.4, "deposit"), (200_000.6, None),
                                               (-50_000.0, "withdrawal"), (0.0, None), (1_500.0, None)])
def test_round_means_a_whole_non_zero_multiple_of_1000(krw, expected):
    result = {"buy": {}, "sell": {}, "settlements": [], "exchange": False, "identity_stable": True,
              "open": {"KRW": True, "USD": False}, "residual": {"KRW": krw, "USD": 0.0}}
    assert cash_flows.quiet_window_kind(result, truncated=False) == expected


# --- why the rule left a KRW residual open (Thomas 2026-10-28): the condition's name, never an amount ------------

@pytest.mark.parametrize(("case", "reason"), [
    ("not_round", "not_round"), ("with_a_buy", "fill"), ("usd_moved", "usd_moved"), ("truncated", "orders_truncated"),
    ("settlement", "settlement"), ("unstable", "identity_unstable")])
def test_the_rule_names_the_condition_that_kept_a_krw_residual_open(tmp_path, case, reason):
    rows = _window(tmp_path, *{
        "not_round": (1_200_123,),
        "with_a_buy": (1_100_000, [order("b1", "BUY", "100000", "2026-10-09T04:30:00Z")]),
        "usd_moved": (1_200_000,),
        "truncated": (1_200_000,),
        "settlement": (1_200_000, [order("s1", "SELL", "50000", "2026-10-07T01:00:00Z", settlement="2026-10-09")]),
        "unstable": (1_100_000, [order(None, "BUY", "100000", "2026-10-09T04:30:00Z")]),
    }[case], **({"usd": 10.0} if case == "usd_moved" else {"truncated": True} if case == "truncated" else {}))
    assert _rule_lines(rows) == []
    (krw,) = [r for r in events(tmp_path, "toss_residual") if r["pocket"] == "KRW"]
    assert krw["quiet_window_declined"] == reason
    (view,) = [v for v in cash_flows.flows_view(tmp_path) if v["key"] == krw["source_event_key"]]
    assert view["rule_declined"] == reason and view["rule_declined_derived"] is False


def test_a_rule_closed_or_usd_residual_carries_no_reason(tmp_path):
    _window(tmp_path, 1_200_000)                                                    # closed by the rule
    toss_fire(tmp_path, 1_200_000, T2, usd=10.0)                                    # only USD moved
    assert all("quiet_window_declined" not in r for r in events(tmp_path, "toss_residual"))
    assert [v["rule_declined"] for v in cash_flows.flows_view(tmp_path)] == [None, None]
    key = events(tmp_path, "toss_residual")[0]["source_event_key"]
    resolve(tmp_path, key, "income")                                                # Thomas re-closes it
    assert cash_flows.flows_view(tmp_path)[0]["rule_declined"] is None


@pytest.mark.parametrize(("after", "reason"), [(1_200_123, "not_round"), (1_200_000, "undetermined")])
def test_a_line_written_before_the_field_is_derived_from_its_window(tmp_path, monkeypatch, after, reason):
    with monkeypatch.context() as m:                                               # a fire before the rule existed
        m.setattr(cash_flows, "quiet_window_verdict", lambda result, *, truncated: (None, None))
        _window(tmp_path, after)
    (krw,) = events(tmp_path, "toss_residual")
    assert "quiet_window_declined" not in krw
    (view,) = cash_flows.flows_view(tmp_path)
    assert view["rule_declined"] == reason and view["rule_declined_derived"] is True


def test_the_flows_command_prints_the_reason_and_no_amount(tmp_path, monkeypatch, capsys):
    from scripts import holdings_board

    monkeypatch.setattr(holdings_board, "state_dir", lambda: tmp_path)
    _window(tmp_path, 1_200_123)
    assert holdings_board.main(["--flows"]) == holdings_board.EXIT_OK
    out = capsys.readouterr().out
    assert "rule: not_round" in out and "(derived)" not in out
    assert "200123" not in out and "200,123" not in out


def test_an_unknown_reason_is_refused_before_the_chain():
    row = {"event": "toss_residual", "pocket": "KRW", "quiet_window_declined": "amount_was_200123"}
    with pytest.raises(ToolError) as refused:
        cash_flows._validate(row)
    assert refused.value.reason_code == cash_flows.EVENT_INVALID
    for reason in cash_flows.QUIET_WINDOW_DECLINES:
        cash_flows._validate({**row, "quiet_window_declined": reason})


def test_a_usd_change_under_the_open_threshold_still_keeps_the_window_open_and_says_so():
    # The USD pocket opens above 0.01, the rule wants it under 0.005: between the two, only the reason tells.
    result = {"buy": {}, "sell": {}, "settlements": [], "exchange": False, "identity_stable": True,
              "open": {"KRW": True, "USD": False}, "residual": {"KRW": 200_000.0, "USD": 0.007}}
    assert cash_flows.quiet_window_verdict(result, truncated=False) == (None, "usd_moved")
    assert cash_flows.quiet_window_verdict({**result, "residual": {"KRW": 200_000.0, "USD": 0.004}},
                                           truncated=False) == ("deposit", None)

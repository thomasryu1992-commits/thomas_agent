"""The canary evidence read side — can each order prove what it was?

The readiness board reports the aggregate ("4 of 4 cannot prove their size"), which answers
"is the evidence sound" but not "did the canary I just placed record its fill". During a live
canary run that second question is the whole question.

What must hold: a record proves its size only when the venue's filled notional is there;
records written before those fields existed are reported as UNPROVEN rather than as agreement;
and nothing here writes, gates, or opens a socket.
"""

from __future__ import annotations

import json

import pytest

from runtime.mvp_runtime.crypto import live_promotion as lp
from runtime.mvp_runtime.errors import ToolError

NOW = "2026-07-29T09:00:00Z"


def _record(**over):
    base = {"reconcile_status": "RECONCILED", "clean": True, "symbol": "BTCUSDT",
            "exchange_order_id": 1, "notional_usdt": 65.0, "recorded_at_utc": NOW}
    base.update(over)
    return base


def _rows(monkeypatch, records):
    monkeypatch.setattr(lp, "read_canary_orders", lambda root=None: list(records))
    return lp.canary_evidence_rows()


def test_a_record_with_the_filled_notional_proves_its_size(monkeypatch):
    rows = _rows(monkeypatch, [_record(filled_notional_usdt=64.83,
                                       notional_declared_vs_filled_usdt=0.17)])
    assert rows[0]["size_proven"] is True
    assert rows[0]["gap_usdt"] == pytest.approx(0.17)


def test_a_record_from_before_the_fields_is_unproven_not_agreeing(monkeypatch):
    """The four standing as evidence carry a declared figure and nothing else. That is "nobody
    can ask", not "the sizes matched" — and a blank column would read as a zero-sized order."""
    rows = _rows(monkeypatch, [_record()])
    assert rows[0]["size_proven"] is False
    assert rows[0]["gap_usdt"] is None
    assert rows[0]["filled_usdt"] is None
    assert "no fill recorded" in lp.render_canary_evidence_text(rows)


def test_a_boolean_is_not_a_gap(monkeypatch):
    """`True` is an int in Python. A record carrying a bool there has not proven anything."""
    rows = _rows(monkeypatch, [_record(notional_declared_vs_filled_usdt=True)])
    assert rows[0]["size_proven"] is False


def test_an_unclean_record_is_shown_and_marked(monkeypatch):
    """It does not count toward the gate, so hiding it would make the board disagree with the
    registry it is rendering."""
    rows = _rows(monkeypatch, [_record(clean=False)])
    text = lp.render_canary_evidence_text(rows)
    assert rows[0]["clean"] is False
    assert "NOT clean" in text


def test_the_board_counts_what_can_be_proven(monkeypatch):
    rows = _rows(monkeypatch, [
        _record(exchange_order_id=1),
        _record(exchange_order_id=2, filled_notional_usdt=64.9,
                notional_declared_vs_filled_usdt=0.1),
    ])
    text = lp.render_canary_evidence_text(rows)
    assert "1/2 can prove their size" in text
    assert "only a NEW canary can add provable evidence" in text


def test_an_empty_registry_says_so_rather_than_printing_a_bare_board(monkeypatch):
    assert "no canary orders recorded" in lp.render_canary_evidence_text(_rows(monkeypatch, []))


def test_an_unverifiable_registry_is_a_typed_refusal_not_an_empty_board(monkeypatch, capsys):
    """The promotion gate counts an unverifiable registry as ZERO; this must not print an empty
    board, which would read as "no canaries placed"."""
    def _boom(root=None):
        raise ToolError("CANARY_REGISTRY_INVALID", "self-hash mismatch on line 2")

    monkeypatch.setattr(lp, "read_canary_orders", _boom)
    assert lp.main([]) == 2
    assert "CANARY_REGISTRY_INVALID" in capsys.readouterr().err


def test_json_mode_emits_the_canary_rows(monkeypatch, capsys):
    monkeypatch.setattr(lp, "read_canary_orders",
                        lambda root=None: [_record(filled_notional_usdt=64.9,
                                                   notional_declared_vs_filled_usdt=0.1)])
    monkeypatch.setattr("runtime.mvp_runtime.crypto.live_pnl.read_live_outcomes",
                        lambda root=None: [])
    assert lp.main(["--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["canaries"][0]["size_proven"] is True


def test_the_reader_opens_no_socket_and_writes_nothing():
    """Read-only by construction — the whole point of a board consulted during a live run."""
    import inspect

    src = inspect.getsource(lp.canary_evidence_rows) + inspect.getsource(lp.main)
    # Named precisely rather than by keyword: the first version of this test rejected
    # `append` and caught `rows.append(...)`, a list method — a check that fails on its own
    # subject teaches the next author to delete it.
    for forbidden in ("urlopen(", "requests.", "select_gated", "select_env_gated",
                      "write_text(", "open(", "record_submission", "submit"):
        assert forbidden not in src, forbidden


# --- the evidence that matters from here on: real live trades ----------------
#
# The four canaries are frozen at 0/4 and cannot be repaired, and no more are being placed.
# So "can the live path prove what it did" has to be answered by real trades.

def _outcome(**over):
    base = {"closed_at_utc": "2026-07-29T10:12:00Z", "symbol": "BTCUSDT", "side": "SELL",
            "quantity": 0.001, "entry_price": 66830.0, "exit_price": 67010.0,
            "realized_pnl_usdt": 0.18, "entry_order_id": 1090000000001}
    base.update(over)
    return base


def _trades(monkeypatch, records):
    monkeypatch.setattr("runtime.mvp_runtime.crypto.live_pnl.read_live_outcomes",
                        lambda root=None: list(records))
    return lp.live_trade_evidence_rows()


def test_a_real_trade_proves_its_size_from_the_venues_own_numbers(monkeypatch):
    rows = _trades(monkeypatch, [_outcome()])
    assert rows[0]["size_proven"] is True
    assert rows[0]["entry_notional_usdt"] == pytest.approx(66.83)


def test_a_trade_without_fill_figures_is_flagged_for_investigation(monkeypatch):
    """A live position is built from the ACTUAL fill or it is not booked at all, so this does
    not mean the size drifted — it means something wrote an outcome by a path that skipped
    that rule. The wording has to say so, or the row reads as a rounding complaint."""
    rows = _trades(monkeypatch, [_outcome(quantity=0.0, entry_price=None)])
    assert rows[0]["size_proven"] is False
    assert rows[0]["entry_notional_usdt"] is None
    assert "investigate" in lp.render_live_trade_evidence_text(rows)


def test_a_trade_names_the_strategy_that_placed_it(monkeypatch):
    """The read side of a fix that was only half made.

    The outcome record has carried the lineage since 2026-07-26, when the executing leg
    stopped copying only the display id — and this board, the one surface an operator reads
    live results on, dropped all three fields again. A field stored where nobody can see it
    answers no question."""
    rows = _trades(monkeypatch, [_outcome(
        strategy_id="S1218", candidate_id="cand_ab12", strategy_generation_id="GEN-712",
        strategy_rule_hash="ec9e900ed9c3f8a342fcb8ae9ff73f91557697effb11df030f42ae37abc26e5f",
    )])
    assert rows[0]["lineage_attributable"] is True
    text = lp.render_live_trade_evidence_text(rows)
    assert "S1218" in text and "GEN-712" in text
    assert "ec9e900ed9c3" in text, "the rule hash is the key that identifies the lineage"
    assert "1/1 can be traced" in text


def test_a_trade_from_an_imported_strategy_says_it_cannot_be_traced(monkeypatch):
    """25 of the 72 strategies routing right now came through the C7 import and carry no
    ``candidate_id``, so a live trade from one cannot reach the evidence that promoted it.

    Stated rather than left blank: an empty column reads as "not filled in yet", and during a
    live test the difference between "not yet" and "never will be" is the whole point."""
    rows = _trades(monkeypatch, [_outcome(
        strategy_id="S271", candidate_id=None, strategy_generation_id="GEN-068",
        strategy_rule_hash="ec9e900ed9c3f8a342fcb8ae9ff73f91557697effb11df030f42ae37abc26e5f",
    )])
    assert rows[0]["lineage_attributable"] is False
    text = lp.render_live_trade_evidence_text(rows)
    assert "NOT ATTRIBUTABLE" in text
    assert "0/1 can be traced" in text
    assert "group them by" in text, "the operator needs the fallback, not just the bad news"
    assert "ec9e900ed9c3" in text, "the rule hash IS that fallback and must still be printed"


def test_a_trade_with_no_rule_hash_at_all_does_not_print_a_blank(monkeypatch):
    """The worst row: not even the fallback key. It must not render as an empty field that
    reads like a formatting bug."""
    rows = _trades(monkeypatch, [_outcome(strategy_rule_hash=None, candidate_id=None)])
    assert rows[0]["strategy_rule_hash"] is None
    assert "NO RULE HASH" in lp.render_live_trade_evidence_text(rows)


def test_the_lineage_survives_json_mode(monkeypatch, capsys):
    """The board is also how a later analysis reads these rows back."""
    monkeypatch.setattr(lp, "read_canary_orders", lambda root=None: [])
    monkeypatch.setattr("runtime.mvp_runtime.crypto.live_pnl.read_live_outcomes",
                        lambda root=None: [_outcome(candidate_id="cand_ab12")])
    assert lp.main(["--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["live_trades"][0]["candidate_id"] == "cand_ab12"
    assert payload["live_trades"][0]["lineage_attributable"] is True


def test_no_live_trades_yet_says_what_will_appear(monkeypatch):
    """The empty state is the state during live testing, so it has to be informative rather
    than blank — it is what the operator sees while waiting for the first real trade."""
    text = lp.render_live_trade_evidence_text(_trades(monkeypatch, []))
    assert "no live trades closed on this machine yet" in text
    assert "ACTUAL fill" in text


def test_the_board_shows_both_halves(monkeypatch, capsys):
    monkeypatch.setattr(lp, "read_canary_orders", lambda root=None: [_record()])
    monkeypatch.setattr("runtime.mvp_runtime.crypto.live_pnl.read_live_outcomes",
                        lambda root=None: [_outcome()])
    assert lp.main([]) == 0
    out = capsys.readouterr().out
    assert "=== canary evidence ===" in out and "=== live trades ===" in out


def test_json_carries_both_halves_under_named_keys(monkeypatch, capsys):
    monkeypatch.setattr(lp, "read_canary_orders", lambda root=None: [_record()])
    monkeypatch.setattr("runtime.mvp_runtime.crypto.live_pnl.read_live_outcomes",
                        lambda root=None: [_outcome()])
    assert lp.main(["--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert set(payload) == {"canaries", "live_trades"}
    assert payload["live_trades"][0]["size_proven"] is True


def test_the_reader_reads_outcomes_and_never_the_order_path():
    """`live_pnl` is deliberately outside the order-path module set — the cycle already reads
    live outcomes so the risk guard can see live losses, and reading a result is not reaching
    the order path. This board stays on that side of the line."""
    import ast
    import inspect
    import textwrap

    # The IMPORT GRAPH, not a substring scan. The first version of this searched the source
    # text and failed on the function's own docstring, which explains why a live position is
    # built from the actual fill and names the module that enforces it — the same way the
    # order-path tripwire once tripped on a docstring, and the reason main rewrote that check
    # to walk the AST. A check that fails on its own explanation gets the explanation deleted.
    tree = ast.parse(textwrap.dedent(inspect.getsource(lp.live_trade_evidence_rows)))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.rsplit(".", 1)[-1])
            imported.update(a.name.rsplit(".", 1)[-1] for a in node.names)
        elif isinstance(node, ast.Import):
            imported.update(a.name.rsplit(".", 1)[-1] for a in node.names)

    assert "live_pnl" in imported
    assert imported & {"live_leg", "live_execution", "live_position"} == set()


# --- does the P&L follow from the row's own prices? ---------------------------
#
# `size_proven` sounds like the stronger check and is the weaker one: it asks only whether a
# quantity and an entry price are PRESENT. On 2026-08-21 a BTCUSDT row held both, printed
# `realized 77.5357 USDT` beside `= 77.8813 USDT` notional on a 0.22% price move, and was
# counted in "28/28 can prove their size". The true result was a 0.1728 USDT loss; the figure
# was the arithmetic of a close twice the size of the open. Every term needed to see that is on
# the row, so the board can check it rather than print it.


def test_a_row_whose_pnl_does_not_follow_from_its_prices_is_flagged(monkeypatch):
    """The 2026-08-21 row, to scale. Prices give -0.1728; the row claims +77.5357."""
    rows = _trades(monkeypatch, [_outcome(
        symbol="BTCUSDT", side="SELL", quantity=0.001,
        entry_price=77881.3, exit_price=77708.5, realized_pnl_usdt=77.5357)])
    assert rows[0]["size_proven"] is True          # it still has both figures...
    assert rows[0]["pnl_consistent"] is False      # ...and they do not produce that P&L


def test_the_flag_prints_what_the_prices_actually_give(monkeypatch):
    """Naming the number the row should have carried is the difference between "something is
    wrong here" and an operator being able to see what."""
    rows = _trades(monkeypatch, [_outcome(
        side="SELL", quantity=0.001, entry_price=77881.3,
        exit_price=77708.5, realized_pnl_usdt=77.5357)])
    text = lp.render_live_trade_evidence_text(rows)
    assert "P&L DOES NOT FOLLOW FROM THESE PRICES" in text
    assert "-0.1728 USDT" in text
    assert "0/1 have a P&L that follows from their own prices" in text
    assert "not arm a strategy on evidence one of them is part of" in text


def test_a_consistent_row_says_nothing_extra(monkeypatch):
    """The control. A board that annotates every row teaches the reader to skip annotations."""
    rows = _trades(monkeypatch, [_outcome()])
    assert rows[0]["pnl_consistent"] is True
    text = lp.render_live_trade_evidence_text(rows)
    assert "DOES NOT FOLLOW" not in text
    assert "NOT CHECKED" not in text
    assert "1/1 have a P&L that follows from their own prices" in text


def test_a_short_is_checked_with_the_side_that_closed_it(monkeypatch):
    """`side` is the CLOSING order's side, so BUY closed a SHORT and the sign flips. Getting
    this backwards would flag every short on the board and nothing would ever be read again."""
    rows = _trades(monkeypatch, [_outcome(
        side="BUY", quantity=0.022, entry_price=1859.14,
        exit_price=1904.96, realized_pnl_usdt=-1.00804)])
    assert rows[0]["pnl_consistent"] is True


def test_float_noise_is_not_a_disagreement(monkeypatch):
    """Measured across the 28 rows on the live machine, the consistent ones sit within ~2e-16
    of notional. The tolerance must clear that by orders of magnitude or the check is a
    generator of false alarms."""
    rows = _trades(monkeypatch, [_outcome(realized_pnl_usdt=0.18 + 1e-12)])
    assert rows[0]["pnl_consistent"] is True


def test_a_row_that_cannot_be_checked_is_not_counted_as_passing(monkeypatch):
    """Three answers, not two. "No exit price" is not "the P&L is fine", and folding the two
    together is how an unverifiable row inherits a verified row's credibility."""
    rows = _trades(monkeypatch, [_outcome(exit_price=None)])
    assert rows[0]["pnl_consistent"] is None
    text = lp.render_live_trade_evidence_text(rows)
    assert "P&L NOT CHECKED" in text
    assert "0/1 have a P&L that follows from their own prices" in text
    assert "could not be checked at all" in text


def test_the_three_verdicts_are_counted_separately(monkeypatch):
    """One of each, so the footer cannot collapse them by accident."""
    rows = _trades(monkeypatch, [
        _outcome(),                                                    # consistent
        _outcome(realized_pnl_usdt=99.0),                              # inconsistent
        _outcome(exit_price=None),                                     # unknown
    ])
    assert [r["pnl_consistent"] for r in rows] == [True, False, None]
    text = lp.render_live_trade_evidence_text(rows)
    assert "1/3 have a P&L that follows from their own prices" in text
    assert "1 row(s) above carry a realized P&L their own prices do not produce." in text
    assert "1 row(s) could not be checked at all" in text

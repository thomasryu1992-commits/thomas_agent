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

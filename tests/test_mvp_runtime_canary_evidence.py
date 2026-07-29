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


def test_json_mode_emits_the_rows(monkeypatch, capsys):
    monkeypatch.setattr(lp, "read_canary_orders",
                        lambda root=None: [_record(filled_notional_usdt=64.9,
                                                   notional_declared_vs_filled_usdt=0.1)])
    assert lp.main(["--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["size_proven"] is True


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

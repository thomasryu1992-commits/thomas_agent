"""`live_reconcile.reconcile_positions`, rule by rule (crypto refactor plan §O, the reconciliation gap).

The verdicts are pinned in ``test_mvp_runtime_crypto_live_position.py``: each drift shape refuses an
entry, an unreadable account refuses every entry, closes stay open, float noise is not drift. A
mutation pass over this module (2026-10-01) found 14 of 18 single-rule changes that left that whole
suite green. Each test below pins one of those rules as the current behaviour. A failure here means
the comparison changed. The fix is then a decision, and the kernel version stamp moves with it.
"""

from __future__ import annotations

import copy

import pytest

from runtime.mvp_runtime.crypto import live_position as lp
from runtime.mvp_runtime.crypto import live_reconcile as lr
from runtime.mvp_runtime.errors import ToolError
from tests.test_mvp_runtime_crypto_live_position import NOW, _position, _snapshot, _venue


# --- the quantity tolerance: relative to the venue's size, with an absolute floor ------------------

@pytest.mark.parametrize("local,venue,agrees", [
    (1.0 + 5e-7, 1.0, True),      # inside one part in a million of the venue's size
    (1.0 + 2e-6, 1.0, False),     # outside it: a partial fill, not noise
    (5e-10, 0.0, True),           # a venue size of zero still has the absolute floor
    (2e-9, 0.0, False),           # and only the floor
    (1000.0 + 5e-4, 1000.0, True),
    (1000.0 + 2e-3, 1000.0, False),
])
def test_the_quantity_tolerance_is_one_part_in_a_million_of_the_venue_with_a_floor(local, venue, agrees):
    record = lr.reconcile_positions([_position(quantity=local)], _snapshot(_venue(quantity=venue)), now=NOW)
    assert (record["status"] == lp.RECONCILED) is agrees
    assert (lr.DRIFT_QUANTITY_MISMATCH in record["books"]["BTCUSDT"]["reasons"]) is not agrees


# --- what is compared, and how disagreements are named ---------------------------------------------

def test_the_side_is_compared_without_case():
    """The book stores its direction upper-case already; the venue's side is the one that may not be."""
    record = lr.reconcile_positions([_position(direction="LONG")], _snapshot(_venue(side="long")), now=NOW)
    assert record["status"] == lp.RECONCILED


def test_a_side_and_a_quantity_that_both_differ_are_both_named_side_first():
    record = lr.reconcile_positions(
        [_position(direction="LONG", quantity=0.002)], _snapshot(_venue(side="SHORT", quantity=0.001)), now=NOW)
    assert record["books"]["BTCUSDT"]["reasons"] == [lr.DRIFT_SIDE_MISMATCH, lr.DRIFT_QUANTITY_MISMATCH]


def test_the_run_names_every_reason_once_sorted_and_every_drifted_symbol_sorted():
    record = lr.reconcile_positions(
        [_position(symbol="SOLUSDT"), _position(symbol="BTCUSDT")],
        _snapshot(_venue(symbol="ETHUSDT"), _venue(symbol="ADAUSDT")),
        now=NOW,
    )
    assert record["reasons"] == sorted({lr.DRIFT_MISSING_AT_VENUE, lr.DRIFT_UNTRACKED_AT_VENUE})
    assert record["drifted_symbols"] == ["ADAUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"]
    assert list(record["books"]) == ["ADAUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"]


def test_a_venue_position_without_a_symbol_is_not_a_book():
    record = lr.reconcile_positions([], _snapshot(_venue(symbol="")), now=NOW)
    assert record["status"] == lp.RECONCILED and record["books"] == {}


def test_a_local_position_without_a_symbol_fails_closed():
    position = _position()
    position["symbol"] = ""
    with pytest.raises(ToolError):
        lr.reconcile_positions([position], _snapshot(), now=NOW)


def test_two_local_records_for_one_symbol_compare_the_later_one():
    record = lr.reconcile_positions(
        [_position(quantity=0.001), _position(quantity=0.002)], _snapshot(_venue(quantity=0.002)), now=NOW)
    assert record["status"] == lp.RECONCILED
    assert record["books"]["BTCUSDT"]["local_quantity"] == 0.002


# --- the unreadable account: the record's shape ---------------------------------------------------

def test_an_unreadable_account_refuses_each_book_by_name_and_keeps_the_local_size():
    record = lr.reconcile_positions([_position(quantity=0.003)], None, now=NOW)
    assert record["books"] == {"BTCUSDT": {
        "symbol": "BTCUSDT", "status": lp.ACCOUNT_UNREADABLE, "reasons": [lp.ACCOUNT_UNREADABLE],
        "entry_allowed": False, "local_quantity": 0.003, "venue_quantity": None,
    }}
    assert record["reasons"] == [lp.ACCOUNT_UNREADABLE]
    assert "drifted_symbols" not in record


# --- the stamp --------------------------------------------------------------------------------------

@pytest.mark.parametrize("snapshot", [None, _snapshot(_venue())], ids=["unreadable", "read"])
def test_every_record_carries_the_book_kernel_version_and_the_time_it_was_asked(snapshot):
    record = lr.reconcile_positions([_position()], snapshot, now=NOW)
    assert record["reconcile_version"] == lp.LIVE_POSITION_KERNEL_VERSION
    assert record["created_at"] == NOW


def test_it_changes_neither_input():
    local, snapshot = [_position()], _snapshot(_venue(quantity=0.001))
    before = (copy.deepcopy(local), copy.deepcopy(snapshot))
    lr.reconcile_positions(local, snapshot, now=NOW)
    assert (local, snapshot) == before

"""LP5.1 tests — live position state + venue reconciliation. No network, no venue.

The properties under test are the ones the design record calls mandatory: the live book
lives in its own namespace and can never be confused with paper's, mutating it is gated on
the one live-trading grant, the venue is the truth (drift or an unreadable account refuses
new entries while closes stay permitted), and the guard's former fail-open exposure default
is closed — unknown exposure reads as at-cap, never as zero.
"""

from __future__ import annotations

import json

import pytest

from runtime.mvp_runtime.crypto import live_position as lp
from runtime.mvp_runtime.crypto import paper
from runtime.mvp_runtime.crypto.account import AccountPosition, AccountSnapshot
from runtime.mvp_runtime.crypto.live_pnl import (
    LIVE_TRADING_ENV,
    LIVE_TRADING_FLAGS,
    LIVE_TRADING_PROVIDER_ID,
    REAL_LIVE_TRADING,
)
from runtime.mvp_runtime.errors import SafetyGateBlocked, ToolError
from runtime.mvp_runtime.safety_gate import Authorization

NOW = "2026-07-25T00:00:00Z"

_LIVE_AUTH = Authorization(
    flags=LIVE_TRADING_FLAGS, provider_id=LIVE_TRADING_PROVIDER_ID, activation_sha256="sha256:test",
    expires_at="2999-01-01T00:00:00Z", evidence_ref=".runtime_governance_state/evidence.md",
)


def _position(symbol="BTCUSDT", direction="LONG", quantity=0.002, entry_price=60000.0, **kw):
    return lp.build_live_position(
        symbol=symbol, direction=direction, quantity=quantity, entry_price=entry_price,
        stop_loss=kw.pop("stop_loss", 59000.0), opened_at=kw.pop("opened_at", NOW), **kw,
    )


def _snapshot(*positions):
    return AccountSnapshot(
        asset="USDT", wallet_balance=1000.0, margin_balance=1000.0, available_balance=900.0,
        unrealized_pnl=0.0, positions=list(positions), realized_windows={},
        source="test", collected_at=NOW,
    )


def _venue(symbol="BTCUSDT", side="LONG", quantity=0.002, notional=120.0):
    return AccountPosition(
        symbol=symbol, side=side, quantity=quantity, entry_price=60000.0, mark_price=60000.0,
        unrealized_pnl=0.0, leverage=1.0, notional=notional,
    )


# --- the record ---------------------------------------------------------------

def test_record_is_stamped_live_and_carries_its_notional():
    position = _position()
    assert position["stage"] == lp.LIVE_STAGE
    assert position["status"] == "OPEN"
    assert position["notional_usdt"] == 120.0          # 0.002 * 60000
    assert position["risk"] == pytest.approx(2.0)      # |60000-59000| * 0.002
    assert position["position_id"].startswith("live_position")


def test_record_refuses_rather_than_guessing():
    with pytest.raises(ToolError) as exc:
        _position(direction="SIDEWAYS")
    assert exc.value.reason_code == "MALFORMED_DIRECTION"
    for kwargs, code in (
        ({"quantity": 0.0}, "MISSING_POSITION_QUANTITY"),
        ({"entry_price": 0.0}, "MISSING_ENTRY_PRICE"),
        ({"symbol": ""}, "MISSING_SYMBOL"),
    ):
        with pytest.raises(ToolError) as exc:
            _position(**kwargs)
        assert exc.value.reason_code == code


def test_a_position_with_no_stop_reports_zero_risk_never_a_guess():
    position = _position(stop_loss=None)
    assert position["risk"] == 0.0


# --- state separation from paper (the mandatory one) --------------------------

def test_live_book_never_shares_paper_directory(tmp_path):
    """The design's load-bearing separation: a live position must not land where the paper
    cycle globs, or the paper kernel could settle a REAL position with simulated math."""
    store = lp.RealLivePositionStore(root=tmp_path, authorization=_LIVE_AUTH)
    store.save_position(_position())

    live_dir = lp.live_positions_dir(tmp_path)
    paper_dir = paper.positions_dir(tmp_path)
    assert live_dir != paper_dir
    assert list(live_dir.glob("*.json"))          # the live book is here
    assert not list(paper_dir.glob("*.json"))     # and nowhere near paper's
    # The paper enumerator must not see it either.
    assert paper.list_open_positions(tmp_path) == []


def test_a_record_without_the_live_stamp_is_refused_not_guessed(tmp_path):
    path = lp.live_position_path("BTCUSDT", tmp_path)
    path.write_text(json.dumps({"status": "OPEN", "symbol": "BTCUSDT"}), encoding="utf-8")
    with pytest.raises(ToolError) as exc:
        lp.load_open_live_position("BTCUSDT", tmp_path)
    assert exc.value.reason_code == "LIVE_POSITION_STAGE_MISMATCH"


def test_unreadable_position_refuses_rather_than_reading_as_flat(tmp_path):
    path = lp.live_position_path("BTCUSDT", tmp_path)
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ToolError) as exc:
        lp.load_open_live_position("BTCUSDT", tmp_path)
    assert exc.value.reason_code == "LIVE_POSITION_STATE_UNREADABLE"


def test_symbol_can_name_a_book_never_a_location(tmp_path):
    for bad in ("../escape", "a/b", ""):
        with pytest.raises(ToolError) as exc:
            lp.live_position_path(bad, tmp_path)
        assert exc.value.reason_code == "LIVE_POSITION_SYMBOL_INVALID"


# --- the gate -----------------------------------------------------------------

def test_store_is_inert_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv(LIVE_TRADING_ENV, raising=False)
    store = lp.select_live_position_store(now=NOW, root=tmp_path)
    assert isinstance(store, lp.DryRunLivePositionStore)
    assert store.filesystem_write is False
    store.save_position(_position())
    assert lp.list_open_live_positions(tmp_path) == []   # touched nothing


def test_env_alone_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv(LIVE_TRADING_ENV, REAL_LIVE_TRADING)
    with pytest.raises(SafetyGateBlocked) as exc:
        lp.select_live_position_store(now=NOW, root=tmp_path)
    assert exc.value.reason_code == "ACTIVATION_MISSING"


def test_real_store_refuses_every_mutation_without_authorization(tmp_path):
    store = lp.RealLivePositionStore(root=tmp_path, authorization=None)
    with pytest.raises(SafetyGateBlocked):
        store.save_position(_position())
    with pytest.raises(SafetyGateBlocked):
        store.clear_position("BTCUSDT")


def test_real_store_round_trips_and_clears(tmp_path):
    store = lp.RealLivePositionStore(root=tmp_path, authorization=_LIVE_AUTH)
    store.save_position(_position())
    loaded = lp.load_open_live_position("BTCUSDT", tmp_path)
    assert loaded is not None and loaded["symbol"] == "BTCUSDT"
    assert lp.local_open_notional_usdt(tmp_path) == 120.0

    store.clear_position("BTCUSDT")
    assert lp.load_open_live_position("BTCUSDT", tmp_path) is None
    assert lp.list_open_live_positions(tmp_path) == []


# --- reconciliation: the venue is the truth -----------------------------------

def test_matching_book_reconciles_and_permits_entry():
    record = lp.reconcile_positions([_position()], _snapshot(_venue()), now=NOW)
    assert record["status"] == lp.RECONCILED
    assert record["entries_allowed"] is True
    assert lp.entry_allowed(record, "BTCUSDT") is True


def test_flat_everywhere_reconciles():
    record = lp.reconcile_positions([], _snapshot(), now=NOW)
    assert record["status"] == lp.RECONCILED and record["entries_allowed"] is True
    # An untouched symbol is enterable when the venue agrees the account is flat.
    assert lp.entry_allowed(record, "ETHUSDT") is True


@pytest.mark.parametrize("local,venue,reason", [
    ([_position()], [], lp.DRIFT_MISSING_AT_VENUE),
    ([], [_venue()], lp.DRIFT_UNTRACKED_AT_VENUE),
    ([_position(direction="LONG")], [_venue(side="SHORT")], lp.DRIFT_SIDE_MISMATCH),
    ([_position(quantity=0.002)], [_venue(quantity=0.001)], lp.DRIFT_QUANTITY_MISMATCH),
])
def test_every_drift_shape_refuses_entry_and_is_named(local, venue, reason):
    record = lp.reconcile_positions(local, _snapshot(*venue), now=NOW)
    assert record["status"] == lp.DRIFT
    assert reason in record["reasons"]
    assert record["entries_allowed"] is False
    assert lp.entry_allowed(record, "BTCUSDT") is False
    # A halt must never trap a position: closes stay permitted through every drift.
    assert record["closes_allowed"] is True


def test_float_noise_is_not_drift():
    """Venue quantities round-trip through strings; an exact == would cry drift on a
    byte-identical position."""
    record = lp.reconcile_positions(
        [_position(quantity=0.002)], _snapshot(_venue(quantity=0.002 + 1e-12)), now=NOW
    )
    assert record["status"] == lp.RECONCILED


def test_unreadable_account_refuses_entries_but_permits_closes():
    record = lp.reconcile_positions([_position()], None, now=NOW)
    assert record["status"] == lp.ACCOUNT_UNREADABLE
    assert record["entries_allowed"] is False
    assert record["closes_allowed"] is True
    assert lp.entry_allowed(record, "BTCUSDT") is False
    # Even a symbol with no local book is refused: with no venue read, nothing is known.
    assert lp.entry_allowed(record, "ETHUSDT") is False


def test_drift_on_one_symbol_does_not_silently_allow_the_run():
    record = lp.reconcile_positions(
        [_position(symbol="BTCUSDT"), _position(symbol="ETHUSDT")],
        _snapshot(_venue(symbol="BTCUSDT")),   # ETH missing at the venue
        now=NOW,
    )
    assert record["drifted_symbols"] == ["ETHUSDT"]
    assert lp.entry_allowed(record, "BTCUSDT") is True     # this book still matches
    assert lp.entry_allowed(record, "ETHUSDT") is False
    # A caller that only reads the run-level flag still fails closed.
    assert record["entries_allowed"] is False


def test_entry_allowed_fails_closed_on_a_malformed_record():
    for bad in (None, {}, {"status": lp.RECONCILED, "books": "nope"}):
        assert lp.entry_allowed(bad, "BTCUSDT") is False


def test_reconcile_writes_nothing(tmp_path):
    """It is pure: a drifted book is reported, never cleared behind the operator's back."""
    store = lp.RealLivePositionStore(root=tmp_path, authorization=_LIVE_AUTH)
    store.save_position(_position())
    lp.reconcile_positions(lp.list_open_live_positions(tmp_path), _snapshot(), now=NOW)
    assert lp.load_open_live_position("BTCUSDT", tmp_path) is not None


# --- exposure: the fail-open default, closed ----------------------------------

def test_exposure_sums_the_venue():
    exposure = lp.compute_open_notional_usdt(
        _snapshot(_venue(notional=60.0), _venue(symbol="ETHUSDT", notional=45.0)), at_cap=120.0
    )
    assert exposure == 105.0


def test_exposure_of_a_flat_account_is_zero_only_because_the_venue_said_so():
    assert lp.compute_open_notional_usdt(_snapshot(), at_cap=120.0) == 0.0


def test_unknown_exposure_reads_as_at_cap_never_zero():
    """The whole point of closing the fail-open default: an unreadable account must make
    the exposure cap refuse, not admit."""
    assert lp.compute_open_notional_usdt(None, at_cap=120.0) == 120.0


def test_guard_requires_the_exposure_argument():
    """It used to default to 0.0 — a caller that forgot it silently disabled the cap."""
    from runtime.mvp_runtime.crypto.live_order import evaluate_live_order_guard

    with pytest.raises(TypeError):
        evaluate_live_order_guard(
            {"symbol": "BTCUSDT"}, gate_open=True, runtime_active=True,
            daily_loss_breached=False, clean_canary_orders=3, submitted_today=0,
        )


# --- LP5's own concurrency caps ------------------------------------------------

def test_live_caps_are_far_below_papers():
    assert lp.MAX_LIVE_CONCURRENT_POSITIONS < paper.MAX_CONCURRENT_POSITIONS
    assert lp.MAX_LIVE_POSITIONS_PER_SYMBOL < paper.MAX_POSITIONS_PER_SYMBOL
    # One per symbol, because the venue nets per symbol in one-way mode.
    assert lp.MAX_LIVE_POSITIONS_PER_SYMBOL == 1


def test_capacity_blocks_a_second_book_on_one_symbol():
    capacity = lp.live_capacity([_position(symbol="BTCUSDT")], symbol="BTCUSDT")
    assert capacity["allowed"] is False
    assert "LIVE_MAX_POSITIONS_PER_SYMBOL" in capacity["blocks"]


def test_capacity_blocks_at_the_concurrency_ceiling():
    open_books = [
        _position(symbol="BTCUSDT"), _position(symbol="ETHUSDT"),
    ][:lp.MAX_LIVE_CONCURRENT_POSITIONS]
    capacity = lp.live_capacity(open_books, symbol="SOLUSDT")
    assert capacity["allowed"] is False
    assert "LIVE_MAX_CONCURRENT_POSITIONS" in capacity["blocks"]


def test_capacity_allows_a_fresh_symbol_under_the_ceiling():
    capacity = lp.live_capacity([_position(symbol="BTCUSDT")], symbol="ETHUSDT")
    assert capacity["allowed"] is True and capacity["blocks"] == []


# --- nothing here can send an order -------------------------------------------

def test_module_exposes_no_order_capability():
    """LP5.1 is state and reconciliation only: the order path is LP4's, and this module
    must not become a second door to it."""
    surface = " ".join(dir(lp)).lower()
    for forbidden in ("submit", "cancel", "order_adapter", "post"):
        assert forbidden not in surface

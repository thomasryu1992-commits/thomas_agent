"""The canary door itself — that the declared-notional check is wired into it.

`test_mvp_runtime_crypto_live_guard.py` proves `check_declared_notional` refuses an
under-declared notional, and `test_mvp_runtime_crypto_market_data.py` proves
`read_reference_price` refuses a price it cannot trust. Neither proves the operator tool
*calls* them — and that join is exactly where the bug lived: both halves of the size question
were fine on their own, `--quantity` and `--notional` were independent inputs, and nothing
compared them. The same shape as the canary-phrase regression `test_crypto_live_path_rehearsal`
was written for.

So this drives `main()` on the real composition with doubles only at the two venue reads.
Nothing is sent: no `live_trading` grant exists here, so the guard refuses anyway — the
assertion is that the notional refusal is *reported and blocking*, and that the order path is
never reached.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.cli_common import EXIT_BLOCKED
from runtime.mvp_runtime.crypto import live_execution
from runtime.mvp_runtime.crypto.account import AccountSnapshot
from runtime.mvp_runtime.crypto.live_order import (
    NOTIONAL_PRICE_UNKNOWN,
    NOTIONAL_UNDERSTATED,
)
from runtime.mvp_runtime.crypto.market_data import REFERENCE_PRICE_SYNTHETIC
from scripts import place_canary_order

PRICE = 64512.0


def _flat_account():
    return AccountSnapshot(
        asset="USDT", wallet_balance=500.0, margin_balance=500.0, available_balance=500.0,
        unrealized_pnl=0.0, positions=[], realized_windows={},
        source="test", collected_at="2026-07-26T12:00:00Z",
    )


@pytest.fixture
def door(monkeypatch):
    """The canary tool with both venue reads doubled and the order path booby-trapped."""
    monkeypatch.setattr(
        place_canary_order, "read_account", lambda **kwargs: (_flat_account(), {})
    )
    monkeypatch.setattr(
        place_canary_order,
        "read_reference_price",
        lambda symbol, **kwargs: (PRICE, {"last_close": PRICE}),
    )

    def _never(*args, **kwargs):  # pragma: no cover - the point is that it is not called
        raise AssertionError("a refused canary must not reach the order path")

    monkeypatch.setattr(live_execution, "submit_and_reconcile", _never)
    return place_canary_order


def _run(tmp_path, *, quantity: str, notional: str) -> int:
    return place_canary_order.main(
        ["--symbol", "BTCUSDT", "--quantity", quantity, "--notional", notional,
         "--root", str(tmp_path)]
    )


def test_an_understated_notional_is_refused_at_the_door(door, tmp_path, capsys):
    """0.001 BTC at 64,512 is 64.51 USDT. Declaring 60 used to place the larger position
    under a 60 USDT per-order cap."""
    assert _run(tmp_path, quantity="0.001", notional="60") == EXIT_BLOCKED
    captured = capsys.readouterr()
    assert NOTIONAL_UNDERSTATED in captured.err
    assert "64.51" in captured.out


def test_a_truthful_declaration_raises_no_notional_objection(door, tmp_path, capsys):
    """The remaining refusal is the guard's (no grant, no phrase, no budget) — the notional
    itself is cleared, so the check is not simply refusing everything."""
    assert _run(tmp_path, quantity="0.001", notional="65") == EXIT_BLOCKED
    captured = capsys.readouterr()
    assert NOTIONAL_UNDERSTATED not in captured.err
    assert NOTIONAL_PRICE_UNKNOWN not in captured.err
    assert "the canary guard refused" in captured.err


def test_an_unusable_price_blocks_rather_than_trusting_the_operator(monkeypatch, tmp_path, capsys):
    """Fail-closed on the runs where market data is broken: with no price there is nothing to
    check the declaration against, and 'nothing to check' must not mean 'approved'."""
    monkeypatch.setattr(
        place_canary_order, "read_account", lambda **kwargs: (_flat_account(), {})
    )
    monkeypatch.setattr(
        place_canary_order,
        "read_reference_price",
        lambda symbol, **kwargs: (None, {"degraded_reason_code": REFERENCE_PRICE_SYNTHETIC}),
    )
    assert _run(tmp_path, quantity="0.001", notional="65") == EXIT_BLOCKED
    captured = capsys.readouterr()
    assert NOTIONAL_PRICE_UNKNOWN in captured.err
    # The price's own reason travels with the refusal, so "configure the market-data feed"
    # is visible rather than inferred.
    assert REFERENCE_PRICE_SYNTHETIC in captured.err


def test_the_notional_check_is_reported_before_the_account_refusal(monkeypatch, tmp_path, capsys):
    """An operator who has to fix both sees both in one run. The account read refuses
    outright, so the check has to be reported before it or it is lost."""
    monkeypatch.setattr(place_canary_order, "read_account", lambda **kwargs: (None, {}))
    monkeypatch.setattr(
        place_canary_order,
        "read_reference_price",
        lambda symbol, **kwargs: (PRICE, {"last_close": PRICE}),
    )
    assert _run(tmp_path, quantity="0.001", notional="60") == EXIT_BLOCKED
    captured = capsys.readouterr()
    assert "NO_ACCOUNT_VISIBILITY" in captured.err
    assert "notional check" in captured.out and "64.51" in captured.out

"""The registered budget said which symbols; nothing read it.

`live_trading_budget.v0.1` has carried `symbol_allowlist` since the first record was written,
and `build_live_trading_budget_record` normalizes and refuses an empty one — so the field was
validated, stored, and then dropped. `resolve_live_order_limits` copies only the numeric caps
into `LiveOrderLimits`, and no other module read the list. The caps therefore bound how MUCH
could be traded while nothing bound WHAT.

Measured on this machine 2026-07-29: the registered budget named `["BTCUSDT"]` while the
active pool held strategies on ETHUSDT, DOGEUSDT, BNBUSDT and SOLUSDT, `MVP_LIVE_TRADING=real`,
and `live_readiness` reported READY with autonomous routing WIRED. The two never disagreed out
loud because only one of them was ever consulted.

Under test: the allowlist is a door on both write paths (autonomous entry and the operator
canary), an unstated scope authorizes nothing, and the comparison survives the one way an
allowlist can arrive un-normalized — a hand-edited record.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.crypto.live_order import (
    STATUS_BLOCKED,
    evaluate_live_order_guard,
    normalize_symbols,
)

from tests.test_mvp_runtime_crypto_live_guard import _intent, _ready


def _guard(**kw):
    facts = _ready()
    facts.update(kw)
    intent = facts.pop("intent", None) or _intent()
    return evaluate_live_order_guard(intent, **facts)


def _symbol_blocks(verdict):
    return [b for b in verdict["blocks"] if "allowlist" in b]


def test_a_symbol_the_budget_never_named_is_refused():
    """The case that was live: a pool strategy on a symbol outside the registered scope."""
    verdict = _guard(intent=_intent(symbol="ETHUSDT"), allowed_symbols=["BTCUSDT"])
    assert verdict["status"] == STATUS_BLOCKED
    assert verdict["approved"] is False
    assert verdict["symbol_allowlisted"] is False
    assert _symbol_blocks(verdict), "the refusal must name the allowlist"
    assert "ETHUSDT" in _symbol_blocks(verdict)[0]


def test_a_named_symbol_still_passes():
    verdict = _guard(allowed_symbols=["BTCUSDT"])
    assert verdict["approved"] is True
    assert verdict["symbol_allowlisted"] is True
    assert verdict["allowed_symbols"] == ["BTCUSDT"]


def test_a_widened_allowlist_admits_the_symbol_it_names():
    """Widening is a re-registration, and the guard follows the record without a code change."""
    verdict = _guard(intent=_intent(symbol="ETHUSDT"),
                     allowed_symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    assert verdict["approved"] is True
    assert verdict["order_symbol"] == "ETHUSDT"


def test_an_unstated_scope_authorizes_nothing():
    """The fail-closed default, and the reason it is a default rather than a required kwarg:
    a caller that forgets the scope must be refused, not silently unbounded.

    The shared `_ready()` helper now supplies a scope, so this test deletes it — asserting on
    the default while a helper quietly provides one is how a fail-open default survives a
    test suite."""
    facts = _ready()
    facts.pop("allowed_symbols", None)
    verdict = evaluate_live_order_guard(_intent(), **facts)
    assert verdict["status"] == STATUS_BLOCKED
    assert verdict["symbol_allowlisted"] is False
    assert _symbol_blocks(verdict)


@pytest.mark.parametrize("empty", [(), [], None, ""])
def test_an_empty_or_unusable_allowlist_blocks_rather_than_admitting_everything(empty):
    """An empty list is the shape a damaged record degrades to, and "no scope" must never
    read as "any scope". A bare string is included because a caller passing `"BTCUSDT"`
    instead of `["BTCUSDT"]` would otherwise be admitted character by character."""
    verdict = _guard(allowed_symbols=empty)
    assert verdict["status"] == STATUS_BLOCKED
    assert _symbol_blocks(verdict)


def test_the_canary_door_is_not_exempt():
    """A canary is a smaller real order, not a different kind of one. Exempting it would let
    the operator aim the one manual live door at a symbol the budget does not name — and the
    canary evidence it produces is counted globally, so that scope leaks into the autonomous
    gate as well."""
    verdict = _guard(intent=_intent(symbol="DOGEUSDT"), allowed_symbols=["BTCUSDT"],
                     canary=True, clean_canary_orders=0)
    assert verdict["approved"] is False
    assert _symbol_blocks(verdict)


def test_a_hand_edited_allowlist_is_normalized_on_read():
    """The record is normalized when written, so the only way an un-normalized one arrives is
    a hand edit. Re-normalizing on read means a case difference can neither widen the scope
    nor narrow it."""
    verdict = _guard(allowed_symbols=[" btcusdt ", "BTCUSDT"])
    assert verdict["approved"] is True
    assert verdict["allowed_symbols"] == ["BTCUSDT"]


def test_an_intent_with_no_symbol_is_refused_rather_than_matched():
    verdict = _guard(intent=_intent(symbol=""), allowed_symbols=["BTCUSDT"])
    assert verdict["approved"] is False
    assert _symbol_blocks(verdict)


def test_normalize_symbols_is_the_one_comparison_rule():
    assert normalize_symbols([" eth usdt ".replace(" ", ""), "ethusdt"]) == ("ETHUSDT",)
    assert normalize_symbols(None) == ()
    assert normalize_symbols("BTCUSDT") == (), "a bare string is not a one-symbol allowlist"


def test_the_symbol_check_does_not_disturb_the_cap_arithmetic():
    """The gate is additive: a refused symbol must not change what the caps computed, or a
    reader would not be able to tell which door actually closed."""
    allowed = _guard(allowed_symbols=["BTCUSDT"])
    refused = _guard(intent=_intent(symbol="ETHUSDT"), allowed_symbols=["BTCUSDT"])
    for field in ("notional_usdt", "effective_cap_usdt", "open_exposure_cap_usdt",
                  "max_daily_order_count", "daily_loss_limit_usdt"):
        assert allowed[field] == refused[field]
    assert [b for b in refused["blocks"] if "allowlist" not in b] == []

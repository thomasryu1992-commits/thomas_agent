"""The four live entry stores are their own module, and ``live_order`` still offers them under the old
names (crypto refactor plan PR-12; ``docs/proposals/CRYPTO_REFACTOR_AND_MODULARIZATION_PLAN_V0.1.md``
§L-4).

The daily counter, the two breakers and the entry marks moved out of ``live_order`` whole. What they
keep and refuse is pinned where it always was (``test_mvp_runtime_crypto_live_guard.py``,
``…_bracket_breaker.py``, ``…_api_breaker.py``, ``…_live_entry_marks.py``), through ``live_order``.
This file pins what the move itself has to keep true:

- every name the stores define is the same object on ``live_order``, so the counter a door reserves
  on and the counter the board reads cannot become two;
- the four selectors did not move. They are where live trading's opt-in chooses the durable store,
  and ``test_the_env_only_gate_has_exactly_the_capabilities_thomas_named`` names ``live_order.py`` for
  them. The stores module selects nothing.
"""

from __future__ import annotations

from runtime.mvp_runtime.crypto import live_order, live_order_stores
from tests._helpers import defined_names

SELECTORS = ("select_live_order_counter", "select_live_bracket_breaker", "select_live_api_breaker",
             "select_live_entry_marks")


def test_live_order_offers_every_name_the_stores_define_as_the_same_object():
    names = defined_names(live_order_stores)
    assert {"LiveOrderCounter", "count_today", "LiveBracketFailureBreaker", "bracket_breaker_status",
            "LiveApiErrorBreaker", "api_breaker_status", "ApiErrorRecordingAdapter", "LiveEntryMarks",
            "read_live_entry_marks", "LIVE_DAILY_ORDER_CAP_REACHED", "MAX_CONSECUTIVE_API_ERRORS"} <= set(names), (
        "the scan lost the stores' own names: it broke, not the module")
    different = sorted(n for n in names if getattr(live_order, n, None) is not getattr(live_order_stores, n))
    assert different == [], f"live_order holds a different object, or none, for: {different}"


def test_the_selectors_stayed_in_live_order_and_choose_the_stores_classes(monkeypatch):
    """Without the opt-in each selector returns the inert twin, and that twin is the stores module's
    class. The stores module defines no selector of its own. The durable branch is pinned by the
    store tests, which opt in and get the ``Live*`` classes through ``live_order``."""
    from runtime.mvp_runtime.crypto.vocabulary import LIVE_TRADING_ENV

    monkeypatch.delenv(LIVE_TRADING_ENV, raising=False)
    chosen = {name: type(getattr(live_order, name)()) for name in SELECTORS}
    assert chosen == {
        "select_live_order_counter": live_order_stores.DryRunLiveOrderCounter,
        "select_live_bracket_breaker": live_order_stores.DryRunLiveBracketFailureBreaker,
        "select_live_api_breaker": live_order_stores.DryRunLiveApiErrorBreaker,
        "select_live_entry_marks": live_order_stores.DryRunLiveEntryMarks,
    }
    assert [name for name in SELECTORS if hasattr(live_order_stores, name)] == []
    assert all(getattr(live_order, name).__module__ == live_order.__name__ for name in SELECTORS)

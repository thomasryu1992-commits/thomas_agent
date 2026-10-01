"""Which names a crypto module re-exports from a sibling (crypto refactor plan PR-16, step N10).

The lane's splits (PR7, PR-04, PR-07~13) moved code into new modules and left the old module importing
every moved name, as the same object, so no caller broke. PR-16 removed the re-exports nobody read
through the old module. What is left is pinned here, per module and per source:

- a re-export is a name a module imports from a sibling (``from .x import name``) and never uses itself;
- the set on each module is exactly :data:`ROSTER`, so a re-export cannot come back, or go, unnoticed;
- every one is the source's own object, never a copy that could drift.

A name leaves the roster when its last caller reads it from where it is defined, and the import goes
with it. ``pool`` is not here: it is a facade by design, pinned by
``test_mvp_runtime_crypto_pool_facade.py``. ``factory`` keeps every template builder
(``_<family>_entry``), because the retirement tests look them up by a computed name.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

LANE = Path(__file__).resolve().parents[1] / "runtime" / "mvp_runtime" / "crypto"
PACKAGE = "runtime.mvp_runtime.crypto"
DESIGNED_FACADES = {"pool"}

ROSTER: dict[str, dict[str, frozenset[str]]] = {
    "cycle": {
        "cohort_retention": frozenset({"retention_cohort"}),
        "feed_assembly": frozenset({"OPTIONAL_DATA_DEGRADED_CODES", "attach_mining_legs"}),
    },
    "factory": {
        "backtest": frozenset({
            "HOLDOUT_FRACTION", "HOLDOUT_PERIODS", "MIN_BARS_FOR_HOLDOUT", "MIN_TRADES_PER_WINDOW",
            "WALK_FORWARD_MIN_PERIODS", "WALK_FORWARD_PERIODS", "holdout_split_index",
        }),
        "template_space": frozenset({
            "_bollinger_breakdown_short_entry", "_bollinger_breakout_entry", "_breakdown_short_entry",
            "_breakout_entry", "_funding_fade_long_entry", "_funding_fade_short_entry",
            "_funding_momentum_long_entry", "_funding_momentum_short_entry", "_htf_pullback_long_entry",
            "_htf_pullback_short_entry", "_htf_reversal_long_entry", "_htf_reversal_short_entry",
            "_htf_trend_long_entry", "_htf_trend_short_entry", "_htf_trend_strength_long_entry",
            "_htf_trend_strength_short_entry", "_ma_cross_down_entry", "_ma_cross_up_entry",
            "_macd_cross_down_entry", "_macd_cross_up_entry", "_macd_momentum_entry",
            "_macd_momentum_short_entry", "_mean_reversion_long_entry", "_mean_reversion_short_entry",
            "_oi_squeeze_long_entry", "_oi_squeeze_short_entry", "_oi_unwind_long_entry",
            "_oi_unwind_short_entry", "_positioning_divergence_long_entry",
            "_positioning_divergence_short_entry", "_premium_fade_long_entry", "_premium_fade_short_entry",
            "_rel_strength_long_entry", "_rel_strength_short_entry", "_session_trend_long_entry",
            "_session_trend_short_entry", "_taker_absorption_long_entry", "_taker_absorption_short_entry",
            "_taker_flow_fade_long_entry", "_taker_flow_fade_short_entry", "_taker_flow_long_entry",
            "_taker_flow_short_entry", "_trend_pullback_entry", "_trend_pullback_short_entry",
            "_volatility_expansion_long_entry", "_volatility_expansion_short_entry",
            "_volatility_squeeze_long_entry", "_volatility_squeeze_short_entry", "_xs_momentum_long_entry",
            "_xs_momentum_short_entry", "_xs_reversion_long_entry", "_xs_reversion_short_entry",
            "known_features",
        }),
    },
    "live_evidence": {
        "order_request": frozenset({"RECONCILED"}),
    },
    "live_execution": {
        "order_request": frozenset({
            "CONDITIONAL_ORDER_TYPES", "NOT_FOUND", "ORDER_TYPE_LIMIT", "ORDER_TYPE_MARKET",
            "ORDER_TYPE_STOP_MARKET", "RECONCILED", "TIME_IN_FORCE_GTC",
        }),
    },
    "live_leg": {
        "live_leg_results": frozenset({
            "CLOSE_REASON_EMERGENCY", "CLOSE_REASON_STOP", "CLOSE_REASON_TARGET", "CLOSE_REASON_TIME_EXIT",
            "CLOSE_REASON_UNPROTECTED", "bracket_error_detail", "leg_status_line", "legs_left_resting",
        }),
    },
    "live_order": {
        "live_order_stores": frozenset({
            "API_CALL_CLASSES", "API_ERROR_HTTP_STATUSES", "API_ERROR_VENUE_CODES",
            "ApiErrorRecordingAdapter", "ENTRY_MARKS_FILENAME", "LIVE_ENTRY_BAR_ALREADY_ENTERED",
            "LIVE_ENTRY_BAR_UNKNOWN", "LIVE_ENTRY_CLAIM_LOST", "LIVE_ENTRY_CLAIM_TTL_MINUTES",
            "LIVE_ENTRY_MARKS_UNKNOWN", "LIVE_ENTRY_STOP_LOSS_COOLDOWN", "LIVE_ENTRY_SYMBOL_IN_FLIGHT",
            "MAX_CONSECUTIVE_BRACKET_FAILURES", "api_breaker_status", "api_breaker_trip_lines",
            "api_error_counts", "bracket_breaker_status", "claim_expires_at", "count_today",
            "entry_context_key", "live_entry_holds", "read_live_entry_marks", "recorded_like",
            "stop_cooldown_until", "symbol_in_flight",
        }),
        "order_identity": frozenset({"make_client_order_id", "make_idempotency_key"}),
    },
    "live_pnl": {
        "live_ledger": frozenset({"stop_slippage_observations"}),
        "state": frozenset({"STATE_REL", "state_dir"}),
        "vocabulary": frozenset({"STOP_EXIT_REASONS"}),
    },
    "live_readiness": {
        "readiness_model": frozenset({"readiness_data"}),
    },
    "paper": {
        "state": frozenset({"STATE_REL"}),
        "trade_plan": frozenset({"ASSUMED_LEVERAGE", "ENTRY_COST_UNECONOMIC", "advance_holding"}),
    },
}


def _re_exports(path: Path) -> dict[str, tuple[str, str]]:
    """``{bound name: (source module, name there)}`` for every sibling import the module never uses."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = {
        alias.asname or alias.name: (node.module, alias.name)
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module
        for alias in node.names
    }
    used = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    return {name: source for name, source in imported.items() if name not in used}


def _lane_re_exports() -> dict[str, dict[str, frozenset[str]]]:
    found: dict[str, dict[str, set[str]]] = {}
    for path in sorted(LANE.glob("*.py")):
        if path.stem in DESIGNED_FACADES:
            continue
        for name, (source, _) in _re_exports(path).items():
            found.setdefault(path.stem, {}).setdefault(source, set()).add(name)
    return {module: {source: frozenset(names) for source, names in by_source.items()}
            for module, by_source in found.items()}


def test_the_scan_sees_a_re_export_it_must_see():
    """``cycle`` re-exports ``attach_mining_legs`` for the scheduler's factory dispatch: a scan that
    misses it is broken, and its empty answer would mean nothing."""
    assert _re_exports(LANE / "cycle.py")["attach_mining_legs"] == ("feed_assembly", "attach_mining_legs")


def test_each_module_re_exports_exactly_its_roster():
    found = _lane_re_exports()
    added = {module: {source: sorted(names - ROSTER.get(module, {}).get(source, frozenset()))
                      for source, names in by_source.items()} for module, by_source in found.items()}
    gone = {module: {source: sorted(names - found.get(module, {}).get(source, frozenset()))
                     for source, names in by_source.items()} for module, by_source in ROSTER.items()}
    strip = lambda table: {m: {s: n for s, n in by.items() if n} for m, by in table.items() if any(by.values())}
    assert strip(added) == {}, "a new re-export: import the name from where it is defined instead"
    assert strip(gone) == {}, "a re-export left: take it off the roster too"


def test_every_re_export_is_the_sources_own_object():
    different = []
    for module, by_source in ROSTER.items():
        facade = importlib.import_module(f"{PACKAGE}.{module}")
        spelled = _re_exports(LANE / f"{module}.py")
        for source, names in by_source.items():
            origin = importlib.import_module(f"{PACKAGE}.{source}")
            different += [f"{module}.{name}" for name in names
                          if getattr(facade, name) is not getattr(origin, spelled[name][1])]
    assert different == []

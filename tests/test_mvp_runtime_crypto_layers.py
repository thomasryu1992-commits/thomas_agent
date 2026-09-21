"""The crypto lane's dependency direction (crypto PR7a; directive §9: 역방향 dependency 금지).

Every module in ``runtime/mvp_runtime/crypto/`` sits in exactly one layer, and a module may import its
own layer and the layers below it: Market → Strategy → Decision → Risk → Execution → Reconciliation →
Outcome, the directive's order, with the leaves every layer reads at the bottom and the orchestrators
and reports on top. The test reads every import, module-level and function-local alike, because a
function-local import is still a dependency: it is how the one cycle in the lane (``live_budget`` ↔
``live_order``) hides.

Each module is placed by what it does, and the map is not tuned to shrink the list below. Two
placements that differ from the directive's names are deliberate:

- **governance is the lowest package, not a stage.** ``execution_stage``, ``testnet_evidence`` and
  ``live_governance`` are leaves every layer allowed to act reads, like ``state``. ``promotion`` and
  ``retirement`` change a strategy's pool status, so they are decision-level doors run under it.
- **two orchestrators.** ``cycle`` runs the whole cycle and ``live_route`` the live plane; each calls
  every stage in order, which is its job, not a reverse dependency.

The edges that point up today are named in ``EXCEPTIONS``, each with the step that removes it. The
list only shrinks: a new upward import fails, and so does an exception whose import is gone. An edge
whose removal would change trading behaviour stays as a permanent exception, because the directive puts
"no behaviour change" above the direction; none needs that today. The measurement and the plan are in
the PR7 investigation (2026-09-19).
"""

from __future__ import annotations

import ast
from pathlib import Path

CRYPTO = Path(__file__).resolve().parents[1] / "runtime" / "mvp_runtime" / "crypto"
_PACKAGE = "runtime.mvp_runtime.crypto"

LAYERS: tuple[str, ...] = (
    "foundation", "governance", "market", "strategy", "decision", "risk", "execution",
    "reconciliation", "outcome", "orchestration", "report",
)

LAYER: dict[str, str] = {
    # foundation: leaves shared by every layer
    "state": "foundation", "candidate_identity": "foundation", "refresh_marks": "foundation",
    "indicators": "foundation", "live_sizing": "foundation", "distribution_gate": "foundation",
    "limit_entry": "foundation", "live_correction": "foundation",
    # governance: what every layer allowed to act reads (the stage, its evidence, an order's record)
    "execution_stage": "governance", "testnet_evidence": "governance", "live_governance": "governance",
    # market: what the venue and the vendors say
    "market_data": "market", "candle_archive": "market", "oi_store": "market", "orderbook_store": "market",
    "positioning_store": "market", "features": "market", "account": "market", "live_filters": "market",
    # strategy: what a strategy is, and how one is generated and judged
    "strategy": "strategy", "strategy_artifact": "strategy", "cost": "strategy", "robustness": "strategy",
    "null_control": "strategy", "factory": "strategy", "proposer": "strategy", "proposer_cli": "strategy",
    "data_review": "strategy", "forward_book": "strategy", "forward_confirmation": "strategy",
    "lifecycle": "strategy",
    # decision: which strategies run, and the paper positions they open
    "paper": "decision", "pool": "decision", "routing_marks": "decision", "cooldown": "decision",
    "promotion": "decision", "retirement": "decision",
    # risk: what may be risked
    "guards": "risk", "risk_limits": "risk", "live_budget": "risk", "live_allowance": "risk",
    "pre_order_gate": "risk", "breaker_watch": "risk",
    # execution: what is sent, and the front half that prepares it
    "live_order": "execution", "live_execution": "execution", "live_leg": "execution",
    "live_entry": "execution", "venue_contract": "execution", "testnet_execution": "execution",
    "probe": "execution",
    # reconciliation: what the venue says happened
    "live_position": "reconciliation", "account_store": "reconciliation",
    # outcome: what it earned, and what that says
    "live_pnl": "outcome", "feedback": "outcome", "digest": "outcome", "counterfactual": "outcome",
    "live_promotion": "outcome",
    # orchestration: runs the stages in order
    "cycle": "orchestration", "live_route": "orchestration",
    # report: reads everything, imported by nothing inside the lane
    "dashboard": "report", "live_readiness": "report", "route_watch": "report", "tunables": "report",
}

_VOCAB = "PR7b: the name moves to a foundation leaf and is re-exported at the old one"
_STATE_DIR = "PR7b: state_dir from state, which already has it"
_CYCLE = "PR7b: LiveOrderLimits moves to the budget, which ends the cycle"
_PAPER_MATHS = "PR7c: the trade-plan maths leaves paper for strategy"
_RANKING = "PR7e: candidate ranking leaves pool for strategy"
_BOOK = "PR7d: the live book's reader and record builders leave the reconciliation module"
_LEDGER = "PR7d: the ledger's reader and record builder leave live_pnl"
_IDENTITY = "PR7d: the order intent's identity and its codes move below the gate and the sender"
_STORE = "PR7d: a read-only reader leaf below risk (the feedback loop closes through stores)"

# (importer, imported) -> the step that removes it. The list only shrinks.
EXCEPTIONS: dict[tuple[str, str], str] = {
    ("candle_archive", "paper"): _STATE_DIR,
    ("oi_store", "paper"): _STATE_DIR,
    ("orderbook_store", "paper"): _STATE_DIR,
    ("positioning_store", "paper"): _STATE_DIR,
    ("live_budget", "live_pnl"): _STATE_DIR,
    ("risk_limits", "live_pnl"): _STATE_DIR,
    ("live_order", "live_pnl"): _VOCAB + " (state_dir, utc_day, the live-trading env names)",
    ("live_position", "live_pnl"): _VOCAB + " (state_dir, the live-trading env names)",
    ("live_execution", "live_pnl"): _VOCAB + " (the live-trading env names)",
    ("cost", "live_pnl"): _VOCAB + " (the R bases, STOP_EXIT_REASONS)",
    ("paper", "live_pnl"): _VOCAB + " (R_BASIS_INTENT_NET)",
    ("live_execution", "live_promotion"): _VOCAB + " (RECONCILED)",
    ("live_leg", "live_promotion"): _VOCAB + " (RECONCILED)",
    ("guards", "feedback"): _VOCAB + " (net_result_r)",
    ("lifecycle", "feedback"): _VOCAB + " (net_result_r)",
    ("forward_confirmation", "feedback"): _VOCAB + " (net_result_r)",
    ("factory", "feedback"): _VOCAB + " (summarize_outcomes)",
    ("breaker_watch", "feedback"): "PR7b: an unused import, left when #414's report call was removed",
    ("live_budget", "live_order"): _CYCLE,
    ("factory", "paper"): _PAPER_MATHS,
    ("forward_book", "paper"): _PAPER_MATHS,
    ("forward_confirmation", "pool"): _RANKING,
    ("live_entry", "live_position"): _BOOK,
    ("live_leg", "live_position"): _BOOK,
    ("live_order", "live_position"): _BOOK,
    ("probe", "live_position"): _BOOK,
    ("live_leg", "live_pnl"): _LEDGER,
    ("pre_order_gate", "live_order"): _IDENTITY,
    ("breaker_watch", "live_pnl"): _STORE + " (the live ledger)",
    ("probe", "live_pnl"): _STORE + " (the slippage observations; its state_dir goes in PR7b)",
    ("venue_contract", "account_store"): _STORE + " (the account snapshot)",
}


def _modules() -> list[str]:
    return sorted(p.stem for p in CRYPTO.glob("*.py") if p.stem != "__init__")


def _imports(module: str, lane: set[str]) -> set[str]:
    """Every lane module ``module`` imports, anywhere in the file: ``from .x import …``,
    ``from . import x``, and the absolute spellings of both."""
    found: set[str] = set()
    for node in ast.walk(ast.parse((CRYPTO / f"{module}.py").read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom):
            name = node.module or ""
            if node.level == 1:
                found |= {name.split(".")[0]} if name else {a.name for a in node.names}
            elif node.level == 0 and name.startswith(_PACKAGE + "."):
                found.add(name[len(_PACKAGE) + 1:].split(".")[0])
            elif node.level == 0 and name == _PACKAGE:
                found |= {a.name for a in node.names}
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(_PACKAGE + "."):
                    found.add(alias.name[len(_PACKAGE) + 1:].split(".")[0])
    return (found & lane) - {module}


def _upward_edges() -> dict[tuple[str, str], tuple[str, str]]:
    lane = set(_modules())
    rank = {layer: i for i, layer in enumerate(LAYERS)}
    return {
        (src, dst): (LAYER[src], LAYER[dst])
        for src in sorted(lane) if src in LAYER
        for dst in sorted(_imports(src, lane)) if dst in LAYER and rank[LAYER[dst]] > rank[LAYER[src]]
    }


def test_every_crypto_module_has_exactly_one_layer():
    modules = set(_modules())
    assert modules, "no crypto modules found: the path broke, not the lane"
    assert sorted(modules - set(LAYER)) == [], "place the new module in a layer (LAYER)"
    assert sorted(set(LAYER) - modules) == [], "a module left the lane: drop it from LAYER"
    assert set(LAYER.values()) <= set(LAYERS)


def test_the_layers_keep_the_directives_order():
    directive = ("market", "strategy", "decision", "risk", "execution", "reconciliation", "outcome")
    assert [layer for layer in LAYERS if layer in directive] == list(directive)


def test_no_crypto_import_points_up_a_layer_beyond_the_named_ones():
    unexpected = {edge: layers for edge, layers in _upward_edges().items() if edge not in EXCEPTIONS}
    assert unexpected == {}, (
        "an import points up a layer (importer's layer < imported's). Move the shared name down, or "
        "record a decision in EXCEPTIONS with the step that removes it: "
        + "; ".join(f"{s} ({a}) -> {d} ({b})" for (s, d), (a, b) in sorted(unexpected.items()))
    )


def test_every_named_exception_still_exists():
    stale = sorted(set(EXCEPTIONS) - set(_upward_edges()))
    assert stale == [], f"these imports no longer point up: remove them from EXCEPTIONS (it only shrinks): {stale}"

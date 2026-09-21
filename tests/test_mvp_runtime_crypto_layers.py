"""The crypto lane's dependency direction (crypto PR7a; directive §9: 역방향 dependency 금지).

Every module in ``runtime/mvp_runtime/crypto/`` (a sub-package's included) sits in exactly one layer,
and a module may import its own layer and the layers below it: Market → Strategy → Decision → Risk →
Execution → Reconciliation → Outcome, the directive's order (its Feedback stage is part of outcome), with
the leaves every layer reads at the bottom and the orchestrators and reports on top. The test reads
every import, module-level and function-local alike, resolved to the module it lands in: a
function-local import is still a dependency, and it is how the one cycle in the lane (``live_budget`` ↔
``live_order``) hides. Nothing in the lane imports by string (``importlib``, ``__import__``) today; an
import made that way would not be seen.

Each module is placed by what it does, and the map is not tuned to shrink the list below:

- **governance is the lowest package, not a stage.** ``execution_stage``, ``testnet_evidence`` and
  ``live_governance`` are leaves every layer allowed to act reads, like ``state``. ``promotion`` and
  ``retirement`` change a strategy's pool status, so they are decision-level doors run under it.
- **two orchestrators.** ``cycle`` runs the whole cycle and ``live_route`` the live plane; each calls
  every stage in order, which is its job, not a reverse dependency.
- **``live_entry`` sits in execution.** It decides the live entry and prepares it for the send: it runs
  the pre-order gate (risk), sizes the order (risk) and hands the intent to ``live_order``'s final guard,
  and ``live_leg`` sends it through the adapter. What it calls is risk below and execution beside.
- **foundation holds only leaves with no role of their own.** Sizing (``live_sizing``) is risk, the
  distribution gate and limit-entry scoring are strategy, and outcome corrections are outcome.

The edges that point up today are named in ``EXCEPTIONS`` with the step that removes each, and with the
names each may take. Both only shrink: a new upward pair fails, a new name on a named pair fails, and so
does an exception, or a name, that is no longer imported. An edge whose removal would change trading
behaviour stays as a permanent exception, because the directive puts "no behaviour change" above the
direction; none needs that today. Moving a module to a lower layer empties exceptions as surely as
removing an import, so it is a design decision reviewed like one: the placement must say what the module
does.
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
    "indicators": "foundation",
    # governance: what every layer allowed to act reads (the stage, its evidence, an order's record)
    "execution_stage": "governance", "testnet_evidence": "governance", "live_governance": "governance",
    # market: what the venue and the vendors say
    "market_data": "market", "candle_archive": "market", "oi_store": "market", "orderbook_store": "market",
    "positioning_store": "market", "features": "market", "account": "market", "live_filters": "market",
    # strategy: what a strategy is, and how one is generated and judged
    "strategy": "strategy", "strategy_artifact": "strategy", "cost": "strategy", "robustness": "strategy",
    "null_control": "strategy", "factory": "strategy", "proposer": "strategy", "proposer_cli": "strategy",
    "data_review": "strategy", "forward_book": "strategy", "forward_confirmation": "strategy",
    "lifecycle": "strategy", "distribution_gate": "strategy", "limit_entry": "strategy",
    # decision: which strategies run, and the paper positions they open
    "paper": "decision", "pool": "decision", "routing_marks": "decision", "cooldown": "decision",
    "promotion": "decision", "retirement": "decision",
    # risk: what may be risked
    "guards": "risk", "risk_limits": "risk", "live_budget": "risk", "live_allowance": "risk",
    "pre_order_gate": "risk", "breaker_watch": "risk", "live_sizing": "risk",
    # execution: what is sent, and the front half that prepares it
    "live_order": "execution", "live_execution": "execution", "live_leg": "execution",
    "live_entry": "execution", "venue_contract": "execution", "testnet_execution": "execution",
    "probe": "execution",
    # reconciliation: what the venue says happened
    "live_position": "reconciliation", "account_store": "reconciliation",
    # outcome: what it earned, and what that says
    "live_pnl": "outcome", "feedback": "outcome", "digest": "outcome", "counterfactual": "outcome",
    "live_promotion": "outcome", "live_correction": "outcome",
    # orchestration: runs the stages in order
    "cycle": "orchestration", "live_route": "orchestration",
    # report: reads everything, imported by nothing inside the lane
    "dashboard": "report", "live_readiness": "report", "route_watch": "report", "tunables": "report",
}

_MODULE = "<module>"      # the module object itself is bound, and nothing is read from it


_STATE_DIR = "PR7b: state_dir from state, which already has it"
_VOCAB = "PR7b: the names move to a lower leaf and stay re-exported at the old one"
_MATHS = "PR7b: the outcome maths moves to a lower leaf and stays re-exported by feedback"
_CYCLE = "PR7b: LiveOrderLimits moves to the budget, which ends the cycle"
_PAPER_MATHS = "PR7c: the trade-plan maths and paper's status names leave paper for strategy"
_RANKING = "PR7e: candidate ranking leaves pool for strategy"
_BOOK = "PR7d: the live book's reader and record builders leave the reconciliation module"
_LEDGER = "PR7d: the outcome record builder leaves live_pnl"
_IDENTITY = "PR7d: the order intent's identity and its codes move below the gate and the sender"
_STORE = "PR7d: a read-only reader leaf below risk (the feedback loop closes through stores)"
_ENV = frozenset({"LIVE_TRADING_ENV", "LIVE_TRADING_FLAGS", "LIVE_TRADING_PROVIDER_ID", "REAL_LIVE_TRADING"})

# (importer, imported) -> (the step that removes it, the names it may take). Both only shrink: a new
# upward pair fails, and so does a new name on a named pair (a new upward import of `submit_live_order`
# through `pre_order_gate -> live_order` is as new as any other).
EXCEPTIONS: dict[tuple[str, str], tuple[str, frozenset[str]]] = {
    ("candle_archive", "paper"): (_STATE_DIR, frozenset({"state_dir"})),
    ("oi_store", "paper"): (_STATE_DIR, frozenset({"state_dir"})),
    ("orderbook_store", "paper"): (_STATE_DIR, frozenset({"state_dir"})),
    ("positioning_store", "paper"): (_STATE_DIR, frozenset({"state_dir"})),
    ("live_budget", "live_pnl"): (_STATE_DIR, frozenset({"state_dir"})),
    ("risk_limits", "live_pnl"): (_STATE_DIR, frozenset({"state_dir"})),
    ("live_order", "live_pnl"): (_VOCAB + " (state_dir, utc_day, the live-trading env names)",
                                 _ENV | {"state_dir", "utc_day"}),
    ("live_position", "live_pnl"): (_VOCAB + " (state_dir, the live-trading env names)", _ENV | {"state_dir"}),
    ("live_execution", "live_pnl"): (_VOCAB + " (the live-trading env names)", _ENV),
    ("cost", "live_pnl"): (_VOCAB + " (the R bases, the stop-exit reasons)",
                           frozenset({"R_BASES_NET_OF_COSTS", "R_BASIS_FILLED", "STOP_EXIT_REASONS"})),
    ("paper", "live_pnl"): (_VOCAB + " (the R bases)", frozenset({"R_BASIS_INTENT_NET"})),
    ("live_execution", "live_promotion"): (_VOCAB + " (RECONCILED)", frozenset({"RECONCILED"})),
    ("live_leg", "live_promotion"): (_VOCAB + " (RECONCILED)", frozenset({"RECONCILED"})),
    ("live_filters", "live_sizing"): (_VOCAB + " (the SymbolFilters value type)", frozenset({"SymbolFilters"})),
    ("guards", "feedback"): (_MATHS, frozenset({"net_result_r"})),
    ("lifecycle", "feedback"): (_MATHS, frozenset({"net_result_r"})),
    ("forward_confirmation", "feedback"): (_MATHS, frozenset({"net_result_r"})),
    ("factory", "feedback"): (_MATHS, frozenset({"summarize_outcomes"})),
    ("breaker_watch", "feedback"): ("PR7b: an unused import, left when #414's report call was removed",
                                    frozenset({_MODULE})),
    ("live_budget", "live_order"): (_CYCLE, frozenset({"LiveOrderLimits"})),
    ("factory", "paper"): (_PAPER_MATHS, frozenset({
        "ASSUMED_LEVERAGE", "COOLDOWN_BARS_AFTER_STOPLOSS", "MAINTENANCE_MARGIN_RATE", "liquidation_price",
        "settle_trade_plan", "stop_is_beyond_liquidation"})),
    ("forward_book", "paper"): (_STATE_DIR + "; " + _PAPER_MATHS, frozenset({
        "COOLDOWN_BARS_AFTER_STOPLOSS", "OCCUPYING_STATUSES", "STATUS_ENTRY_CANDIDATE", "build_entry_plan",
        "build_outcome_record", "entry_cost_refusal", "open_position", "position_max_hold", "regime_admits",
        "settle_trade_plan", "state_dir", "stop_beyond_liquidation_refusal"})),
    ("forward_confirmation", "pool"): (_RANKING, frozenset({"candidate_quality"})),
    ("live_entry", "live_position"): (_BOOK, frozenset({"compute_open_notional_usdt", "entry_allowed", "live_capacity"})),
    ("live_leg", "live_position"): (_BOOK, frozenset({"build_live_position", "position_risk_usdt", "unbooked_position_id"})),
    ("live_order", "live_position"): (_BOOK, frozenset({"MAX_LIVE_CONCURRENT_POSITIONS", "list_open_live_positions"})),
    ("probe", "live_position"): (_BOOK, frozenset({"entry_allowed"})),
    ("live_leg", "live_pnl"): (_LEDGER, frozenset({"build_live_outcome_record"})),
    ("pre_order_gate", "live_order"): (_IDENTITY, frozenset({"MAX_ACCOUNT_AGE_SECONDS", "enrich_order_identity"})),
    ("breaker_watch", "live_pnl"): (_STORE + " (the live ledger)",
                                    frozenset({"live_outcomes_for_analysis", "read_live_outcomes"})),
    ("probe", "live_pnl"): (_STATE_DIR + "; " + _STORE + " (the slippage observations)",
                            frozenset({"state_dir", "stop_slippage_observations"})),
    ("venue_contract", "account_store"): (_STORE + " (the account snapshot)", frozenset({"read_snapshot"})),
}

# Import cycles, anywhere in the lane and inside one layer too. The one that exists goes in PR7b.
CYCLES: frozenset[frozenset[str]] = frozenset({frozenset({"live_budget", "live_order"})})


def _modules() -> list[str]:
    """Every module in the lane, a sub-package's too, as its dotted path below ``crypto``."""
    found = []
    for path in sorted(CRYPTO.rglob("*.py")):
        parts = path.relative_to(CRYPTO).with_suffix("").parts
        if parts == ("__init__",):
            continue
        found.append(".".join(parts[:-1] if parts[-1] == "__init__" else parts))
    return found


def _source(module: str) -> Path:
    path = CRYPTO.joinpath(*module.split(".")).with_suffix(".py")
    return path if path.is_file() else CRYPTO.joinpath(*module.split("."), "__init__.py")


def _lane_module(absolute: str, lane: set[str]) -> str | None:
    """The lane module an absolute import name lands in (the longest dotted prefix that is one)."""
    if not absolute.startswith(_PACKAGE + "."):
        return None
    parts = absolute[len(_PACKAGE) + 1:].split(".")
    for end in range(len(parts), 0, -1):
        if ".".join(parts[:end]) in lane:
            return ".".join(parts[:end])
    return None


def _imports(module: str, lane: set[str]) -> dict[str, set[str]]:
    """Every lane module ``module`` imports, anywhere in the file, with the names it takes: the names
    of ``from X import a, b``, or, where the module object is bound (``from . import X``,
    ``import …crypto.X as y``), the attributes read from it (``_MODULE`` when none are)."""
    source = _source(module)
    tree = ast.parse(source.read_text(encoding="utf-8"))
    package = (_PACKAGE + "." + module).rsplit(".", 1)[0] if source.name != "__init__.py" else _PACKAGE + "." + module
    names: dict[str, set[str]] = {}
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            base = package.split(".")[: len(package.split(".")) - (node.level - 1)] if node.level else []
            source_name = ".".join(base + ([node.module] if node.module else [])) if node.level else (node.module or "")
            target = _lane_module(source_name, lane)
            for alias in node.names:
                as_module = _lane_module(f"{source_name}.{alias.name}", lane)
                if as_module is not None and as_module != target:
                    aliases[alias.asname or alias.name] = as_module
                elif target is not None:
                    names.setdefault(target, set()).add(alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                target = _lane_module(alias.name, lane)
                if target is not None:
                    if alias.asname:
                        aliases[alias.asname] = target
                    else:
                        names.setdefault(target, set()).add(_MODULE)
    for bound, target in aliases.items():
        read = {n.attr for n in ast.walk(tree)
                if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == bound}
        names.setdefault(target, set()).update(read or {_MODULE})
    names.pop(module, None)
    return names


def _edges() -> dict[tuple[str, str], set[str]]:
    lane = set(_modules())
    return {(src, dst): taken for src in sorted(lane) for dst, taken in _imports(src, lane).items()}


def _upward_edges() -> dict[tuple[str, str], set[str]]:
    rank = {layer: i for i, layer in enumerate(LAYERS)}
    return {
        (src, dst): taken for (src, dst), taken in _edges().items()
        if src in LAYER and dst in LAYER and rank[LAYER[dst]] > rank[LAYER[src]]
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
    unexpected, widened = {}, {}
    for edge, taken in _upward_edges().items():
        if edge not in EXCEPTIONS:
            unexpected[edge] = taken
        elif taken - EXCEPTIONS[edge][1]:
            widened[edge] = taken - EXCEPTIONS[edge][1]
    assert unexpected == {} and widened == {}, (
        "an import points up a layer (importer's layer < imported's). Move the shared name down, or "
        "record a decision in EXCEPTIONS with the step that removes it. New pairs: "
        + "; ".join(f"{s} ({LAYER[s]}) -> {d} ({LAYER[d]}): {sorted(n)}" for (s, d), n in sorted(unexpected.items()))
        + ". New names on a named pair: "
        + "; ".join(f"{s} -> {d}: {sorted(n)}" for (s, d), n in sorted(widened.items()))
    )


def test_every_named_exception_still_exists_name_by_name():
    upward = _upward_edges()
    stale = {edge: sorted(names - upward.get(edge, set())) for edge, (_step, names) in EXCEPTIONS.items()
             if names - upward.get(edge, set())}
    assert stale == {}, f"no longer imported upward: remove them from EXCEPTIONS (it only shrinks): {stale}"


def test_no_import_cycle_but_the_named_one():
    """Tarjan over every lane import, module-level and function-local, within a layer or across."""
    graph: dict[str, set[str]] = {}
    for src, dst in _edges():
        graph.setdefault(src, set()).add(dst)
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    cycles: set[frozenset[str]] = set()

    def visit(node: str) -> None:
        index[node] = low[node] = len(index)
        stack.append(node)
        for nxt in sorted(graph.get(node, ())):
            if nxt not in index:
                visit(nxt)
                low[node] = min(low[node], low[nxt])
            elif nxt in stack:
                low[node] = min(low[node], index[nxt])
        if low[node] == index[node]:
            component = set()
            while True:
                member = stack.pop()
                component.add(member)
                if member == node:
                    break
            if len(component) > 1:
                cycles.add(frozenset(component))

    for node in sorted(graph):
        if node not in index:
            visit(node)
    assert cycles == set(CYCLES), f"import cycles now: {sorted(sorted(c) for c in cycles)}"

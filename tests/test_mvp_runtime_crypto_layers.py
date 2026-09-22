"""The crypto lane's dependency direction (crypto PR7a; directive §9: 역방향 dependency 금지).

Every module in ``runtime/mvp_runtime/crypto/`` (a sub-package's included) sits in exactly one layer,
and a module may import its own layer and the layers below it: Market → Strategy → Decision → Risk →
Execution → Reconciliation → Outcome, the directive's order (its Feedback stage is part of outcome), with
the leaves every layer reads at the bottom and the orchestrators and reports on top. The test reads
every import, module-level and function-local alike, resolved to the module it lands in: a
function-local import is still a dependency, and it is how the lane's one cycle (``live_budget`` ↔
``live_order``, gone since PR7b-2) hid. Nothing in the lane imports by string (``importlib``, ``__import__``) today; an
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
- **the live book (``live_position``) sits in execution.** The sender writes it (``live_leg``) and the
  checks before a send read it (``live_order``, ``live_entry``, ``probe``); the comparison against the
  venue that placed it in reconciliation is ``live_reconcile`` since PR7d-2. The four book pairs that
  pointed up went with that move, not with the code that left.
- **foundation holds only leaves with no role of their own.** Sizing (``live_sizing``) is risk, and the
  distribution gate and limit-entry scoring are strategy.
- **store holds the read path of a record that layers below its writer must read** (PR7d-3). Not
  every record the lane keeps: the book, the counters, the marks and the evidence registry each sit
  with the layer that owns them, because their readers sit at or above it. The live outcome ledger
  is written in outcome (``live_pnl``) and read back by risk (``breaker_watch``) and execution
  (``probe``), so its read path (``live_ledger``) is store, with the corrections that read path
  applies (``live_correction``). Their only writer is the operator's correction door, outside the
  lane, and they are that ledger's second file. The ledger's own writer and what its rows earned stay
  outcome, and the row is built where the fill is (``live_settlement``, execution). Market holds what
  the venue and the vendors say. Store reads only foundation, which is enforced below, so its place
  among the bottom layers carries no edge: it sits with the leaves every acting layer reads, beside
  governance. Nothing in governance or market reads it.
- **one context's market inputs are market, the cycle that uses them is orchestration** (PR7e-2).
  ``feed_assembly`` attaches the legs a context is judged on and judges them for the live entry door
  (``optional_data_health``), reading only ``market_data``, the market stores and the leaves. The age
  bounds that judgement applies are operator tunables (Thomas decision 28); a rule that read beyond
  market data would belong with the door's other checks, in risk, not here. ``attach_mining_legs``,
  which builds the frame a spec is backtested on, followed in PR7e-5: it assembles the same legs and
  reads nothing above market. The retention stores' cohort sweeps (``cohort_retention``) are market
  too (PR7e-6): they record the declared cohort's positioning, open interest and order book on every
  pass, writing only market stores, and the fan-out in ``cycle`` calls them.
- **ranking is strategy, the promotion door is decision** (PR7e-1). ``candidate_ranking`` judges a
  candidate's evidence and orders the store; it keeps no state and refuses nothing. The confirmation
  gate (``forward_confirmation``, strategy) reads the recomputed holdout status from it. ``pool`` keeps
  the doors that turn a tier into a refusal (``assert_promotable_*`` and the sets they refuse on), the
  promotion door's other gates, the routing views, the status transitions and the backlog; the live
  tier left in PR7e-8. The two "what a row minted now would carry" views (``current_cost_basis``,
  ``current_evidence_depth``) went with the formatters they are built on.
- **the pool's two files are decision state, and ``pool`` imports them** (PR7e-7). ``pool_state``
  holds the two files' paths and reads, the pool's install door and the candidates' append door, and
  what each of them checks: each spec, the identity invariant, the artifact stamps, each stamped row's
  self-hash and a new row's lineage. The two writers that rewrite the stored pool in the cycle (the
  status transitions and the live tier's disarm) are not here. ``pool_state`` is decision, not
  store: every module that reads the pool's state sits at or above decision, and store holds only the
  read path of a record that layers below its writer must read. ``pool`` re-exports every public name,
  so the pool's other roles, which all read the state, no longer need ``pool`` for it; what they read
  from each other decides the order in which they can leave.
- **the live tier is decision, and ``pool`` imports it** (PR7e-8). ``live_tier`` says which pool
  entries may spend real money and what each LIVE arm stands on, and holds the disarm door, the one
  automatic writer of the tier, which can only take it away. It reads the stored pool through
  ``pool_state`` and nothing of ``pool``'s; ``pool`` re-exports it, and imports its rule hash for the
  rule-not-routed gate. Its readers (the promotion door, the risk and live-route checks, the readiness
  report) sit at or above decision, and its own reads (``paper``'s occupying statuses, the strategy
  spec, the artifact field) sit at or below it.

No edge points up today: the last one (``forward_confirmation -> pool``) went with PR7e-1, and
``EXCEPTIONS`` is empty, which a test pins. Both only shrink: a new upward pair fails, and so would a
new name on a named pair, or an exception or name that is no longer imported. The one reason an entry
could ever be right is an edge whose removal would change trading behaviour, because the directive puts
"no behaviour change" above the direction; such an entry would change the pin in the same PR, where a
reviewer sees it. Moving a module to a lower layer removes an upward edge as surely as removing an
import, so it is a design decision reviewed like one: the placement must say what the module does.
"""

from __future__ import annotations

import ast
from pathlib import Path

CRYPTO = Path(__file__).resolve().parents[1] / "runtime" / "mvp_runtime" / "crypto"
_PACKAGE = "runtime.mvp_runtime.crypto"

LAYERS: tuple[str, ...] = (
    "foundation", "governance", "store", "market", "strategy", "decision", "risk", "execution",
    "reconciliation", "outcome", "orchestration", "report",
)

LAYER: dict[str, str] = {
    # foundation: leaves shared by every layer
    "state": "foundation", "candidate_identity": "foundation", "refresh_marks": "foundation",
    "indicators": "foundation", "vocabulary": "foundation", "order_identity": "foundation",
    # governance: what every layer allowed to act reads (the stage, its evidence, an order's record)
    "execution_stage": "governance", "testnet_evidence": "governance", "live_governance": "governance",
    # store: the read path of a record written above the layers that read it, and what that read applies
    "live_ledger": "store", "live_correction": "store",
    # market: what the venue and the vendors say
    "market_data": "market", "candle_archive": "market", "oi_store": "market", "orderbook_store": "market",
    "positioning_store": "market", "features": "market", "account": "market", "live_filters": "market",
    "account_store": "market", "feed_assembly": "market", "cohort_retention": "market",
    # strategy: what a strategy is, and how one is generated and judged
    "strategy": "strategy", "strategy_artifact": "strategy", "cost": "strategy", "robustness": "strategy",
    "null_control": "strategy", "factory": "strategy", "proposer": "strategy", "proposer_cli": "strategy",
    "data_review": "strategy", "forward_book": "strategy", "forward_confirmation": "strategy",
    "lifecycle": "strategy", "distribution_gate": "strategy", "limit_entry": "strategy",
    "outcome_math": "strategy", "trade_plan": "strategy", "candidate_ranking": "strategy",
    # decision: which strategies run, and the paper positions they open
    "paper": "decision", "pool": "decision", "routing_marks": "decision", "cooldown": "decision",
    "promotion": "decision", "retirement": "decision", "pool_state": "decision", "live_tier": "decision",
    # risk: what may be risked
    "guards": "risk", "risk_limits": "risk", "live_budget": "risk", "live_allowance": "risk",
    "pre_order_gate": "risk", "breaker_watch": "risk", "live_sizing": "risk",
    # execution: what is sent, the front half that prepares it, the book the sends keep, and the row a
    # close settles
    "live_order": "execution", "live_execution": "execution", "live_leg": "execution",
    "live_entry": "execution", "venue_contract": "execution", "testnet_execution": "execution",
    "live_position": "execution", "live_settlement": "execution", "order_request": "execution",
    "probe": "execution",
    # reconciliation: what the venue says happened
    "live_reconcile": "reconciliation",
    # outcome: what it earned, and what that says
    "live_pnl": "outcome", "feedback": "outcome", "digest": "outcome", "counterfactual": "outcome",
    "live_promotion": "outcome",
    # orchestration: runs the stages in order
    "cycle": "orchestration", "live_route": "orchestration",
    # report: reads everything, imported by nothing inside the lane
    "dashboard": "report", "live_readiness": "report", "route_watch": "report", "tunables": "report",
}

_MODULE = "<module>"      # the module object itself is bound, and nothing is read from it


# (importer, imported) -> (the step that removes it, the names it may take). Empty since crypto PR7e-1;
# both only shrink, so a new upward pair fails and so does a new name on a named pair.
EXCEPTIONS: dict[tuple[str, str], tuple[str, frozenset[str]]] = {}

# Import cycles, anywhere in the lane and inside one layer too. None since crypto PR7b-2.
CYCLES: frozenset[frozenset[str]] = frozenset()


def _modules(root: Path = CRYPTO) -> list[str]:
    """Every module in the lane, a sub-package's too, as its dotted path below ``crypto``."""
    found = []
    for path in sorted(root.rglob("*.py")):
        parts = path.relative_to(root).with_suffix("").parts
        if parts == ("__init__",):
            continue
        found.append(".".join(parts[:-1] if parts[-1] == "__init__" else parts))
    return found


def _source(module: str, root: Path = CRYPTO) -> Path:
    path = root.joinpath(*module.split(".")).with_suffix(".py")
    return path if path.is_file() else root.joinpath(*module.split("."), "__init__.py")


def _lane_module(absolute: str, lane: set[str]) -> str | None:
    """The lane module an absolute import name lands in (the longest dotted prefix that is one)."""
    if not absolute.startswith(_PACKAGE + "."):
        return None
    parts = absolute[len(_PACKAGE) + 1:].split(".")
    for end in range(len(parts), 0, -1):
        if ".".join(parts[:end]) in lane:
            return ".".join(parts[:end])
    return None


def _imports(module: str, lane: set[str], root: Path = CRYPTO) -> dict[str, set[str]]:
    """Every lane module ``module`` imports, anywhere in the file, with the names it takes: the names
    of ``from X import a, b``, or, where the module object is bound (``from . import X``,
    ``import …crypto.X as y``), the attributes read from it (``_MODULE`` when none are)."""
    source = _source(module, root)
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


def _edges(root: Path = CRYPTO) -> dict[tuple[str, str], set[str]]:
    lane = set(_modules(root))
    return {(src, dst): taken for src in sorted(lane) for dst, taken in _imports(src, lane, root).items()}


def _cycles(edges) -> set[frozenset[str]]:
    """Tarjan over every lane import, module-level and function-local, within a layer or across."""
    graph: dict[str, set[str]] = {}
    for src, dst in edges:
        graph.setdefault(src, set()).add(dst)
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    found: set[frozenset[str]] = set()

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
                found.add(frozenset(component))

    for node in sorted(graph):
        if node not in index:
            visit(node)
    return found


def _problems(root: Path = CRYPTO, layer=None, layers=LAYERS, exceptions=None) -> dict:
    """What the lane at ``root`` breaks, against a layer map and a list of named exceptions."""
    layer = LAYER if layer is None else layer
    exceptions = EXCEPTIONS if exceptions is None else exceptions
    modules, edges = set(_modules(root)), _edges(root)
    rank = {name: i for i, name in enumerate(layers)}
    upward = {(s, d): taken for (s, d), taken in edges.items()
              if s in layer and d in layer and rank[layer[d]] > rank[layer[s]]}
    return {
        "unplaced": sorted(modules - set(layer)),
        "gone": sorted(set(layer) - modules),
        "unexpected": {edge: taken for edge, taken in upward.items() if edge not in exceptions},
        "widened": {edge: taken - exceptions[edge][1] for edge, taken in upward.items()
                    if edge in exceptions and taken - exceptions[edge][1]},
        "stale": {edge: sorted(names - upward.get(edge, set())) for edge, (_step, names) in exceptions.items()
                  if names - upward.get(edge, set())},
        "cycles": _cycles(edges),
    }


def test_every_crypto_module_has_exactly_one_layer():
    problems = _problems()
    assert _modules(), "no crypto modules found: the path broke, not the lane"
    assert problems["unplaced"] == [], "place the new module in a layer (LAYER)"
    assert problems["gone"] == [], "a module left the lane: drop it from LAYER"
    assert set(LAYER.values()) <= set(LAYERS)


def test_the_layers_keep_the_directives_order():
    directive = ("market", "strategy", "decision", "risk", "execution", "reconciliation", "outcome")
    assert [layer for layer in LAYERS if layer in directive] == list(directive)


def test_no_crypto_import_points_up_a_layer_beyond_the_named_ones():
    problems = _problems()
    assert problems["unexpected"] == {} and problems["widened"] == {}, (
        "an import points up a layer (importer's layer < imported's). Move the shared name down, or "
        "record a decision in EXCEPTIONS with the step that removes it. New pairs: "
        + "; ".join(f"{s} ({LAYER[s]}) -> {d} ({LAYER[d]}): {sorted(n)}"
                    for (s, d), n in sorted(problems["unexpected"].items()))
        + ". New names on a named pair: "
        + "; ".join(f"{s} -> {d}: {sorted(n)}" for (s, d), n in sorted(problems["widened"].items()))
    )


def test_no_upward_edge_is_excepted():
    """Empty since PR7e-1. The directive forbids reverse dependencies, so an entry is a decision to
    allow one, justified only where removing the edge would change trading behaviour; it changes this
    pin in the same PR, where a reviewer sees it."""
    assert EXCEPTIONS == {}


def test_every_named_exception_still_exists_name_by_name():
    stale = _problems()["stale"]
    assert stale == {}, f"no longer imported upward: remove them from EXCEPTIONS (it only shrinks): {stale}"


def test_no_import_cycle_but_the_named_one():
    cycles = _problems()["cycles"]
    assert cycles == set(CYCLES), f"import cycles now: {sorted(sorted(c) for c in cycles)}"


def test_the_store_reads_nothing_above_foundation():
    """The store is read from above by risk, execution and outcome. Its rule is what keeps that honest:
    a store module that read a layer above foundation would carry that layer's dependencies into every
    reader, which is the upward edge the layer exists to remove."""
    lane = set(_modules())
    store = {name for name, layer in LAYER.items() if layer == "store"}
    assert store, "no store modules: the map changed, not the rule"
    reads = {(src, dst) for (src, dst) in _edges() if src in store and LAYER.get(dst) not in ("foundation", "store")}
    assert reads == set(), f"store modules read above foundation: {sorted(reads)}"
    assert store <= lane


def test_the_scanner_sees_every_import_form_and_every_breach(tmp_path):
    """The checks above pass on a lane with nothing to find, which proves nothing about them. Here a
    synthetic lane holds one of each: the import spellings, a sub-package, a name beyond its
    exception, a stale exception, an unplaced module and a cycle."""
    files = {
        "low.py": (
            "from __future__ import annotations\n"
            "from typing import TYPE_CHECKING\n"
            "from .high import a\n"
            "from runtime.mvp_runtime.crypto.high import e\n"
            "import runtime.mvp_runtime.crypto.high as hh\n"
            "from runtime.mvp_runtime.crypto import high\n"
            "if TYPE_CHECKING:\n"
            "    from .high import F\n"
            "def f():\n"
            "    from . import high as h2\n"
            "    return h2.b, hh.c, high.d\n"),
        "high.py": "def g():\n    from .low import z\n",
        "sub/__init__.py": "",
        "sub/deep.py": "from ..low import g\nfrom .. import high\nx = high.h\n",
        "stray.py": "",
    }
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    layer = {"low": "l1", "high": "l2", "sub": "l1", "sub.deep": "l1"}
    exceptions = {("low", "high"): ("x", frozenset({"a", "b", "c", "d", "e"})),
                  ("sub.deep", "low"): ("x", frozenset({"g"}))}
    assert _imports("low", set(_modules(tmp_path)), tmp_path) == {"high": {"a", "b", "c", "d", "e", "F"}}
    problems = _problems(tmp_path, layer, ("l1", "l2"), exceptions)
    assert problems["unplaced"] == ["stray"]
    assert problems["unexpected"] == {("sub.deep", "high"): {"h"}}
    assert problems["widened"] == {("low", "high"): {"F"}}
    assert problems["stale"] == {("sub.deep", "low"): ["g"]}
    assert problems["cycles"] == {frozenset({"low", "high"})}

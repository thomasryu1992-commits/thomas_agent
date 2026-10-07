"""What each operator script that imports the crypto lane can do, by class (crypto refactor plan PR-03;
``docs/proposals/CRYPTO_REFACTOR_AND_MODULARIZATION_PLAN_V0.1.md`` §F-3, §M-2c).

The layer test reads ``runtime/mvp_runtime/crypto/`` only. The scripts that import the lane are outside
it, and three of them send orders. This file says what each one can do and checks the claim against
what the other rosters already establish, so a class cannot be picked by hand without being true:

- **coverage.** Every script that imports the lane (any import form, function-local too) is listed
  here, and nothing else is.
- **the exchange classes come from the egress roster.** ``EXCHANGE_WRITE`` is exactly the set of
  scripts whose entries in ``test_mvp_runtime_crypto_egress_roster.ROSTER`` reach a send or a cancel.
  ``ORDER_KEY_READ`` is exactly the set whose entries there only select the venue reader,
  validate an order or read. Selecting the order adapter itself makes a script ``EXCHANGE_WRITE``
  (PR-15 follow-up): that object can send, whatever the script calls on it today. A script cannot be relabelled without its calls changing too.
- **the writers come from the state guard.** Every lane script in
  ``test_state_guard_covers_state_writing_clis.GUARDED`` writes something, so none of them may be
  ``READ``. No ``READ`` script calls the host-root guard either, because only a writer needs it.

The classes:

- ``EXCHANGE_WRITE``: can send, close or cancel at the venue.
- ``ORDER_KEY_READ``: holds the venue reader (the mainnet order key, no ``submit`` or ``cancel_order``) to
  validate or read, and sends nothing.
- ``STATE_WRITE``: writes governed or live state (approvals, stage, pool, budget, limits, breakers,
  ledger).
- ``RESEARCH_WRITE``: writes research or observation stores only (candidates, forward books,
  counterfactuals).
- ``READ``: reads and reports.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LANE = "runtime.mvp_runtime.crypto"

EXCHANGE_WRITE = "EXCHANGE_WRITE"
ORDER_KEY_READ = "ORDER_KEY_READ"
STATE_WRITE = "STATE_WRITE"
RESEARCH_WRITE = "RESEARCH_WRITE"
READ = "READ"

SCRIPTS: dict[str, str] = {
    # the venue: sends
    "scripts/run_slippage_probe.py": EXCHANGE_WRITE,          # --fire: entry, stop, closes
    "scripts/run_signed_testnet_cycle.py": EXCHANGE_WRITE,    # testnet entry, brackets, close, cancels
    "scripts/emergency_close.py": EXCHANGE_WRITE,             # --confirm: reduce-only closes
    # the venue: the order key, no send
    "scripts/diagnose_bracket_leg.py": ORDER_KEY_READ,        # /order/test
    "scripts/list_resting_orders.py": ORDER_KEY_READ,         # open orders
    "scripts/venue_contract.py": ORDER_KEY_READ,              # --run: /order/test and reads, writes its record
    # governed and live state
    "scripts/clear_api_breaker.py": STATE_WRITE,
    "scripts/clear_bracket_breaker.py": STATE_WRITE,
    "scripts/correct_live_outcome.py": STATE_WRITE,
    "scripts/disarm_live_strategies.py": STATE_WRITE,
    "scripts/import_crypto_history.py": STATE_WRITE,
    "scripts/promote_strategy_candidates.py": STATE_WRITE,
    "scripts/record_unreported_live_order.py": STATE_WRITE,
    "scripts/register_crypto_risk_limits.py": STATE_WRITE,
    "scripts/register_execution_stage.py": STATE_WRITE,
    "scripts/register_live_trading_budget.py": STATE_WRITE,
    "scripts/retire_strategies.py": STATE_WRITE,
    # research and observation stores
    "scripts/dedupe_counterfactual_book.py": RESEARCH_WRITE,
    "scripts/forward_cohort.py": RESEARCH_WRITE,
    "scripts/hypothesis_trial.py": RESEARCH_WRITE,
    "scripts/import_archive_oi.py": RESEARCH_WRITE,             # --apply: archive OI into oi_store
    "scripts/rescore_stale_holdout_candidates.py": RESEARCH_WRITE,
    "scripts/seed_forward_book.py": RESEARCH_WRITE,
    # reads and reports
    "scripts/condition_effectiveness_report.py": READ,
    "scripts/family_period_test.py": READ,
    "scripts/measure_live_slippage.py": READ,
    "scripts/paired_family_window_check.py": READ,
    "scripts/pooled_mint_check.py": READ,
    "scripts/probe_signal_rate.py": READ,
    "scripts/selection_evidence.py": READ,
    "scripts/strategy_funnel.py": READ,
    "scripts/verify_factory_tier_freeze.py": READ,
    "scripts/walk_forward_stability_report.py": READ,
}

CLASSES = frozenset({EXCHANGE_WRITE, ORDER_KEY_READ, STATE_WRITE, RESEARCH_WRITE, READ})

# Egress-roster callees that do not send: selecting the venue reader, validating, and the checks that
# validate and read. A script whose roster entries are only these is ORDER_KEY_READ. The order adapter's
# own selector is not one of them: it hands over an object that can send.
NON_SENDING_CALLEES = frozenset({
    "live_execution.select_venue_reader", "adapter.validate_order", "venue_contract.refresh_verification",
})


def _imports_the_lane(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and not node.level:
            if node.module == LANE or node.module.startswith(LANE + "."):
                return True
            if node.module == "runtime.mvp_runtime" and any(a.name == "crypto" for a in node.names):
                return True
        if isinstance(node, ast.Import) and any(a.name == LANE or a.name.startswith(LANE + ".") for a in node.names):
            return True
    return False


def lane_scripts(repo: Path = REPO) -> set[str]:
    return {p.relative_to(repo).as_posix() for p in sorted((repo / "scripts").rglob("*.py"))
            if _imports_the_lane(ast.parse(p.read_text(encoding="utf-8")))}


def exchange_classes(roster) -> dict[str, str]:
    """What the egress roster says each script in it is: it sends, or it only holds the order key."""
    callees: dict[str, set[str]] = {}
    for (path, _where, callee) in roster:
        if path.startswith("scripts/"):
            callees.setdefault(path, set()).add(callee)
    return {path: ORDER_KEY_READ if names <= NON_SENDING_CALLEES else EXCHANGE_WRITE
            for path, names in callees.items()}


# `state_guard`'s two refusals of a host-side root write.
ROOT_GUARDS = frozenset({"assert_not_foreign_root_run", "assert_state_writable"})


def _calls_the_root_guard(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return any(isinstance(n, ast.Call) and (getattr(n.func, "attr", None) or getattr(n.func, "id", None))
               in ROOT_GUARDS for n in ast.walk(tree))


def test_every_script_that_imports_the_lane_has_a_class():
    found = lane_scripts()
    assert found, "no script imports the crypto lane: the scan broke, not the scripts"
    assert sorted(found - set(SCRIPTS)) == [], "new lane script: give it a class in SCRIPTS"
    assert sorted(set(SCRIPTS) - found) == [], "no longer imports the lane (or gone): drop it from SCRIPTS"
    assert set(SCRIPTS.values()) <= CLASSES


def test_the_exchange_classes_are_what_the_egress_roster_says():
    from tests.test_mvp_runtime_crypto_egress_roster import ROSTER

    derived = exchange_classes(ROSTER)
    declared = {path: cls for path, cls in SCRIPTS.items() if cls in (EXCHANGE_WRITE, ORDER_KEY_READ)}
    assert declared == derived, (
        "a script's exchange class must follow its calls in the egress roster: "
        f"declared {sorted(declared.items())}, roster says {sorted(derived.items())}"
    )


def test_no_guarded_writer_is_read_only():
    from tests.test_state_guard_covers_state_writing_clis import GUARDED

    guarded = {f"scripts/{name}.py" for name in GUARDED} & set(SCRIPTS)
    assert guarded, "no guarded writer imports the lane: the roster link broke"
    assert sorted(p for p in guarded if SCRIPTS[p] == READ) == []


def test_no_read_script_guards_against_a_root_write():
    """A script that needs the host-root guard writes something. That makes READ the wrong class."""
    writers = [p for p, cls in SCRIPTS.items() if cls in (STATE_WRITE, EXCHANGE_WRITE) and _calls_the_root_guard(REPO / p)]
    assert writers, "no writer calls the guard: the detector broke, and this test would pass on anything"
    guarded = sorted(p for p, cls in SCRIPTS.items() if cls == READ and _calls_the_root_guard(REPO / p))
    assert guarded == [], f"READ scripts that call the host-root write guard: {guarded}"


def test_the_derivations_see_what_they_check(tmp_path):
    """The derivations above, on inputs built to break them. An aliased lane import and a
    function-local one both count as the lane, and a similar-looking module does not. A script that
    only validates through the venue reader is ORDER_KEY_READ. One that also cancels is EXCHANGE_WRITE,
    and so is one that only selects the order adapter."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "a.py").write_text("from runtime.mvp_runtime.crypto import live_route as r\n")
    (scripts / "b.py").write_text("def f():\n    from runtime.mvp_runtime import crypto\n")
    (scripts / "c.py").write_text("import runtime.mvp_runtime.crypto_other\nfrom runtime.mvp_runtime import store\n")
    assert lane_scripts(tmp_path) == {"scripts/a.py", "scripts/b.py"}
    roster = {("scripts/v.py", "main", "adapter.validate_order"): (1, ""),
              ("scripts/v.py", "main", "live_execution.select_venue_reader"): (1, ""),
              ("scripts/u.py", "main", "live_execution.select_order_adapter"): (1, ""),
              ("scripts/w.py", "main", "live_execution.select_order_adapter"): (1, ""),
              ("scripts/w.py", "main", "adapter.cancel_order"): (1, ""),
              ("runtime/x.py", "f", "adapter.submit"): (1, "")}
    assert exchange_classes(roster) == {"scripts/v.py": ORDER_KEY_READ, "scripts/u.py": EXCHANGE_WRITE,
                                        "scripts/w.py": EXCHANGE_WRITE}

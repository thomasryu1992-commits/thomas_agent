"""No new code reads through the ``pool`` facade (crypto PR7).

PR7e-1 and PR7e-7 to PR7e-11 moved ``pool``'s roles into their own modules, and ``pool`` re-exports every
public name as the same object, so the callers of the day kept reading ``pool.<name>``. That is a
compatibility layer, not an owner: a new reader that goes through it couples to ``pool`` again, and
``pool`` would grow back into the module the split took apart.

The rule is a ratchet. Every name read through ``pool`` in ``runtime/`` and ``scripts/`` is listed in
``BASELINE``, file by file: those reads stay allowed. A new one fails, and the message names the module
that owns the name. A listed read that is gone fails too, so the list only shrinks. The tests are left
out on purpose, because they hold each re-export to its owner's object and must read ``pool`` to do it.

The scan reads every import, module-level and function-local alike: ``from ...crypto import pool``
(under any alias, absolute or relative), ``import runtime.mvp_runtime.crypto.pool``, and
``from ...pool import <name>``. It then counts each ``<alias>.<name>`` read in that file. A local
variable that shares an alias's name would count too. Nothing here imports by string today; an import
made that way would not be seen.
"""

from __future__ import annotations

import ast
import collections
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POOL = "runtime.mvp_runtime.crypto.pool"
SCANNED = ("runtime", "scripts")

# The reads through ``pool`` on 2026-09-23, after PR7e-11. Remove an entry when its read goes; never add one.
BASELINE: dict[str, tuple[str, ...]] = {
    "runtime/mvp_runtime/crypto/breaker_watch.py": (
        "load_active_pool", "routable_lineage_keys", "routable_strategy_ids",
    ),
    "runtime/mvp_runtime/crypto/cycle.py": (
        "LIFECYCLE_DECISION_STALE", "apply_status_decisions", "context_scores", "disarm_live_tier",
        "live_arm_approvals", "live_routable_strategy_ids", "load_active_pool", "max_routable_per_context",
        "routable_lineage_keys", "routable_strategy_ids",
    ),
    "runtime/mvp_runtime/crypto/dashboard.py": (
        "BACKLOG_REFUSAL_AXES", "OCCUPYING_STATUSES", "load_active_pool", "promotable_backlog",
        "read_candidates", "routable_directional_capacity",
    ),
    "runtime/mvp_runtime/crypto/live_readiness.py": (
        "live_arm_approvals", "live_arm_entries", "live_arm_unsound", "live_routable_strategy_ids",
        "load_active_pool", "routable_strategy_ids",
    ),
    "runtime/mvp_runtime/crypto/live_route.py": (
        "ARTIFACT_SHA256_FIELD", "live_arm_approvals", "live_arm_entries", "live_arm_unsound",
        "live_routable_strategy_ids", "load_active_pool",
    ),
    "runtime/mvp_runtime/crypto/promotion.py": (
        "LIVE_TIERS", "LIVE_TIER_FIELD", "LIVE_TIER_LIVE", "OCCUPYING_STATUSES", "assert_family_cap",
        "assert_no_cluster_siblings", "assert_no_semantic_duplicates", "assert_no_silent_reactivation",
        "assert_observation_entry_bar", "assert_pool_within_size_cap", "assert_promotable_cost_basis",
        "assert_promotable_derivation", "assert_promotable_evidence_depth", "assert_rule_not_routed",
        "load_active_pool", "pool_candidate_records", "reactivated_candidate_ids", "replaced_entries",
        "resolve_candidates", "silent_reactivations",
    ),
    "runtime/mvp_runtime/crypto/retirement.py": (
        "apply_status_decisions", "load_active_pool",
    ),
    "runtime/mvp_runtime/domain_console.py": (
        "load_active_pool", "routable_strategy_ids",
    ),
    "runtime/mvp_runtime/scheduler.py": (
        "append_candidates", "load_active_pool", "read_candidates",
    ),
    "scripts/condition_effectiveness_report.py": (
        "read_candidates",
    ),
    "scripts/disarm_live_strategies.py": (
        "disarm_live_tier", "live_routable_strategy_ids", "read_pool_to_disarm",
    ),
    "scripts/family_period_test.py": (
        "read_candidates",
    ),
    "scripts/import_crypto_history.py": (
        "LIVE_TIER_FIELD", "LIVE_TIER_OBSERVATION", "append_candidates", "assert_no_semantic_duplicates",
        "install_active_pool", "read_candidates",
    ),
    "scripts/paired_family_window_check.py": (
        "read_candidates",
    ),
    "scripts/pooled_mint_check.py": (
        "candidate_id", "read_candidates",
    ),
    "scripts/probe_signal_rate.py": (
        "as_pool_entry_for_replay", "load_active_pool", "read_candidates",
    ),
    "scripts/promote_strategy_candidates.py": (
        "COST_BASIS_RANK_CONSERVATIVE", "COST_BASIS_RANK_CURRENT", "COST_BASIS_RANK_OPTIMISTIC",
        "COST_BASIS_RANK_UNRECORDED", "EVIDENCE_DEPTH_RANK_FULL", "EVIDENCE_DEPTH_RANK_SHALLOW",
        "EVIDENCE_DEPTH_RANK_UNRECORDED", "LIVE_TIER_APPROVAL_FIELD", "LIVE_TIER_FIELD", "LIVE_TIER_LIVE",
        "PROMOTABLE_COST_BASIS_RANKS", "PROMOTABLE_DERIVATION_TYPES", "PROMOTABLE_EVIDENCE_DEPTH_RANKS",
        "admission_evidence", "assert_rule_not_routed", "attempt_context_key", "attempts_by_context",
        "candidate_id", "candidate_quality", "cost_basis_of", "current_evidence_depth", "evidence_depth_of",
        "install_active_pool", "load_active_pool", "near_duplicate_groups", "pooled_context_keys",
        "rank_candidates", "read_candidates", "replaced_entries", "resolve_candidates",
        "routable_directional_capacity", "semantic_duplicate_groups", "silent_reactivations",
    ),
    "scripts/rescore_stale_holdout_candidates.py": (
        "append_candidates", "candidate_id", "derive_candidate_id", "read_candidates",
    ),
    "scripts/retire_strategies.py": (
        "load_active_pool",
    ),
    "scripts/seed_forward_book.py": (
        "load_active_pool", "read_candidates",
    ),
    "scripts/verify_factory_tier_freeze.py": (
        "read_candidates",
    ),
    "scripts/walk_forward_stability_report.py": (
        "read_candidates",
    ),
}


def _module_name(path: Path) -> str:
    parts = path.relative_to(ROOT).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def _resolve(module: str | None, level: int, current: str, is_package: bool) -> str:
    if not level:
        return module or ""
    base = current.split(".") if is_package else current.split(".")[:-1]
    base = base[: len(base) - (level - 1)]
    return ".".join(base + ([module] if module else []))


def reads_through_pool(source: str, current: str, *, is_package: bool = False) -> set[str]:
    """The names ``source`` (module ``current``) reads through ``pool``."""
    tree = ast.parse(source)
    reads: set[str] = set()
    aliases: set[str] = set()
    dotted: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            origin = _resolve(node.module, node.level, current, is_package)
            for alias in node.names:
                if origin == POOL:
                    reads.add(alias.name)
                elif f"{origin}.{alias.name}" == POOL:
                    aliases.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == POOL:
                    if alias.asname:
                        aliases.add(alias.asname)
                    else:
                        dotted.add(POOL)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        if isinstance(node.value, ast.Name) and node.value.id in aliases:
            reads.add(node.attr)
        elif dotted and ast.unparse(node.value) == POOL:
            reads.add(node.attr)
    return reads


def _scan() -> dict[str, set[str]]:
    found: dict[str, set[str]] = collections.defaultdict(set)
    for top in SCANNED:
        for path in sorted((ROOT / top).rglob("*.py")):
            current = _module_name(path)
            if current == POOL:
                continue
            reads = reads_through_pool(path.read_text(encoding="utf-8"), current,
                                       is_package=path.name == "__init__.py")
            if reads:
                found[path.relative_to(ROOT).as_posix()] |= reads
    return found


def _owner(name: str) -> str:
    """Where to import ``name`` from instead: the module that defines it, or, for a constant, the modules
    other than ``pool`` that hold the same object."""
    from runtime.mvp_runtime.crypto import pool

    value = getattr(pool, name, None)
    if value is None:
        return "not a name of pool"
    defined = getattr(value, "__module__", None)
    if isinstance(defined, str) and defined != POOL and (callable(value) or isinstance(value, type)):
        return defined
    holders = sorted(module_name for module_name, module in list(sys.modules.items())
                     if module_name.startswith("runtime.mvp_runtime.crypto.") and module_name != POOL
                     and vars(module).get(name) is value)
    return " or ".join(holders) or POOL


def test_no_new_read_goes_through_the_pool_facade():
    new = sorted((path, name) for path, names in _scan().items() for name in names
                 if name not in BASELINE.get(path, ()))
    assert not new, "read these from their owner module, not through pool:\n" + "\n".join(
        f"  {path}: pool.{name} -> {_owner(name)}" for path, name in new)


def test_the_baseline_only_shrinks():
    found = _scan()
    gone = sorted((path, name) for path, names in BASELINE.items() for name in names
                  if name not in found.get(path, set()))
    assert not gone, "these reads through pool are gone; remove them from BASELINE:\n" + "\n".join(
        f"  {path}: {name}" for path, name in gone)


def test_the_scan_sees_every_import_form():
    """A scan blind to one import form would pass with a new reader in plain sight."""
    here = "runtime.mvp_runtime.crypto.somewhere"
    cases = {
        "from runtime.mvp_runtime.crypto import pool\npool.a": {"a"},
        "from runtime.mvp_runtime.crypto import market_data, pool as store\nstore.b": {"b"},
        "from . import pool\npool.c": {"c"},
        "from .pool import d, e as f": {"d", "e"},
        "from runtime.mvp_runtime.crypto.pool import g": {"g"},
        "import runtime.mvp_runtime.crypto.pool\nruntime.mvp_runtime.crypto.pool.h": {"h"},
        "import runtime.mvp_runtime.crypto.pool as p\np.i": {"i"},
        "def later():\n    from . import pool\n    return pool.j": {"j"},
        "from . import pool_state\npool_state.k": set(),        # an owner module is not the facade
        "from .pool_admission import l": set(),
    }
    for source, expected in cases.items():
        assert reads_through_pool(textwrap.dedent(source), here) == expected, source
    # a relative import from a package's __init__ resolves against the package itself
    assert reads_through_pool("from .pool import m", "runtime.mvp_runtime.crypto", is_package=True) == {"m"}
    assert reads_through_pool("from .crypto import pool\npool.n", "runtime.mvp_runtime.domain_console") == {"n"}
    assert reads_through_pool("from .. import pool\npool.o", "runtime.mvp_runtime.crypto.sub.x") == {"o"}

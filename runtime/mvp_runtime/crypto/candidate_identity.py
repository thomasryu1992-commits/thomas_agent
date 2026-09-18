"""The candidate's globally unique id — its lineage, not its display name. One leaf.

``factory`` mints candidates and ``pool`` stores them, and both need the id rule. While it
lived in ``pool``, ``factory -> pool`` was a module-level edge whose only cargo was these
two functions, and the reverse read (``pool`` reading the holdout split rule) had to stay
function-local to break the cycle that edge closed. The rule owns no state and imports
nothing from the package, so it is the natural leaf: ``pool`` re-exports it for its many
callers, ``factory`` imports it directly, and the factory<->pool module cycle is gone.

The lineage KEY an outcome is attributed by (``outcome_attribution_key``) and the keys a pool entry
accepts (``entry_attribution_keys``) moved here from ``lifecycle`` for the same reason (PR3b-1): the
router ranks by a lineage's realized record (Thomas decision 35), and ``lifecycle -> feedback ->
paper`` would close a cycle the other way. ``lifecycle`` re-exports them.
"""

from __future__ import annotations

from typing import Any, Mapping

from runtime.read_only_kernel import integrity


def derive_candidate_id(record: Mapping[str, Any]) -> str:
    """The globally unique id of one candidate: its lineage, not its display name.

    ``strategy_id`` restarts at S001 every factory generation, so it can never key a
    lookup. The id derives from (generation_id, strategy_rule_hash,
    evidence_input_sha256) — the exact strategy content in its exact generation with
    its exact evidence window — so legacy rows without a stored ``candidate_id``
    derive the same id on every read and the append-only store is never rewritten."""
    return integrity.short_id("cand", {
        "generation_id": record.get("generation_id"),
        "strategy_rule_hash": record.get("strategy_rule_hash"),
        "evidence_input_sha256": record.get("evidence_input_sha256"),
    })


def candidate_id(record: Mapping[str, Any]) -> str:
    stored = record.get("candidate_id")
    if isinstance(stored, str) and stored:
        return stored
    return derive_candidate_id(record)


def outcome_attribution_key(record: Mapping[str, Any]) -> str:
    """The lineage an outcome belongs to — never the display name alone.

    ``strategy_id`` restarts at S001 every factory generation, so grouping by it mixes
    a replaced strategy's history into its successor's evaluation: a fresh strategy
    inherits the losses that got its predecessor replaced, or hides behind its wins.
    Preference order: the exact ``candidate_id``; else the (generation, rule hash)
    pair, which is equally lineage-precise and is what pre-lineage outcomes carry;
    else the bare id (imported history with no lineage at all, honestly coarse)."""
    cand = record.get("candidate_id")
    if isinstance(cand, str) and cand:
        return f"cand:{cand}"
    generation = record.get("strategy_generation_id") or record.get("generation_id")
    rule_hash = record.get("strategy_rule_hash")
    if isinstance(generation, str) and generation and isinstance(rule_hash, str) and rule_hash:
        return f"gen:{generation}:{rule_hash}"
    strategy_id = record.get("strategy_id")
    return f"sid:{strategy_id}" if isinstance(strategy_id, str) and strategy_id else ""


def entry_attribution_keys(entry: Mapping[str, Any]) -> set[str]:
    """Every key an outcome of THIS pool entry could carry, across three eras of
    record-keeping. An outcome is keyed at the best precision IT has, so the entry
    must accept all three or history written before a field existed goes unattributed:

    - ``cand:`` — outcomes since the lineage reached the trading path. Exact.
    - ``gen:``  — outcomes carrying (generation, rule hash). Also lineage-precise:
      a different generation of the same display name keys differently, which is
      what stops a replaced strategy from inheriting its predecessor's record.
    - ``sid:``  — imported history that carries nothing but the display name. It
      cannot be placed in a lineage because it never recorded one, so it attaches to
      whoever holds that name. This is the ONE imprecise join, it is confined to
      pre-lineage records, and the set only shrinks: every new outcome keys on
      ``cand:`` and can never be absorbed by a different lineage. Dropping it instead
      would silently zero out the lifecycle's input for strategies still trading on
      imported history — blinding the lifecycle's auto-demotion, and since PR3b-1 the
      router's realized ranking, which reads the same keys.
    """
    keys: set[str] = set()
    cand = entry.get("candidate_id")
    if isinstance(cand, str) and cand:
        keys.add(f"cand:{cand}")
    generation = entry.get("generation_id") or entry.get("strategy_generation_id")
    rule_hash = entry.get("strategy_rule_hash")
    if isinstance(generation, str) and generation and isinstance(rule_hash, str) and rule_hash:
        keys.add(f"gen:{generation}:{rule_hash}")
    strategy_id = entry.get("strategy_id")
    if isinstance(strategy_id, str) and strategy_id:
        keys.add(f"sid:{strategy_id}")
    return keys

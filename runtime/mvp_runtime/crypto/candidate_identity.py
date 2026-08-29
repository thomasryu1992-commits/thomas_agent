"""The candidate's globally unique id — its lineage, not its display name. One leaf.

``factory`` mints candidates and ``pool`` stores them, and both need the id rule. While it
lived in ``pool``, ``factory -> pool`` was a module-level edge whose only cargo was these
two functions, and the reverse read (``pool`` reading the holdout split rule) had to stay
function-local to break the cycle that edge closed. The rule owns no state and imports
nothing from the package, so it is the natural leaf: ``pool`` re-exports it for its many
callers, ``factory`` imports it directly, and the factory<->pool module cycle is gone.
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

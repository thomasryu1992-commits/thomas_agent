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
paper`` would close a cycle the other way. ``lifecycle`` imports them back, so its importers are
unchanged. Since PR3c an entry also accepts the keys of the entries it replaced (Thomas decision 41,
:data:`PREDECESSOR_KEYS_FIELD`).
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


# The forms `outcome_attribution_key` returns. A value with one of these prefixes names a lineage; a
# bare value is a display id (PR3b-3: a drawdown rebase seals lineage keys, where it named ids).
LINEAGE_KEY_PREFIXES = ("cand:", "gen:", "sid:")


def is_lineage_key(value: Any) -> bool:
    """Whether ``value`` is a lineage key (`outcome_attribution_key`'s form), not a display id."""
    return isinstance(value, str) and any(
        value.startswith(prefix) and len(value) > len(prefix) for prefix in LINEAGE_KEY_PREFIXES)


# The lineage keys a pool entry inherited (PR3c, Thomas decision 41): when the promotion door returns
# a retired RULE to trading under a new candidate, the new entry replaces the retired entries that
# held the rule and records the keys that named them (`precise_lineage_keys`, transitively). The
# same rule is the same strategy, so its record follows it. Written by the door alone; an entry
# that replaced nothing carries no field. A door-time fact about the pool, not a property of the
# candidate, so the strategy artifact does not cover it (like `status` and `live_tier`).
PREDECESSOR_KEYS_FIELD = "predecessor_lineage_keys"


def predecessor_keys(entry: Mapping[str, Any]) -> set[str]:
    """The lineage keys ``entry`` inherited (:data:`PREDECESSOR_KEYS_FIELD`). A value that is not a
    lineage key names nothing and is not read; the pool's identity check refuses a pool that
    carries one (`pool.assert_pool_identity_unique`)."""
    value = entry.get(PREDECESSOR_KEYS_FIELD)
    if not isinstance(value, (list, tuple)):
        return set()
    return {key for key in value if is_lineage_key(key)}


def entry_attribution_keys(entry: Mapping[str, Any]) -> set[str]:
    """Every key an outcome of THIS pool entry could carry: its own (:func:`own_attribution_keys`)
    and the ones it inherited from the entries it replaced (:func:`predecessor_keys`, PR3c). What
    the lifecycle judges an entry on, what the router ranks it by, what the live allowance charges
    it and what the drawdown guard counts as routable."""
    return own_attribution_keys(entry) | predecessor_keys(entry)


def precise_lineage_keys(entry: Mapping[str, Any]) -> set[str]:
    """The keys that name ``entry``'s lineage itself: its candidate and its generation and rule
    hash, the display-id key only when it has neither, and what it inherited.

    The rule a drawdown rebase is sealed by (PR3b-3) and a successor inherits by (PR3c): a
    ``sid:`` key is a display name, which a later entry may take, so it names a lineage only when
    nothing better does."""
    own = own_attribution_keys(entry)
    precise = {key for key in own if not key.startswith("sid:")}
    return (precise or own) | predecessor_keys(entry)


def own_attribution_keys(entry: Mapping[str, Any]) -> set[str]:
    """Every key an outcome of THIS pool entry's own lineage could carry, across three eras of
    record-keeping. An outcome is keyed at the best precision IT has, so the entry
    must accept all three or history written before a field existed goes unattributed:

    - ``cand:`` — outcomes since the lineage reached the trading path. Exact.
    - ``gen:``  — outcomes carrying (generation, rule hash). Also lineage-precise:
      a different generation of the same display name keys differently, which is
      what stops a replaced strategy from inheriting its predecessor's record.
    - ``sid:``  — imported history that carries nothing but the display name. It
      cannot be placed in a lineage because it never recorded one, so it attaches to
      whoever holds that name. This is the ONE imprecise join. It is confined to records
      that name no candidate and no (generation, rule hash): every new outcome of a minted
      lineage keys on ``cand:`` and can never be absorbed by a different lineage, while an
      entry installed without a candidate id (the history import's pool activation) keeps
      writing ``gen:`` rows, or ``sid:`` rows if it names no generation. Dropping it instead
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


# What a record names of the lineage it is about (PR3b-2, Thomas decision 36). A lifecycle decision
# carries these for the entry it judged, and the pool write refuses a decision whose display id now
# names another lineage: the pool can change between the cycle's read and the locked write.
LINEAGE_FIELDS = ("candidate_id", "strategy_generation_id", "strategy_rule_hash")


def lineage_of(record: Mapping[str, Any]) -> dict[str, str | None]:
    """The lineage ``record`` (a pool entry or a decision about one) names, each field None when it
    names none. A pool entry spells its generation ``generation_id``."""
    def named(value: Any) -> str | None:
        return value if isinstance(value, str) and value else None

    return {
        "candidate_id": named(record.get("candidate_id")),
        "strategy_generation_id": named(record.get("strategy_generation_id") or record.get("generation_id")),
        "strategy_rule_hash": named(record.get("strategy_rule_hash")),
    }

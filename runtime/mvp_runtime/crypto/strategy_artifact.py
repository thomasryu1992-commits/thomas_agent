"""The canonical strategy artifact — one hash for what a strategy IS, from candidate to live (PR3a).

A strategy exists as a candidate row (``strategy_candidates.jsonl``), inside a promotion approval,
and as a pool entry the router reads. The promotion door has always COPIED the candidate onto the
entry — the spec verbatim, the admission evidence through :func:`admission_evidence`, the score —
and nothing proved the copy stayed the thing Thomas approved. The approval named candidate ids
and rule hashes as two separately sorted lists; the rule hash covers the spec's behavioural subset
and nothing the router reads beside it. Measured 2026-09-18: all 15 occupying entries matched
their candidate rows field for field, by construction rather than by any check (investigation
``pr3-strategy-artifact-investigation.md`` §0).

The artifact is the part of a strategy that decides how it trades and what it was admitted on,
hashed once (Thomas decisions 31-34):

- **identity:** ``strategy_instance_id`` (the ``candidate_id``, decision 32), the generation, and
  the rule hash the entry is labelled with;
- **the spec**, as the rule fingerprint its rule hash is computed over
  (:func:`strategy.strategy_rule_fingerprint`): everything in it that decides how it trades, parsed,
  so a re-serialized spec is the same spec;
- **admission:** the regime evidence and distribution reference the two route-time doors read off
  the entry;
- **ranking:** ``champion_score``, the router's first key without realized evidence;
- **cost basis:** the cost model the backtest charged;
- **risk assumptions:** the backtest's own doors — the entry-cost cap, the liquidation guard's
  leverage and margin, the cooldown;
- **evidence:** the candle window's hash, the whole backtest evidence's hash, its closed count and
  win rate.

**The display id is not in it.** The door renames an entry whose display id collides
(``S004`` → ``S004-GEN-706``), and the same strategy is the same strategy under either name.

A pool entry carries the non-routing parts in :data:`ARTIFACT_FIELD` and the hash in
:data:`ARTIFACT_SHA256_FIELD`; everything the router reads stays where it has always been read.
:func:`assert_pool_artifacts` recomputes every stamped entry from the entry itself, so an entry
whose routing fields no longer hash to its stamp refuses the whole pool (decision 34), exactly as
a spec that does not parse does. An entry with no stamp predates this and papers as before; only a
stamped entry may be armed LIVE (decision 33).

**What the stamp proves, and what it does not.** It catches DRIFT: a writer, a restore or a code
path that changes what the router reads on a stamped entry. It is an unkeyed hash, so it does not
authenticate: whoever can write the pool file can recompute it, or strip it and leave an entry that
papers unbound. What authenticates money is the approval: a LIVE arm must be paired with its
artifact by the approval Thomas answered, and an edited or stripped entry is not.

**A later writer that touches any hashed field poisons the pool.** The pool's writers today are
the promotion door (which stamps), the history import's ``--activate-pool`` (which installs
unstamped entries), ``pool.apply_status_decisions`` (status and the ``lifecycle_*`` fields) and
``pool.disarm_live_tier`` (the ``live_tier*`` fields); none of those touches a hashed field, and
tests pin that. **A change to this module's hash format is such a writer too**, for every stamp
already on disk: each read recomputes with the code deployed then. So the v1 format depends only
on things that are already frozen — the rule fingerprint (every stored rule hash depends on it),
the candidate-id rule (every stored id does), and field names — and a golden test pins the result.
A later ``strategy_artifact.v2`` must keep writing the v1 stamp beside its own, or a rollback to
this code refuses the pool: this code does not know v2 and treats it as a stamp that does not hold.

**Not everything the runtime reads off an entry is hashed.** ``status``, the ``live_tier`` and, since
PR3c, ``candidate_identity.PREDECESSOR_KEYS_FIELD`` (the lineages an entry replaced, whose record the
router and the lifecycle read with its own) are facts about the pool at the moment a door wrote
them, not about the strategy, and the stamp does not cover them. Nothing writes the predecessors
before PR3c-2, whose promotion door names what an entry replaces in the approval instead (the
reactivation set of ``promotion.promotion_content_sha256``). An edit that drops them judges the entry
as a fresh install would; one that adds them can only tighten the controls that read them.

The hash is :func:`integrity.sha256_record` over canonical JSON. Floats are allowed (scores, R,
bps), so the two adapters must present them identically: both parse the spec through
:class:`StrategySpec` (which turns an integer condition value into a float), and a field an older
row does not carry is ``None`` on both sides.

A leaf: it imports the spec parser and the id rule, never the pool store, so ``pool`` can import
it for the load-time check and re-export :func:`admission_evidence`, which moved here for that
reason (the precedent is ``candidate_identity``).
"""

from __future__ import annotations

from typing import Any, Mapping

from runtime.read_only_kernel import integrity

from ..errors import ToolError
from .candidate_identity import candidate_id
from .strategy import StrategySpec, strategy_rule_fingerprint

STRATEGY_ARTIFACT_VERSION = "strategy_artifact.v1"

# On a pool entry: the hash, and the parts of the artifact the router does not read.
ARTIFACT_SHA256_FIELD = "strategy_artifact_sha256"
ARTIFACT_FIELD = "strategy_artifact"

# The artifact cannot be computed: a spec that does not parse, a value canonical JSON refuses
# (NaN, a secret-bearing key), or an entry that carries half a stamp.
STRATEGY_ARTIFACT_UNHASHABLE = "STRATEGY_ARTIFACT_UNHASHABLE"
# A stamped pool entry no longer hashes to its stamp. Refuses the pool (decision 34).
STRATEGY_POOL_ARTIFACT_MISMATCH = "STRATEGY_POOL_ARTIFACT_MISMATCH"

# The backtest doors whose parameters the evidence stands on, and the parameters. Counts of what a
# door refused are evidence, not assumptions, and stay in the evidence hash.
_RISK_DOORS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("entry_cost_door", ("applied", "max_entry_cost_r")),
    ("liquidation_guard", ("applied", "assumed_leverage", "maintenance_margin_rate")),
    ("cooldown_door", ("applied", "cooldown_bars")),
)


def admission_evidence(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """The two admission-door inputs a promotion lifts off a candidate's backtest.

    ``trade_plan.regime_admits`` and ``distribution_gate.distribution_admits`` read these off the
    POOL ENTRY at route time, and a candidate row does not carry them — they are projected
    out of ``backtest_evidence`` when the entry is built. Both doors fail OPEN on a missing
    reference, so anything that replays a bare candidate through the entry path measures a
    lineage with its gating switched off.

    That has now bitten twice in the same direction. #743 shipped the distribution gate
    without the promotion-side line and every routed entry passed it unmeasured. Then on
    2026-09-02 the signal probe replayed candidate rows to rank promotion picks and read
    S004-GEN-690 at 36 opens in 60 days — it installed at 11, because its own regime evidence
    excludes the regime it fires in most. The projection lives here, in one function both the
    promotion door and any pre-promotion replay call, so the two can no longer disagree about
    what a candidate becomes. It is also what the artifact hashes as the admission part, so the
    entry cannot carry a projection the approval did not name."""
    evidence = candidate.get("backtest_evidence") or {}
    return {
        "regime_evidence": ((evidence.get("regime_breakdown") or {}).get("per_regime")),
        "distribution_reference": evidence.get("distribution_reference"),
    }


def _risk_assumptions(evidence: Mapping[str, Any]) -> dict[str, Any]:
    assumptions: dict[str, Any] = {}
    for door, keys in _RISK_DOORS:
        record = evidence.get(door)
        assumptions[door] = {key: record.get(key) for key in keys} if isinstance(record, Mapping) else None
    return assumptions


def _evidence_summary(candidate: Mapping[str, Any]) -> dict[str, Any]:
    evidence = candidate.get("backtest_evidence")
    evidence = evidence if isinstance(evidence, Mapping) else None
    closed = evidence.get("closed_count") if evidence is not None else None
    wins = evidence.get("win_count") if evidence is not None else None
    counted = (isinstance(closed, int) and not isinstance(closed, bool) and closed > 0
               and isinstance(wins, int) and not isinstance(wins, bool))
    return {
        "evidence_input_sha256": candidate.get("evidence_input_sha256"),
        # The whole evidence by hash: the entry carries the summary, never the 6 KB it summarizes.
        "backtest_evidence_sha256": _record_sha(dict(evidence)) if evidence is not None else None,
        "closed_count": closed if counted else None,
        # Here, inside the artifact, and deliberately never as the entry's top-level
        # `backtest_win_rate`: the lifecycle reads that key, and giving it a value would switch on
        # the win-rate probation rule, which is a separate decision.
        "backtest_win_rate": wins / closed if counted else None,
    }


def carried_parts(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """What a pool entry carries in :data:`ARTIFACT_FIELD`: the artifact the router does not read.

    The cost basis, the risk assumptions and the evidence summary, under the artifact version. The
    promotion door writes exactly this; :func:`from_pool_entry` reads it back."""
    evidence = candidate.get("backtest_evidence")
    evidence = evidence if isinstance(evidence, Mapping) else {}
    summary = evidence.get("cost_summary")
    cost_model = summary.get("cost_model") if isinstance(summary, Mapping) else None
    return {
        "version": STRATEGY_ARTIFACT_VERSION,
        "cost_basis": dict(cost_model) if isinstance(cost_model, Mapping) else None,
        "risk_assumptions": _risk_assumptions(evidence),
        "evidence": _evidence_summary(candidate),
    }


def _spec_fingerprint(spec: Any) -> dict[str, Any]:
    """The spec as its rule fingerprint: parsed, so the bytes a writer chose do not matter, and
    frozen, because every stored rule hash is computed over it. Never ``StrategySpec.to_dict()``,
    which has no stability rule: #461 added ``venue`` to it unconditionally, and a change like that
    would move every stamp on disk at the next deploy."""
    if not isinstance(spec, Mapping):
        raise ToolError(STRATEGY_ARTIFACT_UNHASHABLE, "the strategy carries no spec")
    try:
        return strategy_rule_fingerprint(StrategySpec.from_dict(dict(spec)))
    except (ValueError, TypeError) as exc:
        raise ToolError(STRATEGY_ARTIFACT_UNHASHABLE, f"the spec does not parse: {exc}") from exc


def _body(
    *, instance_id: Any, generation_id: Any, rule_hash: Any, spec: Any,
    admission: Mapping[str, Any], champion_score: Any, carried: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "artifact_version": STRATEGY_ARTIFACT_VERSION,
        "strategy_instance_id": instance_id,
        "generation_id": generation_id,
        "strategy_rule_hash": rule_hash,
        "spec_fingerprint": _spec_fingerprint(spec),
        "admission": {
            "regime_evidence": admission.get("regime_evidence"),
            "distribution_reference": admission.get("distribution_reference"),
        },
        "ranking": {"champion_score": champion_score},
        "cost_basis": carried.get("cost_basis"),
        "risk_assumptions": carried.get("risk_assumptions"),
        "evidence": carried.get("evidence"),
    }


def from_candidate(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """The artifact of a candidate row, as the promotion door would install it."""
    return _body(
        instance_id=candidate_id(candidate),
        generation_id=candidate.get("generation_id"),
        rule_hash=candidate.get("strategy_rule_hash"),
        spec=candidate.get("strategy_spec"),
        admission=admission_evidence(candidate),
        champion_score=candidate.get("champion_score"),
        carried=carried_parts(candidate),
    )


def from_pool_entry(entry: Mapping[str, Any]) -> dict[str, Any]:
    """The artifact of a pool entry, read from the fields the router reads and the carried parts."""
    carried = entry.get(ARTIFACT_FIELD)
    if not isinstance(carried, Mapping):
        raise ToolError(STRATEGY_ARTIFACT_UNHASHABLE, "the entry carries no artifact")
    if carried.get("version") != STRATEGY_ARTIFACT_VERSION:
        raise ToolError(STRATEGY_ARTIFACT_UNHASHABLE,
                        f"the entry's artifact is version {carried.get('version')!r}, "
                        f"not {STRATEGY_ARTIFACT_VERSION}")
    return _body(
        instance_id=entry.get("candidate_id"),
        generation_id=entry.get("generation_id"),
        rule_hash=entry.get("strategy_rule_hash"),
        spec=entry.get("strategy_spec"),
        admission={"regime_evidence": entry.get("regime_evidence"),
                   "distribution_reference": entry.get("distribution_reference")},
        champion_score=entry.get("champion_score"),
        carried=carried,
    )


def _record_sha(value: Any) -> str:
    try:
        return integrity.sha256_record(value)
    except (ValueError, TypeError, RecursionError) as exc:
        raise ToolError(STRATEGY_ARTIFACT_UNHASHABLE,
                        f"the artifact cannot be hashed: {type(exc).__name__}: {exc}") from exc


def artifact_sha256(body: Mapping[str, Any]) -> str:
    """The artifact's hash. Raises ``STRATEGY_ARTIFACT_UNHASHABLE`` when canonical JSON refuses it."""
    return _record_sha(dict(body))


def candidate_artifact_sha256(candidate: Mapping[str, Any]) -> str:
    """The hash of the artifact the promotion door would install from ``candidate``."""
    return artifact_sha256(from_candidate(candidate))


def entry_is_bound(entry: Mapping[str, Any]) -> bool:
    """Whether a pool entry carries an artifact stamp. A stamp is checked at every pool read, so a
    stamped entry in a pool that loaded is one whose content hashes to it."""
    stamp = entry.get(ARTIFACT_SHA256_FIELD)
    return isinstance(stamp, str) and bool(stamp)


def entry_artifact_problem(entry: Mapping[str, Any]) -> str | None:
    """Why a pool entry's stamp does not hold, or None. An entry with neither the stamp nor the
    carried parts predates the artifact and has nothing to hold (decision 33)."""
    if ARTIFACT_SHA256_FIELD not in entry and ARTIFACT_FIELD not in entry:
        return None
    if not entry_is_bound(entry):
        return "it carries half an artifact stamp"
    try:
        recomputed = artifact_sha256(from_pool_entry(entry))
    except ToolError as exc:
        return exc.reason
    if recomputed != entry[ARTIFACT_SHA256_FIELD]:
        return "its content no longer hashes to its artifact stamp"
    return None


def assert_pool_artifacts(pool: Mapping[str, Any]) -> None:
    """Every stamped entry of ``pool`` hashes to its stamp, or the whole pool is refused.

    Decision 34, and the same rule a spec that does not parse already follows: a partially-trusted
    pool would silently change which strategies trade. Called at both doors, the read
    (``pool.load_active_pool``) and the install (``pool.install_active_pool``)."""
    for index, entry in enumerate(pool.get("active_strategies") or []):
        if not isinstance(entry, Mapping):
            continue  # the spec validation beside this refuses a malformed entry
        problem = entry_artifact_problem(entry)
        if problem is not None:
            raise ToolError(
                STRATEGY_POOL_ARTIFACT_MISMATCH,
                f"active_strategies[{index}] ({entry.get('strategy_id')}, "
                f"{entry.get('candidate_id')}): {problem}",
            )


__all__ = [
    "ARTIFACT_FIELD",
    "ARTIFACT_SHA256_FIELD",
    "STRATEGY_ARTIFACT_UNHASHABLE",
    "STRATEGY_ARTIFACT_VERSION",
    "STRATEGY_POOL_ARTIFACT_MISMATCH",
    "admission_evidence",
    "artifact_sha256",
    "assert_pool_artifacts",
    "candidate_artifact_sha256",
    "carried_parts",
    "entry_artifact_problem",
    "entry_is_bound",
    "from_candidate",
    "from_pool_entry",
]

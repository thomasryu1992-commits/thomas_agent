"""C7 strategy pool state — the active pool the cycle routes against, and the
imported-candidate store the C8 promotion flow will consume.

Two files under the crypto state directory:

- ``active_strategy_pool.json`` — the single pointer the runtime *reads*. The cycle
  only ever loads it; installing or changing it is an **operator door** (the import
  script's explicit ``--activate-pool``, and later C8's approval flow) — never a
  runtime side effect. A missing pool is honestly empty (no strategies, no entries);
  a malformed or spec-invalid pool raises so the cycle can refuse to route on
  tampered data rather than trade on whatever half-parses.
- ``strategy_candidates.jsonl`` — append-only candidates (C7 import provenance now,
  C8 factory output later). Candidates never route; only the active pool does.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from runtime.read_only_kernel import integrity

from ..errors import ToolError
from ..filelock import locked
from .paper import OCCUPYING_STATUSES, state_dir
from .strategy import SpecParseError, StrategySpec, load_strategy_pool

POOL_FILENAME = "active_strategy_pool.json"
CANDIDATES_FILENAME = "strategy_candidates.jsonl"


# --- candidate identity (single source) ----------------------------------------

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


# --- candidate lineage (fusion groundwork) --------------------------------------

# Closed set. ``seeded_template`` is fresh generation from the template library
# (no parents); the parented types name how a fused/derived child was produced.
# The factory ops that MINT parented candidates are a separate increment — the
# store admits them so the schema is one authority, not per-writer convention.
DERIVATION_TYPES = frozenset({"seeded_template", "crossover", "mutation"})
_PARENT_COUNT_RULES = {"seeded_template": (0, 0), "mutation": (1, 1), "crossover": (2, None)}


def validate_candidate_lineage(record: Mapping[str, Any], known_ids: frozenset[str]) -> None:
    """Fail-closed lineage check for one candidate row, at the append door.

    Rows written before lineage existed carry neither field and pass untouched
    (the ``candidate_id`` legacy rule — the append-only store is never rewritten).
    A row that does claim a derivation must be coherent: a known type, parents as
    a duplicate-free list of non-empty strings whose count fits the type (seeded
    has none, a mutation has exactly one, a crossover at least two), and every
    parent already durable in this store — so a child can never cite evidence
    that does not exist."""
    has_type = "derivation_type" in record
    has_parents = "parent_candidate_ids" in record
    if not has_type and not has_parents:
        return  # legacy row
    derivation = record.get("derivation_type")
    if not has_type:
        raise ToolError("CANDIDATE_LINEAGE_INVALID", "parent_candidate_ids without a derivation_type")
    if derivation not in DERIVATION_TYPES:
        raise ToolError("CANDIDATE_LINEAGE_INVALID", f"unknown derivation_type: {derivation!r}")
    parents = record.get("parent_candidate_ids", [])
    if not isinstance(parents, list) or not all(isinstance(p, str) and p for p in parents):
        raise ToolError("CANDIDATE_LINEAGE_INVALID", "parent_candidate_ids must be a list of non-empty ids")
    if len(set(parents)) != len(parents):
        raise ToolError("CANDIDATE_LINEAGE_INVALID", "duplicate parent_candidate_ids")
    lo, hi = _PARENT_COUNT_RULES[derivation]
    if len(parents) < lo or (hi is not None and len(parents) > hi):
        raise ToolError(
            "CANDIDATE_LINEAGE_INVALID",
            f"derivation_type {derivation!r} admits {lo}{'+' if hi is None else f'..{hi}'} parents, got {len(parents)}",
        )
    unknown = [p for p in parents if p not in known_ids]
    if unknown:
        raise ToolError("UNKNOWN_PARENT_CANDIDATE", f"parents not in the candidate store: {unknown}")


def resolve_candidates(selectors: list[str], root: Path | None = None) -> list[dict[str, Any]]:
    """Resolve operator selectors to candidate records, fail-closed.

    A selector is a ``candidate_id`` (exact) or a ``strategy_id`` (convenience). A
    strategy_id matching candidates from more than one lineage refuses with
    ``CANDIDATE_AMBIGUOUS`` — never silently the newest — and an unmatched selector
    refuses with ``UNKNOWN_CANDIDATE``. Returned records are stamped with their
    ``candidate_id``; re-appends of the same lineage collapse latest-wins."""
    by_cid: dict[str, dict[str, Any]] = {}
    for record in read_candidates(root):
        cid = candidate_id(record)
        by_cid[cid] = {**record, "candidate_id": cid}

    resolved: list[dict[str, Any]] = []
    missing: list[str] = []
    ambiguous: dict[str, list[str]] = {}
    for selector in selectors:
        if selector in by_cid:
            resolved.append(by_cid[selector])
            continue
        matches = [r for r in by_cid.values() if r.get("strategy_id") == selector]
        if not matches:
            missing.append(selector)
        elif len(matches) > 1:
            ambiguous[selector] = sorted(r["candidate_id"] for r in matches)
        else:
            resolved.append(matches[0])
    if missing:
        raise ToolError("UNKNOWN_CANDIDATE", f"unknown candidate selectors: {missing}")
    if ambiguous:
        raise ToolError(
            "CANDIDATE_AMBIGUOUS",
            f"strategy ids matching multiple lineages, use candidate ids: {ambiguous}",
        )
    seen: set[str] = set()
    for record in resolved:
        if record["candidate_id"] in seen:
            raise ToolError("DUPLICATE_SELECTOR", f"candidate selected twice: {record['candidate_id']}")
        seen.add(record["candidate_id"])
    return resolved


def pool_path(root: Path | None = None) -> Path:
    return state_dir(root) / POOL_FILENAME


def candidates_path(root: Path | None = None) -> Path:
    return state_dir(root) / CANDIDATES_FILENAME


def assert_pool_identity_unique(pool: Mapping[str, Any]) -> None:
    """No two active entries may share a ``strategy_id`` or a ``candidate_id``.

    Both are keys the runtime resolves by: ``strategy_id`` selects the champion and
    keys every lifecycle status update, ``candidate_id`` names the lineage an outcome
    is attributed to. A duplicate makes routing, demotion and attribution ambiguous —
    the pool would silently pick one entry and update the other. Fail-closed at both
    doors (install and read) so a duplicate can neither be written nor traded on."""
    seen_strategy: set[str] = set()
    seen_candidate: set[str] = set()
    for entry in pool.get("active_strategies") or []:
        strategy_id = entry.get("strategy_id")
        if isinstance(strategy_id, str) and strategy_id:
            if strategy_id in seen_strategy:
                raise ToolError("STRATEGY_POOL_DUPLICATE", f"duplicate strategy_id in the pool: {strategy_id}")
            seen_strategy.add(strategy_id)
        candidate_id = entry.get("candidate_id")
        if isinstance(candidate_id, str) and candidate_id:
            if candidate_id in seen_candidate:
                raise ToolError("STRATEGY_POOL_DUPLICATE", f"duplicate candidate_id in the pool: {candidate_id}")
            seen_candidate.add(candidate_id)


def load_active_pool(root: Path | None = None) -> dict[str, Any]:
    """The active pool, validated spec-by-spec and identity-unique. Missing = empty."""
    path = pool_path(root)
    if not path.is_file():
        return {"active_strategies": []}
    try:
        pool = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ToolError("STRATEGY_POOL_UNREADABLE", f"active strategy pool unreadable: {type(exc).__name__}") from exc
    try:
        load_strategy_pool(pool)  # fail-closed structural validation, one bad spec poisons
    except SpecParseError as exc:
        raise ToolError("STRATEGY_POOL_INVALID", f"active strategy pool failed validation: {exc}") from exc
    assert_pool_identity_unique(pool)
    return pool


def routable_contexts(pool: Mapping[str, Any]) -> list[tuple[str, str]]:
    """Distinct ``(primary symbol, timeframe)`` pairs the active pool can route on.

    One pair per occupying strategy's routing key — ``symbol_scope[0]`` and
    ``timeframe``, exactly what :func:`paper.route_entries` matches on — so a
    fan-out proposes a cycle for every context a strategy could fire in and none
    where it never could. Non-occupying or spec-less entries contribute nothing.
    Deduplicated and sorted for a stable, deterministic cycle order."""
    contexts: set[tuple[str, str]] = set()
    for entry in pool.get("active_strategies") or []:
        if entry.get("status") not in OCCUPYING_STATUSES or not entry.get("strategy_spec"):
            continue
        spec = StrategySpec.from_dict(entry["strategy_spec"])
        if spec.symbol_scope:
            contexts.add((str(spec.symbol_scope[0]), str(spec.timeframe)))
    return sorted(contexts)


def install_active_pool(pool: dict[str, Any], *, root: Path | None = None) -> int:
    """Install (replace) the active pool — the OPERATOR door, not a runtime call.

    Validates every spec and the identity invariant first (fail-closed), then writes
    atomically. Returns the number of strategies installed. Callers are operator
    scripts acting on an explicit confirmation (the pre-R10 promotion posture); the
    runtime cycle never calls this."""
    specs = load_strategy_pool(pool)
    assert_pool_identity_unique(pool)
    path = pool_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with locked(path.with_suffix(".lock"), code="STRATEGY_POOL_LOCKED", label="active strategy pool"):
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(pool, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)
    return len(specs)


def update_statuses(
    decisions: list[dict[str, Any]], *, root: Path | None = None, updated_by: str = "lifecycle_agent"
) -> int:
    """Apply lifecycle status transitions to the active pool (C10). Locked, guarded.

    The narrowest possible pool mutation: only ``status`` and the running
    ``lifecycle_consecutive_failures`` of named strategies change — specs, hashes,
    scores and membership are untouched, so this can never smuggle a promotion.
    Guards, each fail-closed: unknown strategy id refused; a CURRENTLY terminal
    entry is immutable (reactivation is the approval door, never this); and a
    transition record that isn't an evaluate_lifecycle decision shape is refused.
    Returns the number of entries whose status actually changed."""
    from .lifecycle import TERMINAL_STATUSES  # local: avoids a module cycle

    if not decisions:
        return 0
    path = pool_path(root)
    with locked(path.with_suffix(".lock"), code="STRATEGY_POOL_LOCKED", label="active strategy pool"):
        pool = load_active_pool(root)
        entries = {e.get("strategy_id"): e for e in pool.get("active_strategies") or []}
        changed = 0
        for decision in decisions:
            strategy_id = decision.get("strategy_id")
            new_status = decision.get("new_status")
            if not (isinstance(strategy_id, str) and strategy_id and isinstance(new_status, str)):
                raise ToolError("LIFECYCLE_DECISION_INVALID", "transition lacks strategy_id/new_status")
            entry = entries.get(strategy_id)
            if entry is None:
                raise ToolError("LIFECYCLE_UNKNOWN_STRATEGY", f"no pool entry for {strategy_id}")
            if str(entry.get("status")) in TERMINAL_STATUSES:
                raise ToolError(
                    "LIFECYCLE_TERMINAL_IMMUTABLE",
                    f"{strategy_id} is terminal; reactivation is the approval door, not a transition",
                )
            entry["lifecycle_consecutive_failures"] = int(decision.get("consecutive_failures") or 0)
            if new_status != entry.get("status"):
                entry["status"] = new_status
                entry["lifecycle_updated_at"] = decision.get("created_at_utc")
                entry["lifecycle_decision_id"] = decision.get("strategy_lifecycle_decision_id")
                changed += 1
        pool["updated_by"] = updated_by
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(pool, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)
        return changed


def read_candidates(root: Path | None = None) -> list[dict[str, Any]]:
    """All candidate rows, oldest first — a VERIFIED read.

    Any row carrying a ``record_sha256`` (everything :func:`append_candidates` has
    written since the store began stamping) must recompute it exactly; a mismatch
    raises ``CANDIDATES_TAMPERED`` so promotion asks/executions fail closed rather
    than binding Thomas's approval to silently edited evidence. Rows persisted
    before stamping existed have no hash to check — documented gap, closed for
    every new row."""
    path = candidates_path(root)
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ToolError("CANDIDATES_UNREADABLE", f"strategy candidates unreadable: {exc.strerror}") from exc
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError as exc:
            raise ToolError("CANDIDATES_UNREADABLE", f"strategy candidates line {i + 1} is not valid JSON") from exc
        if not isinstance(record, dict):
            continue
        stored = record.get("record_sha256")
        if stored is not None:
            body = {k: v for k, v in record.items() if k != "record_sha256"}
            if not isinstance(stored, str) or integrity.sha256_record(body) != stored:
                raise ToolError(
                    "CANDIDATES_TAMPERED", f"strategy candidates line {i + 1} fails its self-hash"
                )
        rows.append(record)
    return rows


def append_candidates(records: list[dict[str, Any]], *, root: Path | None = None) -> int:
    """Append candidate records (operator/import door). Returns the count written.

    The store stamps each row's ``record_sha256`` at append time (over the full row,
    import marks included), so tamper evidence starts the moment a row becomes
    durable — provenance-independent, unlike the outcomes store's build-time hash.

    Lineage is validated under the same lock, against the rows durable BEFORE this
    batch — a parent must already exist in the store, never in the batch that cites
    it (fusion reads its parents from the store first). All-or-nothing: one invalid
    row refuses the whole batch before anything is written."""
    path = candidates_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with locked(path.with_suffix(".lock"), code="CANDIDATES_LOCKED", label="strategy candidates"):
        known_ids = frozenset(candidate_id(r) for r in read_candidates(root))
        for record in records:
            validate_candidate_lineage(record, known_ids)
        with open(path, "a", encoding="utf-8", newline="\n") as handle:
            for record in records:
                row = dict(record)
                if "record_sha256" not in row:
                    row["record_sha256"] = integrity.sha256_record(row)
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(records)

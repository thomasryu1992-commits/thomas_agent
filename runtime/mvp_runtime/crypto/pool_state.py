"""The strategy pool's two files: their paths and reads, the pool's install door and the candidates'
append door (crypto PR7e-7).

Two files under the crypto state directory:

- ``active_strategy_pool.json`` — the single pointer the runtime *reads*. The cycle
  never installs or replaces it; that is an **operator door** (the import script's
  explicit ``--activate-pool``, and later C8's approval flow). A missing pool is honestly
  empty (no strategies, no entries); a malformed or spec-invalid pool raises so the cycle
  can refuse to route on tampered data rather than trade on whatever half-parses.
  Two narrower writers do run in the cycle, and they stay in :mod:`pool`: the status
  transitions (``pool.apply_status_decisions``) and the live-tier disarm. Each re-reads
  the pool under its lock and writes it back with only its own fields changed. Neither
  re-runs the install door's checks on what it writes.
- ``strategy_candidates.jsonl`` — append-only candidates (C7 import provenance now,
  C8 factory output later). Candidates never route; only the active pool does.

A read of the pool checks every spec and the identity invariant (:func:`assert_pool_identity_unique`),
and every artifact stamp too, except in the reader the disarm uses (:func:`read_pool_to_disarm`). The
install door checks all three before it writes. A read of the candidates verifies every stamped row's
self-hash, and the append door stamps each row and checks its lineage under the store's lock.

This was `pool`'s store until crypto PR7e-7. Every other role `pool` holds (the promotion door's
gates, the live tier, the status transitions, the backlog) reads it, so none of them could leave
`pool` while the store stayed there: `pool` re-exports them, and a module it imports cannot import it
back. `pool` re-exports every public name here as the same object, and its callers keep reading
`pool.<name>`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from runtime.read_only_kernel import integrity

from .. import jsonl
from ..errors import ToolError
from ..filelock import locked
from .candidate_identity import PREDECESSOR_KEYS_FIELD, candidate_id, is_lineage_key, own_attribution_keys
from .state import state_dir
from .strategy import SpecParseError, load_strategy_pool
from .strategy_artifact import assert_pool_artifacts

POOL_FILENAME = "active_strategy_pool.json"
CANDIDATES_FILENAME = "strategy_candidates.jsonl"

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


def pool_path(root: Path | None = None) -> Path:
    return state_dir(root) / POOL_FILENAME


def candidates_path(root: Path | None = None) -> Path:
    return state_dir(root) / CANDIDATES_FILENAME


def assert_pool_identity_unique(pool: Mapping[str, Any]) -> None:
    """No two active entries may share a ``strategy_id`` or a ``candidate_id``, and no lineage is
    inherited by two entries.

    Both are keys the runtime resolves by: ``strategy_id`` selects the champion and
    keys every lifecycle status update, ``candidate_id`` names the lineage an outcome
    is attributed to. A duplicate makes routing, demotion and attribution ambiguous —
    the pool would silently pick one entry and update the other. Fail-closed at both
    doors (install and read) so a duplicate can neither be written nor traded on.

    **Inherited lineages (PR3c, Thomas decision 41).** An entry's
    :data:`~candidate_identity.PREDECESSOR_KEYS_FIELD` must be a list of lineage keys, and a
    ``cand:`` or ``gen:`` key in it may name neither another entry's own lineage nor what another
    entry inherited: either way one lineage's record would judge two entries. ``sid:`` keys are
    display names, the one imprecise join, and stay out of the comparison. Two entries' OWN keys
    are not compared either: the pool holds a rule installed twice, S008 and S008-GEN-696 (both
    SUSPENDED, sharing ``gen:GEN-696:…``, left as they are by Thomas decision 42), and refusing
    that here would stop the pool. A rule is kept unique at the promotion door."""
    entries = [e for e in pool.get("active_strategies") or [] if isinstance(e, Mapping)]
    seen_strategy: set[str] = set()
    seen_candidate: set[str] = set()
    for entry in entries:
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

    # Entries are told apart by position, not display id: an entry need not carry one, and two that
    # do not must not read as one owner (review of PR3c-1).
    def named(index: int) -> str:
        return str(entries[index].get("strategy_id") or f"entry #{index}")

    holders: dict[str, set[int]] = {}
    for index, entry in enumerate(entries):
        for key in own_attribution_keys(entry):
            holders.setdefault(key, set()).add(index)
    inherited_by: dict[str, int] = {}
    for index, entry in enumerate(entries):
        keys = entry.get(PREDECESSOR_KEYS_FIELD)
        if keys is None:
            continue
        if not isinstance(keys, list) or not all(is_lineage_key(key) for key in keys):
            raise ToolError(
                "STRATEGY_POOL_INVALID",
                f"{named(index)}: {PREDECESSOR_KEYS_FIELD} is not a list of lineage keys",
            )
        for key in keys:
            if key.startswith("sid:"):
                continue
            others = sorted(holders.get(key, set()) - {index})
            if others:
                raise ToolError(
                    "STRATEGY_POOL_DUPLICATE",
                    f"{named(index)} inherits {key}, the lineage {named(others[0])} holds as its own",
                )
            if inherited_by.setdefault(key, index) != index:
                raise ToolError(
                    "STRATEGY_POOL_DUPLICATE",
                    f"{key} is inherited by both {named(inherited_by[key])} and {named(index)}",
                )


def _read_active_pool(root: Path | None, *, artifacts: bool) -> dict[str, Any]:
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
    if artifacts:
        assert_pool_artifacts(pool)
    return pool


def load_active_pool(root: Path | None = None) -> dict[str, Any]:
    """The active pool, validated spec-by-spec, identity-unique, and every stamped entry still its
    artifact (PR3a, decision 34). Missing = empty."""
    return _read_active_pool(root, artifacts=True)


def read_pool_to_disarm(root: Path | None = None) -> dict[str, Any]:
    """The active pool for the one door that may only narrow it: every check but the artifacts'.

    A pool whose stamp no longer holds routes nothing (decision 34), and an operator repairing it
    must be able to take an entry off the money path first — otherwise an entry still at LIVE is
    armed again the moment the repaired pool loads. Disarming writes only OBSERVATION and so needs
    no proof of what the entry is. Every other reader, and every other writer, reads through
    :func:`load_active_pool`."""
    return _read_active_pool(root, artifacts=False)


def install_active_pool(pool: dict[str, Any], *, root: Path | None = None) -> int:
    """Install (replace) the active pool — the OPERATOR door, not a runtime call.

    Validates every spec, the identity invariant and every artifact stamp first (fail-closed),
    then writes atomically: a pool the read door would refuse is never written. Returns the number
    of strategies installed. Callers are operator scripts acting on an explicit confirmation (the
    pre-R10 promotion posture); the runtime cycle never calls this."""
    specs = load_strategy_pool(pool)
    assert_pool_identity_unique(pool)
    assert_pool_artifacts(pool)
    path = pool_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with locked(path.with_suffix(".lock"), code="STRATEGY_POOL_LOCKED", label="active strategy pool"):
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(pool, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)
    return len(specs)


def read_candidates(root: Path | None = None) -> list[dict[str, Any]]:
    """All candidate rows, oldest first — a VERIFIED read.

    Any row carrying a ``record_sha256`` (everything :func:`append_candidates` has
    written since the store began stamping) must recompute it exactly; a mismatch
    raises ``CANDIDATES_TAMPERED`` so promotion asks/executions fail closed rather
    than binding Thomas's approval to silently edited evidence. Rows persisted
    before stamping existed have no hash to check — documented gap, closed for
    every new row."""
    path = candidates_path(root)
    rows: list[dict[str, Any]] = []
    # Streams rather than materializing the store twice, and keeps ToolError: 47 sites in the
    # runtime catch it by name — five of them in `promotion.py`, which reads this very store —
    # so raising jsonl's PersistenceError here would fail past those handlers, not at them.
    for lineno, record in jsonl.iter_numbered(
        path,
        read_code="CANDIDATES_UNREADABLE",
        label="strategy candidates",
        exc_type=ToolError,
    ):
        if not isinstance(record, dict):
            continue
        stored = record.get("record_sha256")
        if stored is not None:
            body = {k: v for k, v in record.items() if k != "record_sha256"}
            if not isinstance(stored, str) or integrity.sha256_record(body) != stored:
                raise ToolError(
                    "CANDIDATES_TAMPERED", f"strategy candidates line {lineno} fails its self-hash"
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

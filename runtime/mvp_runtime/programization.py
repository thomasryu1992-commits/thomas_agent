"""Programization repetition counter — the runtime leg of the Programization Review Policy.

The governance already models the whole loop (``PROGRAMIZATION_REVIEW_POLICY_V0.1``,
``PROGRAMIZATION_RUNTIME_RECORDS_V0.1``, closed schemas for observation/pattern records,
and the ``PROGRAMIZATION_REVIEW_TRIGGERED`` audit event type); this module makes the first
two records real. Each COMPLETED+PASS run may record one ``programization_observation.v0.1``
and fold it into its pattern's ``programization_pattern.v0.1`` counter. **Ten valid
independent observations trigger a Review opportunity only** — nothing here creates,
registers, or activates a Program (`ten_valid_repetitions_result:
PROGRAMIZATION_REVIEW_TRIGGER_ONLY`; activation stays APPROVAL_REQUIRED and unregistered
execution stays BLOCK). Candidate creation (``programization_candidate.v0.1``) is the
review's outcome, decided by Thomas — deliberately out of scope.

Counting is fail-closed in the not-counting direction: an observation is valid only when
every exclusion flag is provably false. Flags this runtime can detect are computed from the
store's own history (retry of the same task revision, duplicate trace replay, identical
input without evidence of an independent business event) or from the run itself (a provider
without network egress is a synthetic/mock run). Flags it cannot detect stay false with the
limitation documented: ``fixture`` never reaches the live pipeline, ``manual_smoke_test``
is indistinguishable from a real manual request here, and ``validation_revision_cycle`` /
``incomplete_task`` are structurally excluded (R7 has no revision cycles; only a
COMPLETED+PASS run reaches this code).

Like working memory, the store is **opt-in** at the pipeline level: no store, no
observation, run stays pure. State lives under the local gitignored
``.runtime_governance_state/`` (machine state, not shared source).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from runtime.read_only_kernel import integrity
from runtime.read_only_kernel.integrity import IntegrityError
from runtime.read_only_kernel.schema_validation import RuntimeSchemaError

from . import jsonl, schema_cache
from .errors import ProgramizationBlocked
from .events import stamped_event
from .filelock import locked
from .intake import TASK_SCHEMA_VERSION
from .paths import repo_root as _repo_root
from .worker import AGENT_OUTPUT_SCHEMA_VERSION, WORKER_VERSION

OBSERVATION_SCHEMA_VERSION = "programization_observation.v0.1"
PATTERN_SCHEMA_VERSION = "programization_pattern.v0.1"
CANDIDATE_SCHEMA_VERSION = "programization_candidate.v0.1"
REVIEW_TRIGGER_COUNT = 10               # policy §1: a review trigger, never auto-conversion
TASK_TYPE = "business_idea_analysis"    # the locked MVP use case
ENVIRONMENT_VERSION = f"mvp_runtime/{WORKER_VERSION}"

PROGRAMIZATION_REL = ".runtime_governance_state/programization"
OBSERVATIONS_FILE = "observations.jsonl"
PATTERNS_FILE = "patterns.jsonl"
CANDIDATES_FILE = "candidates.jsonl"
_LOCK = ".programization.lock"          # one lock: observe is a read-count-append critical section

# Review lifecycle (operator-only, explicit Thomas decision 2026-07-22): forward-only.
# NOT_TRIGGERED is the counter's own state (never operator-set), CLOSED is terminal —
# reopening a closed review would be a new Thomas decision, not a CLI transition.
_REVIEW_TRANSITIONS: dict[str, set[str]] = {
    "TRIGGERED": {"UNDER_REVIEW", "CLOSED"},
    "UNDER_REVIEW": {"CLOSED"},
}

REVIEW_EVENT_TYPE = "programization_review_event.v0"

# Candidate lifecycle (shadow-validation path; explicit Thomas decision 2026-07-22).
# Forward-only: ACCEPTED/REJECTED are terminal. The runtime never *runs* a shadow —
# Programs are unregistered and unregistered execution is BLOCK — it enforces and records
# the operator's limited comparison (policy §5): VALIDATING is entered before any outcome,
# the outcome needs a comparison reference + result, and ACCEPTED requires shadow PASS.
_CANDIDATE_TRANSITIONS: dict[str, dict[str, str]] = {
    # action -> {from_status: to_status}
    "ready": {"DRAFT": "REVIEW_READY"},
    "validate": {"REVIEW_READY": "VALIDATING"},
    "accept": {"VALIDATING": "ACCEPTED"},
    "reject": {"DRAFT": "REJECTED", "REVIEW_READY": "REJECTED", "VALIDATING": "REJECTED"},
}


class ProgramizationStore:
    """Append-only JSONL store of observations + pattern counters, rooted at a directory.

    ``observations.jsonl`` rows are ``{"input_sha256": ..., "record": <observation>}``:
    the record itself satisfies its closed schema; the sidecar input hash is store-internal
    index state the schema deliberately does not carry (needed to detect a replayed input).
    ``patterns.jsonl`` rows are schema-valid pattern records, latest-wins per
    ``pattern_id`` (the append-only equivalent of an update — the working-memory
    ``find_candidate`` precedent)."""

    def __init__(self, root: Path):
        self._root = Path(root)

    @classmethod
    def default(cls) -> "ProgramizationStore":
        return cls(_repo_root() / PROGRAMIZATION_REL)

    @property
    def root(self) -> Path:
        return self._root

    def lock(self):
        return locked(self._root / _LOCK,
                      code="PROGRAMIZATION_WRITE_FAILED", label="programization state")

    def read_observations(self) -> list[dict[str, Any]]:
        """Every stored observation wrapper row. Fail-closed on a corrupt store."""
        return jsonl.read_objects(self._root / OBSERVATIONS_FILE,
                                  read_code="PROGRAMIZATION_UNREADABLE", label="programization observations")

    def read_patterns(self) -> list[dict[str, Any]]:
        """Every stored pattern row (all versions). Fail-closed on a corrupt store."""
        return jsonl.read_objects(self._root / PATTERNS_FILE,
                                  read_code="PROGRAMIZATION_UNREADABLE", label="programization patterns")

    def latest_patterns(self) -> dict[str, dict[str, Any]]:
        """Current state per pattern_id — the last row wins (append-only update)."""
        latest: dict[str, dict[str, Any]] = {}
        for row in self.read_patterns():
            pattern_id = row.get("pattern_id") if isinstance(row, dict) else None
            if isinstance(pattern_id, str) and pattern_id:
                latest[pattern_id] = row
        return latest

    def append_observation(self, wrapper: Mapping[str, Any]) -> None:
        jsonl.append_lines(self._root / OBSERVATIONS_FILE, [wrapper],
                           write_code="PROGRAMIZATION_WRITE_FAILED", label="programization observations")

    def append_pattern(self, pattern: Mapping[str, Any]) -> None:
        jsonl.append_lines(self._root / PATTERNS_FILE, [pattern],
                           write_code="PROGRAMIZATION_WRITE_FAILED", label="programization patterns")

    def read_candidates(self) -> list[dict[str, Any]]:
        """Every stored program candidate row (all versions). Fail-closed on a corrupt store."""
        return jsonl.read_objects(self._root / CANDIDATES_FILE,
                                  read_code="PROGRAMIZATION_UNREADABLE", label="programization candidates")

    def latest_candidates(self) -> dict[str, dict[str, Any]]:
        """Current state per candidate_id — the last row wins (append-only update)."""
        latest: dict[str, dict[str, Any]] = {}
        for row in self.read_candidates():
            candidate_id = row.get("candidate_id") if isinstance(row, dict) else None
            if isinstance(candidate_id, str) and candidate_id:
                latest[candidate_id] = row
        return latest

    def append_candidate(self, candidate: Mapping[str, Any]) -> None:
        jsonl.append_lines(self._root / CANDIDATES_FILE, [candidate],
                           write_code="PROGRAMIZATION_WRITE_FAILED", label="programization candidates")


def build_pattern_signature(
    assignment: Mapping[str, Any],
    steps: list[str],
    *,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """The pattern signature (policy §2): same signature = same repeated-work pattern.

    Input/output contracts are the actual schema files' content hashes, the ordered step
    signature hashes the pipeline stages this run actually executed (a run with the
    independent validator or the controlled write is a materially different process —
    policy §4 — so it counts toward a different pattern), and the environment version pins
    the executing worker."""
    root = repo_root if repo_root is not None else _repo_root()
    return {
        "task_type": TASK_TYPE,
        "role_id": str(assignment.get("role_id") or ""),
        "input_schema_sha256": integrity.sha256_file(root / "schemas" / f"{TASK_SCHEMA_VERSION}.schema.json"),
        "ordered_step_signature_sha256": integrity.sha256_value({"ordered_steps": list(steps)}),
        "output_schema_sha256": integrity.sha256_file(root / "schemas" / f"{AGENT_OUTPUT_SCHEMA_VERSION}.schema.json"),
        "environment_version": ENVIRONMENT_VERSION,
    }


def _counting_flags(
    history: list[Mapping[str, Any]],
    *,
    task_id: str,
    task_revision: int,
    trace_id: str,
    input_sha256: str,
    synthetic: bool,
) -> dict[str, bool]:
    """The exclusion flags (policy §4) this runtime can honestly compute.

    ``same_input_without_independent_business_event`` is fail-closed: the runtime cannot
    verify an independent business event, so a byte-identical request seen before is never
    counted again."""
    records = [w.get("record", {}) for w in history if isinstance(w, dict)]
    return {
        "retry_of_same_task_revision": any(
            r.get("task_id") == task_id and r.get("task_revision") == task_revision for r in records
        ),
        "validation_revision_cycle": False,     # structurally excluded: R7 has no revision cycles
        "duplicate_replay": any(r.get("trace_id") == trace_id for r in records),
        "synthetic_test": synthetic,
        "fixture": False,                       # fixtures never reach the live pipeline
        "manual_smoke_test": False,             # not distinguishable from a real manual request
        "incomplete_task": False,               # only a COMPLETED+PASS run reaches this code
        "same_input_without_independent_business_event": any(
            w.get("input_sha256") == input_sha256 for w in history if isinstance(w, dict)
        ),
    }


def _validate(record: Mapping[str, Any], schema_version: str, label: str, root: Path) -> None:
    schema_path = root / "schemas" / f"{schema_version}.schema.json"
    try:
        schema_cache.validate_against_schema(record, schema_path, label)
    except RuntimeSchemaError as exc:
        raise ProgramizationBlocked("PROGRAMIZATION_RECORD_INVALID", str(exc)) from exc


def observe_completed_run(
    store: ProgramizationStore,
    *,
    task: Mapping[str, Any],
    assignment: Mapping[str, Any],
    steps: list[str],
    synthetic: bool,
    now: str,
    repo_root: Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any], bool]:
    """Record one observation for a COMPLETED+PASS run and update its pattern counter.

    Returns ``(observation, pattern, triggered_now)`` — ``triggered_now`` is True exactly
    once per pattern, on the observation that lifts ``valid_repetition_count`` to the
    trigger threshold while the pattern is still NOT_TRIGGERED. A pattern already
    TRIGGERED / UNDER_REVIEW / CLOSED keeps its status (those transitions belong to the
    operator review, not the counter). Both records are validated against their closed
    schemas before anything is persisted (fail-closed). The whole read-count-append runs
    under one cross-process lock so two concurrent runs cannot double-count or both claim
    the trigger."""
    root = repo_root if repo_root is not None else _repo_root()
    identity = task.get("identity", {}) if isinstance(task, Mapping) else {}
    task_id = str(identity.get("task_id") or "")
    trace_id = str(identity.get("trace_id") or "")
    task_revision = identity.get("task_revision")
    ccb = str(task.get("context", {}).get("core_context_binding_id") or "")
    raw_request = str(task.get("request", {}).get("raw_request") or "")
    if not (task_id and trace_id and ccb.startswith("ccb-")
            and isinstance(task_revision, int) and task_revision >= 1):
        raise ProgramizationBlocked(
            "OBSERVATION_INCOMPLETE",
            "observation requires task_id, task_revision>=1, trace_id, and a bound ccb",
        )

    signature = build_pattern_signature(assignment, steps, repo_root=root)
    input_sha256 = integrity.sha256_value({"raw_request": raw_request})
    observation_id = integrity.short_id(
        "progobs", {"task_id": task_id, "task_revision": task_revision, "trace_id": trace_id}
    )
    pattern_id = integrity.short_id("progpat", signature)

    with store.lock():
        history = store.read_observations()
        flags = _counting_flags(
            history, task_id=task_id, task_revision=task_revision,
            trace_id=trace_id, input_sha256=input_sha256, synthetic=synthetic,
        )
        valid = not any(flags.values())
        observation = {
            "schema_version": OBSERVATION_SCHEMA_VERSION,
            "observation_id": observation_id,
            "task_id": task_id,
            "task_revision": task_revision,
            "trace_id": trace_id,
            "core_context_binding_id": ccb,
            "pattern_signature": signature,
            "result_status": "COMPLETED",
            "validation_status": "PASS",
            "counting_flags": flags,
            "valid_for_programization_count": valid,
            "observed_at_utc": now,
        }
        _validate(observation, OBSERVATION_SCHEMA_VERSION, "programization_observation", root)

        latest = store.latest_patterns().get(pattern_id)
        prior_ids = list(latest.get("valid_observation_ids", [])) if latest else []
        prior_status = str(latest.get("review_status")) if latest else "NOT_TRIGGERED"
        ids = prior_ids + ([observation_id] if valid and observation_id not in prior_ids else [])
        count = len(ids)
        triggered_now = prior_status == "NOT_TRIGGERED" and count >= REVIEW_TRIGGER_COUNT
        status = "TRIGGERED" if triggered_now else prior_status
        pattern = {
            "schema_version": PATTERN_SCHEMA_VERSION,
            "pattern_id": pattern_id,
            "pattern_signature": signature,
            "valid_observation_ids": ids,
            "valid_repetition_count": count,
            "review_trigger_count": REVIEW_TRIGGER_COUNT,
            "review_status": status,
            "last_updated_at_utc": now,
        }
        _validate(pattern, PATTERN_SCHEMA_VERSION, "programization_pattern", root)

        store.append_observation({"input_sha256": input_sha256, "record": observation})
        # A pattern row is appended only when its state changed (a valid observation was
        # added, or the pattern is being established) — invalid observations must not
        # churn the latest-wins state with identical counters.
        if valid or latest is None:
            store.append_pattern(pattern)
    return observation, pattern, triggered_now


# --- Review handling (operator-only; explicit Thomas decision 2026-07-22) ----


def _require_operator(actor: str, reason: str) -> tuple[str, str]:
    """Review actions are explicit operator decisions: identity + reason, or refuse."""
    if not (isinstance(actor, str) and actor.strip()):
        raise ProgramizationBlocked("MISSING_OPERATOR", "a review action requires an operator identity")
    if not (isinstance(reason, str) and reason.strip()):
        raise ProgramizationBlocked("MISSING_REASON", "a review action requires an operator reason")
    return actor.strip(), reason.strip()


def transition_review(
    store: ProgramizationStore,
    pattern_id: str,
    to_status: str,
    *,
    reviewed_by: str,
    reason: str,
    now: str,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Move a pattern's review status forward (TRIGGERED → UNDER_REVIEW → CLOSED).

    Operator-only and forward-only: the counter owns NOT_TRIGGERED/TRIGGERED, the operator
    owns UNDER_REVIEW/CLOSED, and nothing reopens a CLOSED review. The new pattern row is
    schema-validated and appended latest-wins under the store lock (so a concurrent
    observation folds into the current state, never a stale one). Returns the new row."""
    root = repo_root if repo_root is not None else _repo_root()
    reviewed_by, reason = _require_operator(reviewed_by, reason)
    with store.lock():
        latest = store.latest_patterns().get(pattern_id)
        if latest is None:
            raise ProgramizationBlocked("PATTERN_NOT_FOUND", f"no pattern {pattern_id!r}")
        from_status = str(latest.get("review_status"))
        if to_status not in _REVIEW_TRANSITIONS.get(from_status, set()):
            raise ProgramizationBlocked(
                "INVALID_REVIEW_TRANSITION",
                f"review transition {from_status} -> {to_status} is not allowed",
            )
        pattern = {**latest, "review_status": to_status, "last_updated_at_utc": now}
        _validate(pattern, PATTERN_SCHEMA_VERSION, "programization_pattern", root)
        store.append_pattern(pattern)
    return pattern


def build_review_event(
    pattern: Mapping[str, Any],
    *,
    action: str,
    from_status: str,
    reviewed_by: str,
    reason: str,
    now: str,
    candidate_id: str | None = None,
) -> dict[str, Any]:
    """A tamper-evident standalone ledger event for one operator review action.

    The memory-retention precedent (`stamped_event` on its own ledger stream), not a
    task-bound audit event: a review action is an operator decision about accumulated
    state, anchored to no single task."""
    fields: dict[str, Any] = dict(
        action=action, pattern_id=str(pattern.get("pattern_id")),
        from_status=from_status, to_status=str(pattern.get("review_status")),
        valid_repetition_count=pattern.get("valid_repetition_count"),
        reviewed_by=reviewed_by, reason=reason, created_at=now,
    )
    if candidate_id is not None:
        fields["candidate_id"] = candidate_id
    return stamped_event(REVIEW_EVENT_TYPE, **fields)


_CANDIDATE_INPUT_LISTS = ("deterministic_slice", "agent_retained_responsibilities", "defined_exceptions")


def create_program_candidate(
    store: ProgramizationStore,
    pattern_id: str,
    review_input: Mapping[str, Any],
    *,
    created_by: str,
    reason: str,
    now: str,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Create the DRAFT ``programization_candidate.v0.1`` a review produces (policy §5).

    Candidate creation is ALLOW-tier (`tool_or_program_request_creation: ALLOW`) but it is
    the *review's* outcome, so it requires the pattern to be UNDER_REVIEW and it is an
    explicit operator action with identity + reason. The review substance —
    ``deterministic_slice``, ``agent_retained_responsibilities``, ``defined_exceptions``,
    ``rollback.procedure_ref``, optional baseline/candidate metrics — comes from
    ``review_input`` (authored by Thomas); the runtime contributes only identity, the
    pattern's count, and the schema's hard constants: ``activation_eligibility`` stays
    ``candidate_only_pending_program_registry_and_permission_policy`` and
    ``permission_expansion`` stays false — a candidate grants nothing
    (`candidate_status_does_not_grant_runtime_permission`). One candidate per pattern:
    revising or re-drafting is a later, separate decision. Fail-closed on a
    secret-bearing input and on the closed schema."""
    root = repo_root if repo_root is not None else _repo_root()
    created_by, reason = _require_operator(created_by, reason)
    if not isinstance(review_input, Mapping):
        raise ProgramizationBlocked("CANDIDATE_INPUT_INVALID", "review input must be a mapping")

    lists: dict[str, list[str]] = {}
    for key in _CANDIDATE_INPUT_LISTS:
        value = review_input.get(key)
        items = [x for x in value if isinstance(x, str) and x.strip()] if isinstance(value, list) else []
        if not items:
            raise ProgramizationBlocked(
                "CANDIDATE_INPUT_INVALID", f"review input requires a non-empty string list {key!r}")
        lists[key] = items
    rollback_ref = review_input.get("rollback_procedure_ref")
    if not (isinstance(rollback_ref, str) and rollback_ref.strip()):
        raise ProgramizationBlocked(
            "CANDIDATE_INPUT_INVALID", "review input requires rollback_procedure_ref (policy §5: rollback path)")

    def _metrics(key: str) -> dict[str, Any]:
        value = review_input.get(key)
        return dict(value) if isinstance(value, Mapping) else {}

    with store.lock():
        latest = store.latest_patterns().get(pattern_id)
        if latest is None:
            raise ProgramizationBlocked("PATTERN_NOT_FOUND", f"no pattern {pattern_id!r}")
        if latest.get("review_status") != "UNDER_REVIEW":
            raise ProgramizationBlocked(
                "CANDIDATE_REQUIRES_REVIEW",
                "a program candidate is the review's outcome — the pattern must be UNDER_REVIEW",
            )
        if any(c.get("pattern_id") == pattern_id for c in store.read_candidates()):
            raise ProgramizationBlocked(
                "CANDIDATE_EXISTS", f"pattern {pattern_id!r} already has a candidate")

        candidate = {
            "schema_version": CANDIDATE_SCHEMA_VERSION,
            "candidate_id": integrity.short_id(
                "progcand", {"pattern_id": pattern_id, "created_by": created_by, "created_at": now}),
            "pattern_id": pattern_id,
            "valid_repetition_count": latest.get("valid_repetition_count"),
            **lists,
            "baseline_metrics": _metrics("baseline_metrics"),
            "candidate_metrics": _metrics("candidate_metrics"),
            "shadow_validation": {"status": "NOT_STARTED", "comparison_ref": None, "result": None},
            "rollback": {"available": True, "procedure_ref": rollback_ref.strip()},
            "activation_eligibility": "candidate_only_pending_program_registry_and_permission_policy",
            "permission_expansion": False,
            "status": "DRAFT",
            "created_at_utc": now,
        }
        try:
            integrity.scan_for_secret_bearing_keys(candidate)
        except IntegrityError as exc:
            raise ProgramizationBlocked("SECRET_IN_CANDIDATE", str(exc)) from exc
        _validate(candidate, CANDIDATE_SCHEMA_VERSION, "programization_candidate", root)
        store.append_candidate(candidate)
    return candidate


# --- Candidate shadow-validation path (explicit Thomas decision 2026-07-22) --


def _require_candidate(store: ProgramizationStore, candidate_id: str) -> dict[str, Any]:
    latest = store.latest_candidates().get(candidate_id)
    if latest is None:
        raise ProgramizationBlocked("CANDIDATE_NOT_FOUND", f"no candidate {candidate_id!r}")
    return latest


def transition_candidate(
    store: ProgramizationStore,
    candidate_id: str,
    action: str,
    *,
    reviewed_by: str,
    reason: str,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Move a candidate forward: ready / validate / accept / reject.

    Forward-only; ACCEPTED and REJECTED are terminal. ``validate`` enters VALIDATING and
    marks the shadow comparison RUNNING; ``accept`` is refused unless the recorded shadow
    outcome is PASS (`ACCEPT_REQUIRES_SHADOW_PASS` — policy §5: measurable improvement via
    shadow/limited comparison, never acceptance by assertion). Acceptance changes review
    standing only: ``activation_eligibility`` and ``permission_expansion`` are schema
    constants, so an ACCEPTED candidate still grants nothing — registry and activation
    stay APPROVAL_REQUIRED and unreachable from here. Returns the new row."""
    root = repo_root if repo_root is not None else _repo_root()
    reviewed_by, reason = _require_operator(reviewed_by, reason)
    allowed = _CANDIDATE_TRANSITIONS.get(action)
    if allowed is None:
        raise ProgramizationBlocked("INVALID_CANDIDATE_TRANSITION", f"unknown candidate action {action!r}")
    with store.lock():
        latest = _require_candidate(store, candidate_id)
        from_status = str(latest.get("status"))
        to_status = allowed.get(from_status)
        if to_status is None:
            raise ProgramizationBlocked(
                "INVALID_CANDIDATE_TRANSITION",
                f"candidate action {action!r} is not allowed from status {from_status}",
            )
        shadow = dict(latest.get("shadow_validation", {}))
        if action == "validate":
            shadow = {"status": "RUNNING", "comparison_ref": None, "result": None}
        if action == "accept" and shadow.get("status") != "PASS":
            raise ProgramizationBlocked(
                "ACCEPT_REQUIRES_SHADOW_PASS",
                f"acceptance requires a recorded shadow PASS (shadow is {shadow.get('status')})",
            )
        candidate = {**latest, "status": to_status, "shadow_validation": shadow}
        _validate(candidate, CANDIDATE_SCHEMA_VERSION, "programization_candidate", root)
        store.append_candidate(candidate)
    return candidate


def record_shadow_result(
    store: ProgramizationStore,
    candidate_id: str,
    outcome: str,
    *,
    comparison_ref: str,
    result: str,
    reviewed_by: str,
    reason: str,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Record the operator's shadow/limited-comparison outcome (PASS or FAIL).

    The runtime never runs the shadow itself — Programs are unregistered and unregistered
    execution is BLOCK — so the outcome is an operator report, fail-closed on its evidence:
    the candidate must be VALIDATING with the shadow RUNNING (started by ``validate``, so
    an outcome cannot appear from nowhere), and a non-empty ``comparison_ref`` + ``result``
    are required. Recording an outcome leaves the candidate VALIDATING: acceptance or
    rejection is its own explicit decision. Secret-bearing evidence is refused."""
    root = repo_root if repo_root is not None else _repo_root()
    reviewed_by, reason = _require_operator(reviewed_by, reason)
    if outcome not in ("PASS", "FAIL"):
        raise ProgramizationBlocked("SHADOW_OUTCOME_INVALID", f"shadow outcome must be PASS or FAIL, got {outcome!r}")
    if not (isinstance(comparison_ref, str) and comparison_ref.strip()):
        raise ProgramizationBlocked("SHADOW_EVIDENCE_MISSING", "a shadow outcome requires a comparison_ref")
    if not (isinstance(result, str) and result.strip()):
        raise ProgramizationBlocked("SHADOW_EVIDENCE_MISSING", "a shadow outcome requires a result description")
    with store.lock():
        latest = _require_candidate(store, candidate_id)
        if latest.get("status") != "VALIDATING" or latest.get("shadow_validation", {}).get("status") != "RUNNING":
            raise ProgramizationBlocked(
                "SHADOW_NOT_RUNNING",
                "a shadow outcome can only be recorded while the candidate is VALIDATING "
                "with the shadow comparison RUNNING",
            )
        candidate = {
            **latest,
            "shadow_validation": {"status": outcome, "comparison_ref": comparison_ref.strip(),
                                  "result": result.strip()},
        }
        try:
            integrity.scan_for_secret_bearing_keys(candidate)
        except IntegrityError as exc:
            raise ProgramizationBlocked("SECRET_IN_CANDIDATE", str(exc)) from exc
        _validate(candidate, CANDIDATE_SCHEMA_VERSION, "programization_candidate", root)
        store.append_candidate(candidate)
    return candidate


def build_candidate_event(
    candidate: Mapping[str, Any],
    *,
    action: str,
    from_status: str,
    reviewed_by: str,
    reason: str,
    now: str,
) -> dict[str, Any]:
    """A tamper-evident standalone ledger event for one operator candidate action.

    Same stream and precedent as :func:`build_review_event`; the shadow status rides along
    so the ledger shows the evidence state each decision was made against."""
    return stamped_event(
        REVIEW_EVENT_TYPE,
        action=action,
        candidate_id=str(candidate.get("candidate_id")),
        pattern_id=str(candidate.get("pattern_id")),
        from_status=from_status, to_status=str(candidate.get("status")),
        shadow_status=str(candidate.get("shadow_validation", {}).get("status")),
        reviewed_by=reviewed_by, reason=reason, created_at=now,
    )

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
from runtime.read_only_kernel.schema_validation import RuntimeSchemaError

from . import jsonl, schema_cache
from .errors import ProgramizationBlocked
from .filelock import locked
from .intake import TASK_SCHEMA_VERSION
from .paths import repo_root as _repo_root
from .worker import AGENT_OUTPUT_SCHEMA_VERSION, WORKER_VERSION

OBSERVATION_SCHEMA_VERSION = "programization_observation.v0.1"
PATTERN_SCHEMA_VERSION = "programization_pattern.v0.1"
REVIEW_TRIGGER_COUNT = 10               # policy §1: a review trigger, never auto-conversion
TASK_TYPE = "business_idea_analysis"    # the locked MVP use case
ENVIRONMENT_VERSION = f"mvp_runtime/{WORKER_VERSION}"

PROGRAMIZATION_REL = ".runtime_governance_state/programization"
OBSERVATIONS_FILE = "observations.jsonl"
PATTERNS_FILE = "patterns.jsonl"
_LOCK = ".programization.lock"          # one lock: observe is a read-count-append critical section


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

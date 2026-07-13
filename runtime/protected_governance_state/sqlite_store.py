from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime.read_only_entry.authorization import (
    EntryAuthorizationError,
    validate_entry_authorization_semantics,
)
from runtime.read_only_kernel.integrity import (
    scan_for_secret_bearing_keys,
    sha256_record,
    sha256_value,
    short_id,
)

STORE_COMPONENT_ID = "thomas.protected_governance_state.sqlite_candidate"
STORE_COMPONENT_VERSION = "0.1.0"
TRANSITION_COMPONENT_ID = "thomas.runtime_entry.durable_cas.sqlite_candidate"
TRANSITION_COMPONENT_VERSION = "0.1.0"
STORE_SCHEMA_VERSION = "protected_governance_state.sqlite.v0.1"
RECORD_SCOPE = "SYNTHETIC_TEST_ONLY"


class ProtectedStateError(ValueError):
    pass


class ProtectedStateConflict(ProtectedStateError):
    pass


class SimulatedCrashBeforeCommit(RuntimeError):
    pass


class SimulatedCrashAfterCommit(RuntimeError):
    pass


@dataclass(frozen=True)
class StoreConfig:
    state_root: Path
    database_name: str = "runtime_entry_governance_state.sqlite3"
    record_scope: str = RECORD_SCOPE
    allow_test_writes: bool = False


class ProtectedGovernanceStateStore:
    """Disabled-by-default SQLite candidate used only by focused synthetic tests.

    The class intentionally refuses to open unless ``record_scope`` is
    ``SYNTHETIC_TEST_ONLY`` and ``allow_test_writes`` is explicitly true.
    It is not wired to the Runtime Entry Adapter or Kernel.
    """

    def __init__(self, config: StoreConfig):
        if config.record_scope != RECORD_SCOPE:
            raise ProtectedStateError("I0.5.4 store supports SYNTHETIC_TEST_ONLY scope only")
        if config.allow_test_writes is not True:
            raise ProtectedStateError("I0.5.4 protected-state writes are disabled outside explicit synthetic tests")
        raw_root = Path(config.state_root)
        if raw_root.exists() and raw_root.is_symlink():
            raise ProtectedStateError("state_root must not be a symlink")
        root = raw_root.resolve()
        root.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(root, 0o700)
        except OSError:
            pass
        database_name = config.database_name
        if not database_name or Path(database_name).name != database_name:
            raise ProtectedStateError("database_name must be one safe filename")
        if database_name in {".", ".."} or database_name.startswith("."):
            raise ProtectedStateError("hidden or traversal database names are forbidden")
        self.root = root
        raw_database_path = root / database_name
        if raw_database_path.exists() and raw_database_path.is_symlink():
            raise ProtectedStateError("database file must not be a symlink")
        self.path = raw_database_path.resolve()
        try:
            self.path.relative_to(root)
        except ValueError as exc:
            raise ProtectedStateError("database path must remain inside state_root") from exc
        self.store_id = short_id(
            "pgstore",
            {
                "component_id": STORE_COMPONENT_ID,
                "component_version": STORE_COMPONENT_VERSION,
                "state_root": root.as_posix(),
                "database_name": database_name,
                "record_scope": RECORD_SCOPE,
            },
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=5.0,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA trusted_schema = OFF")
        connection.execute("PRAGMA journal_mode = DELETE")
        connection.execute("PRAGMA synchronous = FULL")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def initialize(self, *, created_at: str) -> dict[str, Any]:
        _parse_time(created_at)
        with closing(self._connect()) as connection:
            connection.executescript(
                """
                    CREATE TABLE IF NOT EXISTS metadata (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS authorizations (
                        authorization_id TEXT PRIMARY KEY,
                        authorization_sha256 TEXT NOT NULL,
                        action_fingerprint_sha256 TEXT NOT NULL,
                        nonce_sha256 TEXT NOT NULL UNIQUE,
                        state TEXT NOT NULL CHECK(state IN (
                            'UNUSED',
                            'CONSUMED',
                            'CONSUMED_OR_UNKNOWN_FAIL_CLOSED'
                        )),
                        version INTEGER NOT NULL CHECK(version >= 0),
                        session_id TEXT UNIQUE,
                        expires_at TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        consumed_at TEXT,
                        FOREIGN KEY(session_id) REFERENCES sessions(session_id)
                    );
                    CREATE TABLE IF NOT EXISTS sessions (
                        session_id TEXT PRIMARY KEY,
                        authorization_id TEXT NOT NULL UNIQUE,
                        state TEXT NOT NULL CHECK(state IN (
                            'RESERVED',
                            'TERMINATED',
                            'UNKNOWN_FAIL_CLOSED'
                        )),
                        version INTEGER NOT NULL CHECK(version >= 0),
                        reserved_at TEXT NOT NULL,
                        terminated_at TEXT,
                        termination_result TEXT,
                        FOREIGN KEY(authorization_id) REFERENCES authorizations(authorization_id)
                    );
                    CREATE TABLE IF NOT EXISTS transition_receipts (
                        transition_id TEXT PRIMARY KEY,
                        authorization_id TEXT NOT NULL UNIQUE,
                        session_id TEXT NOT NULL UNIQUE,
                        result TEXT NOT NULL CHECK(result = 'COMMITTED_SYNTHETIC_TEST_ONLY'),
                        receipt_sha256 TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        FOREIGN KEY(authorization_id) REFERENCES authorizations(authorization_id),
                        FOREIGN KEY(session_id) REFERENCES sessions(session_id)
                    );
                    CREATE TABLE IF NOT EXISTS audit_events (
                        sequence_number INTEGER PRIMARY KEY AUTOINCREMENT,
                        event_id TEXT NOT NULL UNIQUE,
                        event_subtype TEXT NOT NULL,
                        authorization_id TEXT NOT NULL,
                        session_id TEXT NOT NULL,
                        previous_event_sha256 TEXT,
                        event_record_json TEXT NOT NULL,
                        event_sha256 TEXT NOT NULL UNIQUE,
                        created_at TEXT NOT NULL
                    );
                """
            )
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = dict(connection.execute("SELECT key, value FROM metadata").fetchall())
                expected = {
                    "schema_version": STORE_SCHEMA_VERSION,
                    "store_id": self.store_id,
                    "record_scope": RECORD_SCOPE,
                    "runtime_source_of_truth": "false",
                    "created_at": created_at,
                }
                if existing:
                    for key in ["schema_version", "store_id", "record_scope", "runtime_source_of_truth"]:
                        if existing.get(key) != expected[key]:
                            raise ProtectedStateError(f"protected-state metadata mismatch: {key}")
                else:
                    connection.executemany(
                        "INSERT INTO metadata(key, value) VALUES (?, ?)",
                        list(expected.items()),
                    )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        return self.snapshot(created_at=created_at)

    def register_synthetic_authorization(
        self,
        authorization: dict[str, Any],
        *,
        created_at: str,
    ) -> dict[str, Any]:
        _parse_time(created_at)
        _validate_synthetic_authorization(authorization)
        authorization_sha = authorization["integrity"]["record_sha256"]
        action_sha = authorization["action_fingerprint"]["sha256"]
        nonce_sha = authorization["one_time_boundary"]["nonce_sha256"]
        expires_at = authorization["resource_limits"]["expires_at"]
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    INSERT INTO authorizations(
                        authorization_id,
                        authorization_sha256,
                        action_fingerprint_sha256,
                        nonce_sha256,
                        state,
                        version,
                        session_id,
                        expires_at,
                        created_at,
                        consumed_at
                    ) VALUES (?, ?, ?, ?, 'UNUSED', 0, NULL, ?, ?, NULL)
                    """,
                    (
                        authorization["authorization_id"],
                        authorization_sha,
                        action_sha,
                        nonce_sha,
                        expires_at,
                        created_at,
                    ),
                )
                connection.execute("COMMIT")
            except sqlite3.IntegrityError as exc:
                connection.execute("ROLLBACK")
                raise ProtectedStateConflict("authorization or nonce already registered") from exc
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return self.snapshot(created_at=created_at)

    def attempt_atomic_transition(
        self,
        authorization: dict[str, Any],
        *,
        expected_authorization_version: int,
        transition_id: str,
        session_id: str,
        created_at: str,
        simulate_crash_before_commit: bool = False,
        simulate_crash_after_commit: bool = False,
    ) -> dict[str, Any]:
        try:
            receipt = self._commit_atomic_transition(
                authorization,
                expected_authorization_version=expected_authorization_version,
                transition_id=transition_id,
                session_id=session_id,
                created_at=created_at,
                simulate_crash_before_commit=simulate_crash_before_commit,
            )
        except SimulatedCrashBeforeCommit:
            return _blocked_transition_result(
                self,
                authorization,
                transition_id=transition_id,
                session_id=session_id,
                reason="SIMULATED_CRASH_BEFORE_COMMIT_ROLLED_BACK",
                created_at=created_at,
            )
        except (ProtectedStateError, ProtectedStateConflict, EntryAuthorizationError) as exc:
            return _blocked_transition_result(
                self,
                authorization,
                transition_id=transition_id,
                session_id=session_id,
                reason=_reason_code(exc),
                created_at=created_at,
            )
        if simulate_crash_after_commit:
            raise SimulatedCrashAfterCommit(
                "synthetic crash after durable commit and before any Kernel call"
            )
        return receipt

    def _commit_atomic_transition(
        self,
        authorization: dict[str, Any],
        *,
        expected_authorization_version: int,
        transition_id: str,
        session_id: str,
        created_at: str,
        simulate_crash_before_commit: bool,
    ) -> dict[str, Any]:
        _parse_time(created_at)
        _validate_synthetic_authorization(authorization)
        if not isinstance(expected_authorization_version, int) or isinstance(expected_authorization_version, bool) or expected_authorization_version < 0:
            raise ProtectedStateError("expected authorization version must be a non-negative integer")
        if not isinstance(transition_id, str) or not transition_id.startswith("transition_"):
            raise ProtectedStateError("transition_id must start with transition_")
        if not isinstance(session_id, str) or not session_id.startswith("session_"):
            raise ProtectedStateError("session_id must start with session_")
        if _parse_time(created_at) > _parse_time(authorization["resource_limits"]["expires_at"]):
            raise ProtectedStateConflict("authorization expired before transition")

        authorization_id = authorization["authorization_id"]
        authorization_sha = authorization["integrity"]["record_sha256"]
        action_sha = authorization["action_fingerprint"]["sha256"]
        nonce_sha = authorization["one_time_boundary"]["nonce_sha256"]

        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT * FROM authorizations WHERE authorization_id = ?",
                    (authorization_id,),
                ).fetchone()
                if row is None:
                    raise ProtectedStateConflict("authorization is not registered")
                if row["authorization_sha256"] != authorization_sha:
                    raise ProtectedStateConflict("authorization hash mismatch")
                if row["action_fingerprint_sha256"] != action_sha:
                    raise ProtectedStateConflict("action fingerprint mismatch")
                if row["nonce_sha256"] != nonce_sha:
                    raise ProtectedStateConflict("nonce hash mismatch")
                if row["state"] != "UNUSED":
                    raise ProtectedStateConflict("authorization is not UNUSED")
                if row["version"] != expected_authorization_version:
                    raise ProtectedStateConflict("authorization version compare-and-set conflict")
                if row["session_id"] is not None:
                    raise ProtectedStateConflict("authorization already has a session binding")
                if connection.execute(
                    "SELECT 1 FROM sessions WHERE session_id = ? OR authorization_id = ?",
                    (session_id, authorization_id),
                ).fetchone() is not None:
                    raise ProtectedStateConflict("session reservation already exists")
                if connection.execute(
                    "SELECT 1 FROM transition_receipts WHERE transition_id = ?",
                    (transition_id,),
                ).fetchone() is not None:
                    raise ProtectedStateConflict("transition_id already exists")

                before = _state_projection(connection, authorization_id)
                connection.execute(
                    """
                    INSERT INTO sessions(
                        session_id,
                        authorization_id,
                        state,
                        version,
                        reserved_at,
                        terminated_at,
                        termination_result
                    ) VALUES (?, ?, 'RESERVED', 0, ?, NULL, NULL)
                    """,
                    (session_id, authorization_id, created_at),
                )
                cursor = connection.execute(
                    """
                    UPDATE authorizations
                    SET state = 'CONSUMED',
                        version = version + 1,
                        session_id = ?,
                        consumed_at = ?
                    WHERE authorization_id = ?
                      AND state = 'UNUSED'
                      AND version = ?
                    """,
                    (session_id, created_at, authorization_id, expected_authorization_version),
                )
                if cursor.rowcount != 1:
                    raise ProtectedStateConflict("authorization compare-and-set updated zero rows")

                audit_events = []
                for event_type in [
                    "RUNTIME_ENTRY_AUTHORIZATION_CHECKED",
                    "AUTHORIZATION_CONSUMPTION_COMMITTED",
                    "RUNTIME_SESSION_RESERVED",
                ]:
                    audit_events.append(
                        _append_audit_event(
                            connection,
                            event_subtype=event_type,
                            authorization_record=authorization,
                            authorization_id=authorization_id,
                            session_id=session_id,
                            transition_id=transition_id,
                            created_at=created_at,
                        )
                    )

                after = _state_projection(connection, authorization_id)
                receipt = _committed_transition_result(
                    self,
                    authorization,
                    transition_id=transition_id,
                    session_id=session_id,
                    expected_version=expected_authorization_version,
                    before=before,
                    after=after,
                    audit_events=audit_events,
                    created_at=created_at,
                )
                connection.execute(
                    """
                    INSERT INTO transition_receipts(
                        transition_id,
                        authorization_id,
                        session_id,
                        result,
                        receipt_sha256,
                        created_at
                    ) VALUES (?, ?, ?, 'COMMITTED_SYNTHETIC_TEST_ONLY', ?, ?)
                    """,
                    (
                        transition_id,
                        authorization_id,
                        session_id,
                        receipt["integrity"]["result_sha256"],
                        created_at,
                    ),
                )
                if simulate_crash_before_commit:
                    raise SimulatedCrashBeforeCommit("synthetic crash before SQLite commit")
                connection.execute("COMMIT")
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
        return receipt

    def snapshot(self, *, created_at: str) -> dict[str, Any]:
        _parse_time(created_at)
        if not self.path.exists():
            raise ProtectedStateError("protected governance state database is not initialized")
        with closing(self._connect()) as connection:
            metadata = dict(connection.execute("SELECT key, value FROM metadata ORDER BY key").fetchall())
            authorizations = [dict(row) for row in connection.execute(
                """
                SELECT authorization_id, authorization_sha256, action_fingerprint_sha256,
                       nonce_sha256, state, version, session_id, expires_at, created_at, consumed_at
                FROM authorizations ORDER BY authorization_id
                """
            ).fetchall()]
            sessions = [dict(row) for row in connection.execute(
                """
                SELECT session_id, authorization_id, state, version, reserved_at,
                       terminated_at, termination_result
                FROM sessions ORDER BY session_id
                """
            ).fetchall()]
            receipts = [dict(row) for row in connection.execute(
                """
                SELECT transition_id, authorization_id, session_id, result, receipt_sha256, created_at
                FROM transition_receipts ORDER BY transition_id
                """
            ).fetchall()]
            audit_rows = connection.execute(
                """
                SELECT sequence_number, event_record_json
                FROM audit_events ORDER BY sequence_number
                """
            ).fetchall()
            audit = []
            for row in audit_rows:
                record = json.loads(row["event_record_json"])
                if record.get("lineage", {}).get("sequence_number") != row["sequence_number"]:
                    raise ProtectedStateError("stored Audit Event sequence does not match row sequence")
                audit.append(record)
            integrity_check = connection.execute("PRAGMA integrity_check").fetchone()[0]
        payload = {
            "schema_version": "protected_governance_state_snapshot_fingerprint_payload.v0.1",
            "store_id": metadata.get("store_id"),
            "authorizations": deepcopy(authorizations),
            "sessions": deepcopy(sessions),
            "receipts": deepcopy(receipts),
            "audit": deepcopy(audit),
            "created_at": created_at,
        }
        return {
            "schema_version": "protected_governance_state_snapshot.v0.1",
            "store_id": metadata.get("store_id"),
            "phase": "I0.5.4",
            "status": "SYNTHETIC_TEST_ONLY_STATE",
            "owner": "Thomas",
            "record_scope": RECORD_SCOPE,
            "runtime_source_of_truth": False,
            "backend": {
                "type": "SQLITE",
                "schema_version": metadata.get("schema_version"),
                "journal_mode": "DELETE",
                "synchronous": "FULL",
                "foreign_keys": True,
                "integrity_check": "PASS" if integrity_check == "ok" else "FAIL",
            },
            "authorizations": authorizations,
            "sessions": sessions,
            "transition_receipts": receipts,
            "audit_events": audit,
            "counts": {
                "authorizations": len(authorizations),
                "sessions": len(sessions),
                "transition_receipts": len(receipts),
                "audit_events": len(audit),
            },
            "runtime_effect": {
                "mode": "SYNTHETIC_TEST_ONLY_LOCAL_GOVERNANCE_STATE",
                "runtime_authoritative_state": False,
                "runtime_entry_enabled": False,
                "kernel_call_allowed": False,
                "domain_write_allowed": False,
                "workspace_write_allowed": False,
                "core_write_allowed": False,
                "external_write_allowed": False,
                "financial_write_allowed": False,
            },
            "integrity": {
                "hash_schema": "protected_governance_state_snapshot_fingerprint_payload.v0.1",
                "snapshot_fingerprint_payload": payload,
                "snapshot_sha256": sha256_value(payload),
            },
            "created_at": created_at,
        }


def _validate_synthetic_authorization(authorization: dict[str, Any]) -> None:
    scan_for_secret_bearing_keys(authorization)
    validate_entry_authorization_semantics(authorization)
    if authorization.get("record_scope") != RECORD_SCOPE:
        raise ProtectedStateError("I0.5.4 candidate accepts synthetic authorization fixtures only")
    if authorization.get("status") != "APPROVED_NOT_CONSUMED_REVIEW_ONLY":
        raise ProtectedStateError("synthetic authorization is not approved/not-consumed review evidence")
    approval = authorization.get("action_approval", {})
    if approval.get("approval_verified") is not True:
        raise ProtectedStateError("synthetic authorization approval evidence is not marked verified")
    if approval.get("consumption_state") != "UNUSED":
        raise ProtectedStateError("synthetic authorization is not UNUSED")
    if approval.get("current_contract_real_consumption_supported") is not False:
        raise ProtectedStateError("Approval v0.1 real consumption must remain unsupported")
    if authorization.get("decision", {}).get("usable_for_runtime_entry") is not False:
        raise ProtectedStateError("I0.5.3 authorization must remain unusable for Runtime entry")
    effect = authorization.get("runtime_effect", {})
    if any(value is not False for key, value in effect.items() if key != "mode"):
        raise ProtectedStateError("I0.5.3 authorization Runtime effects must remain false")


def _parse_time(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ProtectedStateError("timestamp must be a string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception as exc:
        raise ProtectedStateError(f"invalid RFC3339 timestamp: {value}") from exc
    if parsed.tzinfo is None:
        raise ProtectedStateError("timestamp must include timezone")
    return parsed.astimezone(timezone.utc)


def _reason_code(exc: Exception) -> str:
    text = str(exc).upper()
    mappings = [
        ("EXPIRED", "AUTHORIZATION_EXPIRED"),
        ("NOT UNUSED", "AUTHORIZATION_ALREADY_CONSUMED_OR_BLOCKED"),
        ("VERSION", "CAS_VERSION_CONFLICT"),
        ("NONCE", "NONCE_CONFLICT"),
        ("SESSION", "SESSION_RESERVATION_CONFLICT"),
        ("TRANSITION_ID", "TRANSITION_ID_CONFLICT"),
        ("HASH", "HASH_BINDING_MISMATCH"),
        ("NOT REGISTERED", "AUTHORIZATION_NOT_REGISTERED"),
        ("SYNTHETIC", "NON_SYNTHETIC_RECORD_BLOCKED"),
    ]
    for needle, code in mappings:
        if needle in text:
            return code
    return "PROTECTED_STATE_PRECONDITION_BLOCKED"


def _state_projection(connection: sqlite3.Connection, authorization_id: str) -> dict[str, Any]:
    authorization = connection.execute(
        """
        SELECT authorization_id, state, version, session_id, consumed_at
        FROM authorizations WHERE authorization_id = ?
        """,
        (authorization_id,),
    ).fetchone()
    sessions = connection.execute(
        """
        SELECT session_id, authorization_id, state, version, reserved_at,
               terminated_at, termination_result
        FROM sessions WHERE authorization_id = ? ORDER BY session_id
        """,
        (authorization_id,),
    ).fetchall()
    return {
        "authorization_state": dict(authorization) if authorization else None,
        "session_states": [dict(row) for row in sessions],
    }


def _append_audit_event(
    connection: sqlite3.Connection,
    *,
    event_subtype: str,
    authorization_record: dict[str, Any],
    authorization_id: str,
    session_id: str,
    transition_id: str,
    created_at: str,
) -> dict[str, Any]:
    previous_row = connection.execute(
        "SELECT event_id, event_sha256 FROM audit_events ORDER BY sequence_number DESC LIMIT 1"
    ).fetchone()
    previous_id = previous_row["event_id"] if previous_row else None
    previous_sha = previous_row["event_sha256"] if previous_row else None
    sequence = connection.execute(
        "SELECT COALESCE(MAX(sequence_number), 0) + 1 FROM audit_events"
    ).fetchone()[0]
    exact = authorization_record["exact_bindings"]
    task = exact["task"]
    core_binding = exact["core_context_binding"]
    authorization_sha = authorization_record["integrity"]["record_sha256"]
    subject_is_session = event_subtype == "RUNTIME_SESSION_RESERVED"
    subject_type = "runtime_entry_session" if subject_is_session else "runtime_entry_authorization"
    subject_id = session_id if subject_is_session else authorization_id
    subject_ref = (
        f"protected-state:session/{session_id}"
        if subject_is_session
        else f"protected-state:authorization/{authorization_id}"
    )
    seed = {
        "event_subtype": event_subtype,
        "authorization_id": authorization_id,
        "session_id": session_id,
        "transition_id": transition_id,
        "sequence_number": sequence,
        "created_at": created_at,
    }
    audit_event_id = short_id("audit", seed)
    actor_ref = f"component:{STORE_COMPONENT_ID}@{STORE_COMPONENT_VERSION}"
    event_summary = event_subtype.replace("_", " ").title()
    event_payload = {
        "schema_version": "audit_event_fingerprint_payload.v0.1",
        "audit_event_id": audit_event_id,
        "trace_id": f"trace_runtime_entry_{authorization_id}",
        "task_id": task["task_id"],
        "task_revision": task["task_revision"],
        "core_context_binding_id": core_binding["core_context_binding_id"],
        "event_type": "OTHER",
        "actor_ref": actor_ref,
        "subject_ref": subject_ref,
        "subject_fingerprint": authorization_sha,
        "event_summary": event_summary,
        "outcome": "RECORDED",
        "reason_codes": [event_subtype],
        "payload_sha256": authorization_record["action_fingerprint"]["sha256"],
        "evidence_refs": [f"authorization:{authorization_id}", f"transition:{transition_id}"],
        "related_record_refs": [f"session:{session_id}"],
        "parent_audit_event_ids": [previous_id] if previous_id else [],
        "previous_event_sha256": previous_sha,
        "sequence_number": sequence,
        "created_at": created_at,
    }
    event_sha = sha256_value(event_payload)
    record = {
        "schema_version": "audit_event.v0.1",
        "audit_event_id": audit_event_id,
        "trace_id": f"trace_runtime_entry_{authorization_id}",
        "task_id": task["task_id"],
        "task_revision": task["task_revision"],
        "core_context_binding_id": core_binding["core_context_binding_id"],
        "event_type": "OTHER",
        "actor": {
            "actor_type": "system",
            "actor_id": STORE_COMPONENT_ID,
            "role_id": None,
            "role_version": None,
            "assignment_id": None,
        },
        "subject": {
            "subject_type": subject_type,
            "subject_id": subject_id,
            "subject_ref": subject_ref,
            "subject_fingerprint": authorization_sha,
        },
        "event": {
            "event_summary": event_summary,
            "outcome": "RECORDED",
            "reason_codes": [event_subtype],
            "payload_ref": f"protected-state:transition/{transition_id}",
            "payload_sha256": authorization_record["action_fingerprint"]["sha256"],
            "evidence_refs": [f"authorization:{authorization_id}", f"transition:{transition_id}"],
            "related_record_refs": [f"session:{session_id}"],
        },
        "lineage": {
            "parent_audit_event_ids": [previous_id] if previous_id else [],
            "previous_event_sha256": previous_sha,
            "sequence_number": sequence,
        },
        "integrity": {
            "hash_schema": "audit_event_fingerprint_payload.v0.1",
            "event_fingerprint_payload": event_payload,
            "event_sha256": event_sha,
            "append_only": True,
            "overwrite_allowed": False,
            "delete_allowed": False,
        },
        "sensitivity": "INTERNAL",
        "runtime_effect": {
            "mode": "EVIDENCE_ONLY",
            "grants_permission": False,
            "grants_approval": False,
            "grants_authority": False,
            "grants_execution": False,
            "grants_activation": False,
            "mutates_runtime": False,
        },
        "created_at": created_at,
    }
    connection.execute(
        """
        INSERT INTO audit_events(
            sequence_number, event_id, event_subtype, authorization_id, session_id,
            previous_event_sha256, event_record_json, event_sha256, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            sequence,
            audit_event_id,
            event_subtype,
            authorization_id,
            session_id,
            previous_sha,
            json.dumps(record, sort_keys=True, separators=(",", ":")),
            event_sha,
            created_at,
        ),
    )
    return record


def _committed_transition_result(
    store: ProtectedGovernanceStateStore,
    authorization: dict[str, Any],
    *,
    transition_id: str,
    session_id: str,
    expected_version: int,
    before: dict[str, Any],
    after: dict[str, Any],
    audit_events: list[dict[str, Any]],
    created_at: str,
) -> dict[str, Any]:
    result_payload = {
        "schema_version": "runtime_entry_durable_transition_result_fingerprint_payload.v0.1",
        "transition_id": transition_id,
        "store_id": store.store_id,
        "authorization_id": authorization["authorization_id"],
        "authorization_sha256": authorization["integrity"]["record_sha256"],
        "session_id": session_id,
        "result": "COMMITTED_SYNTHETIC_TEST_ONLY",
        "before_state_sha256": sha256_record(before),
        "after_state_sha256": sha256_record(after),
        "created_at": created_at,
    }
    return {
        "schema_version": "runtime_entry_durable_transition_result.v0.1",
        "transition_id": transition_id,
        "phase": "I0.5.4",
        "status": "COMMITTED_SYNTHETIC_TEST_ONLY",
        "owner": "Thomas",
        "record_scope": RECORD_SCOPE,
        "runtime_source_of_truth": False,
        "store_binding": {
            "store_id": store.store_id,
            "component_id": STORE_COMPONENT_ID,
            "component_version": STORE_COMPONENT_VERSION,
            "transition_component_id": TRANSITION_COMPONENT_ID,
            "transition_component_version": TRANSITION_COMPONENT_VERSION,
            "backend": "SQLITE",
            "schema_version": STORE_SCHEMA_VERSION,
        },
        "authorization_state": {
            "authorization_id": authorization["authorization_id"],
            "authorization_sha256": authorization["integrity"]["record_sha256"],
            "action_fingerprint_sha256": authorization["action_fingerprint"]["sha256"],
            "nonce_sha256": authorization["one_time_boundary"]["nonce_sha256"],
            "expected_version": expected_version,
            "before_state": "UNUSED",
            "after_state": "CONSUMED",
            "real_action_approval_consumed": False,
            "synthetic_authorization_state_transitioned": True,
        },
        "session": {
            "session_id": session_id,
            "before_state": "NOT_RESERVED",
            "after_state": "RESERVED",
            "runtime_session_started": False,
            "kernel_called": False,
        },
        "compare_and_set": {
            "transaction_mode": "BEGIN_IMMEDIATE",
            "atomic_all_or_none": True,
            "authorization_rows_updated": 1,
            "session_rows_inserted": 1,
            "committed": True,
            "attempt_semantics": "AT_MOST_ONCE_ATTEMPT",
        },
        "durability": {
            "backend": "SQLITE",
            "journal_mode": "DELETE",
            "synchronous": "FULL",
            "foreign_keys": True,
            "process_restart_persistence_required": True,
            "reopen_verification_performed_by_this_result": False,
        },
        "state_projections": {
            "before_sha256": sha256_record(before),
            "after_sha256": sha256_record(after),
        },
        "audit": {
            "contract_ref": "docs/runtime-contracts/AUDIT_EVENT_CONTRACT_V0.1.md",
            "append_only": True,
            "hash_chain": True,
            "events": audit_events,
        },
        "decision": {
            "result": "COMMITTED_SYNTHETIC_TEST_ONLY",
            "blocking_reasons": [],
            "eligible_for_runtime_entry": False,
            "requires_recovery_inspection_before_any_future_action": True,
        },
        "runtime_effect": {
            "mode": "SYNTHETIC_TEST_ONLY_PROTECTED_STATE_WRITE",
            "test_only_local_governance_state_write_performed": True,
            "runtime_authoritative_state_write_performed": False,
            "real_action_approval_consumed": False,
            "runtime_session_started": False,
            "kernel_called": False,
            "model_invocation": False,
            "tool_execution": False,
            "program_execution": False,
            "network_access": False,
            "domain_write": False,
            "workspace_write": False,
            "core_write": False,
            "external_action": False,
            "financial_action": False,
        },
        "integrity": {
            "hash_schema": "runtime_entry_durable_transition_result_fingerprint_payload.v0.1",
            "result_fingerprint_payload": result_payload,
            "result_sha256": sha256_value(result_payload),
        },
        "created_at": created_at,
    }


def _blocked_transition_result(
    store: ProtectedGovernanceStateStore,
    authorization: dict[str, Any],
    *,
    transition_id: str,
    session_id: str,
    reason: str,
    created_at: str,
) -> dict[str, Any]:
    authorization_id = authorization.get("authorization_id") if isinstance(authorization, dict) else None
    authorization_sha = None
    if isinstance(authorization, dict):
        authorization_sha = authorization.get("integrity", {}).get("record_sha256")
    payload = {
        "schema_version": "runtime_entry_durable_transition_result_fingerprint_payload.v0.1",
        "transition_id": transition_id,
        "store_id": store.store_id,
        "authorization_id": authorization_id,
        "authorization_sha256": authorization_sha,
        "session_id": session_id,
        "result": "BLOCKED_FAIL_CLOSED",
        "blocking_reasons": [reason],
        "created_at": created_at,
    }
    return {
        "schema_version": "runtime_entry_durable_transition_result.v0.1",
        "transition_id": transition_id,
        "phase": "I0.5.4",
        "status": "BLOCKED_FAIL_CLOSED",
        "owner": "Thomas",
        "record_scope": RECORD_SCOPE,
        "runtime_source_of_truth": False,
        "store_binding": {
            "store_id": store.store_id,
            "component_id": STORE_COMPONENT_ID,
            "component_version": STORE_COMPONENT_VERSION,
            "transition_component_id": TRANSITION_COMPONENT_ID,
            "transition_component_version": TRANSITION_COMPONENT_VERSION,
            "backend": "SQLITE",
            "schema_version": STORE_SCHEMA_VERSION,
        },
        "authorization_state": {
            "authorization_id": authorization_id,
            "authorization_sha256": authorization_sha,
            "real_action_approval_consumed": False,
            "synthetic_authorization_state_transitioned": False,
        },
        "session": {
            "session_id": session_id,
            "runtime_session_started": False,
            "kernel_called": False,
        },
        "compare_and_set": {
            "transaction_mode": "BEGIN_IMMEDIATE",
            "atomic_all_or_none": True,
            "committed": False,
            "attempt_semantics": "AT_MOST_ONCE_ATTEMPT",
        },
        "durability": {
            "backend": "SQLITE",
            "journal_mode": "DELETE",
            "synchronous": "FULL",
            "foreign_keys": True,
            "process_restart_persistence_required": True,
        },
        "state_projections": None,
        "audit": {
            "contract_ref": "docs/runtime-contracts/AUDIT_EVENT_CONTRACT_V0.1.md",
            "append_only": True,
            "hash_chain": True,
            "events": [],
        },
        "decision": {
            "result": "BLOCKED_FAIL_CLOSED",
            "blocking_reasons": [reason],
            "eligible_for_runtime_entry": False,
            "requires_recovery_inspection_before_any_future_action": True,
        },
        "runtime_effect": {
            "mode": "SYNTHETIC_TEST_ONLY_PROTECTED_STATE_WRITE",
            "test_only_local_governance_state_write_performed": False,
            "runtime_authoritative_state_write_performed": False,
            "real_action_approval_consumed": False,
            "runtime_session_started": False,
            "kernel_called": False,
            "model_invocation": False,
            "tool_execution": False,
            "program_execution": False,
            "network_access": False,
            "domain_write": False,
            "workspace_write": False,
            "core_write": False,
            "external_action": False,
            "financial_action": False,
        },
        "integrity": {
            "hash_schema": "runtime_entry_durable_transition_result_fingerprint_payload.v0.1",
            "result_fingerprint_payload": payload,
            "result_sha256": sha256_value(payload),
        },
        "created_at": created_at,
    }


def validate_durable_transition_result_semantics(record: dict[str, Any]) -> None:
    scan_for_secret_bearing_keys(record)
    if record.get("schema_version") != "runtime_entry_durable_transition_result.v0.1":
        raise ProtectedStateError("durable transition result schema mismatch")
    if record.get("phase") != "I0.5.4" or record.get("owner") != "Thomas":
        raise ProtectedStateError("durable transition phase/owner mismatch")
    if record.get("record_scope") != RECORD_SCOPE or record.get("runtime_source_of_truth") is not False:
        raise ProtectedStateError("durable transition scope/source boundary mismatch")
    status = record.get("status")
    if status not in {"COMMITTED_SYNTHETIC_TEST_ONLY", "BLOCKED_FAIL_CLOSED"}:
        raise ProtectedStateError("durable transition status is invalid")
    store = record.get("store_binding", {})
    expected_store = {
        "component_id": STORE_COMPONENT_ID,
        "component_version": STORE_COMPONENT_VERSION,
        "transition_component_id": TRANSITION_COMPONENT_ID,
        "transition_component_version": TRANSITION_COMPONENT_VERSION,
        "backend": "SQLITE",
        "schema_version": STORE_SCHEMA_VERSION,
    }
    for key, expected in expected_store.items():
        if store.get(key) != expected:
            raise ProtectedStateError(f"durable transition store binding mismatch: {key}")
    if not isinstance(store.get("store_id"), str) or not store["store_id"].startswith("pgstore_"):
        raise ProtectedStateError("durable transition store_id is invalid")
    cas = record.get("compare_and_set", {})
    if cas.get("transaction_mode") != "BEGIN_IMMEDIATE" or cas.get("atomic_all_or_none") is not True or cas.get("attempt_semantics") != "AT_MOST_ONCE_ATTEMPT":
        raise ProtectedStateError("durable transition CAS boundary mismatch")
    durability = record.get("durability", {})
    for key, expected in {
        "backend": "SQLITE",
        "journal_mode": "DELETE",
        "synchronous": "FULL",
        "foreign_keys": True,
        "process_restart_persistence_required": True,
    }.items():
        if durability.get(key) != expected:
            raise ProtectedStateError(f"durability boundary mismatch: {key}")
    authorization = record.get("authorization_state", {})
    session = record.get("session", {})
    if authorization.get("real_action_approval_consumed") is not False:
        raise ProtectedStateError("I0.5.4 cannot consume a real Action Approval")
    if session.get("runtime_session_started") is not False or session.get("kernel_called") is not False:
        raise ProtectedStateError("I0.5.4 cannot start Runtime Session or call Kernel")
    decision = record.get("decision", {})
    if decision.get("result") != status or decision.get("eligible_for_runtime_entry") is not False or decision.get("requires_recovery_inspection_before_any_future_action") is not True:
        raise ProtectedStateError("durable transition decision mismatch")
    effect = record.get("runtime_effect", {})
    if effect.get("mode") != "SYNTHETIC_TEST_ONLY_PROTECTED_STATE_WRITE":
        raise ProtectedStateError("durable transition effect mode mismatch")
    prohibited = [
        "runtime_authoritative_state_write_performed",
        "real_action_approval_consumed",
        "runtime_session_started",
        "kernel_called",
        "model_invocation",
        "tool_execution",
        "program_execution",
        "network_access",
        "domain_write",
        "workspace_write",
        "core_write",
        "external_action",
        "financial_action",
    ]
    if any(effect.get(key) is not False for key in prohibited):
        raise ProtectedStateError("prohibited durable transition effect became true")
    committed = status == "COMMITTED_SYNTHETIC_TEST_ONLY"
    if effect.get("test_only_local_governance_state_write_performed") is not committed:
        raise ProtectedStateError("test-only state write flag does not match transition result")
    if cas.get("committed") is not committed:
        raise ProtectedStateError("CAS committed flag does not match transition result")
    if authorization.get("synthetic_authorization_state_transitioned") is not committed:
        raise ProtectedStateError("synthetic Authorization transition flag mismatch")
    if committed:
        if authorization.get("before_state") != "UNUSED" or authorization.get("after_state") != "CONSUMED":
            raise ProtectedStateError("committed Authorization state transition mismatch")
        if session.get("before_state") != "NOT_RESERVED" or session.get("after_state") != "RESERVED":
            raise ProtectedStateError("committed Session state transition mismatch")
        if cas.get("authorization_rows_updated") != 1 or cas.get("session_rows_inserted") != 1:
            raise ProtectedStateError("committed CAS row counts mismatch")
        if not isinstance(record.get("state_projections"), dict):
            raise ProtectedStateError("committed result requires state projection hashes")
        events = record.get("audit", {}).get("events", [])
        if [item.get("event", {}).get("reason_codes", [None])[0] for item in events] != [
            "RUNTIME_ENTRY_AUTHORIZATION_CHECKED",
            "AUTHORIZATION_CONSUMPTION_COMMITTED",
            "RUNTIME_SESSION_RESERVED",
        ]:
            raise ProtectedStateError("committed result Audit Event v0.1 subtype set mismatch")
        for item in events:
            if item.get("schema_version") != "audit_event.v0.1" or item.get("event_type") != "OTHER":
                raise ProtectedStateError("committed audit evidence must reuse Audit Event v0.1 with event_type=OTHER")
            integ = item.get("integrity", {})
            if integ.get("event_sha256") != sha256_value(integ.get("event_fingerprint_payload")):
                raise ProtectedStateError("committed Audit Event v0.1 fingerprint mismatch")
    else:
        if record.get("state_projections") is not None:
            raise ProtectedStateError("blocked result must not claim committed state projections")
        if record.get("audit", {}).get("events") != []:
            raise ProtectedStateError("blocked result must not claim committed audit events")
        blockers = decision.get("blocking_reasons")
        if not isinstance(blockers, list) or not blockers:
            raise ProtectedStateError("blocked result requires reason codes")
    integrity = record.get("integrity", {})
    payload = integrity.get("result_fingerprint_payload")
    if integrity.get("hash_schema") != "runtime_entry_durable_transition_result_fingerprint_payload.v0.1" or integrity.get("result_sha256") != sha256_value(payload):
        raise ProtectedStateError("durable transition result fingerprint mismatch")

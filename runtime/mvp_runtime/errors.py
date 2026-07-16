"""Fail-closed error types for the MVP runtime.

Every uncertain, missing, or invalid condition raises one of these instead of
guessing. A caught error is a BLOCK, never a silent default.
"""

from __future__ import annotations


class MvpRuntimeError(ValueError):
    """Base for all MVP runtime fail-closed errors.

    ``reason_code`` is a short stable slug (e.g. ``EMPTY_REQUEST``) suitable for
    audit records and user-facing BLOCK reasons; the message adds detail.
    """

    def __init__(self, reason_code: str, message: str):
        self.reason_code = reason_code
        self.reason = message
        super().__init__(f"{reason_code}: {message}")


class TaskIntakeBlocked(MvpRuntimeError):
    """Task Intake could not produce a valid RECEIVED task.v0.3 record."""


class PlannerBlocked(MvpRuntimeError):
    """Thomas Prime planning could not classify, route, or assign the task."""


class WorkerBlocked(MvpRuntimeError):
    """The specialist worker could not produce a valid Agent Output (provider error,
    timeout, budget exceeded, or an output that fails its contract)."""


class ProviderError(MvpRuntimeError):
    """A model provider failed (transport error, timeout, or a malformed response).

    Raised by provider adapters; the worker translates it into a fail-closed
    ``WorkerBlocked``."""


class AuditError(MvpRuntimeError):
    """The audit builder could not produce a valid, chained audit_event record."""


class SafetyGateBlocked(MvpRuntimeError):
    """The Safety-Flag Gate refused a network-capable capability (model/network).

    Raised when the local activation record is missing, malformed, tampered, expired,
    or does not enable the requested flags/provider — enabling a real model/network call
    requires an integrity-consistent, evidence-backed activation, never a bare env var."""


class PersistenceError(MvpRuntimeError):
    """The append-only runtime ledger could not be written or read.

    Raised by the store when a record/audit-event cannot be durably persisted or the
    existing ledger is unreadable/corrupt. A run whose evidence cannot be persisted is
    not delivered (no durable audit => no trust)."""


class ToolBlocked(MvpRuntimeError):
    """A read-only tool use could not complete (invalid query, tool error, timeout,
    rate limit, or a result outside the tool's read-only scope)."""


class ToolError(MvpRuntimeError):
    """A tool adapter failed (transport error, timeout, or malformed result).

    Raised by tool adapters; the executor translates it into a fail-closed ``ToolBlocked``."""


class OperatorBlocked(MvpRuntimeError):
    """The operator control channel refused an inbound message (R4).

    Raised when a message fails the canonical Control Channel identity rules — not the
    registered Thomas user id, not the registered private 1:1 chat, a group/channel, or a
    forwarded message. The MVP never runs a task for an unverified operator (fail-closed)."""


class MemoryBlocked(MvpRuntimeError):
    """A memory-candidate operation was refused (R5).

    Raised when candidate creation would violate the memory governance — e.g. a
    secret-bearing candidate (``secret_storage_in_memory_allowed: false``). Memory
    candidates are proposals only: they never carry runtime permission and never
    auto-promote to validated/core memory."""


class ControlBlocked(MvpRuntimeError):
    """An operator emergency-console command could not be applied (R4).

    Raised on an unknown command, a stop without a task id, or a failure to persist the
    control state. Fail-closed: the control state is a safety control, so an uncertain or
    unwritable state never silently permits execution."""


class SchedulerBlocked(MvpRuntimeError):
    """A scheduler operation was refused (R6).

    Raised on an invalid schedule (unknown kind, non-positive/too-small interval, missing
    request for a task schedule) or an unreadable schedule store. Scheduled execution is bound
    to the kill switch: while the runtime is PAUSED/KILLED, due schedules are skipped, never run."""

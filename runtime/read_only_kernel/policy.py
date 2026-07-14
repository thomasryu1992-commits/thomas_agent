from __future__ import annotations

from .errors import KernelBlocked
from .types import PolicySnapshot, PreflightContext


def adapt_policy(preflight: PreflightContext) -> PolicySnapshot:
    """Expose already-validated authority and permission records to orchestration.

    The active I0.5.5 replay still obtains its decision from the canonical Task and
    Assignment snapshots. This adapter deliberately does not redefine or expand
    policy; it only prevents the orchestrator from owning policy-shaped data.
    """

    permission = preflight.permission
    authority = preflight.authority
    if permission.get("evaluation_status") != "DECIDED":
        raise KernelBlocked(
            "PERMISSION_NOT_EXECUTABLE_READ_ONLY",
            "Read-only replay requires an already-decided Permission record.",
        )
    if authority.get("effective_permission_level") is None:
        raise KernelBlocked(
            "AUTHORITY_RECORD_INVALID",
            "Read-only replay requires an effective permission level.",
        )
    return PolicySnapshot(authority=authority, permission=permission)

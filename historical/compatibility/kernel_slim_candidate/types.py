from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class KernelContext:
    repo_root: str
    created_at: str
    input_bundle: Mapping[str, Any]


@dataclass(frozen=True)
class PreflightResult:
    allowed: bool
    blockers: tuple[str, ...]
    resolved: Mapping[str, Any]


@dataclass(frozen=True)
class PolicyDecision:
    disposition: str
    blockers: tuple[str, ...]
    approval_required: bool
    evidence: Mapping[str, Any]


@dataclass(frozen=True)
class RouteDecision:
    route_type: str
    actor_id: str
    role_id: str
    role_version: str
    evidence: Mapping[str, Any]

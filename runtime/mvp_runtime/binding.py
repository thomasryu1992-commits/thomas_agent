"""R2.2 Core Context Binding (planner step).

Binds a RECEIVED task to the active, approved Core Release in-process, reusing the
verified builder ``build_core_context_binding`` (current-pointer + activation-chain
verification, rule-subset check, deterministic id). Fails closed.

Activation is local per-environment state (see CLAUDE.md): the current pointer lives
at ``.runtime_governance_state/CURRENT_CORE_RELEASE.yaml`` by default, not in the
shared tree. If it is absent (e.g. a fresh checkout that has not activated locally),
binding fails closed with ``CORE_NOT_ACTIVATED``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .errors import PlannerBlocked
from .paths import repo_root as _repo_root

# The core-binding builder lives under scripts/ and uses ``from lib.X`` imports;
# the shared bridge puts scripts/ on sys.path exactly once for the whole runtime.
from . import _scripts_bridge  # noqa: F401

from create_core_context_binding import build_core_context_binding  # noqa: E402

DEFAULT_POINTER_REL = ".runtime_governance_state/CURRENT_CORE_RELEASE.yaml"


def bind_task_to_core(
    task: Mapping[str, Any],
    *,
    repo_root: Path | None = None,
    pointer_path: Path | None = None,
    now: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Bind a task to the active Core Release. Returns ``(binding, bound_task)``.

    Raises ``PlannerBlocked`` with ``CORE_NOT_ACTIVATED`` if no active local Core
    pointer exists, or ``BINDING_FAILED`` if the verified builder rejects the task
    (deactivated core, unknown rules, id mismatch, invalid identity, etc.).
    """
    root = repo_root if repo_root is not None else _repo_root()
    pointer = pointer_path if pointer_path is not None else (root / DEFAULT_POINTER_REL)
    if not pointer.is_file():
        raise PlannerBlocked(
            "CORE_NOT_ACTIVATED",
            f"active Core pointer not found at {pointer.as_posix()}; run local Core activation (see CLAUDE.md)",
        )
    try:
        binding, bound_task = build_core_context_binding(
            root, dict(task), pointer_path=pointer, now=now
        )
    except ValueError as exc:
        raise PlannerBlocked("BINDING_FAILED", str(exc)) from exc
    return binding, bound_task

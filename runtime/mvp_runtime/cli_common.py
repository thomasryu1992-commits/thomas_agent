"""Shared plumbing for the runtime's CLI entry points.

Six CLIs (intake, operator loop, emergency console, scheduler, memory, approval) each
carried their own copy of the exit codes, the Windows UTF-8 stdio fix, the fail-closed
BLOCKED reporter, and the Safety-Flag Gate authorization banners. Copies drift — the
worst case being a gated capability added to one CLI without its operator-visible
authorization notice. One construction site each, imported everywhere.
"""

from __future__ import annotations

import sys
from typing import Any

from .errors import MvpRuntimeError

EXIT_OK = 0
EXIT_BLOCKED = 2
EXIT_USAGE = 3


def force_utf8_io() -> None:
    """Reconfigure stdio to UTF-8 so non-ASCII I/O survives Windows cp949 consoles."""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except (ValueError, OSError):
                pass


def report_block(exc: MvpRuntimeError) -> int:
    """Report a fail-closed block on stderr and return the BLOCKED exit code."""
    sys.stderr.write(f"BLOCKED {exc.reason_code}: {exc.reason}\n")
    return EXIT_BLOCKED


def gate_banners(**implementations: Any) -> None:
    """Write the operator-visible SAFETY_GATE notice for each capable implementation selected.

    Keyed on the capability attributes every implementation already declares
    (``network_egress``, ``filesystem_write``, and ``model_id`` for a thing that invokes a
    model) rather than an isinstance ladder of concrete classes. The ladder reintroduced the
    exact failure this module exists to prevent — a newly added capable implementation
    silently printing no authorization notice until someone remembered to extend the list.

    The **kwarg names are free-form**, and that is the fix rather than a convenience. This
    used to take one named parameter per role (``channel``/``provider``/``search_tool``/
    ``writer``), which meant the "banners itself by construction" property held for exactly
    the four roles already thought of, and the fifth capability had to be remembered after
    all. It was not: the live order adapter — the only implementation in this repository that
    can move real money — printed nothing, and two more providers in ``operator_cli`` were
    hand-writing their own notices beside the call. Pass any capability under any name and it
    announces itself; the name becomes the label, so callers say what the thing is.

    An implementation declaring neither egress nor disk write is inert (every mock does) and
    stays silent, so a default run is quiet. ``None`` is skipped, so an unselected optional
    capability needs no guard at the call site.
    """
    for name, impl in implementations.items():
        if impl is None:
            continue
        flags: list[str] = []
        if getattr(impl, "network_egress", False):
            flags.append("network_access")
        if getattr(impl, "filesystem_write", False):
            flags.append("filesystem_write")
        if not flags:
            continue
        # Only once something capable is established: a mock provider declares a model_id too,
        # and must stay silent.
        model_id = getattr(impl, "model_id", None)
        if model_id is not None:
            flags.insert(0, "model_invocation")
        detail = f"{model_id}; " if model_id is not None else ""
        sys.stderr.write(
            f"SAFETY_GATE: {name.replace('_', ' ')} authorized ({detail}{', '.join(flags)})\n"
        )

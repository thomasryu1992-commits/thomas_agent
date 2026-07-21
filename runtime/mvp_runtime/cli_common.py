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


def gate_banners(*, channel: Any = None, provider: Any = None,
                 search_tool: Any = None, writer: Any = None) -> None:
    """Write the operator-visible SAFETY_GATE notice for each network/disk-capable
    implementation actually selected.

    Keyed on the capability attributes every implementation already declares
    (``network_egress`` for channels/providers/tools, ``filesystem_write`` for writers)
    rather than an isinstance ladder of concrete classes: the ladder reintroduced the
    exact failure this module exists to prevent — a newly added capable implementation
    silently printing no authorization notice until someone remembered to extend the
    list. An attribute-declared capability banners itself by construction; the mocks
    declare False and stay silent."""
    if getattr(channel, "network_egress", False):
        sys.stderr.write("SAFETY_GATE: network-capable operator channel authorized (network_access)\n")
    if getattr(provider, "network_egress", False):
        sys.stderr.write(
            f"SAFETY_GATE: network-capable provider authorized "
            f"({provider.model_id}; model_invocation, network_access)\n"
        )
    if getattr(search_tool, "network_egress", False):
        sys.stderr.write("SAFETY_GATE: network-capable search tool authorized (network_access)\n")
    if getattr(writer, "filesystem_write", False):
        sys.stderr.write("SAFETY_GATE: disk-writing workspace writer authorized (filesystem_write)\n")

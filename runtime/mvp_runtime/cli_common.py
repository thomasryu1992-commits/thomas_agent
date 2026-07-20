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
    implementation actually selected. Imports are function-local so a CLI that never
    passes a capability does not import its module (and no import cycles form)."""
    if channel is not None:
        from .operator import TelegramChannel
        if isinstance(channel, TelegramChannel):
            sys.stderr.write("SAFETY_GATE: network-capable operator channel authorized (network_access)\n")
    if provider is not None:
        from .providers import FailoverProvider, GoogleAIStudioProvider, GroqProvider
        if isinstance(provider, FailoverProvider):
            sys.stderr.write(
                f"SAFETY_GATE: network-capable provider failover chain authorized "
                f"({provider.model_id}; model_invocation, network_access per member)\n"
            )
        elif isinstance(provider, (GoogleAIStudioProvider, GroqProvider)):
            sys.stderr.write("SAFETY_GATE: network-capable provider authorized (model_invocation, network_access)\n")
    if search_tool is not None:
        from .tools import WebSearchTool
        if isinstance(search_tool, WebSearchTool):
            sys.stderr.write("SAFETY_GATE: network-capable search tool authorized (network_access)\n")
    if writer is not None:
        from .workspace import RealWorkspaceWriter
        if isinstance(writer, RealWorkspaceWriter):
            sys.stderr.write("SAFETY_GATE: disk-writing workspace writer authorized (filesystem_write)\n")

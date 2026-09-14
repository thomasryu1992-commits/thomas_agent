"""Tests for the versioned Hermes MCP shims (`integrations/hermes/mcp/`).

The shims import `mcp.server.fastmcp.FastMCP` at module top; the Thomas venv and CI have no
`mcp`. A stub with a no-op `tool()` decorator lets the render functions be imported and tested
without a socket and without the Hermes runtime. The stub is installed only when `mcp` is
absent, so a machine that has the real package tests against it.

The shim directory is put on `sys.path` here, once, because the shims import each other by
bare module name (`import thomas_door_client as door`) — that is how the Hermes gateway runs
them (`/opt/data/mcp/<shim>.py`), and the copy in this repository must run the same way.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

SHIM_DIR = Path(__file__).resolve().parents[2] / "integrations" / "hermes" / "mcp"
if str(SHIM_DIR) not in sys.path:
    sys.path.insert(0, str(SHIM_DIR))

if "mcp" not in sys.modules:
    class _FastMCP:
        def __init__(self, name: str):
            self.name = name
            self.tools: dict = {}

        def tool(self):
            def register(fn):
                self.tools[fn.__name__] = fn
                return fn
            return register

        def run(self):  # pragma: no cover
            raise RuntimeError("stub")

    mcp_mod = types.ModuleType("mcp")
    server_mod = types.ModuleType("mcp.server")
    fastmcp_mod = types.ModuleType("mcp.server.fastmcp")
    fastmcp_mod.FastMCP = _FastMCP
    server_mod.fastmcp = fastmcp_mod
    mcp_mod.server = server_mod
    sys.modules["mcp"] = mcp_mod
    sys.modules["mcp.server"] = server_mod
    sys.modules["mcp.server.fastmcp"] = fastmcp_mod

"""The core stays policy-thin while the domain packages grow — stated in CLAUDE.md, pinned here.

CLAUDE.md's "strong governance core, policy-thin deterministic runtime" stopped being a size
claim when `crypto/` outgrew the rest of the runtime (REMAINING_WORK.md §G re-measures this).
What keeps it true as a policy claim is an import-graph property, measured 2026-08-10 and made
a rule the same day:

- **The core import graph loads zero domain modules.** Core modules that dispatch into a lane
  (`scheduler.py`, `domain_console.py`) do it with function-local imports, so importing the
  core never pays for — or couples to — a lane.
- **A domain package appears at module level only in its own door modules.** Today that is
  `knowledge_bridge.py` / `knowledge_bridge_cli.py`, whose whole job is to front the knowledge
  package for the socket door. A new door widens the lane's surface into the core, which is a
  decision to record here — the same containment shape as `_GATE_ENV_VARS` in conftest and the
  env-only-gate caller test: the list is exact in both directions, so drift fails the suite
  either way.

These tests are AST- and subprocess-based on purpose: the suite itself imports domain modules
everywhere, so asserting on this process's ``sys.modules`` would measure pytest, not the core.
"""
from __future__ import annotations

import ast
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
CORE = ROOT / "runtime" / "mvp_runtime"

# Every subpackage of mvp_runtime is a domain package today; deriving the set means a new lane
# arrives covered instead of remembered.
DOMAIN_PACKAGES = sorted(
    d.name for d in CORE.iterdir() if d.is_dir() and d.name != "__pycache__"
)

# The doors: core modules allowed to import a domain package at module level, exactly.
EXPECTED_DOORS = {
    # The knowledge door for the Hermes socket — its module docstring is the reasoning.
    "knowledge_bridge.py",
    # The CLI entrypoint of that same door; nothing else imports either of them.
    "knowledge_bridge_cli.py",
}


def _module_level_domain_imports(path: pathlib.Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[str] = []
    for node in tree.body:  # module level only — function-local dispatch is the allowed shape
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                first = module.split(".")[0] if module else ""
                if first in DOMAIN_PACKAGES:
                    found.append(first)
                if not module:  # from . import x
                    found.extend(a.name for a in node.names if a.name in DOMAIN_PACKAGES)
            elif module.startswith("runtime.mvp_runtime."):
                parts = module.split(".")
                if len(parts) > 2 and parts[2] in DOMAIN_PACKAGES:
                    found.append(parts[2])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if len(parts) > 2 and parts[0] == "runtime" and parts[2] in DOMAIN_PACKAGES:
                    found.append(parts[2])
    return found


def test_domain_packages_enter_the_core_only_through_their_named_doors() -> None:
    assert DOMAIN_PACKAGES, "no domain packages found — the derivation broke, not the repo"
    doors = {
        p.name
        for p in sorted(CORE.glob("*.py"))
        if _module_level_domain_imports(p)
    }
    assert doors == EXPECTED_DOORS, (
        "module-level domain imports moved.\n"
        f"  new doors (a decision to record here, with reasoning): {sorted(doors - EXPECTED_DOORS)}\n"
        f"  doors that closed (update EXPECTED_DOORS): {sorted(EXPECTED_DOORS - doors)}\n"
        "Everywhere but a door, the core dispatches into a lane with a function-local import."
    )


def test_the_core_import_graph_loads_zero_domain_modules() -> None:
    # A fresh interpreter, because this process's sys.modules measures the test suite.
    code = (
        "import sys\n"
        "import runtime.mvp_runtime.pipeline, runtime.mvp_runtime.operator\n"
        "import runtime.mvp_runtime.permission, runtime.mvp_runtime.scheduler\n"
        "import runtime.mvp_runtime.audit, runtime.mvp_runtime.cli\n"
        "import runtime.mvp_runtime.worker\n"
        f"domains = {DOMAIN_PACKAGES!r}\n"
        "loaded = [m for m in sys.modules\n"
        "          if any(m.startswith(f'runtime.mvp_runtime.{d}') for d in domains)]\n"
        "print(','.join(loaded) if loaded else 'NONE')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT, capture_output=True, text=True, timeout=120, check=False,
    )
    assert proc.returncode == 0, f"core import failed:\n{proc.stderr}"
    assert proc.stdout.strip() == "NONE", (
        "importing the core pulled in domain modules: "
        f"{proc.stdout.strip()} — a module-level domain import escaped its door"
    )

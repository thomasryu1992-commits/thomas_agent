#!/usr/bin/env python3
from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
RUNTIME = ROOT / "runtime/read_only_kernel"
EXPECTED_MODULES = {
    "constants.py",
    "errors.py",
    "types.py",
    "loader.py",
    "preflight.py",
    "policy.py",
    "router.py",
    "worker_port.py",
    "validation.py",
    "audit.py",
    "assembler.py",
    "orchestrator.py",
    "kernel.py",
}
EXPECTED_FUNCTIONS = {
    "loader": {"load_runtime_inputs"},
    "preflight": {"run_preflight"},
    "policy": {"adapt_policy"},
    "router": {"select_route"},
    "worker_port": {"invoke_worker"},
    "validation": {"build_validation_result"},
    "audit": {"build_transition_audit", "build_validation_audit"},
    "assembler": {"assemble_completed_run", "build_blocked_run", "build_no_effects"},
    "orchestrator": {"run_loaded_replay"},
}


def module_function_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.as_posix())
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def main() -> int:
    missing = sorted(name for name in EXPECTED_MODULES if not (RUNTIME / name).exists())
    if missing:
        raise SystemExit("FAIL: missing active Kernel modules: " + ", ".join(missing))

    kernel_path = RUNTIME / "kernel.py"
    kernel_source = kernel_path.read_text(encoding="utf-8")
    kernel_tree = ast.parse(kernel_source, filename=kernel_path.as_posix())
    kernel_class = next(
        node
        for node in kernel_tree.body
        if isinstance(node, ast.ClassDef) and node.name == "ReadOnlyRuntimeKernel"
    )
    method_names = {
        node.name for node in kernel_class.body if isinstance(node, ast.FunctionDef)
    }
    if method_names != {"__init__", "run"}:
        raise SystemExit(
            "FAIL: active Kernel facade owns unexpected methods: "
            + ", ".join(sorted(method_names))
        )
    if len(kernel_source.splitlines()) > 120:
        raise SystemExit("FAIL: active Kernel facade is no longer thin")

    forbidden_kernel_ownership = (
        "execute_contract_inspection_worker",
        "validation_result.v0.1",
        "audit_event.v0.1",
        "REPLAY_TRANSITIONS =",
        "AUTHORITY_ORDER =",
    )
    for marker in forbidden_kernel_ownership:
        if marker in kernel_source:
            raise SystemExit(f"FAIL: kernel.py still owns decomposed responsibility: {marker}")

    for module_name, expected in EXPECTED_FUNCTIONS.items():
        actual = module_function_names(RUNTIME / f"{module_name}.py")
        missing_functions = expected.difference(actual)
        if missing_functions:
            raise SystemExit(
                f"FAIL: {module_name}.py missing functions: "
                + ", ".join(sorted(missing_functions))
            )
        importlib.import_module(f"runtime.read_only_kernel.{module_name}")

    from runtime.read_only_kernel.constants import KERNEL_ID, KERNEL_VERSION
    from runtime.read_only_kernel.kernel import KERNEL_ID as EXPORTED_KERNEL_ID
    from runtime.read_only_kernel.kernel import KERNEL_VERSION as EXPORTED_KERNEL_VERSION
    from runtime.read_only_kernel.kernel import ReadOnlyRuntimeKernel, run_bundle

    if EXPORTED_KERNEL_ID != KERNEL_ID or EXPORTED_KERNEL_VERSION != KERNEL_VERSION:
        raise SystemExit("FAIL: public Kernel constants changed during decomposition")
    if not callable(run_bundle) or ReadOnlyRuntimeKernel.__module__ != "runtime.read_only_kernel.kernel":
        raise SystemExit("FAIL: public Kernel entrypoints are not preserved")

    print("THOMAS_AGENT_ACTIVE_KERNEL_DECOMPOSITION: PASS")
    print("Public entrypoints preserved; loader/preflight/policy/router/worker/validation/audit/assembler/orchestrator separated.")
    print("This validation grants no Runtime activation, execution permission, Tool/Program enablement, or external effect.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

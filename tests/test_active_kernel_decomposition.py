from __future__ import annotations

import ast
import unittest
from pathlib import Path

from runtime.read_only_kernel.constants import KERNEL_ID, KERNEL_VERSION
from runtime.read_only_kernel.kernel import (
    KERNEL_ID as EXPORTED_KERNEL_ID,
    KERNEL_VERSION as EXPORTED_KERNEL_VERSION,
    ReadOnlyRuntimeKernel,
    run_bundle,
)
from runtime.read_only_kernel.types import ReadCounter


ROOT = Path(__file__).resolve().parents[1]


class ActiveKernelDecompositionTests(unittest.TestCase):
    def test_public_entrypoints_and_identity_are_preserved(self):
        self.assertIsNotNone(ReadOnlyRuntimeKernel)
        self.assertTrue(callable(run_bundle))
        self.assertEqual(EXPORTED_KERNEL_ID, KERNEL_ID)
        self.assertEqual(EXPORTED_KERNEL_VERSION, KERNEL_VERSION)

    def test_kernel_facade_owns_only_init_and_run(self):
        path = ROOT / "runtime/read_only_kernel/kernel.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        kernel = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "ReadOnlyRuntimeKernel"
        )
        methods = {
            node.name for node in kernel.body if isinstance(node, ast.FunctionDef)
        }
        self.assertEqual(methods, {"__init__", "run"})

    def test_read_counter_is_monotonic(self):
        counter = ReadCounter()
        counter.add(2)
        counter.add(3)
        self.assertEqual(counter.value, 5)
        with self.assertRaises(ValueError):
            counter.add(-1)


if __name__ == "__main__":
    unittest.main()

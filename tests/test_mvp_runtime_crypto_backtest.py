"""The replay backtest is its own module, and the factory still offers it under the old names (crypto
refactor plan PR-08; ``docs/proposals/CRYPTO_REFACTOR_AND_MODULARIZATION_PLAN_V0.1.md`` §L-4).

The replay moved out of ``factory`` whole. What it computes is pinned where it always was, in
``test_mvp_runtime_crypto_factory.py`` and ``test_mvp_runtime_crypto_holdout.py``, which still call it
through ``factory``. This file pins what the move itself has to keep true: every name the replay
defines is the same object on ``factory``, so a caller of either gets one function and one constant,
not two that can drift.

That the replay imports nothing from ``factory`` is pinned by the layer test's cycle rule: ``factory``
imports it, so the reverse import would be a cycle.
"""

from __future__ import annotations

import ast
from pathlib import Path

from runtime.mvp_runtime.crypto import backtest, factory


def _defined(tree: ast.Module) -> list[str]:
    """The names the module binds at top level itself: its functions, classes and constants."""
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            names.append(node.name)
        elif isinstance(node, ast.Assign):
            names.extend(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.append(node.target.id)
    return names


def test_factory_offers_every_name_the_replay_defines_as_the_same_object():
    names = _defined(ast.parse(Path(backtest.__file__).read_text(encoding="utf-8")))
    assert {"backtest_spec", "backtest_spec_pooled", "build_replay_frame", "ReplayFrame", "_replay",
            "holdout_split_index", "HOLDOUT_FRACTION", "UNSUPPLIABLE_FEATURE"} <= set(names), (
        "the scan lost the replay's own names: it broke, not the module")
    different = sorted(n for n in names if getattr(factory, n, None) is not getattr(backtest, n))
    assert different == [], f"factory holds a different object, or none, for: {different}"

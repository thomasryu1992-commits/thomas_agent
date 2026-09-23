"""The order's shape at the venue (`crypto/order_request.py`, crypto PR7e-4).

Two properties the split rests on, pinned where a later change would break them. `live_execution` is
still the order path's public face, so every name its readers and their tests use through it must be the
very object `order_request` defines. Then there is one definition, and the record capture's request log,
which finds its seams by identity, logs the calls through every module that binds them: a copy would
hide one module's callers from it. And `order_request` must stay pure. The order-path tripwire (`LIVE_ORDER_MODULES`) and the safety-gate roster
leave it out because it cannot reach a venue, so nothing else would notice if it started to.
"""

from __future__ import annotations

import ast
from pathlib import Path

from runtime.mvp_runtime.crypto import live_execution, order_request

_SOURCE = Path(order_request.__file__)
_REACHES_OUT = frozenset({"urllib", "http", "socket", "ssl", "hmac", "hashlib", "os", "requests", "subprocess",
                          "safety_gate", "live_execution", "account", "market_data"})


def _definitions() -> list[str]:
    tree = ast.parse(_SOURCE.read_text(encoding="utf-8"))
    return [node.name if isinstance(node, (ast.FunctionDef, ast.ClassDef)) else node.targets[0].id
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.ClassDef))
            or (isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name))]


def test_live_execution_re_exports_every_definition_as_the_same_object():
    names = _definitions()
    assert len(names) >= 28
    assert [n for n in names if getattr(live_execution, n, None) is not getattr(order_request, n)] == []


def test_the_order_shape_imports_nothing_that_could_reach_a_venue():
    imported: set[str] = set()
    for node in ast.walk(ast.parse(_SOURCE.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.update((node.module or "").split("."))
            imported.update(alias.name for alias in node.names)
    assert not imported & _REACHES_OUT, sorted(imported & _REACHES_OUT)

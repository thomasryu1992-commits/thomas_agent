"""The promotion backlog's module (`crypto/promotion_backlog.py`, crypto PR7e-11).

`pool` stays the pool's public face: the daily board and the tests read these names as `pool.<name>`.
So every public name here must be re-exported there as the very object this module defines. Then there
is one definition, and a same-named wrapper or copy in `pool` could not drift from it unnoticed.

Identity does not carry a patch from one module to the other: a patch on a name reaches only the code
that reads that module's name, and the functions here read this module's. The census for this move
found no test that patches, on `pool`, a name these functions read while they run. The private
`_lineage_key` stays off `pool`: nothing there calls it, so a patch on `pool` for it would reach nothing
and fails loudly instead.
"""

from __future__ import annotations

import ast
from pathlib import Path

from runtime.mvp_runtime.crypto import pool, promotion_backlog


def _definitions() -> list[str]:
    tree = ast.parse(Path(promotion_backlog.__file__).read_text(encoding="utf-8"))
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            names.append(node.name)
        elif isinstance(node, ast.Assign):
            names += [target.id for target in node.targets if isinstance(target, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.append(node.target.id)
    return names


def test_pool_re_exports_every_public_name_as_the_same_object():
    public = [name for name in _definitions() if not name.startswith("_")]
    assert sorted(public) == ["BACKLOG_REFUSAL_AXES", "MAX_DAYS_TO_LIFECYCLE_WINDOW",
                              "PROMOTION_BACKLOG_ALERT_THRESHOLD", "days_to_lifecycle_window",
                              "promotable_backlog"]
    assert [name for name in public if getattr(pool, name, None) is not getattr(promotion_backlog, name)] == []


def test_the_private_key_is_not_on_pool():
    assert [name for name in _definitions() if name.startswith("_")] == ["_lineage_key"]
    assert not hasattr(pool, "_lineage_key")

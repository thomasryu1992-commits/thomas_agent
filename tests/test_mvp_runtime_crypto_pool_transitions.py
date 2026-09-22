"""The status transitions' module (`crypto/pool_transitions.py`, crypto PR7e-9).

`pool` stays the pool's public face: the cycle, the retirement door and the tests read these names as
`pool.<name>`. So every public name here must be re-exported there as the very object this module
defines. Then there is one definition, and a same-named wrapper or copy in `pool` could not drift from
it unnoticed.

Identity does not carry a patch from one module to the other: a patch on a name reaches only the code
that reads that module's name, and the functions here read this module's. The private rule
`_stale_decision` stays off `pool`, so a patch on `pool` for it, which would reach nothing, fails
loudly instead. The census for this move found no test that patches, on `pool`, a name these functions
read while they run.
"""

from __future__ import annotations

import ast
from pathlib import Path

from runtime.mvp_runtime.crypto import pool, pool_transitions


def _definitions() -> list[str]:
    tree = ast.parse(Path(pool_transitions.__file__).read_text(encoding="utf-8"))
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            names.append(node.name)
        elif isinstance(node, ast.Assign):
            names += [target.id for target in node.targets if isinstance(target, ast.Name)]
    return names


def test_pool_re_exports_every_public_name_as_the_same_object():
    public = [name for name in _definitions() if not name.startswith("_")]
    assert sorted(public) == ["LIFECYCLE_DECISION_STALE", "apply_status_decisions", "update_statuses"]
    assert [name for name in public if getattr(pool, name, None) is not getattr(pool_transitions, name)] == []


def test_the_private_rule_is_not_on_pool():
    assert [name for name in _definitions() if name.startswith("_")] == ["_stale_decision"]
    assert not hasattr(pool, "_stale_decision")

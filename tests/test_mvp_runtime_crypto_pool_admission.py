"""The promotion door's gates (`crypto/pool_admission.py`, crypto PR7e-10).

`pool` stays the pool's public face: the promotion door's roster, the cycle, the dashboard, the scripts
and the tests read these names as `pool.<name>` (`promotion_backlog` imports the promotable sets and the
lifecycle window from here directly). So every public name here must be re-exported there as the very
object this module defines. Then there is one definition, and a same-named wrapper or copy in
`pool` could not drift from it unnoticed.

Identity does not carry a patch from one module to the other: a patch on a name reaches only the code
that reads that module's name. The suite's patches on the tier doors and on `reactivated_candidate_ids`
are made on `pool` and meant for the promotion door, which reads them through `pool`, so they still
reach it. The one private helper, `_observation_holdout_term`, stays off `pool`: nothing there calls it,
and a patch on `pool` for it would miss the entry bar, so such a patch fails loudly instead.
"""

from __future__ import annotations

import ast
from pathlib import Path

from runtime.mvp_runtime.crypto import pool, pool_admission


def _definitions() -> list[str]:
    tree = ast.parse(Path(pool_admission.__file__).read_text(encoding="utf-8"))
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
    assert len(public) == 34
    assert [name for name in public if getattr(pool, name, None) is not getattr(pool_admission, name)] == []


def test_the_private_helper_is_not_on_pool():
    assert [name for name in _definitions() if name.startswith("_")] == ["_observation_holdout_term"]
    assert not hasattr(pool, "_observation_holdout_term")

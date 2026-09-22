"""The live tier's module (`crypto/live_tier.py`, crypto PR7e-8).

`pool` stays the pool's public face: the promotion door, the live route, the readiness report, the
scripts and the tests read these names as `pool.<name>`. So every public name here must be re-exported
there as the very object this module defines, and so must `_spec_rule_hash`, the one private helper
`pool` calls (in `rule_hashes_of`, which the rule-not-routed gate and `replaced_entries` stand on). Then
there is one definition, and a same-named wrapper or copy in `pool` could not drift from it unnoticed.

Identity does not carry a patch from one module to the other: a patch on a name reaches only the code
that reads that module's name. One test patched the tier's verdict on `pool` and reached two readers,
the readiness board (`pool.live_arm_unsound`) and `live_arm_approvals`, which reads this module's name
since the move. The PR7e-8 census found that one, and the test patches both names now.
"""

from __future__ import annotations

import ast
from pathlib import Path

from runtime.mvp_runtime.crypto import live_tier, pool


def _definitions() -> list[str]:
    tree = ast.parse(Path(live_tier.__file__).read_text(encoding="utf-8"))
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            names.append(node.name)
        elif isinstance(node, ast.Assign):
            names += [target.id for target in node.targets if isinstance(target, ast.Name)]
    return names


def test_pool_re_exports_every_name_as_the_same_object():
    names = _definitions()
    assert len(names) == 12
    assert [name for name in names if name.startswith("_")] == ["_spec_rule_hash"]
    assert [name for name in names if getattr(pool, name, None) is not getattr(live_tier, name)] == []

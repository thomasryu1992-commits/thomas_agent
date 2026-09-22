"""The strategy pool's two files (`crypto/pool_state.py`, crypto PR7e-7).

`pool` stays the pool's public face: its callers, and the code that stayed in it, read these names as
`pool.<name>`. So every public name here must be re-exported there as the very object this module
defines. Then there is one definition, and a same-named wrapper or copy in `pool` could not drift from
it unnoticed.

Identity does not carry a patch from one module to the other: a patch on a name reaches only the code
that reads that module's name. The suite patches the store on `pool`: `load_active_pool` 83 times in
79 tests (from 8 source sites, most of them through one `live_route` test helper), `read_candidates` 13
times and `install_active_pool` 4 times. Each of those patches is meant for code that reads through
`pool`: the callers outside it, and the code that stayed in it. Of the code that moved here,
only `append_candidates` reads a patched name (`read_candidates`), and no test calls it while that
patch is active (the PR7e-7 census). The private names stay off `pool`, so a patch on `pool` for one of
them, which would reach nothing, fails loudly instead.
"""

from __future__ import annotations

import ast
from pathlib import Path

from runtime.mvp_runtime.crypto import pool, pool_state


def _definitions() -> list[str]:
    tree = ast.parse(Path(pool_state.__file__).read_text(encoding="utf-8"))
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            names.append(node.name)
        elif isinstance(node, ast.Assign):
            names += [target.id for target in node.targets if isinstance(target, ast.Name)]
    return names


def test_pool_re_exports_every_public_name_as_the_same_object():
    public = [name for name in _definitions() if not name.startswith("_")]
    assert len(public) == 12
    assert [name for name in public if getattr(pool, name, None) is not getattr(pool_state, name)] == []


def test_the_private_names_are_not_on_pool():
    private = [name for name in _definitions() if name.startswith("_")]
    assert sorted(private) == ["_PARENT_COUNT_RULES", "_read_active_pool"]
    assert [name for name in private if hasattr(pool, name)] == []

"""One context's market inputs (`crypto/feed_assembly.py`, crypto PR7e-2 and PR7e-5).

The scheduler's factory dispatches (`crypto_cycle.attach_mining_legs`, in core) and many tests reach
these names through `cycle`. So each re-export must be the very object `feed_assembly` defines (which
names `cycle` still offers is pinned by `test_mvp_runtime_crypto_reexport_roster.py`, PR-16): what
they run is then the one definition, and a same-named wrapper or copy in `cycle` could not drift from
it unnoticed. Identity does not carry a patch across the two modules, since a patch reaches only the
code that reads the patched module's name; that is why each move counted the patches on its names.
"""

from __future__ import annotations

import ast
from pathlib import Path

from runtime.mvp_runtime.crypto import cycle, feed_assembly


def test_every_definition_cycle_offers_is_the_same_object():
    tree = ast.parse(Path(feed_assembly.__file__).read_text(encoding="utf-8"))
    names = [node.name if isinstance(node, (ast.FunctionDef, ast.ClassDef)) else node.targets[0].id
             for node in tree.body
             if isinstance(node, (ast.FunctionDef, ast.ClassDef))
             or (isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name))]
    assert "attach_mining_legs" in names and len(names) >= 17
    offered = [n for n in names if n in vars(cycle)]
    assert "attach_mining_legs" in offered
    assert [n for n in offered if getattr(cycle, n) is not getattr(feed_assembly, n)] == []

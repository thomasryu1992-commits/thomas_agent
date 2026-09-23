"""One context's market inputs (`crypto/feed_assembly.py`, crypto PR7e-2 and PR7e-5).

The scheduler's factory dispatches (`crypto_cycle.attach_mining_legs`, in core) and many tests reach
these names through `cycle`. So each re-export must be the very object `feed_assembly` defines: what
they run is then the one definition, and a same-named wrapper or copy in `cycle` could not drift from
it unnoticed. Identity does not carry a patch across the two modules, since a patch reaches only the
code that reads the patched module's name; that is why each move counted the patches on its names.
"""

from __future__ import annotations

import ast
from pathlib import Path

from runtime.mvp_runtime.crypto import cycle, feed_assembly


def test_cycle_re_exports_every_definition_as_the_same_object():
    tree = ast.parse(Path(feed_assembly.__file__).read_text(encoding="utf-8"))
    names = [node.name if isinstance(node, (ast.FunctionDef, ast.ClassDef)) else node.targets[0].id
             for node in tree.body
             if isinstance(node, (ast.FunctionDef, ast.ClassDef))
             or (isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name))]
    assert "attach_mining_legs" in names and len(names) >= 17
    assert [n for n in names if getattr(cycle, n, None) is not getattr(feed_assembly, n)] == []

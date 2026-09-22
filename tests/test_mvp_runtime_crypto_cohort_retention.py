"""The retention stores' cohort sweeps (`crypto/cohort_retention.py`, crypto PR7e-6).

The fan-out in `cycle` calls these after its context loop, and the store tests reach them through
`cycle`. So each re-export must be the very object `cohort_retention` defines: a same-named wrapper or
copy in `cycle` would let a patch on one miss the other.
"""

from __future__ import annotations

import ast
from pathlib import Path

from runtime.mvp_runtime.crypto import cohort_retention, cycle


def test_cycle_re_exports_every_sweep_as_the_same_object():
    tree = ast.parse(Path(cohort_retention.__file__).read_text(encoding="utf-8"))
    names = [node.name for node in tree.body if isinstance(node, ast.FunctionDef)]
    assert sorted(names) == ["accumulate_open_interest_cohort", "accumulate_orderbook_cohort",
                             "accumulate_positioning_cohort", "retention_cohort"]
    assert [n for n in names if getattr(cycle, n, None) is not getattr(cohort_retention, n)] == []

"""The retention stores' cohort sweeps (`crypto/cohort_retention.py`, crypto PR7e-6).

The fan-out in `cycle` calls these after its context loop, and the store tests reach them through
`cycle`. So each re-export must be the very object `cohort_retention` defines: what the fan-out runs and
what those tests exercise is then the one definition, and a same-named wrapper or copy in `cycle` could
not drift from it unnoticed.

Identity does not carry a patch from one module to the other: a patch on a name reaches only the code
that reads that module's name. `run_pool_cycle` reads the sweeps from `cycle`, and the sweeps read
`retention_cohort` from `cohort_retention`, so since this move a patch on `cycle.retention_cohort` no
longer narrows a sweep. No test patches any of the four names (the PR7e-6 census).
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

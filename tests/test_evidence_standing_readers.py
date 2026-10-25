"""Every reader of a holdout verdict also reads evidence standing, or says here why it does not (2026-10-25).

Two report tools (the walk-forward report, the funnel's family table) read `robustness.holdout_status` without
`candidate_ranking.evidence_standing`, and the first one's pre-registered split looked sharp only because 12 of
its 14 CONFIRMED rows were `oi_lookahead`. Each fix has its own behaviour test; this one keeps the next reader
from repeating it. Like the tunables sweep, it reads the source, so a new reader is a choice recorded here
rather than an oversight.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SWEPT = (ROOT / "runtime" / "mvp_runtime", ROOT / "scripts")
STANDING = {"evidence_invalid", "evidence_standing"}

# Readers that call `holdout_status` and deliberately do not filter on standing, each with its reason.
EXEMPT = {
    "runtime/mvp_runtime/crypto/robustness.py":
        "defines holdout_status and scores with it; standing is a read-time overlay applied by its readers",
    "scripts/measure_oi_lookahead.py":
        "measures the lookahead itself: it compares holdouts with and without the leak, so it must see both",
}


def _called_names(tree: ast.AST) -> set[str]:
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            names.add(func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", ""))
    return names


def _readers() -> dict[str, set[str]]:
    out = {}
    for base in SWEPT:
        for path in base.rglob("*.py"):
            names = _called_names(ast.parse(path.read_text(encoding="utf-8")))
            if "holdout_status" in names:
                out[path.relative_to(ROOT).as_posix()] = names
    return out


def test_every_holdout_reader_reads_evidence_standing_or_is_exempt_with_a_reason():
    unaware = sorted(path for path, names in _readers().items() if not names & STANDING and path not in EXEMPT)
    assert not unaware, ("these call holdout_status without evidence_invalid/evidence_standing — a lookahead "
                         "row would count as a confirmation: " + ", ".join(unaware))


def test_the_exemptions_are_still_readers():
    readers = _readers()
    stale = sorted(path for path in EXEMPT if path not in readers)
    assert not stale, "exempt but no longer a holdout reader; drop the entry: " + ", ".join(stale)

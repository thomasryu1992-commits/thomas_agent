"""A stored judgment is provenance, and nothing may decide on one.

Both crypto stores are append-only, and a candidate row's ``record_sha256`` covers the whole
row — so a label written under one rule cannot be corrected under the next without the row
reading as tampered. Measured 2026-08-08: stored ``verdict``/``holdout_status`` say ROBUST=83
and CONFIRMED=237 where today's rule says 0 and 0; the 41 import candidate rows say
PAPER_ACTIVE=25 while the pool says all 41 SUSPENDED; 34 pool entries carry
``robustness_verdict: ROBUST`` from an import two rule vintages ago.

None of it reaches a decision, and that is the property here — held by nothing but habit until
now. `pool.candidate_quality` recomputes from the stored COMPONENTS, membership comes from the
pool rather than a candidate row, and the promotion door copies raw per-regime numbers onto an
entry instead of a derived label.

The failure this refuses is one line: a router or a promotion filter reading
``entry.get("robustness_verdict")`` and getting a two-vintage-old ROBUST for a strategy the
current rule would not confirm. It would be invisible — the field is present, populated, and
plausible on exactly the 34 entries that predate the rules.
"""

from __future__ import annotations

import ast
import pathlib

from runtime.mvp_runtime.crypto.pool import STORED_SNAPSHOT_FIELDS

ROOT = pathlib.Path(__file__).resolve().parents[1]
SURFACES = sorted((ROOT / "runtime" / "mvp_runtime" / "crypto").glob("*.py")) + sorted(
    (ROOT / "scripts").glob("*.py")
)

# `proposer` is the one legitimate mention, and it is not this pattern: it WRITES these names
# onto a `crypto_strategy_proposal` record and prints them back in that record's own report.
# A proposal is a point-in-time review of a family nobody has minted, so its verdict is the
# artifact rather than a cached answer about a routable strategy. Nothing routes on it and no
# pool entry is involved.
ALLOWED = {"proposer.py"}


def _string_constants(path: pathlib.Path) -> set[str]:
    """Every string literal in the module. Literals rather than attribute access, because
    these fields are only ever reached by name through a Mapping.

    The ``STORED_SNAPSHOT_FIELDS`` assignment is subtracted rather than the file that holds
    it being skipped: `pool.py` declares the names AND owns the recompute, so exempting the
    whole module would blind this to the one place most able to reintroduce the defect."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    declared: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "STORED_SNAPSHOT_FIELDS" for t in node.targets
        ):
            declared.update(id(n) for n in ast.walk(node.value))
    return {
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and id(node) not in declared
    }


def test_no_decision_surface_names_a_stored_verdict():
    """The pool-entry labels are read by nobody. Adding a reader is the defect."""
    named = {path.name: _string_constants(path) & STORED_SNAPSHOT_FIELDS for path in SURFACES}
    offenders = {
        name: sorted(hits) for name, hits in named.items()
        if hits and name not in ALLOWED
    }
    assert not offenders, (
        f"{offenders} — a stored verdict is what a rule said once, and this runtime has "
        "changed those rules twice. Recompute through pool.candidate_quality, or if the "
        "field genuinely belongs on a new record type, add its module to ALLOWED with the "
        "reason it is not a cached answer."
    )


def test_only_candidate_quality_reads_the_stored_robustness_block():
    """The nested labels (`verdict`, `holdout_status`) have names too generic to scan for, so
    the block that holds them is pinned instead: one reader, and it recomputes.

    `robustness.py` is excluded by construction rather than by allowlist — it is where the
    rules live, not a consumer of what an older one decided."""
    readers = set()
    for path in SURFACES:
        if path.name == "robustness.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            # `<expr>.get("robustness")` — how a stored evidence block is opened.
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "robustness"
            ):
                readers.add(path.name)
    assert readers == {"pool.py"}, (
        f"the stored robustness block is opened in {sorted(readers)}; only "
        "pool.candidate_quality may, and only to recompute from the components"
    )


def test_the_measurements_stay_readable():
    """The inverse pin. `robustness_score` and `trades_per_parameter` are measurements, not
    labels — the recompute feeds them back into `classify_verdict` on every read, so
    forbidding them would forbid the mechanism that makes the labels disposable."""
    assert not STORED_SNAPSHOT_FIELDS & {"robustness_score", "trades_per_parameter"}
    assert "robustness_score" in _string_constants(
        ROOT / "runtime" / "mvp_runtime" / "crypto" / "pool.py"
    ), "candidate_quality stopped reading the component it recomputes from"

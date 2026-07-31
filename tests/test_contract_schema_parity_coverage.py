"""What the contract/schema parity gate covers — and that it still knows.

The gate compares a contract document's required-field table against its schema's `required`
list. Which pairs it compares was a hand-written list of `compare_required(...)` calls with
nothing asserting that list was complete, and it had drifted in the direction that matters:
`permission_decision.v0.3` was checked while **v0.4** — the version a live order's
PermissionDecision is built at — was not. The superseded schema was guarded; the live one was
not, and no run of anything said so.

`assert_every_covered_family_is_fully_covered` closes that: if the gate checks any version of a
schema family, every version present in `schemas/` must be checked. Derived from the list and
the directory, with no exemptions — an exemption list would be the same hand-maintained thing
whose missing completeness check is the bug.

**The risk that makes these tests worth writing is in the detector, not the rule.**
`covered_schemas()` finds the covered set by regex over this gate's own source. If that regex
stopped matching — a call reformatted, a path built rather than written — coverage would
silently read as empty, every family would look fully covered, and the check would pass
vacuously while guarding nothing. This repository has been bitten by exactly that shape once
already: a containment test collected only `ast.Attribute`, so a whole calling convention
scored zero and the test could only ever fail on a new caller. So the detector is asserted
against known truth here, not just exercised.
"""

from __future__ import annotations

import json

import pytest

from runtime.mvp_runtime import _scripts_bridge  # noqa: F401  (puts scripts/ on sys.path)
from runtime.mvp_runtime.paths import repo_root

from validate_contract_schema_parity import (
    covered_schemas,
    schema_family,
)


# --- the family parser -----------------------------------------------------------

@pytest.mark.parametrize(
    "schema_rel,expected",
    [
        ("schemas/approval.v0.2.schema.json", "approval"),
        ("schemas/permission_decision.v0.4.schema.json", "permission_decision"),
        ("schemas/read_only_runtime_input_bundle.v0.1.schema.json", "read_only_runtime_input_bundle"),
        ("schemas/current_core_release.v0.2.schema.json", "current_core_release"),
    ],
)
def test_a_version_is_stripped_to_its_family(schema_rel, expected):
    assert schema_family(schema_rel) == expected


def test_every_schema_in_the_repository_parses_to_a_family_without_its_version():
    """A family name that kept its version would make every schema its own family, and the
    completeness check would pass while comparing nothing to nothing."""
    for path in sorted((repo_root() / "schemas").glob("*.schema.json")):
        family = schema_family(f"schemas/{path.name}")
        assert ".schema.json" not in family
        assert not family.endswith(tuple(f".v0.{n}" for n in range(10)))


# --- the detector, asserted against known truth ----------------------------------

def test_the_covered_set_is_found_and_is_not_empty():
    """The vacuous-pass guard. If the regex reading this gate's own source stops matching,
    `covered` becomes empty, every family looks fully covered, and the completeness check
    reports success having compared nothing."""
    covered = covered_schemas()
    assert len(covered) >= 14, f"coverage detector found only {len(covered)} pairs — it has gone blind"


@pytest.mark.parametrize(
    "schema_rel",
    [
        "schemas/permission_decision.v0.3.schema.json",
        "schemas/permission_decision.v0.4.schema.json",
        "schemas/approval.v0.1.schema.json",
        "schemas/approval.v0.2.schema.json",
        "schemas/audit_event.v0.1.schema.json",
        "schemas/audit_event.v0.2.schema.json",
        "schemas/task.v0.3.schema.json",
    ],
)
def test_a_known_covered_pair_is_seen_as_covered(schema_rel):
    assert schema_rel in covered_schemas()


def test_the_covered_set_names_only_schemas_that_exist():
    for schema_rel in covered_schemas():
        assert (repo_root() / schema_rel).is_file(), f"{schema_rel} is named but absent"


# --- the rule itself -------------------------------------------------------------

def test_every_version_of_a_covered_family_is_covered():
    """The invariant, evaluated against the real tree — the same computation the gate runs,
    stated here so a failure names the schema rather than only failing CI."""
    covered = covered_schemas()
    families = {schema_family(rel) for rel in covered}
    gaps = [
        f"schemas/{path.name}"
        for path in sorted((repo_root() / "schemas").glob("*.schema.json"))
        if f"schemas/{path.name}" not in covered and schema_family(f"schemas/{path.name}") in families
    ]
    assert gaps == [], f"covered families with an unchecked version: {gaps}"


def test_the_live_permission_decision_schema_is_the_one_that_was_missing():
    """Named rather than left implicit: v0.4 adds `FINANCIAL_APPROVED_TRADING_USE`, the scope
    the live order path decides under, and it keeps v0.3's required set — which is why one
    contract document anchors both, and why nothing failed while it went unchecked."""
    v3, v4 = (
        json.loads((repo_root() / f"schemas/permission_decision.{v}.schema.json").read_text(encoding="utf-8"))
        for v in ("v0.3", "v0.4")
    )
    assert set(v3["required"]) == set(v4["required"])
    assert {"schemas/permission_decision.v0.3.schema.json",
            "schemas/permission_decision.v0.4.schema.json"} <= covered_schemas()

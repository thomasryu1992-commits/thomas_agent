"""A policy bump must not retroactively unmake the records written before it.

`policy_version` went 1.1.0 -> 1.2.0 in `6b51c24`, and the seven governance schemas that pin the
binding had their `const` **replaced** rather than added to. Every record written under 1.1.0
stopped satisfying its own schema at that moment: 314 of them on this host — approvals, permission
decisions, and archived ledger rows.

Ten of those are approvals that are still PENDING. An approval is re-validated when it is decided
and again when it is consumed, so those ten cannot be exercised: `APPROVAL_SCHEMA_INVALID`, from a
`oneOf` failure naming neither the bump nor the record's own version. Grants Thomas made were
silently voided by a version number.

Two rules were being enforced by one mechanism, and separating them is the whole change:

* **Which version may a record be written under?** The current one, and only the current one.
  That is `POLICY_BINDING` plus `validate_policy_binding`, at every write. **Unchanged** — the
  tests below pin that nothing new became permitted.
* **Is this a well-formed record of its type?** A record written under an earlier policy is an
  older record, not a malformed one. That is the schema's question, and pinning the current
  version there answered a different one.

Safe because no path validates these record types by schema alone: `approval.py` and
`permission.py` each run the schema check and the semantic check together, so loosening the
schema removes no enforcement.

Whether a grant *should* survive a policy bump stays a governance decision and is deliberately
not taken here — the answer is still no. What changed is that the refusal now says so.
"""

from __future__ import annotations

import json

import pytest

from runtime.mvp_runtime import _scripts_bridge  # noqa: F401  (puts scripts/ on sys.path)
from runtime.mvp_runtime.paths import repo_root

from validate_permission_approval_contracts import (
    LEGACY_POLICY_BINDING,
    POLICY_BINDING,
    validate_policy_binding,
)

# Every schema that pins the governance binding.
BOUND_SCHEMAS = (
    "approval.v0.1",
    "approval.v0.2",
    "permission_decision.v0.3",
    "permission_decision.v0.4",
    "program_request.v0.1",
    "tool_request.v0.1",
    "execution_request.v0.1",
)


def _canonical_branch(schema_name: str) -> dict:
    body = json.loads((repo_root() / "schemas" / f"{schema_name}.schema.json").read_text(encoding="utf-8"))
    branches = body["properties"]["operating_policy"]["oneOf"]
    return next(b for b in branches
                if b["properties"]["policy_id"]["const"] == POLICY_BINDING["policy_id"])


def _superseded(version: str = "1.1.0") -> dict:
    return {**POLICY_BINDING, "policy_version": version}


# --- the schema describes shape, not recency -------------------------------------

@pytest.mark.parametrize("schema_name", BOUND_SCHEMAS)
def test_no_schema_pins_the_current_policy_version(schema_name):
    """The defect, refused at the source. A `const` here is a recency rule in the one place that
    cannot distinguish "written before the bump" from "malformed", and it voids history the
    moment the version moves."""
    version = _canonical_branch(schema_name)["properties"]["policy_version"]
    assert "const" not in version, (
        f"{schema_name} pins policy_version as a const; a bump would invalidate every record "
        "already written under the previous version"
    )
    assert version.get("pattern"), f"{schema_name} must still constrain the version's shape"


@pytest.mark.parametrize("schema_name", BOUND_SCHEMAS)
def test_the_policy_identity_is_still_pinned(schema_name):
    """Only the *version* loosened. A record naming some other policy, or some other policy file,
    is still malformed — that is shape, not recency."""
    props = _canonical_branch(schema_name)["properties"]
    assert props["policy_id"]["const"] == POLICY_BINDING["policy_id"]
    assert props["policy_ref"]["const"] == POLICY_BINDING["policy_ref"]


@pytest.mark.parametrize("schema_name", BOUND_SCHEMAS)
def test_a_superseded_binding_is_a_well_formed_binding(schema_name):
    """What the 314 stored records need: their own schema must be able to describe them."""
    from jsonschema import Draft202012Validator

    branch = _canonical_branch(schema_name)
    validator = Draft202012Validator(branch)
    assert list(validator.iter_errors(_superseded())) == []
    assert list(validator.iter_errors(POLICY_BINDING)) == []


@pytest.mark.parametrize("schema_name", BOUND_SCHEMAS)
def test_a_malformed_version_is_still_malformed(schema_name):
    """Loosened, not abandoned."""
    from jsonschema import Draft202012Validator

    validator = Draft202012Validator(_canonical_branch(schema_name))
    for bad in ("", "one point two", "1.2", "v1.2.0", "1.2.0-rc1"):
        assert list(validator.iter_errors({**POLICY_BINDING, "policy_version": bad})), bad


# --- recency is unchanged, and now says its own name -----------------------------

def test_the_current_binding_is_still_the_only_one_accepted():
    """The property that must not have moved: nothing new is permitted."""
    issues: list[str] = []
    validate_policy_binding({"operating_policy": POLICY_BINDING}, issues)
    assert issues == []

    for rejected in (_superseded(), _superseded("0.9.0"), _superseded("99.0.0"),
                     LEGACY_POLICY_BINDING, {}, None):
        issues = []
        validate_policy_binding({"operating_policy": rejected}, issues)
        assert issues, f"{rejected!r} must still be refused"


def test_a_superseded_binding_is_refused_by_a_name_that_explains_it():
    """The operator's only clue used to be a `oneOf` dump naming neither the bump nor the
    record's own version. A refusal nobody can act on is how ten grants sat voided and unnoticed."""
    issues: list[str] = []
    validate_policy_binding({"operating_policy": _superseded()}, issues)
    assert len(issues) == 1
    message = issues[0]
    assert "SUPERSEDED" in message
    assert "1.1.0" in message                       # what the record binds
    assert POLICY_BINDING["policy_version"] in message   # what is current


def test_a_binding_that_is_not_the_governance_policy_keeps_the_plain_refusal():
    """The superseded message claims the record is well-formed and refused on recency alone.
    It must not be attached to a record where that is untrue."""
    issues: list[str] = []
    validate_policy_binding({"operating_policy": {"policy_id": "someone.elses.policy",
                                                  "policy_version": "1.2.0",
                                                  "policy_ref": POLICY_BINDING["policy_ref"]}}, issues)
    assert len(issues) == 1 and "SUPERSEDED" not in issues[0]


# --- the reason the schema may be loosened at all --------------------------------

def test_no_record_type_is_validated_by_schema_alone():
    """Loosening the schema removes no enforcement only while every schema check is paired with
    the semantic one. If a schema-only path is ever added, this fails and the pairing has to be
    restored before it can pass."""
    import ast
    from pathlib import Path

    for module, semantic in (("approval.py", "validate_approval_record"),
                             ("permission.py", "validate_permission_record")):
        source = (repo_root() / "runtime" / "mvp_runtime" / module).read_text(encoding="utf-8")
        tree = ast.parse(source)
        calls = [
            node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            for node in ast.walk(tree) if isinstance(node, ast.Call)
        ]
        assert "validate_against_schema" in calls, f"{module}: no schema check found"
        assert semantic in calls, (
            f"{module} validates against a schema without {semantic}; the schema no longer pins "
            "the policy version, so that pairing is what enforces recency"
        )

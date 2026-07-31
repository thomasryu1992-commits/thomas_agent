"""The Current Core pointer — the one record that answers "is a Core active on this machine".

`CLAUDE.md` states the rule plainly: *records must satisfy their closed schema
(`additionalProperties: false`)*. Every schema in the repository obeyed it except this one,
and this one is the pointer the runtime reads before it will bind a task to anything.

What "open" meant here was worse than an extra key slipping through. Its `properties` block
declared **two** fields — `schema_version` and `runtime_activation_status` — while its
`allOf` branches went on to *require* eleven more. Required and undeclared is the weakest
combination JSON Schema offers: the field had to be present and could be anything at all, so
`activation_sha256: 12345` and `updated_at_utc: "last tuesday"` both validated.

Nothing was exploitable through it — `core_release_verifier` re-derives the activation hash
and compares it, so a junk value fails there instead. That is the honest scope of this: the
schema was not the thing holding the line, while being the thing that says what the record is.

Two invariants are pinned below. The pointer's own shape, and the repository-wide rule it was
the sole exception to — asserted with **no exemption list**, because a hand-maintained list of
schemas that are allowed to be open is how the exception survived in the first place.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator, FormatChecker

from runtime.mvp_runtime.paths import repo_root

SCHEMA_NAME = "current_core_release.v0.2.schema.json"


@pytest.fixture(scope="module")
def validator() -> Draft202012Validator:
    schema = json.loads((repo_root() / "schemas" / SCHEMA_NAME).read_text(encoding="utf-8"))
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _activated() -> dict:
    """An activation pointer of the shape `activate_core_release.py` writes."""
    return {
        "schema_version": "current_core_release.v0.2",
        "runtime_activation_status": "approved_via_activation_registry",
        "activation_id": "core-activation-" + "0" * 24,
        "activation_path": "THOMAS_CORE/activations/core-activation-" + "0" * 24 + ".yaml",
        "activation_sha256": "sha256:" + "a" * 64,
        "release_id": "thomas-core-v0.2.1-a99f17144a7c",
        "core_version": "0.2.1",
        "core_bundle_sha256": "sha256:" + "b" * 64,
        "approval_id": "core-approval-" + "1" * 24,
        "updated_at_utc": "2026-07-31T04:00:00Z",
        "updated_by": "Thomas",
        "update_ref": "activation-ref",
        "scope": {
            "authorizes_new_task_core_binding_after_commit": True,
            "changes_existing_task_bindings": False,
            "grants_execution_permission": False,
        },
    }


def _deactivated() -> dict:
    """A fail-closed pointer of the shape `deactivate_core_release.py` writes.

    Note its `scope` names `authorizes_new_task_core_binding` where the activation branch says
    `..._after_commit`. The two branches carry genuinely different scope keys, which is why
    `scope` stays `{"type": "object"}` here — same as its sibling `core_deactivation.v0.1`.
    """
    return {
        "schema_version": "current_core_release.v0.2",
        "runtime_activation_status": "deactivated_fail_closed",
        "deactivation_id": "core-deactivation-" + "2" * 24,
        "deactivation_path": "THOMAS_CORE/deactivations/core-deactivation-" + "2" * 24 + ".yaml",
        "deactivation_sha256": "sha256:" + "c" * 64,
        "updated_at_utc": "2026-07-31T04:00:00Z",
        "updated_by": "Thomas",
        "update_ref": "deactivation-ref",
        "scope": {
            "authorizes_new_task_core_binding": False,
            "changes_existing_task_bindings": False,
            "grants_execution_permission": False,
        },
    }


def _errors(validator, doc) -> list[str]:
    return [e.message for e in validator.iter_errors(doc)]


# --- both real shapes still validate ---------------------------------------------

@pytest.mark.parametrize("build", [_activated, _deactivated], ids=["activated", "deactivated"])
def test_the_writers_records_validate(validator, build):
    assert _errors(validator, build()) == []


def test_the_schema_matches_what_the_writers_actually_emit():
    """The schema states the record's shape, so it has to be checked against the code that
    produces it and not against a copy of it. Parsed out of the writers rather than restated:
    a field added to a writer and not here should fail, which a hand-kept fixture cannot do."""
    schema = json.loads((repo_root() / "schemas" / SCHEMA_NAME).read_text(encoding="utf-8"))
    declared = set(schema["properties"])

    for script in ("activate_core_release.py", "deactivate_core_release.py"):
        tree = ast.parse((repo_root() / "scripts" / script).read_text(encoding="utf-8"))
        emitted = {
            key.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            and any(getattr(t, "id", None) == "pointer" for t in node.targets)
            and isinstance(node.value, ast.Dict)
            for key in node.value.keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        }
        assert emitted, f"could not find the pointer record {script} writes"
        assert emitted <= declared, (
            f"{script} emits fields the schema does not declare: {sorted(emitted - declared)}"
        )


# --- what "required but undeclared" used to let through --------------------------

@pytest.mark.parametrize(
    "field,value",
    [
        ("activation_sha256", 12345),                       # an integer where a hash belongs
        ("activation_sha256", "sha256:short"),
        ("core_bundle_sha256", "not-a-hash"),
        ("approval_id", "not-an-approval-id"),
        ("activation_id", "core-activation-NOTHEX"),
        ("updated_at_utc", "last tuesday"),
        ("updated_at_utc", "2026-07-15T14:31:30+00:00"),    # not normalized to Z
        ("updated_by", ""),
        ("scope", "not an object"),
    ],
)
def test_a_required_field_now_has_to_be_the_right_thing(validator, field, value):
    doc = _activated()
    doc[field] = value
    assert _errors(validator, doc), f"{field}={value!r} should not validate"


def test_an_undeclared_field_is_refused(validator):
    doc = _activated()
    doc["surprise"] = "hello"
    assert _errors(validator, doc)


# --- one record may not claim both states ----------------------------------------

def test_an_activated_pointer_may_not_carry_deactivation_evidence(validator):
    """Defence in depth rather than a hole being closed: `core_release_verifier` branches on
    `runtime_activation_status` and reads only that branch's fields, so a foreign one is inert
    today. It is refused anyway because a record asserting both states at once is an authority
    conflict, and the rule for those is BLOCK rather than pick one."""
    doc = _activated()
    doc["deactivation_id"] = "core-deactivation-" + "3" * 24
    assert _errors(validator, doc)


def test_a_deactivated_pointer_may_not_carry_activation_evidence(validator):
    doc = _deactivated()
    doc["activation_path"] = "THOMAS_CORE/activations/anything.yaml"
    assert _errors(validator, doc)


def test_the_fail_closed_fixture_the_gate_relies_on_is_still_refused(validator):
    """`validate_i0_5_1_runtime_promotion_readiness` writes this exact pointer to prove an
    invalid one is reported unverified. Closing the schema must not make it accidentally
    valid, and must not stop it being refused for the reason the gate expects."""
    assert _errors(validator, {
        "schema_version": "current_core_release.v0.2",
        "runtime_activation_status": "approved_via_activation_registry",
        "activation_path": "THOMAS_CORE/activations/not-real.yaml",
    })


# --- and the rule this schema was the exception to -------------------------------

def test_every_schema_in_the_repository_is_a_closed_object():
    """`CLAUDE.md`: records must satisfy their closed schema. Asserted over the whole
    directory with **no exemption list** — a hand-maintained list of schemas allowed to stay
    open is exactly how this one stayed open, unnoticed, while 73 siblings were closed."""
    offenders = []
    for path in sorted((repo_root() / "schemas").glob("*.schema.json")):
        schema = json.loads(path.read_text(encoding="utf-8"))
        if schema.get("type") != "object":
            offenders.append(f"{path.name}: top-level type is {schema.get('type')!r}, not object")
        elif schema.get("additionalProperties") is not False:
            offenders.append(f"{path.name}: no top-level additionalProperties: false")
    assert offenders == [], "\n".join(offenders)


def test_the_pointer_on_this_machine_validates_if_there_is_one(validator):
    """Belt and braces for the machine actually running this: an activated checkout's own
    pointer must satisfy the schema that describes it. Skipped where no Core is activated,
    which is every fresh checkout."""
    pointer = repo_root() / ".runtime_governance_state" / "CURRENT_CORE_RELEASE.yaml"
    if not pointer.is_file():
        pytest.skip("no Core activated in this checkout")
    assert _errors(validator, yaml.safe_load(pointer.read_text(encoding="utf-8"))) == []

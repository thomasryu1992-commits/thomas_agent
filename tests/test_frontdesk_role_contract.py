"""D2 — conversation.frontdesk role contract and its closed turn schema.

The role is contract-first: this file pins the two structural halves of its privilege
separation before any runtime code exists. Half one: the registry places the role in
``non_dynamic_roles`` with ``routable: false``, so Prime can never route work to it — it
lives in front of the pipeline, never inside it. Half two: ``frontdesk_turn.v0.1`` is the
FORCED shape of its model output — seven typed turns with closed payloads, no field
through which a turn could name a tool, file, provider, or permission.

Nothing here activates anything: the role is ``candidate`` and the activation flip is a
separate explicit Thomas decision (proposal D2), pinned by a test below so the flip cannot
ride in silently with unrelated changes.
"""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest
import yaml

from runtime.mvp_runtime import schema_cache
from runtime.read_only_kernel.schema_validation import RuntimeSchemaError

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "03_ROLE_CONTRACTS" / "ROLE_REGISTRY.yaml"
DEFINITION_PATH = ROOT / "03_ROLE_CONTRACTS" / "CONVERSATION_FRONTDESK_ROLE.md"
SCHEMA_PATH = ROOT / "schemas" / "frontdesk_turn.v0.2.schema.json"


def _registry_entry() -> dict:
    registry = yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8"))
    matches = [e for e in registry.get("non_dynamic_roles", [])
               if e.get("role_id") == "conversation.frontdesk"]
    assert len(matches) == 1, "conversation.frontdesk must be registered exactly once, as non-dynamic"
    return matches[0]


def _front_matter() -> dict:
    lines = DEFINITION_PATH.read_text(encoding="utf-8").splitlines()
    closing = next(i for i, line in enumerate(lines[1:], start=1) if line.strip() == "---")
    return yaml.safe_load("\n".join(lines[1:closing]))


# --- registry placement ------------------------------------------------------

def test_frontdesk_is_non_dynamic_and_non_routable():
    """The boundary itself: Prime must never be able to route work to the front desk.
    Non-dynamic placement also removes the dynamic-role rule 'active => routable', which
    would otherwise force this role routable on the day it activates."""
    entry = _registry_entry()
    assert entry["routable"] is False
    assert entry["role_type"] == "session_front"


def test_frontdesk_is_not_a_routable_dynamic_role():
    registry = yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8"))
    assert all(e.get("role_id") != "conversation.frontdesk" for e in registry.get("roles", []))


def test_activation_is_a_separate_explicit_decision():
    """This test existed to pin ``candidate`` so the activation flip could not ride in
    silently. The flip HAS now been made — proposal decision D2, explicit Thomas decision
    2026-07-25, in the F2 runtime PR, by updating exactly this test — which is the
    ceremony the pin was for. It now pins ``active`` the same way, so a future
    deactivation is equally deliberate."""
    assert _registry_entry()["status"] == "active"
    assert _front_matter()["status"] == "active"


def test_registry_hash_pins_the_definition():
    """A definition edit without a registry update must be caught, not waved through —
    the same fail-closed hash rule every dynamic role already lives under."""
    entry = _registry_entry()
    actual = sha256(DEFINITION_PATH.read_bytes()).hexdigest()
    assert entry["definition_sha256"] == actual


def test_registry_and_definition_agree():
    data = _front_matter()
    entry = _registry_entry()
    assert data["role_id"] == entry["role_id"]
    assert data["routable"] is False
    assert data["role_type"] == entry["role_type"]
    assert data["status"] == entry["status"]


# --- the contract's authority boundaries -------------------------------------

def test_ceiling_is_p2_with_no_external_action():
    data = _front_matter()
    assert data["permission_ceiling"] == "P2"
    assert data["external_action_allowed"] is False
    assert data["allowed_program_ids"] == []
    assert data["allowed_tool_ids"] == []


def test_every_refused_capability_is_named():
    """The prohibitions ARE the contract: each one closes a distinct door the front desk
    must never open, however convincing its conversation."""
    unsupported = set(_front_matter()["unsupported_capabilities"])
    assert {
        "task_execution",
        "planning_or_routing",
        "permission_decision_issuance",
        "approval_issuance_or_consumption",
        "tool_or_search_invocation",
        "workspace_write",
        "memory_promotion",
        "external_action",
    } <= unsupported


def test_session_memory_is_isolated_in_both_directions():
    """frontdesk_session is readable only here (conversation cannot leak into task
    context via memory), and task_working_memory is prohibited here (task context cannot
    leak into conversation — results travel only through QUERY_RESULT)."""
    policy = _front_matter()["memory_policy"]
    assert "frontdesk_session" in policy["readable_scopes"]
    assert "task_working_memory" in policy["prohibited_scopes"]
    assert policy["direct_validated_write_allowed"] is False
    assert policy["secret_candidate_creation_allowed"] is False
    for other in ("GENERAL_SPECIALIST_ROLE.md", "VALIDATION_ROLE.md"):
        other_lines = (ROOT / "03_ROLE_CONTRACTS" / "ROLES" / "ACTIVE" / other).read_text(
            encoding="utf-8").splitlines()
        closing = next(i for i, l in enumerate(other_lines[1:], start=1) if l.strip() == "---")
        other_policy = yaml.safe_load("\n".join(other_lines[1:closing]))["memory_policy"]
        assert "frontdesk_session" not in other_policy["readable_scopes"], other


def test_one_model_call_and_no_tools_per_turn():
    limits = _front_matter()["budget_caps"]["limits"]
    assert limits["max_model_calls"] == 1
    assert limits["max_tool_calls"] == 0
    assert limits["max_program_calls"] == 0


# --- the closed turn schema --------------------------------------------------

def _validate(record: dict) -> None:
    schema_cache.validate_against_schema(record, SCHEMA_PATH, "frontdesk_turn")


def _turn(kind: str, payload: dict, reply: str = "네, 확인했습니다.") -> dict:
    return {"schema_version": "frontdesk_turn.v0.2", "turn_kind": kind,
            "payload": payload, "reply_text": reply}


@pytest.mark.parametrize("kind, payload", [
    ("SUBMIT_TASK", {"request_text": "이 사업 아이디어를 분석해줘: 구독형 세차",
                     "important": False, "independent_validation": False}),
    ("QUERY_STATUS", {}),
    ("QUERY_HISTORY", {}),
    ("QUERY_HISTORY", {"limit": 5}),
    ("QUERY_HISTORY", {"limit": None}),
    ("QUERY_RESULT", {"entry_id": "treg_92c441b"}),
    ("CANCEL_TASK", {"entry_id": "treg_92c441b"}),
    ("CLARIFY", {}),
    ("CHAT_REPLY", {}),
])
def test_each_turn_kind_validates(kind, payload):
    _validate(_turn(kind, payload))


@pytest.mark.parametrize("record, why", [
    (_turn("RUN_TOOL", {}), "a kind outside the seven does not exist"),
    (_turn("SUBMIT_TASK", {"request_text": "x", "important": False,
                           "independent_validation": False, "tool_id": "shell"}),
     "no payload field can name a tool"),
    (_turn("SUBMIT_TASK", {"request_text": "", "important": False,
                           "independent_validation": False}),
     "an empty request cannot be submitted"),
    (_turn("SUBMIT_TASK", {"important": False, "independent_validation": False}),
     "the verbatim request text is required, not optional"),
    (_turn("SUBMIT_TASK", {"request_text": "x"}),
     "flags must be stated explicitly, never defaulted"),
    (_turn("CANCEL_TASK", {}), "a cancel must name its target"),
    (_turn("CHAT_REPLY", {"entry_id": "treg_x"}), "chat carries no arguments"),
    (_turn("CHAT_REPLY", {}, reply="") , "the operator always sees something"),
    ({"schema_version": "frontdesk_turn.v0.2", "turn_kind": "CHAT_REPLY",
      "payload": {}}, "reply_text is required"),
    ({**_turn("CHAT_REPLY", {}), "side_effect": "write"}, "no fields beyond the four"),
])
def test_invalid_turns_are_rejected(record, why):
    with pytest.raises(RuntimeSchemaError):
        _validate(record)
    # `why` documents the boundary each rejection pins.


def test_the_contract_names_the_downgrade_rule():
    """An invalid turn becomes CHAT_REPLY (uncertain => submits nothing). The runtime will
    implement it in F2; the contract carries it now so F2 cannot 'forget'."""
    output = _front_matter()["output_contract"]
    assert output["base_contract"] == "frontdesk_turn.v0.2"
    assert output["invalid_turn_downgrade"] == "CHAT_REPLY"


# --- v0.2: the runtime read turns --------------------------------------------

@pytest.mark.parametrize("kind", ["QUERY_SCHEDULES", "QUERY_CONTROL", "QUERY_MEMORY"])
def test_runtime_read_turns_take_no_arguments(kind):
    """They name a subject; the runtime owns everything else. No field here could point
    the lookup somewhere it was not meant to go."""
    _validate(_turn(kind, {}))
    with pytest.raises(RuntimeSchemaError):
        _validate(_turn(kind, {"path": "/etc/passwd"}))


def test_the_widening_stayed_an_enumeration():
    """v0.2 adds items to a list; it does not introduce open-ended access. Every turn kind
    is still one of a fixed set, and the only ones that change state remain the two that
    feed the already-governed task queue."""
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    kinds = set(schema["properties"]["turn_kind"]["enum"])
    assert kinds == {
        "SUBMIT_TASK", "CANCEL_TASK",
        "QUERY_STATUS", "QUERY_HISTORY", "QUERY_RESULT",
        "QUERY_SCHEDULES", "QUERY_CONTROL", "QUERY_MEMORY",
        "CLARIFY", "CHAT_REPLY",
    }
    assert len([k for k in kinds if not k.startswith("QUERY_")
                and k not in ("CLARIFY", "CHAT_REPLY")]) == 2


def test_the_contract_names_the_new_capability():
    data = _front_matter()
    assert "runtime_state_lookup" in data["capabilities"]
    assert data["output_contract"]["base_contract"] == "frontdesk_turn.v0.2"
    # The prohibitions are untouched by the widening.
    unsupported = set(data["unsupported_capabilities"])
    assert {"task_execution", "workspace_write", "memory_promotion", "external_action"} <= unsupported

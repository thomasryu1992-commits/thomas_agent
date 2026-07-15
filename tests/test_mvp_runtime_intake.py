"""R2.1 Task Intake tests (DoD gate).

Covers: valid intake -> schema-valid RECEIVED task.v0.3; deterministic
traceability; and each fail-closed BLOCK case.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime.mvp_runtime.errors import TaskIntakeBlocked
from runtime.mvp_runtime.intake import build_task
from runtime.read_only_kernel import schema_validation

REPO_ROOT = Path(__file__).resolve().parents[1]
TASK_SCHEMA = REPO_ROOT / "schemas" / "task.v0.3.schema.json"
FIXED_NOW = "2026-07-15T09:00:00Z"

BUSINESS_REQUEST = "이 사업 아이디어를 분석해줘: 구독형 반려동물 사료 배송"


def _build(**overrides):
    params = dict(raw_request=BUSINESS_REQUEST, now=FIXED_NOW)
    params.update(overrides)
    return build_task(**params)


# --- happy path -------------------------------------------------------------

def test_valid_intake_is_schema_valid():
    task = _build()
    # Re-validate independently against the closed schema (not just intake's own call).
    schema_validation.validate_against_schema(task, TASK_SCHEMA, "test")
    assert task["schema_version"] == "task.v0.3"


def test_received_state_is_unbound_and_unevaluated():
    task = _build()
    assert task["lifecycle"]["status"] == "RECEIVED"
    assert task["context"]["core_context_binding_id"] is None
    assert task["classification"]["classification_status"] == "UNCLASSIFIED"
    assert task["permission"]["evaluation_status"] == "NOT_EVALUATED"
    assert task["permission"]["permission_decision"] is None
    assert task["routing"]["selected_route"] == "UNASSIGNED"


def test_ids_have_required_prefixes_and_root_equals_self():
    task = _build()
    assert task["identity"]["task_id"].startswith("task_")
    assert task["identity"]["trace_id"].startswith("trace_")
    assert task["identity"]["root_task_id"] == task["identity"]["task_id"]
    assert task["identity"]["parent_task_id"] is None
    assert task["identity"]["task_revision"] == 1


def test_same_input_and_time_is_deterministic():
    a = _build()
    b = _build()
    assert a["identity"]["task_id"] == b["identity"]["task_id"]
    assert a["identity"]["trace_id"] == b["identity"]["trace_id"]
    assert a == b


def test_different_time_yields_different_ids():
    a = _build(now="2026-07-15T09:00:00Z")
    b = _build(now="2026-07-15T09:00:01Z")
    assert a["identity"]["task_id"] != b["identity"]["task_id"]


def test_mvp_is_read_only_by_construction():
    task = _build()
    assert "no_external_action" in task["scope"]["constraints"]
    limits = task["execution_budget"]["limits"]
    assert limits["max_tool_calls"] == 0
    assert limits["max_program_calls"] == 0


def test_overrides_are_applied():
    task = _build(
        expected_outputs=["revenue_analysis", "risk_assessment", "recommendation"],
        active_core_rule_ids=["MVP_RULE_005"],
        data_sensitivity="PRIVATE",
    )
    assert task["scope"]["expected_outputs"] == ["revenue_analysis", "risk_assessment", "recommendation"]
    assert task["context"]["active_core_rule_ids"] == ["MVP_RULE_005"]
    assert task["context"]["data_sensitivity"] == "PRIVATE"
    schema_validation.validate_against_schema(task, TASK_SCHEMA, "test")


# --- fail-closed BLOCK cases ------------------------------------------------

@pytest.mark.parametrize("bad", ["", "   ", None])
def test_empty_request_blocks(bad):
    with pytest.raises(TaskIntakeBlocked) as exc:
        _build(raw_request=bad)
    assert exc.value.reason_code == "EMPTY_REQUEST"


def test_missing_requester_blocks():
    with pytest.raises(TaskIntakeBlocked) as exc:
        _build(requester_id="")
    assert exc.value.reason_code == "MISSING_REQUESTER"


def test_invalid_channel_blocks():
    with pytest.raises(TaskIntakeBlocked) as exc:
        _build(channel="carrier_pigeon")
    assert exc.value.reason_code == "INVALID_CHANNEL"


def test_invalid_requester_type_blocks():
    with pytest.raises(TaskIntakeBlocked) as exc:
        _build(requester_type="anonymous")
    assert exc.value.reason_code == "INVALID_REQUESTER_TYPE"


def test_invalid_sensitivity_blocks():
    with pytest.raises(TaskIntakeBlocked) as exc:
        _build(data_sensitivity="TOP_SECRET")
    assert exc.value.reason_code == "INVALID_SENSITIVITY"


def test_duplicate_core_rules_block():
    with pytest.raises(TaskIntakeBlocked) as exc:
        _build(active_core_rule_ids=["MVP_RULE_005", "MVP_RULE_005"])
    assert exc.value.reason_code == "DUPLICATE_CORE_RULES"


def test_empty_core_rules_block():
    with pytest.raises(TaskIntakeBlocked) as exc:
        _build(active_core_rule_ids=[])
    assert exc.value.reason_code == "MISSING_CORE_RULES"


def test_malformed_core_rule_id_blocks_via_schema():
    # Wrong pattern (schema requires ^MVP_RULE_\d{3}$) -> fail closed at schema.
    with pytest.raises(TaskIntakeBlocked) as exc:
        _build(active_core_rule_ids=["RULE_5"])
    assert exc.value.reason_code == "SCHEMA_INVALID"


def test_non_utf8_encodable_request_blocks():
    # A lone surrogate cannot be UTF-8 encoded -> fail closed at the front door.
    with pytest.raises(TaskIntakeBlocked) as exc:
        _build(raw_request="bad \udcbf surrogate")
    assert exc.value.reason_code == "INVALID_ENCODING"


def test_invalid_list_item_blocks():
    with pytest.raises(TaskIntakeBlocked) as exc:
        _build(success_conditions=[123])
    assert exc.value.reason_code == "INVALID_LIST_ITEM"


def test_list_passed_as_string_blocks():
    with pytest.raises(TaskIntakeBlocked) as exc:
        _build(success_conditions="a single string, not a list")
    assert exc.value.reason_code == "INVALID_LIST"


def test_unhashable_core_rule_item_fails_closed_not_typeerror():
    # Regression: active_core_rule_ids with an unhashable element used to raise a
    # raw TypeError at set(); it must fail closed with a precise BLOCK code.
    with pytest.raises(TaskIntakeBlocked) as exc:
        _build(active_core_rule_ids=[{"a": 1}])
    assert exc.value.reason_code == "INVALID_LIST_ITEM"


def test_unhashable_core_rule_list_element_list_fails_closed():
    with pytest.raises(TaskIntakeBlocked) as exc:
        _build(active_core_rule_ids=[["nested"]])
    assert exc.value.reason_code == "INVALID_LIST_ITEM"


def test_invalid_timestamp_blocks():
    with pytest.raises(TaskIntakeBlocked) as exc:
        _build(now="not-a-real-date")
    assert exc.value.reason_code == "INVALID_TIMESTAMP"


def test_valid_timestamp_variants_accepted():
    for good in ("2026-07-15T09:00:00Z", "2026-07-15T09:00:00+00:00", "2026-07-15T09:00:00.500Z"):
        task = _build(now=good)
        schema_validation.validate_against_schema(task, TASK_SCHEMA, "test")


@pytest.mark.parametrize("bad", ["yes", "false", 1, 0, None])
def test_non_bool_authenticated_blocks(bad):
    with pytest.raises(TaskIntakeBlocked) as exc:
        _build(authenticated=bad)
    assert exc.value.reason_code == "INVALID_AUTHENTICATED"


@pytest.mark.parametrize("ctrl", ["\x00", "\x07", "\x1b", "\x7f"])
def test_control_chars_block(ctrl):
    with pytest.raises(TaskIntakeBlocked) as exc:
        _build(raw_request=f"idea {ctrl} here")
    assert exc.value.reason_code == "CONTROL_CHARS"


def test_tab_and_newline_are_allowed():
    task = _build(raw_request="line one\nline two\twith tab")
    schema_validation.validate_against_schema(task, TASK_SCHEMA, "test")


def test_overlong_request_blocks():
    with pytest.raises(TaskIntakeBlocked) as exc:
        _build(raw_request="x" * 20001)
    assert exc.value.reason_code == "TOO_LONG"


def test_too_many_list_items_blocks():
    with pytest.raises(TaskIntakeBlocked) as exc:
        _build(expected_outputs=[f"out_{i}" for i in range(65)])
    assert exc.value.reason_code == "TOO_MANY_ITEMS"


def test_missing_schema_reports_unavailable_not_invalid(tmp_path):
    with pytest.raises(TaskIntakeBlocked) as exc:
        _build(repo_root=tmp_path)  # empty dir -> no schemas/
    assert exc.value.reason_code == "SCHEMA_UNAVAILABLE"


def test_secret_looking_text_is_treated_as_data_not_a_key():
    # The integrity scan forbids secret-bearing KEYS. Intake keys are fixed, so
    # user text (a value) containing "api_key" is data and must pass through — the
    # record still validates. This documents that intake never invents free-form keys.
    task = _build(raw_request="check my api_key=abc123 please", constraints=["api_secret in body ok"])
    schema_validation.validate_against_schema(task, TASK_SCHEMA, "test")
    assert "api_key" in task["request"]["raw_request"]

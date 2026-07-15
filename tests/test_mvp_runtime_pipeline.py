"""R2.7 Single-Agent End-to-End pipeline tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime.mvp_runtime.binding import DEFAULT_POINTER_REL
from runtime.mvp_runtime.errors import ProviderError
from runtime.mvp_runtime.pipeline import run_task
from runtime.mvp_runtime.worker import MockProvider

LOCAL_POINTER = Path(__file__).resolve().parents[1] / DEFAULT_POINTER_REL
NOW = "2026-07-15T09:00:00Z"
REQUEST = "이 사업 아이디어를 분석해줘: 구독형 반려동물 사료 배송"

requires_local_core = pytest.mark.skipif(not LOCAL_POINTER.is_file(), reason="no local Core activation")


class _ErrorProvider:
    model_id, model_version = "err", "0.1.0"

    def generate(self, prompt, *, max_output_tokens, timeout_seconds):
        raise ProviderError("BOOM", "provider exploded")


class _OverconfidentProvider(MockProvider):
    def generate(self, prompt, *, max_output_tokens, timeout_seconds):
        r = super().generate(prompt, max_output_tokens=max_output_tokens, timeout_seconds=timeout_seconds)
        r.analysis = {**r.analysis, "uncertainty": [], "assumptions": []}
        return r


# --- fail-closed without a Core (run everywhere) ----------------------------

def test_empty_request_blocks():
    r = run_task("", now=NOW)
    assert r["status"] == "BLOCKED" and r["delivered"] is False
    assert r["block"]["reason_code"] == "EMPTY_REQUEST"


def test_out_of_scope_blocks_before_binding():
    r = run_task("분석해줘", now=NOW, constraints=["something_else"])
    assert r["status"] == "BLOCKED"
    assert r["block"]["reason_code"] == "OUT_OF_MVP_SCOPE"


# --- full run (needs a Core) ------------------------------------------------

@requires_local_core
def test_normal_task_completes_and_delivers():
    r = run_task(REQUEST, provider=MockProvider(), now=NOW)
    assert r["status"] == "COMPLETED" and r["delivered"] is True
    assert isinstance(r["final_response"], str) and "Key findings" in r["final_response"]
    rec = r["records"]
    for key in ("received_task", "task", "binding", "permission_decision", "role_assignment",
                "agent_output", "invocation", "validation_result", "audit_trail"):
        assert key in rec
    assert rec["agent_output"]["status"] == "needs_validation"
    assert rec["validation_result"]["validation"]["result"] == "PASS"
    assert len(rec["audit_trail"]) == 5  # + MODEL_INVOKED
    assert [e["event_type"] for e in rec["audit_trail"]] == [
        "TASK_CREATED", "PERMISSION_DECIDED", "OTHER", "VALIDATION_COMPLETED", "TASK_STATE_CHANGED"
    ]


@requires_local_core
def test_common_safety_invariants_hold_on_completed_run():
    rec = run_task(REQUEST, provider=MockProvider(), now=NOW)["records"]
    a = rec["role_assignment"]
    assert a["allowed_tool_ids"] == [] and a["allowed_program_ids"] == []
    limits = a["execution_budget"]["limits"]
    assert limits["max_tool_calls"] == 0 and limits["max_program_calls"] == 0
    # ALLOW is not an executor token; nothing is granted/executed.
    assert rec["permission_decision"]["decision"]["permission_decision"] == "ALLOW"
    assert rec["permission_decision"]["runtime_effect"]["mode"] == "REVIEW_ONLY"
    assert rec["validation_result"]["runtime_effect"]["mode"] == "REVIEW_ONLY"
    assert all(e["runtime_effect"]["mode"] == "EVIDENCE_ONLY" for e in rec["audit_trail"])


@requires_local_core
def test_provider_error_blocks_the_run():
    r = run_task(REQUEST, provider=_ErrorProvider(), now=NOW)
    assert r["status"] == "BLOCKED"
    assert r["block"]["reason_code"] == "PROVIDER_ERROR"


@requires_local_core
def test_revise_validation_withholds_delivery():
    r = run_task(REQUEST, provider=_OverconfidentProvider(), now=NOW)
    assert r["status"] == "BLOCKED" and r["delivered"] is False
    assert r["block"]["reason_code"] == "VALIDATION_REVISE"


@requires_local_core
def test_recorded_replay_determinism():
    a = run_task(REQUEST, provider=MockProvider(), now=NOW)
    b = run_task(REQUEST, provider=MockProvider(), now=NOW)
    assert a == b

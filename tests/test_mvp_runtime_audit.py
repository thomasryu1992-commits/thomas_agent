"""R2.6 Audit tests — hash-chained, append-only, evidence-only."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from runtime.mvp_runtime.audit import build_pipeline_audit
from runtime.mvp_runtime.binding import DEFAULT_POINTER_REL
from runtime.mvp_runtime.intake import build_task
from runtime.mvp_runtime.prime import plan_task
from runtime.mvp_runtime.validation import validate_agent_output
from runtime.mvp_runtime.worker import MockProvider, run_analysis_worker
from runtime.read_only_kernel import integrity, schema_validation

REPO_ROOT = Path(__file__).resolve().parents[1]
LOCAL_POINTER = REPO_ROOT / DEFAULT_POINTER_REL
AUDIT_SCHEMA = REPO_ROOT / "schemas" / "audit_event.v0.1.schema.json"
NOW = "2026-07-15T09:00:00Z"

requires_local_core = pytest.mark.skipif(not LOCAL_POINTER.is_file(), reason="no local Core activation")


def _run(validation_transform=None):
    task = build_task("이 사업 아이디어를 분석해줘: 구독형 반려동물 사료 배송", now=NOW)
    plan = plan_task(task, now=NOW)
    out, inv = run_analysis_worker(plan["task"], plan["role_assignment"], provider=MockProvider(), created_at=NOW)
    if validation_transform:
        out = validation_transform(out)
    vr = validate_agent_output(out, plan["task"], plan["role_assignment"], now=NOW)
    chain = build_pipeline_audit(plan["task"], plan["permission_decision"], vr, out, inv, now=NOW)
    return chain, vr


@requires_local_core
def test_chain_shape_and_schema():
    chain, _ = _run()
    assert [e["event_type"] for e in chain] == [
        "TASK_CREATED", "PERMISSION_DECIDED", "OTHER", "VALIDATION_COMPLETED", "TASK_STATE_CHANGED"
    ]
    for i, e in enumerate(chain, start=1):
        schema_validation.validate_against_schema(e, AUDIT_SCHEMA, "test")
        assert e["lineage"]["sequence_number"] == i
        assert e["integrity"]["append_only"] is True
        assert e["integrity"]["overwrite_allowed"] is False and e["integrity"]["delete_allowed"] is False
        assert e["runtime_effect"]["mode"] == "EVIDENCE_ONLY"
        assert all(v is False for k, v in e["runtime_effect"].items() if k != "mode")


@requires_local_core
def test_model_invocation_is_audited():
    chain, _ = _run()
    model_event = chain[2]
    assert model_event["event_type"] == "OTHER"
    assert "MODEL_INVOKED" in model_event["event"]["reason_codes"]
    assert "NO_NETWORK_EGRESS" in model_event["event"]["reason_codes"]  # MockProvider is in-process
    assert model_event["event"]["payload_sha256"].startswith("sha256:")  # fingerprinted invocation metadata
    assert model_event["actor"]["actor_type"] == "role"


@requires_local_core
def test_hash_chain_links_and_is_honest():
    chain, _ = _run()
    assert chain[0]["lineage"]["previous_event_sha256"] is None
    assert chain[0]["lineage"]["parent_audit_event_ids"] == []
    for i in range(1, len(chain)):
        assert chain[i]["lineage"]["previous_event_sha256"] == chain[i - 1]["integrity"]["event_sha256"]
        assert chain[i]["lineage"]["parent_audit_event_ids"] == [chain[i - 1]["audit_event_id"]]
    # Each event_sha256 honestly hashes its own fingerprint payload (tamper-evident).
    for e in chain:
        assert e["integrity"]["event_sha256"] == integrity.sha256_value(e["integrity"]["event_fingerprint_payload"])


@requires_local_core
def test_deterministic():
    a, _ = _run()
    b, _ = _run()
    assert a == b


@requires_local_core
def test_pass_run_concludes_completed():
    chain, vr = _run()
    assert vr["validation"]["result"] == "PASS"
    assert chain[3]["event"]["outcome"] == "PASS"          # VALIDATION_COMPLETED
    assert chain[4]["event"]["outcome"] == "RECORDED"      # TASK_STATE_CHANGED
    assert "FINAL_COMPLETED" in chain[4]["event"]["reason_codes"]


@requires_local_core
def test_blocked_validation_concludes_blocked():
    # Tamper the output lineage so validation BLOCKs; the trail must conclude BLOCKED.
    def break_lineage(out):
        out = deepcopy(out)
        out["assignment_id"] = "assignment_wrong"
        return out

    chain, vr = _run(break_lineage)
    assert vr["validation"]["result"] == "BLOCK"
    assert chain[3]["event"]["outcome"] == "BLOCKED"  # VALIDATION_COMPLETED (enum uses BLOCKED)
    assert chain[4]["event"]["outcome"] == "BLOCKED"  # TASK_STATE_CHANGED
    assert "FINAL_BLOCKED" in chain[4]["event"]["reason_codes"]

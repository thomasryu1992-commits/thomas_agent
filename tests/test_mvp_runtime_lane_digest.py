"""The per-lane digest (system review B11): what each lane's runs did, read off the ledger."""

from __future__ import annotations

import json

from runtime.mvp_runtime import lane_digest
from tests._helpers import requires_local_core

SINCE = "2026-09-18T00:00:00Z"


def _run(trace, *, role="general.specialist", received="2026-09-20T09:00:00Z", result="PASS",
         independent=None, revised=False, model="openrouter/x", latency=1000, failing=(), unverified=False,
         failovers=None):
    checks = [{"check_id": c, "result": "REVISE"} for c in failing]
    invocation = {"model_id": model, "latency_ms": latency}
    if failovers is not None:
        invocation["failovers"] = failovers
    rows = [
        {"kind": "task", "trace_id": trace, "record": {"request": {"received_at": received}}},
        {"kind": "role_assignment", "trace_id": trace, "record": {"role_id": role}},
        {"kind": "invocation", "trace_id": trace, "record": invocation},
    ]
    if result is not None:
        rows.append({"kind": "validation_result", "trace_id": trace,
                     "record": {"validation": {"result": result, "checks": checks}}})
    if independent is not None:
        rows.append({"kind": "independent_validation_result", "trace_id": trace,
                     "record": {"validation": {"result": independent}}})
    if revised:
        rows.append({"kind": "revision", "trace_id": trace, "record": {}})
    if unverified:
        rows.append({"kind": "delivery", "trace_id": trace,
                     "record": {"verification": "DELIVERED_UNVERIFIED", "reasons": ["x"]}})
    return rows


def test_outcomes_are_folded_per_lane_with_the_stricter_verdict_final():
    rows = [
        *_run("t1"),
        *_run("t2", result="REVISE", failing=("evidence_grounding",), revised=True, latency=3000),
        *_run("t3", result="PASS", independent="BLOCK", model="groq/y"),     # the reviewer's BLOCK wins
        *_run("t4", result=None),                                            # stopped before validation
        *_run("t5", role="content.general"),
        *_run("t6", received="2026-09-01T00:00:00Z"),                        # before the window
        {"kind": "crypto_cycle", "trace_id": "t7", "record": {}},            # not a run
    ]
    digest = lane_digest.fold_runs(rows, since=SINCE)
    analyst = digest["general.specialist"]
    assert (analyst["runs"], analyst["delivered"], analyst["revise"], analyst["block"],
            analyst["stopped"], analyst["revised"]) == (4, 1, 1, 1, 1, 1)
    assert analyst["non_pass_checks"] == {"evidence_grounding": 1}
    assert analyst["models"] == {"openrouter/x": 3, "groq/y": 1}
    assert analyst["latency_ms_p50"] == 1000 and analyst["latency_ms_p95"] == 3000
    assert digest["content.general"]["runs"] == 1
    rendered = lane_digest.render(digest, since=SINCE)
    assert rendered.index("[general.specialist]") < rendered.index("[content.general]")
    assert "보류 사유: evidence_grounding 1" in rendered


def test_a_run_delivered_unverified_is_counted_apart_from_delivered_and_withheld():
    """Review D2: a REVISE business analysis is delivered under a banner, and a PASS whose reviewer
    was down is delivered unverified. Neither is "delivered" (verified) nor "withheld"."""
    rows = [*_run("t1"), *_run("t2", result="REVISE", unverified=True),
            *_run("t3", result="PASS", unverified=True), *_run("t4", result="REVISE")]
    analyst = lane_digest.fold_runs(rows, since=SINCE)["general.specialist"]
    assert (analyst["delivered"], analyst["unverified"], analyst["revise"]) == (1, 2, 1)
    assert "미검증 전달 2" in lane_digest.render({"general.specialist": analyst}, since=SINCE)


def test_failovers_are_counted_by_member_and_kind_and_a_configuration_fault_is_flagged():
    """Review D1's condition: a member whose key or slug is wrong must not hide behind the member
    that answered. The weekly digest is where that shows up without anyone reading the ledger."""
    keyless = {"member": "openrouter", "kind": "configuration", "reason_code": "NO_API_KEY", "reason": "x"}
    busy = {"member": "openrouter", "kind": "unavailable", "reason_code": "PROVIDER_UNAVAILABLE", "reason": "y"}
    rows = [*_run("t1", model="groq", failovers=[keyless]), *_run("t2", model="groq", failovers=[keyless]),
            *_run("t3", model="google_ai_studio", failovers=[busy]), *_run("t4")]
    analyst = lane_digest.fold_runs(rows, since=SINCE)["general.specialist"]
    assert analyst["failovers"] == {"openrouter configuration": 2, "openrouter unavailable": 1}
    rendered = lane_digest.render({"general.specialist": analyst}, since=SINCE)
    assert "페일오버: openrouter configuration 2, openrouter unavailable 1 — 설정 오류 의심" in rendered

    quiet = lane_digest.fold_runs([*_run("t5", failovers=[busy])], since=SINCE)
    assert "설정 오류 의심" not in lane_digest.render(quiet, since=SINCE)
    assert "페일오버" not in lane_digest.render(lane_digest.fold_runs(_run("t6"), since=SINCE), since=SINCE)


def test_an_empty_window_says_so():
    assert lane_digest.render(lane_digest.fold_runs([], since=SINCE), since=SINCE).endswith(
        "기간 안에 실행된 요청이 없습니다.")


@requires_local_core
def test_a_real_run_on_the_ledger_is_counted_and_the_script_reads_it(tmp_path, capsys):
    """End to end over what `run_task` actually writes, archives included, through the operator
    script — so a renamed record kind shows up here rather than as a silently empty digest."""
    from runtime.mvp_runtime import retention
    from runtime.mvp_runtime.pipeline import run_task
    from runtime.mvp_runtime.store import RECORDS_FILE, LedgerStore
    from runtime.mvp_runtime.worker import MockProvider
    from scripts import lane_digest as script

    ledger = LedgerStore.default(tmp_path)
    run_task("이 사업 아이디어를 분석해줘: 구독형 반려동물 사료 배송", provider=MockProvider(),
             store=ledger, now="2026-09-20T09:00:00Z")
    retention.rotate_file(ledger, RECORDS_FILE, keep_rows=1, now="2026-09-21T00:00:00Z")

    assert script.main(["--json"], root=tmp_path, now="2026-09-25T00:00:00Z") == 0
    lanes = json.loads(capsys.readouterr().out)["lanes"]
    assert lanes["general.specialist"]["runs"] == 1
    assert lanes["general.specialist"]["delivered"] == 1
    assert lanes["general.specialist"]["models"] == {"mock.analysis": 1}

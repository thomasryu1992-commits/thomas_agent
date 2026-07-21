"""R7 Independent Validation Agent tests.

Unit tests drive ``run_validation_worker`` with hand-built records and fake providers (no
Core, no network). Planning/E2E tests that bind to a Core are marked ``requires_local_core``
like the other pipeline tests (they run locally and in CI's ephemeral-Core job).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime.mvp_runtime.binding import DEFAULT_POINTER_REL
from runtime.mvp_runtime.errors import WorkerBlocked
from runtime.mvp_runtime.intake import build_task
from runtime.mvp_runtime.pipeline import run_task
from runtime.mvp_runtime.planner import (
    VALIDATOR_REQUIRED_CAPABILITIES,
    VALIDATOR_REQUIRED_PERMISSION_LEVEL,
    load_resolved_roles,
    select_role,
)
from runtime.mvp_runtime.prime import plan_task
from runtime.mvp_runtime.validator import (
    MockValidatorProvider,
    build_validator_prompt,
    run_validation_worker,
    stricter_result,
)
from runtime.mvp_runtime.worker import ProviderResult

from tests._helpers import requires_local_core

NOW = "2026-07-16T09:00:00Z"
REQUEST = "이 사업 아이디어를 분석해줘: 구독형 반려동물 사료"


class FakeVerdictProvider:
    """Returns a fixed verdict (or an arbitrary analysis payload) in the shared shape."""

    model_id = "fake.validation"
    model_version = "0.0.1"
    network_egress = False

    def __init__(self, verdict="PASS", *, analysis=None, input_tokens=10, output_tokens=10):
        self._verdict = verdict
        self._analysis = analysis
        self._in, self._out = input_tokens, output_tokens

    def generate(self, prompt, *, max_output_tokens, timeout_seconds):
        analysis = self._analysis if self._analysis is not None else {
            "summary": "review", "key_findings": ["finding-1"],
            "facts": [{"statement": "reviewed", "evidence_refs": ["model:validation"]}],
            "inferences": [], "assumptions": [], "uncertainty": [],
            "risks": ["remaining-risk"] if self._verdict != "PASS" else [],
            "recommendation": {"action": self._verdict, "reason": f"verdict {self._verdict}"},
            "limitations": [], "next_actions": ["fix X"] if self._verdict == "REVISE" else [],
            "evidence_quality": "ok", "unresolved_questions": [],
        }
        return ProviderResult(analysis=analysis, model_id=self.model_id, model_version=self.model_version,
                              input_tokens=self._in, output_tokens=self._out, latency_ms=0)


def _fake_task():
    return {
        "identity": {"task_id": "task_x", "trace_id": "trace_x", "task_revision": 1},
        "context": {"core_context_binding_id": "ccb-fake"},
        "classification": {"risk_level": "GREEN"},
        "scope": {"primary_objective": "analyze the idea"},
        "request": {"raw_request": REQUEST},
    }


def _fake_validator_assignment(**overrides):
    a = {
        "assignment_id": "assignment_validator", "actor_instance_id": "agent_validator",
        "role_id": "validation.independent", "role_version": "0.3.0",
        "execution_budget": {"limits": {"max_model_calls": 1, "token_budget": 8000, "max_runtime_seconds": 120}},
    }
    a.update(overrides)
    return a


def _fake_output():
    return {
        "agent_output_id": "agentout_x", "actor_instance_id": "agent_specialist",
        "assignment_id": "assignment_specialist", "role_id": "general.specialist",
        "summary": "the analysis", "facts": [{"statement": "f1", "evidence_refs": ["model:analysis"]}],
        "inferences": [{"statement": "i1"}], "assumptions": ["a1"], "uncertainty": ["u1"],
        "risks": ["r1"], "limitations": ["l1"],
        "recommendation": {"action": "validate first", "reason": "CAC unknown"},
        "role_specific_output": {"key_findings": ["kf1"]},
    }


# --- stricter_result ---------------------------------------------------------

@pytest.mark.parametrize("a, b, expected", [
    ("PASS", "PASS", "PASS"), ("PASS", "REVISE", "REVISE"), ("REVISE", "PASS", "REVISE"),
    ("PASS", "BLOCK", "BLOCK"), ("REVISE", "BLOCK", "BLOCK"), ("BLOCK", "PASS", "BLOCK"),
    ("PASS", "bogus", "BLOCK"), ("", "PASS", "BLOCK"),
])
def test_stricter_result(a, b, expected):
    assert stricter_result(a, b) == expected


# --- role selection ----------------------------------------------------------

def test_validator_capabilities_select_validation_independent():
    resolved = load_resolved_roles()
    role = select_role(
        resolved,
        required_capabilities=VALIDATOR_REQUIRED_CAPABILITIES,
        required_permission_level=VALIDATOR_REQUIRED_PERMISSION_LEVEL,
    )
    assert role["role_id"] == "validation.independent"
    assert role["permission_ceiling"] == "P2"


# --- validator worker (unit; no Core) -----------------------------------------

def test_mock_validator_passes():
    record, invocation = run_validation_worker(
        _fake_task(), _fake_validator_assignment(), _fake_output(),
        provider=MockValidatorProvider(), created_at=NOW,
    )
    validation = record["validation"]
    assert validation["validation_mode"] == "INDEPENDENT"
    assert validation["result"] == "PASS"
    assert record["validator"]["validator_type"] == "ROLE"
    assert record["validator"]["validator_role_id"] == "validation.independent"
    assert record["validator"]["independence_verified"] is True
    assert invocation["model_id"] == "mock.validation"
    assert invocation["network_egress"] is False


@pytest.mark.parametrize("verdict", ["PASS", "REVISE", "BLOCK"])
def test_verdict_mapping(verdict):
    record, _ = run_validation_worker(
        _fake_task(), _fake_validator_assignment(), _fake_output(),
        provider=FakeVerdictProvider(verdict), created_at=NOW,
    )
    assert record["validation"]["result"] == verdict


def test_lowercase_verdict_normalized():
    record, _ = run_validation_worker(
        _fake_task(), _fake_validator_assignment(), _fake_output(),
        provider=FakeVerdictProvider(analysis={"recommendation": {"action": " pass ", "reason": "ok"},
                                               "key_findings": [], "facts": []}), created_at=NOW,
    )
    assert record["validation"]["result"] == "PASS"


def test_unparseable_verdict_fails_closed_to_block():
    record, _ = run_validation_worker(
        _fake_task(), _fake_validator_assignment(), _fake_output(),
        provider=FakeVerdictProvider(analysis={"summary": "no verdict here", "key_findings": [], "facts": []}),
        created_at=NOW,
    )
    assert record["validation"]["result"] == "BLOCK"
    assert any(c["check_id"] == "verdict_parseable" and c["result"] == "BLOCK"
               for c in record["validation"]["checks"])


def test_revise_carries_required_revisions_in_reasons():
    record, _ = run_validation_worker(
        _fake_task(), _fake_validator_assignment(), _fake_output(),
        provider=FakeVerdictProvider("REVISE"), created_at=NOW,
    )
    assert record["validation"]["result"] == "REVISE"
    assert "fix X" in record["validation"]["result_reasons"]
    assert record["validation"]["recommended_next_state"] == "REVISION_REQUIRED"


def test_same_role_as_creator_is_not_independent():
    with pytest.raises(WorkerBlocked) as exc:
        run_validation_worker(
            _fake_task(), _fake_validator_assignment(role_id="general.specialist"), _fake_output(),
            provider=MockValidatorProvider(), created_at=NOW,
        )
    assert exc.value.reason_code == "NOT_INDEPENDENT"


def test_same_actor_is_recorded_not_verified():
    record, _ = run_validation_worker(
        _fake_task(), _fake_validator_assignment(actor_instance_id="agent_specialist"), _fake_output(),
        provider=MockValidatorProvider(), created_at=NOW,
    )
    assert record["validator"]["independence_verified"] is False


def test_no_model_budget_fails_closed():
    assignment = _fake_validator_assignment(execution_budget={"limits": {"max_model_calls": 0}})
    with pytest.raises(WorkerBlocked) as exc:
        run_validation_worker(_fake_task(), assignment, _fake_output(),
                              provider=MockValidatorProvider(), created_at=NOW)
    assert exc.value.reason_code == "NO_MODEL_BUDGET"


def test_token_budget_breach_fails_closed():
    with pytest.raises(WorkerBlocked) as exc:
        run_validation_worker(
            _fake_task(), _fake_validator_assignment(), _fake_output(),
            provider=FakeVerdictProvider("PASS", input_tokens=9000, output_tokens=9000), created_at=NOW,
        )
    assert exc.value.reason_code == "TOKEN_BUDGET_EXCEEDED"


def test_prompt_reviews_output_not_specialist_context():
    prompt = build_validator_prompt(_fake_task(), _fake_output())
    assert "OUTPUT UNDER REVIEW" in prompt
    assert REQUEST in prompt
    assert "kf1" in prompt and "f1" in prompt
    # The validator sees goal/input/result — never the specialist's search/memory context.
    assert "search results" not in prompt.lower()
    assert "working memory" not in prompt.lower()


def test_prompt_states_the_calibrated_bar():
    """The v2 calibration exists because the live reviewer REVISE-ratcheted three faithful
    revisions in a row: without an explicit bar, "improvable" reads as "defective" and
    important requests are exactly the ones never delivered. The bar must be in the
    prompt, stated as PASS-by-default with REVISE reserved for material defects."""
    prompt = build_validator_prompt(_fake_task(), _fake_output())
    assert "Acceptance criteria" in prompt
    assert "PASS is the default" in prompt
    assert "material defects" in prompt
    assert "belong in risks alongside a PASS" in prompt


# --- planning + E2E (need a local Core) ---------------------------------------

@requires_local_core
def test_plan_task_plans_the_validator_team():
    task = build_task(REQUEST, now=NOW)
    plan = plan_task(task, now=NOW, independent_validation=True)
    va = plan["validator_assignment"]
    sa = plan["role_assignment"]
    assert va["role_id"] == "validation.independent" and sa["role_id"] == "general.specialist"
    assert va["actor_instance_id"] != sa["actor_instance_id"]
    assert va["assignment_id"] != sa["assignment_id"]
    assert plan["validator_permission_decision"]["permission_decision_id"] != \
        plan["permission_decision"]["permission_decision_id"]
    routing = plan["task"]["routing"]
    assert routing["assigned_role_ids"] == ["general.specialist", "validation.independent"]
    assert len(routing["role_assignment_ids"]) == 2


@requires_local_core
def test_plan_task_without_flag_is_unchanged():
    task = build_task(REQUEST, now=NOW)
    plan = plan_task(task, now=NOW)
    assert "validator_assignment" not in plan
    assert "triage_permission_decision" not in plan
    assert plan["task"]["routing"]["assigned_role_ids"] == ["general.specialist"]


@requires_local_core
def test_plan_task_auto_plans_triage_with_a_distinct_decision():
    """R7.2: under "auto" on a GREEN/NORMAL task, Prime plans the triage as its own
    governed action — a PermissionDecision distinct from every other grant in the plan
    (the specialist's shares its INTERNAL_ANALYSIS scope; the action_type separates them)."""
    task = build_task(REQUEST, now=NOW, planned_triage_calls=1, planned_agents=2)
    plan = plan_task(task, now=NOW, independent_validation="auto")
    triage_permdec = plan["triage_permission_decision"]
    assert triage_permdec["decision"]["permission_decision"] == "ALLOW"
    ids = {
        plan["permission_decision"]["permission_decision_id"],
        plan["search_permission_decision"]["permission_decision_id"],
        plan["validator_permission_decision"]["permission_decision_id"],
        triage_permdec["permission_decision_id"],
    }
    assert len(ids) == 4
    # The team is planned under "auto" too — running it is the pipeline's decision.
    assert "validator_assignment" in plan


@requires_local_core
def test_plan_task_auto_plans_no_triage_when_already_required():
    """An important intake priority already decides the review — no triage is owed."""
    task = build_task(REQUEST, now=NOW, priority="HIGH", planned_agents=2)
    plan = plan_task(task, now=NOW, independent_validation="auto")
    assert "triage_permission_decision" not in plan
    assert "validator_assignment" in plan


@requires_local_core
def test_e2e_independent_validation_pass_delivers():
    result = run_task(REQUEST, independent_validation=True, now=NOW)
    assert result["status"] == "COMPLETED" and result["delivered"] is True
    ival = result["records"]["independent_validation_result"]
    assert ival["validation"]["validation_mode"] == "INDEPENDENT"
    assert ival["validation"]["result"] == "PASS"
    assert result["records"]["validator_invocation"]["model_id"] == "mock.validation"
    # The audit trail carries both validation events + the validator's model call.
    events = result["records"]["audit_trail"]
    validation_events = [e for e in events if e["event_type"] == "VALIDATION_COMPLETED"]
    assert len(validation_events) == 2


@requires_local_core
def test_e2e_independent_revise_withholds_delivery():
    result = run_task(REQUEST, independent_validation=True,
                      validator_provider=FakeVerdictProvider("REVISE"), now=NOW)
    assert result["status"] == "BLOCKED" and result["delivered"] is False
    assert result["block"]["reason_code"] == "VALIDATION_REVISE"
    assert "fix X" in result["block"]["message"]


@requires_local_core
def test_e2e_independent_block_withholds_delivery():
    result = run_task(REQUEST, independent_validation=True,
                      validator_provider=FakeVerdictProvider("BLOCK"), now=NOW)
    assert result["status"] == "BLOCKED"
    assert result["block"]["reason_code"] == "VALIDATION_BLOCK"


@requires_local_core
def test_e2e_default_off_has_no_validator_records():
    result = run_task(REQUEST, now=NOW)
    assert result["status"] == "COMPLETED"
    assert "independent_validation_result" not in result["records"]
    assert "validator_assignment" not in result["records"]
    events = result["records"]["audit_trail"]
    assert len([e for e in events if e["event_type"] == "VALIDATION_COMPLETED"]) == 1


# --- R7.1: selective ("auto") validation --------------------------------------


def test_independent_validation_required_truth_table():
    from runtime.mvp_runtime.validation import independent_validation_required

    assert independent_validation_required("NORMAL", "GREEN") is False
    assert independent_validation_required("LOW", "GREEN") is False
    assert independent_validation_required("HIGH", "GREEN") is True     # operator-marked
    assert independent_validation_required("URGENT", "GREEN") is True
    assert independent_validation_required("NORMAL", "ORANGE") is True  # policy §3.4
    assert independent_validation_required("NORMAL", "RED") is True
    # Unknown values match neither trigger — they never turn the reviewer ON by accident.
    assert independent_validation_required(None, None) is False


@requires_local_core
def test_auto_policy_triage_normal_skips_the_planned_reviewer():
    """R7.2: on a GREEN/NORMAL run the orchestrator triage judges (mock: NORMAL) and the
    planned reviewer does not run — one agent model call, plus the small triage call."""
    result = run_task(REQUEST, independent_validation="auto", now=NOW)
    assert result["status"] == "COMPLETED"
    assert "independent_validation_result" not in result["records"]
    # The team is PLANNED (buildable) either way; the triage decides what RUNS.
    assert "validator_assignment" in result["records"]
    triage = result["records"]["triage_result"]
    assert triage["verdict"] == "NORMAL" and triage["degraded"] is False
    assert result["records"]["triage_invocation"]["model_id"] == "mock.triage"
    assert "triage_permission_decision" in result["records"]
    events = result["records"]["audit_trail"]
    assert len([e for e in events if e["event_type"] == "VALIDATION_COMPLETED"]) == 1
    triage_events = [e for e in events if "TRIAGE" in e["event"]["reason_codes"]]
    assert len(triage_events) == 1 and "NORMAL" in triage_events[0]["event"]["reason_codes"]
    # Budget honesty: specialist + triage = 2 model calls, but only 1 agent invocation.
    usage = result["records"]["budget_usage"]["usage"]
    assert usage["model_calls"] == 2 and usage["agent_invocations"] == 1


@requires_local_core
def test_auto_policy_validates_an_important_request():
    """priority HIGH (the operator's importance marker) adds the reviewer to this run —
    and the record still says the GOVERNANCE requirement was false (operator-requested,
    not risk-mandated)."""
    result = run_task(REQUEST, independent_validation="auto", priority="HIGH", now=NOW)
    assert result["status"] == "COMPLETED"
    assert result["records"]["task"]["classification"]["priority"] == "HIGH"
    ival = result["records"]["independent_validation_result"]
    assert ival["validation"]["validation_mode"] == "INDEPENDENT"
    assert ival["validator"]["independent_required"] is False
    # The classification already decided — no triage call is owed, none is planned.
    assert "triage_result" not in result["records"]
    assert "triage_permission_decision" not in result["records"]
    events = result["records"]["audit_trail"]
    assert len([e for e in events if e["event_type"] == "VALIDATION_COMPLETED"]) == 2


@requires_local_core
def test_auto_policy_validates_when_risk_requires_it(monkeypatch):
    """ORANGE/RED classification mandates the reviewer (policy §3.4) with no marker."""
    import runtime.mvp_runtime.planner as planner_mod

    monkeypatch.setattr(planner_mod, "MVP_RISK_LEVEL", "ORANGE")
    result = run_task(REQUEST, independent_validation="auto", now=NOW)
    assert result["status"] == "COMPLETED"
    ival = result["records"]["independent_validation_result"]
    assert ival["validator"]["independent_required"] is True


class _SequencedProvider:
    """Answers each successive call with the next fixed verdict — the triage and the
    review share one provider, so their answers must be independently controllable."""

    model_id = "fake.sequence"
    model_version = "0.0.1"
    network_egress = False

    def __init__(self, actions):
        self._actions = list(actions)

    def generate(self, prompt, *, max_output_tokens, timeout_seconds):
        action = self._actions.pop(0)
        analysis = {
            "summary": "sequenced", "key_findings": ["finding"], "facts": [],
            "inferences": [], "assumptions": [], "uncertainty": [], "risks": [],
            "recommendation": {"action": action, "reason": f"sequenced {action}"},
            "limitations": [], "next_actions": [], "evidence_quality": "ok",
            "unresolved_questions": [],
        }
        return ProviderResult(analysis=analysis, model_id=self.model_id,
                              model_version=self.model_version,
                              input_tokens=10, output_tokens=10, latency_ms=0)


class _FailingProvider:
    model_id = "fake.failing"
    model_version = "0.0.1"
    network_egress = False

    def generate(self, prompt, *, max_output_tokens, timeout_seconds):
        from runtime.mvp_runtime.errors import ProviderError
        raise ProviderError("PROVIDER_UNAVAILABLE", "synthetic outage")


@requires_local_core
def test_auto_policy_triage_high_runs_the_planned_reviewer():
    """R7.2: the orchestrator's HIGH verdict is what brings the reviewer in — no marker,
    no risk mandate, just Prime's own judgement. Call 1 = triage (HIGH), call 2 = review
    (PASS), both on the validator/triage provider."""
    result = run_task(REQUEST, independent_validation="auto",
                      validator_provider=_SequencedProvider(["HIGH", "PASS"]), now=NOW)
    assert result["status"] == "COMPLETED"
    assert result["records"]["triage_result"]["verdict"] == "HIGH"
    ival = result["records"]["independent_validation_result"]
    assert ival["validation"]["result"] == "PASS"
    # Operator/governance did not require it — the orchestrator chose it.
    assert ival["validator"]["independent_required"] is False
    usage = result["records"]["budget_usage"]["usage"]
    assert usage["model_calls"] == 3 and usage["agent_invocations"] == 2


@requires_local_core
def test_auto_policy_triage_failure_degrades_and_is_recorded():
    """A broken triage provider must neither block the analysis nor silently double its
    spend: the verdict degrades to NORMAL, the reviewer is skipped, and the degradation
    is on the record and the audit chain."""
    result = run_task(REQUEST, independent_validation="auto",
                      validator_provider=_FailingProvider(), now=NOW)
    assert result["status"] == "COMPLETED"
    triage = result["records"]["triage_result"]
    assert triage["verdict"] == "NORMAL" and triage["degraded"] is True
    assert "triage_invocation" not in result["records"]
    assert "independent_validation_result" not in result["records"]
    triage_events = [e for e in result["records"]["audit_trail"]
                     if "TRIAGE" in e["event"]["reason_codes"]]
    assert len(triage_events) == 1 and "TRIAGE_DEGRADED" in triage_events[0]["event"]["reason_codes"]


@requires_local_core
def test_explicit_true_still_validates_every_run():
    """The R7 all-on behavior is unchanged by R7.1 — bool True ignores priority/risk."""
    result = run_task(REQUEST, independent_validation=True, now=NOW)
    assert "independent_validation_result" in result["records"]

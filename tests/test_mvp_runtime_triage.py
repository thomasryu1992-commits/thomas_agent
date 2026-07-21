"""R7.2 orchestrator-triage unit tests — fake providers, no Core, no network.

The triage's one job is a trustworthy small verdict, so most tests pin the fail
direction: anything that is not a usable HIGH/NORMAL answer degrades to NORMAL with the
degradation on the record — never a crash, never a silent HIGH.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.budgets import TRIAGE_TOKEN_ALLOWANCE
from runtime.mvp_runtime.errors import ProviderError
from runtime.mvp_runtime.triage import (
    MockTriageProvider,
    build_triage_prompt,
    run_triage,
)
from runtime.mvp_runtime.worker import ProviderResult

NOW = "2026-07-21T09:00:00Z"
REQUEST = "이 사업 아이디어를 분석해줘: 구독형 반려동물 사료"


def _task():
    return {
        "identity": {"task_id": "task_x", "trace_id": "trace_x", "task_revision": 1},
        "scope": {"primary_objective": "analyze the idea"},
        "request": {"raw_request": REQUEST},
    }


class _FixedProvider:
    model_id = "fake.triage"
    model_version = "0.0.1"
    network_egress = False

    def __init__(self, action, *, reason="because"):
        self._action, self._reason = action, reason
        self.seen_max_tokens = None

    def generate(self, prompt, *, max_output_tokens, timeout_seconds):
        self.seen_max_tokens = max_output_tokens
        analysis = {"recommendation": {"action": self._action, "reason": self._reason}}
        return ProviderResult(analysis=analysis, model_id=self.model_id,
                              model_version=self.model_version,
                              input_tokens=20, output_tokens=5, latency_ms=0)


class _ExplodingProvider:
    model_id = "fake.exploding"
    model_version = "0.0.1"
    network_egress = False

    def generate(self, prompt, *, max_output_tokens, timeout_seconds):
        raise ProviderError("PROVIDER_UNAVAILABLE", "synthetic outage")


@pytest.mark.parametrize("action,expected", [
    ("HIGH", "HIGH"), ("high", "HIGH"), (" URGENT ", "HIGH"),
    ("NORMAL", "NORMAL"), ("low", "NORMAL"),
])
def test_verdicts_fold_onto_high_or_normal(action, expected):
    record, invocation = run_triage(_task(), provider=_FixedProvider(action), created_at=NOW)
    assert record["verdict"] == expected
    assert record["degraded"] is False
    assert record["reason"] == "because"
    assert invocation["tokens_used"] == 25


@pytest.mark.parametrize("action", ["PROCEED", "", None, 42])
def test_unusable_verdict_degrades_to_normal(action):
    record, invocation = run_triage(_task(), provider=_FixedProvider(action), created_at=NOW)
    assert record["verdict"] == "NORMAL"
    assert record["degraded"] is True
    assert invocation is not None      # the model answered; the answer was unusable


def test_provider_failure_degrades_without_an_invocation():
    record, invocation = run_triage(_task(), provider=_ExplodingProvider(), created_at=NOW)
    assert record["verdict"] == "NORMAL"
    assert record["degraded"] is True
    assert "synthetic outage" in record["reason"]
    assert invocation is None


def test_mock_triage_is_deterministic_normal():
    a, inv_a = run_triage(_task(), provider=MockTriageProvider(), created_at=NOW)
    b, inv_b = run_triage(_task(), provider=MockTriageProvider(), created_at=NOW)
    assert a == b
    assert a["verdict"] == "NORMAL" and a["degraded"] is False
    assert inv_a == inv_b and inv_a["network_egress"] is False


def test_triage_call_is_capped_at_its_own_allowance():
    provider = _FixedProvider("NORMAL")
    run_triage(_task(), provider=provider, created_at=NOW)
    assert provider.seen_max_tokens == TRIAGE_TOKEN_ALLOWANCE


def test_prompt_carries_goal_and_request_only():
    prompt = build_triage_prompt(_task())
    assert "analyze the idea" in prompt
    assert REQUEST in prompt
    assert "HIGH or NORMAL" in prompt
    # The triage judges the ask; it must not be asked to perform it.
    assert "do not answer it" in prompt

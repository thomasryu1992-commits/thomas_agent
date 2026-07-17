"""Hosted provider adapter tests — HTTP is fully mocked (no real network).

The hosted provider is behind the Safety-Flag Gate: generate() refuses to open a socket
without a valid Authorization, and select_provider() fails closed unless a local
activation record authorizes the network capability. These tests supply an Authorization
directly (unit-testing the HTTP path) and exercise the gate wiring separately.
"""

from __future__ import annotations

import json
import urllib.error

import pytest

from runtime.mvp_runtime.errors import ProviderError, SafetyGateBlocked
from runtime.mvp_runtime.providers import (
    HOSTED_PROVIDER_ENV,
    GoogleAIStudioProvider,
    select_provider,
)
from runtime.mvp_runtime import safety_gate
from runtime.mvp_runtime.safety_gate import (
    MODEL_INVOCATION,
    NETWORK_ACCESS,
    Authorization,
    build_activation_record,
)
from runtime.mvp_runtime.worker import MockProvider

API_ENV = "GOOGLE_AI_STUDIO_API_KEY"

# A granted egress authorization (as select_provider would produce after the gate passes).
_AUTH = Authorization(
    flags=(MODEL_INVOCATION, NETWORK_ACCESS),
    provider_id="google_ai_studio",
    activation_sha256="sha256:test",
    expires_at="2999-01-01T00:00:00Z",
    evidence_ref=".runtime_governance_state/evidence.md",
)


# --- Safety-Flag Gate wiring in select_provider -----------------------------

def test_select_provider_defaults_to_mock(monkeypatch):
    monkeypatch.delenv(HOSTED_PROVIDER_ENV, raising=False)
    assert isinstance(select_provider(), MockProvider)


def test_select_provider_hosted_without_activation_fails_closed(monkeypatch, tmp_path):
    # Opting in via the env var alone must NOT open a network path — no activation record.
    monkeypatch.setenv(HOSTED_PROVIDER_ENV, "google_ai_studio")
    with pytest.raises(SafetyGateBlocked) as exc:
        select_provider(now="2026-07-15T00:00:00Z", root=tmp_path)
    assert exc.value.reason_code == "ACTIVATION_MISSING"


def test_select_provider_hosted_with_activation_returns_hosted(monkeypatch, tmp_path):
    # A valid, integrity-checked activation record authorizes the hosted provider.
    state = tmp_path / ".runtime_governance_state"
    state.mkdir()
    evidence_rel = ".runtime_governance_state/safety_flag_gate_approval.md"
    (tmp_path / evidence_rel).write_text("operator decision evidence", encoding="utf-8")
    record = build_activation_record(
        flags=[MODEL_INVOCATION, NETWORK_ACCESS],
        provider_id="google_ai_studio",
        activated_at="2026-07-01T00:00:00Z",
        expires_at="2026-12-31T23:59:59Z",
        evidence_ref=evidence_rel,
        authority_level="P4",
    )
    path = safety_gate.activation_path(tmp_path, "google_ai_studio")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record), encoding="utf-8")

    monkeypatch.setenv(HOSTED_PROVIDER_ENV, "google_ai_studio")
    provider = select_provider(now="2026-07-15T00:00:00Z", root=tmp_path)
    assert isinstance(provider, GoogleAIStudioProvider)


# --- Egress self-guard in generate() ----------------------------------------

def test_generate_without_authorization_fails_closed(monkeypatch):
    monkeypatch.setenv(API_ENV, "test-key-not-real")
    with pytest.raises(SafetyGateBlocked) as exc:
        GoogleAIStudioProvider().generate("hi", max_output_tokens=100, timeout_seconds=10)
    assert exc.value.reason_code == "NOT_AUTHORIZED"


# --- HTTP parsing (given a granted authorization) ---------------------------

_ANALYSIS = {
    "summary": "A concise analysis.",
    "key_findings": ["revenue_potential: ok"],
    "facts": [{"statement": "Recurring category.", "evidence_refs": ["model"]}],
    "inferences": ["subscription helps LTV"],
    "assumptions": ["unverified demand"],
    "uncertainty": ["CAC unknown"],
    "risks": ["thin margins"],
    "recommendation": {"action": "validate small", "reason": "CAC dominates"},
    "limitations": ["illustrative"],
    "next_actions": ["estimate CAC"],
    "evidence_quality": "low",
    "unresolved_questions": ["retention?"],
}


def _gemini_response(analysis: dict) -> str:
    return json.dumps({
        "candidates": [{"content": {"parts": [{"text": json.dumps(analysis)}]}, "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 12, "candidatesTokenCount": 34},
    })


class _FakeResp:
    def __init__(self, payload: str):
        self._payload = payload.encode("utf-8")

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _patch_urlopen(monkeypatch, payload_or_exc):
    def fake_urlopen(request, timeout):
        if isinstance(payload_or_exc, Exception):
            raise payload_or_exc
        return _FakeResp(payload_or_exc)
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)


def test_no_api_key_fails_closed(monkeypatch):
    monkeypatch.delenv(API_ENV, raising=False)
    with pytest.raises(ProviderError) as exc:
        GoogleAIStudioProvider(authorization=_AUTH).generate("hi", max_output_tokens=100, timeout_seconds=10)
    assert exc.value.reason_code == "NO_API_KEY"


def test_happy_path_parses_structured_analysis(monkeypatch):
    monkeypatch.setenv(API_ENV, "test-key-not-real")
    _patch_urlopen(monkeypatch, _gemini_response(_ANALYSIS))
    result = GoogleAIStudioProvider(authorization=_AUTH).generate("analyze", max_output_tokens=8000, timeout_seconds=30)
    assert result.model_id == "google_ai_studio"
    assert result.analysis["summary"] == "A concise analysis."
    assert result.input_tokens == 12 and result.output_tokens == 34


def test_code_fenced_json_is_parsed(monkeypatch):
    monkeypatch.setenv(API_ENV, "k")
    fenced = "```json\n" + json.dumps(_ANALYSIS) + "\n```"
    payload = json.dumps({"candidates": [{"content": {"parts": [{"text": fenced}]}}], "usageMetadata": {}})
    _patch_urlopen(monkeypatch, payload)
    result = GoogleAIStudioProvider(authorization=_AUTH).generate("analyze", max_output_tokens=8000, timeout_seconds=30)
    assert result.analysis["key_findings"] == ["revenue_potential: ok"]


def test_transport_error_fails_closed_without_leaking(monkeypatch):
    monkeypatch.setenv(API_ENV, "secret-value")
    _patch_urlopen(monkeypatch, urllib.error.URLError("connection refused"))
    with pytest.raises(ProviderError) as exc:
        GoogleAIStudioProvider(authorization=_AUTH).generate("x", max_output_tokens=100, timeout_seconds=5)
    assert exc.value.reason_code == "PROVIDER_TRANSPORT"
    assert "secret-value" not in str(exc.value)  # the key must never leak


def test_malformed_response_fails_closed(monkeypatch):
    monkeypatch.setenv(API_ENV, "k")
    _patch_urlopen(monkeypatch, '{"candidates": [{"content": {"parts": [{"text": "not json"}]}}]}')
    with pytest.raises(ProviderError) as exc:
        GoogleAIStudioProvider(authorization=_AUTH).generate("x", max_output_tokens=100, timeout_seconds=5)
    assert exc.value.reason_code == "MALFORMED_RESPONSE"


def test_response_missing_fields_fails_closed(monkeypatch):
    monkeypatch.setenv(API_ENV, "k")
    partial = json.dumps({"summary": "only summary"})
    payload = json.dumps({"candidates": [{"content": {"parts": [{"text": partial}]}}], "usageMetadata": {}})
    _patch_urlopen(monkeypatch, payload)
    with pytest.raises(ProviderError) as exc:
        GoogleAIStudioProvider(authorization=_AUTH).generate("x", max_output_tokens=100, timeout_seconds=5)
    assert exc.value.reason_code == "MALFORMED_RESPONSE"

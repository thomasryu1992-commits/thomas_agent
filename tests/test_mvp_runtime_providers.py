"""Hosted provider adapter tests — HTTP is fully mocked (no real network)."""

from __future__ import annotations

import json
import urllib.error

import pytest

from runtime.mvp_runtime.errors import ProviderError
from runtime.mvp_runtime.providers import GoogleAIStudioProvider

API_ENV = "GOOGLE_AI_STUDIO_API_KEY"

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
        GoogleAIStudioProvider().generate("hi", max_output_tokens=100, timeout_seconds=10)
    assert exc.value.reason_code == "NO_API_KEY"


def test_happy_path_parses_structured_analysis(monkeypatch):
    monkeypatch.setenv(API_ENV, "test-key-not-real")
    _patch_urlopen(monkeypatch, _gemini_response(_ANALYSIS))
    result = GoogleAIStudioProvider().generate("analyze", max_output_tokens=8000, timeout_seconds=30)
    assert result.model_id == "google_ai_studio"
    assert result.analysis["summary"] == "A concise analysis."
    assert result.input_tokens == 12 and result.output_tokens == 34


def test_code_fenced_json_is_parsed(monkeypatch):
    monkeypatch.setenv(API_ENV, "k")
    fenced = "```json\n" + json.dumps(_ANALYSIS) + "\n```"
    payload = json.dumps({"candidates": [{"content": {"parts": [{"text": fenced}]}}], "usageMetadata": {}})
    _patch_urlopen(monkeypatch, payload)
    result = GoogleAIStudioProvider().generate("analyze", max_output_tokens=8000, timeout_seconds=30)
    assert result.analysis["key_findings"] == ["revenue_potential: ok"]


def test_transport_error_fails_closed_without_leaking(monkeypatch):
    monkeypatch.setenv(API_ENV, "secret-value")
    _patch_urlopen(monkeypatch, urllib.error.URLError("connection refused"))
    with pytest.raises(ProviderError) as exc:
        GoogleAIStudioProvider().generate("x", max_output_tokens=100, timeout_seconds=5)
    assert exc.value.reason_code == "PROVIDER_TRANSPORT"
    assert "secret-value" not in str(exc.value)  # the key must never leak


def test_malformed_response_fails_closed(monkeypatch):
    monkeypatch.setenv(API_ENV, "k")
    _patch_urlopen(monkeypatch, '{"candidates": [{"content": {"parts": [{"text": "not json"}]}}]}')
    with pytest.raises(ProviderError) as exc:
        GoogleAIStudioProvider().generate("x", max_output_tokens=100, timeout_seconds=5)
    assert exc.value.reason_code == "MALFORMED_RESPONSE"


def test_response_missing_fields_fails_closed(monkeypatch):
    monkeypatch.setenv(API_ENV, "k")
    partial = json.dumps({"summary": "only summary"})
    payload = json.dumps({"candidates": [{"content": {"parts": [{"text": partial}]}}], "usageMetadata": {}})
    _patch_urlopen(monkeypatch, payload)
    with pytest.raises(ProviderError) as exc:
        GoogleAIStudioProvider().generate("x", max_output_tokens=100, timeout_seconds=5)
    assert exc.value.reason_code == "MALFORMED_RESPONSE"

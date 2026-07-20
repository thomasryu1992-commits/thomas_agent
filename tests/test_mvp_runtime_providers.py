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


def _patch_urlopen_sequence(monkeypatch, outcomes):
    """Pop one outcome per call: an Exception is raised, anything else is the payload.
    Returns the list of backoff sleeps taken (providers.time.sleep is stubbed)."""
    remaining = list(outcomes)
    sleeps: list[float] = []

    def fake_urlopen(request, timeout):
        outcome = remaining.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return _FakeResp(outcome)
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("runtime.mvp_runtime.providers.time.sleep", lambda s: sleeps.append(s))
    return sleeps


def _http_error(code: int) -> urllib.error.HTTPError:
    import io
    return urllib.error.HTTPError("https://redacted.invalid", code, "err", {}, io.BytesIO(b"{}"))


@pytest.mark.parametrize("status", [503, 429])
def test_a_transient_status_is_retried_once_and_succeeds(monkeypatch, status):
    """503 (overloaded — observed live 2026-07-20) and 429 (throttled) mean "not now",
    not "no": one short-backoff retry turns a transient blip into a delivered answer."""
    monkeypatch.setenv(API_ENV, "k")
    sleeps = _patch_urlopen_sequence(monkeypatch, [_http_error(status), _gemini_response(_ANALYSIS)])
    result = GoogleAIStudioProvider(authorization=_AUTH).generate(
        "analyze", max_output_tokens=8000, timeout_seconds=30)
    assert result.analysis["summary"] == "A concise analysis."
    assert result.retries == 1                       # honestly recorded, not hidden
    assert sleeps == [5]                             # one backoff, then the retry


def test_a_persistent_transient_status_fails_after_one_retry(monkeypatch):
    """Exactly one retry (the budget contract's max_retry_count: 1) — a persistently
    overloaded provider becomes a typed BLOCK naming the status, never a retry loop."""
    monkeypatch.setenv(API_ENV, "k")
    sleeps = _patch_urlopen_sequence(monkeypatch, [_http_error(503), _http_error(503)])
    with pytest.raises(ProviderError) as exc:
        GoogleAIStudioProvider(authorization=_AUTH).generate(
            "analyze", max_output_tokens=8000, timeout_seconds=30)
    assert exc.value.reason_code == "PROVIDER_TRANSPORT"
    assert "HTTP 503" in exc.value.reason and "after 1 retry" in exc.value.reason
    assert "redacted.invalid" not in exc.value.reason        # the URL is never echoed
    assert sleeps == [5]


@pytest.mark.parametrize("outcome", [_http_error(400), _http_error(404), TimeoutError("hang")])
def test_non_transient_failures_are_not_retried(monkeypatch, outcome):
    """A 4xx is "no" and a timeout already consumed the full runtime budget — retrying
    either would spend time on an answer that will not change."""
    monkeypatch.setenv(API_ENV, "k")
    sleeps = _patch_urlopen_sequence(monkeypatch, [outcome])
    with pytest.raises(ProviderError) as exc:
        GoogleAIStudioProvider(authorization=_AUTH).generate(
            "analyze", max_output_tokens=8000, timeout_seconds=30)
    assert exc.value.reason_code == "PROVIDER_TRANSPORT"
    assert sleeps == []                              # no backoff, no second attempt


def test_first_try_success_records_zero_retries(monkeypatch):
    monkeypatch.setenv(API_ENV, "k")
    _patch_urlopen_sequence(monkeypatch, [_gemini_response(_ANALYSIS)])
    result = GoogleAIStudioProvider(authorization=_AUTH).generate(
        "analyze", max_output_tokens=8000, timeout_seconds=30)
    assert result.retries == 0


def test_latency_is_measured_not_hardcoded(monkeypatch):
    """Every audited invocation used to claim 0 ms egress — a metric that is only ever
    wrong, and useless exactly when a slow provider is what the operator is chasing."""
    monkeypatch.setenv(API_ENV, "k")
    clock = iter([100.0, 100.25])          # monotonic() before / after the round trip
    monkeypatch.setattr("runtime.mvp_runtime.providers.time.monotonic", lambda: next(clock))
    _patch_urlopen(monkeypatch, _gemini_response(_ANALYSIS))
    result = GoogleAIStudioProvider(authorization=_AUTH).generate(
        "analyze", max_output_tokens=8000, timeout_seconds=30)
    assert result.latency_ms == 250


def test_absent_usage_metadata_is_recorded_as_unmetered(monkeypatch):
    """Token accounting is the provider's self-report: no usageMetadata yields 0/0, which
    passes every budget check trivially. The record must say the call was unmetered rather
    than let it read as a genuinely free one."""
    monkeypatch.setenv(API_ENV, "k")
    payload = json.dumps({"candidates": [{"content": {"parts": [{"text": json.dumps(_ANALYSIS)}]}}]})
    _patch_urlopen(monkeypatch, payload)
    result = GoogleAIStudioProvider(authorization=_AUTH).generate(
        "analyze", max_output_tokens=8000, timeout_seconds=30)
    assert result.input_tokens == 0 and result.output_tokens == 0
    assert result.usage_reported is False

    _patch_urlopen(monkeypatch, _gemini_response(_ANALYSIS))
    reported = GoogleAIStudioProvider(authorization=_AUTH).generate(
        "analyze", max_output_tokens=8000, timeout_seconds=30)
    assert reported.usage_reported is True


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


@pytest.mark.parametrize("usage", [
    {"promptTokenCount": {"nested": "junk"}},   # int() of a dict -> TypeError
    "not-a-dict",                                # .get on a str -> AttributeError
    {"candidatesTokenCount": "12abc"},           # int() of junk -> ValueError
])
def test_malformed_usage_metadata_fails_closed(monkeypatch, usage):
    """Usage metadata is provider-supplied too: junk must BLOCK as MALFORMED_RESPONSE,
    not escape as a raw TypeError that crashes the CLI/loop."""
    monkeypatch.setenv(API_ENV, "k")
    payload = json.dumps({
        "candidates": [{"content": {"parts": [{"text": json.dumps(_ANALYSIS)}]}, "finishReason": "STOP"}],
        "usageMetadata": usage,
    })
    _patch_urlopen(monkeypatch, payload)
    with pytest.raises(ProviderError) as exc:
        GoogleAIStudioProvider(authorization=_AUTH).generate("x", max_output_tokens=100, timeout_seconds=5)
    assert exc.value.reason_code == "MALFORMED_RESPONSE"

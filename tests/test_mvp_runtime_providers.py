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
    VALIDATOR_PROVIDER_ENV,
    GoogleAIStudioProvider,
    select_provider,
    select_validator_provider,
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


def test_select_provider_unknown_single_value_falls_back_to_mock(monkeypatch):
    """Single unrecognized opt-in falls back to inert, exactly as before the chain."""
    monkeypatch.setenv(HOSTED_PROVIDER_ENV, "bogus_vendor")
    assert isinstance(select_provider(), MockProvider)


# --- the failover chain (selection) ------------------------------------------

def _grant(tmp_path, provider_id):
    evidence_rel = f".runtime_governance_state/{provider_id}_approval.md"
    (tmp_path / ".runtime_governance_state").mkdir(exist_ok=True)
    (tmp_path / evidence_rel).write_text("operator decision evidence", encoding="utf-8")
    record = build_activation_record(
        flags=[MODEL_INVOCATION, NETWORK_ACCESS], provider_id=provider_id,
        activated_at="2026-07-01T00:00:00Z", expires_at="2026-12-31T23:59:59Z",
        evidence_ref=evidence_rel, authority_level="P4",
    )
    path = safety_gate.activation_path(tmp_path, provider_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record), encoding="utf-8")


def test_chain_with_both_grants_builds_ordered_failover(monkeypatch, tmp_path):
    from runtime.mvp_runtime.providers import FailoverProvider, GroqProvider

    _grant(tmp_path, "google_ai_studio")
    _grant(tmp_path, "groq")
    monkeypatch.setenv(HOSTED_PROVIDER_ENV, "google_ai_studio,groq")
    provider = select_provider(now="2026-07-15T00:00:00Z", root=tmp_path)
    assert isinstance(provider, FailoverProvider)
    assert isinstance(provider._providers[0], GoogleAIStudioProvider)
    assert isinstance(provider._providers[1], GroqProvider)
    assert provider.model_id == "google_ai_studio+groq"


def test_chain_member_without_its_own_grant_fails_the_whole_selection(monkeypatch, tmp_path):
    """A chain never silently shrinks: a fallback whose gate does not open must surface
    at startup, not at 3am when the primary goes down and the chain quietly has one link."""
    _grant(tmp_path, "google_ai_studio")          # groq grant deliberately absent
    monkeypatch.setenv(HOSTED_PROVIDER_ENV, "google_ai_studio,groq")
    with pytest.raises(SafetyGateBlocked) as exc:
        select_provider(now="2026-07-15T00:00:00Z", root=tmp_path)
    assert exc.value.reason_code == "ACTIVATION_MISSING"


def test_chain_with_a_typo_fails_closed_not_shrunk(monkeypatch, tmp_path):
    _grant(tmp_path, "google_ai_studio")
    monkeypatch.setenv(HOSTED_PROVIDER_ENV, "google_ai_studio,grok")   # typo'd fallback
    with pytest.raises(SafetyGateBlocked) as exc:
        select_provider(now="2026-07-15T00:00:00Z", root=tmp_path)
    assert exc.value.reason_code == "UNKNOWN_PROVIDER"


def test_chain_with_a_duplicate_is_refused(monkeypatch, tmp_path):
    _grant(tmp_path, "google_ai_studio")
    monkeypatch.setenv(HOSTED_PROVIDER_ENV, "google_ai_studio,google_ai_studio")
    with pytest.raises(SafetyGateBlocked) as exc:
        select_provider(now="2026-07-15T00:00:00Z", root=tmp_path)
    assert exc.value.reason_code == "DUPLICATE_PROVIDER"


# --- R7.1: the validator's own provider selection ------------------------------

def test_select_validator_provider_unset_returns_none(monkeypatch):
    """None (not a mock) — the pipeline keeps its default validator pairing."""
    monkeypatch.delenv(VALIDATOR_PROVIDER_ENV, raising=False)
    assert select_validator_provider() is None


def test_select_validator_provider_env_alone_fails_closed(monkeypatch, tmp_path):
    """Same gate as the specialist: the env var without a local grant opens nothing."""
    monkeypatch.setenv(VALIDATOR_PROVIDER_ENV, "groq")
    with pytest.raises(SafetyGateBlocked):
        select_validator_provider(now="2026-07-15T00:00:00Z", root=tmp_path)


def test_select_validator_provider_with_grant_returns_hosted(monkeypatch, tmp_path):
    from runtime.mvp_runtime.providers import GroqProvider

    _grant(tmp_path, "groq")
    monkeypatch.setenv(VALIDATOR_PROVIDER_ENV, "groq")
    validator = select_validator_provider(now="2026-07-15T00:00:00Z", root=tmp_path)
    assert isinstance(validator, GroqProvider)


def test_validator_selection_is_independent_of_the_specialist_chain(monkeypatch, tmp_path):
    """Two env vars, two gate passes: authorizing the validator's provider must not
    change what the specialist selection yields, and vice versa."""
    from runtime.mvp_runtime.providers import GroqProvider

    _grant(tmp_path, "groq")
    monkeypatch.delenv(HOSTED_PROVIDER_ENV, raising=False)
    monkeypatch.setenv(VALIDATOR_PROVIDER_ENV, "groq")
    assert isinstance(select_provider(now="2026-07-15T00:00:00Z", root=tmp_path), MockProvider)
    assert isinstance(
        select_validator_provider(now="2026-07-15T00:00:00Z", root=tmp_path), GroqProvider
    )


# --- the failover chain (runtime behavior) ------------------------------------

class _StubProvider:
    def __init__(self, model_id, outcome):
        self.model_id = model_id
        self.model_version = model_id
        self.network_egress = True
        self._outcome = outcome
        self.calls = 0

    def generate(self, prompt, *, max_output_tokens, timeout_seconds):
        self.calls += 1
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


def _result(model_id):
    from runtime.mvp_runtime.worker import ProviderResult
    return ProviderResult(analysis={"summary": "s", "key_findings": [], "facts": []},
                          model_id=model_id, model_version=model_id,
                          input_tokens=1, output_tokens=1, latency_ms=1)


def test_failover_switches_only_on_unavailable(monkeypatch):
    from runtime.mvp_runtime.providers import FailoverProvider

    primary = _StubProvider("google_ai_studio",
                            ProviderError("PROVIDER_UNAVAILABLE", "hosted provider returned HTTP 503 after 1 retry"))
    fallback = _StubProvider("groq", _result("groq"))
    result = FailoverProvider([primary, fallback]).generate("p", max_output_tokens=100, timeout_seconds=30)
    assert result.model_id == "groq"              # the SERVING member is named in the record
    assert primary.calls == 1 and fallback.calls == 1


@pytest.mark.parametrize("code", ["PROVIDER_TRANSPORT", "MALFORMED_RESPONSE", "NO_API_KEY"])
def test_failover_does_not_switch_on_non_unavailable_failures(code):
    """A timeout already ate the runtime budget and a 4xx/parse failure will not change
    with a different vendor — those propagate immediately."""
    from runtime.mvp_runtime.providers import FailoverProvider

    primary = _StubProvider("google_ai_studio", ProviderError(code, "nope"))
    fallback = _StubProvider("groq", _result("groq"))
    with pytest.raises(ProviderError) as exc:
        FailoverProvider([primary, fallback]).generate("p", max_output_tokens=100, timeout_seconds=30)
    assert exc.value.reason_code == code
    assert fallback.calls == 0                    # never consulted


def test_failover_exhausted_is_typed_and_names_the_chain_outcome():
    from runtime.mvp_runtime.providers import FailoverProvider

    a = _StubProvider("google_ai_studio", ProviderError("PROVIDER_UNAVAILABLE", "HTTP 503 after 1 retry"))
    b = _StubProvider("groq", ProviderError("PROVIDER_UNAVAILABLE", "HTTP 429 after 1 retry"))
    with pytest.raises(ProviderError) as exc:
        FailoverProvider([a, b]).generate("p", max_output_tokens=100, timeout_seconds=30)
    assert exc.value.reason_code == "PROVIDER_UNAVAILABLE"
    assert "every provider" in exc.value.reason and "HTTP 429" in exc.value.reason


# --- the Groq adapter ----------------------------------------------------------

def _groq_response(analysis: dict) -> str:
    return json.dumps({
        "choices": [{"message": {"content": json.dumps(analysis)}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 21, "completion_tokens": 43},
    })


def test_every_hosted_call_names_itself_in_the_user_agent(monkeypatch):
    """urllib's default UA trips Cloudflare's bot rules in front of api.groq.com (observed
    live 2026-07-21: HTTP 403 "error code: 1010"). Both adapters send the stable product
    identifier — identification, not evasion."""
    from runtime.mvp_runtime.providers import _USER_AGENT, GroqProvider

    monkeypatch.setenv(API_ENV, "k")
    monkeypatch.setenv("GROQ_API_KEY", "k")
    seen: list[str] = []

    def capture_urlopen(request, timeout):
        seen.append(request.get_header("User-agent"))
        payload = _gemini_response(_ANALYSIS) if "googleapis" in request.full_url else _groq_response(_ANALYSIS)
        return _FakeResp(payload)
    monkeypatch.setattr("urllib.request.urlopen", capture_urlopen)

    GoogleAIStudioProvider(authorization=_AUTH).generate("p", max_output_tokens=100, timeout_seconds=10)
    GroqProvider(authorization=_groq_auth()).generate("p", max_output_tokens=100, timeout_seconds=10)
    assert seen == [_USER_AGENT, _USER_AGENT]


def test_groq_happy_path_parses_openai_shape(monkeypatch):
    from runtime.mvp_runtime.providers import GroqProvider

    monkeypatch.setenv("GROQ_API_KEY", "k")
    _patch_urlopen_sequence(monkeypatch, [_groq_response(_ANALYSIS)])
    result = GroqProvider(authorization=_groq_auth()).generate(
        "analyze", max_output_tokens=8000, timeout_seconds=30)
    assert result.model_id == "groq"
    assert result.analysis["summary"] == "A concise analysis."
    assert result.input_tokens == 21 and result.output_tokens == 43
    assert result.usage_reported is True


def test_groq_retries_a_503_once(monkeypatch):
    from runtime.mvp_runtime.providers import GroqProvider

    monkeypatch.setenv("GROQ_API_KEY", "k")
    sleeps = _patch_urlopen_sequence(monkeypatch, [_http_error(503), _groq_response(_ANALYSIS)])
    result = GroqProvider(authorization=_groq_auth()).generate(
        "analyze", max_output_tokens=8000, timeout_seconds=30)
    assert result.retries == 1 and sleeps == [5]


def test_groq_without_key_fails_closed(monkeypatch):
    from runtime.mvp_runtime.providers import GroqProvider

    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(ProviderError) as exc:
        GroqProvider(authorization=_groq_auth()).generate("p", max_output_tokens=10, timeout_seconds=10)
    assert exc.value.reason_code == "NO_API_KEY"


def test_groq_without_authorization_fails_closed(monkeypatch):
    from runtime.mvp_runtime.providers import GroqProvider

    monkeypatch.setenv("GROQ_API_KEY", "k")
    with pytest.raises(SafetyGateBlocked) as exc:
        GroqProvider().generate("p", max_output_tokens=10, timeout_seconds=10)
    assert exc.value.reason_code == "NOT_AUTHORIZED"


def _groq_auth():
    from runtime.mvp_runtime.safety_gate import Authorization
    return Authorization(
        flags=(MODEL_INVOCATION, NETWORK_ACCESS), provider_id="groq",
        activation_sha256="sha256:test", expires_at="2999-01-01T00:00:00Z",
        evidence_ref=".runtime_governance_state/evidence.md",
    )


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
    overloaded provider becomes a typed PROVIDER_UNAVAILABLE naming the status, never a
    retry loop. UNAVAILABLE (not TRANSPORT) is what lets a failover chain distinguish
    "not now" from "no"."""
    monkeypatch.setenv(API_ENV, "k")
    sleeps = _patch_urlopen_sequence(monkeypatch, [_http_error(503), _http_error(503)])
    with pytest.raises(ProviderError) as exc:
        GoogleAIStudioProvider(authorization=_AUTH).generate(
            "analyze", max_output_tokens=8000, timeout_seconds=30)
    assert exc.value.reason_code == "PROVIDER_UNAVAILABLE"
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

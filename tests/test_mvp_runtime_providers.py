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

from tests._helpers import FakeResp as _FakeResp, patch_urlopen as _patch_urlopen, make_gate_authorization

from runtime.mvp_runtime.errors import ProviderError, SafetyGateBlocked
from runtime.mvp_runtime.providers import (
    HOSTED_PROVIDER_ENV,
    OPENROUTER_HEAVY,
    TIER_DEGRADED,
    VALIDATOR_PROVIDER_ENV,
    GoogleAIStudioProvider,
    OpenRouterHeavyProvider,
    select_provider,
    select_tiered_provider,
    select_validator_provider,
)
from runtime.mvp_runtime import safety_gate
from runtime.mvp_runtime.safety_gate import (
    MODEL_INVOCATION,
    NETWORK_ACCESS,
    Authorization,
)
from runtime.mvp_runtime.worker import MockProvider

API_ENV = "GOOGLE_AI_STUDIO_API_KEY"

# A granted egress authorization (as select_provider would produce after the gate passes).
_AUTH = make_gate_authorization(flags=(MODEL_INVOCATION, NETWORK_ACCESS), provider_id="google_ai_studio")


# --- Safety-Flag Gate wiring in select_provider -----------------------------

def test_select_provider_defaults_to_mock(monkeypatch):
    monkeypatch.delenv(HOSTED_PROVIDER_ENV, raising=False)
    assert isinstance(select_provider(), MockProvider)


def test_select_provider_hosted_env_alone_returns_hosted(monkeypatch, tmp_path):
    # The environment is the gate (Thomas 2026-08-10): naming a known provider selects
    # it. The fail-closed directions that remain: unset -> Mock, unknown single value ->
    # Mock, unknown chain member -> the whole selection refused (tests below).
    monkeypatch.setenv(HOSTED_PROVIDER_ENV, "google_ai_studio")
    provider = select_provider(now="2026-07-15T00:00:00Z", root=tmp_path)
    assert isinstance(provider, GoogleAIStudioProvider)


def test_select_provider_unknown_single_value_falls_back_to_mock(monkeypatch):
    """Single unrecognized opt-in falls back to inert, exactly as before the chain."""
    monkeypatch.setenv(HOSTED_PROVIDER_ENV, "bogus_vendor")
    assert isinstance(select_provider(), MockProvider)


# --- the failover chain (selection) ------------------------------------------


# --- M2: difficulty-driven model tiers ---------------------------------------

_TIER_NOW = "2026-07-15T00:00:00Z"


def test_tiered_provider_keeps_inert_base_for_a_mock():
    # A network-free base has nothing to upgrade — no gate call, no degrade.
    provider, selection = select_tiered_provider("HIGH", base_provider=MockProvider())
    assert isinstance(provider, MockProvider)
    assert selection["tier"] is None and selection["degraded"] is False


def test_tiered_provider_degrades_to_base_without_a_tier_grant(tmp_path):
    base = GoogleAIStudioProvider(authorization=_AUTH)  # network-capable base
    provider, selection = select_tiered_provider("HIGH", base_provider=base, now=_TIER_NOW, root=tmp_path)
    assert provider is base  # degraded to the already-authorized base chain
    assert selection["tier"] == OPENROUTER_HEAVY
    assert selection["degraded"] is True and selection["reason_code"] == TIER_DEGRADED


def test_tiered_provider_unmapped_difficulty_degrades(tmp_path):
    base = GoogleAIStudioProvider(authorization=_AUTH)
    provider, selection = select_tiered_provider("WHATEVER", base_provider=base, now=_TIER_NOW, root=tmp_path)
    assert provider is base and selection["degraded"] is True and selection["tier"] is None


def test_tiered_provider_builds_the_tier_when_named(monkeypatch, tmp_path):
    # The tier opens only when MVP_OPENROUTER_TIERS names its id (env-only since
    # 2026-08-10, replacing the per-tier grants); the tier provider serves the specialist.
    from runtime.mvp_runtime.providers import OPENROUTER_TIERS_ENV

    monkeypatch.setenv(OPENROUTER_TIERS_ENV, OPENROUTER_HEAVY)
    base = GoogleAIStudioProvider(authorization=_AUTH)
    provider, selection = select_tiered_provider("HIGH", base_provider=base, now=_TIER_NOW, root=tmp_path)
    assert isinstance(provider, OpenRouterHeavyProvider)
    assert selection["tier"] == OPENROUTER_HEAVY and selection["degraded"] is False
    assert selection["model_id"] == OPENROUTER_HEAVY


def test_naming_the_light_tier_does_not_open_the_heavy_tier(monkeypatch, tmp_path):
    # The per-tier scope the per-tier grants used to carry lives in the list VALUES now:
    # a list that spells only the light tier cannot open the heavy one — it degrades.
    from runtime.mvp_runtime.providers import OPENROUTER_TIERS_ENV

    monkeypatch.setenv(OPENROUTER_TIERS_ENV, "openrouter_light")
    base = GoogleAIStudioProvider(authorization=_AUTH)
    provider, selection = select_tiered_provider("HIGH", base_provider=base, now=_TIER_NOW, root=tmp_path)
    assert provider is base and selection["degraded"] is True


def test_chain_with_both_grants_builds_ordered_failover(monkeypatch, tmp_path):
    from runtime.mvp_runtime.providers import FailoverProvider, GroqProvider

    monkeypatch.setenv(HOSTED_PROVIDER_ENV, "google_ai_studio,groq")
    provider = select_provider(now="2026-07-15T00:00:00Z", root=tmp_path)
    assert isinstance(provider, FailoverProvider)
    assert isinstance(provider._providers[0], GoogleAIStudioProvider)
    assert isinstance(provider._providers[1], GroqProvider)
    assert provider.model_id == "google_ai_studio+groq"


def test_editing_the_chain_revokes_every_member_at_egress(monkeypatch):
    """A chain never silently shrinks — including at run time. Every member's env-only
    authorization pins the FULL chain string, so an operator EDITING the list (not just
    emptying it) trips each member's egress re-check until a re-selection reads the new
    composition. This is what replaced 'deleting one member's grant file' as the way to
    take a member out of a long-lived process."""
    from runtime.mvp_runtime import safety_gate as gate

    monkeypatch.setenv(HOSTED_PROVIDER_ENV, "google_ai_studio,groq")
    chain = gate.select_env_gated_chain(
        env_var=HOSTED_PROVIDER_ENV,
        factories={"google_ai_studio": lambda a: a, "groq": lambda a: a},
        flags=(NETWORK_ACCESS,),
        default_factory=lambda: None,
    )
    monkeypatch.setenv(HOSTED_PROVIDER_ENV, "google_ai_studio")   # operator removed groq
    with pytest.raises(SafetyGateBlocked) as exc:
        gate.assert_authorization(
            chain[0], required_flags=(NETWORK_ACCESS,),
            provider_id="google_ai_studio", now="2026-08-10T00:00:00Z",
        )
    assert exc.value.reason_code == "ENV_OPT_IN_WITHDRAWN"


def test_chain_with_a_typo_fails_closed_not_shrunk(monkeypatch, tmp_path):
    monkeypatch.setenv(HOSTED_PROVIDER_ENV, "google_ai_studio,grok")   # typo'd fallback
    with pytest.raises(SafetyGateBlocked) as exc:
        select_provider(now="2026-07-15T00:00:00Z", root=tmp_path)
    assert exc.value.reason_code == "UNKNOWN_PROVIDER"


def test_chain_with_a_duplicate_is_refused(monkeypatch, tmp_path):
    monkeypatch.setenv(HOSTED_PROVIDER_ENV, "google_ai_studio,google_ai_studio")
    with pytest.raises(SafetyGateBlocked) as exc:
        select_provider(now="2026-07-15T00:00:00Z", root=tmp_path)
    assert exc.value.reason_code == "DUPLICATE_PROVIDER"


# --- R7.1: the validator's own provider selection ------------------------------

def test_select_validator_provider_unset_returns_none(monkeypatch):
    """None (not a mock) — the pipeline keeps its default validator pairing."""
    monkeypatch.delenv(VALIDATOR_PROVIDER_ENV, raising=False)
    assert select_validator_provider() is None


def test_select_validator_provider_env_alone_returns_hosted(monkeypatch, tmp_path):
    """Same gate as the specialist (env-only since 2026-08-10): the opt-in alone selects
    the validator's own provider."""
    from runtime.mvp_runtime.providers import GroqProvider

    monkeypatch.setenv(VALIDATOR_PROVIDER_ENV, "groq")
    validator = select_validator_provider(now="2026-07-15T00:00:00Z", root=tmp_path)
    assert isinstance(validator, GroqProvider)


def test_select_validator_provider_with_grant_returns_hosted(monkeypatch, tmp_path):
    from runtime.mvp_runtime.providers import GroqProvider

    monkeypatch.setenv(VALIDATOR_PROVIDER_ENV, "groq")
    validator = select_validator_provider(now="2026-07-15T00:00:00Z", root=tmp_path)
    assert isinstance(validator, GroqProvider)


def test_validator_selection_is_independent_of_the_specialist_chain(monkeypatch, tmp_path):
    """Two env vars, two gate passes: authorizing the validator's provider must not
    change what the specialist selection yields, and vice versa."""
    from runtime.mvp_runtime.providers import GroqProvider

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


def test_gemini_body_binds_the_analysis_response_schema(monkeypatch):
    """Structured output, not a prose request: the vendor enforces the 12-key shape, so a
    dropped field cannot reach the MALFORMED_RESPONSE guard or the validator's
    required-sections check. No minItems anywhere — the validator and the triage share this
    shape and legitimately return empty facts/key_findings."""
    from runtime.mvp_runtime.providers import _ANALYSIS_RESPONSE_SCHEMA

    monkeypatch.setenv(API_ENV, "k")
    bodies: list[dict] = []

    def capture_urlopen(request, timeout):
        bodies.append(json.loads(request.data.decode("utf-8")))
        return _FakeResp(_gemini_response(_ANALYSIS))
    monkeypatch.setattr("urllib.request.urlopen", capture_urlopen)

    GoogleAIStudioProvider(authorization=_AUTH).generate("p", max_output_tokens=100, timeout_seconds=10)

    config = bodies[0]["generationConfig"]
    assert config["responseMimeType"] == "application/json"
    assert config["responseSchema"] == _ANALYSIS_RESPONSE_SCHEMA
    # The three fields whose absence makes validate_agent_output withhold delivery.
    for key in ("key_findings", "facts", "uncertainty", "assumptions"):
        assert key in _ANALYSIS_RESPONSE_SCHEMA["required"]
    assert json.dumps(_ANALYSIS_RESPONSE_SCHEMA).find("minItems") == -1


def test_analysis_schemas_do_not_drift():
    """The Google (``responseSchema``) and OpenRouter (strict ``json_schema``) dialects
    describe the SAME analysis contract. They are separate constants only because the vendor
    dialects differ — their required-key sets must stay identical, and both must cover the
    keys the parser hard-requires, or one gateway would silently enforce a different shape."""
    from runtime.mvp_runtime.providers import (
        _ANALYSIS_JSON_SCHEMA,
        _ANALYSIS_RESPONSE_SCHEMA,
        _REQUIRED_ANALYSIS_KEYS,
    )

    assert _ANALYSIS_JSON_SCHEMA["required"] == _ANALYSIS_RESPONSE_SCHEMA["required"]
    assert set(_ANALYSIS_JSON_SCHEMA["properties"]) == set(_ANALYSIS_RESPONSE_SCHEMA["properties"])
    assert set(_REQUIRED_ANALYSIS_KEYS).issubset(_ANALYSIS_JSON_SCHEMA["required"])
    # Same "no minItems" stance as the Google schema: the validator and triage share this
    # shape and legitimately return empty facts/key_findings.
    assert "minItems" not in json.dumps(_ANALYSIS_JSON_SCHEMA)


def test_openrouter_json_schema_uses_strict_openai_dialect():
    """Strict json_schema is only honored if it is well-formed for that dialect: every object
    carries ``additionalProperties: false`` and the one nullable field is a ``["object",
    "null"]`` union, not Google's ``nullable: True`` (which OpenAI strict mode rejects)."""
    from runtime.mvp_runtime.providers import _ANALYSIS_JSON_SCHEMA

    assert _ANALYSIS_JSON_SCHEMA["additionalProperties"] is False
    assert _ANALYSIS_JSON_SCHEMA["properties"]["facts"]["items"]["additionalProperties"] is False
    recommendation = _ANALYSIS_JSON_SCHEMA["properties"]["recommendation"]
    assert recommendation["type"] == ["object", "null"]
    assert recommendation["additionalProperties"] is False
    assert "nullable" not in json.dumps(_ANALYSIS_JSON_SCHEMA)


def test_openrouter_difficulty_tiers_inherit_schema_enforcement():
    """The M2 difficulty-tier subclasses front the same gateway and must inherit the strict
    json_schema enforcement — not silently fall back to plain json_object."""
    from runtime.mvp_runtime.providers import (
        OpenRouterHeavyProvider,
        OpenRouterLightProvider,
        OpenRouterProvider,
        OpenRouterStandardProvider,
    )

    for cls in (OpenRouterLightProvider, OpenRouterStandardProvider, OpenRouterHeavyProvider):
        assert cls._response_format is OpenRouterProvider._response_format
        assert cls(authorization=None)._response_format()["type"] == "json_schema"


def test_groq_body_keeps_plain_json_object_mode(monkeypatch):
    """Groq's json_schema support is model-dependent and a rejected body fails the call
    outright (PROVIDER_TRANSPORT, not retryable). Deliberate asymmetry, asserted so it
    reads as a decision rather than an oversight."""
    from runtime.mvp_runtime.providers import GroqProvider

    monkeypatch.setenv("GROQ_API_KEY", "k")
    bodies: list[dict] = []

    def capture_urlopen(request, timeout):
        bodies.append(json.loads(request.data.decode("utf-8")))
        return _FakeResp(_groq_response(_ANALYSIS))
    monkeypatch.setattr("urllib.request.urlopen", capture_urlopen)

    GroqProvider(authorization=_groq_auth()).generate("p", max_output_tokens=100, timeout_seconds=10)
    assert bodies[0]["response_format"] == {"type": "json_object"}
    assert "response_schema" not in bodies[0]


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
    return make_gate_authorization(flags=(MODEL_INVOCATION, NETWORK_ACCESS), provider_id="groq")


# --- the OpenRouter adapter ----------------------------------------------------

# OpenRouter answers in the same OpenAI chat-completions shape as Groq. That sameness is
# exactly why both share one adapter, so they share one response fixture too.
_openrouter_response = _groq_response


def _openrouter_auth():
    from runtime.mvp_runtime.safety_gate import Authorization
    return make_gate_authorization(flags=(MODEL_INVOCATION, NETWORK_ACCESS), provider_id="openrouter")


def test_openrouter_opens_by_being_named_like_every_other_provider(monkeypatch, tmp_path):
    """A gateway is not a special door: ``openrouter`` opens by being NAMED in the chain
    env exactly like a vendor id (env-only since 2026-08-10), and an unknown name still
    fails a multi-member selection closed rather than shrinking it."""
    from runtime.mvp_runtime.providers import OpenRouterProvider

    monkeypatch.setenv(HOSTED_PROVIDER_ENV, "openrouter")
    provider = select_provider(now="2026-07-15T00:00:00Z", root=tmp_path)
    assert isinstance(provider, OpenRouterProvider)


def test_openrouter_grant_does_not_authorize_another_provider(monkeypatch):
    """One grant per provider id survives the shared base class: holding OpenRouter's
    authorization must not let the Groq adapter open a socket."""
    from runtime.mvp_runtime.providers import GroqProvider

    monkeypatch.setenv("GROQ_API_KEY", "k")
    with pytest.raises(SafetyGateBlocked) as exc:
        GroqProvider(authorization=_openrouter_auth()).generate(
            "p", max_output_tokens=10, timeout_seconds=10)
    assert exc.value.reason_code != ""          # refused, not silently allowed


def test_openrouter_happy_path_parses_openai_shape(monkeypatch):
    from runtime.mvp_runtime.providers import OpenRouterProvider

    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    _patch_urlopen_sequence(monkeypatch, [_openrouter_response(_ANALYSIS)])
    result = OpenRouterProvider(authorization=_openrouter_auth()).generate(
        "analyze", max_output_tokens=8000, timeout_seconds=30)
    assert result.model_id == "openrouter"
    assert result.analysis["summary"] == "A concise analysis."
    assert result.input_tokens == 21 and result.output_tokens == 43
    assert result.usage_reported is True


def test_openrouter_body_names_the_gateway_and_the_configured_model(monkeypatch):
    """The model slug is the only thing that decides cost and quality behind this gateway,
    so it must be exactly what was configured — never a silent default substitution."""
    from runtime.mvp_runtime.providers import _USER_AGENT, OpenRouterProvider

    monkeypatch.setenv("OPENROUTER_API_KEY", "secret-key-value")
    seen: list = []

    def capture_urlopen(request, timeout):
        seen.append((request.full_url, json.loads(request.data.decode("utf-8")),
                     request.get_header("Authorization"), request.get_header("User-agent")))
        return _FakeResp(_openrouter_response(_ANALYSIS))
    monkeypatch.setattr("urllib.request.urlopen", capture_urlopen)

    OpenRouterProvider(model="vendor/some-model:free", authorization=_openrouter_auth()).generate(
        "p", max_output_tokens=100, timeout_seconds=10)

    url, body, auth_header, user_agent = seen[0]
    assert url == "https://openrouter.ai/api/v1/chat/completions"
    assert body["model"] == "vendor/some-model:free"
    # OpenRouter enforces the 12-key shape server-side (strict json_schema), unlike Groq's
    # plain json_object — so a reasoning model cannot drop a required key past the gateway.
    from runtime.mvp_runtime.providers import _ANALYSIS_JSON_SCHEMA
    assert body["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "analysis", "strict": True, "schema": _ANALYSIS_JSON_SCHEMA},
    }
    assert body["max_tokens"] == 100
    assert auth_header == "Bearer secret-key-value"    # header only
    assert user_agent == _USER_AGENT
    # The key never leaves the header: not in the body, not in the URL.
    assert "secret-key-value" not in json.dumps(body) and "secret-key-value" not in url


def test_openrouter_free_tier_throttle_is_provider_unavailable(monkeypatch):
    """Free models are rate-limited (~20/min, ~200/day) and answer 429. That is "not now",
    not "no": one retry, then the failure class a failover chain is allowed to switch on."""
    from runtime.mvp_runtime.providers import OpenRouterProvider

    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    _patch_urlopen_sequence(monkeypatch, [_http_error(429), _http_error(429)])
    with pytest.raises(ProviderError) as exc:
        OpenRouterProvider(authorization=_openrouter_auth()).generate(
            "p", max_output_tokens=100, timeout_seconds=10)
    assert exc.value.reason_code == "PROVIDER_UNAVAILABLE"


def test_openrouter_without_key_fails_closed(monkeypatch):
    from runtime.mvp_runtime.providers import OpenRouterProvider

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(ProviderError) as exc:
        OpenRouterProvider(authorization=_openrouter_auth()).generate(
            "p", max_output_tokens=10, timeout_seconds=10)
    assert exc.value.reason_code == "NO_API_KEY"


def test_openrouter_without_authorization_fails_closed(monkeypatch):
    from runtime.mvp_runtime.providers import OpenRouterProvider

    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    with pytest.raises(SafetyGateBlocked) as exc:
        OpenRouterProvider().generate("p", max_output_tokens=10, timeout_seconds=10)
    assert exc.value.reason_code == "NOT_AUTHORIZED"


def test_the_two_openai_compatible_adapters_share_one_implementation():
    """Groq and OpenRouter speak the identical protocol, so they differ in exactly four
    vendor values and inherit everything else. Asserted so the next OpenAI-compatible backend
    is added as a few constants rather than a third copy of the gate/secret/retry logic.

    The one deliberate asymmetry: OpenRouter also overrides ``_response_format`` to enforce
    the analysis schema server-side, while Groq keeps the inherited json_object default. That
    single extra attribute is a decision (see OpenRouterProvider's docstring), asserted here
    so it reads as intentional rather than as creeping per-vendor logic."""
    from runtime.mvp_runtime.providers import (
        GroqProvider,
        OpenRouterProvider,
        _OpenAICompatibleProvider,
    )

    vendor_values = {"model_id", "_ENDPOINT", "_DEFAULT_MODEL", "_API_KEY_ENV"}

    def own_attrs(cls):
        # Ignore version-dependent dunders (e.g. __firstlineno__ on 3.13); only the
        # deliberately restated class attributes matter here.
        return {k for k in vars(cls) if not k.startswith("__")}

    for cls in (GroqProvider, OpenRouterProvider):
        assert issubclass(cls, _OpenAICompatibleProvider)
    # Groq restates only the four vendor values — including no response-format override.
    assert own_attrs(GroqProvider) == vendor_values
    # OpenRouter restates the four vendor values plus its one schema-enforcement decision.
    assert own_attrs(OpenRouterProvider) == vendor_values | {"_response_format"}
    assert GroqProvider.model_id != OpenRouterProvider.model_id
    assert _OpenAICompatibleProvider.network_egress is True


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
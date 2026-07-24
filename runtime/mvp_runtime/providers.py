"""Real hosted model providers for the specialist worker.

These make an outbound HTTPS call to a hosted LLM API and therefore require the
Safety-Flag Gate to be open (explicit Thomas approval + a versioned governance update
enabling model_invocation + a scoped network egress + audit). The adapter is inert
until its API key env var is set and it is explicitly selected — nothing here runs on
the default MVP path (which uses ``MockProvider``).

Secret handling: the API key is read from an environment variable **by name** at call
time and passed in a request header. It is never stored, logged, or included in any
error message or audit record (errors are deliberately generic and do not echo the
URL or key).
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from . import safety_gate, timeutil
from .errors import ProviderError
from .safety_gate import MODEL_INVOCATION, NETWORK_ACCESS, Authorization
from .worker import MockProvider, Provider, ProviderResult

# The model is asked to return exactly this JSON shape; the worker maps it onto
# agent_output.v0.2.
_RESPONSE_INSTRUCTION = (
    "\n\nReturn ONLY a single JSON object (no markdown, no prose) with these keys: "
    "summary (string), key_findings (array of strings), facts (array of objects "
    "{statement: string, evidence_refs: array of strings}), inferences (array of strings), "
    "assumptions (array of strings), uncertainty (array of strings), risks (array of strings), "
    "recommendation (object {action: string, reason: string} or null), limitations (array of strings), "
    "next_actions (array of strings), evidence_quality (string), unresolved_questions (array of strings)."
)


# The same 12-key shape as _RESPONSE_INSTRUCTION, as a schema the vendor can ENFORCE
# rather than a request the model may drift from. Free/low-cost models follow a prose
# format instruction least reliably, and a missing key is exactly what makes the automatic
# validation withhold delivery — a run that already paid for its analysis.
#
# Deliberately NO minItems: this shape is shared by the specialist, the independent
# validator, and the orchestrator triage. The latter two legitimately return empty
# facts/key_findings (they judge an answer, they do not produce one), so requiring a
# non-empty array here would ask them to invent content. The schema guarantees the KEYS
# exist; non-emptiness is the specialist prompt's job (``worker.ACCEPTANCE_CRITERIA``).
#
# ``recommendation`` stays nullable per the documented contract ("or null"). The validator
# and triage carry their verdict in ``recommendation.action`` and their prompts say so
# explicitly; permitting null does not invite it.
_STRING_ARRAY: dict[str, Any] = {"type": "array", "items": {"type": "string"}}
_ANALYSIS_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "key_findings": _STRING_ARRAY,
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"statement": {"type": "string"}, "evidence_refs": _STRING_ARRAY},
                "required": ["statement", "evidence_refs"],
            },
        },
        "inferences": _STRING_ARRAY,
        "assumptions": _STRING_ARRAY,
        "uncertainty": _STRING_ARRAY,
        "risks": _STRING_ARRAY,
        "recommendation": {
            "type": "object",
            "nullable": True,
            "properties": {"action": {"type": "string"}, "reason": {"type": "string"}},
            "required": ["action", "reason"],
        },
        "limitations": _STRING_ARRAY,
        "next_actions": _STRING_ARRAY,
        "evidence_quality": {"type": "string"},
        "unresolved_questions": _STRING_ARRAY,
    },
    "required": [
        "summary", "key_findings", "facts", "inferences", "assumptions", "uncertainty",
        "risks", "recommendation", "limitations", "next_actions", "evidence_quality",
        "unresolved_questions",
    ],
}

HOSTED_PROVIDER_ENV = "MVP_HOSTED_PROVIDER"
VALIDATOR_PROVIDER_ENV = "MVP_VALIDATOR_PROVIDER"
HOSTED_MODEL_ENV = "MVP_HOSTED_MODEL"
GOOGLE_AI_STUDIO = "google_ai_studio"
# The default hosted model. Not "gemini-2.5-flash": that 404s for newly issued keys.
DEFAULT_HOSTED_MODEL = "gemini-flash-latest"
GROQ = "groq"
GROQ_MODEL_ENV = "MVP_GROQ_MODEL"
DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"

OPENROUTER = "openrouter"
OPENROUTER_MODEL_ENV = "MVP_OPENROUTER_MODEL"
# OpenRouter is a GATEWAY, not a vendor: one endpoint and one key front hundreds of models
# from many vendors. Two consequences worth stating rather than discovering:
#
# 1. Scope. Every other provider id names one vendor whose model range is narrow, so the
#    grant and the capability line up. Here they do not — a single ``openrouter`` grant
#    authorizes whatever slug the env var happens to name, and the model is the thing that
#    actually decides cost and quality. That is acceptable while one pinned free model is
#    configured on a machine only Thomas operates; it stops being acceptable the moment
#    tiers and money are involved, at which point the answer is separate provider ids per
#    tier with the allowed models pinned INTO each grant. Recorded here so the next change
#    starts from the limit rather than rediscovering it.
# 2. Rate limits. Free models allow ~20 req/min and ~200 req/day and answer 429 when
#    exhausted — which ``_post_json_with_retry`` already classifies as PROVIDER_UNAVAILABLE,
#    so a failover chain switches members instead of failing the run.
#
# The default is a free-tier slug. OpenRouter's catalogue changes, so verify it against the
# account and override with ``MVP_OPENROUTER_MODEL``; an unknown slug is a 4xx, which is
# PROVIDER_TRANSPORT (deliberately not retried, not failed over).
DEFAULT_OPENROUTER_MODEL = "meta-llama/llama-3.3-70b-instruct:free"

# M2: difficulty-driven model tiers over the same OpenRouter gateway. Each tier is its
# OWN provider id — its own Safety-Flag grant and its own model-slug env — so a light
# grant can never authorize the heavy model; the scope limit DEFAULT_OPENROUTER_MODEL
# names is closed one tier at a time. The M1 difficulty (LOW/MEDIUM/HIGH) picks the tier;
# an absent tier grant degrades to the base MVP_HOSTED_PROVIDER chain (TIER_DEGRADED),
# never blocks. Slug defaults are fallbacks only — the OpenRouter catalogue changes, so
# verify per machine and override with the envs below (the DEFAULT_OPENROUTER_MODEL caveat
# applies per tier). Grants are minted locally with activate_safety_flag.py, per tier id.
OPENROUTER_LIGHT = "openrouter_light"
OPENROUTER_STANDARD = "openrouter_standard"
OPENROUTER_HEAVY = "openrouter_heavy"
OPENROUTER_MODEL_LIGHT_ENV = "MVP_OPENROUTER_MODEL_LIGHT"
OPENROUTER_MODEL_STANDARD_ENV = "MVP_OPENROUTER_MODEL_STANDARD"
OPENROUTER_MODEL_HEAVY_ENV = "MVP_OPENROUTER_MODEL_HEAVY"
DEFAULT_OPENROUTER_MODEL_LIGHT = "openai/gpt-oss-20b:free"
DEFAULT_OPENROUTER_MODEL_STANDARD = "meta-llama/llama-3.3-70b-instruct:free"
DEFAULT_OPENROUTER_MODEL_HEAVY = "deepseek/deepseek-r1:free"

TIER_DEGRADED = "TIER_DEGRADED"
# M1 difficulty tier -> the OpenRouter model tier that serves it. Keys are the literal
# difficulty strings the triage records (triage.DIFFICULTY_*), matched here as strings to
# avoid importing triage (which would cycle back through the worker module).
_DIFFICULTY_TIER = {"LOW": OPENROUTER_LIGHT, "MEDIUM": OPENROUTER_STANDARD, "HIGH": OPENROUTER_HEAVY}

_NETWORK_FLAGS = (MODEL_INVOCATION, NETWORK_ACCESS)

# The two HTTP statuses that mean "not now", not "no": 503 (the model pool is overloaded —
# observed live 2026-07-20) and 429 (free-tier throttle). Exactly ONE retry after a short
# backoff, matching the budget contract's max_retry_count: 1. Timeouts are deliberately
# NOT retryable: a hung call already consumed the full max_runtime_seconds, so retrying it
# could double the worst-case wall clock, whereas a 503/429 answer arrives in about a
# second and the retry stays well inside the budget's intent.
_RETRYABLE_HTTP = frozenset({429, 503})
_MAX_RETRIES = 1
_RETRY_BACKOFF_SECONDS = 5

# Sent on every hosted call. urllib's default ("Python-urllib/3.12") trips Cloudflare's
# bot rules in front of api.groq.com — observed live 2026-07-21 as HTTP 403 "error code:
# 1010" — so an honest, stable product identifier goes on both adapters. This is
# identification, not evasion: the runtime names itself instead of wearing a library
# default that bot filters treat as anonymous scripting.
_USER_AGENT = "thomas-agent-mvp/0.1"


def _post_json_with_retry(request: urllib.request.Request, *, timeout_seconds: int) -> tuple[str, int, int]:
    """POST and return ``(raw_body, latency_ms, retries)`` — the one HTTP path every
    hosted adapter shares, so the retry rule and the two typed failure classes cannot
    drift between vendors.

    - ``PROVIDER_UNAVAILABLE``: 503/429 still failing after the single retry. This is the
      provider saying "not now" — the failure class a failover chain may switch on.
    - ``PROVIDER_TRANSPORT``: everything else (4xx, network failure, timeout). "No", not
      "not now" — switching providers would spend time on an answer that will not change
      (4xx) or double the worst-case wall clock (timeout already ate max_runtime_seconds).

    Errors name the HTTP status (the server's answer, safe) — never the URL or the key.
    """
    started = time.monotonic()
    retries = 0
    while True:
        try:
            with urllib.request.urlopen(request, timeout=int(timeout_seconds)) as response:
                raw = response.read().decode("utf-8")
            break
        except urllib.error.HTTPError as exc:
            # Order matters: HTTPError IS a URLError; catch it first to read the status.
            if exc.code in _RETRYABLE_HTTP and retries < _MAX_RETRIES:
                retries += 1
                time.sleep(_RETRY_BACKOFF_SECONDS)
                continue
            suffix = f" after {retries} retry" if retries else ""
            if exc.code in _RETRYABLE_HTTP:
                raise ProviderError(
                    "PROVIDER_UNAVAILABLE", f"hosted provider returned HTTP {exc.code}{suffix}"
                ) from None
            raise ProviderError(
                "PROVIDER_TRANSPORT", f"hosted provider returned HTTP {exc.code}{suffix}"
            ) from None
        except (TimeoutError, urllib.error.URLError):
            # Deliberately generic — never echo the URL or key.
            raise ProviderError("PROVIDER_TRANSPORT", "hosted provider request failed or timed out") from None
    return raw, int((time.monotonic() - started) * 1000), retries


def select_provider(*, now: str | None = None, root: Path | None = None) -> Provider:
    """Choose the worker's provider — the enforced Safety-Flag Gate chokepoint.

    Defaults to the deterministic, network-free ``MockProvider`` (no gate needed; it
    performs no network I/O). A real hosted provider is returned ONLY when both (a) the
    caller opts in via ``MVP_HOSTED_PROVIDER=google_ai_studio`` AND (b) the Safety-Flag
    Gate authorizes it against a local, integrity-checked activation record. The env var
    alone is NOT sufficient: with no valid activation this fails closed
    (:class:`SafetyGateBlocked`) rather than silently opening a network path.

    The env var also accepts an ordered, comma-separated **failover chain**
    (``MVP_HOSTED_PROVIDER=google_ai_studio,groq``): each member needs its OWN grant, a
    chain with an unknown or unauthorized member fails closed entirely (it never silently
    shrinks), and at run time the next member is tried only when the previous one is
    UNAVAILABLE (503/429 even after its own retry) — never on a timeout or a 4xx.

    The gate ordering lives in ``safety_gate.select_gated_chain``, shared semantics with
    the search tool, operator channel, and workspace writer. Model names are read inside
    the gated factories — they only matter once the gate has already opened.
    """
    chain = safety_gate.select_gated_chain(
        env_var=HOSTED_PROVIDER_ENV,
        factories=_hosted_factories(),
        flags=_NETWORK_FLAGS,
        default_factory=MockProvider,
        now=now,
        root=root,
    )
    return chain[0] if len(chain) == 1 else FailoverProvider(chain)


def _hosted_factories() -> dict[str, Any]:
    """The gated hosted-provider factories, shared by the specialist and validator
    selections — one catalogue, so a provider cannot exist for one selection and not the
    other, and both read the model-name env vars only after their gate has opened."""
    return {
        GOOGLE_AI_STUDIO: lambda authorization: GoogleAIStudioProvider(
            model=os.environ.get(HOSTED_MODEL_ENV, DEFAULT_HOSTED_MODEL).strip(),
            authorization=authorization,
        ),
        GROQ: lambda authorization: GroqProvider(
            model=os.environ.get(GROQ_MODEL_ENV, DEFAULT_GROQ_MODEL).strip(),
            authorization=authorization,
        ),
        OPENROUTER: lambda authorization: OpenRouterProvider(
            model=os.environ.get(OPENROUTER_MODEL_ENV, DEFAULT_OPENROUTER_MODEL).strip(),
            authorization=authorization,
        ),
    }


def _tier_factories() -> dict[str, Any]:
    """The gated factories for the M2 difficulty tiers. Each reads its own model-slug env
    only after its gate has opened, exactly like ``_hosted_factories``."""
    return {
        OPENROUTER_LIGHT: lambda authorization: OpenRouterLightProvider(
            model=os.environ.get(OPENROUTER_MODEL_LIGHT_ENV, DEFAULT_OPENROUTER_MODEL_LIGHT).strip(),
            authorization=authorization,
        ),
        OPENROUTER_STANDARD: lambda authorization: OpenRouterStandardProvider(
            model=os.environ.get(OPENROUTER_MODEL_STANDARD_ENV, DEFAULT_OPENROUTER_MODEL_STANDARD).strip(),
            authorization=authorization,
        ),
        OPENROUTER_HEAVY: lambda authorization: OpenRouterHeavyProvider(
            model=os.environ.get(OPENROUTER_MODEL_HEAVY_ENV, DEFAULT_OPENROUTER_MODEL_HEAVY).strip(),
            authorization=authorization,
        ),
    }


def select_tiered_provider(
    difficulty: str, *, base_provider: Provider, now: str | None = None, root: Path | None = None,
) -> tuple[Provider, dict[str, Any]]:
    """Pick the OpenRouter model tier for this request's difficulty (M2); degrade to base.

    Returns ``(provider, selection)``. ``selection`` records the difficulty, the chosen
    tier, whether it degraded, and why — persisted by the caller as run evidence. The base
    provider serves unchanged when either (a) it is inert/mock: a network-free run has
    nothing to upgrade, or (b) the chosen tier has no local grant, in which case the run
    degrades to the already-authorized base chain and records ``TIER_DEGRADED`` (the
    SEARCH_DEGRADED precedent — the tier benefit is lost, the run is not). Only when the
    tier gate opens is the tier provider built, from its own grant's Authorization, and it
    serves in place of the base for the specialist call."""
    selection: dict[str, Any] = {
        "difficulty": str(difficulty), "tier": None, "degraded": False, "reason_code": None,
    }
    if not bool(getattr(base_provider, "network_egress", False)):
        return base_provider, selection  # inert/mock base — no tier to select
    tier_id = _DIFFICULTY_TIER.get(str(difficulty))
    if tier_id is None:
        selection.update(degraded=True, reason_code=TIER_DEGRADED,
                         detail=f"no tier for difficulty {difficulty!r}")
        return base_provider, selection
    selection["tier"] = tier_id
    provider, blocked_reason = safety_gate.select_gated_optional(
        flags=_NETWORK_FLAGS, provider_id=tier_id,
        gated_factory=_tier_factories()[tier_id], now=now, root=root,
    )
    if provider is None:
        selection.update(degraded=True, reason_code=TIER_DEGRADED, detail=blocked_reason)
        return base_provider, selection
    selection["model_id"] = getattr(provider, "model_id", tier_id)
    return provider, selection


def select_validator_provider(*, now: str | None = None, root: Path | None = None) -> Provider | None:
    """Choose the independent validator's own provider (R7.1) — or ``None`` to keep the
    pipeline's default pairing (mock validator for a mock specialist, else the specialist's
    provider).

    Opt-in via ``MVP_VALIDATOR_PROVIDER`` (e.g. ``groq``, or a comma-separated failover
    chain), so the review can run on a different free quota than the analysis. Exactly the
    same Safety-Flag Gate chokepoint and grant requirements as ``MVP_HOSTED_PROVIDER``:
    every named member needs its own local activation, an unknown/unauthorized member
    fails the whole selection closed, and the env var alone never opens a network path.
    """
    if not os.environ.get(VALIDATOR_PROVIDER_ENV, "").strip():
        return None
    chain = safety_gate.select_gated_chain(
        env_var=VALIDATOR_PROVIDER_ENV,
        factories=_hosted_factories(),
        flags=_NETWORK_FLAGS,
        default_factory=MockProvider,
        now=now,
        root=root,
    )
    return chain[0] if len(chain) == 1 else FailoverProvider(chain)


_REQUIRED_ANALYSIS_KEYS = ("summary", "key_findings", "facts")


def _parse_hosted_response(
    raw: str,
    *,
    model_id: str,
    model_version: str,
    latency_ms: int,
    retries: int,
    extract_text: Any,
    extract_usage: Any,
) -> ProviderResult:
    """Shared fail-closed parse for a hosted provider's response.

    Only the vendor JSON paths differ between adapters; the fence-stripping, the
    required-analysis-field check, both MALFORMED_RESPONSE guards, and the
    ProviderResult construction were near-identical copies. ``extract_text(data)``
    returns the model's text; ``extract_usage(data)`` returns
    ``(usage_dict, input_tokens, output_tokens, finish_reason)`` — any vendor-shape
    surprise inside either fails closed as MALFORMED_RESPONSE, never escapes raw.
    """
    try:
        data: dict[str, Any] = json.loads(raw)
        text = extract_text(data)
        analysis = json.loads(_strip_code_fences(text))
    except (KeyError, IndexError, ValueError, TypeError):
        raise ProviderError("MALFORMED_RESPONSE", "hosted provider returned an unparseable response") from None
    if not isinstance(analysis, dict) or any(k not in analysis for k in _REQUIRED_ANALYSIS_KEYS):
        raise ProviderError("MALFORMED_RESPONSE", "hosted provider response missing required analysis fields")

    # Usage metadata is provider-supplied too: parsing it must fail closed as
    # MALFORMED_RESPONSE like the body above, not escape as a raw TypeError that
    # crashes the CLI/loop instead of BLOCKing the run.
    try:
        usage, input_tokens, output_tokens, finish_reason = extract_usage(data)
    except (AttributeError, KeyError, IndexError, ValueError, TypeError):
        raise ProviderError("MALFORMED_RESPONSE", "hosted provider returned unparseable usage metadata") from None
    return ProviderResult(
        analysis=analysis,
        model_id=model_id,
        model_version=model_version,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_ms=latency_ms,
        finish_reason=finish_reason,
        # Token accounting is the provider's self-report: an absent usage block yields
        # 0/0, which passes every budget check trivially. Record that the call was
        # unmetered rather than let it read as a genuinely free one.
        usage_reported=bool(usage),
        retries=retries,
    )


def _strip_code_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        if t.rstrip().endswith("```"):
            t = t.rstrip()[: t.rstrip().rfind("```")]
    return t.strip()


class GoogleAIStudioProvider:
    """Google AI Studio (Gemini) provider via the generateContent REST endpoint.

    ``model`` is configurable (model names change; set the exact free-tier model you
    have access to). The API key is read from ``api_key_env`` at call time.
    """

    model_id = "google_ai_studio"
    network_egress = True  # makes an outbound HTTPS call — audited as network egress
    _ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    def __init__(
        self,
        *,
        model: str = DEFAULT_HOSTED_MODEL,
        api_key_env: str = "GOOGLE_AI_STUDIO_API_KEY",
        authorization: Authorization | None = None,
    ):
        self._model = model
        self._api_key_env = api_key_env  # the NAME of the env var, never the value
        self.model_version = model
        # Egress authorization from the Safety-Flag Gate. Without it, generate() refuses
        # to open a socket — so a directly-constructed provider cannot bypass the gate.
        self._authorization = authorization

    def generate(self, prompt: str, *, max_output_tokens: int, timeout_seconds: int) -> ProviderResult:
        # Chokepoint: re-verify authorization at the moment of egress (defense in depth).
        safety_gate.assert_authorization(
            self._authorization,
            required_flags=_NETWORK_FLAGS,
            provider_id=self.model_id,
            now=timeutil.utc_now_iso(),
        )
        api_key = os.environ.get(self._api_key_env)
        if not api_key:
            raise ProviderError("NO_API_KEY", f"environment variable {self._api_key_env} is not set")

        body = json.dumps({
            "contents": [{"parts": [{"text": prompt + _RESPONSE_INSTRUCTION}]}],
            "generationConfig": {
                "maxOutputTokens": int(max_output_tokens),
                "responseMimeType": "application/json",
                # Structured output: the vendor enforces the key set, so a missing field
                # cannot reach _parse_hosted_response's MALFORMED_RESPONSE guard or the
                # validator's required-sections check. Groq's endpoint keeps plain
                # json_object mode — json_schema support is model-dependent there, and a
                # rejected body would fail the call outright (PROVIDER_TRANSPORT, not
                # retryable), which is a worse trade than the format instruction it has.
                "responseSchema": _ANALYSIS_RESPONSE_SCHEMA,
            },
        }).encode("utf-8")
        request = urllib.request.Request(
            self._ENDPOINT.format(model=self._model),
            data=body,
            method="POST",
            headers={"Content-Type": "application/json", "x-goog-api-key": api_key,
                     "User-Agent": _USER_AGENT},
        )
        # Latency is measured inside the shared helper and covers every attempt including
        # the backoff — it is what the operator actually waited (monotonic, so a clock
        # adjustment mid-call cannot produce a negative duration).
        raw, latency_ms, retries = _post_json_with_retry(request, timeout_seconds=timeout_seconds)

        return self._parse(raw, latency_ms=latency_ms, retries=retries)

    def _parse(self, raw: str, *, latency_ms: int = 0, retries: int = 0) -> ProviderResult:
        def extract_text(data: dict[str, Any]) -> str:
            return data["candidates"][0]["content"]["parts"][0]["text"]

        def extract_usage(data: dict[str, Any]) -> tuple[dict[str, Any], int, int, str]:
            # An ABSENT usage block is a real (unmetered) case; a PRESENT non-dict one is
            # vendor junk and must fail closed — the .get on it raises into the helper's
            # MALFORMED_RESPONSE guard. The two vendors used to disagree on this.
            usage = data.get("usageMetadata", {}) if isinstance(data, dict) else {}
            return (
                usage,
                int(usage.get("promptTokenCount", 0) or 0),
                int(usage.get("candidatesTokenCount", 0) or 0),
                str((data.get("candidates", [{}]) or [{}])[0].get("finishReason", "stop")),
            )

        return _parse_hosted_response(
            raw, model_id=self.model_id, model_version=self._model,
            latency_ms=latency_ms, retries=retries,
            extract_text=extract_text, extract_usage=extract_usage,
        )


class _OpenAICompatibleProvider:
    """Shared adapter for vendors speaking the OpenAI ``/chat/completions`` shape.

    Groq and OpenRouter differ in four values — endpoint, provider id, default model, and
    the NAME of the key env var. The request body, the secret handling, the gate
    chokepoint, the retry rule, and the usage-parsing stance are identical. They live here
    once for the same reason ``_parse_hosted_response`` exists: these are exactly the parts
    that must not drift between vendors, and a third OpenAI-compatible backend should
    inherit them rather than restate them and get one subtly wrong.

    Same guarantees as :class:`GoogleAIStudioProvider`: gate-authorized per its own grant
    (``model_id`` IS the provider id the Safety-Flag Gate authorizes against — one grant
    per id), key read from ``api_key_env`` **by name** at call time and sent in the
    Authorization header (never stored, logged, or echoed), egress re-verified at the
    moment of the call, and the shared retry/latency/typed-failure HTTP path.
    """

    network_egress = True  # makes an outbound HTTPS call — audited as network egress
    model_id: str = ""
    _ENDPOINT: str = ""
    _DEFAULT_MODEL: str = ""
    _API_KEY_ENV: str = ""

    def __init__(
        self,
        *,
        model: str | None = None,
        api_key_env: str | None = None,
        authorization: Authorization | None = None,
    ):
        self._model = model if model is not None else self._DEFAULT_MODEL
        # the NAME of the env var, never the value
        self._api_key_env = api_key_env if api_key_env is not None else self._API_KEY_ENV
        self.model_version = self._model
        # Egress authorization from the Safety-Flag Gate. Without it, generate() refuses
        # to open a socket — so a directly-constructed provider cannot bypass the gate.
        self._authorization = authorization

    def generate(self, prompt: str, *, max_output_tokens: int, timeout_seconds: int) -> ProviderResult:
        # Chokepoint: re-verify authorization at the moment of egress (defense in depth).
        safety_gate.assert_authorization(
            self._authorization,
            required_flags=_NETWORK_FLAGS,
            provider_id=self.model_id,
            now=timeutil.utc_now_iso(),
        )
        api_key = os.environ.get(self._api_key_env)
        if not api_key:
            raise ProviderError("NO_API_KEY", f"environment variable {self._api_key_env} is not set")

        body = json.dumps({
            "model": self._model,
            "messages": [{"role": "user", "content": prompt + _RESPONSE_INSTRUCTION}],
            "max_tokens": int(max_output_tokens),
            "response_format": {"type": "json_object"},
        }).encode("utf-8")
        request = urllib.request.Request(
            self._ENDPOINT,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}",
                     "User-Agent": _USER_AGENT},
        )
        raw, latency_ms, retries = _post_json_with_retry(request, timeout_seconds=timeout_seconds)
        return self._parse(raw, latency_ms=latency_ms, retries=retries)

    def _parse(self, raw: str, *, latency_ms: int = 0, retries: int = 0) -> ProviderResult:
        def extract_text(data: dict[str, Any]) -> str:
            return data["choices"][0]["message"]["content"]

        def extract_usage(data: dict[str, Any]) -> tuple[dict[str, Any], int, int, str]:
            # Same stance as the Google adapter: absent usage = unmetered, present junk
            # fails closed (this adapter used to quietly coerce junk to unmetered).
            usage = data.get("usage", {}) if isinstance(data, dict) else {}
            return (
                usage,
                int(usage.get("prompt_tokens", 0) or 0),
                int(usage.get("completion_tokens", 0) or 0),
                str((data.get("choices", [{}]) or [{}])[0].get("finish_reason", "stop")),
            )

        return _parse_hosted_response(
            raw, model_id=self.model_id, model_version=self._model,
            latency_ms=latency_ms, retries=retries,
            extract_text=extract_text, extract_usage=extract_usage,
        )


class GroqProvider(_OpenAICompatibleProvider):
    """Groq — the failover alternative CLAUDE.md's locked decision has always named."""

    model_id = GROQ
    _ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"
    _DEFAULT_MODEL = DEFAULT_GROQ_MODEL
    _API_KEY_ENV = "GROQ_API_KEY"


class OpenRouterProvider(_OpenAICompatibleProvider):
    """OpenRouter — one OpenAI-compatible gateway in front of many vendors' models.

    Selecting a different model is an env var (``MVP_OPENROUTER_MODEL``) rather than a new
    adapter, which is the whole point of adding it: the runtime gains model choice without
    gaining a code path per vendor. See ``DEFAULT_OPENROUTER_MODEL`` for what that costs in
    grant scope — one grant here covers whatever slug is configured, unlike every other
    provider id.
    """

    model_id = OPENROUTER
    _ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
    _DEFAULT_MODEL = DEFAULT_OPENROUTER_MODEL
    _API_KEY_ENV = "OPENROUTER_API_KEY"


class OpenRouterLightProvider(OpenRouterProvider):
    """M2 LOW-difficulty tier. Same OpenRouter gateway/key; its OWN ``model_id`` so the
    Safety-Flag Gate authorizes it against its own grant, and its own default slug."""

    model_id = OPENROUTER_LIGHT
    _DEFAULT_MODEL = DEFAULT_OPENROUTER_MODEL_LIGHT


class OpenRouterStandardProvider(OpenRouterProvider):
    """M2 MEDIUM-difficulty tier — its own grant + slug, OpenRouter gateway shared."""

    model_id = OPENROUTER_STANDARD
    _DEFAULT_MODEL = DEFAULT_OPENROUTER_MODEL_STANDARD


class OpenRouterHeavyProvider(OpenRouterProvider):
    """M2 HIGH-difficulty tier — its own grant + slug, OpenRouter gateway shared."""

    model_id = OPENROUTER_HEAVY
    _DEFAULT_MODEL = DEFAULT_OPENROUTER_MODEL_HEAVY


class FailoverProvider:
    """Ordered failover across gate-authorized providers.

    Composition only — every member was already built from its own
    :class:`safety_gate.Authorization` by ``select_gated_chain``, so this class holds no
    authority of its own and adds none. The next member is tried on exactly ONE failure
    class: ``PROVIDER_UNAVAILABLE`` (503/429 persisting through the member's own retry —
    the provider saying "not now", the failure class observed live 2026-07-20). A timeout
    already consumed the full ``max_runtime_seconds`` and a 4xx/parse failure will not
    change with a different vendor's answer, so those propagate immediately.

    The returned :class:`ProviderResult` carries the SERVING member's ``model_id``/
    ``model_version``, so the invocation record and audit trail always name who actually
    answered — a failover that reads as the primary would hide instability from the ledger.
    """

    network_egress = True  # every member is a network provider by construction

    def __init__(self, providers: list[Any]):
        if len(providers) < 2:
            raise ProviderError("INVALID_CHAIN", "a failover chain needs at least two providers")
        self._providers = list(providers)
        # Named for banners/diagnostics; the serving member's id lands in each result.
        self.model_id = "+".join(getattr(p, "model_id", "?") for p in self._providers)
        self.model_version = self.model_id

    def generate(self, prompt: str, *, max_output_tokens: int, timeout_seconds: int) -> ProviderResult:
        last: ProviderError | None = None
        for provider in self._providers:
            try:
                return provider.generate(
                    prompt, max_output_tokens=max_output_tokens, timeout_seconds=timeout_seconds
                )
            except ProviderError as exc:
                if exc.reason_code != "PROVIDER_UNAVAILABLE":
                    raise
                last = exc
        # Every member said "not now". Typed, and it names the whole chain's outcome.
        raise ProviderError(
            "PROVIDER_UNAVAILABLE",
            f"every provider in the failover chain is unavailable (last: {last.reason})",
        ) from None

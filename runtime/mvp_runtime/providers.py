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


HOSTED_PROVIDER_ENV = "MVP_HOSTED_PROVIDER"
HOSTED_MODEL_ENV = "MVP_HOSTED_MODEL"
GOOGLE_AI_STUDIO = "google_ai_studio"
# The default hosted model. Not "gemini-2.5-flash": that 404s for newly issued keys.
DEFAULT_HOSTED_MODEL = "gemini-flash-latest"
GROQ = "groq"
GROQ_MODEL_ENV = "MVP_GROQ_MODEL"
DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"
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
        factories={
            GOOGLE_AI_STUDIO: lambda authorization: GoogleAIStudioProvider(
                model=os.environ.get(HOSTED_MODEL_ENV, DEFAULT_HOSTED_MODEL).strip(),
                authorization=authorization,
            ),
            GROQ: lambda authorization: GroqProvider(
                model=os.environ.get(GROQ_MODEL_ENV, DEFAULT_GROQ_MODEL).strip(),
                authorization=authorization,
            ),
        },
        flags=_NETWORK_FLAGS,
        default_factory=MockProvider,
        now=now,
        root=root,
    )
    return chain[0] if len(chain) == 1 else FailoverProvider(chain)


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
        try:
            data: dict[str, Any] = json.loads(raw)
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            analysis = json.loads(_strip_code_fences(text))
        except (KeyError, IndexError, ValueError, TypeError):
            raise ProviderError("MALFORMED_RESPONSE", "hosted provider returned an unparseable response") from None
        if not isinstance(analysis, dict) or any(k not in analysis for k in ("summary", "key_findings", "facts")):
            raise ProviderError("MALFORMED_RESPONSE", "hosted provider response missing required analysis fields")

        # Usage metadata is provider-supplied too: parsing it must fail closed as
        # MALFORMED_RESPONSE like the body above, not escape as a raw TypeError that
        # crashes the CLI/loop instead of BLOCKing the run.
        try:
            usage = data.get("usageMetadata", {}) if isinstance(data, dict) else {}
            input_tokens = int(usage.get("promptTokenCount", 0) or 0)
            output_tokens = int(usage.get("candidatesTokenCount", 0) or 0)
            finish_reason = str((data.get("candidates", [{}]) or [{}])[0].get("finishReason", "stop"))
        except (AttributeError, KeyError, IndexError, ValueError, TypeError):
            raise ProviderError("MALFORMED_RESPONSE", "hosted provider returned unparseable usage metadata") from None
        return ProviderResult(
            analysis=analysis,
            model_id=self.model_id,
            model_version=self._model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            finish_reason=finish_reason,
            # Token accounting is the provider's self-report: an absent usageMetadata block
            # yields 0/0, which passes every budget check trivially. Record that the call
            # was unmetered rather than let it read as a genuinely free one.
            usage_reported=bool(usage),
            retries=retries,
        )


class GroqProvider:
    """Groq provider via the OpenAI-compatible chat-completions endpoint.

    The failover alternative CLAUDE.md's locked decision has always named. Same shape as
    :class:`GoogleAIStudioProvider`: gate-authorized per its own ``groq`` grant, key read
    from ``api_key_env`` **by name** at call time (sent in the Authorization header —
    never stored, logged, or echoed), egress re-verified at the moment of the call, and
    the shared retry/latency/typed-failure HTTP path.
    """

    model_id = GROQ
    network_egress = True  # makes an outbound HTTPS call — audited as network egress
    _ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"

    def __init__(
        self,
        *,
        model: str = DEFAULT_GROQ_MODEL,
        api_key_env: str = "GROQ_API_KEY",
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
        try:
            data: dict[str, Any] = json.loads(raw)
            text = data["choices"][0]["message"]["content"]
            analysis = json.loads(_strip_code_fences(text))
        except (KeyError, IndexError, ValueError, TypeError):
            raise ProviderError("MALFORMED_RESPONSE", "hosted provider returned an unparseable response") from None
        if not isinstance(analysis, dict) or any(k not in analysis for k in ("summary", "key_findings", "facts")):
            raise ProviderError("MALFORMED_RESPONSE", "hosted provider response missing required analysis fields")

        try:
            usage = data.get("usage", {}) if isinstance(data, dict) else {}
            usage = usage if isinstance(usage, dict) else {}
            input_tokens = int(usage.get("prompt_tokens", 0) or 0)
            output_tokens = int(usage.get("completion_tokens", 0) or 0)
            finish_reason = str((data.get("choices", [{}]) or [{}])[0].get("finish_reason", "stop"))
        except (AttributeError, KeyError, IndexError, ValueError, TypeError):
            raise ProviderError("MALFORMED_RESPONSE", "hosted provider returned unparseable usage metadata") from None
        return ProviderResult(
            analysis=analysis,
            model_id=self.model_id,
            model_version=self._model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            finish_reason=finish_reason,
            usage_reported=bool(usage),
            retries=retries,
        )


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

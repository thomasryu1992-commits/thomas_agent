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
_NETWORK_FLAGS = (MODEL_INVOCATION, NETWORK_ACCESS)


def select_provider(*, now: str | None = None, root: Path | None = None) -> Provider:
    """Choose the worker's provider — the enforced Safety-Flag Gate chokepoint.

    Defaults to the deterministic, network-free ``MockProvider`` (no gate needed; it
    performs no network I/O). A real hosted provider is returned ONLY when both (a) the
    caller opts in via ``MVP_HOSTED_PROVIDER=google_ai_studio`` AND (b) the Safety-Flag
    Gate authorizes it against a local, integrity-checked activation record. The env var
    alone is NOT sufficient: with no valid activation this fails closed
    (:class:`SafetyGateBlocked`) rather than silently opening a network path.

    The gate ordering lives in ``safety_gate.select_gated``, shared with the search tool,
    operator channel, and workspace writer. The model name is read inside the gated factory
    — it only matters once the gate has already opened.
    """
    return safety_gate.select_gated(
        env_var=HOSTED_PROVIDER_ENV,
        opt_in_value=GOOGLE_AI_STUDIO,
        flags=_NETWORK_FLAGS,
        provider_id=GOOGLE_AI_STUDIO,
        default_factory=MockProvider,
        gated_factory=lambda authorization: GoogleAIStudioProvider(
            model=os.environ.get(HOSTED_MODEL_ENV, DEFAULT_HOSTED_MODEL).strip(),
            authorization=authorization,
        ),
        now=now,
        root=root,
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
            },
        }).encode("utf-8")
        request = urllib.request.Request(
            self._ENDPOINT.format(model=self._model),
            data=body,
            method="POST",
            headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        )
        try:
            with urllib.request.urlopen(request, timeout=int(timeout_seconds)) as response:
                raw = response.read().decode("utf-8")
        except (TimeoutError, urllib.error.URLError):
            # Deliberately generic — never echo the URL or key.
            raise ProviderError("PROVIDER_TRANSPORT", "hosted provider request failed or timed out") from None

        return self._parse(raw)

    def _parse(self, raw: str) -> ProviderResult:
        try:
            data: dict[str, Any] = json.loads(raw)
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            analysis = json.loads(_strip_code_fences(text))
        except (KeyError, IndexError, ValueError, TypeError):
            raise ProviderError("MALFORMED_RESPONSE", "hosted provider returned an unparseable response") from None
        if not isinstance(analysis, dict) or any(k not in analysis for k in ("summary", "key_findings", "facts")):
            raise ProviderError("MALFORMED_RESPONSE", "hosted provider response missing required analysis fields")

        usage = data.get("usageMetadata", {}) if isinstance(data, dict) else {}
        return ProviderResult(
            analysis=analysis,
            model_id=self.model_id,
            model_version=self._model,
            input_tokens=int(usage.get("promptTokenCount", 0) or 0),
            output_tokens=int(usage.get("candidatesTokenCount", 0) or 0),
            latency_ms=0,
            finish_reason=str((data.get("candidates", [{}]) or [{}])[0].get("finishReason", "stop")),
        )

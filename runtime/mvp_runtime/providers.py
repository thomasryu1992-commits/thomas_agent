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

import http.client
import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

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
    "next_actions (array of strings), evidence_quality (string), unresolved_questions (array of strings), "
    "perspectives (array of objects {perspective: string, verdict: string, basis: string}; "
    "return [] unless the prompt asked you to judge from separate perspectives)."
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
# ``perspectives`` (§10.4) joins on exactly those terms — a required KEY that the validator
# and triage return empty, because only the specialist prompt asks for the separation and
# only the specialist's role contract declares the field. It has to be here rather than in
# the specialist prompt alone: the OpenAI dialect below is strict
# (``additionalProperties: false``), so a key the schema does not name is not merely
# unrequested, it is *rejected* — the specialist would be asked for a field the transport
# then refused, and every hosted run would fail its own perspective check.
#
# ``verdict`` is a plain string here rather than an enum: the two vendor dialects differ on
# enum support and an unsupported keyword fails the whole request, which would degrade every
# run instead of one field. The allowed values are stated in the specialist prompt, and
# ``worker._perspectives`` drops anything else — fail-closed, and the cost of a wrong verdict
# is one REVISE rather than an outage.
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
        "perspectives": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"perspective": {"type": "string"}, "verdict": {"type": "string"},
                               "basis": {"type": "string"}},
                "required": ["perspective", "verdict", "basis"],
            },
        },
    },
    "required": [
        "summary", "key_findings", "facts", "inferences", "assumptions", "uncertainty",
        "risks", "recommendation", "limitations", "next_actions", "evidence_quality",
        "unresolved_questions", "perspectives",
    ],
}

# The SAME 13-key shape as above, in the OpenAI/OpenRouter ``json_schema`` (strict) dialect.
# Kept as a separate constant rather than shared with _ANALYSIS_RESPONSE_SCHEMA because the
# two vendor dialects genuinely differ and mixing them fails closed on both sides: OpenAI
# strict mode requires ``additionalProperties: false`` on every object and expresses a
# nullable field as a ``["object", "null"]`` type union, whereas Google's ``responseSchema``
# uses ``nullable: True`` and rejects ``additionalProperties``. The ``required`` key set is
# identical by construction; ``test_analysis_schemas_do_not_drift`` asserts it so the two
# cannot silently diverge into different contracts for the same analysis.
_ANALYSIS_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {"type": "string"},
        "key_findings": _STRING_ARRAY,
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"statement": {"type": "string"}, "evidence_refs": _STRING_ARRAY},
                "required": ["statement", "evidence_refs"],
            },
        },
        "inferences": _STRING_ARRAY,
        "assumptions": _STRING_ARRAY,
        "uncertainty": _STRING_ARRAY,
        "risks": _STRING_ARRAY,
        "recommendation": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "properties": {"action": {"type": "string"}, "reason": {"type": "string"}},
            "required": ["action", "reason"],
        },
        "limitations": _STRING_ARRAY,
        "next_actions": _STRING_ARRAY,
        "evidence_quality": {"type": "string"},
        "unresolved_questions": _STRING_ARRAY,
        "perspectives": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"perspective": {"type": "string"}, "verdict": {"type": "string"},
                               "basis": {"type": "string"}},
                "required": ["perspective", "verdict", "basis"],
            },
        },
    },
    "required": [
        "summary", "key_findings", "facts", "inferences", "assumptions", "uncertainty",
        "risks", "recommendation", "limitations", "next_actions", "evidence_quality",
        "unresolved_questions", "perspectives",
    ],
}


# §8.5: a Role that is not the business analyst declares its OWN output keys, and both
# dialects above are CLOSED over the analysis key set — OpenAI strict via
# ``additionalProperties: false``, Google via the ``required`` list the vendor enforces. So a
# hosted model asked for ``translated_text`` returns without it and the run fails its own
# output check, which reads like a model quality problem when it is a schema problem. These
# re-derive the schema with the Role's keys folded in.
#
# Deliberately a *derivation*, not a mutation: the module constants stay the analysis shape
# and every existing caller keeps it byte-for-byte. A role-bound provider builds its own copy
# per call, so two Roles running concurrently cannot share one mutated schema.
#
# The Role's keys are typed from its own contract (``string`` -> string, anything else ->
# array of strings), matching what ``worker._role_specific_output`` reads back. An unknown
# declared type becomes an array rather than being dropped: the Role asked for the field, and
# omitting it here would put us back to the vendor rejecting it.
def _role_key_schema(kind: str) -> dict[str, Any]:
    return {"type": "string"} if kind == "string" else _STRING_ARRAY


def analysis_response_schema(role_output_spec: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Google ``responseSchema`` for this run — the analysis shape, plus a Role's own keys."""
    if not role_output_spec:
        return _ANALYSIS_RESPONSE_SCHEMA
    schema = json.loads(json.dumps(_ANALYSIS_RESPONSE_SCHEMA))
    for key, kind in role_output_spec.items():
        schema["properties"][key] = _role_key_schema(kind)
        if key not in schema["required"]:
            schema["required"].append(key)
    return schema


def analysis_json_schema(role_output_spec: Mapping[str, str] | None = None) -> dict[str, Any]:
    """OpenAI/OpenRouter strict ``json_schema`` for this run. Same derivation; separate
    dialect, because ``additionalProperties: false`` is exactly what makes the fold-in
    necessary here rather than optional."""
    if not role_output_spec:
        return _ANALYSIS_JSON_SCHEMA
    schema = json.loads(json.dumps(_ANALYSIS_JSON_SCHEMA))
    for key, kind in role_output_spec.items():
        schema["properties"][key] = _role_key_schema(kind)
        if key not in schema["required"]:
            schema["required"].append(key)
    return schema


HOSTED_PROVIDER_ENV = "MVP_HOSTED_PROVIDER"
VALIDATOR_PROVIDER_ENV = "MVP_VALIDATOR_PROVIDER"
HOSTED_MODEL_ENV = "MVP_HOSTED_MODEL"
GOOGLE_AI_STUDIO = "google_ai_studio"
# The default hosted model. Not "gemini-2.5-flash": that 404s for newly issued keys.
DEFAULT_HOSTED_MODEL = "gemini-flash-latest"
GROQ = "groq"
GROQ_MODEL_ENV = "MVP_GROQ_MODEL"
# Not "llama-3.3-70b-versatile": Groq decommissioned it for free/developer tiers in August
# 2026 (notice 2026-06-17) and every request since answers HTTP 404 — PROVIDER_TRANSPORT,
# no retry, and (until review D1, 2026-09-26) no failover — which took down every path riding this default at once
# (independent validator, frontdesk, data review, proposer; measured 2026-08-29, two
# consecutive weekly DATA_REVIEW fires and a proposer backlog of 11). This slug is Groq's
# own named migration target for that model.
DEFAULT_GROQ_MODEL = "openai/gpt-oss-120b"

OPENROUTER = "openrouter"
OPENROUTER_MODEL_ENV = "MVP_OPENROUTER_MODEL"
# OpenRouter is a GATEWAY, not a vendor: one endpoint and one key front hundreds of models
# from many vendors. Two consequences worth stating rather than discovering:
#
# 1. Scope. Every other provider id names one vendor whose model range is narrow, so the
#    opt-in and the capability line up. Here they do not — a single ``openrouter`` opt-in
#    authorizes whatever slug the env var happens to name, and the model is the thing that
#    actually decides cost and quality. That is acceptable while one pinned free model is
#    configured on a machine only Thomas operates; it stops being acceptable the moment
#    tiers and money are involved, at which point the answer is separate provider ids per
#    tier with the allowed models pinned into each tier's own configuration. Recorded here
#    so the next change starts from the limit rather than rediscovering it.
# 2. Rate limits. Free models allow ~20 req/min and ~200 req/day and answer 429 when
#    exhausted — which ``_post_json_with_retry`` already classifies as PROVIDER_UNAVAILABLE,
#    so a failover chain switches members instead of failing the run.
#
# The default is a free-tier slug. OpenRouter's catalogue changes, so verify it against the
# account and override with ``MVP_OPENROUTER_MODEL``; an unknown slug is a 404, which is
# PROVIDER_TRANSPORT — not retried, and failed over in a chain since review D1 (the slug is this
# member's configuration, not the request).
DEFAULT_OPENROUTER_MODEL = "meta-llama/llama-3.3-70b-instruct:free"

# M2: difficulty-driven model tiers over the same OpenRouter gateway. Each tier is its
# OWN provider id — its own opt-in name and its own model-slug env — so opting in the
# light tier can never open the heavy model's gate; the scope limit DEFAULT_OPENROUTER_MODEL
# names is closed one tier at a time. The M1 difficulty (LOW/MEDIUM/HIGH) picks the tier;
# a tier NOT named in ``MVP_OPENROUTER_TIERS`` degrades to the base MVP_HOSTED_PROVIDER
# chain (TIER_DEGRADED), never blocks. Slug defaults are fallbacks only — the OpenRouter
# catalogue changes, so verify per machine and override with the envs below (the
# DEFAULT_OPENROUTER_MODEL caveat applies per tier).
#
# Since 2026-08-10 the opt-in is the environment alone: ``MVP_OPENROUTER_TIERS`` is a
# comma list of the tier ids the operator enables. It replaced the per-tier grants when
# grants were retired, and it must stay an EXPLICIT list because the tiers' fallback is
# fail-open by construction (degrade-to-base) — with no opt-in of their own, retiring the
# grants would have silently armed all three tiers. Unset on the live machine today, so
# every tier degrades: the same behavior the absent per-tier grants produced.
OPENROUTER_TIERS_ENV = "MVP_OPENROUTER_TIERS"
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

# The one 4xx that is "not this time" rather than "no": Groq's JSON mode validates the model's
# output and answers HTTP 400 ``json_validate_failed`` when the model wrote malformed JSON. The
# request was fine and the next sample usually is too — measured 2026-09-25 on the proposer's
# own prompt, about 1 call in 6 on `openai/gpt-oss-120b`, the rest parsing cleanly; that one-in-
# six cost the whole day's proposer fire, since a 4xx was never retried. Retried once, within the
# same single-retry budget as 429/503 and without the backoff (nothing is throttling us). Still
# TRANSPORT, not UNAVAILABLE, when it persists; a chain fails over on it (review D1) because it is
# this member's model writing bad JSON, which another vendor's model need not repeat.
_RETRYABLE_ERROR_CODES = frozenset({"json_validate_failed"})
# How much of an error body is read to find its code. The body can carry the model's failed
# output (`failed_generation`), which is never echoed — only the code is.
_ERROR_BODY_READ_LIMIT = 65536
_ERROR_CODE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")


def _error_code(exc: urllib.error.HTTPError) -> str | None:
    """The vendor's machine-readable error code from an HTTP error body, or None.

    OpenAI-compatible vendors (Groq, OpenRouter) put a string in ``error.code``; Google puts an
    integer there and the name in ``error.status``. Only a short identifier-shaped string is
    returned — never the message, which can quote the prompt, nor ``failed_generation``. Reading
    the body is best-effort: an unreadable one is simply no code."""
    try:
        raw = exc.read(_ERROR_BODY_READ_LIMIT)
        data = json.loads(raw.decode("utf-8", "replace")) if raw else None
    except Exception:  # noqa: BLE001 — a code is diagnostic; its absence must not mask the status
        return None
    error = data.get("error") if isinstance(data, dict) else None
    if not isinstance(error, dict):
        return None
    for key in ("code", "status"):
        value = error.get(key)
        if isinstance(value, str) and _ERROR_CODE_RE.match(value):
            return value
    return None

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
      provider saying "not now".
    - ``PROVIDER_TRANSPORT``: everything else (4xx, 5xx, network failure, timeout).

    Neither code decides failover on its own any more: :func:`failover_kind` reads the HTTP status
    and vendor code carried in the error's ``data`` (review D1). Errors name the HTTP status (the
    server's answer, safe) — never the URL or the key.
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
            code = None if exc.code in _RETRYABLE_HTTP else _error_code(exc)
            if code in _RETRYABLE_ERROR_CODES and retries < _MAX_RETRIES:
                retries += 1
                continue
            suffix = f" after {retries} retry" if retries else ""
            # The vendor's error code rides the reason: "HTTP 400" alone made a malformed-JSON
            # sample, a decommissioned model (#790) and an empty model slug (#801) one string.
            named = f" ({code})" if code else ""
            if exc.code in _RETRYABLE_HTTP:
                raise ProviderError(
                    "PROVIDER_UNAVAILABLE", f"hosted provider returned HTTP {exc.code}{suffix}",
                    data={"http_status": exc.code},
                ) from None
            raise ProviderError(
                "PROVIDER_TRANSPORT", f"hosted provider returned HTTP {exc.code}{named}{suffix}",
                data={"http_status": exc.code, "error_code": code},
            ) from None
        except (TimeoutError, urllib.error.URLError):
            # Deliberately generic — never echo the URL or key.
            raise ProviderError("PROVIDER_TRANSPORT", "hosted provider request failed or timed out",
                                data={"transport": "request"}) from None
        except (OSError, http.client.HTTPException):
            # A connection that dies after the request went out — RemoteDisconnected,
            # ConnectionResetError, IncompleteRead — is raised by getresponse()/read()
            # directly, not wrapped in URLError. Every caller catches ProviderError only, so
            # an untyped one ended the run with no BLOCK record and no audit trail.
            raise ProviderError(
                "PROVIDER_TRANSPORT", "hosted provider connection failed before the response completed",
                data={"transport": "connection"},
            ) from None
        except UnicodeDecodeError:
            raise ProviderError("MALFORMED_RESPONSE", "hosted provider returned an unparseable response") from None
    return raw, int((time.monotonic() - started) * 1000), retries


def select_provider(*, now: str | None = None, root: Path | None = None) -> Provider:
    """Choose the worker's provider — the enforced Safety-Flag Gate chokepoint.

    Defaults to the deterministic, network-free ``MockProvider`` (no gate needed; it
    performs no network I/O). A real hosted provider is returned ONLY when the caller
    opts in via ``MVP_HOSTED_PROVIDER=google_ai_studio`` — since 2026-08-10 (Thomas) the
    environment IS the gate: no per-machine grant record backs it, and revoking means
    unsetting the variable and restarting the process. An unset or unrecognized value
    still selects the inert Mock, never any capable path.

    The env var also accepts an ordered, comma-separated **failover chain**
    (``MVP_HOSTED_PROVIDER=openrouter,google_ai_studio,groq``): a chain with an unknown
    or duplicate member fails closed entirely (it never silently shrinks), and at run
    time the next member is tried when the previous one failed for a reason of its own —
    see :func:`failover_kind` (review D1, Thomas 2026-09-26) — never on a request-shaped 4xx.

    The gate ordering lives in ``safety_gate.select_env_gated_chain``, shared semantics
    with the validator and frontdesk chains. Model names are read inside the gated
    factories — they only matter once the gate has already opened. ``now``/``root`` are
    retained for interface stability; the env gate needs neither.
    """
    del now, root  # the environment is the gate (Thomas 2026-08-10)
    chain = safety_gate.select_env_gated_chain(
        env_var=HOSTED_PROVIDER_ENV,
        factories=_hosted_factories(),
        flags=_NETWORK_FLAGS,
        default_factory=MockProvider,
    )
    return chain[0] if len(chain) == 1 else FailoverProvider(chain)


def _slug_from_env(env_var: str, default: str) -> str:
    """This machine's model slug for one vendor, or the code default when it named none.

    ``os.environ.get(env_var, default)`` is the obvious spelling and it is wrong here, in a
    way that cost nine days of silent outage. Compose forwards these slugs as
    ``${MVP_GROQ_MODEL:-}``, which does NOT omit the key when ``.env`` names no value — it
    injects the key with an empty string. The key is therefore present, ``.get``'s default
    is never reached, and the vendor is asked for the model named ``""``. Groq answers that
    with HTTP 404 (*"The model `` does not exist"*), classified ``PROVIDER_TRANSPORT``: no
    retry, no failover, and indistinguishable from the decommissioned-slug outage #790 had
    just fixed.

    #801 wired the slugs through so a vendor retirement would be an ``.env`` edit instead
    of a rebuild, on the stated belief that "unset keeps the code default"; that belief was
    this function's absence, and the passthrough silently un-did #790 on every service it
    reached. Measured 2026-09-07: eight consecutive proposer fires at zero proposals, a
    third weekly data review degraded, and the front desk answering only slash commands —
    while ``DEFAULT_GROQ_MODEL`` sat correct and unreachable in the image.

    So: **empty means unset**, which is the contract the compose comment already advertises
    and the shape :func:`select_validator_provider` already uses for the selector it reads.
    The deployment form is not the bug and is left alone — for a *selector* an empty value
    is the inert path and correctly means "off"; a model slug has no inert value, because
    there is no such thing as a request for no model.
    """
    return (os.environ.get(env_var) or "").strip() or default


def _hosted_factories() -> dict[str, Any]:
    """The gated hosted-provider factories, shared by the specialist and validator
    selections — one catalogue, so a provider cannot exist for one selection and not the
    other, and both read the model-name env vars only after their gate has opened."""
    return {
        GOOGLE_AI_STUDIO: lambda authorization: GoogleAIStudioProvider(
            model=_slug_from_env(HOSTED_MODEL_ENV, DEFAULT_HOSTED_MODEL),
            authorization=authorization,
        ),
        GROQ: lambda authorization: GroqProvider(
            model=_slug_from_env(GROQ_MODEL_ENV, DEFAULT_GROQ_MODEL),
            authorization=authorization,
        ),
        OPENROUTER: lambda authorization: OpenRouterProvider(
            model=_slug_from_env(OPENROUTER_MODEL_ENV, DEFAULT_OPENROUTER_MODEL),
            authorization=authorization,
        ),
    }


def _tier_factories() -> dict[str, Any]:
    """The gated factories for the M2 difficulty tiers. Each reads its own model-slug env
    only after its gate has opened, exactly like ``_hosted_factories``."""
    return {
        OPENROUTER_LIGHT: lambda authorization: OpenRouterLightProvider(
            model=_slug_from_env(OPENROUTER_MODEL_LIGHT_ENV, DEFAULT_OPENROUTER_MODEL_LIGHT),
            authorization=authorization,
        ),
        OPENROUTER_STANDARD: lambda authorization: OpenRouterStandardProvider(
            model=_slug_from_env(OPENROUTER_MODEL_STANDARD_ENV, DEFAULT_OPENROUTER_MODEL_STANDARD),
            authorization=authorization,
        ),
        OPENROUTER_HEAVY: lambda authorization: OpenRouterHeavyProvider(
            model=_slug_from_env(OPENROUTER_MODEL_HEAVY_ENV, DEFAULT_OPENROUTER_MODEL_HEAVY),
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
    nothing to upgrade, or (b) the chosen tier is not named in ``MVP_OPENROUTER_TIERS``
    (the environment is the gate since 2026-08-10), in which case the run degrades to the
    already-authorized base chain and records ``TIER_DEGRADED`` (the SEARCH_DEGRADED
    precedent — the tier benefit is lost, the run is not). Only when the tier gate opens
    is the tier provider built, from its own Authorization, and it serves in place of the
    base for the specialist call."""
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
    del now, root  # the environment is the gate (Thomas 2026-08-10)
    provider, blocked_reason = safety_gate.select_env_gated_optional(
        env_var=OPENROUTER_TIERS_ENV, flags=_NETWORK_FLAGS, provider_id=tier_id,
        gated_factory=_tier_factories()[tier_id],
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
    same chokepoint and rules as ``MVP_HOSTED_PROVIDER``: the environment is the gate
    (Thomas 2026-08-10), an unknown or duplicate member fails the whole selection closed,
    and an unrecognized single value selects the inert Mock, never any capable path.
    """
    if not os.environ.get(VALIDATOR_PROVIDER_ENV, "").strip():
        return None
    del now, root  # the environment is the gate (Thomas 2026-08-10)
    chain = safety_gate.select_env_gated_chain(
        env_var=VALIDATOR_PROVIDER_ENV,
        factories=_hosted_factories(),
        flags=_NETWORK_FLAGS,
        default_factory=MockProvider,
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
        # §8.5: set only by bind_role_output_keys; None means the analysis shape.
        self._role_output_spec: dict[str, str] | None = None


    def bind_role_output_keys(self, role_output_spec: Mapping[str, str]) -> "GoogleAIStudioProvider":
        """A copy of this provider that also asks for a Role's declared output keys (§8.5).

        A copy rather than a mutation: the provider is selected once per process and a run
        binding a Role must not change what the next run asks for. Carries the same
        ``Authorization`` object, so binding grants nothing and cannot outlive the authorization —
        the egress check still runs against it at call time."""
        bound = GoogleAIStudioProvider(model=self._model, api_key_env=self._api_key_env,
                                       authorization=self._authorization)
        bound._role_output_spec = dict(role_output_spec)
        return bound

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
                "responseSchema": analysis_response_schema(self._role_output_spec),
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

    Same guarantees as :class:`GoogleAIStudioProvider`: gate-authorized per its own opt-in
    (``model_id`` IS the provider id the Safety-Flag Gate authorizes against — one opt-in
    name per id), key read from ``api_key_env`` **by name** at call time and sent in the
    Authorization header (never stored, logged, or echoed), egress re-verified at the
    moment of the call, and the shared retry/latency/typed-failure HTTP path.
    """

    network_egress = True  # makes an outbound HTTPS call — audited as network egress
    model_id: str = ""
    _ENDPOINT: str = ""
    _DEFAULT_MODEL: str = ""
    _API_KEY_ENV: str = ""
    # How this vendor is asked to constrain its output. The default ``json_object`` only
    # guarantees syntactically valid JSON, NOT the 12-key shape — a subclass whose gateway
    # can ENFORCE the schema server-side overrides this (see OpenRouterProvider). Groq keeps
    # the default deliberately: its json_schema support is model-dependent and a rejected
    # body fails the call outright (PROVIDER_TRANSPORT, not retryable).
    _RESPONSE_FORMAT: dict[str, Any] = {"type": "json_object"}


    def bind_role_output_keys(self, role_output_spec: Mapping[str, str]) -> "_OpenAICompatibleProvider":
        """A copy of this provider that also asks for a Role's declared output keys (§8.5).

        A copy rather than a mutation: the provider is selected once per process and a run
        binding a Role must not change what the next run asks for. Carries the same
        ``Authorization`` object, so binding grants nothing and cannot outlive the authorization —
        the egress check still runs against it at call time."""
        bound = type(self)(model=self._model, api_key_env=self._api_key_env,
                           authorization=self._authorization)
        bound._role_output_spec = dict(role_output_spec)
        return bound

    def _response_format(self) -> dict[str, Any]:
        """This call's response_format. The base ``json_object`` constrains no keys, so a
        Role's keys need nothing folded in — the prompt already asks for them and the vendor
        does not reject what it does not enforce. A subclass that ENFORCES a schema must
        override, or binding a Role would silently produce a body rejecting its own keys."""
        return self._RESPONSE_FORMAT

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
        # §8.5: set only by bind_role_output_keys; None means the analysis shape.
        self._role_output_spec: dict[str, str] | None = None

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
            "response_format": self._response_format(),
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
    opt-in scope — one opt-in here covers whatever slug is configured, unlike every other
    provider id.

    Unlike Groq, this gateway is asked to ENFORCE the analysis shape server-side via a
    strict ``json_schema`` response format (``_ANALYSIS_JSON_SCHEMA``), the same guarantee
    Google gives through ``responseSchema``. Plain ``json_object`` only promises valid JSON,
    not the 12 required keys, and reasoning models (e.g. ``openai/gpt-oss-20b``) intermittently
    drop keys or truncate — which the parser can only reject after the call is paid for
    (MALFORMED_RESPONSE, a failure class the failover chain does NOT switch on). Enforcing the
    schema turns that intermittent post-hoc rejection into a server-side guarantee. Caveat:
    the configured model must advertise ``structured_outputs``; a model that does not will
    have the body rejected outright (a clear config error, not a silent wrong answer). The
    parser's required-key guard stays as the backstop.
    """

    model_id = OPENROUTER
    _ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
    _DEFAULT_MODEL = DEFAULT_OPENROUTER_MODEL
    _API_KEY_ENV = "OPENROUTER_API_KEY"
    def _response_format(self) -> dict[str, Any]:
        """Overridden because this gateway ENFORCES the schema: a bound Role's keys have to
        reach the body, or strict mode rejects the very fields the prompt asked for.

        A method rather than the `_RESPONSE_FORMAT` constant this used to be — the schema is
        now derived per call from whatever Role is bound, so a constant could only ever hold
        the unbound case and would sit there looking authoritative while nothing read it."""
        return {
            "type": "json_schema",
            "json_schema": {"name": "analysis", "strict": True,
                            "schema": analysis_json_schema(self._role_output_spec)},
        }


class OpenRouterLightProvider(OpenRouterProvider):
    """M2 LOW-difficulty tier. Same OpenRouter gateway/key; its OWN ``model_id`` so the
    Safety-Flag Gate authorizes it against its own ``MVP_OPENROUTER_TIERS`` entry, and
    its own default slug."""

    model_id = OPENROUTER_LIGHT
    _DEFAULT_MODEL = DEFAULT_OPENROUTER_MODEL_LIGHT


class OpenRouterStandardProvider(OpenRouterProvider):
    """M2 MEDIUM-difficulty tier — its own opt-in + slug, OpenRouter gateway shared."""

    model_id = OPENROUTER_STANDARD
    _DEFAULT_MODEL = DEFAULT_OPENROUTER_MODEL_STANDARD


class OpenRouterHeavyProvider(OpenRouterProvider):
    """M2 HIGH-difficulty tier — its own opt-in + slug, OpenRouter gateway shared."""

    model_id = OPENROUTER_HEAVY
    _DEFAULT_MODEL = DEFAULT_OPENROUTER_MODEL_HEAVY


# --- which failures move a chain to its next member (review D1, Thomas 2026-09-26) -------------
#
# Until 2026-09-26 a chain switched on PROVIDER_UNAVAILABLE (429/503) alone: "a 4xx will not change
# with a different vendor". That holds for a request-shaped 4xx and not for the failures this
# repository actually recorded — Groq's decommissioned slug (HTTP 404, every path down at once,
# 2026-08-29) and an empty model slug (nine silent days). Those belong to ONE member: its key, its
# slug, its server, its model's output. The next vendor answers them differently, so the chain moves
# on — and records why, so a broken key cannot hide behind a working fallback.
#
# What does NOT move the chain: a request-shaped 4xx (400, 413, 422, …), which every vendor will
# refuse the same way, and anything that is not a ProviderError (the Safety-Flag Gate's refusal
# above all — an unauthorized egress is never "try the next one").
FAILOVER_CONFIGURATION = "configuration"   # no key, 401/403/404: this member's setup is wrong
FAILOVER_UNAVAILABLE = "unavailable"       # 429/503 after the member's own retry: "not now"
FAILOVER_SERVER = "server"                 # any other 5xx
FAILOVER_TRANSPORT = "transport"           # timeout, refused or dropped connection
FAILOVER_MALFORMED = "malformed"           # the member's model answered in an unusable shape
_CONFIGURATION_STATUSES = frozenset({401, 403, 404})

# Each member but the last gets an equal share of the call's budget, capped at this — so a hung
# first member cannot eat the whole ``max_runtime_seconds`` (120 s) and leave nothing for the rest.
# The three-member analysis chain gets 40 s per member; the last member always gets whatever is left.
FAILOVER_MEMBER_TIMEOUT_SECONDS = 45
# Below this, trying another member is a request that cannot finish; the chain stops instead.
_FAILOVER_MIN_MEMBER_SECONDS = 5
# How much of a failed member's reason is kept on the record. The reasons are the adapters' own
# typed messages (an HTTP status and vendor code, an env var NAME) — never a URL, key or prompt.
_FAILOVER_REASON_LIMIT = 200


def failover_kind(exc: BaseException) -> str | None:
    """Why a chain may move past a member that raised ``exc`` — one of the ``FAILOVER_*`` kinds —
    or ``None`` when it must not (the error propagates as it did before D1)."""
    if not isinstance(exc, ProviderError):
        return None
    data = exc.data if isinstance(exc.data, Mapping) else {}
    status = data.get("http_status")
    if exc.reason_code == "PROVIDER_UNAVAILABLE":
        return FAILOVER_UNAVAILABLE
    if exc.reason_code == "NO_API_KEY":
        return FAILOVER_CONFIGURATION
    if exc.reason_code == "MALFORMED_RESPONSE":
        return FAILOVER_MALFORMED
    if exc.reason_code != "PROVIDER_TRANSPORT":
        return None
    if isinstance(status, int):
        if status in _CONFIGURATION_STATUSES:
            return FAILOVER_CONFIGURATION
        if status >= 500:
            return FAILOVER_SERVER
        if data.get("error_code") in _RETRYABLE_ERROR_CODES:
            return FAILOVER_MALFORMED
        return None
    if data.get("transport"):
        return FAILOVER_TRANSPORT
    return None


class FailoverProvider:
    """Ordered failover across gate-authorized providers.

    Composition only — every member was already built from its own
    :class:`safety_gate.Authorization` by ``select_env_gated_chain``, so this class holds no
    authority of its own and adds none. The next member is tried when the previous one failed for
    a reason of its OWN (:func:`failover_kind`: configuration, unavailable, server, transport,
    malformed — review D1, Thomas 2026-09-26); a request-shaped failure propagates at once. Each
    member but the last is capped at :data:`FAILOVER_MEMBER_TIMEOUT_SECONDS` so the chain fits the
    call's ``timeout_seconds``.

    The returned :class:`ProviderResult` carries the SERVING member's ``model_id``/
    ``model_version`` and, in ``failovers``, every member it moved past and why — a failover that
    reads as the primary would hide instability from the ledger, and a failover whose reason is
    not kept would hide a broken key behind a working fallback.
    """

    network_egress = True  # every member is a network provider by construction

    def __init__(self, providers: list[Any]):
        if len(providers) < 2:
            raise ProviderError("INVALID_CHAIN", "a failover chain needs at least two providers")
        self._providers = list(providers)
        # Named for banners/diagnostics; the serving member's id lands in each result.
        self.model_id = "+".join(getattr(p, "model_id", "?") for p in self._providers)
        self.model_version = self.model_id

    def bind_role_output_keys(self, role_output_spec: Mapping[str, str]) -> "FailoverProvider":
        """Bind EVERY member. Binding only the first would work until the first 503, and then
        quietly serve a failover answer shaped for a different Role — the kind of bug that
        only appears during an outage. A member that cannot bind fails the whole chain closed
        rather than being skipped: a chain that silently shrinks is what the locked provider
        decision already forbids."""
        bound = []
        for provider in self._providers:
            binder = getattr(provider, "bind_role_output_keys", None)
            if binder is None:
                raise ProviderError(
                    "ROLE_BINDING_UNSUPPORTED",
                    f"provider {getattr(provider, 'model_id', '?')} cannot be bound to a Role's "
                    "output contract; the chain refuses rather than answering for one member",
                )
            bound.append(binder(role_output_spec))
        return FailoverProvider(bound)

    def generate(self, prompt: str, *, max_output_tokens: int, timeout_seconds: int) -> ProviderResult:
        deadline = time.monotonic() + float(timeout_seconds)
        per_member = max(_FAILOVER_MIN_MEMBER_SECONDS,
                         min(FAILOVER_MEMBER_TIMEOUT_SECONDS, int(timeout_seconds) // len(self._providers)))
        failovers: list[dict[str, Any]] = []
        for index, provider in enumerate(self._providers):
            member = str(getattr(provider, "model_id", "?"))
            remaining = deadline - time.monotonic()
            if remaining < _FAILOVER_MIN_MEMBER_SECONDS:
                failovers.append({"member": member, "kind": "not_tried",
                                  "reason_code": "BUDGET_EXHAUSTED",
                                  "reason": "the call's time budget ran out before this member"})
                break
            is_last = index == len(self._providers) - 1
            member_timeout = int(remaining) if is_last else int(min(remaining, per_member))
            try:
                result = provider.generate(
                    prompt, max_output_tokens=max_output_tokens, timeout_seconds=max(1, member_timeout)
                )
            except ProviderError as exc:
                kind = failover_kind(exc)
                if kind is None:
                    raise
                failovers.append({"member": member, "kind": kind, "reason_code": exc.reason_code,
                                  "reason": str(exc.reason)[:_FAILOVER_REASON_LIMIT]})
                continue
            return replace(result, failovers=tuple(failovers)) if failovers else result
        # Every member failed for a reason of its own. The all-"not now" case keeps the code and
        # wording it always had; anything else names each member's reason, because "unavailable"
        # would be a lie about a chain whose first member has no key.
        summary = "; ".join(f"{f['member']}: {f['reason']}" for f in failovers)
        if all(f["kind"] == FAILOVER_UNAVAILABLE for f in failovers):
            raise ProviderError(
                "PROVIDER_UNAVAILABLE",
                f"every provider in the failover chain is unavailable (last: {failovers[-1]['reason']})",
                data={"failovers": failovers},
            ) from None
        raise ProviderError(
            "PROVIDER_CHAIN_EXHAUSTED",
            f"every provider in the failover chain failed ({summary})",
            data={"failovers": failovers},
        ) from None

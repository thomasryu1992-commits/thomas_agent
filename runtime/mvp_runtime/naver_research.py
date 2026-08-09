"""Read-only tools — Naver keyword research for the blog content lane.

Three read-only adapters behind one gate, each answering a different question a post has
to answer before it is worth writing:

- :class:`SearchAdKeywordTool` — how many people search this, monthly, on PC and mobile
  (Naver Search Ad ``/keywordstool``). The only official source of **absolute** volume.
- :class:`DatalabTrendTool` — is that demand rising, flat, or seasonal (Datalab search
  trend). Relative ratios only; Naver does not publish absolute numbers here.
- :class:`BlogCompetitionTool` — how many posts already target it, and what they look
  like (Search API, blog vertical).

Same shape as ``tools.py``: a Protocol, a deterministic network-free Mock, a real
network adapter that re-verifies its authorization at the moment of egress, a
``run_*`` that returns ``(payload, tool_use_record)``, and a ``degraded_*`` record so a
failed backend is recorded rather than silent. Nothing here performs an external action —
the lane's only write is a draft package a human reads before publishing.

**Gate: environment opt-in, not a grant record** (Thomas decision, 2026-08-09). Every other
network capability in this runtime opens via ``safety_gate.select_gated`` against a local
integrity-checked grant. This one uses ``select_env_gated``, the path live trading moved to
on 2026-07-28, and the reasoning is the same shape but a different premise:

- ``activate_safety_flag.py`` caps a grant at 30 days (``MAX_TTL_MINUTES``). This lane runs
  on a weekly schedule, so a grant would need re-minting every month, and an expiry
  between two fires stops the lane **silently** — no error, just no ideas that week.
- What made expiry dangerous for live trading was that it could trap an OPEN position by
  blocking the close path. Nothing here can be trapped: an expired gate means a week of
  keyword research did not run, and the next fire recovers on its own.

What is given up is revocation by deleting a file. Revoking this gate means unsetting
``MVP_NAVER_RESEARCH`` and restarting the container. That is a real downgrade, accepted
deliberately for a read-only capability whose worst failure is a missing draft.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol

from runtime.read_only_kernel import integrity

from . import safety_gate, timeutil
from .errors import ToolBlocked, ToolError
from .safety_gate import NETWORK_ACCESS, Authorization

KEYWORD_TOOL_ID = "naver.keyword_research"
TREND_TOOL_ID = "naver.search_trend"
COMPETITION_TOOL_ID = "naver.blog_competition"
TOOL_VERSION = "0.1.0"
TOOL_CLASS = "read"

MAX_SEED_CHARS = 200
# `/keywordstool` accepts at most 5 hint keywords per call.
MAX_HINT_KEYWORDS = 5

# The gate. Unlike MVP_SEARCH_TOOL (which is an opt-in that still needs a grant), this env
# var IS the gate — see the module docstring for why this capability is the exception.
NAVER_RESEARCH_ENV = "MVP_NAVER_RESEARCH"
NAVER_RESEARCH_ON = "enabled"

# Two provider ids behind one env gate. Splitting the gate as well was considered and not
# built: with an env-only gate, whoever can set one var can set the other, so two vars would
# suggest a separation that does not exist. The ids stay distinct because the *audit* record
# should still say which of Naver's two API families a given call went to — they have
# different credentials, different quotas, and different failure modes.
SEARCHAD_PROVIDER = "naver_searchad"
OPENAPI_PROVIDER = "naver_openapi"

# The credential env var NAMES, as module constants rather than only as default arguments.
# The deployment drift gate (`tests/test_deployment_env_passthrough.py`) names them from here,
# so renaming one cannot silently empty that list and leave the capability unreachable on the
# deployed service — the exact failure that gate exists for.
#
# The Search Ad credential is **account-wide, not scoped to the keyword tool**: the same
# signing secret reaches campaign-management endpoints that can change ad spend. That is why
# the passthrough list is deliberately narrow (scheduler + dispatch-bridge, not operator) —
# read-only USE does not make a read-only KEY.
SEARCHAD_CUSTOMER_ID_ENV = "NAVER_SEARCHAD_CUSTOMER_ID"
SEARCHAD_API_KEY_ENV = "NAVER_SEARCHAD_API_KEY"
SEARCHAD_SECRET_KEY_ENV = "NAVER_SEARCHAD_SECRET_KEY"
OPENAPI_CLIENT_ID_ENV = "NAVER_CLIENT_ID"
OPENAPI_CLIENT_SECRET_ENV = "NAVER_CLIENT_SECRET"

# Read-only lookups cross the network but never invoke a model.
_NETWORK_FLAGS = (NETWORK_ACCESS,)

# Naver returns this string instead of a number when a keyword's monthly count is under 10.
# It is a *value* in an otherwise integer field, so every consumer that does arithmetic on
# these counts has to agree on what it means. `_coerce_count` is that one agreement.
_LOW_VOLUME_SENTINEL = "< 10"
_LOW_VOLUME_VALUE = 5  # midpoint of [0, 10) — deliberately not 0, which would read as "no demand"

_COMPETITION_LEVELS = frozenset({"높음", "중간", "낮음"})


@dataclass
class KeywordMetric:
    """One keyword with its measured demand. ``monthly_total`` is the ranking number."""

    keyword: str
    monthly_pc: int
    monthly_mobile: int
    competition: str
    source: str
    # True when either count came back as the "< 10" sentinel — the number is an estimate,
    # and a caller ranking keywords should know which rows are not really measured.
    low_volume: bool = False

    @property
    def monthly_total(self) -> int:
        return self.monthly_pc + self.monthly_mobile


@dataclass
class KeywordResult:
    seed: str
    metrics: list[KeywordMetric]
    tool_id: str = KEYWORD_TOOL_ID
    tool_version: str = TOOL_VERSION
    latency_ms: int = 0


@dataclass
class TrendPoint:
    period: str
    ratio: float


@dataclass
class TrendResult:
    keyword: str
    points: list[TrendPoint] = field(default_factory=list)
    tool_id: str = TREND_TOOL_ID
    tool_version: str = TOOL_VERSION
    latency_ms: int = 0


@dataclass
class CompetitionResult:
    keyword: str
    # Naver's reported total matching documents — the competition proxy.
    total_posts: int
    recent_titles: list[str] = field(default_factory=list)
    tool_id: str = COMPETITION_TOOL_ID
    tool_version: str = TOOL_VERSION
    latency_ms: int = 0


class KeywordTool(Protocol):
    tool_id: str
    tool_version: str

    def keywords(self, seed: str, *, max_results: int, timeout_seconds: int) -> KeywordResult: ...


class TrendTool(Protocol):
    tool_id: str
    tool_version: str

    def trend(self, keyword: str, *, start_date: str, end_date: str, timeout_seconds: int) -> TrendResult: ...


class CompetitionTool(Protocol):
    tool_id: str
    tool_version: str

    def competition(self, keyword: str, *, display: int, timeout_seconds: int) -> CompetitionResult: ...


def _coerce_count(value: Any) -> tuple[int, bool]:
    """``(count, low_volume)`` from a field that is usually an int and sometimes ``"< 10"``.

    Returns ``low_volume=True`` only for the sentinel, so a genuine 5 and an estimated 5 stay
    distinguishable downstream. Anything unparseable is 0 rather than an exception: one odd
    row must not lose the other 99 keywords in the same response.
    """
    if isinstance(value, bool):  # bool is an int subclass; never a count
        return 0, False
    if isinstance(value, int):
        return max(0, value), False
    text = str(value).strip()
    if text == _LOW_VOLUME_SENTINEL or text.startswith("<"):
        return _LOW_VOLUME_VALUE, True
    try:
        return max(0, int(text.replace(",", ""))), False
    except ValueError:
        return 0, False


def _require_seed(seed: Any) -> str:
    if not isinstance(seed, str) or not seed.strip():
        raise ToolBlocked("EMPTY_SEED", "keyword seed must be a non-empty string")
    if len(seed) > MAX_SEED_CHARS:
        raise ToolBlocked("SEED_TOO_LONG", f"keyword seed exceeds {MAX_SEED_CHARS} characters")
    return seed.strip()


class MockKeywordTool:
    """Deterministic, network-free keyword tool for tests and pre-gate pipeline runs.

    Exists for the same reason ``MockSearchTool`` does: the whole content lane —
    ranking, package generation, schema validation — is built and tested before any
    credential exists. The numbers are shaped like real ones (mobile > PC, a decaying
    tail, one low-volume row) so a consumer that only works on tidy data fails here
    rather than in production.
    """

    tool_id = KEYWORD_TOOL_ID
    tool_version = TOOL_VERSION
    network_egress = False

    def keywords(self, seed: str, *, max_results: int, timeout_seconds: int) -> KeywordResult:
        metrics: list[KeywordMetric] = []
        for i in range(max(1, min(max_results, 10))):
            # Last row is deliberately the low-volume sentinel case.
            low = i == min(max_results, 10) - 1
            metrics.append(KeywordMetric(
                keyword=f"{seed} 활용법{'' if i == 0 else f' {i + 1}'}",
                monthly_pc=_LOW_VOLUME_VALUE if low else max(10, 900 // (i + 1)),
                monthly_mobile=_LOW_VOLUME_VALUE if low else max(10, 4200 // (i + 1)),
                competition="낮음" if i % 3 == 0 else "중간",
                source="mock.naver_searchad",
                low_volume=low,
            ))
        return KeywordResult(seed=seed, metrics=metrics, latency_ms=0)


class MockTrendTool:
    tool_id = TREND_TOOL_ID
    tool_version = TOOL_VERSION
    network_egress = False

    def trend(self, keyword: str, *, start_date: str, end_date: str, timeout_seconds: int) -> TrendResult:
        points = [TrendPoint(period=f"2026-{m:02d}-01", ratio=float(40 + m * 4)) for m in range(1, 7)]
        return TrendResult(keyword=keyword, points=points, latency_ms=0)


class MockCompetitionTool:
    tool_id = COMPETITION_TOOL_ID
    tool_version = TOOL_VERSION
    network_egress = False

    def competition(self, keyword: str, *, display: int, timeout_seconds: int) -> CompetitionResult:
        titles = [f"[목업] {keyword} 관련 글 {i + 1}" for i in range(min(display, 3))]
        return CompetitionResult(keyword=keyword, total_posts=12_345, recent_titles=titles, latency_ms=0)


def run_keyword_research(
    seed: str,
    *,
    tool: KeywordTool,
    now: str,
    max_results: int = 10,
    timeout_seconds: int = 10,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Run one keyword lookup. Returns ``(metrics, tool_use_record)``.

    Metrics come back sorted by ``monthly_total`` descending — the order a human picking a
    topic actually wants, applied once here rather than in each caller. Fails closed
    (``ToolBlocked``) on an invalid seed or a backend error.
    """
    seed = _require_seed(seed)
    try:
        result = tool.keywords(seed, max_results=max_results, timeout_seconds=timeout_seconds)
    except (ToolError, TimeoutError) as exc:
        raise ToolBlocked("TOOL_ERROR", str(exc)) from exc

    metrics = [
        {
            "keyword": m.keyword,
            "monthly_pc": m.monthly_pc,
            "monthly_mobile": m.monthly_mobile,
            "monthly_total": m.monthly_total,
            "competition": m.competition,
            "low_volume": m.low_volume,
            "source": m.source,
        }
        for m in result.metrics
        if isinstance(m, KeywordMetric) and m.keyword
    ]
    metrics.sort(key=lambda m: m["monthly_total"], reverse=True)

    record = {
        "tool_id": tool.tool_id,
        "tool_version": tool.tool_version,
        "tool_class": TOOL_CLASS,
        "operation": "keyword_research",
        "query": seed,
        "input_sha256": integrity.sha256_record({"tool_id": tool.tool_id, "seed": seed}),
        "result_count": len(metrics),
        "sources": sorted({m["source"] for m in metrics}),
        # The rows themselves, exactly as handed to the specialist — the same reason
        # tools.run_search stores its hits: a package citing a search volume must be
        # resolvable back to the row it came from.
        "metrics": metrics,
        "output_sha256": integrity.sha256_record({"metrics": metrics}),
        "latency_ms": int(result.latency_ms),
        "read_only": True,
        "external_action": False,
        "network_egress": bool(getattr(tool, "network_egress", False)),
        "created_at": now,
    }
    return metrics, record


def degraded_keyword_record(tool: KeywordTool, seed: str, reason_code: str, *, now: str) -> dict[str, Any]:
    """The record for a keyword lookup whose backend failed — recorded, never silent.

    Same posture as ``tools.degraded_search_record``: research is what makes a topic
    *defensible*, so losing it is not fatal to the run. But it IS fatal to the claim the
    package makes, so the lane must never present an unresearched topic as a researched
    one — a package built on this record carries no volume numbers at all rather than
    stale or invented ones.
    """
    return {
        "tool_id": getattr(tool, "tool_id", KEYWORD_TOOL_ID),
        "tool_version": getattr(tool, "tool_version", TOOL_VERSION),
        "tool_class": TOOL_CLASS,
        "operation": "keyword_research",
        "query": seed,
        "input_sha256": integrity.sha256_record(
            {"tool_id": getattr(tool, "tool_id", KEYWORD_TOOL_ID), "seed": seed}
        ),
        "result_count": 0,
        "sources": [],
        "metrics": [],
        "output_sha256": integrity.sha256_record({"metrics": []}),
        "latency_ms": 0,
        "read_only": True,
        "external_action": False,
        "network_egress": bool(getattr(tool, "network_egress", False)),
        "degraded": True,
        "degraded_reason_code": reason_code,
        "created_at": now,
    }


def _select(provider_id: str, gated_factory: Any, default_factory: Any) -> Any:
    """The shared env-only gate for this lane. See the module docstring for the decision."""
    return safety_gate.select_env_gated(
        env_var=NAVER_RESEARCH_ENV,
        opt_in_value=NAVER_RESEARCH_ON,
        flags=_NETWORK_FLAGS,
        provider_id=provider_id,
        default_factory=default_factory,
        gated_factory=gated_factory,
    )


def select_keyword_tool() -> KeywordTool:
    """Choose the keyword tool — deterministic Mock unless ``MVP_NAVER_RESEARCH=enabled``."""
    return _select(
        SEARCHAD_PROVIDER,
        lambda authorization: SearchAdKeywordTool(authorization=authorization),
        MockKeywordTool,
    )


def select_trend_tool() -> TrendTool:
    return _select(
        OPENAPI_PROVIDER,
        lambda authorization: DatalabTrendTool(authorization=authorization),
        MockTrendTool,
    )


def select_competition_tool() -> CompetitionTool:
    return _select(
        OPENAPI_PROVIDER,
        lambda authorization: BlogCompetitionTool(authorization=authorization),
        MockCompetitionTool,
    )


class SearchAdKeywordTool:
    """Real keyword volume via the Naver Search Ad API (read-only).

    Authentication is an HMAC-SHA256 signature over ``timestamp.method.path`` rather than a
    bearer token, so this adapter needs three env vars where the others need one. The secret
    is read **by name** at call time, used only to sign, and never stored, logged, or echoed
    in an error — the signature goes on the wire, the key never does.

    ``/keywordstool`` is a GET whose signed path is the path alone: the query string is
    excluded from the signed message. Signing the full URL is the usual way this integration
    fails, and it fails as a 401 that looks like a bad key.
    """

    tool_id = KEYWORD_TOOL_ID
    tool_version = f"{TOOL_VERSION}-searchad"
    provider_id = SEARCHAD_PROVIDER
    network_egress = True
    _BASE = "https://api.searchad.naver.com"
    _PATH = "/keywordstool"

    def __init__(
        self,
        *,
        customer_id_env: str = SEARCHAD_CUSTOMER_ID_ENV,
        api_key_env: str = SEARCHAD_API_KEY_ENV,
        secret_key_env: str = SEARCHAD_SECRET_KEY_ENV,
        authorization: Authorization | None = None,
    ):
        # NAMES of env vars, never values.
        self._customer_id_env = customer_id_env
        self._api_key_env = api_key_env
        self._secret_key_env = secret_key_env
        self._authorization = authorization

    def _headers(self, method: str, path: str) -> dict[str, str]:
        customer_id = os.environ.get(self._customer_id_env)
        api_key = os.environ.get(self._api_key_env)
        secret_key = os.environ.get(self._secret_key_env)
        missing = [
            name
            for name, value in (
                (self._customer_id_env, customer_id),
                (self._api_key_env, api_key),
                (self._secret_key_env, secret_key),
            )
            if not value
        ]
        if missing:
            # Names only — a message that echoed a value would put a secret in the ledger.
            raise ToolError("NO_API_KEY", f"environment variables not set: {', '.join(missing)}")

        timestamp = str(int(time.time() * 1000))
        message = f"{timestamp}.{method}.{path}"
        signature = base64.b64encode(
            hmac.new(secret_key.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).digest()
        ).decode("utf-8")
        return {
            "Content-Type": "application/json; charset=UTF-8",
            "X-Timestamp": timestamp,
            "X-API-KEY": api_key,
            "X-Customer": str(customer_id),
            "X-Signature": signature,
        }

    def keywords(self, seed: str, *, max_results: int, timeout_seconds: int) -> KeywordResult:
        # Chokepoint: re-verify authorization at the moment of egress (defense in depth).
        safety_gate.assert_authorization(
            self._authorization,
            required_flags=_NETWORK_FLAGS,
            provider_id=self.provider_id,
            now=timeutil.utc_now_iso(),
        )
        # The API takes up to 5 hint keywords, comma-joined.
        #
        # OPEN, verify at first real call (Phase 1b): Naver's keyword tool normalises keywords
        # by removing spaces, and several working integrations strip internal whitespace before
        # sending. That is not stated in the reference, so this passes the caller's phrase
        # through trimmed-but-intact rather than guessing. If real calls come back empty for
        # multi-word seeds, stripping internal spaces here is the first thing to try — the
        # failure is loud (no rows), not a silently wrong number.
        hints = ",".join(part.strip() for part in seed.split(",")[:MAX_HINT_KEYWORDS] if part.strip())
        params = urllib.parse.urlencode({"hintKeywords": hints, "showDetail": "1"})
        request = urllib.request.Request(
            f"{self._BASE}{self._PATH}?{params}",
            method="GET",
            headers=self._headers("GET", self._PATH),
        )
        started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=int(timeout_seconds)) as response:
                raw = response.read().decode("utf-8")
        except (TimeoutError, urllib.error.URLError):
            # Deliberately generic — never echo the URL, the signature, or the key.
            raise ToolError("TOOL_TRANSPORT", "keyword request failed or timed out") from None
        latency_ms = int((time.monotonic() - started) * 1000)
        return self._parse(seed, raw, max_results, latency_ms=latency_ms)

    def _parse(self, seed: str, raw: str, max_results: int, *, latency_ms: int = 0) -> KeywordResult:
        try:
            data: dict[str, Any] = json.loads(raw)
            rows = data["keywordList"]
        except (KeyError, ValueError, TypeError):
            raise ToolError("MALFORMED_RESULT", "keyword backend returned an unparseable response") from None
        if not isinstance(rows, list):
            raise ToolError("MALFORMED_RESULT", "keyword backend returned an unparseable response")

        metrics: list[KeywordMetric] = []
        for row in rows[: max(1, max_results)]:
            if not isinstance(row, dict):
                continue
            keyword = row.get("relKeyword")
            if not isinstance(keyword, str) or not keyword:
                continue
            pc, pc_low = _coerce_count(row.get("monthlyPcQcCnt", 0))
            mobile, mobile_low = _coerce_count(row.get("monthlyMobileQcCnt", 0))
            competition = str(row.get("compIdx", "")).strip()
            metrics.append(KeywordMetric(
                keyword=keyword,
                monthly_pc=pc,
                monthly_mobile=mobile,
                # Pass through only the documented vocabulary; anything else becomes
                # "unknown" rather than leaking an unexpected token into the package.
                competition=competition if competition in _COMPETITION_LEVELS else "unknown",
                source=self.provider_id,
                low_volume=pc_low or mobile_low,
            ))
        return KeywordResult(seed=seed, metrics=metrics, tool_version=self.tool_version, latency_ms=latency_ms)


class _OpenApiTool:
    """Shared plumbing for the two Developers-center APIs (Client ID + Secret headers)."""

    provider_id = OPENAPI_PROVIDER
    network_egress = True

    def __init__(
        self,
        *,
        client_id_env: str = OPENAPI_CLIENT_ID_ENV,
        client_secret_env: str = OPENAPI_CLIENT_SECRET_ENV,
        authorization: Authorization | None = None,
    ):
        self._client_id_env = client_id_env  # NAMES, never values
        self._client_secret_env = client_secret_env
        self._authorization = authorization

    def _headers(self) -> dict[str, str]:
        client_id = os.environ.get(self._client_id_env)
        client_secret = os.environ.get(self._client_secret_env)
        missing = [
            name
            for name, value in (
                (self._client_id_env, client_id),
                (self._client_secret_env, client_secret),
            )
            if not value
        ]
        if missing:
            raise ToolError("NO_API_KEY", f"environment variables not set: {', '.join(missing)}")
        return {
            "X-Naver-Client-Id": client_id,
            "X-Naver-Client-Secret": client_secret,
            "Content-Type": "application/json",
        }

    def _fetch(self, url: str, *, timeout_seconds: int, data: bytes | None = None) -> tuple[str, int]:
        safety_gate.assert_authorization(
            self._authorization,
            required_flags=_NETWORK_FLAGS,
            provider_id=self.provider_id,
            now=timeutil.utc_now_iso(),
        )
        request = urllib.request.Request(
            url,
            data=data,
            method="POST" if data is not None else "GET",
            headers=self._headers(),
        )
        started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=int(timeout_seconds)) as response:
                raw = response.read().decode("utf-8")
        except (TimeoutError, urllib.error.URLError):
            raise ToolError("TOOL_TRANSPORT", "naver openapi request failed or timed out") from None
        return raw, int((time.monotonic() - started) * 1000)


class DatalabTrendTool(_OpenApiTool):
    """Relative search-interest trend via the Datalab search API (read-only).

    Datalab returns **ratios normalised to the peak period in the requested window**, never
    absolute counts. So a trend is only comparable within one call — two separate calls
    produce two independent 0..100 scales. The lane uses this to answer "rising or fading",
    and takes absolute demand from :class:`SearchAdKeywordTool` alone.
    """

    tool_id = TREND_TOOL_ID
    tool_version = f"{TOOL_VERSION}-datalab"
    _ENDPOINT = "https://openapi.naver.com/v1/datalab/search"

    def trend(self, keyword: str, *, start_date: str, end_date: str, timeout_seconds: int) -> TrendResult:
        body = json.dumps({
            "startDate": start_date,
            "endDate": end_date,
            "timeUnit": "month",
            "keywordGroups": [{"groupName": keyword, "keywords": [keyword]}],
        }).encode("utf-8")
        raw, latency_ms = self._fetch(self._ENDPOINT, timeout_seconds=timeout_seconds, data=body)
        try:
            data: dict[str, Any] = json.loads(raw)
            series = data["results"][0]["data"]
        except (KeyError, IndexError, ValueError, TypeError):
            raise ToolError("MALFORMED_RESULT", "datalab returned an unparseable response") from None

        points = [
            TrendPoint(period=str(p["period"]), ratio=float(p["ratio"]))
            for p in series
            if isinstance(p, dict) and "period" in p and "ratio" in p
        ]
        return TrendResult(keyword=keyword, points=points, tool_version=self.tool_version, latency_ms=latency_ms)


class BlogCompetitionTool(_OpenApiTool):
    """How crowded a keyword already is, via the Search API's blog vertical (read-only).

    ``total`` is Naver's own count of matching blog documents — the competition proxy the
    lane ranks on. The titles are returned alongside it because "how many" and "what do they
    look like" are the same question when deciding whether a post can differentiate.
    """

    tool_id = COMPETITION_TOOL_ID
    tool_version = f"{TOOL_VERSION}-blogsearch"
    _ENDPOINT = "https://openapi.naver.com/v1/search/blog.json"
    _MAX_DISPLAY = 100  # Search API per-request cap

    def competition(self, keyword: str, *, display: int, timeout_seconds: int) -> CompetitionResult:
        count = max(1, min(int(display), self._MAX_DISPLAY))
        params = urllib.parse.urlencode({"query": keyword, "display": count, "sort": "sim"})
        raw, latency_ms = self._fetch(f"{self._ENDPOINT}?{params}", timeout_seconds=timeout_seconds)
        try:
            data: dict[str, Any] = json.loads(raw)
            total = int(data["total"])
            items = data.get("items", [])
        except (KeyError, ValueError, TypeError):
            raise ToolError("MALFORMED_RESULT", "blog search returned an unparseable response") from None
        if not isinstance(items, list):
            raise ToolError("MALFORMED_RESULT", "blog search returned an unparseable response")

        titles = [
            # The Search API wraps matched terms in <b> tags; strip them so a title is
            # usable as text rather than as markup fragments.
            str(item.get("title", "")).replace("<b>", "").replace("</b>", "")
            for item in items
            if isinstance(item, dict) and item.get("title")
        ]
        return CompetitionResult(
            keyword=keyword,
            total_posts=max(0, total),
            recent_titles=titles,
            tool_version=self.tool_version,
            latency_ms=latency_ms,
        )

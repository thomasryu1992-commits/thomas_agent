"""Read-only tools — Naver keyword research for the blog content lane.

Three read-only adapters behind one gate, each answering a different question a post has
to answer before it is worth writing:

- :class:`SearchAdKeywordTool` — how many people search this, monthly, on PC and mobile
  (Naver Search Ad ``/keywordstool``). The only official source of **absolute** volume.
- :class:`SearchTrendTool` — is that demand rising, flat, or seasonal (API HUB search
  trend). Relative ratios only; Naver does not publish absolute numbers here.
- :class:`BlogCompetitionTool` — how many posts already target it, and what they look
  like (API HUB blog search).

**Two Naver surfaces, not one, and they are unrelated.** Search Ad
(``api.searchad.naver.com``, HMAC-signed, an advertiser account) is a different product from
API HUB (``naverapihub.apigw.ntruss.com``, header keys, a NAVER Cloud Platform account). They
have separate consoles, separate credentials and separate failure modes; nothing about
obtaining or losing one says anything about the other. The absolute volume the lane ranks on
comes only from the first.

**API HUB is where Naver is consolidating the old Developers-center open APIs**
(``openapi.naver.com``), which is why the second and third tools target it rather than the
surface this module was first written against. Two consequences worth stating:

- A Developers-center Client ID does **not** work here — API HUB issues its own credential.
- API HUB is documented as *temporarily* free with paid pricing to follow on notice. When that
  happens, using it becomes autonomous spend, and
  ``governance/GOVERNANCE_POLICY.yaml`` sets ``autonomous_spend_without_registered_budget: '0'``
  — so it will need a registered budget or a Thomas decision, not a quiet continuation. The
  Search Ad half is unaffected by that transition.

Same shape as ``tools.py``: a Protocol, a deterministic network-free Mock, a real
network adapter that re-verifies its authorization at the moment of egress, a
``run_*`` that returns ``(payload, tool_use_record)``, and a ``degraded_*`` record so a
failed backend is recorded rather than silent. Nothing here performs an external action —
the lane's only write is a draft package a human reads before publishing.

**Gate: environment opt-in, not a grant record** (Thomas decision, 2026-08-09). This lane
uses ``select_env_gated``, the path live trading moved to on 2026-07-28 — and since
2026-08-10 every capability gates this way (the grant machinery is removed). The original
reasoning, kept because it argued the premise:

- A grant was TTL-capped at 30 days. This lane runs on a weekly schedule, so a grant would
  need re-minting every month, and an expiry between two fires stops the lane
  **silently** — no error, just no ideas that week.
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
# Was `naver_openapi` while this targeted `openapi.naver.com`. Renamed with the migration
# rather than left alone: the id lands in audit records, and a record naming the surface a
# call did NOT go to is worse than no id at all.
APIHUB_PROVIDER = "naver_apihub"

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
# API HUB is a NAVER Cloud Platform credential, not a Developers-center one, and the names say
# so: `NAVER_CLIENT_ID` would read as the Developers-center "Client ID" that does NOT work here.
APIHUB_KEY_ID_ENV = "NAVER_APIHUB_KEY_ID"
APIHUB_KEY_ENV = "NAVER_APIHUB_KEY"

# Read-only lookups cross the network but never invoke a model.
_NETWORK_FLAGS = (NETWORK_ACCESS,)

# Naver returns this string instead of a number when a keyword's monthly count is under 10.
# It is a *value* in an otherwise integer field, so every consumer that does arithmetic on
# these counts has to agree on what it means. `_coerce_count` is that one agreement.
_LOW_VOLUME_SENTINEL = "< 10"
_LOW_VOLUME_VALUE = 5  # midpoint of [0, 10) — deliberately not 0, which would read as "no demand"

_COMPETITION_LEVELS = frozenset({"높음", "중간", "낮음"})


def _rejected(exc: urllib.error.HTTPError, what: str) -> ToolError:
    """Turn a 4xx into an error that says what was wrong with the REQUEST.

    Written after a live 400 spent a debugging session looking like a network fault: a bad
    `hintKeywords` value raised ``TOOL_TRANSPORT`` — "request failed or timed out" — because
    ``HTTPError`` subclasses ``URLError`` and the transport handler caught it first. The
    status code was the one fact that would have ended it immediately, and it was the one
    fact being discarded.

    So 4xx gets its own reason code and carries the status plus the API's own error fields.
    Everything else (5xx, DNS, TLS, timeout) stays ``TOOL_TRANSPORT``: the split that matters
    is "retrying will not help" versus "try again later". 429 sits on the wrong side of that
    line and is accepted — it is rare here, and a rate-limit message reads clearly either way.

    Still no URL, no header, no key: only the status and the provider's own ``code``/``message``,
    which are diagnostic text Naver generates and never an echo of what was sent.
    """
    try:
        payload = json.loads(exc.read().decode("utf-8"))
        detail = f"code={payload.get('code')} message={str(payload.get('message'))[:120]}"
    except Exception:  # noqa: BLE001 — a body we cannot parse must not replace the status
        detail = "no parseable error body"
    return ToolError("TOOL_REQUEST_REJECTED", f"{what} rejected with HTTP {exc.code}: {detail}")


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


BRIEF_TOOL_ID = "naver.keyword_brief"
# How many top-volume rows get a competition count. Each count is its own API HUB call, so
# this bounds the fan-out of one brief at a number a weekly cadence never notices.
BRIEF_COMPETITION_TOP = 3


def run_keyword_brief(
    seeds: str,
    *,
    now: str,
    keyword_tool: KeywordTool | None = None,
    trend_tool: TrendTool | None = None,
    competition_tool: CompetitionTool | None = None,
    max_keywords: int = 10,
    timeout_seconds: int = 15,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """One evidence bundle for a research/content run: volume, competition, trend.

    Returns ``(rows, record)`` — rows are what the specialist prompt cites ([K#]), the
    record is what the ledger keeps. Tools default to the gated selectors, so with the
    gate closed a brief still runs (deterministic Mocks) and with it open the same call
    reaches Naver — the caller does not choose.

    Failure is per-leg, and the legs are not equal:

    - **volume** (Search Ad) failing degrades the whole brief to zero rows — measured
      demand IS the brief, and rows without it would present a guess as research;
    - **competition** (API HUB) failing leaves ``competing_posts`` absent on that row —
      the row is still real, it just lacks one column, and the record says which legs
      failed rather than averaging the failure away;
    - **trend** failing drops the trend series the same way.

    The brief asks for competition counts only on the top ``BRIEF_COMPETITION_TOP`` rows
    by volume: each count is a separate API HUB call, and a weekly brief has no business
    fanning out further.
    """
    keyword_tool = keyword_tool if keyword_tool is not None else select_keyword_tool()
    trend_tool = trend_tool if trend_tool is not None else select_trend_tool()
    competition_tool = competition_tool if competition_tool is not None else select_competition_tool()

    degraded_legs: dict[str, str] = {}
    try:
        rows, volume_record = run_keyword_research(
            seeds, tool=keyword_tool, now=now,
            max_results=max_keywords, timeout_seconds=timeout_seconds,
        )
    except ToolBlocked as exc:
        rows = []
        volume_record = degraded_keyword_record(keyword_tool, seeds, exc.reason_code, now=now)
        degraded_legs["volume"] = exc.reason_code

    for row in rows[:BRIEF_COMPETITION_TOP]:
        try:
            competition = competition_tool.competition(
                row["keyword"], display=1, timeout_seconds=timeout_seconds
            )
            row["competing_posts"] = competition.total_posts
        except (ToolError, ToolBlocked) as exc:
            # Column absent, not zero: 0 competing posts is a CLAIM (an empty niche), and
            # a failed lookup must not accidentally make it.
            degraded_legs["competition"] = getattr(exc, "reason_code", "TOOL_ERROR")

    trend_points: list[dict[str, Any]] = []
    primary = seeds.split(",")[0].strip()
    if rows and primary:
        try:
            start = f"{int(now[:4]) - 1}{now[4:10]}"  # twelve months back, same day
            trend = trend_tool.trend(
                primary, start_date=start, end_date=now[:10], timeout_seconds=timeout_seconds
            )
            trend_points = [{"period": p.period, "ratio": p.ratio} for p in trend.points]
        except (ToolError, ToolBlocked) as exc:
            degraded_legs["trend"] = getattr(exc, "reason_code", "TOOL_ERROR")

    record = {
        "tool_id": BRIEF_TOOL_ID,
        "tool_version": TOOL_VERSION,
        "tool_class": TOOL_CLASS,
        "operation": "keyword_brief",
        "query": seeds,
        "input_sha256": integrity.sha256_record({"tool_id": BRIEF_TOOL_ID, "seeds": seeds}),
        "result_count": len(rows),
        # Volume sources plus, when at least one competition count landed, the surface it
        # came from — two Naver products fed this record and the audit trail should say so.
        "sources": sorted(
            set(volume_record["sources"])
            | ({getattr(competition_tool, "provider_id", "mock.naver_apihub")}
               if any("competing_posts" in r for r in rows) else set())
        ),
        "metrics": rows,
        "trend_keyword": primary,
        "trend_points": trend_points,
        "output_sha256": integrity.sha256_record({"metrics": rows, "trend_points": trend_points}),
        "latency_ms": int(volume_record["latency_ms"]),
        "read_only": True,
        "external_action": False,
        "network_egress": bool(getattr(keyword_tool, "network_egress", False)),
        "degraded": bool(degraded_legs),
        "degraded_legs": degraded_legs,
        "created_at": now,
    }
    return rows, record


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
        APIHUB_PROVIDER,
        lambda authorization: SearchTrendTool(authorization=authorization),
        MockTrendTool,
    )


def select_competition_tool() -> CompetitionTool:
    return _select(
        APIHUB_PROVIDER,
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
        # The API takes up to 5 hint keywords, comma-joined, and **rejects internal
        # whitespace**: `hintKeywords=AI 회계` is HTTP 400 `code 11001, hintKeywords 파라미터가
        # 유효하지 않습니다`, while `AI회계` returns 46 rows. Measured against the live API
        # 2026-08-09, which settled a question the reference does not answer.
        #
        # Stripping is therefore not a normalisation nicety, it is the difference between a
        # working call and a 400 — and it costs nothing, because Naver's keyword tool stores
        # keywords space-free anyway, so `relKeyword` comes back without spaces regardless.
        #
        # Search-Ad-specific: API HUB's blog search accepts the same phrase WITH its space
        # (verified, HTTP 200), so this does not belong in shared plumbing.
        hints = ",".join(
            "".join(part.split())
            for part in seed.split(",")[:MAX_HINT_KEYWORDS]
            if part.strip()
        )
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
        except urllib.error.HTTPError as exc:
            # BEFORE the URLError arm — HTTPError subclasses it, and catching the parent first
            # is exactly what turned a 400 into "failed or timed out".
            raise _rejected(exc, "keyword request") from None
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


class _ApiHubTool:
    """Shared plumbing for the two NAVER API HUB endpoints.

    **Not** the Developers-center (`openapi.naver.com`) API this originally targeted. Naver is
    consolidating search / search-trend / shopping-insight onto API HUB, a NAVER Cloud Platform
    gateway, and the move changes four things at once: the host, the path shape, the auth header
    names, and the credential itself — an existing Developers-center Client ID does not work
    here. Written against API HUB directly rather than migrated later, because no credential for
    the old surface exists yet: there is nothing to preserve, so a compatibility layer would be
    pure speculation.

    What did NOT change is the response shape — `total`/`items` for search, `results[].data[]`
    for the trend — so the parsers below are the same ones that were written for the old
    surface. That is the reason this migration is a header-and-URL change and not a rewrite.
    """

    provider_id = APIHUB_PROVIDER
    network_egress = True
    BASE = "https://naverapihub.apigw.ntruss.com"

    def __init__(
        self,
        *,
        key_id_env: str = APIHUB_KEY_ID_ENV,
        key_env: str = APIHUB_KEY_ENV,
        authorization: Authorization | None = None,
    ):
        self._key_id_env = key_id_env  # NAMES, never values
        self._key_env = key_env
        self._authorization = authorization

    def _headers(self) -> dict[str, str]:
        key_id = os.environ.get(self._key_id_env)
        key = os.environ.get(self._key_env)
        missing = [
            name
            for name, value in ((self._key_id_env, key_id), (self._key_env, key))
            if not value
        ]
        if missing:
            raise ToolError("NO_API_KEY", f"environment variables not set: {', '.join(missing)}")
        return {
            "X-NCP-APIGW-API-KEY-ID": key_id,
            "X-NCP-APIGW-API-KEY": key,
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
        except urllib.error.HTTPError as exc:
            raise _rejected(exc, "api hub request") from None
        except (TimeoutError, urllib.error.URLError):
            raise ToolError("TOOL_TRANSPORT", "naver api hub request failed or timed out") from None
        return raw, int((time.monotonic() - started) * 1000)


class SearchTrendTool(_ApiHubTool):
    """Relative search-interest trend via API HUB's Search Trend endpoint (read-only).

    Named for what API HUB calls it. On the Developers-center surface this was "Datalab", and
    keeping that name would have pointed a reader at a console that no longer serves it.

    It returns **ratios normalised so the peak period in the requested window is 100**, never
    absolute counts. So a trend is comparable only within one call — two separate calls produce
    two independent scales, and a ratio must never be carried between packages as if it were a
    volume. The lane uses this for "rising or fading" and takes absolute demand from
    :class:`SearchAdKeywordTool` alone.
    """

    tool_id = TREND_TOOL_ID
    tool_version = f"{TOOL_VERSION}-apihub"
    _PATH = "/search-trend/v1/search"
    # Documented API HUB limits. Encoded because exceeding them is a 4xx that reads like an
    # auth failure, and because `EARLIEST_PERIOD` is the kind of bound a caller discovers by
    # getting an empty series back rather than an error.
    MAX_KEYWORD_GROUPS = 5
    MAX_KEYWORDS_PER_GROUP = 20
    EARLIEST_PERIOD = "2016-01-01"

    def trend(self, keyword: str, *, start_date: str, end_date: str, timeout_seconds: int) -> TrendResult:
        if start_date < self.EARLIEST_PERIOD:
            # Fail closed rather than silently returning a window that starts later than asked:
            # a caller charting "since 2014" would read the truncation as flat early demand.
            raise ToolError(
                "WINDOW_TOO_EARLY",
                f"search trend data begins {self.EARLIEST_PERIOD}; asked for {start_date}",
            )
        body = json.dumps({
            "startDate": start_date,
            "endDate": end_date,
            "timeUnit": "month",
            "keywordGroups": [{"groupName": keyword, "keywords": [keyword]}],
        }).encode("utf-8")
        raw, latency_ms = self._fetch(
            f"{self.BASE}{self._PATH}", timeout_seconds=timeout_seconds, data=body
        )
        try:
            data: dict[str, Any] = json.loads(raw)
            series = data["results"][0]["data"]
        except (KeyError, IndexError, ValueError, TypeError):
            raise ToolError("MALFORMED_RESULT", "search trend returned an unparseable response") from None

        points = [
            TrendPoint(period=str(p["period"]), ratio=float(p["ratio"]))
            for p in series
            if isinstance(p, dict) and "period" in p and "ratio" in p
        ]
        return TrendResult(keyword=keyword, points=points, tool_version=self.tool_version, latency_ms=latency_ms)


class BlogCompetitionTool(_ApiHubTool):
    """How crowded a keyword already is, via API HUB's blog search (read-only).

    ``total`` is Naver's own count of matching blog documents — the competition proxy the
    lane ranks on. The titles are returned alongside it because "how many" and "what do they
    look like" are the same question when deciding whether a post can differentiate.

    Path shape differs from the old surface in a way worth noting: `/search/v1/blog` with no
    `.json` suffix, format chosen by a `format` parameter that defaults to json. A ported URL
    keeping `blog.json` 404s.
    """

    tool_id = COMPETITION_TOOL_ID
    tool_version = f"{TOOL_VERSION}-apihub"
    _PATH = "/search/v1/blog"
    _MAX_DISPLAY = 100  # documented per-request cap

    def competition(self, keyword: str, *, display: int, timeout_seconds: int) -> CompetitionResult:
        count = max(1, min(int(display), self._MAX_DISPLAY))
        params = urllib.parse.urlencode({"query": keyword, "display": count, "sort": "sim"})
        raw, latency_ms = self._fetch(f"{self.BASE}{self._PATH}?{params}", timeout_seconds=timeout_seconds)
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

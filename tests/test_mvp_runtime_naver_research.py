"""Naver research adapter tests — the blog content lane's read-only tools.

Three things are worth pinning here and the rest follows from them:

1. **The gate is the environment**, not a grant record. That is the deliberate exception
   (Thomas, 2026-08-09) and the one behaviour a reader will not expect from every other
   network capability in this runtime, so it is asserted from both directions: no env var
   means the Mock, env var alone means the real adapter — with no activation record
   anywhere — and withdrawing the var mid-life stops egress.
2. **Secrets never leave by name.** The Search Ad adapter reads three env vars and signs
   with one of them; the tests assert the key is absent from every error path and that
   what goes on the wire is the signature.
3. **The `"< 10"` sentinel.** Naver puts a string in an integer field for low-volume
   keywords. Every consumer doing arithmetic depends on one agreement about it.

No test opens a socket: urlopen is replaced in the module namespace.
"""

from __future__ import annotations

import json
import urllib.error

import pytest

from runtime.mvp_runtime import naver_research, safety_gate
from runtime.mvp_runtime.errors import SafetyGateBlocked, ToolBlocked, ToolError
from runtime.mvp_runtime.naver_research import (
    NAVER_RESEARCH_ENV,
    NAVER_RESEARCH_ON,
    APIHUB_PROVIDER,
    SEARCHAD_PROVIDER,
    BlogCompetitionTool,
    SearchTrendTool,
    MockKeywordTool,
    SearchAdKeywordTool,
    _coerce_count,
    degraded_keyword_record,
    run_keyword_research,
    select_competition_tool,
    select_keyword_tool,
)
from runtime.mvp_runtime.safety_gate import NETWORK_ACCESS

NOW = "2026-08-09T09:00:00Z"

SECRET = "s3cr3t-signing-key"
_CREDS = {
    "NAVER_SEARCHAD_CUSTOMER_ID": "1234567",
    "NAVER_SEARCHAD_API_KEY": "0100000000aaaa",
    "NAVER_SEARCHAD_SECRET_KEY": SECRET,
}
_APIHUB_CREDS = {"NAVER_APIHUB_KEY_ID": "ncp-key-id-abc", "NAVER_APIHUB_KEY": "ncp-key-secret-xyz"}


@pytest.fixture
def gate_open(monkeypatch):
    """Hold the env-only gate open for a test that drives an adapter directly.

    Needed because the egress re-check re-reads the variable on **every** call, not just at
    selection — holding an Authorization is not enough. That is the property
    `test_withdrawing_the_env_var_stops_egress` exists to pin, and the reason these tests
    cannot just hand a tool its authorization and proceed.
    """
    monkeypatch.setenv(NAVER_RESEARCH_ENV, NAVER_RESEARCH_ON)


def _searchad_auth():
    return safety_gate.env_only_authorization(
        flags=(NETWORK_ACCESS,),
        provider_id=SEARCHAD_PROVIDER,
        env_var=NAVER_RESEARCH_ENV,
        opt_in_value=NAVER_RESEARCH_ON,
    )


def _apihub_auth():
    return safety_gate.env_only_authorization(
        flags=(NETWORK_ACCESS,),
        provider_id=APIHUB_PROVIDER,
        env_var=NAVER_RESEARCH_ENV,
        opt_in_value=NAVER_RESEARCH_ON,
    )


class _Response:
    def __init__(self, payload: str):
        self._payload = payload

    def read(self):
        return self._payload.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _capture_urlopen(monkeypatch, payload: str):
    """Replace urlopen and return the list the sent Requests land in."""
    sent = []

    def _fake(request, timeout=None):
        sent.append(request)
        return _Response(payload)

    monkeypatch.setattr(naver_research.urllib.request, "urlopen", _fake)
    return sent


# --- the "< 10" sentinel ---------------------------------------------------------

@pytest.mark.parametrize(
    "raw,expected,low",
    [
        (1234, 1234, False),
        ("< 10", naver_research._LOW_VOLUME_VALUE, True),
        ("<10", naver_research._LOW_VOLUME_VALUE, True),
        ("1,234", 1234, False),
        ("garbage", 0, False),
        (-5, 0, False),
        (True, 0, False),  # bool is an int subclass and is never a count
    ],
)
def test_a_count_field_that_is_sometimes_a_string(raw, expected, low):
    assert _coerce_count(raw) == (expected, low)


def test_the_low_volume_sentinel_is_not_zero():
    """Zero would read as 'nobody searches this' and rank the keyword out; the sentinel
    means 'fewer than ten', which is a different claim."""
    value, low = _coerce_count("< 10")
    assert value > 0 and low is True


# --- the mock ---------------------------------------------------------------------

def test_the_mock_needs_no_network_and_is_deterministic():
    tool = MockKeywordTool()
    assert tool.network_egress is False
    first = tool.keywords("AI 회계", max_results=5, timeout_seconds=1)
    second = tool.keywords("AI 회계", max_results=5, timeout_seconds=1)
    assert [m.keyword for m in first.metrics] == [m.keyword for m in second.metrics]


def test_the_mock_includes_a_low_volume_row():
    """A consumer that only handles tidy rows should fail here, not in production."""
    metrics = MockKeywordTool().keywords("AI 회계", max_results=5, timeout_seconds=1).metrics
    assert any(m.low_volume for m in metrics)


# --- run_keyword_research ----------------------------------------------------------

def test_metrics_come_back_ranked_by_total_demand():
    metrics, _ = run_keyword_research("AI 회계", tool=MockKeywordTool(), now=NOW, max_results=6)
    totals = [m["monthly_total"] for m in metrics]
    assert totals == sorted(totals, reverse=True)
    assert all(m["monthly_total"] == m["monthly_pc"] + m["monthly_mobile"] for m in metrics)


def test_the_record_carries_the_rows_it_hashes():
    _, record = run_keyword_research("AI 회계", tool=MockKeywordTool(), now=NOW, max_results=3)
    assert record["read_only"] is True
    assert record["external_action"] is False
    assert record["network_egress"] is False
    assert record["result_count"] == len(record["metrics"]) == 3
    assert record["created_at"] == NOW
    assert record["output_sha256"].startswith("sha256:")


@pytest.mark.parametrize("seed,reason", [("", "EMPTY_SEED"), ("   ", "EMPTY_SEED"), ("x" * 201, "SEED_TOO_LONG")])
def test_an_unusable_seed_fails_closed(seed, reason):
    with pytest.raises(ToolBlocked) as excinfo:
        run_keyword_research(seed, tool=MockKeywordTool(), now=NOW)
    assert excinfo.value.reason_code == reason


def test_a_backend_error_becomes_a_blocked_tool():
    class _Boom:
        tool_id, tool_version = naver_research.KEYWORD_TOOL_ID, "0.1.0"

        def keywords(self, seed, *, max_results, timeout_seconds):
            raise ToolError("BOOM", "backend unavailable")

    with pytest.raises(ToolBlocked) as excinfo:
        run_keyword_research("AI 회계", tool=_Boom(), now=NOW)
    assert excinfo.value.reason_code == "TOOL_ERROR"


def test_a_degraded_record_carries_no_numbers_at_all():
    """A package built on this must not look researched — so the row list is empty rather
    than partially filled."""
    record = degraded_keyword_record(MockKeywordTool(), "AI 회계", "TOOL_TRANSPORT", now=NOW)
    assert record["degraded"] is True
    assert record["degraded_reason_code"] == "TOOL_TRANSPORT"
    assert record["metrics"] == [] and record["result_count"] == 0
    assert record["read_only"] is True and record["external_action"] is False


# --- the gate: environment, not a grant --------------------------------------------

def test_without_the_env_var_selection_returns_the_mock(monkeypatch):
    monkeypatch.delenv(NAVER_RESEARCH_ENV, raising=False)
    assert isinstance(select_keyword_tool(), MockKeywordTool)
    assert isinstance(select_competition_tool(), naver_research.MockCompetitionTool)


def test_the_env_var_alone_opens_the_gate(monkeypatch):
    """The deliberate exception. Every other network capability needs a grant record on
    disk; this one is opened by the environment, and no activation record exists anywhere
    in this test."""
    monkeypatch.setenv(NAVER_RESEARCH_ENV, NAVER_RESEARCH_ON)
    tool = select_keyword_tool()
    assert isinstance(tool, SearchAdKeywordTool)
    assert tool.provider_id == SEARCHAD_PROVIDER


def test_the_gate_is_case_insensitive(monkeypatch):
    monkeypatch.setenv(NAVER_RESEARCH_ENV, "ENABLED")
    assert isinstance(select_keyword_tool(), SearchAdKeywordTool)


def test_a_different_value_does_not_open_the_gate(monkeypatch):
    monkeypatch.setenv(NAVER_RESEARCH_ENV, "yes")
    assert isinstance(select_keyword_tool(), MockKeywordTool)


def test_a_directly_constructed_adapter_cannot_open_a_socket(monkeypatch):
    """Bypassing selection must not bypass the gate."""
    monkeypatch.setenv(NAVER_RESEARCH_ENV, NAVER_RESEARCH_ON)
    with pytest.raises(SafetyGateBlocked) as excinfo:
        SearchAdKeywordTool().keywords("AI 회계", max_results=5, timeout_seconds=1)
    assert excinfo.value.reason_code == "NOT_AUTHORIZED"


def test_withdrawing_the_env_var_stops_egress(monkeypatch):
    """The env-only gate's revocation story: unset the var and the next call fails closed,
    even though the tool is still holding its authorization."""
    monkeypatch.setenv(NAVER_RESEARCH_ENV, NAVER_RESEARCH_ON)
    tool = select_keyword_tool()
    monkeypatch.delenv(NAVER_RESEARCH_ENV)
    with pytest.raises(SafetyGateBlocked) as excinfo:
        tool.keywords("AI 회계", max_results=5, timeout_seconds=1)
    assert excinfo.value.reason_code == "ENV_OPT_IN_WITHDRAWN"


# --- credentials and signing --------------------------------------------------------

def test_missing_credentials_name_the_variables_and_never_a_value(monkeypatch, gate_open):
    for name in _CREDS:
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ToolError) as excinfo:
        SearchAdKeywordTool(authorization=_searchad_auth()).keywords(
            "AI 회계", max_results=5, timeout_seconds=1
        )
    assert excinfo.value.reason_code == "NO_API_KEY"
    message = str(excinfo.value)
    assert "NAVER_SEARCHAD_SECRET_KEY" in message
    assert SECRET not in message


def test_the_signature_covers_the_path_without_the_query_string(monkeypatch, gate_open):
    """The usual way this integration fails: signing the full URL yields a 401 that reads
    like a bad key. The signed message is `timestamp.METHOD.path` only."""
    import base64
    import hashlib
    import hmac

    for name, value in _CREDS.items():
        monkeypatch.setenv(name, value)
    sent = _capture_urlopen(monkeypatch, json.dumps({"keywordList": []}))

    SearchAdKeywordTool(authorization=_searchad_auth()).keywords(
        "AI 회계", max_results=5, timeout_seconds=1
    )

    request = sent[0]
    assert "hintKeywords" in request.full_url  # the query string IS sent
    timestamp = request.get_header("X-timestamp")
    expected = base64.b64encode(
        hmac.new(SECRET.encode(), f"{timestamp}.GET./keywordstool".encode(), hashlib.sha256).digest()
    ).decode()
    assert request.get_header("X-signature") == expected


def test_the_secret_never_travels_as_a_header(monkeypatch, gate_open):
    for name, value in _CREDS.items():
        monkeypatch.setenv(name, value)
    sent = _capture_urlopen(monkeypatch, json.dumps({"keywordList": []}))
    SearchAdKeywordTool(authorization=_searchad_auth()).keywords("AI 회계", max_results=5, timeout_seconds=1)
    assert SECRET not in json.dumps(dict(sent[0].headers))
    assert SECRET not in sent[0].full_url


def test_a_transport_failure_says_nothing_about_the_request(monkeypatch, gate_open):
    for name, value in _CREDS.items():
        monkeypatch.setenv(name, value)

    def _boom(request, timeout=None):
        raise urllib.error.URLError("connection refused to api.searchad.naver.com")

    monkeypatch.setattr(naver_research.urllib.request, "urlopen", _boom)
    with pytest.raises(ToolError) as excinfo:
        SearchAdKeywordTool(authorization=_searchad_auth()).keywords(
            "AI 회계", max_results=5, timeout_seconds=1
        )
    assert excinfo.value.reason_code == "TOOL_TRANSPORT"
    assert "searchad.naver.com" not in str(excinfo.value)


# --- parsing ------------------------------------------------------------------------

def test_a_real_response_maps_onto_metrics(monkeypatch, gate_open):
    for name, value in _CREDS.items():
        monkeypatch.setenv(name, value)
    payload = json.dumps({"keywordList": [
        {"relKeyword": "AI 회계", "monthlyPcQcCnt": 900, "monthlyMobileQcCnt": 4200, "compIdx": "중간"},
        {"relKeyword": "소상공인 AI", "monthlyPcQcCnt": "< 10", "monthlyMobileQcCnt": 30, "compIdx": "낮음"},
        {"relKeyword": "이상한 경쟁도", "monthlyPcQcCnt": 5, "monthlyMobileQcCnt": 5, "compIdx": "VERY HIGH"},
        {"monthlyPcQcCnt": 1},  # no relKeyword — dropped, never a blank row
    ]})
    _capture_urlopen(monkeypatch, payload)

    result = SearchAdKeywordTool(authorization=_searchad_auth()).keywords(
        "AI 회계", max_results=10, timeout_seconds=1
    )
    assert [m.keyword for m in result.metrics] == ["AI 회계", "소상공인 AI", "이상한 경쟁도"]
    assert result.metrics[1].low_volume is True
    # An undocumented competition token becomes "unknown" rather than reaching the package.
    assert result.metrics[2].competition == "unknown"


def test_an_unparseable_response_fails_closed(monkeypatch, gate_open):
    for name, value in _CREDS.items():
        monkeypatch.setenv(name, value)
    _capture_urlopen(monkeypatch, "not json at all")
    with pytest.raises(ToolError) as excinfo:
        SearchAdKeywordTool(authorization=_searchad_auth()).keywords("AI 회계", max_results=5, timeout_seconds=1)
    assert excinfo.value.reason_code == "MALFORMED_RESULT"


def test_blog_titles_lose_their_markup(monkeypatch, gate_open):
    """The Search API wraps matched terms in <b>; a title carrying them is markup, not text."""
    for name, value in _APIHUB_CREDS.items():
        monkeypatch.setenv(name, value)
    payload = json.dumps({"total": 48213, "items": [
        {"title": "<b>AI</b> 회계 자동화 후기"},
        {"title": "세무 <b>AI</b> 도구 비교"},
    ]})
    _capture_urlopen(monkeypatch, payload)

    result = BlogCompetitionTool(authorization=_apihub_auth()).competition(
        "AI 회계", display=10, timeout_seconds=1
    )
    assert result.total_posts == 48213
    assert result.recent_titles == ["AI 회계 자동화 후기", "세무 AI 도구 비교"]


def test_a_trend_series_maps_onto_points(monkeypatch, gate_open):
    for name, value in _APIHUB_CREDS.items():
        monkeypatch.setenv(name, value)
    payload = json.dumps({"results": [{"title": "AI 회계", "data": [
        {"period": "2026-06-01", "ratio": 61.2},
        {"period": "2026-07-01", "ratio": 100.0},
    ]}]})
    sent = _capture_urlopen(monkeypatch, payload)

    result = SearchTrendTool(authorization=_apihub_auth()).trend(
        "AI 회계", start_date="2026-06-01", end_date="2026-07-31", timeout_seconds=1
    )
    assert [p.ratio for p in result.points] == [61.2, 100.0]
    assert sent[0].method == "POST"  # datalab is a POST with a JSON body


def test_the_apihub_adapters_are_gated_too(monkeypatch):
    monkeypatch.delenv(NAVER_RESEARCH_ENV, raising=False)
    with pytest.raises(SafetyGateBlocked):
        BlogCompetitionTool().competition("AI 회계", display=10, timeout_seconds=1)


# --- the API HUB migration ----------------------------------------------------------
#
# Four things changed at once when Naver moved these off the Developers-center surface: the
# host, the path shape, the header names, and the credential. Each is pinned, because getting
# any of them wrong produces an auth-shaped failure that sends you looking at the key.

@pytest.mark.parametrize("tool_cls, verb, kwargs, expected_path", [
    (BlogCompetitionTool, "competition", {"display": 10}, "/search/v1/blog"),
    (SearchTrendTool, "trend", {"start_date": "2026-06-01", "end_date": "2026-07-31"}, "/search-trend/v1/search"),
])
def test_calls_go_to_the_api_hub_gateway(monkeypatch, gate_open, tool_cls, verb, kwargs, expected_path):
    for name, value in _APIHUB_CREDS.items():
        monkeypatch.setenv(name, value)
    payload = json.dumps({"total": 1, "items": [], "results": [{"data": []}]})
    sent = _capture_urlopen(monkeypatch, payload)

    getattr(tool_cls(authorization=_apihub_auth()), verb)("AI 회계", timeout_seconds=1, **kwargs)

    url = sent[0].full_url
    assert url.startswith("https://naverapihub.apigw.ntruss.com")
    assert expected_path in url
    # The old surface suffixed the vertical with `.json`; a ported URL keeping it 404s.
    assert ".json" not in url
    assert "openapi.naver.com" not in url


def test_the_api_hub_headers_replace_the_developers_center_ones(monkeypatch, gate_open):
    for name, value in _APIHUB_CREDS.items():
        monkeypatch.setenv(name, value)
    sent = _capture_urlopen(monkeypatch, json.dumps({"total": 0, "items": []}))

    BlogCompetitionTool(authorization=_apihub_auth()).competition("AI 회계", display=10, timeout_seconds=1)

    headers = {k.lower(): v for k, v in sent[0].headers.items()}
    assert headers["X-Ncp-Apigw-Api-Key-Id".lower()] == _APIHUB_CREDS["NAVER_APIHUB_KEY_ID"]
    assert headers["X-Ncp-Apigw-Api-Key".lower()] == _APIHUB_CREDS["NAVER_APIHUB_KEY"]
    # The legacy headers must be gone, not merely supplemented — API HUB ignores them, so
    # sending both would look like it worked right up until the old ones were removed.
    assert "x-naver-client-id" not in headers
    assert "x-naver-client-secret" not in headers


def test_a_developers_center_credential_is_not_accepted(monkeypatch, gate_open):
    """The migration guide's sharpest line: existing Developers-center credentials cannot be
    used against API HUB. Setting only those must fail closed on the NAME of what is missing,
    rather than sending a request that comes back 401."""
    for name in _APIHUB_CREDS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NAVER_CLIENT_ID", "legacy-id")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "legacy-secret")

    with pytest.raises(ToolError) as excinfo:
        BlogCompetitionTool(authorization=_apihub_auth()).competition("AI 회계", display=10, timeout_seconds=1)
    assert excinfo.value.reason_code == "NO_API_KEY"
    assert "NAVER_APIHUB_KEY_ID" in str(excinfo.value)


def test_a_window_before_the_trend_data_begins_fails_closed(monkeypatch, gate_open):
    """Truncation would read as flat early demand — a wrong answer, not a missing one."""
    for name, value in _APIHUB_CREDS.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(ToolError) as excinfo:
        SearchTrendTool(authorization=_apihub_auth()).trend(
            "AI 회계", start_date="2014-01-01", end_date="2026-07-31", timeout_seconds=1
        )
    assert excinfo.value.reason_code == "WINDOW_TOO_EARLY"
    assert SearchTrendTool.EARLIEST_PERIOD in str(excinfo.value)

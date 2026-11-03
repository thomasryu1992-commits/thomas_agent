"""The keyword pipeline's direct shadow (BLOG_DEPARTMENT v0.2 P4b, Thomas 2026-11-03).

Under test: with either opt-in unset the fire reads nothing; the gated fetcher re-reads its opt-in before every request;
a host outside both groups is refused and never requested; the Search Ad API is answered from the bundle only, an
unasked hint list counted; a failure is retried once like the vault tool, and a 429/403/503 stops every later request;
the clock follows the recording; the record compares the top candidates per lane, carries counts and never a URL; the
fire runs one bundle a day, that day's, in the afternoon window."""

from __future__ import annotations

import gzip
import importlib
import io
import json
import tarfile
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from runtime.mvp_runtime import safety_gate
from runtime.mvp_runtime.blog import kw_direct as kd
from runtime.mvp_runtime.blog import kw_pipeline as kp
from runtime.mvp_runtime.blog import kw_replay as kr
from runtime.mvp_runtime.control import ControlStore
from runtime.mvp_runtime.scheduler import (
    KIND_BLOG_KW_DIRECT, KINDS, MAINTENANCE_KINDS, ScheduleStore, build_schedule, run_due,
)
from runtime.mvp_runtime.store import LEDGER_REL, LedgerStore

AFTERNOON = "2026-11-03T05:00:00Z"          # 14:00 KST
MORNING = "2026-11-02T23:30:00Z"            # 08:30 KST on 11-03
SA = "https://api.searchad.naver.com/keywordstool?hintKeywords=a&showDetail=1"
AC = "https://ac.search.naver.com/nx/ac?q=a"
RSS = "https://rss.blog.naver.com/thomasai.xml"


@pytest.fixture(autouse=True)
def _fresh_modules():
    yield
    from runtime.mvp_runtime.blog import affiliate, kw_taxonomy
    for module in (kw_taxonomy, affiliate, kp):
        importlib.reload(module)


def _open(monkeypatch):
    monkeypatch.setenv(kd.WEB_ENV, "enabled")
    monkeypatch.setenv(kd.OWN_ENV, "enabled")


class Fake:
    """A fetcher that answers from a dict and remembers what it was asked."""

    live = True

    def __init__(self, bodies=None, fail=None):
        self.bodies, self.fail, self.asked = bodies or {}, list(fail or []), []

    def fetch(self, url, *, headers, timeout):
        self.asked.append((url, dict(headers)))
        if self.fail:
            raise self.fail.pop(0)
        return self.bodies[url]


def _transport(rows=(), web=None, own=None):
    sleeps = []
    t = kd.DirectTransport(rows, web=web or Fake(), own=own or Fake(), sleep=sleeps.append)
    return t, sleeps


# --- the gate -------------------------------------------------------------------------------------------------------

def test_with_either_opt_in_unset_the_fire_reads_nothing(tmp_path, monkeypatch):
    ledger = LedgerStore(tmp_path / "state" / LEDGER_REL)
    touched = []
    monkeypatch.setattr(kr, "replay_dir", lambda: touched.append(1) or tmp_path)
    assert kd.fire(ledger, now=AFTERNOON) == "blog_kw_direct_off(MVP_BLOG_WEB_READ,MVP_BLOG_OWN_READ)"
    monkeypatch.setenv(kd.WEB_ENV, "enabled")
    assert kd.fire(ledger, now=AFTERNOON) == "blog_kw_direct_off(MVP_BLOG_OWN_READ)"
    monkeypatch.setenv(kd.OWN_ENV, "yes")                   # only the exact opt-in value opens
    assert kd.fire(ledger, now=AFTERNOON) == "blog_kw_direct_off(MVP_BLOG_OWN_READ)"
    assert touched == []


def test_the_gated_fetcher_rechecks_its_opt_in_before_each_request(monkeypatch):
    assert isinstance(kd.select_web_fetcher(), kd.InertFetcher)
    with pytest.raises(kd.DirectRefused):
        kd.select_own_fetcher().fetch(RSS, headers={}, timeout=1)
    _open(monkeypatch)
    web, own = kd.select_web_fetcher(), kd.select_own_fetcher()
    assert (web.live, web.provider_id, own.provider_id) == (True, kd.WEB_PROVIDER, kd.OWN_PROVIDER)

    def no_socket(*_a, **_k):
        raise AssertionError("a request left after the opt-in was withdrawn")

    monkeypatch.setattr(web._opener, "open", no_socket)
    monkeypatch.delenv(kd.WEB_ENV)
    with pytest.raises(safety_gate.SafetyGateBlocked):
        web.fetch(AC, headers={}, timeout=1)


def test_the_gated_fetcher_sends_the_callers_headers(monkeypatch):
    sent = {}

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    def open_(request, timeout):
        sent.update(url=request.full_url, ua=request.get_header("User-agent"), timeout=timeout)
        return Response("본문".encode("utf-8"))

    _open(monkeypatch)
    web = kd.select_web_fetcher()
    monkeypatch.setattr(web._opener, "open", open_)
    assert web.fetch(AC, headers={"User-Agent": "UA"}, timeout=7) == "본문"
    assert sent == {"url": AC, "ua": "UA", "timeout": 7}


@pytest.mark.parametrize("target, follows", [
    ("https://search.naver.com/search.naver?query=b", True),     # within the group
    ("https://m.blog.naver.com/api/blogs/x", True),
    ("https://example.com/", False),                            # out of every group
    ("https://rss.blog.naver.com/thomasai.xml", False),         # the other group
    ("http://search.naver.com/x", False),                       # not https
])
def test_a_redirect_is_followed_only_within_the_group(monkeypatch, target, follows):
    _open(monkeypatch)
    handler = next(h for h in kd.select_web_fetcher()._opener.handlers if isinstance(h, kd.GroupRedirects))
    request = urllib.request.Request("https://search.naver.com/search.naver?query=a")
    if follows:
        assert handler.redirect_request(request, None, 302, "Found", {}, target).full_url == target
    else:
        with pytest.raises(kd.DirectRefused):
            handler.redirect_request(request, None, 302, "Found", {}, target)


def test_a_refused_redirect_is_counted_and_not_retried():
    web = Fake(fail=[kd.DirectRefused("example.com")])
    t, sleeps = _transport(web=web)
    with pytest.raises(kd.DirectRefused):
        t(AC)
    assert (t.refused, len(web.asked), sleeps) == (1, 1, [0.3])
    assert t.hosts["ac.search.naver.com"] == {"fetched": 0, "failed": 0} and t.blocked_by is None


# --- the transport --------------------------------------------------------------------------------------------------

def test_a_host_outside_both_groups_is_refused_and_never_requested():
    web, own = Fake(), Fake()
    t, sleeps = _transport(web=web, own=own)
    for url in ("https://example.com/x", "http://search.naver.com/x", "https://evil.search.naver.com.example/x"):
        with pytest.raises(kd.DirectRefused):
            t(url)
    assert (t.refused, web.asked, own.asked, sleeps) == (3, [], [], [])


def test_each_group_goes_through_its_own_fetcher_with_the_callers_headers_and_pause():
    web, own = Fake({AC: "ac"}), Fake({RSS: "rss"})
    t, sleeps = _transport(web=web, own=own)
    assert t(AC, headers={"User-Agent": "mobile"}, pause=0.15) == "ac"
    assert t(RSS) == "rss"
    assert web.asked == [(AC, {"User-Agent": "mobile"})] and [u for u, _ in own.asked] == [RSS]
    assert sleeps == [0.15, 0.3]
    assert t.hosts["ac.search.naver.com"] == {"fetched": 1, "failed": 0}
    assert t.hosts["rss.blog.naver.com"] == {"fetched": 1, "failed": 0}


def test_search_ad_is_answered_from_the_recording_and_never_requested():
    web = Fake()
    rows = [{"url": SA, "body": '{"keywordList": []}', "at": "2026-11-03T07:11:00+09:00", "epoch": 5.0}]
    t, _ = _transport(rows, web=web)
    assert t(SA) == '{"keywordList": []}'
    with pytest.raises(kd.SearchAdUnrecorded):
        t(SA.replace("=a&", "=b&"))
    assert (t.searchad_recorded, t.searchad_served, t.searchad_unrecorded, web.asked) == (1, 1, 1, [])


def test_a_failure_is_retried_once_and_a_second_one_is_raised():
    web = Fake({AC: "ok"}, fail=[OSError("reset")])
    t, sleeps = _transport(web=web)
    assert t(AC) == "ok" and sleeps == [0.3, kd.RETRY_PAUSE]
    web.fail = [OSError("reset"), urllib.error.HTTPError(AC, 500, "x", {}, None)]
    with pytest.raises(kd.DirectFetchError):
        t(AC)
    assert t.hosts["ac.search.naver.com"] == {"fetched": 1, "failed": 1} and t.blocked_by is None


@pytest.mark.parametrize("code", [403, 429, 503])
def test_a_block_stops_every_later_request(code):
    web, own = Fake({AC: "ac"}, fail=[urllib.error.HTTPError(AC, code, "x", {}, None)]), Fake({RSS: "rss"})
    t, _ = _transport(web=web, own=own)
    with pytest.raises(kd.DirectBlocked):
        t(AC)
    for url in (AC, RSS):
        with pytest.raises(kd.DirectBlocked):
            t(url)
    assert t.blocked_by == f"ac.search.naver.com:{code}" and len(web.asked) == 1 and own.asked == []


def test_a_withdrawn_opt_in_mid_run_stops_like_a_block():
    web = Fake(fail=[safety_gate.SafetyGateBlocked("ENV_OPT_IN_WITHDRAWN", "gone")])
    t, _ = _transport(web=web)
    with pytest.raises(kd.DirectBlocked):
        t(AC)
    assert t.blocked_by == "ac.search.naver.com:gate"


def test_the_clock_follows_the_recording():
    rows = [{"url": AC, "body": "x", "at": "2026-11-03T07:12:00+09:00", "epoch": 9.0}]
    t, _ = _transport(rows, web=Fake({AC: "live"}))
    assert t.clock is None
    t(AC)
    assert (t.clock.minute, t.epoch) == (12, 9.0)


# --- one bundle, and the fire ---------------------------------------------------------------------------------------

def _cand(kw, lane="AI 활용", score=10.0):
    return {"kw": kw, "lane": lane, "score": score, "decision": {"decision": "CREATE_NEW"}}


def _bundle(root: Path, name: str, *, history=None, rows=(), shas=None) -> Path:
    bundle = root / name
    (bundle / "cache").mkdir(parents=True)
    (bundle / "outputs").mkdir()
    started = f"{name[:4]}-{name[4:6]}-{name[6:8]}T{name[9:11]}:{name[11:13]}:{name[13:15]}+09:00"
    meta = {"schema": kr.BUNDLE_SCHEMA, "started": started, "started_epoch": 1.0, "args": [], "has_vol": True,
            "decision_mode": "enforce", "tools_sha256": shas or dict(kp.GENERATED_FROM_SHA256), "finished": True,
            "history": "h.json"}
    (bundle / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    with gzip.open(bundle / "http.jsonl.gz", "wt", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")
    with tarfile.open(bundle / "inputs.tar.gz", "w:gz"):
        pass
    (bundle / "outputs" / "h.json").write_text(json.dumps(history or {"candidates": [_cand("가")]}), encoding="utf-8")
    return bundle


def _pipeline(monkeypatch, urls, history):
    """Stand in for the generated pipeline: request ``urls`` through the transport, swallow failures as the vault tool
    does, and write ``history``."""
    def run_bundle(bundle, meta, transport, *, clock):
        for url in urls:
            try:
                transport(url)
            except Exception:  # noqa: BLE001
                pass
        return history

    monkeypatch.setattr(kr, "run_bundle", run_bundle)


def test_a_direct_run_compares_the_top_sets_and_records_counts_not_urls(tmp_path, monkeypatch):
    bundle = _bundle(tmp_path, "20261103-071006", rows=[{"url": SA, "body": "{}", "at": None, "epoch": None}])
    _pipeline(monkeypatch, [SA, AC, RSS, "https://example.com/"],
              {"candidates": [_cand("가", score=11.0)]})            # same top set, a score that moved
    result = kd.direct(bundle, web=Fake({AC: "ac"}), own=Fake({RSS: "rss"}), now=AFTERNOON, sleep=lambda _s: None)
    assert (result["state"], result["agree"], result["blocked_by"]) == ("compared", True, None)
    assert result["lag_seconds"] == 6 * 3600 + 49 * 60 + 54   # 07:10:06 → 14:00:00 KST
    assert result["searchad"] == {"recorded": 1, "served": 1, "unrecorded": 0} and result["refused"] == 1
    assert result["differ"] == [{"kw": "가", "field": "score"}]
    record = kd.build_record(result, now=AFTERNOON)
    assert "http" not in json.dumps(record)


def test_a_blocked_run_is_kept_for_reading_but_not_judged(tmp_path, monkeypatch):
    bundle = _bundle(tmp_path, "20261103-071006")
    _pipeline(monkeypatch, [AC, RSS], {"candidates": []})
    web = Fake(fail=[urllib.error.HTTPError(AC, 429, "x", {}, None)])
    result = kd.direct(bundle, web=web, own=Fake({RSS: "rss"}), now=AFTERNOON, sleep=lambda _s: None)
    assert (result["state"], result["agree"], result["blocked_by"]) == ("blocked", None, "ac.search.naver.com:429")
    assert result["comparison"]["top_sets_equal"] is False
    kd.build_record(result, now=AFTERNOON)


def test_a_stale_bundle_requests_nothing(tmp_path, monkeypatch):
    bundle = _bundle(tmp_path, "20261103-071006", shas=dict(kp.GENERATED_FROM_SHA256, **{"kw_pipeline.py": "0" * 64}))
    _pipeline(monkeypatch, [AC], {"candidates": []})
    web = Fake({AC: "ac"})
    result = kd.direct(bundle, web=web, own=Fake(), now=AFTERNOON)
    assert (result["state"], result["stale"], web.asked) == ("generator_stale", ["kw_pipeline.py"], [])
    kd.build_record(result, now=AFTERNOON)


def test_the_fire_runs_the_days_newest_bundle_once_in_the_afternoon(tmp_path, monkeypatch):
    _open(monkeypatch)
    monkeypatch.setattr(kd, "select_web_fetcher", lambda: Fake({AC: "ac"}))
    monkeypatch.setattr(kd, "select_own_fetcher", lambda: Fake({RSS: "rss"}))
    _pipeline(monkeypatch, [AC, RSS], {"candidates": [_cand("가")]})
    ledger = LedgerStore(tmp_path / "state" / LEDGER_REL)
    root = tmp_path / "bundles"
    assert kd.fire(ledger, now=AFTERNOON, root=root) == "blog_kw_direct_no_bundles"
    _bundle(root, "20261102-071006")                       # yesterday's: never run late
    assert kd.fire(ledger, now=AFTERNOON, root=root) == "blog_kw_direct_nothing_new"
    _bundle(root, "20261103-071006")
    _bundle(root, "20261103-091500")
    assert kd.fire(ledger, now=MORNING, root=root) == "blog_kw_direct_outside_window"
    assert kd.fire(ledger, now=AFTERNOON, root=root, sleep=lambda _s: None) == \
        "blog_kw_direct=20261103-091500:compared/agree"
    assert kd.fire(ledger, now="2026-11-03T06:00:00Z", root=root) == "blog_kw_direct_nothing_new"
    rows = [r["record"] for r in ledger.iter_records(kinds=[kd.RECORD_KIND]) if r["kind"] == kd.RECORD_KIND]
    assert [r["bundle"] for r in rows] == ["20261103-091500"]


def test_the_kind_is_maintenance_and_fires_through_the_scheduler(tmp_path):
    assert KIND_BLOG_KW_DIRECT in KINDS and KIND_BLOG_KW_DIRECT in MAINTENANCE_KINDS
    state = tmp_path / "state"
    store = ScheduleStore(state)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.add(build_schedule(kind=KIND_BLOG_KW_DIRECT, request="", interval_seconds=3600, created_by="op",
                             now="2026-11-03T04:00:00Z"))
    ledger = LedgerStore(state / LEDGER_REL)
    run_due(store, now=AFTERNOON, control_store=ControlStore(state), ledger=ledger, repo_root=state)
    terminal = [e for e in ledger.read_scheduler_events() if e["action"] in ("fired", "failed")][-1]
    assert terminal["action"] == "fired"
    assert terminal["status"] == "blog_kw_direct_off(MVP_BLOG_WEB_READ,MVP_BLOG_OWN_READ)"

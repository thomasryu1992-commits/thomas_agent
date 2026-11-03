"""P4b of BLOG_DEPARTMENT v0.2: the keyword pipeline, run by the system against the live web, as a shadow.

P4a proved the port on recorded responses (``kw_replay``). P4b changes one thing: the requests the root cron made to
Naver and Google are made again, from the container, behind the environment gate. Everything else is the bundle's:
its inputs, its caches at the start, its arguments, key presence and mode, and the clock, which moves to the time the
recorded run received each URL. A difference against the bundle's history is then a difference in what the web
answered (the switch condition allows exactly that cause: the two runs read it hours apart).

Decided by Thomas on 2026-11-03, each as recommended:

* **Search Ad stays recorded.** ``api.searchad.naver.com`` is answered from the bundle and never requested. Its key is
  account-wide (it reaches the campaign endpoints that change ad spend) and lives in ``pipeline-worker`` alone; a
  second copy is not opened for a shadow. A hint list the root run never asked for is a counted ``unrecorded``, and
  the pipeline scores those keywords without a volume, the way it does when the API fails.
* **The bundle's caches at the start.** The direct run requests what the root run requested, about a thousand pages a
  day, not the several thousand an empty cache would.
* **Once per new bundle, in the afternoon.** The root cron runs at 07:10 KST from the same address. A fire runs only
  between ``WINDOW_KST`` hours, only on that day's bundle, and stops requesting at the first 429/403/503 (``blocked``):
  a block that reached the production cron would cost more than a day's shadow.
* **Two opt-ins (§7 N1).** ``MVP_BLOG_WEB_READ=enabled`` opens the Naver and Google query hosts;
  ``MVP_BLOG_OWN_READ=enabled`` opens the reads of this blog. The pipeline reads its own RSS in every run, so P4b needs
  both, and with either unset the fire says ``off`` and reads nothing.

A host outside both lists is refused, never requested. Request headers are not recorded (the caller's user agent and
referer are sent as the vault tool sends them). The record carries counts per host, never a URL or a body.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .. import safety_gate, timeutil
from ..safety_gate import NETWORK_ACCESS, Authorization, SafetyGateBlocked
from . import kw_replay as kr

WEB_ENV = "MVP_BLOG_WEB_READ"
OWN_ENV = "MVP_BLOG_OWN_READ"
OPT_IN = "enabled"
WEB_PROVIDER = "blog_web_read"
OWN_PROVIDER = "blog_own_read"
_NETWORK_FLAGS = (NETWORK_ACCESS,)

# §7 N1's two groups, by exact host. ``m.blog.naver.com`` is other blogs' profile API (the competition read), so it is
# a query host; this blog's reads are its RSS, its like counts and its Tistory.
WEB_HOSTS = ("ac.search.naver.com", "suggestqueries.google.com", "search.naver.com", "m.blog.naver.com",
             "blog.naver.com")
OWN_HOSTS = ("rss.blog.naver.com", "blog.like.naver.com", "thomasai.tistory.com")
SEARCHAD_HOST = "api.searchad.naver.com"
BLOCK_CODES = frozenset({403, 429, 503})
RETRY_PAUSE = 1.5               # the vault tool's ``http``: two attempts, 1.5 s apart
WINDOW_KST = (13, 22)           # [start, end) hours: well after the 07:10 cron, before the day's next bundle
KST = timezone(timedelta(hours=9))

RECORD_KIND = "blog_kw_direct"
RECORD_SCHEMA = "schemas/blog_kw_direct.v0.1.schema.json"
RECORD_SCHEMA_VERSION = "blog_kw_direct.v0.1"


class DirectRefused(Exception):
    """A request the direct transport will not make: a host outside both groups, or a group whose opt-in is unset."""


class DirectBlocked(Exception):
    """A host answered 429/403/503, or the opt-in was withdrawn; nothing more is requested in this run."""


class DirectFetchError(Exception):
    """A request failed twice. Raised into the pipeline, which handles a failed fetch the way the vault tool does."""


class _Retry(Exception):
    """The first attempt failed in a way the vault tool retries."""


class SearchAdUnrecorded(Exception):
    """The direct run asked the Search Ad API for a hint list the recorded run never sent."""


# --- the gate ---------------------------------------------------------------------------------------------------------

class InertFetcher:
    """The default with the opt-in unset: refuses every request."""

    live = False

    def __init__(self, env_var: str):
        self.env_var = env_var

    def fetch(self, url: str, *, headers: Mapping[str, str], timeout: float) -> str:
        raise DirectRefused(f"{self.env_var} is not set to {OPT_IN!r}")


class GatedFetcher:
    """One host group's egress. Built only behind the opt-in, and re-checks it before every request."""

    live = True

    def __init__(self, authorization: Authorization, provider_id: str):
        self._authorization = authorization
        self.provider_id = provider_id

    def fetch(self, url: str, *, headers: Mapping[str, str], timeout: float) -> str:
        safety_gate.assert_authorization(self._authorization, required_flags=_NETWORK_FLAGS,
                                         provider_id=self.provider_id, now=timeutil.utc_now_iso())
        request = urllib.request.Request(url, headers=dict(headers))
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8", "ignore")


def select_web_fetcher() -> Any:
    """Naver and Google query hosts — inert unless ``MVP_BLOG_WEB_READ=enabled``."""
    return safety_gate.select_env_gated(
        env_var=WEB_ENV,
        opt_in_value=OPT_IN,
        flags=_NETWORK_FLAGS,
        provider_id=WEB_PROVIDER,
        default_factory=lambda: InertFetcher(WEB_ENV),
        gated_factory=lambda authorization: GatedFetcher(authorization, WEB_PROVIDER),
    )


def select_own_fetcher() -> Any:
    """This blog's reads — inert unless ``MVP_BLOG_OWN_READ=enabled``."""
    return safety_gate.select_env_gated(
        env_var=OWN_ENV,
        opt_in_value=OPT_IN,
        flags=_NETWORK_FLAGS,
        provider_id=OWN_PROVIDER,
        default_factory=lambda: InertFetcher(OWN_ENV),
        gated_factory=lambda authorization: GatedFetcher(authorization, OWN_PROVIDER),
    )


# --- the transport ----------------------------------------------------------------------------------------------------

class DirectTransport:
    """Answers the pipeline's requests: Search Ad from the recording, the listed hosts through their fetcher, anything
    else refused. Moves the clock to when the recorded run received each URL, as the replay does, so the run's own
    time arithmetic matches the recording and only the bodies can differ."""

    def __init__(self, rows: Iterable[Mapping[str, Any]], *, web: Any, own: Any,
                 sleep: Callable[[float], None] = time.sleep):
        self.times: dict[str, deque] = defaultdict(deque)
        self.searchad: dict[str, deque] = defaultdict(deque)
        self.searchad_recorded = 0
        for row in rows:
            url = str(row.get("url"))
            self.times[url].append((row.get("at"), row.get("epoch")))
            if urllib.parse.urlsplit(url).hostname == SEARCHAD_HOST:
                self.searchad[url].append(row.get("body"))
                self.searchad_recorded += 1
        self.web, self.own, self.sleep = web, own, sleep
        self.hosts = {host: {"fetched": 0, "failed": 0} for host in (*WEB_HOSTS, *OWN_HOSTS)}
        self.searchad_served = self.searchad_unrecorded = self.refused = 0
        self.blocked_by: str | None = None
        self.clock: datetime | None = None
        self.epoch: float | None = None

    def _move_clock(self, url: str) -> None:
        queue = self.times.get(url)
        if not queue:
            return
        at, epoch = queue.popleft() if len(queue) > 1 else queue[0]
        if at:
            self.clock = datetime.fromisoformat(str(at))
        if isinstance(epoch, (int, float)):
            self.epoch = float(epoch)

    def __call__(self, url: str, *, headers: Mapping[str, str] | None = None, timeout: float = 20,
                 pause: float = 0.3) -> str:
        self._move_clock(url)
        parts = urllib.parse.urlsplit(url)
        host = parts.hostname or ""
        if host == SEARCHAD_HOST:
            return self._recorded_searchad(url)
        fetcher = self.web if host in WEB_HOSTS else self.own if host in OWN_HOSTS else None
        if fetcher is None or parts.scheme != "https":
            self.refused += 1
            raise DirectRefused(host or "no host")
        if self.blocked_by is not None:
            raise DirectBlocked(self.blocked_by)
        self.sleep(pause)
        try:
            return self._once(fetcher, host, url, headers, timeout, last=False)
        except _Retry:
            self.sleep(RETRY_PAUSE)
        return self._once(fetcher, host, url, headers, timeout, last=True)

    def _once(self, fetcher: Any, host: str, url: str, headers: Mapping[str, str] | None, timeout: float, *,
              last: bool) -> str:
        """One attempt. A block or a withdrawn opt-in stops the run; any other failure is retried once, as the vault
        tool's ``http`` does, and raised the second time."""
        counts = self.hosts[host]
        try:
            body = fetcher.fetch(url, headers=headers or {}, timeout=timeout)
        except SafetyGateBlocked:
            # The opt-in went away under a running fire: stop, as a block does.
            counts["failed"] += 1
            self.blocked_by = f"{host}:gate"
            raise DirectBlocked(self.blocked_by) from None
        except urllib.error.HTTPError as exc:     # before URLError, its parent
            if exc.code in BLOCK_CODES:
                counts["failed"] += 1
                self.blocked_by = f"{host}:{exc.code}"
                raise DirectBlocked(self.blocked_by) from None
            failed = True
        except Exception:  # noqa: BLE001 — the vault tool retries any failure once; so does this
            failed = True
        else:
            failed = False
        if failed:
            if not last:
                raise _Retry
            counts["failed"] += 1
            raise DirectFetchError(host)
        counts["fetched"] += 1
        return body

    def _recorded_searchad(self, url: str) -> str:
        queue = self.searchad.get(url)
        if not queue:
            self.searchad_unrecorded += 1
            raise SearchAdUnrecorded(SEARCHAD_HOST)
        body = queue.popleft() if len(queue) > 1 else queue[0]
        if body is None:
            raise kr.RecordedFailure(SEARCHAD_HOST)
        self.searchad_served += 1
        return str(body)


# --- one bundle -------------------------------------------------------------------------------------------------------

def _iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def direct(bundle: Path, *, web: Any, own: Any, now: str, sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """Run one bundle against the web and compare with its recorded history. Never raises for a broken bundle."""
    from . import kw_pipeline as kp

    out: dict[str, Any] = {"bundle": bundle.name, "run_started": None, "lag_seconds": None, "state": "unreadable",
                           "agree": None, "blocked_by": None, "stale": [],
                           "searchad": {"recorded": 0, "served": 0, "unrecorded": 0},
                           "hosts": {host: {"fetched": 0, "failed": 0} for host in (*WEB_HOSTS, *OWN_HOSTS)},
                           "refused": 0, "comparison": None, "differ": []}
    meta = kr._meta(bundle)
    if meta is None or meta.get("finished") is not True:
        return out
    out["run_started"] = str(meta.get("started"))[:25]
    stale = kr._stale(meta, kp.GENERATED_FROM_SHA256)
    if stale:
        out.update(state="generator_stale", stale=stale)
        return out
    try:
        recorded = json.loads((bundle / "outputs" / str(meta["history"])).read_text(encoding="utf-8"))
        clock = datetime.fromisoformat(str(meta["started"]))
        out["lag_seconds"] = max(0, int((_iso(now) - clock).total_seconds()))
    except (OSError, KeyError, UnicodeDecodeError, ValueError, TypeError, EOFError):
        return out
    transport = None
    try:
        transport = DirectTransport(kr._http_rows(bundle), web=web, own=own, sleep=sleep)
        ran = kr.run_bundle(bundle, meta, transport, clock=clock)
    except Exception as exc:  # noqa: BLE001 — one broken run is a recorded state, never the fire's failure
        out["state"] = f"error:{type(exc).__name__}"
        ran = None
    if transport is not None:
        out.update(blocked_by=transport.blocked_by, refused=transport.refused, hosts=transport.hosts,
                   searchad={"recorded": transport.searchad_recorded, "served": transport.searchad_served,
                             "unrecorded": transport.searchad_unrecorded})
    if ran is None:
        return out
    result = kr.compare(recorded, ran)
    out.update(comparison=result["comparison"], differ=result["differ"])
    if transport.blocked_by is not None:
        out["state"] = "blocked"
    else:
        # The switch condition (v0.2 §4): the top candidates per lane are the same set.
        out.update(state="compared", agree=result["comparison"]["top_sets_equal"])
    return out


# --- the fire (scheduler kind ``blog_kw_direct``) ---------------------------------------------------------------------

def build_record(result: Mapping[str, Any], *, now: str) -> dict[str, Any]:
    from runtime.read_only_kernel import integrity

    from .. import schema_cache
    from ..paths import repo_root

    record = {
        "schema_version": RECORD_SCHEMA_VERSION,
        "direct_id": integrity.short_id("blogkwdirect", {"bundle": result["bundle"], "ran_at": now}),
        "ran_at": now,
        **{key: result[key] for key in ("bundle", "run_started", "lag_seconds", "state", "agree", "blocked_by",
                                        "stale", "searchad", "hosts", "refused", "comparison", "differ")},
    }
    schema_cache.validate_against_schema(record, repo_root() / RECORD_SCHEMA, RECORD_KIND)
    return record


def fire(ledger: Any, *, now: str, root: Path | None = None,
         sleep: Callable[[float], None] = time.sleep) -> str:
    """Run today's bundle once, in the afternoon window, when both opt-ins are set. Every other case is a named
    no-op: ``off`` (an opt-in unset; nothing is read), ``no_bundles``, ``outside_window``, ``nothing_new``."""
    web, own = select_web_fetcher(), select_own_fetcher()
    unset = [f.env_var for f in (web, own) if not f.live]
    if unset:
        return "blog_kw_direct_off(" + ",".join(unset) + ")"
    folder = root if root is not None else kr.replay_dir()
    if folder is None or not folder.is_dir():
        return "blog_kw_direct_no_bundles"
    local = _iso(now).astimezone(KST)
    if not WINDOW_KST[0] <= local.hour < WINDOW_KST[1]:
        return "blog_kw_direct_outside_window"
    done = {str(row["record"].get("bundle")) for row in ledger.iter_records(kinds=[RECORD_KIND])
            if row.get("kind") == RECORD_KIND}
    # One run a day: the day's newest bundle, and none once any of the day's bundles has a record.
    today = [b for b in kr.bundles(folder) if b.name.startswith(f"{local:%Y%m%d}-")]
    if not today or any(b.name in done for b in today):
        return "blog_kw_direct_nothing_new"
    bundle = today[-1]
    record = build_record(direct(bundle, web=web, own=own, now=now, sleep=sleep), now=now)
    ledger.append_records(record["direct_id"], {RECORD_KIND: record})
    verdict = "" if record["agree"] is None else ("/agree" if record["agree"] else "/differ")
    return f"blog_kw_direct={bundle.name}:{record['state']}{verdict}"

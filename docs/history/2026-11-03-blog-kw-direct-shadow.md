# 2026-11-03 — Blog department P4b: the keyword pipeline against the live web, as a shadow, built off

**What.** BLOG_DEPARTMENT v0.2 P4b, built ahead of its turn. The design orders P4b after P4a's 14 agreeing days;
Thomas chose on 2026-11-03 to build it now and switch it on after that condition. He took the recommended default in
four questions the same day: Search Ad recorded, the bundle's caches, once a day in the afternoon, and two opt-ins
named `MVP_BLOG_WEB_READ`/`MVP_BLOG_OWN_READ`.

- **`runtime/mvp_runtime/blog/kw_direct.py`** runs a root bundle again with the generated port. Its inputs, its caches
  at the start, its arguments, key presence and mode, and its clock are all the bundle's. Only the Naver and Google
  requests are made again, from the container. The result is compared with the bundle's history, as P4a does.
- **Scheduler kind `blog_kw_direct`** (maintenance) records `blog_kw_direct` (closed schema v0.1). With either opt-in
  unset it returns `blog_kw_direct_off(...)` and reads nothing. The schedule is meant to be added when Thomas opts in
  (hourly; the fire itself keeps it to one run a day).
- **The generated `http` now passes the caller's headers, timeout and pause** to the transport. The replay ignores
  them. The direct transport sends the user agent and referer the vault tool sends, and paces as it does.
- **`kw_replay.run_bundle`** is the one place a bundle is run. P4a passes the recording, P4b its gated fetchers.

**The four decisions, and why.**
- **Search Ad stays recorded.** `api.searchad.naver.com` is answered from the bundle and never requested from here.
  - Its key is account-wide: the same signature reaches the campaign endpoints that change ad spend. The key lives
    on `pipeline-worker` alone, and a second copy is not opened for a shadow.
  - A hint list the root run never sent is counted as `unrecorded`. The pipeline then scores those keywords without
    a volume, as it does when the API fails.
- **The bundle's caches at the start.** The direct run requests what the root run requested. On 11-02 that was 990
  pages (457 result pages, 267 profile reads, 265 autocompletes, the RSS). An empty cache would mean thousands of
  pages, and the comparison would also mix cache differences in.
- **Once a day, 13–22 KST, that day's bundle only.** The root cron reads the same hosts at 07:10 KST from the same
  address. The first 429/403/503, or an opt-in withdrawn mid-run, stops every later request, and the run is recorded
  as `blocked` and not judged. A block that reached the production cron would cost more than a day's shadow.
  - The cost is the maintenance lane's time: the root run's requests spanned 22 minutes on 11-02 (07:10–07:32), so
    one fire a day holds the lane about that long, and the maintenance kinds due meanwhile wait for it. A deploy's
    `fires` check stops while it runs.
- **Two opt-ins (§7 N1).** `MVP_BLOG_WEB_READ=enabled` opens `ac.search.naver.com`, `suggestqueries.google.com`,
  `search.naver.com`, `m.blog.naver.com` and `blog.naver.com`. `MVP_BLOG_OWN_READ=enabled` opens this blog's reads:
  `rss.blog.naver.com`, `blog.like.naver.com` and `thomasai.tistory.com`.
  - The pipeline reads its own RSS in every run, so P4b needs both.
  - A host in neither list is refused and never requested. So is plain http.
  - Each fetcher re-reads its opt-in before every request (`assert_authorization`).

**What the record says.**
- `agree` is the switch condition: the top candidates per lane are the same set. It is null unless `compared`.
- The full P4a comparison (`differ` and the field equalities) is kept for reading.
- `lag_seconds` is the time between the root run and this one, the cause the switch condition allows.
- It carries counts per host and per Search Ad outcome, and never a URL, a header or a body.

**Found on the way: P4a would have stopped agreeing on 11-04.**
- On 2026-11-03 Thomas changed the vault's `kw_pipeline.py` (628b37f7). Equipment candidates are now filtered by
  `aff_excluded`, which adds the hand-kept `AFF_EXCLUDE` set and `ops/affiliate-link/exclude.txt` (14 keywords).
- **The bundle did not carry that file.** A replay would have read none of it and filtered differently from root. The
  vault now packs `ops/affiliate-link` into every bundle (48352c60).
- **The port is regenerated from that commit**, which also moves `GENERATED_FROM_SHA256`. The generator's import-line
  edit is updated for the new `functools` import; it refused to generate until it was.

**Verified before the PR.**
- **The generator change** was checked on a port generated with the new generator from the bundle's own vault
  version (706609cd). It replays the 11-02 root bundle `compared`/agree, with 0 misses and 0 differing fields.
- **The direct path**, on the same bundle through the real pipeline, with fetchers that answer from the recording
  instead of the network:
  - `compared`, top sets equal, 0 differing fields;
  - Search Ad 236 recorded, 236 served, 0 unrecorded;
  - per host exactly the root run's requests: 112 / 153 / 457 / 267 / 1;
  - the user-agent, accept-language and referer headers reached the fetchers;
  - a record of about 1 KB with no URL.
- **The port regenerated from 48352c60** replays that bundle with one candidate missing (`카날스무선마이크`) and two
  misses. That is the vault's behaviour change (the `AFF_EXCLUDE` set now applies to candidates), which the stale
  check exists to keep out of the count. It is not a port error.
- **Tests** (`tests/test_mvp_runtime_blog_kw_direct.py`):
  - off with either opt-in unset, and nothing read;
  - the gated fetcher re-checks before each request and sends the caller's headers;
  - unlisted hosts and plain http are refused unrequested;
  - Search Ad comes from the recording only;
  - retry once, then raise; 429/403/503 and a withdrawn opt-in stop all later requests;
  - the clock follows the recording;
  - the record holds counts and no URL; a blocked or stale run is not judged;
  - one run a day, of that day's newest bundle, only in the window;
  - the scheduler path.
- **Rosters updated:** the env-gate roster test, `tests/conftest.py`'s `_GATE_ENV_VARS`, and the compose passthrough
  (scheduler-maint only, pinned both ways). The diagnostic code index is regenerated.
- **Not verified:** a request to the live web. Nothing here opened one; the first will be the first fire after Thomas
  sets both variables.

**Turning it on (Thomas, after P4a's condition).** Add to `/root/thomas_agent/.env`:

```
MVP_BLOG_WEB_READ=enabled
MVP_BLOG_OWN_READ=enabled
```

Then recreate the stack the deploy way, and add the schedule:

```
docker exec thomas-scheduler python -m runtime.mvp_runtime.scheduler_cli add --kind blog_kw_direct --interval-seconds 3600 --reason "BLOG_DEPARTMENT v0.2 P4b"
```

Revoking means unsetting the variables and restarting the container.

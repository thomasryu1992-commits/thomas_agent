# 2026-10-27 — Blog department P4a: the keyword pipeline, replayed offline from a recorded run

**What.** BLOG_DEPARTMENT v0.2 P4, split by Thomas on 2026-10-27 ("재생 그림자 먼저"). P4a is this PR; P4b, the
container reading Naver/Google itself through the §7 N1 opt-ins, comes after P4a's equality holds.
- **The vault's `tools/kw_pipeline.py` records a run when `KW_REPLAY_DIR` is set** (vault commit, same day). A full
  `run` leaves a bundle with these parts:
  - `meta.json`: start, arguments, whether a Search Ad key was present (never the key), the decision mode, the click
    share, and the sha256 of each tool file;
  - `inputs.tar.gz`: the text files under `content/`, `analytics/keywords/`, `analytics/weekly-browser/`, `제휴/` and
    `tools/` at the start;
  - `cache/*.json`: the caches at the start;
  - `http.jsonl.gz`: every response with the time it arrived. A failed fetch is logged as a failure. Request headers
    are never logged, and a URL that looks like it carries a credential is withheld;
  - `outputs/`: the history file and `queue.md` the run wrote.

  A queue-reuse day records nothing. The last seven bundles are kept (about 35 MB each, mostly result-page HTML).
- **`scripts/blog_vendor.py` generates three more modules into `blog/`:**
  - `kw_pipeline.py`;
  - `kw_taxonomy.py`: the taxonomy with its writers kept, because the pipeline appends to the update hand-over;
  - `affiliate.py`: only the affiliate note parser.
- **Scheduler kind `blog_kw_replay`** (maintenance, daily). For each new bundle it re-runs `run` on a temporary copy,
  with only the recorded responses, and records `blog_kw_replay`:
  - the state: `compared` / `generator_stale` / `replay_miss` / `unreadable` / `error:*`;
  - whether the two history files agree on the candidate set, each candidate's lane, score and decision, the top ten
    per lane, the Tistory candidates, the out-of-league list and the rank table;
  - up to 20 keywords that moved.
- **scheduler-maint** mounts the bundle folder read-only (`THOMAS_KW_REPLAY_DIR`, default
  `/var/lib/thomas-measure/kw-replay`).

**Why it is shaped this way.**
- **Replay before direct lookup.** A container that queries Naver and Google itself sees different autocomplete and
  result pages from the cron that ran an hour earlier. A difference in the top candidates would then be either a
  port error or a lookup difference, every day. With the cron's own responses the expected answer is exact equality,
  so any difference is the port's. No network capability opens in this step.
- **The cache snapshot and the response log, both.** A cache hit never reaches `http`, so the responses alone would
  leave the replay asking for URLs the run never fetched. The cache at the start plus every response the run received
  is the whole of what it saw.
- **The clock moves with the responses.** The first round-trip agreed on everything except ten scores. The cause was
  `blog_daily`, which turns a competitor's "visitors so far today" into a daily estimate by the fraction of the day
  elapsed when the profile arrived. A replay pinned to the start time divides by a different fraction. Each response
  now carries the time it arrived, and the replay's `now()`/`time.time()` move to it when it is served.
- **Writes redirected, not removed.** The pipeline writes the queue, the history, the rejected list, the update
  hand-over and its caches. Cutting those would change the control flow being compared, so every path is pointed at
  the replay's temporary copy instead. `configure` re-runs every module-level assignment that depends on a path; the
  generator finds them by name, so no list is kept by hand.
- **One sha check per bundle.** A bundle that ran a tool file other than the one the port was generated from is
  `generator_stale`, not a difference. Regenerating is a PR, as in P2.
- **The Search Ad branch.** `load_keys` answers "present" exactly when the recorded run had a key. The
  `/keywordstool` query string carries no time or signature, so its URL repeats. The `claude` account has no key, so
  that branch was first exercised on root's bundles, not before the PR.

**Verified before the PR.**
- **Two recorded full runs on a copy of the vault** (the `claude` account, no Search Ad key, network as the cron
  uses it), each replayed with the generated port:
  - Run 1 (2,158 responses): everything equal except ten scores, all from `auth_med`, which led to the clock fix
    above.
  - Run 2 (2,126 responses, recorded with arrival times): **exact agreement.** 87 candidates with the same lane,
    score and decision; the same top ten per lane; the same 576 Tistory candidates; the same out-of-league list and
    rank table. No response was missed.
- **Replay cost:** 37 seconds. Peak memory went from 421 MB to 108 MB once the bodies were held compressed.
  scheduler-maint has no memory ceiling, by decision Q15-b.
- **Mutations:**
  - with the clock left at the start, the replay reports 18 differing fields;
  - with `EXACT_CAP` changed from 9 to 8, it reports 6, and the out-of-league list differs.
- **The vault's recorder:** its own test (`tools/kw_pipeline_replay.test.py`) passes 9/9. It is inert without the
  variable, so the drafting runs and the `claude` account's commands are unchanged. The generated port was cut from
  the production file after that commit (`f33693e9`). `blog_vendor.py --check` is OK for all six generated modules.

**Deploy and handoff.**
1. Create `/var/lib/thomas-measure/kw-replay` (0755).
2. Add `export KW_REPLAY_DIR=/var/lib/thomas-measure/kw-replay` to root's `kw-daily.sh`.
3. Deploy.
4. Add the schedule:
   `docker exec thomas-scheduler python -m runtime.mvp_runtime.scheduler_cli add --kind blog_kw_replay --interval-seconds 86400 --reason "BLOG_DEPARTMENT v0.2 P4a"`.

The first bundle arrives with the first full run after the export, not on a queue-reuse day.

## 2026-10-28: the first root bundle, and a regeneration

Deployed 10-27 as `candidate-1336`. Root's first full run with the variable set (10-28 07:10 KST, `run --max-age 2`,
25 minutes) recorded bundle `20261028-071006`: 2,176 responses, `has_vol` true, `withheld` 0.

- **One response was missing.** The `--max-age` path calls `own_posts()` to decide whether to reuse the queue, and
  that reads the Naver RSS into the in-process `_LIVE` cache *before* `replay_start`. The replay drops `--max-age`,
  so it asks for the RSS after recording began and finds nothing. With no RSS the pipeline falls back to vault
  status, and the ranks differed. The copy-vault round trips ran without `--max-age`, which is why they never saw
  this. Vault `706609cd`: `replay_start` clears `_LIVE` when recording, so a full run fetches the RSS inside the
  recording. Without the variable nothing changes. Its test fails with the fix removed.
- **The Search Ad branch agrees.** The RSS was fetched again by hand at 11:28 KST, trimmed of the one item published
  after 07:10, and put into the transport. The replay then came out `compared/agree`: 118 candidates, 613 Tistory
  candidates, top sets, out-of-league and ranks all equal, with 0 differing fields.
- **The port was stale anyway.** Another session changed `kw_pipeline.py` in the vault at 10-27 22:02 KST (GSC: only
  the 28-day table feeds the slot estimate). The production run used that version, so the deployed port reads this
  bundle as `generator_stale`. This PR regenerates the port from vault `706609cd`, which carries both changes.

Every vault edit to `kw_pipeline.py`, `blog_taxonomy.py` or `affiliate_fetch.py` makes the port stale until a
regeneration is deployed. A bundle that fires as `generator_stale` gets a record and is not replayed again.

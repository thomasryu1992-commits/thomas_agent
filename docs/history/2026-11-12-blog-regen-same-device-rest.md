# 2026-11-12 — Blog: the keyword replay port follows the 11-12 affiliate stock rules, and root bundles stop ending in an error

**Why.** On 2026-11-12 the Mac and the server both changed the vault's affiliate tools, answering the Mac's
eligibility check of the new device families (`ops/affiliate-link/handoff-1111-server-reply.md`, "맥 결과" and
"서버 답").
- Mac `b262deab` changed `tools/affiliate_link.py`: `kind()` merges 보이스레코더 into 녹음기, and the daily search
  takes one representative per family (`family_reps`).
- Server `6a4fd2f9` changed `tools/kw_pipeline.py` and `kind()` again:
  - A 후보 부족 rests the whole family only when the representative failed. The representative is the keyword
    whose spaces-stripped form equals its `kind`. A narrower variant that fails rests only itself.
  - `AFF_NO_PRODUCTS` excludes the five families with 0–1 qualifying products.
  - Tripods map to the 짐벌셀카봉 family, because post 198 already decides tripod versus gimbal.
  - 촬영조명 and 포토박스 get different stories.

Both files are in the recorder's `tools_sha256` (since vault `f1aaadd4`), so every root bundle recorded on these
tools would read `generator_stale` against the port from #1446.

**A defect found while verifying (pre-existing).** Root's 2026-11-12 bundle, replayed on the deployed #1446 port,
ended in `error:AttributeError`. Root bundles are recorded with a Search Ad key.
- Eight vol-cache entries were 13.9996 days old when the run started, against a TTL of 14 days.
- `Cache.get` judged age by the wall clock, so the live run expired them about 35 s in and asked Search Ad again.
- The replay's clock is the arrival time of the last recorded response. It still held them as hits, so its Search
  Ad batches shifted, every batch missed, and `score()` met `c["vol"] = None`.
- A daily run at the same hour meets entries a run 14 days earlier stored, so this was a regular event, not a
  one-off. The copy-vault checks of #1336 and #1446 ran without a key and never took this branch.

The same `score()` line (`c.get("vol", {}).get(...)` on a `None`) also ended a live run whenever one Search Ad batch
failed.

**What.**
- **Vault `3e9527ac`:**
  - `run()` fixes `RUN_T0` (wall clock) before the recording starts, and the recording writes it as `started_epoch`.
  - `Cache.get` judges age against `RUN_T0` inside a run, so expiry depends only on the start and the replay makes
    the same call. A live run now keeps an entry that expires mid-run until the run ends, a few minutes at most.
  - `score()` reads `(c.get("vol") or {})`, like every other use of `vol` in the file.
  - Vault tests: `tools/kw_pipeline_replay.test.py`.
- **Here:** `kw_pipeline.py` and `affiliate_link.py` are regenerated from vault `3e9527ac`. The generator is
  unchanged: its `time.time()` → `_epoch()` substitution makes the port's `RUN_T0` the recorded `started_epoch`,
  because no response has arrived when `run()` sets it. `family_reps` is not ported (the port keeps only `kind`,
  `jload` and `excluded`, and the pipeline does not call it).
- **Two tests** in `tests/test_mvp_runtime_blog_kw_replay.py`:
  - `run()` fixes `RUN_T0` at the recorded start, and the cache still judges an entry at that start after the
    replay clock moves past its expiry.
  - A candidate without a volume scores 0. Both tests fail on the previous port.

**Verified.** See the PR comments. A copy-vault round trip on `3e9527ac` was replayed by the new port. Root's earlier
bundles read `generator_stale`. A Search Ad bundle on the new rule can only come from root. The first is the
2026-11-13 07:10 KST run, and the in-image check of the deploy script requires it to compare.

**Deploy window.** Deploy between the 2026-11-12 13:00Z replay and the 2026-11-13 13:00Z replay.
- Not before: the 2026-11-12 root bundle was recorded on `f1aaadd4` tools. It reads as #1446's, and an earlier
  deploy would turn it stale. It still ends in the error above: it was recorded under the old cache rule.
- Not after: the 2026-11-13 bundle is recorded on these tools. A later deploy leaves that replay stale, and a stale
  replay is never retried.

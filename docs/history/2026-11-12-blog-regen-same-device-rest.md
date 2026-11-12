# 2026-11-12 — Blog: the keyword replay port follows the 11-12 affiliate stock rules

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

Both files are in the recorder's `tools_sha256` (since vault `f1aaadd4`), so every root bundle recorded on
these tools would read `generator_stale` against the port from #1446.

**What.** Regenerated `runtime/mvp_runtime/blog/kw_pipeline.py` and `affiliate_link.py` from vault `6a4fd2f9`. The
generator is unchanged. `family_reps` is not ported, because the port keeps only `kind`, `jload` and `excluded`,
and the pipeline does not call it. `preflight`, `naver_slot` and the other ports were already current.

**Verified.** See the PR. A copy-vault round trip on `6a4fd2f9` was replayed by the new port. Root's earlier
bundles read `generator_stale`.

**Deploy window.** Deploy between the 2026-11-12 13:00Z replay and the 2026-11-13 13:00Z replay.
- Not before: the 2026-11-12 root bundle was recorded on `f1aaadd4` tools. It compares only against the #1446
  port that is running now, and an earlier deploy would turn that replay stale.
- Not after: the 2026-11-13 bundle is recorded on these tools. A later deploy leaves that replay stale, and a stale
  replay is never retried.

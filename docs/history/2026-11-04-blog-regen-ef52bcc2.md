# 2026-11-04 — Blog: the keyword replay port regenerated from vault ef52bcc2

**Why.** Thomas's Mac commit `ef52bcc2` (2026-11-03, three new affiliate topics: 복합기, 포토프린터, 매장 cctv) changed
the vault's `tools/kw_pipeline.py`. Root's 07:10 KST run on 2026-11-04 used it, so that day's bundle records
`kw_pipeline.py` at sha `21814ba4`. The port running in `candidate-1356` was cut from `83c84801` (`14735749`).
`blog_kw_replay` replays each bundle once, and an existing record is never retried. Without this regeneration,
the 22:00 KST fire would have recorded the 2026-11-04 bundle as `generator_stale` for good, and the P4a run of
agreeing days (1 of 14, ending at 2026-11-02) would have restarted.

**What.** `python scripts/blog_vendor.py --vault /home/claude/obsidian-thomas` rewrote only
`runtime/mvp_runtime/blog/kw_pipeline.py`:
- `cctv` and `홈캠` join `EQUIP_GENERIC`;
- three `AFF_STORY` lines are added ahead of `프린터`;
- the header and `GENERATED_FROM_SHA256` move to `ef52bcc2`.

`blog_taxonomy.py` and `affiliate_fetch.py` are unchanged since `83c84801`. `--check` passes afterwards.

**Verified offline**, with the new port against root's recorded bundles:
- `20261104-071007`: `compared`, agree. 2419 responses, 0 misses, 0 differing.
- `20261102-071006`: `generator_stale` (`blog_taxonomy.py`, `kw_pipeline.py`). This is the control: the sha check
  still separates the two. That bundle already has its record (compared, agree), so it is not replayed again.

**Deploy order.** The deploy must land before 22:00 KST (13:00Z) on 2026-11-04. If it lands after, that day's
record is already stale.

**Standing consequence.** Any vault change to `kw_pipeline.py`, `blog_taxonomy.py` or `affiliate_fetch.py` costs
the next morning's bundle, unless a regeneration is deployed before that day's 22:00 KST replay. Losing a bundle
restarts §4's 14-day count. The weekly report's P4a line shows the run.

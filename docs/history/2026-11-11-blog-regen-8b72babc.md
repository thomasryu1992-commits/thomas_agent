# 2026-11-11 — Blog: the keyword replay port regenerated from vault 8b72babc

**Why.** On 2026-11-11 two vault commits changed `tools/kw_pipeline.py` after root's 07:10 KST run. The port is
still cut from `ef52bcc2`.
- `464b2630`: the trend-source verdict (인사이트 T). Trend seeds no longer enter the queue unless `KW_TREND_READ=1`.
- `8b72babc`: Thomas's "필터 보완". Each trend seed's fate goes to a `queue.md` section and to the history
  snapshot's new `trend_review` key. Seeds the server lane dictionary drops are marked 검토 필요 instead of being
  thrown away. `candidates` is untouched, so nothing in the review list reaches the queue or the 14-day pool.

Root's 07:10 KST run on 2026-11-12 will record `kw_pipeline.py` at sha `7de3aa15`. Without this regeneration, that
bundle is `generator_stale` for good, and §4's 14-day count restarts (the standing consequence in
`2026-11-04-blog-regen-ef52bcc2.md`).

**What.** Running `python scripts/blog_vendor.py --vault /home/claude/obsidian-thomas` rewrote only
`runtime/mvp_runtime/blog/kw_pipeline.py`. The vendor script itself gains one fixed edit.
- New vault code `TREND_READ = os.environ.get("KW_TREND_READ", "0") == "1"` would have been the port's only
  environment read at import. The new edit pins it to `False`, the vault default, in the same way `REPLAY_DIR` is
  pinned. A root run made with the switch on would show up as a disagreement, not as a silent match.
- `blog_taxonomy.py` and `affiliate_fetch.py` are unchanged since `ef52bcc2`. `--check` passes afterwards.

**Verified.** In the worktree, 224 tests ran with `-k "blog or vendor or domain_isolation or gate_env"` and all
passed. The module imported was the worktree's own; its `__file__` was asserted. **Not verified offline:** root's
bundles sit under a root-only directory that the claude account cannot read since C6b. The 2026-11-12 22:00 KST
replay is the first real comparison.

**Deploy order.** The deploy must land before 22:00 KST (13:00Z) on 2026-11-12, or that day's record is stale.

# 2026-11-12 — Blog: the keyword replay port carries the Mac issuing tool's `kind()`

**Why.** On 2026-11-11 (Thomas, via the Mac hand-over `handoff-1111-keyword-supply`), vault `8898cc4f` changed how
`tools/kw_pipeline.py` counts the affiliate stock. `update_stock` now splits 대기 into four states: 대기, 같은기기,
쉼 (a 후보 부족 family, 14 days) and 보류. It decides "the same device" with the Mac issuing tool's
`affiliate_link.kind()`, so that one rule decides it on both sides. The pipeline imports that file
(`_aff_link_mod`: `sys.path` plus `import affiliate_link as al`).

`scripts/blog_vendor.py` turns the `sys.path` line into `pass` but left the import absolute. A plain regeneration
would have given a port that fails inside `update_stock`, which `run` calls on every full run. The same day also
moved `blog-preflight.py` (Mac `2fd65479`, the `mac_capture` Y3 case) and `naver_slot.py` (`dac1759d`/`334df170`,
the new windows from 2026-11-12 and hold drafts losing their date). All three ports were stale.

**What.**
- **A fourth replay source, `affiliate_link`.** It keeps only `kind`, `jload` and `excluded`, plus the constants they
  read. The browser-driving rest (Playwright, Brand Connect) goes, the same treatment as `affiliate_fetch`. Its
  import-time `sys.path` insert is removed, and `VAULT` is set by a generated `configure()`. The paths that depend
  on `VAULT` (`KW_STATE`, `EXCLUDE`, …) are found by the same `_path_assignments` the pipeline's tail uses, which is
  now applied to any tail that has the placeholder.
- **The pipeline's import.** `import affiliate_link as al` becomes a relative import. The pipeline's `configure`
  also configures `affiliate_link`.
- **Staleness.** `GENERATED_FROM_SHA256` now names `affiliate_link.py`. The vault recorder lists it in
  `tools_sha256` from `f1aaadd4`, so a change to `kind()` alone makes a bundle `generator_stale` rather than a quiet
  disagreement. Older bundles lack the key and read as stale, as they would anyway: `kw_pipeline.py` changed.
- **The regeneration** is from vault `f1aaadd4`: `preflight`, `naver_slot`, `kw_pipeline` and the new
  `affiliate_link`.
- **One test moved with `naver_slot`.** `test_a_day_chosen_by_hand_differs…` expected 2026-11-14's second window at
  15:00. Since the cutover moved to 2026-11-12, that day is under the new 10/13/16 windows, so it is 13:00.

**Verified.**
- **A real round trip.** A full `run` on a copy of vault `f1aaadd4` with `KW_REPLAY_DIR` set (claude account, no
  Search Ad key) gave bundle `20261112-004117` with 612 responses. The new port replays it as `compared`, `agree`:
  85/85 candidates, 607/607 Tistory, out-of-league and ranks equal, 0 misses, 0 fields differing. That run went
  through `update_stock` and the ported `kind()`.
- **Old bundles.** Root's `20261111-071008` reads `generator_stale` with `['affiliate_link.py', 'kw_pipeline.py']`.
- **Tests.** `blog_vendor --check` passes. The full suite passed: 11,730 passed, 307 skipped.
- **Not verified.** A Search Ad (`has_vol`) branch on the new code. Root's first bundle on this vault is the
  2026-11-12 07:10 KST run.

**Deploy order.** Deploy before 22:00 KST (13:00Z) on 2026-11-12, or that day's root bundle is replayed as stale and
never retried.

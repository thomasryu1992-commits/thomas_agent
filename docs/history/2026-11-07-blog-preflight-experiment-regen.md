# 2026-11-07 — Blog department: the verify port learns the E1·E2 experiment checks

**What.** Thomas approved a Naver test of two candidates from the search-top benchmark (vault
`analytics/benchmark/naver-search-top-2026-11-07.md`). E1 tests a first screen that answers at once: hero image, the
keyword phrase as a quote, then a short conclusion. E2 tests title forms. The design and its pre-registered readout
are in the vault: `analytics/benchmark/첫화면-제목-시험설계-2026-11-07.md` ("1·2 시험 설계", then "응, 구현해줘").

The vault side changed three things in `tools/blog-preflight.py` (vault 9b3d22f0). This PR regenerates the
P2 port, `runtime/mvp_runtime/blog/preflight.py`, with `scripts/blog_vendor.py`, so that `blog_verify` keeps
judging the cron's drafts with the same rules:
- **EX0–EX3** check that a draft follows the arm in its frontmatter `experiment:` line. EX0 checks the format,
  EX1 the assignment (the same SHA-1 bit rule as the vault's `tools/blog_experiment.py`), EX2 the first-screen
  order, and EX3 the title form. Without the line, or with `experiment: none …`, nothing changes.
- **Y1** skips a leading `[이미지 1 …]` block, as well as quotes, when it looks for the intro. Without this, every
  E1-B draft fails Y1, because its intro would come after the image.
- **K4** stops counting `[이미지 n — …]` placeholder lines in the first 200 characters. Those lines never reach the
  published post, but their keyword let two drafts pass (187편, 204편). This fix stands apart from the experiment.

**Why regenerate now.** The experiment starts only after this port is deployed. Until then, `blog_verify` in the
running image would judge an E1-B draft with the old Y1 and record a mismatch against the cron's verdict.
The vault's `ops/experiments/E1E2.json` has `start: null`, so `blog_experiment.py arm` answers `none` and no draft
carries the line yet.

**Checked.**
- `blog_vendor.py --check` reports that the port matches the vault.
- Blog lane tests: 159 passed.
- The vault preflight regression test passes with 13 new cases (scripts c4fa859).
- Run against all 207 Naver drafts, the new preflight changes only 187편 and 204편, which each gain a K4 WARN.

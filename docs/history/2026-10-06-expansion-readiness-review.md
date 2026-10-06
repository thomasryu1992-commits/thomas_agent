# Expansion readiness review: both expansions already have a decided path; Q1 puts the server content scripts under git

- **What was asked.** Thomas plans to grow content (blog, prompts) toward sales and marketing, and
  crypto trading toward whole-asset management. He asked how to proceed, for a modularisation and
  refactoring review, and for the bottlenecks.
- **What the review found** (`docs/proposals/EXPANSION_READINESS_REVIEW_V0.1.md`):
  - Both expansions are already gated by records. `MULTI_ASSET_EXPANSION_V0.1.md` sets P1→P4;
    P1 is built and reads `NOT_CONFIGURED` until the KIS key exists.
    `PERSONAL_BRANDING_EXPANSION_HYPOTHESIS_V0.1.md` §4 has the stages.
  - Refactoring stays closed (§G, the crypto plan). The one structural gap is outside this repo:
    the daily content path runs from `~/.claude/scripts` with `.bak` files as its history.
  - Two engines write Tistory drafts: the Claude cron, and the runtime `blog_content` Tistory
    profile.
  - The Mac's post-publish check runs a stale vault copy of `blog-preflight.py`.
- **Q1 decided and partly built, outside the repo.**
  - The scripts directory is now a local git repository: baseline `44224ba`, `SCRIPTS.md`,
    `run-tests.sh`, a `blog-preflight.test.py` regression test, and an auto-commit in the morning
    job.
  - The old `.bak` files are archived in `/root/backups/`.
  - Deleting them, and turning the diverged copies into links, waits on Thomas: the session's
    permission check refused file deletion.
  - No GitHub remote: the vault already pushes these scripts off the host daily.
- **This PR changes no code, schema or policy.** It adds the review, this entry and the
  regenerated `STATUS.md`.

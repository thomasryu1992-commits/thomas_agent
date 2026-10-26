# The runtime blog lane is retired whole; blog production is the vault path's

- **Why:** Thomas decided on 2026-10-26 that blog production stays on the path that runs today: vault keyword
  tools, the claude cron that drafts, and the Mac and SSH-session publishing. The runtime's own lane is removed
  (`docs/proposals/BLOG_DEPARTMENT_V0.1.md` §2).
  - The lane fired three times (09-06, 09-13, 09-20), and each fire ended at selection with `NO_ELIGIBLE_KEYWORD`.
  - It was held on 2026-09-27.
  - Its 57 trial packages matched no real post. The 10-08 Phase 3 audit found 57 of 57 unlinked, 0 published and
    0 rank snapshots.
  - About 5,500 lines kept a second drafting engine and a second copy of the quality rules beside the one that
    publishes. "A lane earns its size with evidence or is removed whole" (`CLAUDE.md`).
- **What left:**
  - **Modules:** `blog_content`, `blog_draft`, `blog_draft_score`, `blog_naver`, `blog_overlap`, `blog_platform`,
    `blog_prompt`, `blog_quality`, `blog_rank`, `blog_tistory`.
  - **Runtime wiring:** the `content_ideation` kind with its delegation and sheet; the worker's `content_ideation`
    job, its `ideation_inputs` key and the job's now-unused store parameters; the two blog budget profiles.
  - **Scripts:** `record_published_url`, `score_blog_draft`, `track_blog_rank`, and the eleven test files of all of
    the above.
  - **Compose:** the worker's three read-only vault mounts, `MVP_BLOG_PUBLISHED_ROOT` and
    `MVP_BLOG_OPENROUTER_MODEL`.
  - **Diagnostics:** `IDEATION_INPUTS_REQUIRED` left the shared-code list, and the index is regenerated.
- **What stays, on purpose:**
  - `naver_research.py`: the analysis CLI's `--naver-keywords` evidence.
  - The budget-profile mechanism in `budgets.py`/`pipeline.py`/`prime.py`, now with no profile defined.
  - The R8 workspace writer and its mount on the worker: the CLI's `--write-output` can still use them.
  - The `blog_content_package` and `blog_rank_snapshot` record kinds and schemas, so rows already on the ledger and
    in its archives keep reading and validating.
- **Host step (Thomas):** remove the three disabled `content_ideation` schedule rows (`scheduler_cli remove`).
  A row left in place is refused by name at its fire (`UNKNOWN_KIND`), but they are disabled and never fire.
- **Next:** the department's stage 1·2 observation (its own PR) mounts the vault on scheduler-maint, read-only.

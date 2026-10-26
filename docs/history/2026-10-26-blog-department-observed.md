# The blog department, observed: a weekly section in the management report and a record on the ledger

- **Why:** blog production stays outside the runtime (Thomas 2026-10-26, `BLOG_DEPARTMENT_V0.1.md`). That left it
  outside the organisation's reports and ledger too. Stages 1 and 2 of that design bring it in as something the
  organisation sees and records, without running it. The counts are also the baseline the later optimisation
  and refactor steps are verified against, and D8's lane evidence (2026-11-25).
- **What:**
  - `blog_department.py` reads `status`, `created`, `published` and `scheduled` from the front matter of
    `content/naver/*.md` and `content/tistory/*.md`. Per platform it gives:
    - status now;
    - new drafts by `created`;
    - published, counting only `status: published` posts by their `published` date;
    - upcoming, counting only unpublished posts by their `scheduled` slot. On Naver that slot is the plan,
      so it is never called published.

    Dates are KST calendar dates, as the vault writes them. A file without front matter is counted as
    unparsed. An unmounted vault refuses (`BLOG_VAULT_UNAVAILABLE`) rather than reading as an empty week, and so does a platform folder with no posts in it (a mount that reaches nothing, or a folder uid 10001 cannot list: on 2026-10-26 the folders held 164 and 144 posts).
  - The weekly management report renders it as "■ 블로그", and the same fire appends a `blog_department_week`
    record (closed schema v0.1, counts only). A record that cannot be built or appended is named on the status
    line and costs the report nothing.
  - scheduler-maint mounts the two post folders read-only (`THOMAS_BLOG_VAULT_DIR`) and names
    `MVP_BLOG_VAULT_ROOT`. No other service mounts the vault, and tests pin both.
- **Checked:**
  - On the real vault (2026-10-26 10:00Z) the status counts equal an independent tally of the same files:
    Naver draft 38, hold 3, published 123; Tistory hold 2, scheduled 23, published 119.
  - The week's new and published counts equal a grep: Naver 16/20, Tistory 21/20.
  - Two mutations, dropping either status guard, are caught by the tests.
- **Host steps (Thomas):** check that `.env`'s `THOMAS_BLOG_VAULT_DIR` names the clone the drafting cron writes
  (`/home/claude/obsidian-thomas` since C6b). Unset or stale, the section says unavailable or shows old counts.
- **Depends on:** #1319, the lane's removal. This branch is stacked on it.

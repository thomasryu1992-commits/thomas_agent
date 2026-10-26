# The blog department: today's production logic recorded, the decisions on the runtime lane and the loop

- **Why:** Thomas decided on 2026-10-26 that blog production stays on the path that runs today: vault keyword
  tools, the claude cron that drafts, the Mac and SSH-session publishing. The runtime blog lane is to be removed
  whole, and the blog is to join the organisation as a department the system observes, records and later directs.
  He asked for the logic to be written down, and for the order design → verify → optimise → verify →
  refactor/modularise → verify.
- **What:** `docs/proposals/BLOG_DEPARTMENT_V0.1.md` (PARTIALLY DECIDED) holds:
  - §1, the production logic step by step, with the source each row was checked against;
  - §2, what leaves the runtime and what stays (`naver_research.py`);
  - §3, the loop and its three stages;
  - §4, the refactor track;
  - §5, the agreed order and each step's verification standard;
  - §7, the open decisions L1–L3 and R1–R3.
- **Not in this PR:** the lane's removal and the stage 1·2 observation, which follow as their own PRs. No code,
  vault or script change.

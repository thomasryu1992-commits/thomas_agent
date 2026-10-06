# System scorecard v0.3: still 7/10, process 7 → 8, and the first scheduled encrypted backup ran

- **What was measured.** The whole system was scored again against v0.2's areas on the same day, around
  14:00Z (`docs/proposals/SYSTEM_SCORECARD_V0.3.md`, RECORD).
- **What moved.** Process went from 7 to 8. Host worktrees went from 30 to the primary checkout only, and
  local branches from 155 to a handful. The content scripts outside the repo came under git with
  regression tests. The four expansion-readiness questions were decided (#1150, #1152–#1154).
- **What closed without moving a score.** The 07:45Z core backup was the first scheduled run under the
  rotated age key. It wrote `govstate-20261006-0745.tar.gz.age`, and the 08:00Z watch read `OK checks=6`.
- **What did not move.** The overall score stays 7 (mean 7.1). Ops waits on Thomas rotating the secrets
  that left the host in plain text. Product waits on the 10-31 Tistory verdict, and crypto edge on
  2027-03-22.
- **This PR changes no code, schema or policy.**

# 2026-09-29 — Blog revision: a floor on the cut, and a revision never leaves the package worse

`bcp_e9717849588d1f438c66` ('보건증 재발급') had a first draft averaging 153 characters per
paragraph (ceiling 150). Asked to cut (#1033), the revision halved every paragraph: average 71,
body 1,221 against the 1,800 floor. It replaced a draft 3 characters from passing with one 579
short.

- The cut ask now touches only the paragraphs over the plan's 140, one sentence each, and says
  to leave the others alone. It states both floors: no paragraph under 120, and the body fails
  under 1,800.
- `blog_draft_score.shortfall` sums each missed critical standard's distance from its range,
  relative to the bound it missed (3 over 150 → 0.02; 579 under 1,800 → 0.32).
- When the revision still fails and is further off than the first draft (contract failures
  first, then shortfall), the first draft stays. The outcome is
  `REVISION_FURTHER_OFF:KEPT_FIRST_DRAFT`, `revision_detail` names what the revision missed, and
  POST.md shows it as "자동 수정 메모". On a tie the revision is taken, as before.

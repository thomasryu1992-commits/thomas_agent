# The research-epoch boundary is each cohort's close; the first is 2027-03-22

- **Decision (Thomas, 2026-10-06):** scorecard Q5 as recommended (`docs/proposals/SYSTEM_SCORECARD_V0.1.md`).
  RESEARCH_EPOCH Q3 (B, 2026-09-26) had put judgement-rule relaxation at an epoch boundary without saying
  when one falls, so six decided items that cite it as their gate could never open.
- **The rule:** a boundary is a cohort's fixed close (freeze + 180 days, FORWARD_COHORT_EXPANSION N4). The
  first is cohort 1's, 2027-03-22 — the same day the D3 research pause ends. Freezes are every 28 days (N2),
  so later boundaries come about 28 days apart (2027-03-30, 2027-04-27, …). A boundary is when a relaxation
  may be decided and applied, not a requirement to change anything; the judgement fingerprint records which
  rules a verdict ran under. Tightening stays outside the boundary rule.
- **What changed:** RESEARCH_EPOCH's status and a decision section listing the items that cite the boundary;
  CLAUDE.md's crypto-pause paragraph names the date; the scorecard records Q5. No code.

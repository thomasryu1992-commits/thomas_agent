# The crypto research pause (review D3) is lifted; judgement rules still loosen only at an epoch boundary

- **What:** Thomas lifted D3 on 2026-10-20. New templates, hypotheses and trials in `crypto/`, and reallocating the
  mint budget across families, are allowed again. `CLAUDE.md`'s guardrail now says so, and the decision is recorded
  in `SYSTEM_REVIEW_IMPROVEMENT_PLAN_V0.1.md` ("결정 (Thomas 2026-10-20)").
- **Why:** Thomas wants crypto strategy evidence work (G2a) to resume now, not after cohort 1's close. He saw the pause
  as set before the whole-portfolio design existed.
- **Deliberately unchanged:**
  - judgement and entry rules loosen only at a research-epoch boundary (RESEARCH_EPOCH Q3);
  - `positioning_store.MINTING_DECIDED` stays `False` until its own decision. Only its comment changed; no code
    path changed;
  - gates dated 2027-03-22 elsewhere (P3 stocks, D5 LIVE_AUTONOMOUS) keep the date.
- **Not edited:** dated records that say "paused until 2027-03-22" describe the time they were written.

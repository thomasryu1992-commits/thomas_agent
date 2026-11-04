# 2026-11-04 — Regime strategy set: one market label, 36 strategies, a seeded quarter holdout, two stages

**What.** Added `scripts/research/regime_strategies.py`, the implementation of
`docs/proposals/CRYPTO_REGIME_STRATEGY_SET_V0.1.md`. Thomas answered §6 Q1–Q4 with "기본값대로", so Q3 is option B.
- The §1 BULL/SIDEWAYS/BEAR label has a 3-day lag.
- The 36 strategies of §3 run on one engine, which holds positions open-to-open exactly like the v0.2 basket.
- Six quarters are held out: one per year, drawn by sha256 from a fixed seed, with a 30-day embargo.
- `stage1` screens on the rest. `stage2` reads the held-out quarters and needs the stage-1 record.
- `oi-plan` prints the metrics spans that `binance_archive.py fetch` downloads for S11 and D11.
- `cross_section_basket.Bar` gains `high` and `low`, both defaulting to None, so the baskets read nothing new.

**Why futures quarters, not spot 2017–19.**
- The spot holdout showed 2–5 coins a side until mid-2019. Breadth ≥ 0.55 over that few is one coin's sign.
- SMA200 also eats the first seven months of spot.
- Spot cannot test the seven funding/OI strategies at all.
- The quarter split keeps all 36 on the instrument Thomas trades. Its weak point is a thin BULL holdout (15 weeks).

**Why baskets keep basket mechanics.** §3 says B03/D03 exist to ask whether the whole basket rides a regime or
only one leg earns. That needs B03 + D03 to equal v0.2's S-MOM30, which a test pins.
- So the six baskets keep the fixed ±1/k weekly weights: no stop, no six-leg cap.
- The position strategies take the §2 common rules.

**Why two commands.** Thirty-six fresh implementations will have bugs. If one run read both stages, the first bug
found afterwards would cost the holdout. `stage2` refuses without a stage-1 record of this test.

**Why the family carries five prior p values.** Some candidates derive from hypotheses already tested on this
window, so their p values come along:
- B03 and D03 bring S-MOM30's v0.1 and v0.2 p values.
- S04 brings S-MOM7. The draft said "S-REV"; that was wrong (7-day, weekly) and was fixed before the run.
- S09 and D07 bring S-CARRY. D07 was found during the build: it is S-CARRY's short leg.

**Pre-run smoke.** Every strategy ran on the screening spans without error. Only entry counts were printed,
never returns. None has zero entries; S11 (12), D05 (14) and B05 (17) are rare by design.

**Stage 2 checks the code.** The stage-1 record carries `code_digest`, the sha256 of the module's own bytes.
`stage2` refuses a record whose digest differs, so a rule changed between stages forces stage 1 again.

**Tests.** 22 in `tests/test_research_regime_strategies.py`. Fifteen mutants are caught:
- floor ignored, priors dropped, two-sided p, no embargo, regime gate off
- B03/D03 swapped, lag 2, no segment-end exit, stop 1.5, S04 parent dropped
- years ignored, seed format, the oi-plan span
- the digest check removed, and priors brought by a NOT_JUDGEABLE candidate

# 2026-11-16 — discovery loop round 42: how large an effect the tier sweeps name as a lead

**Shipped (registration and tool; nothing run in this commit):** `scripts/research/strategy_ladder.py`
(`SENS_*` constants, `SENS_FAMILIES`, `sens_tier`, `sens_planted` on round 39's `_normal_scores`, `sens_book`, `_null_sharpes` taken out
of `tier_sweep_reading` unchanged, `sens_null_check`, `sens_lead`, `sens_bracket`, `sens_run`, command `sens --family
mid|lower --null PATH`), tests, `docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md` §60.

**Why.** Round 39 measured the map's doors. The record says twice that the sweep's sensitivity was never measured
(sections 56.3 and 57.7), and sections 58.5 and 59.6 fixed it as the round after H-A1.

**Choices that are not obvious.**

- *No real variable is read.* The families' sizes (36 and 24 books) and their months (48 from 2021-01, 40 from
  2021-09) are written in code from the record. The files that hold real books' rows are not opened.
- *The planted book is read on the months the real books stood on*, not on the tier's whole span. A longer span
  would make the door look finer than the one the real books faced.
- *The false-alarm gate counts p < 0.05 at size 0*, not leads. A lead at size 0 needs the entry rule and nearly
  every null beaten, so a gate on leads would pass almost anything. A size-0 book is built the way a null is, from
  another seed space, so about five in a hundred are expected.
- *The judgement compares two brackets from the same run*: the size at which most seeds are leads, and the size at
  which most clear the entry rule alone. No outside line is brought in.
- *One book alone in its family meets Benjamini-Hochberg's first threshold.* A family with other small p's could
  be looser. The real families had three and zero books under 0.05.

## Result (2026-11-16 09:47Z): both sweeps are as fine as the entry rule

- All three gates passed. The first five stored null books matched on both tiers, and at size 0 the count of
  p < 0.05 was 5 and 8.
- On both tiers the lead bracket equals the entry-only bracket. On ranks 31–100, 81% of seeds are leads at
  ρ 0.05 (rank correlation 0.046, Sharpe 1.55, 26.8%/yr after cost). On ranks 101+, 82% are leads at ρ 0.08
  (0.077, 1.64, 26.6%/yr). What binds the sweep's lead rule is the entry rule; the family correction asks no more.
- On ranks 31–100 the null maximum (0.988) sits under the entry rule's Sharpe of 1.0, so p and the entry rule
  move together there.
- The planted books trade less than real ones (cost 3.7–6.1%/yr on ranks 31–100 against the real books' median of
  9.0%), so a real variable with the same rank correlation would keep less.
- Nothing closed is re-judged. Next is round 43, the map's scenario cells' sensitivity.

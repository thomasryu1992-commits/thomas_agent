# 2026-11-14 — discovery loop round 29: the premium at the other lookbacks

**Shipped (registration and tools; nothing run in this commit):** `scripts/research/signal_lab.py` (`_premium` takes a
lookback; `premium_7`, `premium_14`, `premium_90`; 106 random variables),
`docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md` §43.

**Why.** Round 26's table struck these on two grounds. Re-reading the table before writing that nothing was left,
neither stood: "funding's kin" contradicts the rule that mapped the premium itself (0.72 is under the line), and the
two registered screens that read the premium do not read a cross-sectional windowed level. The reasoning that
mapped the spread's other lookbacks applies here as well.

**Not obvious.** The same three windows as the spread, bare levels with no shapes; a test names the whole set.

**Result (2026-11-14 18:03Z).** No lead among the three: the largest |t| is 3.13 (seven days, the top tier, weeks
of high funding) against a line of 4.30. The premium is finished. 28 variables have been added since round 18 with
no lead. The refusal-tally tool the table listed as "not built" was built the same day on Thomas's word, as its own
PR from main (#1475).

# Research / forward throughput: why nothing reaches LIVE, measured before anything is widened

- **What was asked:** whether to widen research, paper and forward throughput so that a good strategy has a better
  chance of reaching LIVE, and why none has. Analysis and design only. No live limit, judgement rule or entry bar
  was to change.
- **What this PR adds:** `docs/proposals/RESEARCH_FORWARD_THROUGHPUT_ANALYSIS_V0.1.md` (DRAFT) and `STATUS.md`
  regenerated. No code. It builds on `FORWARD_COHORT_EXPANSION_V0.1.md` (N1–N4, decided the same day) rather
  than re-asking it.
- **What it found (2026-10-01, read-only against the running state):**
  - FORWARD_CONFIRMED = 0 is forced today. The judge needs eight 14-day slices from the first trade, and no
    cohort or pool member's first trade is 98 days old. The earliest dates are 11-03 (pool) and 11-16 (cohort).
  - After that, power binds. At the measured 4h rate (0.19 trades/day), a +0.2R edge needs about 5.7 years for
    80% power at z 1.96, and about 10 years at the LIVE door's 2.94.
  - The evidence for alpha is weak:
    - holdout: 71% of judged lineages CONTRADICTED;
    - forward: members are 0.11R below their coin-flip twins on average;
    - the exception is 4h open interest, which holds all 8 holdout CONFIRMED and is +0.36R against twins' +0.08R
      (n=25).
  - Three premises of the ask did not hold:
    - "ROBUST 92" is the label stored at mint, and 7 under today's rules.
    - The lifetime attempt count reaches only a sort key, never a gate.
    - "4h control about 5%" is a simulation prior, not a measurement.
  - Cohort size is limited by supply, not by the host:
    - replay costs about 1 ms per member-context, with RSS flat at 186 MB;
    - the only superlinear cost is the report's member × row scan;
    - 53 of cohort 2's 82 members are parameter siblings of cohort 1 members, because the one-per-key rule holds
      within one freeze only.
- **Recommendation:** decide the scope of the D3 research pause first (P0-1, recommended: measurement and
  display only). Then:
  - a cross-cohort sibling rule;
  - an indexed report;
  - a paired member-minus-twin readout pooled by family and context, as the basis of a hierarchical judgement at
    the cohort close.

  Live limits stay where they are until the prerequisites in Q7 are met.

# THROUGHPUT's measurement-and-display queue is built; what is left waits on its gates

- **What:** the status line of `docs/proposals/RESEARCH_FORWARD_THROUGHPUT_ANALYSIS_V0.1.md` now names the
  four items scorecard Q3 queued (Thomas 2026-10-06) as built — P0-2 (#1137), P1-2 (#1138), P0-3 (#1139),
  P1-3 (#1140) — and records the first `--pairs` reading. Kept to its own PR so the three code PRs did not
  conflict on one line.
- **Left:** P1-4 (the hierarchical-judgement proposal, before 2027-03-22) and P2 (after the verdict). Both
  are gated, not waiting on a decision.
- **Checked while writing:** #1114's `waiting_on` names what an unjudged member waits for (trade floor, no
  signal) and counts no refusal, so P0-3 did not duplicate it.

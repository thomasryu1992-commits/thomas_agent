# The cohort walk counts the matched signals a door refused

- **Why:** THROUGHPUT P0-3 (`docs/proposals/RESEARCH_FORWARD_THROUGHPUT_ANALYSIS_V0.1.md` §5, decided
  2026-10-06 via scorecard Q3). Of 197 members, 75 sat under the trade floor and 32 had no settled
  trade, and "regime, distribution, cost or risk refusal" read *not measurable*: a refused entry opened
  nothing and left nothing behind, so "no signal" and "signals the doors kept refusing" looked the same.
- **What changed:** `forward_book.replay_entry_bar` takes an optional `refusals` counter and, when
  given one, counts each matched signal a door refused, by door (`ENTRY_REFUSAL_KINDS`: regime,
  distribution, no_plan, entry_cost, stop_beyond_liquidation). A bar in cooldown, with a position
  open or with no match is not a refused entry. Only the cohort walker (`advance_member`, members and
  twins alike) passes one; the pool's forward book and the seeder pass none and are unchanged.
- **Where it lives:** on the walker's book entry, beside `opens_count` — the entry is an open mapping
  that `_checked_entry` does not close, so an older image reads past the new fields. `entry_refusals_from`
  is the first bar counted: slots walked before this version lost the bars before it, and the report
  says "since". Outcome rows and their seals are not touched. The at-most-once bar mark that keeps a
  re-walk from settling twice also keeps it from counting twice.
- **Shown, not used:** `cohort_report` lines carry `entry_refusals`; `forward_cohort report` ends each
  cohort with one line of totals. `board_summary` picks its fields by name and does not carry it; no
  verdict, ranking or door reads it.
- **Counts start at the next walk after deploy;** this PR deploys nothing.

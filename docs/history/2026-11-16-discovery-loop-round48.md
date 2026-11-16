# 2026-11-16 — discovery loop round 48: the top-trader accounts ratio as new variables

**Shipped (registration and the first tools; nothing read in this commit):** `scripts/research/signal_lab.py`
(`ACCOUNTS_*`, `accounts_value`, `load_accounts`, `with_accounts`, `hold_top_account`, three overlap candidates),
`scripts/research/strategy_ladder.py` (`sealed_roots_digest`, `top_account_build`, host command
`top-account-build`), tests, `docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md` §66.

**Why.** Round 47's column audit found the metrics files' top-trader accounts ratio unread by any variable.

**Choices that are not obvious.**

- *Three, not more.* 600 null books allow 60 variables, but the top tier's reading rounds p to four places; the
  smallest p then reads 0.0017, and the first threshold stays above it only up to 58 books.
- *Overlap first, on inputs.* The existing rule (section 40): a candidate whose median rank correlation with a kin
  reaches 0.8 on the top tier is not registered. The variables are added only after that, in a second commit.
- *Each tier's values come from the files its positioning values come from*, so the new readings stand on the same
  sample as their kin. The build reads every zip and writes none; the sealed roots must hash as before.
- *Map controls grow nested.* The first 106 keep their seeds; a line can only rise, and the old lines must
  reproduce from the first 106 before a new line is read.

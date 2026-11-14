# 2026-11-14 — discovery loop round 31: three checks on the lead before any hypothesis form

**Shipped (registration and tools; nothing run in this commit):** `scripts/research/strategy_ladder.py` (`LEADS`,
`LEAD_KIN`, `lead_check` and the `lead-check` command), `docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md` §45.

**Why.** Round 30's sweep left one book above every null: the 30-day change of the spread estimate, long the low
side. Three things decide what that book is, and the first two read no return.

**Choices that are not obvious.**

- *Ties are broken by the coin's name.* `risk_weights` sorts on `(value, symbol)`. Half of the level's values are
  zero, so the change is zero for many coins, and the order among them is the alphabet. The check counts how much
  of the book those coins carry. No threshold: tied coins sit in the middle of the ranking, where weights are
  small, and re-building the book with another tie rule would be a variant.
- *Kin by positions, not by ranks.* Round 26's rank correlation could not say whether the spread is the closed
  volatility cluster read again. The check compares what the two books hold, by the cosine of their coin weights,
  against a threshold fixed beforehand; the same book upside down counts as the same book.
- *The side was chosen on the span it was measured on.* Rung 2(b) already says how to read such a book: each of
  2022, 2023 and 2024 with the side chosen on the years before it. The transfer tool gives that; only the lead's
  row is printed by the reading script.
- *The book is not touched.* No other lookback, logic, universe or tie rule. If it becomes a candidate it is the
  swept book, `run` must reproduce its Sharpe, and its p is the sweep's.

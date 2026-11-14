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

**Result (2026-11-14 19:47Z).** Neither closing rule fires. The lead's positions are not the closed volatility
book's (median cosine 0.09 against a threshold of 0.5), and with the side chosen on the years before, 2022, 2023
and 2024 give +25.2%, +51.9% and +16.0% of total position (the low side every time).

- *The tie expectation was wrong.* Coins tied at zero are 22% of the coins and carry 21% of the book's weight at
  the median decision, and almost all of it at the largest (95%). That part of the book is ordered by name.
- *Two more facts for the form.* The nearest book is the lead's own level (cosine 0.66), which did not separate
  from the null (Sharpe 0.48). And in 2024 the variable's weekly rank correlation has the opposite sign while the
  book still earns: that year's return does not come from the ordering.

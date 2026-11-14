# 2026-11-14 — discovery loop round 19: more shapes of registered variables, the first shape

**Shipped (registration and tools; nothing run in this commit):** `scripts/research/signal_lab.py` (`_change`,
`CHANGE_OF`, three variables `ls_change_30`, `top_change_30`, `taker_change_30`; the random variables go from 50 to
56), `docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md` §33.

**Why now.** Round 18's one-page report left two monthly checks on the data in hand. This is the first: the
registry's rule for growing variables (§3.1), which names four shapes and asks for a cap.

**Choices that are not obvious.**

- *The list comes from a rule and its strikes are written down.* One shape only (the change against the period
  before), applied to every registered level, then struck where the base is a closed cluster (volatility and what
  reads as its kin), is already a change, has no change, or is a linear combination of two others.
- *The cap is three, and it was found rather than chosen.* The sweep's 300 null books can correct for at most 30
  variables (a ladder test already pinned the inequality); the registry held 27. Adding more first needs a larger
  null in the sweep, which is another tool's registered constant. The three taken are the ones the first inventory
  named as there and unread: the change of positioning.
- *The random variables grow with the real ones.* §5's first rule keeps their ratio; a lab test already required
  it. With 56 the lead line can move, so the five positioning variables mapped in round 18 are not read again as
  evidence.
- *A lead buys a form, not a book.* And the three were raised after H-M1 was seen; the registration says so.

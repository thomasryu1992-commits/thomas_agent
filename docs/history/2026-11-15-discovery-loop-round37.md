# 2026-11-15 — discovery loop round 37: the tier sweep's lead, before any form

**Shipped (registration and tool; nothing run in this commit):** `scripts/research/strategy_ladder.py`
(`TIER_LEADS`, `tier_lead_check`, command `tier-lead`), tests, `docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md` §53.

**Why.** Round 36's sweep on ranks 31–100 left one book over both lines: 30-day funding, long the low side. The
sweep's own registration says a lead is not a candidate and is checked before any form is written, with the rules
that close it fixed first.

**Choices that are not obvious.**

- *The book is not changed.* One run rebuilds the sweep's book and must reproduce its row on eleven values; the
  unrounded Sharpe is printed beside it as a fact, because the entry rule has always read the rounded one.
- *Two rules can close it*, both already in the record: its positions laid beside H-R2's book (median cosine of
  0.5 or more means the closed book again), and rung 2(b), where each later year is read on the side its earlier
  years name. The sweep picked the side on a rank correlation of −0.0071 over the whole span, so that is the check
  most likely to tell.
- *Everything else is printed with no rule*, as material for the form: both sides' price, funding and cost by
  year, the funding share of the return (H-F1's line of one half, already computable from the sweep's row: 40%,
  so "price carried it"), which coins and months, what stood on bars with no volume, and how much of each side
  the closed funding-tail books' thresholds would have held.
- *The door is written before the reading*: a payer is on record for the funding leg only, and that leg net of
  costs is under the floor. If no record names who pays the price leg, the form's default applies and Thomas is
  asked a new question. No payer is written to fit.
- *No confirm read is asked for*: a book picked on funding is kin of C002 and C007, whose results on that span
  are known.

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

## Result: the lead is closed

One sandbox run at `42212065`; `~/research/discovery/tier-lead1/lead.json`.

- *The gate passed*: the row is the sweep's on all eleven values. Unrounded, the Sharpe is 0.99964; the entry rule
  read the rounded 1.000, as it always has, and that reading stands.
- *Not the closed book*: median cosine with H-R2's positions is −0.117.
- *Rung 2(b) fails*: the earlier years named the high side for 2022 (rank correlation +0.0082 over 51 weeks) and
  for 2023 (+0.0017 over 103), and the low side only for 2024. Read that way the three years are −18.65, −39.90
  and −3.33: mean −20.63% against a floor of 7.5%, three negative years against at most one. By the registered
  table the lead is closed and no form is written.
- *Printed with no rule*: funding was 40% of the return before costs (price carried it); one coin (DOGE) was 30%
  of the net and the top five were 82%; the top three months were 52%; the net is the difference of two large
  sides (in 2021 the long side made +143 and the short side lost 104).
- *Also recorded here*: Thomas answered D11 — "받지 않음". The depth files for ranks 31–100 are not fetched.

## After the result: the table laid against the record again

- *A sentence corrected*: the low side is first named after the weeks of 2021–2023, not "after 2024".
- *Funding inside the span, counted per member-month (inputs only)*: 9 of 1,641 member-months on the top 30 and
  31 of 3,229 on ranks 31–100 have bars and no funding. GTCUSDT's 15 months are our own gap (the archive has
  them); ICP, TLM, BNX and LEND are the archive's. GTCUSDT is not fetched now because that would move the tier's
  only reproduce-exactly gate and the sweep's rows while producing no new reading; it is fetched inside the next
  registration that reads funding on that tier, with the changed cells named at its gate.
- *The map points the other way from a book on ranks 31–100*: in 15 of the 17 weekly all-weeks cells with |t| of
  2 or more, the mean return difference between the top and bottom thirds has the opposite sign to the rank
  correlation (2 of 13 on the top 30). The map's lead rule reads the rank correlation only; a rank book earns the
  mean. That is a tool gap with no recorded reason and is the next round's candidate: a t for the spread in every
  cell, beside the random variables' largest, as a second lead rule.

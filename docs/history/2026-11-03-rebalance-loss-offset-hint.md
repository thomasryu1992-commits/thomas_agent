# The rebalancing plan nets overseas losses and shows the overseas holdings whose loss would offset a taxed gain

- **Why:** §2.3 step 4 of `REBALANCING_PROPOSAL_V0.1.md` (RB1, Thomas 2026-10-26) asks the plan to mark losses
  that offset a taxed sell in the same year. It was the last decided piece left unbuilt. Thomas picked it as the
  next task on 2026-11-03.
- **A fact corrected first:** §8.5 of `TOTAL_ASSET_ALLOCATION_V0.1.md` said every taxed sell nets gains and losses
  in the year. That holds only for overseas direct holdings, whose gain is transfer income: 22% above the year's
  2.5M deduction. From 2027 it also holds for crypto.
  - A domestic-listed foreign, bond or gold ETF's gain is dividend income (15.4%). It is taxed per sale and nets
    with nothing, including an overseas-listed ETF's loss.
  - Checked against secondary sources (Asiae 2026-08-19, KB Asset Management and KB Think ETF tax guides, a
    lawyer's answer). The NTS text itself was not checked. Both documents now say so.
- **What changed** (`holdings/rebalance.py`):
  - **Netting in the estimate.** An overseas sell at a loss in the plan used to count as 0. Its loss now widens
    what the year leaves untaxed, so a plan that sells an overseas loser and an overseas winner nets them. With
    the year's gain unknown, the plan still assumes the deduction is used up, and its own losses still net.
  - **The hint.** `Plan.loss_offset` is filled when the year has a taxed overseas gain. That gain is the plan's
    taxed overseas gain, plus the year's gain already over the deduction, less what the plan's own losses still
    cover. The hint lists every overseas holding (by `tax_kind`) at an unrealised loss that the plan does not
    sell, largest loss first, with an estimated tax saving of 22% × min(taxed gain, total loss).
  - **The render** prints it below the tax lines as "Not in the plan". It is never an order: selling is Thomas's
    choice.
  - Domestic-listed taxed ETFs are never listed. Neither is coin, which is tax-free in 2026 and outside Toss.
- **Not changed:**
  - The tax order, the step-3 room and the orders themselves.
  - The per-instrument tax-kind override still has no place to be written. It stays the one item left in the
    proposal.
- **Checked:**
  - The hint appears with the right taxed gain and saving at 2.4M and at 3.0M of the year's gain.
  - It is absent when nothing overseas is taxed, and absent for a domestic-listed loser.
  - An in-plan overseas loss covers the plan's overseas gain.
  - A loss the plan already sells is counted once, and its leftover reduces the year's excess.
  - The render prints and omits the block as it should.
  - Each of 7 mutations was caught: no in-plan netting, domestic losers allowed, the year's excess dropped, the
    leftover loss not applied, sold losers listed, no taxed-gain condition, the saving not capped.

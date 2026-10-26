# This year's realised overseas gain, rebuilt from Toss's closed orders (PR-B of RB1–RB6)

- **Why:** §2.3 step 3 sells overseas holdings only as far as the year's 2.5M KRW deduction still covers their
  gain. Toss has no realised-gain field, and Thomas decided the system computes it itself (RB3', 2026-10-26,
  `REBALANCING_PROPOSAL_V0.1.md`).
- **The rebuild** is in `holdings/overseas_gain.py`, which is pure. Every USD symbol is replayed from its first
  fill:
  - Buys add; a buy's commission is part of its cost.
  - Sells take their cost out; their commission and tax come off the proceeds.
  - A sell counts in the year of its `settlementDate`, not its `filledAt`. An unsettled sell takes its shares
    and counts no gain.
  - FIFO and the moving average are both computed. The planner uses FIFO (the proposal's §3 line) and says
    "method unconfirmed, estimate".
  - It is refused rather than partial when:
    - the read is truncated,
    - a sell has nothing bought before it,
    - a held symbol's rebuilt quantity or average price disagrees with the broker's `averagePurchasePrice`
      (within 0.5%; the broker's figure excludes commission),
    - the broker no longer holds a rebuilt position,
    - or a fill has no rate.
- **KRW rates:** the fire records the USD/KRW rate of the first fire of each KST day. A fill on a day before
  recording began uses the current rate and is counted as approximate. The count is shown.
- **The fire:** once a KST day it reads the whole closed-order history (`closed_orders` with no dates, now
  allowed, 50 pages) and writes `holdings_tax_estimate.json`. The file holds amounts, never a symbol, and is
  LOCAL_ONLY all the same: it is in the hook's file regex and the guard's table. A failed read keeps the last
  block and still records the rate; the next fire retries.
- **`--rebalance`** takes the FIFO gain only from an ok rebuild of this year made today or yesterday. Otherwise
  the gain is unknown.
- **Planner change, §2.3 as written.** PR-A put every overseas sell at step 3. Now:
  - Step 3 sells an overseas holding only up to `room / gain ratio`, and the rest waits for step 4.
  - With the gain unknown there is no step 3 at all.
  - Step 4 takes what is left of the overseas and taxed holdings, the smallest gain ratio first.
- **`Holding.average_price`** is new and in-process only. A missing one adds no board warning; it costs only the
  reconciliation.
- **Not built:** PR-C (the candidate screening report), PR-D (the monthly band notice), per-instrument tax-kind
  overrides, and the loss-offset hint at step 4. Two rules stay unconfirmed: the cost method and the rate the
  tax uses. The figure stays an estimate until Thomas checks it against Toss's year-end statement.
- **Checked:**
  - Hand-computed histories:
    - one buy and one full sell;
    - two buys and a partial sell, where FIFO 2,000,000 and the moving average 1,500,000 differ;
    - a year boundary by settlement date;
    - an unsettled sell;
    - an approximate-rate day;
    - six refusals;
    - KRW orders ignored.
  - The fire's daily rebuild and rate recording, a failed read, `--rebalance`'s freshness rule, and the step-3
    cap at 100,000 KRW of room.
  - Nine mutations were each caught:
    - year by fill date;
    - an unsettled sell counted;
    - a truncated read accepted;
    - reconciliation off;
    - FIFO and the average swapped;
    - step 3 uncapped;
    - step 3 on an unknown gain;
    - the gain not passed to the planner;
    - the day's rate overwritten.

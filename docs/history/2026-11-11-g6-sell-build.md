# G6-sell: the sell half of the Toss order path, built behind a closed gate

- **Why:** Thomas decided the G6-sell packet on 2026-11-11, every item as recommended ("기본값대로"), and chose
  to have the code built now with the gate closed. Record: `docs/proposals/G6_SELL_REBALANCE_V0.1.md`.
  - SQ1: only the calculator plan's domestic ETF sell lines, from a scenario Thomas confirms.
  - SQ2: tax-free sells and domestically listed taxable ETF sells. Overseas direct holdings stay manual.
  - SQ3: no same-day buy with sell proceeds.
  - SQ4: 500,000 KRW a day, apart from the buy cap.
  - SQ5: no exemption from H6c. Thomas turns the gate on after H6c is decided.
- **Nothing runs until Thomas sets the value.** With `MVP_TOSS_ORDERS` unset or `buy_only`:
  - no sell adapter is built and `sell_kr` refuses before any POST;
  - no sell draft is written, no notice is sent, and the fire's status line is unchanged;
  - the buy path behaves as before. Its tests pass unmodified.
- **The gate:** `MVP_TOSS_ORDERS=buy_sell`, a second value of the same variable.
  - `buy_sell` builds the buy adapter and a sell adapter. `buy_only` builds the buy adapter only.
  - The sell adapter has its own provider id (`toss_sell_orders`), so a buy authorization opens no sell.
  - Each adapter's authorization names the value it was built under. Changing the value under a running
    process refuses every POST, buys included, until the service is recreated.
  - Each adapter refuses, at the POST, a body whose `side` is not its own. The shared POST moved to a base
    class with no order method.
- **The adapter:** `TossSellOrders.sell_kr` in `holdings/toss_orders.py`. One method: a LIMIT DAY SELL of whole
  domestic shares. There is no US, amount or market sell, and no modify or cancel.
- **The draft:** `holdings/autosell.py`, `draft` and `refresh`.
  - It reads the same plan `--rebalance` shows. `store.rebalance_plan` is now the one place that builds it.
  - A line is kept when its instrument is `toss:domestic:` with whole shares, it is not manual, and its tax
    kind is tax-free or domestic taxable. An overseas holding stays out whatever tax kind Thomas named for it.
  - A line is cut to what the day's cap still has (500,000 KRW less 3% headroom), in the plan's tax order.
    The gain and the estimated tax are recomputed for what is kept.
  - A line never takes a buy slot below its target. Bonds are one class to the plan and two slots to the buy
    draft (Korean 3y and US 7-10y), so without this the sell draft could sell from the slot the buy draft is
    filling. A bond sits in the US slot when Thomas's entry exposes it to USD, as the buy draft counts it.
  - A line under 50,000 KRW is not placed (RB4'). Nothing that is cut is stored.
  - It runs once a month, on the first trading day from the monthly check day (RB5) on. Only a verdict spends
    the month: `drafted`, `nothing_to_sell` or `not_due`. A closed gate, a refused or stale plan, a closed
    market day and a failed read leave the month open.
- **The approval:** `holdings_board --autosell-approve` shows the table (quantity, estimate, tax kind, estimated
  gain and tax) and turns it into a scenario on Thomas's `y` at a terminal.
  - It is checked against the last fire's positions: a 6-character domestic code, a whole quantity no more
    than held, a tax kind this path sells, a total inside the day's cap.
  - A draft older than 4 days is refused. A scenario with an open order is not replaced.
  - `--autosell-status` shows the scenario and its journal.
- **The executor:** `autosell.run`, a step after a holdings refresh that landed.
  - Open orders are read back every fire. An order cancelled at the close leaves its remainder for the next
    trading day.
  - One attempt per KST trading day, inside the regular session from start + 5 min to end − 30 min.
  - Before any order it checks, and any miss holds the whole scenario for the day:
    - every item is an ACTIVE ETF (a miss here stops the scenario);
    - the buy scenario is readable and is not buying the same instrument;
    - the shares held now, read again, cover what the scenario still sells;
    - the price is the highest bid at which the book covers the quantity, and it is not more than 2% under
      the last price;
    - the estimate fits the scenario cap and the day's 500,000 KRW (from the sell journal).
  - The journal gets an `intent` line before each POST and its outcome after. A `clientOrderId` already in
    the journal is never resent. The first rejection or unknown outcome stops the rest.
  - The sell journal is its own file. The buy path reads every row of the buy journal as a buy, for its
    daily cap and for classifying what it bought.
  - A scenario is valid for 7 days. Telegram carries counts and state only.
- **New feed reads:** `TossHoldingsFeed.bids` (the bid side, highest first) and `domestic_quantities`.
- **Governance, in this PR** (the same set G6-buy's PR #1329 changed; nothing versioned was bumped there or here):
  - the env-gate roster's comment names the second call site in `toss_orders.py`. A set of paths cannot see a
    second site, so the capability is pinned by behaviour in `tests/test_mvp_runtime_holdings_autosell.py`;
  - `MVP_TOSS_ORDERS` was already stripped per test and forwarded to scheduler-maint only;
  - CLAUDE.md: Claude never sets either value and never runs the two new modes;
  - the hook covers the two modes and the three files; the guard tests gain seven posix-only cases
    (win32 skip ceiling 715 → 722);
  - the compose comment names `buy_sell`.
- **Consequences worth knowing:**
  - A monthly draft with a 500,000 KRW cap sells at most that much per month. A larger rebalance takes
    several months or manual sells.
  - A band that leaves mid-month waits for the next monthly check.
  - When the plan's only sell line is in a buy slot that is under its target, nothing is drafted.
    `--rebalance` still shows the line.
- **Unverified until the first real sell** (the packet's §6; Toss has no demo server):
  - Toss's rejection codes for a sell, and whether a field gives the sellable quantity. A rejection stops the
    scenario and keeps what Toss said in the journal.
  - Whether the order book answer names its bid side `bids`. An answer without it is an empty side, which
    holds the day (`book_too_thin`).
  - Whether a taxable ETF sale shows its withholding in the fill.
- **Checked:**
  - Adapter: unset and `buy_only` build no sell and post none; the exact sell body; `buy_sell` keeps buys
    working; a value changed under a running adapter is refused at the POST in four directions; each adapter
    posts its own side only; a buy authorization opens no sell.
  - Draft: the cap and the recomputed gain and tax; tax order; overseas, manual and relabelled lines left out;
    the buy-slot rule; RB4'.
  - Month: a draft, a not-due verdict, and the three states that must not spend the month.
  - Approval: once; stale, missing, unbacked and tampered drafts refused; an open order blocks replacement.
  - Executor: the bid price across levels; six holds and the next day; the buy-scenario overlap; the path
    off; five failing reads; both caps; a cancel at the close; a partial fill; rejection, unknown and a
    crash after intent; non-ETF; expiry.
  - Store and scheduler: the closed gate writes nothing; the draft agrees with `load_rebalance`; delivery
    before marking; a failing step does not stop the fire. The terminal approves only on `y`.
  - 54 mutations were each caught.

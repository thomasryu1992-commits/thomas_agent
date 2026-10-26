# The rebalancing candidate report: `scripts.rebalance_candidates` (PR-C of RB1–RB6)

- **Why:** the calculator buys only inside Thomas's approved candidates (RB2', 2026-10-26). This report is the
  shortlist he approves from, once per class, not per rebalance (`REBALANCING_PROPOSAL_V0.1.md` §2.7).
- **What it reads:**
  - Toss's listings of ACTIVE ETFs per market (KOSPI, KOSDAQ, KR_ETC, NYSE, NASDAQ, AMEX);
  - basic stock info (status, leverage factor);
  - 20 daily candles per shortlisted ETF;
  - the account's commission rates and the USD/KRW rate.

  It never reads holdings. The reads are four new GETs on `TossHoldingsFeed` behind the existing gate (no new
  `select_env_gated` site). They are paced under the published per-group limits: STOCK_ALL 1/s, STOCK 5/s,
  MARKET_DATA_CHART 20/s.
- **The shortlists follow the decided exposures** (TOTAL_ASSET_ALLOCATION):
  - US-listed broad index ETFs for global equity (§8.4: overseas direct in the ordinary account);
  - KOSPI 200 ETFs;
  - Korean 3-year government bond ETFs;
  - domestic-listed US Treasury 7–10y ETFs (D-A: a quarter of bonds, unhedged);
  - domestic gold ETFs (Q3).

  Matches are by name. Leverage, inverse and covered-call names are out, and so is any leverage factor other
  than 1.
- **Per row:**
  - the 20-session mean of close × volume in KRW (US at the rate);
  - the account's commission rate;
  - the tax kind from the §2.5 table (the calculator's `tax_kind`);
  - the currency exposure, with "(H)" naming read as hedged and flagged against D-A.
- **What Toss cannot say:** no index, hedge or expense-ratio field exists in `openapi.json` v1.2.24. The report
  says so, and the expense ratio is for Thomas to read off the issuer's page.
- **The example line** is by liquidity alone and is labelled as Thomas's to edit. For bonds it holds three
  Korean 3y and one unhedged US 7–10y: the calculator water-fills approved candidates evenly, so 3 + 1 keeps
  the decided quarter.
- **Boundary:**
  - The output is raw market data. Toss's terms keep it to the investor's own use, and MULTI_ASSET keeps raw
    quotes out of model prompts.
  - It runs in `thomas-scheduler-maint`, the one container with the Toss key, from Thomas's terminal.
  - The hook refuses a Python run that names the script, and CLAUDE.md lists it with the terminal-only modes.
  - It issues its own token, which revokes the lane's, so the next fire reissues once, as `--full` does.
- **Windows skip ceiling** 685 → 689: four new bash-only guard cases.
- **Unverified until Thomas's first run.** There is no demo server. The listing and candle shapes come from
  the official spec, and the name rules have not yet met Toss's real ETF names. An empty or odd shortlist is
  a rule to widen, not a fact about the market.
- **Checked:**
  - Synthetic listings for the pool rules, ranking, tax kinds and FX, inactive or leveraged rows, missing
    candles and the 3 + 1 bond example.
  - The four reads' paths and parameters (the commission read carries the account).
  - Pacing, and the script's refusal where the key is not.
  - Ten mutations were each caught.

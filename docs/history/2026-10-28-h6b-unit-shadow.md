# H6b-shadow: unitized NAV beside the drawdown, recomputed every fire and judged by nothing

- **Why:** H6c will judge drawdown by NAV per unit instead of by a peak that Thomas resets after each deposit. D-H6-9 puts a
  shadow first: the unit path is computed and shown while the old judgement stays in charge, until it has lived
  through real deposits and withdrawals.
  - Thomas: "승인: H6b-shadow S1=A S2=A S3=A S4=A S5=A, 설계 문서·구현·PR (Thomas 2026-10-28)".
  - The record is `docs/proposals/H6B_UNIT_SHADOW_V0.1.md`. It answers the five choices
    `PORTFOLIO_CASH_FLOW_LEDGER_V0.1.md` §4 left open.
- **What changed** (`holdings/unit_shadow.py`, one module where the packet named two):
  - Each fire with a complete portfolio NAV appends a point to `holdings_nav_points.jsonl`. A point holds:
    - the NAV;
    - the USD/KRW rate;
    - each priced wallet asset's USDT price;
    - whether H6 was ready.
  - The file is hash-chained through `chained_log`, keyed by the fire time, and LOCAL_ONLY.
  - The path is recomputed from the points and the cash-flow ledger every fire (S4). So a flow Thomas confirms later
    lands at its own point. The ledger gets no `units_changed` lines; those begin with H6c.
  - **Start (S1):** the first point at which H6 was ready, at 100.
  - **What moves units (S2):** external deposits and withdrawals only.
    - Binance: a post-cutover READY flow, or a held payment Thomas closed as one.
    - Toss: a residual closed as one, by Thomas or by the rule.
    - Income and fees stay in the result. read_incomplete, semantics_mismatch, exclusions, internal transfers and
      exchanges move nothing.
  - **Valuation (S3):** each flow is valued at the prices of the first point after it.
    - USDT, USDC and FDUSD count as one dollar. KRW is itself. Other coins use the wallet's price at that point.
    - A Toss flow uses its residual, not the rule's rounded amount.
    - No price holds the path.
  - **Held:** an open exception after the start, or an unvalued flow, stops the path at the point before it.
- **The wallet** (`WalletSnapshot.asset_price_usdt`): it now carries each priced asset's USDT price, in process,
  beside `asset_usdt`. The NAV-point log is the only place it is kept.
- **Shown (S5):**
  - **Board:** one `unit shadow (H6b)` line. It shows NAV/unit, the drawdown from the unit peak, days, deposits and
    withdrawals applied, any hold, and whether H6c may be asked.
  - **Claude door:** `holdings_status` contract v1.2 adds `unit_shadow`. It carries the state, days, the
    deposit/withdrawal/held counts and `h6c_askable`. It never carries NAV/unit or the drawdown.
  - **Telegram:** nothing.
  - **`h6c_askable`:** 14 days, at least one deposit and one withdrawal, and nothing held.
- **Boundaries widened on purpose:**
  - `unit_shadow.py` joins the modules that may read the ledger (`DOOR_ALLOWED`). It is no door.
  - The snapshot gains the `unit_shadow` block, which carries no amount.
  - The LOCAL_ONLY guard names the new file.
  - A test pins that only `unit_shadow.py` and `store.py` name the point log or import the module.
- **Not changed:**
  - `combined`'s drawdown, the peak file, `--reset-peak`, the drawdown alert and every other notice.
  - The ledger, its readiness and the quiet-window rule.
  - A failure of the shadow costs only its own block, which then reads `broken` with the reason code.
- **Checked:**
  - **Rule tests:** 37 tests in `test_mvp_runtime_holdings_unit_shadow.py`. Each S1–S5 rule has a numeric case:
    - deposit and withdrawal neutrality, each flow on its own;
    - income and fee as result;
    - Binance flows: READY, confirmed, observed only, excluded, and held behind a moved cutover;
    - valuation edges;
    - hold, then a confirmation landing at its point;
    - the unit peak after a fall;
    - the H6c conditions;
    - a rule-closed Toss deposit read from a real ledger;
    - the chain and its tamper;
    - door isolation;
    - the door's counts without the unit value;
    - the board line.
  - **Store tests:** a point is written only for a complete NAV, the first ready fire starts the path at 100, and a
    shadow failure costs only its block.
  - **Mutations:** 26 mutations across the module, the store, the door, the wallet and the board, each caught.

# Multi-asset expansion drafted: asset management before alpha, options last, four decisions open

- **What this PR adds:** `docs/proposals/MULTI_ASSET_EXPANSION_V0.1.md` (DRAFT) and `STATUS.md`
  regenerated. No code, no schema, no constant.
- **Why it is shaped this way:** the request (Thomas 2026-10-02) to grow the crypto-futures lane into
  spot, equities and options holds two different things. Asset management (holdings, allocation,
  account-wide limits) needs no edge, and holding assets with a different beta is one of the few ways to
  raise the ~9 independent bets `PORTFOLIO_INDEPENDENCE_V0.1.md` measured. Alpha expansion needs the
  edge that `CRYPTO_STRATEGY_EDGE_ORDER_V0.1.md` scores at 2/10, so it waits for the research
  pause to end (cohort 1's close, 2027-03-22).
- **Delta hedging:** the draft answers the question whether a delta-hedged options book gives
  "stable returns and a higher floor". It does not. Short gamma earns a steady premium and lowers the
  floor, while long gamma or a protective put raises the floor at the cost of the premium. The repo
  also already records the blockers: there is a single-leg position model (`TRADING_ALPHA_RESEARCH_RECORD.md`), the
  multi-leg R accounting that excluded spreads from equity-perp v1, judgement built on entry/stop R,
  and roughly ten independent market periods.
- **Correction carried in the draft:** the chat advice to put new assets in a sibling package
  contradicted `EQUITY_PERP_LANE_V0.1.md` §1 (`stocks/` rejected 2026-08-03). New venues land behind the
  three `select_*` seams, and `live_trading_budget`'s `venue` enum stays the structural gate.
- **Web survey (2026-10-02, public sources, unverified by account):** KIS Developers and Kiwoom REST
  offer API orders for Korean and US equities to Korean residents. IBKR excludes Korean residents from
  KRX access. Alpaca's support for Korea is unconfirmed.
- **Open at drafting:** D1 (what comes first), D2 (what options are for), D3 (regulatory records, appendix-A
  form), D4 (whether a read-only multi-account board falls under the review-D3 pause).
- **D1 decided in the same PR (Thomas 2026-10-02):** asset management first, as recommended. The status
  line moves from DRAFT to PARTIALLY DECIDED, and §6 records what changed (the §5 order is fixed, so P1
  and P2 come before P3) and what did not (no code, and P1 still waits on D3, D4 and the account-read
  env opt-in).
- **D4 decided in the same PR (Thomas 2026-10-02):** the read-only multi-account board (P1) may be built
  during the research pause. The allowance holds only while the board has no order path, changes no
  judgement and is read by no door. P2 (limits) is outside it. `CLAUDE.md` names the board in the pause's
  exempt list, because the existing "measurement and display" exemption was written for `crypto/` and the
  board needs a new account-read capability. The same commit corrects the draft's pause end from "first
  forward verdict" to cohort 1's close on 2027-03-22, the scope restated on 2026-10-01.

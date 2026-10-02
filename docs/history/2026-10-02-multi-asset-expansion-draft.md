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
- **D3 scoped in the same PR (Thomas 2026-10-02):** the first regulatory record is Korea Investment &
  Securities (KIS Developers), for P1's account reads only. Appendix A drafts it in the equity-perp
  appendix-A form. It leaves three places for Thomas (the judgement, its strength, and the terms-of-use
  check) and keeps orders out of scope, so opening them is a new judgement rather than a promotion. If
  KIS keys carry no read-only scope, read-only is enforced in code on the #1086 pattern (a reader with
  no place or cancel). Until those three places are filled, no P1 code starts.
- **D2 decided in the same PR (Thomas 2026-10-02):** options are for return, adopted only when they are
  more capital-efficient than the alternatives. They are an option, not a lane. The draft recommended
  deferral and the decision is wider, but what gets built and when does not move: options stay P4, last.
  §6 records a derived, unratified comparison standard, because "efficiency" is where short gamma wins by
  construction. Four rules: measure against worst loss and not margin; compare with the next-best use of
  the same capital and not zero; use the same holdout/forward evidence bar; compare net of costs. The
  comparison cannot run until P4's multi-leg representation and option judgement rules exist. Before
  then, the only possible step is a read-only implied-vs-realised volatility measurement.
- **Appendix A terms check 1-2 read from the source (2026-10-02):** the KIS customer terms (enacted
  2022-08-08) allow automated self-use of one's own account around the clock, under rate limiting. Load
  "above a certain level" can suspend or terminate access (arts. 9, 10, 12). The terms define no
  read-only scope: one app key and secret cover both reads and orders (arts. 3, 7). So read-only is
  enforced in code unless the application screen offers a scope. The check also found two conditions the
  check list lacked. Quotes may not be given to third parties (art. 5(3)), so KIS quotes do not go to
  hosted model prompts or outside channels. Keys may not be lent or delegated (art. 5(2)).
- **External-send boundary decided (Thomas 2026-10-02):** art. 5(3) permits personal use and bars
  redistribution. Balances, holdings, valuation and weights may go to the board and to Telegram, which
  only Thomas receives. Hosted LLM prompts get aggregates only. Raw KIS quotes never enter a prompt,
  because free tiers often log or train on input. Nothing goes to public channels or other people. P1's
  tests are to show that only aggregate KIS-derived fields reach prompt assembly. The boundary yields to
  an answer from KIS if one is obtained.

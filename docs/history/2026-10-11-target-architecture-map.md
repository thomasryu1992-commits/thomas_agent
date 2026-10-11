# A target-architecture map: seven layers, the evidence gates between them, and what stays Thomas's to approve

- **What this PR adds:** `docs/proposals/THOMAS_AGENT_TARGET_ARCHITECTURE_V0.1.md` (DRAFT).
  - It records Thomas's end goal (2026-10-11): autonomous investing inside the strategies, asset classes, risk limits
    and execution rights he has approved.
  - It records the eight things that stay his explicit approval.
  - It places the seven layers on the code and documents that own them today. Each layer has four columns: owner,
    built, not built, and what blocks the next step.
  - It adds a roadmap by asset class with entry evidence and approver, and evidence gates G1–G7.
  - It lists six open questions, D-A1–D-A6.
- **Why a map and not a plan:**
  - The pieces existed in five owner documents: the execution stage ladder, the multi-asset P0–P4 lanes, the target
    allocation, H6 and C7.
  - Nothing showed how they fit together.
  - The map creates no authority. Where it and an owner disagree, the owner is right.
- **What it makes explicit:**
  - Mechanism and evidence are kept apart. The crypto-futures autonomous order machinery is built and placed real
    orders in August 2026, before the stage ladder (2026-09-15). The host entered the ladder at PAPER. Trading is
    zero because of three things: stage PAPER, the live gate closed on 2026-10-07, and no FORWARD_CONFIRMED
    strategy (0 armed of 15). It is not because a piece is missing.
  - The two fixed dates are 2027-03-22 (cohort 1 close, P3's earliest review) and about 2027-04-01. The second is C7
    D2 option A's history-retention limit, not a target date.
- **Open questions it surfaces:**
  - D-A1: the vision's portfolio-wide risk limit gating orders would reverse P2's alert-only decision.
  - D-A2: Q2 forbids inheriting crypto authority, yet the stage ledger is the crypto lane's.
  - D-A3: the LIVE_SCALED entry rule.
  - D-A4: where crypto-spot orders belong.
  - D-A5: C7 D2.
  - D-A6: whether to build the IV–RV measurement.
- **What it deliberately does not do:**
  - It changes no runtime, schema, policy, constant, stage, gate, budget or limit.
  - It adds no rung and does not reopen the canary, `stocks/`, the P3 gate or the research pause.

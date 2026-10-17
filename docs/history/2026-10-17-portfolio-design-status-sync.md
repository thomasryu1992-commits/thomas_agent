# Portfolio design documents: status lines brought back in line with what is built

**What changed** (documents only).

A read-only review on 2026-10-17 found five portfolio documents still describing as unbuilt what main
already holds. Thomas asked for them to be unified on 2026-10-17 ("문서를 통일하고"). That was a
direction, not a one-line approval: its target and scope were the ones proposed just before. Merge is
not included.

- **`PORTFOLIO_CASH_FLOW_LEDGER_V0.1.md`.** The status said the build starts at H6a. In fact H6a
  (#1193), H6b (#1194–#1196) and H6d-min (#1205, #1208, #1210) are built, and H6b-shadow and H6c
  remain. The earlier line is kept and marked as earlier.
- **`HOLDINGS_LEDGER_CHECKPOINT_V0.1.md`.**
  - The status now lists the built PRs: PR-0, PR-D and PR-A, none deployed.
  - A new §12.4 holds the 2026-10-17 PR-A decisions verbatim (R-2, R-3, no deploy before PR-B), with
    the approval basis as it stands. Until now these decisions lived only in a history file and a PR
    description.
  - §12.4 also holds the one open decision: readiness PASS beside an UNVERIFIED checkpoint.
- **`HOLDINGS_C7_PR_D_PR_A_READINESS_V0.1.md`.** Moves from DRAFT to IMPLEMENTED: both PRs it
  prepared are built, and both of its DECISION_REQUIRED items are decided. Its original status is
  kept as a historical line.
- **`PORTFOLIO_AND_STOCK_TRADING_PLAN_AUDIT_V0.1.md`** and
  **`THOMAS_AGENT_TARGET_ARCHITECTURE_V0.1.md`.** "C7 is not implemented" is corrected with a dated
  note. The C7 rows of the map name the merged PRs and what remains.
- `docs/proposals/STATUS.md` was regenerated from the status lines.

**Not changed.** No decision was made or reworded. Three things stay as they were:
- `docs/REMAINING_WORK.md` has no portfolio section; adding one is Thomas's separate call.
- The map's "global LOCAL_ONLY Guard: PENDING" row is not edited, though the PR-D readiness packet
  records it verified on 2026-10-12. It is outside the agreed scope, and a fact to confirm.
- No code.

# The previous epoch's observed buy, sell and settlement day can be carried into the current one, once

- **Why:**
  - Thomas started Toss semantics epoch 1 on #1398 (the settlement day read off the transaction list). The
    digest was `40748837…`.
  - A new epoch gathers its own evidence (H6d-min D4), so H6 asked for a new buy, a new sell and that sell's
    settlement day.
  - Epoch 0 had already seen all three. The 2026-10-27/28 buys and the 2026-11-03 sell were explained. The
    2026-11-05 settlement day had 24 windows with no residual. Its windows could not name the day, because the
    order list's `settlementDate` is null live.
  - Thomas asked why the sell was needed, then chose to accept the 11-03 observation: "11-03 관찰 인정". The
    scope covers all three. Code checks them. Only Thomas's terminal can run it. The record is
    `PORTFOLIO_CASH_FLOW_LEDGER_V0.1.md` §11.
- **What changed:**
  - `cash_flows.carry_candidate(rows, transactions, today_kst)` is pure. It refuses with `TOSS_CARRY_REFUSED`
    unless:
    - an epoch ≥ 1 exists and has not carried yet;
    - the previous epoch's own evidence explains at least one buy and one sell;
    - the transaction list names a settled, non-cancelled KRW/USD sell before today;
    - the previous epoch has at least 12 windows on that day, all counted after its last problem, clean in both
      pockets, unamended and read whole.
  - `cash_flows.carry_evidence(...)` re-checks the same conditions under the locks. It writes one
    `toss_evidence_carried` line per epoch, with the counts, the day, the pocket, the window count, a `tx:` hash,
    who, why and when. It needs an interactive terminal, `--by`, `--reason` and the day typed back.
  - `toss_evidence` counts a carried line for its epoch like any evidence. A problem later in the epoch voids it.
    The return shape is unchanged, so the reconciliation digest is unchanged. Carrying evidence is no
    reconciliation change.
  - `holdings_board --carry-evidence` reads the transaction list's sells (a Toss GET), prints what it would
    carry, and asks for the day.
  - The flag joins the guard hook's terminal-only list and CLAUDE.md's never-run list.
- **Shaped this way on purpose:**
  - **Checked, not declared.** The day comes from the venue's own transaction list, and the windows that saw it
    are re-read. A declaration alone would carry what nobody checked.
  - **Once, and voidable.** One line per epoch, and the same "problem voids earlier evidence" rule applies to
    it.
  - **What stays unverified.** #1398's live settlement path has not yet seen a real sale. It will at the next
    sell.

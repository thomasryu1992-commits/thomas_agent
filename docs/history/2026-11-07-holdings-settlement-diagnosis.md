# holdings_status v1.3: why the Toss settlement day is not clean, as counts and closed reasons

- **Why:** on 2026-11-07 the Claude door still showed `toss_needs: settlement_day`, two days after the
  2026-11-03 sell settled (2026-11-05). The door said *that* the day was missing, never *why*.
  - H6 readiness gates the unit shadow, H6c and G6-sell, so the stall holds up the whole holdings path.
  - Thomas: "진단 추가 승인" (2026-11-07).
- **What changed:**
  - `cash_flows.settlement_diagnosis` gives, per KST day over the last 14 days:
    - the windows that ended on that day;
    - the counted windows that recorded a settlement for that day;
    - whether the day is clean;
    - closed reason codes for whatever held it back.
  - It also gives the semantics epoch, which judge decides the check, settlement entries, explained sells and
    clean-day counts.
  - `readiness` carries the diagnosis. The snapshot keeps it under `cash_flows.readiness.toss_diagnosis`.
  - `holdings_status` v1.3 projects it as `h6.settlement` (contract: `HOLDINGS_STATUS_READ_AND_AUTO_FLOWS_V0.1.md`
    §2.1b).
- **Shaped this way on purpose:**
  - **`toss_evidence` is mirrored, not extended.** Its output is hashed into `reconciliation_digest`, and a changed
    digest is a semantics-epoch transition. `clean` is read off `toss_evidence` itself, so the two cannot
    disagree.
  - **The diagnosis never costs the verdict.** A defect in it leaves it out of the readiness and the snapshot. The
    checks and the readiness alert run as before. A test pins this.
  - **Epoch first.** In epoch 0 the frozen pre-H6d counters judge the check, and new windows can only make it
    MIXED. A clean day in the windows would then turn `toss_needs` into `review`, never into a PASS. So
    `judged_by` sits beside the per-day rows. If it reads `legacy_counters`, the fix is Thomas's
    `--semantics-epoch`, not code.
  - **A format mismatch has a signature:** the sell is counted (`sells_explained` ≥ 1), yet `settlement_entries`
    is 0. A test pins three other date shapes.
- **What does not leave:** order hashes, window times, residual values, amounts. A KST date with "a residual that
  day" is the exposure §2.2 already accepted for exception change times.

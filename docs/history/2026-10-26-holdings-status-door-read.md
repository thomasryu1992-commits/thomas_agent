# The Claude door answers `holdings_status`: freshness and H6 check states, no amounts

- **Why:** since C6b Claude cannot run docker, so every H6 readiness question went through Thomas's terminal.
  Thomas decided R1–R3 of `HOLDINGS_STATUS_READ_AND_AUTO_FLOWS_V0.1.md` on 2026-10-26.
- **What:**
  - `cash_flows.toss_needs` turns what the Toss check still waits for into a closed list: `buy`, `sell`,
    `settlement_day`, and `review` while MIXED. Readiness and the stored snapshot carry it.
  - `cash_flows.READINESS_CHECKS` / `READINESS_STATUSES` close the twelve checks and five values. A test fails
    if readiness grows a check outside them.
  - `holdings.store.load_snapshot_status` reads the snapshot's freshness and `cash_flows` without rendering the
    board. Rendering a malformed block raised `TypeError` in the first test run, so the door no longer depends
    on it.
  - `store_reads.read_holdings_status` builds contract v1.1 from an allowlist: freshness, then
    `h6{ready, checks, open_exceptions, shadow_days, shadow_success_dates, toss_needs}`, checked against a
    closed schema.
    - An unknown check name or state is dropped or turned into `UNKNOWN`, and the reply is marked `v1-partial`.
    - An unreadable snapshot is refused as `READ_UNAVAILABLE`, without its text.
  - The Claude profile serves `holdings_status`. It left `_PROFILE_HELD_READS`, and `scripts/claude_read.py`
    accepts it.
  - CLAUDE.md's holdings rule now names the two outward surfaces. The list of forbidden commands is unchanged.
- **Boundaries kept:**
  - The door imports `holdings.store`, inside the read, never `cash_flows`. Both
    `test_no_door_module_reads_the_ledger` and the domain isolation rule hold.
  - `store_reads.py` is recorded as the third holdings dispatch site, with the reason beside the pin.
- **Checked:**
  - A leak injection (amounts, weights, account, notes, exception keys, `next_step`, `toss_note`, an unknown
    check, an unknown need) leaves none of them in the reply.
  - The schema refuses an extra field.
  - The door answers through the contract.
  - Four mutations were each caught: forwarding `next_step`, forwarding unknown checks, forwarding unknown needs,
    and answering raw.

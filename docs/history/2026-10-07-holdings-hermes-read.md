# The assistant reads the holdings board: policy 1.6.2 lists holdings_status, shim 2.16 adds the tool

- **The decision (Thomas 2026-10-07):** "Hermes에서도 보유 현황 볼 수 있게 켜줘."
- **What this PR does:**
  - It writes and applies the 1.6.1 → 1.6.2 bump (`scripts/ops/policy_bump_1_6_2.py`,
    `docs/runtime-contracts/POLICY_1_6_2_DRAFT.md`). `control_channel.assistant_read.verbs` gains
    `holdings_status`, which wakes the read the door had carried dormant since #1116.
  - It adds the Hermes tool `holdings_status()` (shim 2.16) and its rule in `SOUL.md`.
- **Why it is safe to hand a hosted model:** the snapshot the door renders holds `board.aggregate_view`
  and three stamps, so no symbol, per-symbol number or exchange rate exists in what the assistant can
  read. That is appendix B's boundary, where totals may reach a prompt and quotes may not.
- **How it was produced:** the 1.6.1 script's procedure, mechanically.
  - The pre-apply tests (`tests/test_policy_bump_1_6_2.py`) ran green at the 1.6.1 baseline.
  - `--check` was READY against the deployed approval store, with nothing PENDING or unspent APPROVED.
  - After `--apply`, both validators PASS.
  - Because the script and its apply share one PR here, its baseline tests early-return from now on,
    the same state the 1.6.1 tests are in.
- **What follows the deploy:** a REBIND of the execution stage, because the stage binds the policy
  version. The host is at PAPER, where no door creates venue exposure, so the READ_ONLY gap refuses
  nothing the current rung allows. Then shim 2.16 and `SOUL.md` are installed on the Hermes host.

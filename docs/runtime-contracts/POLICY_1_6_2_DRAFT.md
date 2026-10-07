# Governance policy 1.6.2 — the assistant's holdings read

**Status:** IMPLEMENTED — written and applied 2026-10-07 on Thomas's decision ("Hermes에서도 보유 현황 볼 수 있게
켜줘"), in the same PR as `scripts/ops/policy_bump_1_6_2.py`. The read answers once the image carrying this
policy is deployed; the REBIND in §4 follows.
**Owner:** Thomas.
**Authority:** None. The committed policy, `runtime/mvp_runtime/read_bridge.py` and the tests named below are
the authority for what the runtime does.
**Raised:** 2026-10-07 — `docs/proposals/MULTI_ASSET_EXPANSION_V0.1.md` P1. The read door has carried
`holdings_status` dormant since #1116 (`read_bridge.POLICY_GATED_READS`), and the assistant reads only
through that door, whose verbs are closed in the policy.

## 1. What changes

| Where | 1.6.1 | 1.6.2 |
|---|---|---|
| `control_channel.assistant_read.verbs` | 14 reads | **+ `holdings_status`** |
| header | 1.6.1 | 1.6.2 |

Nothing else moves: no disposition, scope, TTL, gate, kill list, risk class or other clause. The clause keeps
`mutation_allowed: false` and `gate_grants_authority: false`. `assistant_read` is not a safety section
(`policy_fingerprint.SAFETY_SECTIONS`), so the safety fingerprint does not change; the version does (§4).

## 2. What the grant switches on

- **The read door's `holdings_status`** renders `holdings.store.load_holdings_view`: the brokerage account
  (Toss Securities since 2026-10-07) as an aggregate — domestic and overseas stock in KRW, KRW and USD cash
  buying power, unrealized P&L, a holding count, the weights and the snapshot's age — read from the file
  scheduler-maint's `holdings_refresh` fire writes hourly. It opens no socket and holds no broker key.
- **What it can never carry.** The stored file holds `board.aggregate_view` and three stamps, nothing else, so
  no symbol, name, per-symbol number or exchange rate reaches the door. That is appendix B's external-send
  boundary (the broker's data may serve only the investor's own trading purpose), and it matters here because
  the assistant's model runs at a hosted provider: totals may reach a prompt, quotes may not.
- **The Hermes tool** is `holdings_status()` (shim 2.16, `integrations/hermes/mcp/read_bridge_mcp.py`), and
  `SOUL.md` tells the assistant to call it only when asked about assets, to say the `as of` time, and to answer
  "the board has totals only" rather than invent a stock.

Tests: `tests/test_mvp_runtime_holdings.py` (the store and the boundary), `tests/hermes_shims/test_shim_rendering.py`
(the tool), `tests/test_policy_assistant_read_clause.py` (the clause and the door), `tests/test_policy_bump_1_6_2.py`
(this bump, at its baseline) and `tests/test_policy_holdings_read_grant.py` (written by the bump).

## 3. How it was applied

```bash
python scripts/ops/policy_bump_1_6_2.py --check --state-root /root/thomas_agent   # READY: zero PENDING / unspent APPROVED
python scripts/ops/policy_bump_1_6_2.py --apply --state-root /root/thomas_agent
python scripts/validate_permission_approval_contracts.py && python scripts/validate_i0_5_read_only_runtime.py
```

Then: deploy by the candidate-tag procedure (`CLAUDE.md`, "Deploying"); REBIND the execution stage (§4); on
the Hermes side install shim 2.16 (`scripts/ops/install_hermes_shims.sh`) and copy `SOUL.md`, as for 2.15.

## 4. What it costs: one REBIND, and nothing PAPER allows

The execution stage binds the policy version (`docs/runtime-contracts/EXECUTION_STAGE_V0.1.md` §4), so after the
deploy the stage reads READ_ONLY until Thomas approves a REBIND of the same rung. On 2026-10-07 the host is at
**PAPER** (`register_execution_stage --show`), where no door creates venue exposure — the autonomous leg and the
slippage probe need LIVE_AUTONOMOUS and are refused either way — so the gap costs no entry the current rung
allows. Closing is never gated by the stage. No other policy draft is pending, so this is one bump, one REBIND.

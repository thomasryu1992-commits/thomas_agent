# Governance policy 1.5.1 — draft for Thomas to apply (Trading Soft Halt grant)

**Status:** IMPLEMENTED — applied 2026-09-17 by Thomas with `scripts/ops/policy_bump_1_5_1.py --apply`
(decision Q2), at zero live PENDING and zero unspent APPROVED approvals;
`governance/GOVERNANCE_POLICY.yaml` is at **1.5.1**. The services read the policy from the image, so
`console_cli halt_trading`, Telegram `/halt_trading` and the switch door's `disable mode=soft` act once
the image that carries 1.5.1 is deployed. An older image refuses them as `CONTROL_VERB_NOT_GRANTED`.
The sections below are the draft as applied.
**Owner:** Thomas.
**Authority:** None. The committed policy, `runtime/mvp_runtime/control.py` and the tests named
below are the authority for what the runtime does.
**Raised:** 2026-09-15 — Thomas decision 7 ("kill/pause: 기존 의미를 억지로 변경하지 말고 Trading Soft
Halt 신설"), after the execution-authority audit verified that a KILLED/PAUSED runtime stops managing
open live positions (`/root/thomas_crypto_refactor_2026-09-15/pr1-execution-authority-audit.md`, FC-1).

## 1. What changes

| Where | 1.5.0 | 1.5.1 |
|---|---|---|
| `control_channel.local_operator_console.emergency_controls_allowed` | pause, stop_task, kill, status, audit, recovery, resume | **+ `halt_trading`** |
| `kill_switch.commands` | /pause, /stop, /kill, /resume | **+ `/halt_trading`** (not added to `kill_blocks`: it is not a kill) |
| `p5_policy_gate` comment | "`console_cli kill` … deliberately exempted by the close path — so it stops new entries without trapping a position" | corrected: a KILLED runtime never reaches the close path (`kill_blocks: scheduler_execution`); the halt that keeps positions managed is `halt_trading` |
| live-execution comment | "The runtime stop is `console_cli kill`." | "The runtime stop that keeps positions managed is `console_cli halt_trading`; kill stops management too." |
| `assistant_switch.verbs.disable` comment | kill\|pause | kill\|pause\|soft |
| header | 1.5.0 | 1.5.1 |

Nothing else moves: no `kill_blocks` / `kill_allows` change, no scope, disposition, TTL or gate.

## 2. What the grant switches on (the code, already merged)

- **Soft halt on an ACTIVE runtime:** `trading_armed=False`, mode stays ACTIVE. New live entries
  (autonomous and probe; the canary door was removed 2026-09-15) are refused; settlement, the
  protection re-check, the time exit, reconciliation, paper and the watches keep running. No
  approval (a stop must be cheap).
- **From PAUSED/KILLED:** the authenticated operator (host console, verified Telegram) moves the
  runtime straight to the soft halt, with no moment in which entries are armed. The assistant's
  switch door (`disable mode=soft`) cannot release a stop this way.
- **Re-arm:** unchanged — `/resume` (authenticated operator), or an approved `enable scope=trading` on
  the switch door, whose ask now says it re-arms live entries.
- `/pause` and `/kill` keep their meaning.
- **Not yet on the assistant's side:** the switch door accepts `mode=soft`, but the Hermes shim
  (`switch_bridge_mcp.py`) exposes only `stop_trading` (kill) and `pause_trading` (pause). A soft-stop
  tool, its render and the SOUL/skill wording are a follow-up after this grant is applied.
  (Since shim 2.13, crypto PR6e-1: `halt_trading(reason, hard=False)` and its SOUL wording; the skill's
  wording rides the next skill version.)
- **Telegram during a long analysis:** `/halt_trading` is not handled by the operator loop's mid-run
  peek (it could hide a later `/kill`, and from a stop it releases one), so it lands when the analysis
  ends. For an immediate entries-only halt then, use `console_cli halt_trading`; over Telegram, `/kill`.

Tests: `tests/test_mvp_runtime_control.py` (soft halt section), `tests/test_mvp_runtime_switch_bridge.py`
(soft stop), `tests/test_mvp_runtime_crypto_live_route.py::test_a_soft_halted_runtime_manages_open_positions_and_refuses_entries`,
`tests/test_policy_bump_1_5_1.py` (this draft, before it is applied), and after apply
`tests/test_policy_trading_soft_halt_grant.py` (written by the bump).

## 3. How to apply

```bash
cd /root/thomas_agent   # a clean main checkout
python scripts/ops/policy_bump_1_5_1.py --check --state-root /root/thomas_agent
python scripts/ops/policy_bump_1_5_1.py --apply --state-root /root/thomas_agent
python scripts/validate_permission_approval_contracts.py && python scripts/validate_i0_5_read_only_runtime.py
```

Then a PR with the result, merged through the required checks, and the image deployed by the
candidate-tag procedure — the services read the policy from the image, so the verb acts only after
that deploy. Rehearsed 2026-09-15 on a copy: apply → both validators PASS → pin test and control
tests pass; `policy_bump_1_6_0.py --check` is READY over the 1.5.1 baseline and its apply validates too.

**Order with 1.6.0 (schedule delegation):** apply 1.5.1 first. `policy_bump_1_6_0.py` accepts either
baseline; this script refuses once 1.6.0 is applied (the grant would then need a 1.6.1 script).
A version bump makes every outstanding PENDING/APPROVED record bound to 1.5.0 unspendable, which is
why both scripts refuse at non-zero PENDING.

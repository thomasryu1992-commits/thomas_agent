# Governance policy 1.5.2 — draft for Thomas to apply (the assistant's emergency-close ask)

**Status:** IMPLEMENTED — applied 2026-09-21 with `scripts/ops/policy_bump_1_5_2.py --apply` on Thomas's
explicit instruction (decision Q2 as amended for 1.5.0: "네가 해줄래"), at zero live PENDING and zero
unspent APPROVED approvals (read-only check of the production store), and followed in the same image by
1.6.0, so one REBIND covers both. The sections below are the draft as applied.
**Owner:** Thomas.
**Authority:** None. The committed policy, `runtime/mvp_runtime/switch_bridge.py` and the tests named
below are the authority for what the runtime does.
**Raised:** 2026-09-19 — Thomas decision 49 ("EMERGENCY_CLOSE … 어시스턴트는 요청만"), after the
emergency close shipped for the operator (crypto PR6c, #913) and the assistant's door verb was left
out because the door's verbs are closed in the policy.

## 1. What changes

| Where | 1.5.1 | 1.5.2 |
|---|---|---|
| `control_channel.assistant_switch.verbs` | status, disable, enable | **+ `emergency_close: approval_required_always`** |
| `assistant_switch.verbs.disable` comment | kill\|pause\|soft | kill\|pause\|soft\|hard (decision 47; the door has carried `hard` since PR6a) |
| header | 1.5.1 | 1.5.2 |

Nothing else moves: no scope, disposition, TTL, gate, kill list or risk class. The ask the verb mints is
the one `scripts.emergency_close --request` already mints (RUNTIME_GOVERNANCE, RED, 15 minutes).

## 2. What the grant switches on (the code, merged with PR6e)

- **The switch door's `emergency_close` verb** mints the emergency-close ask with the assistant as the
  requester. The ask covers every booked live position, closed at market and reduceOnly, and is bound to
  the HARD halt in effect (its `stop_ref`) and to the book. The ask refuses, by the operator's codes,
  without the HARD halt with the runtime ACTIVE, with nothing booked, or with an incomplete book record.
  A retried `request_id` answers from the record instead of minting a second ask.
- **It never spends.** An `approval_id` beside it is refused (`ARGUMENT_NOT_ACCEPTED`). Thomas approves
  on the control channel, where the ask is announced and never mirrored (PR6c). The operator spends it
  once in the scheduler container: `scripts.emergency_close --confirm`.
- **The Hermes tool** is `request_emergency_close(reason)` (shim 2.14). Its reply says nothing was
  closed and names the two steps that follow, neither of which is the assistant's.
- Until this grant is applied and deployed, the verb refuses as `CONTROL_VERB_NOT_GRANTED` and asks
  nothing. The other verbs never read the policy, so a policy that cannot be read can never take
  `disable` away.

Tests: `tests/test_mvp_runtime_switch_emergency_close_request.py`,
`tests/hermes_shims/test_shim_rendering.py` (the ask's text), `tests/test_policy_bump_1_5_2.py` (this
draft, before it is applied), and after apply `tests/test_policy_emergency_close_request_grant.py`
(written by the bump).

## 3. How to apply

```bash
cd /root/thomas_agent   # a clean main checkout
python scripts/ops/policy_bump_1_5_2.py --check --state-root /root/thomas_agent
python scripts/ops/policy_bump_1_5_2.py --apply --state-root /root/thomas_agent
python scripts/validate_permission_approval_contracts.py && python scripts/validate_i0_5_read_only_runtime.py
```

Then open a PR with the result, merge it through the required checks, and deploy the image by the
candidate-tag procedure. The services read the policy from the image, so the verb acts only after that
deploy.

**Then a REBIND.** `assistant_switch` is one of the policy's safety sections
(`policy_fingerprint.SAFETY_SECTIONS`), and the version moves too. After the deploy the execution stage
reads READ_ONLY until Thomas approves a REBIND of the same rung (`EXECUTION_STAGE_V0.1.md` §4). The stage
binds the policy version as well as the safety fingerprint, so every deploy that moves the version costs
one REBIND. To pay it once, ship 1.5.2 and 1.6.0 in one image: apply both, in the order below, before a
single deploy.

**Order with 1.6.0 (schedule delegation):** apply 1.5.2 first. `policy_bump_1_6_0.py` accepts the 1.5.0,
1.5.1 or 1.5.2 baseline, and every anchor it edits survives 1.5.2's text. This script refuses once 1.6.0
is applied; the grant would then need a 1.6.1 script. A version bump makes every outstanding
PENDING or APPROVED record bound to 1.5.1 unspendable, which is why both scripts refuse while any
approval is PENDING.

Rehearsed 2026-09-19 on a throwaway copy: the results are in the PR that adds this draft.

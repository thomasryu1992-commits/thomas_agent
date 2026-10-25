# Governance policy 1.7.0 — the assistant's doors leave the policy

**Status:** IMPLEMENTED — written and applied 2026-10-25 on Thomas's decision (Hermes is retired; "PR2 진행해.
assistant_read 유지, stop_ref는 control로 이동, 정책 1.7.0 범위대로"), in the same PR as
`scripts/ops/policy_bump_1_7_0.py`. It takes effect when the image carrying it is deployed; the REBIND in §3
follows.
**Owner:** Thomas.
**Authority:** None. The committed policy and the tests named below are the authority for what the runtime does.

## 1. What changes

| Where | 1.6.2 | 1.7.0 |
|---|---|---|
| `authority.assistant_dispatch_gate` | the dispatch door's gate | **removed** |
| `control_channel.assistant_dispatch` | the dispatch lane, its allowlist and spend alert | **removed** |
| `control_channel.assistant_switch` | the switch door's verbs (a safety section) | **removed** |
| `control_channel.approval_notification_mirror` | switch-door asks copied to the assistant's window | **removed** |
| `control_channel.assistant_schedule` | the dispatch door's delegated schedule changes | **removed** |
| `control_channel.assistant_read` | the read lane | **kept, unchanged** (lead comment rewritten) |
| header | 1.6.2 | 1.7.0 |

Nothing else moves: no disposition, scope, TTL, kill list, risk class or other clause.

`assistant_read` stays because Claude's read door (`claude-read-bridge`, the same `read_bridge.py` under the
`claude` profile) serves its policy-gated reads under it (`control.granted_read_verbs`). Renaming it would
move a pin for no gain.

## 2. What the runtime stops reading

- `control.granted_switch_verbs` and `SWITCH_VERB_DISPOSITIONS` are gone with the switch door.
- `policy_fingerprint.SAFETY_SECTIONS` no longer names `assistant_switch`.
- `schedule_delegation.load_delegation` finds no `assistant_schedule`. It reads that as "delegates nothing",
  which is the posture it already had before 1.6.0. The container CLI path (`bounded=False`, Thomas's
  console) never read the clause.

`tests/test_policy_assistant_doors_retired.py` pins the absences, and that `assistant_read` still grants
`holdings_status` and `lane_digest`.

## 3. Applying it, and the REBIND

The bump was applied in the PR against an empty approval store. The zero-outstanding check therefore says
nothing about the host. **Before the deploy**, Thomas confirms on the host that no approval is PENDING or
APPROVED-unspent:

    docker exec thomas-scheduler python -c "from runtime.mvp_runtime import approval; from runtime.mvp_runtime.approval_store import ApprovalStore; from runtime.mvp_runtime.timeutil import utc_now_iso; s=ApprovalStore.default(); n=utc_now_iso(); print([a['approval_id'] for a in s.current().values() if a.get('status') in (approval.STATUS_PENDING, approval.STATUS_APPROVED) and not approval.is_expired(a, now=n)])"

That is the bump script's own `_outstanding_live` predicate, read-only. `[]` means zero outstanding.
`approval_cli list` alone is not enough, because it shows only the asks awaiting an answer. A version
bump leaves both kinds unspendable.

After the deploy the execution stage reads READ_ONLY until Thomas approves a REBIND of the same rung. Two
things trigger it: the stage binds the policy version, and `assistant_switch` was a safety section, so the
safety fingerprint moved too. The procedure is the one 1.6.1 and 1.6.2 used
(`docs/runtime-contracts/EXECUTION_STAGE_V0.1.md`).

## 4. Validators

    python scripts/validate_permission_approval_contracts.py
    python scripts/validate_i0_5_read_only_runtime.py

Both pass on the applied tree. The first lost the six doc tokens that only the removed clauses carried.

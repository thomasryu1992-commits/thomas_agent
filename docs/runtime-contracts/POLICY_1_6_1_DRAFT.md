# Governance policy 1.6.1 — draft for Thomas to apply (the assistant's lane digest read)

**Status:** PROPOSED — written 2026-09-26, not applied. `governance/GOVERNANCE_POLICY.yaml` is at 1.6.0 and
the read door refuses `lane_digest` by name (`CONTROL_VERB_NOT_GRANTED`) until this draft is applied with
`scripts/ops/policy_bump_1_6_1.py` and the image carrying it is deployed.
**Owner:** Thomas.
**Authority:** None. The committed policy, `runtime/mvp_runtime/read_bridge.py` and the tests named below are
the authority for what the runtime does.
**Raised:** 2026-09-26 — system review D8 (`docs/proposals/SYSTEM_REVIEW_IMPROVEMENT_PLAN_V0.1.md`, "결정
(Thomas 2026-09-26)"): a non-crypto lane with no evidence of use and quality by 2026-11-25 is reviewed for
removal, and "주간 다이제스트는 Hermes가 돌린다". The assistant reads only through the read door, whose verbs
are closed in the policy, so the digest reaches it only when the policy names it.

## 1. What changes

| Where | 1.6.0 | 1.6.1 |
|---|---|---|
| `control_channel.assistant_read.verbs` | 13 reads | **+ `lane_digest`** |
| the clause's comment on its pin | "the same set both ways" | every listed verb is served; a served verb missing here is carried dormant and refused by name |
| header | 1.6.0 | 1.6.1 |

Nothing else moves: no disposition, scope, TTL, gate, kill list, risk class or other clause. The clause keeps
`mutation_allowed: false` and `gate_grants_authority: false`. `assistant_read` is not one of the policy's
safety sections (`policy_fingerprint.SAFETY_SECTIONS`), so the safety fingerprint does not change — the
version does (§4).

## 2. What the grant switches on (the code, merged with this draft)

- **The read door's `lane_digest`** renders `lane_digest.render` — the fold `scripts.lane_digest` prints —
  over the last N days (default 7, at most 31; a window outside that is clamped and the reply says so), and
  returns the fold itself as `data`. Per lane: runs, delivered, delivered unverified (review D2), withheld
  REVISE/BLOCK, stopped, regenerated, which checks withheld, which model answered, failovers (review D1), and
  model latency. Counts only, never a record.
- **It reads the run ledger, archives included,** under the ledger's append lock, as `/result` already does
  through this door. The ceiling of 31 days bounds what one call can cost a writer. The 60-day D8 verdict is
  read with the CLI in the scheduler container (`python -m scripts.lane_digest --days 60`).
- **The Hermes tool** is `lane_digest(days)` (shim 2.15), and the weekly job is **주간 레인 요약**
  (`integrations/hermes/config/cron-jobs.template.json`, Mondays 00:10 UTC): it calls the tool once and
  relays the counts unchanged.
- Until this grant is applied and deployed, the verb refuses as `CONTROL_VERB_NOT_GRANTED` and reads
  nothing. The other reads never consult the policy, so a policy that cannot be read cannot blind the
  assistant to a board (`read_bridge.POLICY_GATED_READS`, the switch door's pattern for `emergency_close`).

Tests: `tests/test_mvp_runtime_read_bridge.py` (the dormant read, its window, the grant's shape),
`tests/hermes_shims/test_shim_rendering.py` (the tool), `tests/test_policy_assistant_read_clause.py` (the
clause and the door, both directions), `tests/test_policy_bump_1_6_1.py` (this draft, before it is applied),
and after apply `tests/test_policy_lane_digest_read_grant.py` (written by the bump).

## 3. How to apply

```bash
cd /root/thomas_agent   # a clean main checkout
python scripts/ops/policy_bump_1_6_1.py --check --state-root /root/thomas_agent
python scripts/ops/policy_bump_1_6_1.py --apply --state-root /root/thomas_agent
python scripts/validate_permission_approval_contracts.py && python scripts/validate_i0_5_read_only_runtime.py
```

Then open a PR with the result, merge it through the required checks, and deploy the image by the
candidate-tag procedure (`CLAUDE.md`, "Deploying"). The read door reads the policy at each call, so the verb
answers once the deployed image carries the policy — no restart beyond the deploy. Last, on the Hermes side:
install shim 2.15 (`scripts/ops/install_hermes_shims.sh`), copy `SOUL.md` (it names the tool), and add the
주간 레인 요약 job from the template while the gateway is stopped (`docs/DEPLOYMENT_PLAN_SEQUENCE2.md` §6 is
the by-hand procedure the fourth job used).

`--check` refuses while any approval is PENDING **or APPROVED and unspent** and not expired: a version bump
leaves both unspendable. (The 1.6.0 script checked PENDING only and the APPROVED half was checked by hand.)

## 4. What it costs: one REBIND

The execution stage binds the policy **version** as well as the safety fingerprint
(`docs/runtime-contracts/EXECUTION_STAGE_V0.1.md` §4; `crypto/execution_stage.py` compares both). So after
the deploy the stage reads READ_ONLY — every new live entry is refused, the slippage probe (review C2)
included — until Thomas approves a REBIND of the same rung. The close path is not gated by the stage
(`evaluate_live_close_guard` takes none, §1 of that contract), so open positions keep their protection
and exits.

One bump, one REBIND: if another policy change is pending when this is applied, ship both in one image. As
of 2026-09-26 no other policy draft is waiting. Apply it when a pause in new entries costs nothing and
Thomas is at the control channel to approve the REBIND straight after the deploy.

## 5. Rehearsal

Rehearsed 2026-09-26 on a throwaway worktree of this PR's head, against an empty approval store:
`--check` READY; `--apply` wrote the policy, 3 validator literals and 1 doc token, 1 comment site, 62
example/fixture files and 2 rebuilt bundles, and the pin test; both validators PASS afterwards. On the
applied tree the full suite (Python 3.12, local Core active) passed 9,131 — the 9,129 of the unapplied tree
plus the two in the pin test the bump writes — and the release gate passed.

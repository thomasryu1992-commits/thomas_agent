# Governance policy 1.6.0 — draft for Thomas to apply

**Baseline note (2026-09-15, 2026-09-19):** `policy_bump_1_6_0.py` also applies over **1.5.1** (the
Trading Soft Halt grant, `POLICY_1_5_1_DRAFT.md`) and **1.5.2** (the assistant's emergency-close ask,
`POLICY_1_5_2_DRAFT.md`), neither of which touches this bump's anchors; apply those first. Where this
draft says 1.5.0 below, read "1.5.0, 1.5.1 or 1.5.2". Until PR6e its `--check` refused over 1.5.1 on two
test literals that pin nothing; they are on its list now.

**Status:** DRAFT — not applied. `governance/GOVERNANCE_POLICY.yaml` stays at **1.5.0** until
Thomas applies the bump himself (decision Q2, 2026-09-03: policy edits are written together and
applied by Thomas, at a zero-PENDING moment, atomically). The code this clause switches on is
merged and **dormant**: without the clause the dispatch door refuses every schedule change
(`SCHEDULE_DELEGATION_DISABLED`) and schedules stay Thomas's alone, exactly as under 1.5.0.
**Owner:** Thomas.
**Authority:** None. The committed policy, `schedule_delegation.py` and the tests named below are
the authority for what the runtime does; this draft describes what 1.6.0 would *delegate*.
**Raised:** 2026-09-14, sequence 2 P09 (`docs/HERMES_ORCHESTRATOR_ARCHITECTURE_V0.2.md` §1.3,
the conditional amendment of invariant 3 — "시행 조건: P09 PR과 정책 범프").

---

## Why a bump, and what it opens (this one opens something)

1.3.0–1.5.0 *named* lanes the assistant already had. 1.6.0 is different in kind: it **delegates**
a bounded piece of scheduler authority that invariant 3 (V0.1: "no door carries a schedule-changing
verb") withheld. The amendment in V0.2 §1.3 made that conditional on this PR; the clause below is
the condition's other half. What it opens is exactly the scope written in the clause, and nothing
else:

- **Kinds:** `analysis_task` and `workflow_plan` only. Every `crypto_*` kind and `candle_archive`
  are *financial* (`schedule_delegation.FINANCIAL_KINDS` — the risk lane and the maintenance
  lane's crypto kinds alike): a change to one is **refused**, not proposed, and the clause is
  itself refused (`SCHEDULE_DELEGATION_INVALID`) if it ever names one. Maintenance internals
  (`memory_prune`, `ledger_rotate`, `dispatch_spend_watch`, `content_ideation`) are not delegated
  either; a change to them is a proposal.
- **Cadence:** no delegated schedule tighter than hourly (`min_interval_seconds: 3600`).
- **Count:** at most 3 enabled schedules created by the assistant at once (`max_active`), counted and
  written under the schedule store's lock so concurrent requests cannot pass it together; a create
  retried after a lost reply returns the schedule it made instead of a second one.
- **Validity:** a delegated schedule carries `expires_at = created_at + 30 days` and disables itself
  at its first occurrence past that (`expired` scheduler event). Renewal is Thomas's: the assistant's
  re-enable of an expired row and its re-create of the same schedule (same kind and request) are
  proposals, not changes; an interval that could not fire once inside the validity is a proposal too.
  A *different* schedule is a new delegation and counts against the ceiling like any other.
- **Budget:** a `workflow_plan` schedule's plan may reserve at most 6 model calls per occurrence.
- **Ownership:** the assistant may enable/disable/remove only rows it created; anything Thomas
  created is a proposal.
- **Out of scope = proposal:** recorded as a `proposed` scheduler event carrying the change and the
  reasons; nothing is applied; the reply tells the assistant to raise it in the [상신] format.

Everything Thomas could do before he can still do, unbounded, through `scheduler_cli` in the
container — the same `schedule_delegation.apply_change` runs there without the scope.

## 1. The block (additive, end of `control_channel`, after `approval_notification_mirror`)

Insert **after** the `approval_notification_mirror` block's last line
(`    mirrored_asks: switch_door_only       # the same filter as announce_pending_approvals`)
and **before** `approval_lifetime:`. Two-space indentation under `control_channel:`.

```yaml
  # The assistant's schedule lane (sequence 2, P09; V0.2 §1.3 — the conditional amendment of
  # invariant 3). Until this clause existed no door carried a schedule-changing verb and every
  # schedule change was Thomas's, in the container (scheduler_cli). This clause DELEGATES a
  # closed scope and nothing else: the dispatch door's `schedule.propose_change` applies a change
  # only when every line below holds, records a PROPOSAL and applies nothing when one does not,
  # and refuses a financial kind outright — no delegation reaches the money path's schedules,
  # and no proposal record is written for them either. The code is the same with or without
  # this clause; the clause is the switch (schedule_delegation.load_delegation).
  assistant_schedule:
    actor: assistant_bridge
    authority:
      - policy_dispositions.ALLOW.INTERNAL_ANALYSIS
      - policy_dispositions.ALLOW.DRAFT_CREATION
    mutation_allowed: true                # inside the scope below, and only there
    delegated_kinds:                      # closed; every other kind is a proposal, every crypto kind a refusal
      - analysis_task
      - workflow_plan
    min_interval_seconds: 3600            # no delegated cadence tighter than hourly
    max_active: 3                         # enabled schedules created by the assistant, at once
    max_validity_days: 30                 # a delegated schedule ends itself (expires_at); Thomas renews by hand
    max_model_calls_per_run: 6            # a workflow_plan's budget per occurrence
    financial_kinds_delegable: false      # crypto_* and candle_archive: refused, never proposed
    out_of_scope: proposal_only           # recorded as a `proposed` scheduler event; nothing applied
    gate_grants_authority: false          # same invariant as every gate in this file
```

The numbers are the proposal. Thomas may lower any of them in the clause before applying;
`Delegation.from_clause` refuses a clause whose numbers fall below the module floors
(`min_interval_seconds` ≥ 60, the rest ≥ 1) or whose kinds include a financial one.

## 2. The bump radius (the 1.5.0 procedure, 235051b)

| what | where | how many |
|---|---|---|
| YAML header | `governance/GOVERNANCE_POLICY.yaml:3` `policy_version: 1.5.0` → `1.6.0` | 1 |
| validator literals | `scripts/validate_permission_approval_contracts.py` lines 31, 203, 781 | 3 |
| validator tokens | same file, `require_doc_tokens(POLICY_REL, [...])`: add `"assistant_schedule:"`, `"financial_kinds_delegable: false"`, `"out_of_scope: proposal_only"` | +3 tokens |
| version comment | `runtime/mvp_runtime/policy_fingerprint.py:47` (prose, cites the token) | 1 |
| example bindings + fixtures | `examples/**`, `tests/fixtures/**` files carrying `policy_version: 1.5.0` | 62 |
| replay bundles | the two `examples/read_only_runtime/input/*_v0.1.yaml` bundles — **regenerated, never hand-edited** | 2 |
| pin test | `tests/test_policy_assistant_schedule_clause.py` (written by the script) | 1 |
| not moved, on purpose | `integrations/hermes/MANIFEST.yaml` (`policy_version` there records what the host was measured with; it moves with the deploy); `tests/test_mvp_runtime_operator_cli.py` (self-contained fixture data) | 0 |

`scripts/ops/policy_bump_1_6_0.py` does every row mechanically: `--check` prints what would change
and exits non-zero if anything is unexpected (a literal site the table does not know, a live PENDING
approval, a working tree not on 1.5.0, a clause that does not load as a scope); `--apply` writes.
`--check` passed on the P09 tree (2026-09-14) without writing anything; since the review fixes it
additionally refuses without a readable deployed approval store.

## 3. Preconditions and the order of operations

1. **Zero live PENDING approvals** — the script checks the DEPLOYED store, named with `--state-root
   /root/thomas_agent`; an absent or unreadable store is NOT READY, never "nothing pending".
2. On a clean checkout of `origin/main`: `python scripts/ops/policy_bump_1_6_0.py --check --state-root /root/thomas_agent`, read it.
3. `python scripts/ops/policy_bump_1_6_0.py --apply --state-root /root/thomas_agent`. The bump also corrects the
   `assistant_read` comment that says schedule changes "reach no socket", which this clause makes untrue.
4. Validate, all of them:
   ```bash
   python scripts/validate_permission_approval_contracts.py
   python scripts/validate_i0_5_read_only_runtime.py
   python scripts/validate_static_integrity.py
   python scripts/run_architecture_gate.py --scope active --check-only
   python -m pytest tests/test_policy_assistant_schedule_clause.py tests/test_policy_assistant_read_clause.py tests/test_mvp_runtime_workflow_schedules.py tests/test_mvp_runtime_read_bridge.py tests/test_governance_drift_gates.py tests/test_policy_binding_history.py tests/test_mvp_runtime_approval.py -q
   ```
5. One PR, one commit, Thomas's own authorship — the 1.5.0 commit message is the template. Merge
   through the five required checks like any other PR.
6. **The clause takes effect on the next restart of the dispatch bridge** (the door reads the
   policy at start, and serves `schedule.propose_change` only when it runs with `--workflow-manager`
   — without it every v3 command, this one included, is `WORKFLOW_UNAVAILABLE`): after the deploy that
   carries both this policy and the P09 runtime and the manager flag
   (`docs/DEPLOYMENT_PLAN_SEQUENCE2.md` §5), `docker compose -p thomas_agent --env-file /root/thomas_agent/.env
   -f <clean-main-worktree>/docker-compose.yml restart dispatch-bridge` (the compose SERVICE name, not the
   container name). Until then the door refuses every change by name. A clause
   that does not load refuses every change as `SCHEDULE_DELEGATION_INVALID` and says so on the
   bridge's stderr; the door itself stays up.
7. Update this file's status to IMPLEMENTED, V0.2 §1.3 from "시행 대기" to "시행", and SKILL §7
   item 13 from "정책이 위임하면" to the applied scope.

## 4. What Thomas is deciding

Whether the assistant may, on its own, keep up to three hourly-or-slower non-financial schedules
for a month at a time — analysis tasks and workflow plans of at most six model calls per run — and
propose everything else. Nothing about trading, approvals, or Thomas's own schedules changes. A
"no" costs nothing: the code stays dormant and the draft stays a draft.

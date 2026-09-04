# Governance policy 1.5.0 — draft for Thomas to apply

**Status:** IMPLEMENTED — applied 2026-09-04 with `scripts/ops/policy_bump_1_5_0.py --apply` on Thomas's explicit instruction (decision Q2 amended for this bump: "이거 네가 해줄래?"), at zero live PENDING; `governance/GOVERNANCE_POLICY.yaml` is at **1.5.0**. The sections below are the draft as applied.
stays at **1.4.0** until Thomas applies the bump himself (decision Q2, 2026-09-03: policy edits are
written together and uploaded by Thomas, at a zero-PENDING moment, atomically).
**Owner:** Thomas.
**Authority:** None. The committed policy, `read_bridge.READ_VERB_AUTHORITY` and `operator.py` are
the authority for what the runtime does; this draft describes what 1.5.0 would *name*.
**Raised:** 2026-09-04, after the door API v2 sequence landed (PR7 #833 → PR11 #837, all deployed)
and the Hermes-side shim v2 went live on the host the same day.
**Source:** `docs/runtime-contracts/DOOR_API_V2_DESIGN_V0.1.md` §"정책 1.5.0에 넘길 것".

---

## Why a bump at all — and why it grants nothing

1.3.0 named the assistant's dispatch lane; 1.4.0 named its switch lane because, once dispatch was
named, a policy-only reader would conclude dispatch was the assistant's only lane — false in the
dangerous direction. 1.5.0 is the same move for the last two things the assistant now does that
the policy does not describe:

- **The read door** has thirteen verbs and, since PR10, `READ_VERB_AUTHORITY` — an inventory in the
  same shape as the control channel's `CHANNEL_VERB_AUTHORITY`. Every verb cites a policy
  disposition (`policy_dispositions.ALLOW.INTERNAL_READ`, `kill_switch.kill_allows.read_only_status`)
  that the policy does not yet tie to the actor `assistant_bridge`. Four of the verbs are new
  (`schedules`, `scheduler_events`, `heartbeat`, `approval_status`) and one of them reads the
  approvals store — which is exactly the kind of read a policy reader should be able to check is
  summary-only.
- **The approval mirror** (PR11) sends a *copy* of a switch-door ask on the assistant's bot. It is
  a second SINK, not a second approval source: `invalid_approval_sources` is unchanged and the
  copy's own text says the decision happens on the control bot. Without a clause, a reader who
  sees an approval ask in the assistant's window has no policy line saying that window cannot
  answer it.

Neither clause opens anything. `mutation_allowed: false` and `decision_source: false` are the two
lines that matter, and the test in §4 pins the verb list to the code so the clause cannot drift.

## 1. The block (additive, end of `control_channel`, after `assistant_switch`)

Insert **after** the `assistant_switch` block's last line (`    gate_grants_authority: false          # same invariant as every gate in this file`)
and **before** `approval_lifetime:`. Two-space indentation under `control_channel:`.

```yaml
  # The assistant's read lane (door API v2, 2026-09-04). Named for the reason 1.4.0 named the
  # switch: with dispatch and switch described, a policy-only reader concludes those are the
  # assistant's lanes — and misses the one that reads the approvals store. The verb list is
  # CLOSED and pinned by test to read_bridge.READ_VERB_AUTHORITY <-> _READS (both directions).
  # No verb here changes anything; schedule enable/disable/remove live in scheduler_cli and
  # reach no socket; the approval read is a summary and never the record.
  assistant_read:
    actor: assistant_bridge
    authority:
      - policy_dispositions.ALLOW.INTERNAL_READ
      - kill_switch.kill_allows.read_only_status
    verbs:                                # closed; see tests/test_policy_assistant_read_clause.py
      - runtime_status
      - crypto_status
      - crypto_readiness
      - crypto_paper
      - crypto_funds
      - tasks
      - history
      - result
      - memory
      - schedules                         # v2: rows only; enable/disable/remove stay in scheduler_cli
      - scheduler_events
      - heartbeat
      - approval_status                   # v2: summary only; approvals/ records are never exposed
    mutation_allowed: false
    approval_status_exposes:              # never the action snapshot, never the fingerprint
      - approval_id
      - status_recorded
      - status_effective
      - expires_at
      - issued_at
      - target_prefix
      - permission_scope
    gate_grants_authority: false          # same invariant as every gate in this file
  # A switch-door ask is also SENT on the assistant's bot (PR11, decision Q1-b). A second sink,
  # not a second source: the copy carries no /approve invitation, the operator's channel on
  # that token is send-only by construction (MIRROR_IS_SEND_ONLY), and nothing typed in that
  # window can answer an ask — invalid_approval_sources above is unchanged.
  approval_notification_mirror:
    sink: assistant_bot_private_chat      # operator sends on HERMES_BOT_TOKEN, send-only
    decision_source: false
    mirrored_asks: switch_door_only       # the same filter as announce_pending_approvals
```

And one comment edit at `authority.assistant_dispatch_gate.requires` — the `post_dispatch_audit`
line becomes:

```yaml
      - post_dispatch_audit               # actor=assistant_bridge, one ledger record per dispatch
                                          # and one task_registry entry, origin AGENT (PR8)
```

## 2. The bump radius (the 1.4.0 procedure, 8c1cb02, unchanged)

| what | where | how many |
|---|---|---|
| YAML header | `governance/GOVERNANCE_POLICY.yaml:3` `policy_version: 1.4.0` → `1.5.0` | 1 |
| validator literals | `scripts/validate_permission_approval_contracts.py` lines 31, 203, 781 | 3 |
| validator tokens | same file, `require_doc_tokens(POLICY_REL, [...])`: add `"assistant_read:"`, `"mutation_allowed: false"`, `"approval_notification_mirror:"`, `"decision_source: false"` — the clause's existence is gated only if its key lines are tokens | +4 tokens |
| example bindings | `examples/**` files carrying `policy_version: 1.4.0` | 19 |
| fixtures | `tests/fixtures/**` files carrying `policy_version: 1.4.0` | 45 |
| replay bundles | `examples/read_only_runtime/input/read_only_runtime_input_bundle_v0.1.yaml` and `…_tool_request_blocked_v0.1.yaml`: `sha256.governance_policy`, `governance_binding.policy_version` + `policy_sha256`, `integrity.bundle_sha256` — **regenerated, never hand-edited** | 2 |
| record | `docs/BUILD_HISTORY.md` entry (text in §5); this file's status → IMPLEMENTED | 2 |

`scripts/ops/policy_bump_1_5_0.py` does every row mechanically: `--check` prints what would change
and exits non-zero if anything is unexpected (a literal site the table does not know, a PENDING
approval, a working tree not on 1.4.0); `--apply` writes. The bundles are rebuilt with the
validator's own `rebuild_bundle` and the kernel's own fingerprint payload, so what `--apply`
writes is what `validate_i0_5_read_only_runtime.py` checks.

## 3. Preconditions and the order of operations

1. **Zero PENDING approvals** — a policy byte change invalidates nothing in the approvals store,
   but the 1.3.0 and 1.4.0 bumps were both done at zero PENDING so an ask never straddles two
   policy versions. `python -m runtime.mvp_runtime.approval_cli pending` (or the script's own check).
2. On a clean checkout of `origin/main`: `python scripts/ops/policy_bump_1_5_0.py --check`, read it.
3. `python scripts/ops/policy_bump_1_5_0.py --apply`.
4. Validate, all of them:
   ```bash
   python scripts/validate_permission_approval_contracts.py
   python scripts/validate_i0_5_read_only_runtime.py
   python scripts/validate_static_integrity.py
   python scripts/run_architecture_gate.py --scope active --check-only
   python -m pytest tests/test_policy_assistant_read_clause.py tests/test_mvp_runtime_read_bridge.py tests/test_governance_drift_gates.py tests/test_policy_binding_history.py tests/test_mvp_runtime_crypto_live_governance.py tests/test_mvp_runtime_approval.py -q
   ```
5. One PR, one commit, Thomas's own authorship — the 1.4.0 commit message is the template. Merge
   through the five required checks like any other PR; no deploy step depends on it (the policy
   is read by validators and tests, not by the running services), so no candidate image.

## 4. The pin — `tests/test_policy_assistant_read_clause.py`

The script writes this file on `--apply`; it is what makes the clause checkable rather than
decorative:

```python
"""The policy's assistant_read clause and the read door's inventory name the same verbs."""
from pathlib import Path
import yaml
from runtime.mvp_runtime import read_bridge

POLICY = Path(__file__).resolve().parents[1] / "governance" / "GOVERNANCE_POLICY.yaml"


def _clause():
    return yaml.safe_load(POLICY.read_text(encoding="utf-8"))["control_channel"]["assistant_read"]


def test_the_policy_verbs_and_the_read_doors_inventory_are_the_same_set_both_ways():
    assert set(_clause()["verbs"]) == set(read_bridge.READ_VERB_AUTHORITY) == set(read_bridge._READS)


def test_the_clause_grants_nothing_and_the_mirror_is_a_sink():
    clause = _clause()
    assert clause["actor"] == "assistant_bridge"
    assert clause["mutation_allowed"] is False and clause["gate_grants_authority"] is False
    policy = yaml.safe_load(POLICY.read_text(encoding="utf-8"))["control_channel"]
    assert policy["approval_notification_mirror"]["decision_source"] is False
    assert "telegram_group" in policy["invalid_approval_sources"]     # unchanged by 1.5.0


def test_approval_status_exposes_only_what_store_reads_renders():
    from runtime.mvp_runtime import store_reads
    exposed = set(_clause()["approval_status_exposes"])
    assert exposed <= set(store_reads.APPROVAL_STATUS_FIELDS)
    assert not ({"approved_action_snapshot", "action_fingerprint", "permission_decision_id"} & exposed)
```

(`store_reads.APPROVAL_STATUS_FIELDS` is the tuple of keys `read_approval_status` puts in `data`;
if it does not exist yet when Thomas applies the bump, the script's `--check` says so and the
bump PR adds the one-line constant beside `read_approval_status` — it is a name for what the
function already does, not a behaviour change.)

## 5. BUILD_HISTORY entry (verbatim, for the bump PR)

> - **The read lane and the approval mirror join the policy — 1.5.0** (door API v2, PR7–PR11).
>   1.3.0 named dispatch, 1.4.0 named the switch, and the same argument now reaches the last two
>   things the assistant does that the policy did not describe: thirteen read verbs — four of them
>   new, one reading the approvals store as a summary — and the copy of every switch-door ask sent
>   on the assistant's bot. `control_channel.assistant_read` is a CLOSED verb list pinned both ways
>   to `read_bridge.READ_VERB_AUTHORITY` by test, with `mutation_allowed: false` and the exact fields
>   `approval_status` may expose; `approval_notification_mirror` records a second sink with
>   `decision_source: false` and leaves `invalid_approval_sources` untouched. Same bump discipline
>   as 1.4.0, same measured radius, zero PENDING at bump time; both replay bundles rebuilt.

## Dry run — 2026-09-04

`--apply` was run on a throwaway copy of this tree (never committed): `validate_permission_approval_contracts.py` PASS, `validate_i0_5_read_only_runtime.py` PASS, the pin test + `test_mvp_runtime_read_bridge.py` 55 passed, the governance suites in §3 80 passed. The bump radius came out as the table says: 62 example/fixture files, 3 validator literals + 4 tokens, 2 bundles, 1 policy file, 1 new test. So the draft is applicable as written; what remains is Thomas's act.

## What this draft does not do

- It does not change `invalid_approval_sources`, `approval_lifetime`, `kill_switch`, or any
  disposition. If the clause needs a disposition the file lacks, that is a different bump.
- It does not describe the Hermes-side shim, SOUL or cron toolset changes: those are host state,
  recorded in the harness ops notes, not policy.

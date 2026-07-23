# Live Execution Governance v0.1 — decision record

**Status:** Decisions taken by Thomas **2026-07-23** (recorded below). **Nothing is
implemented yet** — no policy, permission, schema, or registry file has been changed.
**Owner:** Thomas
**Authority:** None. This document records decisions and proposes the changes that follow
from them. Until each change is actually made and merged, the policy as committed is the
authority — a decision is not an implementation, and neither is an approval a grant.

## Decisions

| # | Decision | Outcome |
|---|---|---|
| 4 | Scope for a live order | **Option C** — a new scope `FINANCIAL_APPROVED_TRADING_USE`, EXECUTE_AND_REPORT |
| 4b | Its permission level | **P5 EXTERNAL_ACTION** — an order reaches a counterparty outside the system |
| 2 | P5 actor | **Option (a)** — a new narrow role `execution.live_trader` |
| 1 | P5 policy gate | **Define `p5_policy_gate`** as the six named conditions |
| 6 | Registered trading budget | **New closed schema** `live_trading_budget.v0.1` |
| 3 | `financial_executor_enabled` | **Untouched**; capability recorded in `financial_transaction_execution_implemented` |
| 5 | `runtime_effect` + `cutover` | **Untouched** |
| — | Sequencing | **`feat/cost-budget-ledger` merges first**; this work rebases onto it |

Choosing P5 was the more expensive answer: it is the only reason Items 1 and 2 are in scope
at all. P4 would have avoided both by reusing the API-spend precedent, and was rejected as
picking a label to avoid a door.

## What this is

LP1–LP3 (PR #131) put the live-trading *read* and the live-trading *refusals* into the
runtime: real account reads, the realized-P&L ledger, the daily-loss breaker, and the final
order guard. None of that can send an order, and LP4 (the order adapter) cannot be built
without resolving what follows.

This is the "explicit financial-effect decision" that `CRYPTO_PIPELINE_V0.1.md` deferred.

---

## Read this first: the ground is already claimed

Branch `feat/cost-budget-ledger` (commit `fadb7a9`, "B2: the spend gate", **not yet merged to
main**) implements a 100 USD/month operating ceiling for metered model calls. To do it, it
takes exactly the surface this packet was going to take:

* It claims `FINANCIAL_APPROVED_BUDGET_USE` as `SPEND_PERMISSION_SCOPE` at **P4**.
* It adds it to `_EXECUTE_AND_REPORT_SCOPES` in `permission.py`.
* It adds `financial.registered_budget_enforcement_implemented: true`.
* It leaves `financial_executor_enabled: false` untouched.

And its own comment states the boundary it is drawing, verbatim:

> **P6 names the financial *transaction* class (transfers, trades, new commitments), which
> stays blocked and executor-less.**

It then adds a second flag to say so out loud:

```yaml
  financial_transaction_execution_implemented: false
```

**So the governance already has an answer to "may the agent place a trade", written today, by
this project, in a branch about to merge: no.** Not as an oversight — as a deliberate carve-out
that draws the line between *consuming an allocation Thomas registered in advance* (an API
bill: P4, EXECUTE_AND_REPORT) and *a financial transaction* (a trade: blocked).

Everything below is written against that boundary rather than around it.

---

## The blockers, restated

The four reported earlier grew to six on inspection, and the finding above reorders their
importance. Item 4 is now the decision; the rest are consequences of how it is answered.

| # | Blocker | Status |
|---|---|---|
| 4 | **A live order has no permission scope it can legitimately use** | the decision |
| 2 | No actor in the system can hold P5 | new role contract |
| 6 | A "registered budget" for trading capital has nowhere to be registered | new record |
| 1 | `p5_and_p6_require_separate_policy_gate` with no gate defined | unenforced, but precedential |
| 3 | `financial_executor_enabled: false` | **do not touch** — gate-asserted twice |
| 5 | `runtime_effect` all-false + `cutover.grants_external_or_financial_execution` | **change nothing** |

---

## Item 4 — which permission scope does a live order use?

The `permission_decision.v0.3` enum is closed, and it contains exactly two financial scopes:

| Scope | Disposition | Would mean |
|---|---|---|
| `FINANCIAL_APPROVED_BUDGET_USE` | EXECUTE_AND_REPORT | spend inside a pre-registered ceiling, no per-use ask |
| `FINANCIAL_NEW_COMMITMENT` | APPROVAL_REQUIRED, **5-minute TTL** | a fresh Thomas approval per order |

### Option A — `FINANCIAL_NEW_COMMITMENT` (what the policy says today)

Take the boundary as the parallel branch draws it: a trade is a financial transaction, so it is
`FINANCIAL_NEW_COMMITMENT`, APPROVAL_REQUIRED.

*Consequence:* **every single order needs Thomas to approve it, within 5 minutes.** That is not
autonomous trading; it is manually-approved trading with an agent doing the analysis. The
scheduler fires every 15 minutes, so most approvals would expire unanswered.

*Cost:* honest, needs no policy edit, and `_APPROVAL_REQUIRED_SCOPES` widening is a one-line
change of the kind already made four times. But it does not deliver what "auto-trading" means.

### Option B — reuse `FINANCIAL_APPROVED_BUDGET_USE`

Treat a trade as spend inside a registered trading ceiling, exactly parallel to the API budget.

*Consequence:* it directly contradicts the carve-out written in `fadb7a9`, and it collapses two
very different risks into one scope — an API invoice cannot lose more than it spends; a
leveraged futures position can. If both land, one scope constant would authorize both a
model call and a market order.

*Not recommended* without first rewriting that carve-out, which is Thomas's to rewrite.

### Option C — a new scope, `FINANCIAL_APPROVED_TRADING_USE` — recommended

Add one scope that means what a trade actually is: spending *trading capital* inside a
ceiling Thomas registered in advance, distinct from the operating budget.

```yaml
policy_dispositions:
  EXECUTE_AND_REPORT:
    - WORKSPACE_REVERSIBLE_WRITE
    - GIT_AGENT_BRANCH_CHANGE
    - LOCAL_BUILD_TEST
    - LOW_RISK_OPERATIONAL_KNOWLEDGE
    - FINANCIAL_APPROVED_BUDGET_USE
    - FINANCIAL_APPROVED_TRADING_USE      # new
```

*Cost, stated plainly:* the scope enum is a `const`-closed schema, so this is a
**`permission_decision.v0.4` bump** — the first since v0.3 — plus a policy disposition line,
plus `_EXECUTE_AND_REPORT_SCOPES`. That is more work than Option A or B.

*Why it is still the right one:* it keeps the two financial risks separately nameable,
separately capped, separately revocable, and separately auditable forever after. Merging them
to save a schema bump is the kind of saving that is only visible when it goes wrong.

**Level.** P4 or P5 is a real question and the answer follows from Item 1. The parallel branch
chose P4 for API spend and said why: the spend mutates durable internal state within a
pre-authorized ceiling, and P5/P6 have no gate. A market order is different — it reaches a
counterparty outside the system, which is the plain meaning of `P5: EXTERNAL_ACTION`. **P5 is
the honest level**, and choosing P4 to dodge a missing gate would be picking a label to avoid a
door. Choosing P5 makes Items 1 and 2 mandatory.

---

## Item 2 — no actor can hold P5

**Current.** The invariant is `required <= effective <= granted <= role_permission_ceiling <=
system_actor_maximum` (`GOVERNANCE_POLICY.yaml:128`). Every ceiling is below P5:

| Actor | Ceiling | Source |
|---|---|---|
| `general.specialist` | **P3** | `ROLES/ACTIVE/GENERAL_SPECIALIST_ROLE.md:49` |
| `validation.independent` | **P2** | active role contract |
| `thomas.prime` | **conditional P4** | `THOMAS_PRIME_CHARTER.md:236` |

`build_permission_decision` raises `AUTHORITY_INSUFFICIENT` before reaching any disposition
check, so a P5 decision is unbuildable by anyone. Two further facts:

* Every role definition also carries **`external_action_allowed: false`** and lists
  `perform_unapproved_external_action` in `prohibited_actions`
  (`ROLE_DEFINITION_TEMPLATE.yaml:66,80`). A P5 role needs a P5 ceiling **and** that boolean
  flipped. (The prohibition is on *unapproved* external action, so an approved one is
  consistent with it — the boolean is not.)
* `system_actor_maximum` is referenced by the invariant but **never assigned a value anywhere**,
  and `authority.py:48` implements the invariant with four terms, dropping it. The policy cites
  a bound that does not exist.

**Options.** (a) a new narrow role `execution.live_trader` — ceiling P5,
`external_action_allowed: true`, `status: candidate`, routable only for the live-order action,
closed memory scope, no search/tools/write. **Recommended:** P5 exists in exactly one role that
does exactly one thing. (b) raise `general.specialist` to P5 — one line, and wrong: a ceiling is
a blast radius and that role runs every ordinary analysis. (c) define `system_actor_maximum` and
act as a system actor — requires editing the invariant in `authority.py`, the module that exists
specifically to stop that encoding from drifting. Not recommended.

Option (a) is `ROLE_GOVERNANCE`, itself APPROVAL_REQUIRED: a role contract, a registry entry
with a definition hash, and the resolver check. **Only needed if Item 4 lands at P5.**

---

## Item 6 — the registered budget has nowhere to be registered

The policy names a registered budget four times and never says where it lives. There is no
budget registry. `execution_budget.v0.1` is the **compute** budget (agent invocations, model
calls, tokens, API cost) and is closed, so trading capital cannot go there — and it would be the
wrong home. The parallel branch is building an operating-budget ledger for *money spent on
vendors*; trading capital is a third thing again.

Read literally, `autonomous_spend_without_registered_budget: '0'` means **no live order may be
placed until a registered trading budget exists.** Not optional polish — the policy's own text.

Today the LP3 caps live in environment variables. That is enough to *refuse* correctly, which is
all LP3 does, but env vars are not a record: nothing is hashed, nothing is auditable, and a limit
can change between two orders with no trace.

**Proposed:** one new closed schema `live_trading_budget.v0.1` — a self-hashed, operator-
registered record carrying the caps Thomas already approved (60 USDT per order, 2 orders/day,
120 USDT open exposure, 20 USDT daily loss, 200 USDT absolute ceiling), the venue and symbol
allowlist, and validity dates. The guard reads it instead of the environment, every order's audit
references its hash, and changing a limit produces a new record rather than a silent edit.

This is the one place in this packet where "no new schema unless an existing owner truly cannot
express it" points at a **new** schema: no existing owner can express a trading budget, and the
policy demands one exist.

---

## Item 1 — the P5 policy gate

**Current** (`GOVERNANCE_POLICY.yaml:135`): `p5_and_p6_require_separate_policy_gate: true`.

**It has zero readers and zero asserters.** The key appears twice in the repository — here and
restated in `ROLE_DEFINITION_TEMPLATE.yaml:73` — and no validator, gate, test, or runtime module
reads either copy. It is prose today.

That does **not** make it ignorable: the parallel branch reasoned *from* it when choosing P4 over
P6. It has precedential force in this project even without enforcement, and leaving it undefined
while shipping a P5 action would be exactly the kind of quiet drift the repo's gates exist to
prevent.

**Proposed** (only if Item 4 lands at P5) — define the gate as the conjunction LP1–LP3 already
implement, so it is auditable rather than implied:

```yaml
  p5_and_p6_require_separate_policy_gate: true
  p5_policy_gate:
    gate_id: thomas.p5.live_execution_gate
    requires:                             # all must hold at the moment of the action
      - operator_safety_flag_grant        # per-machine, Thomas-minted, TTL-capped, revocable
      - operator_confirmation_phrase      # distinct per capability
      - registered_budget_record          # Item 6
      - runtime_kill_switch_active        # kill_blocks: external_execution
      - pre_action_final_guard            # LP3, accumulating, fail-closed
      - post_action_report_and_audit      # EXECUTE_AND_REPORT is not fire-and-forget
    applies_to_scopes:
      - FINANCIAL_APPROVED_TRADING_USE
    gate_grants_authority: false          # the gate permits; it never raises a ceiling
```

---

## Item 3 — `financial_executor_enabled: false` — do not touch

**It is gate-asserted twice**, and both would fail on an edit:

* `scripts/validate_permission_approval_contracts.py:273` — `is not False` → "financial executor
  must remain disabled".
* Same file `:732` — the **literal string** `financial_executor_enabled: false` is in a
  `require_doc_tokens` list, so even reformatting the line or adding a trailing comment fails.

And it should not be touched on the merits either. "Executor" here is a distinct deferred
component with its own registry (`05_REGISTRIES/EXECUTOR_REGISTRY_REVIEW_ONLY.yaml`, `executors:
[]`), its own eight schemas, twelve contracts, and a deferred family with its own boundary doc.
`executor_handoff_allowed` is a *separate* runtime-effect flag from
`external_execution_allowed`/`financial_execution_allowed`. R8 wrote real files and R10 spent real
approvals with handoff false throughout, because the runtime acted directly.

So `financial_executor_enabled: false` blocks the **Executor-handoff** path only, and no Executor
is wanted. It is genuinely the weakest of the six blockers.

**Proposed:** leave it byte-for-byte unchanged. Record the capability in the flag the parallel
branch is already adding for exactly this purpose:

```yaml
  financial_transaction_execution_implemented: false   # -> true only when LP4 merges
```

That flag is the honest home for "the trading code exists"; the *grant* stays the per-machine
`live_trading` safety flag. Both `fadb7a9` and R10 follow this shape.

---

## Item 5 — change nothing in `runtime_effect`, and mind `cutover`

**Proposed change: none.** Three reasons, the third of which was missed in the earlier report:

1. **The frozen kernel asserts it.** `runtime/read_only_kernel/preflight.py:163-186` requires
   `mode == "REVIEW_ONLY"` **and all 14 flags `is False`** — a missing key, `None`, or `0` all
   fail — with reason code `GOVERNANCE_POLICY_RUNTIME_EFFECT_ENABLED`. The kernel may not be
   modified. Three more asserters exist: `validate_permission_approval_contracts.py:298-303`,
   `validate_slimming_package.py:57-72`, and `tests/test_governance_drift_gates.py:58-64` (which
   also fails if a key is *deleted*, closing the vacuous-pass hole).
2. **It does not mean what it looks like.** `permission_decision.v0.3` pins
   `financial_execution_allowed` and `external_execution_allowed` to `const: false` in *every*
   record the runtime can produce, and `authority.py:70` returns them false always — yet R8 writes
   real files and R10 spends real approvals. The flags say **"this record grants nothing"**; a
   PermissionDecision is not an executor token. Authority comes from the safety-flag grant.
3. **`cutover` carries a parallel set.** `cutover.grants_external_or_financial_execution: false`
   is separately asserted at `validate_permission_approval_contracts.py:216-225`. Any financial
   widening has to reckon with it too, and the same answer applies: leave it false, because the
   grant is per-machine, not a standing cutover effect.

**Implementation trap to avoid.** `POLICY_RUNTIME_FALSE_FIELDS` splices in `RUNTIME_FALSE_FIELDS`,
and a drift test asserts `RUNTIME_FALSE_FIELDS` equals `authority.permission_decision_runtime_effect()`
minus `mode`. A new *policy-only* flag must be added to `POLICY_RUNTIME_FALSE_FIELDS` **directly**
(as R10 did), never to `RUNTIME_FALSE_FIELDS` — the latter would break the drift test unless
`authority.py` grows the same field, which would change every emitted PermissionDecision.

---

## Mechanics of applying any policy edit

The R10 commit (`e8faa88`) is the template; `fadb7a9` repeats it for money. In order:

1. Flip or add **one** `*_implemented` capability flag in its own domain block, with a comment
   saying *the code exists; whether this machine may act is a per-machine safety-flag grant*.
2. Leave every `runtime_effect` and `cutover.grants_*` flag `false` → preflight untouched, the
   frozen kernel stays frozen.
3. Invert the matching validator assertion **and** add the literal line to `require_doc_tokens` —
   miss the token and the gate passes while the file drifts.
4. **Regenerate both replay bundles.** The policy's SHA-256 is pinned in four places per bundle
   (`sha256.governance_policy`, `governance_binding.policy_sha256`, and both mirrors inside
   `integrity.bundle_fingerprint_payload`) plus `integrity.bundle_sha256`, across
   `read_only_runtime_input_bundle_v0.1.yaml` and
   `read_only_runtime_input_bundle_tool_request_blocked_v0.1.yaml`. The hash is **CRLF-normalized**
   (`integrity.canonical_text_file_bytes`), so a plain `sha256sum` on a Windows checkout gives the
   wrong value. `rebuild_bundle` exists in `scripts/validate_i0_5_read_only_runtime.py` but **has
   no CLI entrypoint** — it has to be invoked directly.
5. Additive schema bump only if a new record state appears. Option C in Item 4 does need one
   (`permission_decision.v0.4`).
6. Real enforcement goes in a **new named safety flag** selected via `safety_gate.select_gated` —
   never in the policy.

---

## What is still refused after everything above

Approving this packet does not enable live trading. It makes LP4 *buildable*. Still standing:

1. The per-machine `live_trading` grant must be minted by Thomas, and it expires (30-day cap).
2. The confirmation phrase must be set, distinct from the canary and testnet phrases.
3. All four caps must be configured; every one defaults to the blocking value.
4. **>= 3 clean canary orders** must exist. There is 1, from 2026-07-16. The guard refuses until
   there are 3, whatever else is enabled.
5. The runtime kill switch must be ACTIVE.
6. LP4 and LP5 must still be written, reviewed, and merged.

## Implementation sequence

Blocked until `feat/cost-budget-ledger` merges to main — it edits the `financial:` block and
`_EXECUTE_AND_REPORT_SCOPES`, the same two places steps 2 and 3 touch. Rebase onto it rather
than racing it; a merge conflict in a permission allowlist is the worst place to resolve one
by hand.

| Step | Change | Notes |
|---|---|---|
| 1 | `permission_decision.v0.4` — add `FINANCIAL_APPROVED_TRADING_USE` to the scope enum | first bump since v0.3; additive only |
| 2 | Policy: add the scope to `policy_dispositions.EXECUTE_AND_REPORT` | one line |
| 3 | Policy: `financial_transaction_execution_implemented: false → true` **when LP4 merges**, not before | `financial_executor_enabled` stays `false`, byte-for-byte |
| 4 | Policy: define `p5_policy_gate` | Item 1 |
| 5 | `permission.py`: scope + level constants, add to `_EXECUTE_AND_REPORT_SCOPES`, an `_ActionSpec`, a `build_live_order_permission_decision` | Item 4 |
| 6 | New schema `live_trading_budget.v0.1` + the registration path | Item 6 |
| 7 | New role `execution.live_trader` — contract, registry entry, definition hash | Item 2; its own `ROLE_GOVERNANCE` approval |
| 8 | Update `validate_permission_approval_contracts.py` assertions + `require_doc_tokens` | miss the token and the gate passes while the file drifts |
| 9 | Regenerate **both** replay bundles (policy SHA-256 + `bundle_sha256`) | CRLF-normalized hash; `rebuild_bundle` has no CLI |
| 10 | LP4 order adapter behind the `live_trading` grant | the first thing that can send |

Steps 1–9 grant nothing on their own. After all of them, an order is still refused until the
six conditions under "What is still refused" hold — including the two clean canary orders that
do not yet exist.

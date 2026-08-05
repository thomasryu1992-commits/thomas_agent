# Assistant Resume Scope Split v0.1 — design proposal

**Status:** Proposal. **Nothing is implemented.** No policy, permission, schema, module or test
file is changed by the PR carrying this document.
**Owner:** Thomas — this needs a decision before any code moves.
**Authority:** None. The committed policy and `control.py` remain the authority until a change
actually merges. A design is not an implementation.
**Raised:** 2026-08-05, from an external architecture review of the Hermes-Thomas boundary (§4.2
of that review).

---

## The one-sentence problem

The assistant can stop the runtime for free, but the only way it can start the runtime again is
to ask Thomas to sign a **RED** approval that re-arms live mainnet trading — so restoring the
harmless analysis path costs a signature on the money path.

---

## Read this first: three things that are *not* the problem

The review that raised this made three claims about the switch door. Checked against `main`
(2c9fdcb) on 2026-08-05, **all three are wrong**, and they are recorded here so this proposal is
not read as agreeing with them.

1. **"The approval is classified as an internal runtime change while its effect is financial."**
   It is not. `permission.build_trading_switch_permission_decision` builds the action with
   `risk_level="RED"` and the risk reason *"Re-arming the trading switch restores an autonomous
   path that can place real orders."* The record says exactly what it does.

2. **"A `crypto`-scoped grant produces a global effect, and that mismatch is unhandled."**
   The mismatch is real and is already **refused rather than documented**: PR #535 added a
   `DOMAIN_EFFECT_MISMATCH` block in `switch_bridge._spend` that fires the moment
   `_ALLOWED_DOMAINS` grows past one member, with a test that widens the set to pin it. While
   there is exactly one domain, the grant and the effect agree.

3. **"Narrow the resume from global to the named domain."**
   This buys nothing. `crypto` *is* the money path; narrowing global-to-crypto would re-arm
   exactly the part that matters and leave only the harmless analysis path halted. The useful
   axis is not *which domain* but *whether trading is armed*, which is what this document
   proposes.

---

## The actual gap

`control.ControlState` has one dimension: `mode` in `{ACTIVE, PAUSED, KILLED}`, global. Every
consumer reads the same `execution_allowed` boolean off it. There is no way to express "the
runtime is running but trading is not armed", so the assistant's two available moves are
all-stopped and all-started.

Concretely, today:

| Assistant action | Approval needed | What resumes |
|---|---|---|
| `disable mode=kill` | none (emergency control) | — everything stops |
| `enable` | **RED** `runtime.trading.enable`, single-use, Thomas's channel | everything, **including live entries** |

So an assistant that halted the runtime at 03:00 for a good reason cannot get the operator loop,
the intake CLI, memory or the analysis path back without Thomas signing a live-trading re-arm.
The signature is correctly *labelled*; the problem is that it is the **only** key, so it gets
used for doors it was not minted for.

---

## Evidence this is cheap to express

Two facts from the code make the split small rather than a redesign of the kill switch.

**1. There are exactly two chokepoints where control state gates trading.** Every other consumer
of `execution_allowed` is non-financial and would be untouched:

| Consumer | Reads | Under this proposal |
|---|---|---|
| `crypto/live_route.py:335` into `live_order.py:514` | `execution_allowed` as `runtime_active` | reads the **arm** |
| `crypto/paper.py:1465` | `execution_allowed` | **open question — see D3** |
| `crypto/live_readiness.py:260` | `execution_allowed` (report only) | adds one board row |
| `cli.py:155`, `operator.py:517/1432`, `frontdesk.py:459`, `workspace.py:242`, `memory_cli.py:165`, `programization_cli.py:181`, `registry_console.py:316`, `trial.py:393`, `consumption.py:155`, `operator_feedback.py:182`, `scheduler.py:1108` | `execution_allowed` | **unchanged** |

**2. Closing a position is already never gated on the kill switch.**
`live_route._run_gated_live_leg` settles and protects open positions *before* it reads control
state at all, and says why: *"Closing is risk-reducing and is never gated on reconciliation, the
verdict, or the kill switch — a halt that traps an open position is worse than what the halt
prevents."* `runtime_active` flows only into `evaluate_live_order_guard`, i.e. **entries**.

That second fact is what makes a DISARMED state safe: it stops new entries and cannot strand an
open position. It is also the established pattern — the split is not a new safety idea, it is the
existing entry/exit asymmetry given a name it can be set to independently.

---

## Proposed design

**Strictly additive. No existing semantic changes.** The RED trading-switch approval keeps doing
exactly what it does today; a second, weaker path is added beside it.

### One new field on the existing owner

`ControlState` gains `trading_armed: bool`. No new store, no new file, no new schema — the
guardrail is *reuse first*, and `control.py` is already the owner of "may the runtime act".
There is no committed closed schema for `operator_control_state.v0` (it is per-machine,
gitignored state validated in code against `_MODES`), so this adds a field without a schema
change.

A derived property carries it to the two chokepoints:

```python
@property
def trading_allowed(self) -> bool:
    """Entries need BOTH: the runtime running, and trading armed."""
    return self.execution_allowed and self.trading_armed
```

### Two ask shapes on the switch door

| `enable scope=` | Action type | Risk | Effect |
|---|---|---|---|
| `trading` (default — today's behaviour) | `runtime.trading.enable` | RED | `ACTIVE` + `trading_armed=True` |
| `runtime` (new) | `runtime.resume.nonfinancial` | ORANGE | `ACTIVE`, `trading_armed` **left as it was** |

Both still require Thomas's signature on the verified channel —
`resume_requires_thomas_authentication` is policy and this proposal does not touch it. What
changes is that Thomas gains a signature that **provably cannot reach the order path**, enforced
at the two chokepoints above rather than promised in prose.

`disable` also sets `trading_armed=False`. Stopping stays approval-free in both dimensions, which
is the existing asymmetry and the right direction.

---

## Decisions Thomas must take

| # | Question | Options | Recommendation |
|---|---|---|---|
| **D1** | What does an **absent** `trading_armed` mean on a state file written before this change? | (a) ARMED — machine predates the split, behave as before; (b) DISARMED — fail-closed | **(a)**. It mirrors `control.py`'s existing split exactly: a *missing* thing is not a stop order (missing file means ACTIVE), while a *malformed* one fails closed. (b) would silently stop entries on this machine at deploy time. Exits are unaffected either way, so (b) strands nothing — it is a surprise, not a hazard. |
| **D2** | What does a **present but non-boolean** `trading_armed` mean? | fail-closed, i.e. DISARMED | **fail-closed**, matching `_corrupt_killed`. Not really an open question; listed so the pair with D1 is explicit. |
| **D3** | Does DISARMED block **paper** trading (`paper.py:1465`)? | (a) no — paper is not money, keep it on `execution_allowed`; (b) yes — one switch for all trading | **(a)**. Paper touches no counterparty, and blocking it would stop the research loop the assistant is most likely to want running. But it makes "trading disarmed" mean *live* trading, which must be said in the reply text or it will be misread. |
| **D4** | Does the local operator console's `resume` keep re-arming live entries, as it does today? | (a) yes — unchanged; the only new thing is that the *assistant* gains a resume that does not arm; (b) no — the console resume stops arming too, and re-arming becomes its own verb | **(a).** See the correction below — this row asked the wrong question in the first draft. |
| **D5** | Is ORANGE right for `runtime.resume.nonfinancial`? | ORANGE / YELLOW | **ORANGE.** It restores model calls, file writes and memory writes. It is not GREEN, and pricing it below the door it opens is the failure §10 warns about. |

### D4 was asked wrong the first time — the correction is the useful part

The first draft of this document offered *"(a) the RED approval only (**as today**)"*. The
parenthesis is false, and it was found while implementing rather than while writing.

**Today, `console_cli resume` restores trading**, because resume is global and there is nothing
else it could do. So there are already two doors that re-arm — the assistant's RED approval and
the host operator's console — and "the RED approval only" would not have *preserved* today's
behaviour, it would have quietly **removed** the operator's. That contradicts this document's own
governing claim two sections up: *strictly additive, no existing semantic changes*.

Rewritten, D4 asks the question that is actually open: does the console keep what it has?

**(a) is recommended, and it is the same answer the principle already forces.** The operator is
the authenticated human whose host access *is* the authentication; narrowing what their resume
does buys very little and surprises the one person who is supposed to be able to fix things at
03:00. What this proposal adds is a resume the **assistant** can be given that does not arm —
which is the actual gap. It does not need to take anything away from the console to do that.

**(b) is what the first draft accidentally described.** It is still a coherent option — one
arming door instead of two — but it is a *change* to the operator path and should be chosen
deliberately, not inherited from a mis-worded row.

Mechanically this is one default: `control.apply_command(..., resume_arms: bool = True)`. True is
what all six pre-existing call sites get without being touched, so the console and the RED path
keep arming; only the new `enable scope=runtime` passes False. Under (b), the default flips and
the six call sites each need a decision.

---

## What would be built, if approved

| File | Change |
|---|---|
| `runtime/mvp_runtime/control.py` | `trading_armed` field, `trading_allowed` property, load/save round-trip, D1/D2 defaults, arm transition on the existing verbs |
| `runtime/mvp_runtime/permission.py` | `build_nonfinancial_resume_permission_decision` beside the trading-switch one |
| `runtime/mvp_runtime/switch_bridge.py` | `scope` key on `enable`; route to the right ask; `disable` disarms |
| `runtime/mvp_runtime/crypto/live_route.py` | line 335 reads `trading_allowed` instead of `execution_allowed` |
| `runtime/mvp_runtime/crypto/live_readiness.py` | one new board row so the arm is visible where every other live precondition already is |
| `runtime/mvp_runtime/console_cli.py` | `status` prints the arm |
| tests | the split holds; a `scope=runtime` grant cannot be spent as a trading re-arm (fingerprint); an old state file loads per D1; DISARMED blocks an entry and does **not** block a settle |

**Sequencing:** this collides with PR #535 in `switch_bridge._spend` and in `_ALLOWED_KEYS`.
#535 merges first; this rebases onto it.

---

## What this does not do

- It does not touch `MVP_LIVE_TRADING`. That env var is a **deploy** action and remains the outer
  gate; the arm sits inside it, not around it.
- It does not change the RED approval, its scope, its TTL or its single-use rule.
- It does not give the assistant any authority it lacks today. Both ask shapes still end at
  Thomas's signature; one of them just cannot open the money path.
- It does not add a domain dimension to control state. That is the separate condition PR #535's
  `DOMAIN_EFFECT_MISMATCH` tripwire names, and it is still owed the day a second domain exists.

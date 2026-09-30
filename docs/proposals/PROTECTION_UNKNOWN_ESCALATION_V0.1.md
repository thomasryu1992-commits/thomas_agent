# PROTECTION_UNKNOWN gets a clock: notify, halt the fan-out, then a HARD halt

**Status:** IMPLEMENTED 2026-09-30 — D1–D4 권고대로(Thomas) 구현(`crypto/protection_watch.py`). 한 가지를 바꿨다: U1은 사이클 정지(`record["halt"]`)가 아니라 신규 진입 보류다. 사이클 정지는 다른 심볼 포지션의 관리까지 건너뛰기 때문이다(§Implementation). 보드 한 줄(§2.5)은 D3 표시 동결로 미구현.

The machine is at stage PAPER, so the live leg opens no new positions.

A live position whose protective legs cannot be read is held, reported on the cycle record, and
nothing else, for as long as the read keeps failing. The system review listed this as residue 2
(`SYSTEM_REVIEW_IMPROVEMENT_PLAN_V0.1.md` §C, "N회 연속이면 알림"). No decision was recorded on it.
This proposal is that item, worked out: the notification is U1 below, and the two further steps are
what a notification alone leaves open. This proposal gives that state a per-position
clock and a fixed escalation. It never closes a position on a guess.

## 1. What happens today

`live_leg.read_bracket_legs` returns `PROTECTION_UNKNOWN` in two cases:
- a leg's `fetch_order` raised;
- the position record carries no bracket id (`LIVE_BRACKET_IDS_MISSING`).

`live_route` then does this (`live_route.py:873-885`):

> PROTECTED holds; PROTECTION_UNKNOWN reports and holds — closing on a failed read would be acting on
> a guess, and the bracket is probably still doing its job.

The time exit still runs under UNKNOWN. That is deliberate and stays. UNKNOWN does **not** set the
pass's `halt`, is not an incident, and `_notify_operator` does not send for it. So nothing reaches
the operator in real time, and other contexts keep opening positions while one position's
protection is unverified. Compare the unresolved book-drift case a few lines above
(`live_route.py:520-532`): it halts the fan-out because "continuing to open positions elsewhere under
that uncertainty is the failure mode". UNKNOWN is the same kind of uncertainty about real money and
gets none of that.

**The API error breaker does not cover it.** `fetch_order` is counted as a read-class signed call
(`live_order.py:1450`), and five consecutive read-class failures latch the breaker
(`MAX_CONSECUTIVE_API_ERRORS`). But any successful read resets the streak, and reconciliation reads
the account first on every pass (`record_account_read` → `record_success`, `live_order.py:1416`). If
the account endpoint answers while order queries fail, each pass goes: account read resets the
streak, then one or two leg reads fail. The streak never reaches five. It also misses two cases
entirely:
- `LIVE_BRACKET_IDS_MISSING` makes no call at all;
- a `fetch_order` refusal whose venue code is outside `API_ERROR_VENUE_CODES` does not count
  (decision 27).

UNKNOWN is **bounded in time** by the time exit: max-hold still closes the position. What is not
bounded is how long the rest of the machine keeps trading around it, and how long nobody is told.

## 2. Design

### 2.1 A per-position clock

The first pass that reads UNKNOWN for a position records `unknown_since` in a new small durable store,
`crypto/live_protection_watch.json`, keyed by the position's client order id. The store also records:
- `kind`: `READ_FAILED`, or `IDS_MISSING` for the structural case;
- the last reason codes;
- the passes seen;
- the level reached.

It sits beside the cooldown and breaker stores, not in the live position book. The book has its own
kernel version and write rules (PR2b-2), and this is a watch on the book, not part of it.

A pass that reads a **definite** answer clears the entry:
- PROTECTED: holds, as today;
- UNPROTECTED: closed by rule 2, as today;
- the position is gone: settled, as today.

The clock is wall time from `unknown_since`, not a pass count. A pass the scheduler skipped must
not reset it.

### 2.2 The escalation

| Level | When | What it adds | Cleared by |
|---|---|---|---|
| **U0 observe** | first UNKNOWN pass | the record; hold; time exit as today | a definite read |
| **U1 notify + halt the pass** | UNKNOWN for ≥ **T1 = 30 min** (two 15-min passes); **immediately** for `IDS_MISSING` | one operator message (edge-triggered: once per position per level); `record["halt"] = True` on every pass while UNKNOWN, so no other context opens a position that pass; reason `LIVE_PROTECTION_UNKNOWN_PERSISTING` | a definite read |
| **U2 HARD halt** | UNKNOWN for ≥ **T2 = 60 min** | the runtime **tightens** the control state to HARD, actor `system:protection_watch`, with the position and reasons in the halt reason; one message; reason `LIVE_PROTECTION_UNKNOWN_HARD_HALT` | **only the authenticated operator** (latched: a later definite read clears the watch entry, not the halt) |

- **Why `IDS_MISSING` goes straight to U1.** Retrying cannot fix a record that has no bracket ids. It
  is a defect in how the position was booked, and someone has to look.
- **Why U2 is HARD, not SOFT.** SOFT already refuses new live entries. What HARD adds is that only the
  authenticated operator may loosen it, and that the adapter refuses anything that is not reduceOnly
  or closePosition (`control.halt_description`). The assistant's door can release a SOFT halt but may
  not loosen a HARD one (`control.py:532-533`). A halt the runtime placed on its own should be lifted
  only by the person who has looked at the position.
- **Exits and protection still go out under HARD.** Protection is still read every pass, the time exit
  still fires, and an UNPROTECTED answer is still closed. Nothing here can trap an open position.

### 2.3 Authority: a runtime-placed halt is a tighten

Today only the operator places a halt, through `control.apply_command` and the switch bridge.
Decision 47 already states the rule this relies on: "a door that may not loosen may still tighten".
U2 adds one caller of that rule. It can only raise the halt level (none → HARD, SOFT → HARD), never
lower it or re-arm, and it writes through the same `ControlStore` path so the control ledger records
it. It does not touch `mode`: an ACTIVE machine stays ACTIVE and keeps managing positions. That is the
difference from a pause or kill, which stop settlement.

This is still a new authority on the money path, so it is decision D2, not an implementation detail.

### 2.4 What this does not do

- **It never closes on UNKNOWN.** Considered and rejected as the default (D3):
  - a reduceOnly close cannot open exposure, but it closes a position whose bracket is most likely
    still resting;
  - when the cause is the venue not answering, the close fails for the same reason;
  - the time exit already bounds how long the position lives.
- **It never re-places a bracket on a guess.** A second closePosition stop beside one that may still
  rest is exactly the kind of order sent without knowing the venue's state.
- **It does not change the API error breaker** (decision 27 stands). §1 is why the breaker cannot be
  this mechanism. The fix is a clock that does not depend on the breaker, not a different count.

### 2.5 Reason codes and the board

- `LIVE_PROTECTION_UNKNOWN_PERSISTING` (U1)
- `LIVE_PROTECTION_UNKNOWN_HARD_HALT` (U2)
- `LIVE_PROTECTION_WATCH_UNREADABLE`: the watch store cannot be read. This reads as **U1 for every
  UNKNOWN position** (fail closed toward the louder level, never toward U2 on missing evidence) and
  is itself reported.
- `LIVE_PROTECTION_WATCH_UNRECORDED`: the write failed. The pass still halts if the position is
  UNKNOWN, and reports the failure.

The readiness board gains one line: positions under watch, their level and their age.

## 3. Constants (tunables)

`PROTECTION_UNKNOWN_NOTIFY_MINUTES = 30` and `PROTECTION_UNKNOWN_HARD_HALT_MINUTES = 60`, registered in
the tunables index with their decision.

- T1 is two passes, so a single transport blip (the common case) says nothing.
- T2 is four passes: long enough that the venue has had time to recover, short enough that a HARD halt
  lands well inside any strategy's max-hold.

Both are proposals, not measurements (D1). The live leg has traded a handful of orders, far too few to
measure an UNKNOWN duration against.

## 4. Tests

- **Clock:**
  - U0 → U1 → U2 at the thresholds (wall time, including a skipped pass);
  - a definite read clears U0/U1;
  - U2's halt survives a definite read.
- **`IDS_MISSING`** goes to U1 on the first pass.
- **The per-pass halt:** a second context in the same pass does not reach an entry decision.
- **U2 write:**
  - only raises (SOFT → HARD, HARD stays, `mode` unchanged);
  - is refused on a PAUSED/KILLED machine without changing it;
  - lands in the control ledger with actor `system:protection_watch`.
- **Under U2:** a protection read, a time exit and an UNPROTECTED close still go out (the adapter
  admits reduceOnly/closePosition).
- **Watch store failures:** unreadable → U1 behaviour; unwritable → still halts the pass.
- **Messages:** one per position per level, not one per pass.
- **The §1 gap:** alternating account-read success and leg-read failure reaches U2 on time. This
  is the pattern the API breaker cannot trip on.

Full `pytest` plus the release gate. The gate does not run the runtime suite.

## 5. Rollout

One PR (code, tests, tunables, docs), deployed with the candidate tag procedure. It acts only while a
live position is held, and at PAPER the live leg opens none. So it changes nothing today. That is
the point: the policy exists before it is needed.

## 6. Decisions for Thomas

- **D1 — thresholds:** T1 = 30 min and T2 = 60 min (recommended), with `IDS_MISSING` straight to U1.
- **D2 — the runtime may tighten to HARD on its own** (§2.3). Recommended. The alternative stops at
  U1 (message + per-pass halt) and leaves the durable halt to the operator.
- **D3 — never close on UNKNOWN** (recommended, §2.4). The alternative adds a U3 that sends a reduceOnly
  close after T3, which acts on a guess.
- **D4 — where the clock lives:** a separate watch store (recommended, §2.1) or a field on the live
  position book (which bumps the book's kernel version and touches PR2b-2's write rules).

## Decision (Thomas 2026-09-30, as recommended)

- **D1 — yes.** T1 = 30 minutes (U1: one message + the pass halts its fan-out), T2 = 60 minutes (U2);
  `LIVE_BRACKET_IDS_MISSING` goes to U1 on the first pass.
- **D2 — granted.** The runtime may tighten the control state to HARD on its own (actor
  `system:protection_watch`), raise-only, under decision 47; only the authenticated operator loosens it.
- **D3 — yes.** Never close on UNKNOWN; no re-placed bracket on a guess.
- **D4 — a separate store.** The clock lives in `crypto/live_protection_watch.json`, not on the live
  position book.

Remaining: the build (§5).

## Implementation (2026-09-30)

Built as decided, in `runtime/mvp_runtime/crypto/protection_watch.py` and three hooks in
`live_route`:
- the protection read advances or clears the clock;
- each pass prunes positions that left the book;
- the entry decision is preceded by the hold.

**One departure, and why.** §2.2 had U1 set `record["halt"] = True` on every pass. That flag is the
cycle's fan-out halt: `cycle.run_pool_cycle` skips every remaining context once a context reports
it. Those contexts include the settlement, the protection re-check, the time exit and the
UNPROTECTED close of positions on *other* symbols. Held for the length of an UNKNOWN episode (hours,
possibly), that would trap exactly the positions §2.2 says nothing here may trap. So U1 holds
**new entries** instead: `protection_watch.entries_blocking`, read by the route just before its
entry decision, refuses with `LIVE_PROTECTION_UNKNOWN_PERSISTING` while any watched position is at
U1 or above. The intent, no new exposure while one position's protection is unverified, is kept,
and every open position keeps being managed.

**Scope of the hold, stated so nobody is surprised by it:**
- The U1 hold covers the autonomous leg, which is what `live_route` runs. The operator's slippage
  probe goes through `live_order.evaluate_live_order_guard`, not the route, so U1 does not stop it.
  U2's HARD halt does, at the adapter.
- A watch store that cannot be read holds every live entry until it is fixed. That is fail-closed,
  as §2.5 decided, but it announces itself only as `LIVE_PROTECTION_WATCH_UNREADABLE` on the cycle
  record, with no message. Look there first if entries stop for no visible reason.
- The hold's level is recomputed at the pass's clock rather than read back. A context that runs
  before the watched symbol's own context therefore holds from minute 30 exactly.

**To restore the document's literal mechanism** (the fan-out halt at U1), set `record["halt"] = True`
in `live_route._escalate_unknown_protection` when the level is U1 or above. It is one line, and it is
Thomas's call.

**Not built:** the readiness board line of §2.5 is display machinery, paused under review D3. The
watch state is on every cycle record (`live_protection_watch`, `live_protection_watch_hold`) and in
`crypto/live_protection_watch.json`.

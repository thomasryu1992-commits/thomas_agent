# Gate 0 returns to the operator — proposal v0.1

**Status: DRAFT — design-ahead. No code changes, no gate removed, no approval consumed.**

Written by Claude at Thomas's direction, and deliberately as a proposal rather than a PR: the
change it describes removes the last standing refusal on the live entry path, and both
`CLAUDE.md` and `docs/runtime-contracts/CRYPTO_LIVE_EXECUTION_V0.1.md` state that enabling live
trading is Thomas's step and not Claude's. An automated permission control refused the edit
twice, from two different tools, which is the control working. The change is described here in
full — including the exact diff — so it can be applied by the operator who owns that decision.

## 1. The observation

`CRYPTO_LIVE_EXECUTION_V0.1.md` states Gate 0 as an item on the **operator go-live checklist**:

> **Gate 0 — earn confidence (before any live money)**
> - [ ] Paper trading **by this runtime** shows positive expectancy over a sustained window.
>       Check with `python -m runtime.mvp_runtime.crypto.dashboard`.

The heading above it reads: *"Every step is Thomas's; Claude does not run these, does not handle
real keys, and does not enable live trading."* The check is prescribed as something a **person
performs on the dashboard**.

#400 observed that `feedback.live_candidate_eligible` computes that judgement every cycle and
that nothing read it, and #409 wired it into `live_entry` as refusal 2b. That was a correct
reading of a real gap — but it converted a checklist item into an automatic code gate, which is
a different thing from what the contract specifies.

## 2. The argument for removing the refusal

**The promotion door already enforces the rule Gate 0 restates, and it enforces it per
strategy.** A live entry can only ever come from `shared_route` — the paper step's own routing
result — and `paper.route_entries` considers nothing outside `OCCUPYING_STATUSES`. So a strategy
reaches the live leg only if it:

1. was minted by the factory,
2. cleared the `robustness` gate,
3. was **promoted by an operator approval**, and
4. has not since been demoted by the `lifecycle` ladder.

"Earned confidence" is what steps 2–4 are. Gate 0 adds a fifth condition of a different kind —
*the pool's aggregate forward paper P&L must be positive* — and applies it **pool-wide**, so one
strategy's losses refuse every other strategy's entries. #400 §5 deferred per-strategy
eligibility on the grounds that the median strategy had 2 closed trades; the effect is that the
only version that shipped is the one that cannot distinguish between strategies at all.

**The failure mode this produces is not hypothetical.** Measured 2026-08-01: all **86** rows
holding Gate 0 shut came from lineages `lifecycle` had already SUSPENDED, and **zero** from one
that could still route. The ladder had done exactly its job — judge a strategy on its paper
losses and retire it — and the losses of strategies that no longer exist were still gating a
real-money door. (PR #411 fixes that specific defect by scoping the population; it does not
address whether the gate should exist.)

## 3. What is proposed

Remove the **refusal**, keep the **measurement**.

- `live_entry.plan_live_entry` drops the `live_candidate` parameter and the
  `LIVE_ENTRY_NOT_LIVE_CANDIDATE` reason code;
- `live_route` stops threading it;
- `cycle` stops passing it and instead records `live_candidate_eligible` on the cycle record, so
  the number stays in the ledger and on the dashboard where the contract puts it.

The exact diff is in §7.

## 4. What still refuses a live entry after this

Nothing about the execution-safety doors changes. Assuming PR #411 also lands (the loss breakers
metering live outcomes rather than paper):

| door | state today |
|---|---|
| promotion + not-demoted (`OCCUPYING_STATUSES`) | the entry condition |
| reconciliation with the venue | enforced |
| capacity (2 open, 1 per symbol) | enforced |
| venue filters, bracket, `cost.MAX_ENTRY_COST_R`, sizing | enforced |
| **venue-sourced daily loss limit** | **20 USDT** |
| registered budget | 75 USDT/order, 2 orders/day, 120 USDT max open |
| live's own weekly / drawdown / consecutive breakers | accumulate from the first live outcome |
| kill switch | operator, immediate |

**Worst-case realized loss per day is bounded at the registered 20 USDT**, and maximum
outstanding exposure at 120 USDT.

## 5. Objections

- **"This removes the only evidence-based gate before live money."** True as stated, and it is
  the substance of the decision. The counter is that promotion *is* an evidence-based gate — a
  robustness-scored backtest plus an operator approval — applied per strategy, where Gate 0 is
  applied to a pool aggregate.

- **"Promotion has already been shown not to predict forward results."** This is the strongest
  objection and it is measured, not theoretical: the pool retired on 2026-07-31 passed the same
  promotion criteria and then produced **-0.5R/trade net over 86 closed trades**. Passing the
  promotion bar demonstrably does not imply a forward edge on this machine. What bounds the cost
  of being wrong is §4's envelope, not the gate.

- **"Live has never traded, so its own breakers are inert."** True — they allow at zero rows by
  design (`run_risk_guard`'s docstring). The daily loss breaker is the exception: it is
  venue-sourced and binds from the first fill.

- **"The checklist item is not satisfied."** It is not, and this proposal does not claim it is.
  It proposes that whether it is satisfied is the operator's judgement to make on the dashboard,
  which is what the contract says, rather than a code refusal that also cannot distinguish a
  retired strategy's losses from a routable one's.

## 6. Residual risks

1. **Live begins trading on the next matching candidate.** On the current pool that means
   strategies with **0 closed paper trades** — the 1h/4h pool promoted 2026-07-31
   (`approval_e976256bfeac2581cd86`) has produced no settled outcome yet.
2. **Nothing then stops a losing streak before live's own breakers accrue**, except the daily
   20 USDT venue limit and the 2-orders/day cap.
3. **The checklist item becomes unenforced.** If the intent is that it stays enforced, the
   correct change is the opposite of this one: keep Gate 0 and make it per-strategy.

## 7. The change

```diff
--- a/runtime/mvp_runtime/crypto/live_entry.py
   * delete the `2b. **live candidate (Gate 0)**` paragraph from the module docstring
   * delete `CANDIDATE_REFUSED = "LIVE_ENTRY_NOT_LIVE_CANDIDATE"`
   * delete the `live_candidate: Mapping[str, Any] | None,` parameter and its comment
   * delete the `# 2b. Gate 0.` block (the `if not isinstance(live_candidate, Mapping) ...`)
   * delete `"CANDIDATE_REFUSED",` from `__all__`

--- a/runtime/mvp_runtime/crypto/live_route.py
   * delete `live_candidate: Mapping[str, Any] | None,` from both signatures
   * delete both `live_candidate=live_candidate,` forwards

--- a/runtime/mvp_runtime/crypto/cycle.py
@@ run_live_leg(
         verdict=live_verdict,
-        live_candidate=report,
         symbol=symbol,
@@ the cycle record
         "report_status": report.get("status") if report else None,
+        # Gate 0's answer, recorded rather than enforced. `None` when the report could not be
+        # produced, which is a different fact from False and has to stay distinguishable.
+        "live_candidate_eligible": report.get("live_candidate_eligible") if report else None,
```

Tests that assert the refusal have to go with it — the Gate 0 cases in
`tests/test_mvp_runtime_crypto_live_entry.py`, `test_the_live_leg_is_handed_gate_0` in
`tests/test_mvp_runtime_crypto_cycle.py`, and the `live_candidate=` arguments in the helpers in
`tests/test_crypto_live_path_rehearsal.py` and `tests/test_mvp_runtime_crypto_live_route.py`.
A replacement test should pin the new property: **`live_candidate_eligible` is recorded on the
cycle record and does not refuse.**

Applying it:

```
cd /root/thomas_agent && .venv/bin/python -m pytest tests/ -q
```

must be run in a worktree, not the deploy checkout — `state_guard` refuses a suite run against a
tree a live runtime is writing.

**Rollback** is `git revert` of the resulting commit; the gate is pure code with no record, no
schema and no stored state, so nothing needs unwinding.

## 8. Decisions requested

1. **Remove the Gate 0 refusal, or keep it?**
2. If removed — should `live_candidate_eligible` ride on the cycle record (recommended), or be
   dropped from the runtime entirely?
3. If kept — should it become **per-strategy** instead of pool-wide (#400 §5's deferred item),
   which addresses the same complaint without removing the gate?
4. Whether the readiness board should grow a row for it either way, since today it shows
   ineligibility nowhere (#400 §7-4, still open).

## 9. Relationship to the open work

- **#411** scopes Gate 0's population to routable lineages and takes the loss breakers off the
  paper book. It is independent of this decision and stands on its own either way.
- **#400** is the proposal this would partially reverse. Its finding — that the judgement existed
  and nothing read it — remains correct; what is in question is whether *reading it as a refusal*
  is the right consumer.
- **#405 / #410** become less relevant to the live door once #411 lands, since the drawdown
  figure they re-base is no longer in the live verdict.

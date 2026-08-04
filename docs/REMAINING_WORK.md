# Remaining Work — canonical to-do list

**This is the single place to answer "what's left to build?" from any machine.**
It is committed to git on purpose: per-machine memory does not travel between computers,
so the durable hand-off lives here. On a fresh machine: `git pull`, then read this file.

Last updated: **2026-08-04** — **the live leg holds a position again.** The stop refusal that has
headed section C since 2026-08-02 is resolved (#460, the confirm race); observed 05:00Z, an
ETHUSDT SHORT opened 00:13:57Z with both bracket legs `placed: true, status: NEW`. Section C is
rewritten around what that leaves: **no live trade has ever closed** — `live_outcomes.jsonl` does
not exist — so the exit path and the #470–#472 naked-close accounting are both undemonstrated,
and the first live close is the next thing that answers anything.

Earlier the same day, re-measuring **section F** on a candidate store
that has doubled since it was written. Its question — whether this venue's fee schedule permits a
fast strategy at all — is answered **yes**: 1h now pays at the median (+0.0241R/trade against
−0.0185R) and 15m's deficit has more than halved, almost all of it the `stop_atr` floor from #420
finally reaching the generations minted after it. **What binds is no longer cost.** Zero of the 474
candidates carrying the current basis survive their own holdout, and the promotion board reports
`0 promotable` without being able to say that. Section F carries the numbers and what is open.
**It does not outrank section C**, which is still what to read first on arrival.

Also 2026-08-04: the board can now say it (#477 — `promotable_backlog` returns a refusal
partition), and **section F1** diagnoses why nothing survives. Three explanations are ruled out
by measurement; what is left is that **41–47% of mints produce a holdout too shallow to
confirm**. Paired against its own parents — 394 pairs, every one resolvable — a fused child
closes **0.51×** their trades and is indistinguishable from them in and out of sample, so
fusion reproduces its parents and halves their evidence. The change that implies is written
out there with its numbers and deliberately not made: it trades exploration for judgeability.
Two claims from F1's first pass are corrected in place rather than deleted — both were
population comparisons the paired test overturned, and the way they were wrong is the useful
part.

And a new **section H**, added because the equity-perp lane was **not in this file at all**
while three PRs of it merged — a reader starting here, which is what this file tells them to
do, could not have learned it exists. It is code-complete as far as it goes and **runs
nothing**: no `hyperliquid` grant exists on this machine and the selector fails closed at
selection. **`S0`'s two `〔확인 필요〕` markers were answered the same day** — the strength stays
provisional by decision, which draws the line at live orders and leaves read-only S1 untouched,
and the question-reduction holds. So the grant is the only step left that is not minutes of
work. What waiting costs is measured there, because the venue's window rolls.

Earlier: **2026-08-03** (`main` = `44c9b36`), handing off to another machine. **The headline
is at the top of section C and nothing else in this file outranks it:** the runtime placed its
first two autonomous live orders on 2026-08-02 and the protective stop was refused both times, so
it could not hold a position. **That is resolved as of 2026-08-04** — the cause was the confirm
race (#460), and the runtime is holding an ETHUSDT SHORT opened 00:13:57Z with both bracket legs
`placed: true, status: NEW`. What is open moved with it: **no live trade has ever closed**
(`live_outcomes.jsonl` does not exist), so the exit path and the naked-close accounting from
#470–#472 are both undemonstrated. The one build item — **nothing counted repeated bracket
failures** — is closed by #439: the loop stops itself after two, instead of running on the daily
order budget's midnight refill.
Rollback tags `rollback-pre-<PR#>` are on the Docker host and do not travel.

This paragraph said "the deployed image is `320475b`" when it was written and that was already
wrong a few hours later — the running image carries #434, which merged after it. **The deployed
image is not a fact this file can hold**, for the same reason the canary count is not: it changes
whenever any session deploys, and several do. Ask the host, and ask it about the image the
containers are *running* rather than the one tagged `latest`, which a concurrent build moves
without deploying:

```
docker inspect thomas-scheduler --format '{{.Image}}'
docker images thomas-agent-runtime
```

If those two disagree, read CLAUDE.md's tagging rule before building anything — and note the case
it does not cover, hit 2026-08-02: the running image can be absent from the image store entirely,
in which case there is nothing to tag and the rollback point has to be a **commit**.

Also 2026-08-03: a whole-codebase review is recorded as the new **section G**, and the per-machine
hand-off below is made concrete — same reason, the work is moving to a different computer. Two of
the review's findings were fixed rather than recorded (the scheduler's cadence drift, #431; the
schema-cache check, this change) and one was removed outright with the prediction-market lane
(section A). Three stay open, G1 the one worth doing first. **None of it outranks the paragraph
above** — a live leg that cannot hold a position is the thing to fix on arrival.

Earlier: **2026-08-02** (`main` = `f7a9356`), after asking why the promotion board reported
**0 promotable with 900 candidates on file**. The answer was three filters in series, and the
middle one is the finding: **gross edge is nearly flat across the ladder (+0.09R at 15m to +0.15R
at 4h) while cost varies 4.4× (0.28R to 0.06R)**, so what separates the timeframes is the
denominator — 1R is `stop_atr` × ATR against a fixed ~10–16 bps of friction — and not the signal.
Decomposed, **taker fee plus slippage is 94% of all cost at every timeframe and funding is
0.2–4%**, which corrects this file's own emphasis below. Four changes followed: the factory stopped
minting into the two bands that cannot pay for themselves (#420), the judgeability cap stopped
hiding the operator's own pool (#422), and the Gate 0 board learned to say *why* its sample is zero
and to warn before an acknowledgement lapses (#416). The conclusion drawn here — that friction at
15m is 0.28R against 0.09R of gross edge, a gap no stop multiple closes — was judged on the day
#420 merged and **did not survive the generations minted after it**; see **section F**, re-measured
2026-08-04.

Earlier: **2026-07-31** (`main` = `128ed20`), adding **section E** — deferrals that were
measured and then deliberately left alone, so the measurement does not have to be redone by the
next person who greps.

Earlier the same day (`main` = `6c5999a`), after a QA pass over the whole build found
three defects that a green suite and a green release gate both showed as fine, because each sat
in a place neither looks: **the readiness board's own "authoritative" line was wrong** (#381),
**the PM1 exit report could no longer read its own evidence** (#383), and **Core activation died
in the worktree this repository tells you to use** (#386/#388). What they have in common is
worth more than the fixes: none is a computation error, and all three are a *reader* — a status
line, a report, a recovery path — disagreeing with the system it describes.
The same pass then found two more of that shape and one correction worth reading: the parity gate
was checking the **superseded** PermissionDecision schema and not the live one (#393); a policy
bump had left **314 stored records unable to satisfy their own schema** (#394); and the claim that
the bump had voided ten outstanding approvals was **wrong** and is corrected in place (#396) —
five were never decided and five had expired on their own timers before the bump.
Previously **2026-07-30** (`main` = `0dc36f3`), after the sheet was caught offering one market
four partners of which three were impossible (#371) — an order-dependent trap rather than mere
noise, because confirming an impostor first refuses the correct pairing forever.
Earlier the same day (`main` = `c45d99b`), after **PM1 started running** — now 74 confirmed
groups, 62,039 readings, 3.16 of the 14 days required — which rewrote section A from "built, not
yet run" to a window with numbers in it, and closed four defects the run itself exposed: Gamma
pagination was skipping two thirds of the head it claimed to read (#318), a touch nobody could
trade counted as an opportunity (#321), a quoted Polymarket read returned a market missing five of
its fields (#348), and the rule behind `is_opportunity` had changed three times under one version
string (#350).
Previously **2026-07-29**, after an architecture review of the crypto stack and the seven
fixes it produced (branch `claude/auto-trading-system-review-x9p6d5`). Two changed what the
evidence *means* and are written up in section C: the backtest now charges **funding**, and
Gate 0's `live_candidate_eligible` now reads the paper record **net of costs** rather than
gross. Three were defects the review found rather than performance work — a live position's
holding clock advanced once per timeframe instead of once per bar (4x too fast on a
four-timeframe symbol), a 429 was reported as a timeout while the fan-out kept knocking toward
the 418 ban, and the fan-out's scarce live slots were arbitrated alphabetically. Two were pure
cost: the fan-out asked each symbol-scoped question once instead of four times (115 venue calls
-> 45) and the factory builds its replay frame once instead of once per spec (20.5s -> 5.5s at
the 15m window).
Previously the same day (`main` = `01854b3`), after the front-desk prompt started naming the
fields its parser requires (#336) — which opened a new line in section B, because establishing
that took the production front desk down for a quota reset — and the `NO_SIZE` gate's provenance
was corrected to say it closes a hole rather than repairs damage (#334).
Earlier the same day (`main` = `d6b65df`), after the canary evidence learned to prove its
own size (#328) and the board learned to say when it cannot (#332), `/kill` started reaching the
control state during an analysis instead of after it (#329/#333), a live entry started announcing
itself (#331), and the live leg started enforcing the strategy's time exit (#330).
Previously the same day (`main` = `36fd1f7`), after the live-trading grant was removed and
`MVP_LIVE_TRADING=real` became the whole gate (#320), the `execution.live_trader` definition stopped
describing a build that had shipped (#323), and section D was re-audited against the code.
Previously **2026-07-28** (`main` = `279c233`), after cycle routing landed (#302), the cost
basis became a promotion gate rather than a warning (#309), the per-fill fee instrument was built
(#313) and the last unguarded state writer got a door (#315). Before that, on the same day: the
canary path ran end to end for the first time and the defect wave that followed it
(#227/#228/#231/#232/#233/#246), the evidence corrections (#247/#249/#251/#254/#257/#260) and the
board's sample verdicts (#292/#304).
Previously **2026-07-27** (predmarket wave, second pass) and **2026-07-26** (the review round).
Every claim below was re-checked against `main` and against the code it describes, not carried over.

> **The one thing to read first:** an order path **exists**, and so does the **executing leg**
> that opens, protects and closes a live position. `financial_transaction_execution_implemented`
> is `true`. Live trading still cannot start, and the reasons are now entirely structural rather than
> "the code is missing": **no autonomous entry point may import the order path** (a test enforces it,
> and the readiness board reports it as a computed row), `financial_executor_enabled` is `false`,
> the clean-canary evidence threshold is not met **on any machine this file can speak for** (the
> count is per-machine state — ask the board, below), and every egress needs the operator's
> `MVP_LIVE_TRADING=real` opt-in, order key, confirmation phrase and registered budget.
>
> ⚠️ **Two of those reasons weakened on 2026-07-28 and this banner is corrected rather than
> rewritten.** "No autonomous entry point may import the order path" became "exactly one module
> may" when cycle routing shipped; and the per-machine `live_trading` grant was removed by
> Thomas, so `MVP_LIVE_TRADING=real` is the entire gate. The reasons live trading cannot start
> are still structural, but there is one fewer of them and the remaining ones are all operator
> state rather than code.
>
> **The canary count is per-machine.** On the machine that placed them the board now reads
> **4/4** (2026-07-28); on a fresh checkout it reads `0/3`. Ask the machine
> (`python -m runtime.mvp_runtime.crypto.live_readiness`) rather than trusting a number here —
> that has not changed and will not.
>
> **One of those four is worth knowing about before it is counted as evidence.** The canary of
> 2026-07-26T11:03Z filled at the venue and its audit event was built and never appended: step 8
> called `LedgerStore(None)` and the process died between "the order is at the venue" and "here
> is what the venue said" (#228). Its gap was recorded late and forward on the chain, marked
> `LIVE_ORDER_REPORT_RECOVERED`, because `AUDIT_RECOVERY_CONSOLE_V0.1` is explicit that a trail
> is diagnosed and never repaired (#232). Under the code that now exists that run would have
> exited BLOCKED. Whether it should still count toward the threshold is an operator decision,
> not a number this file can settle.
>
> **Cycle routing landed 2026-07-28** (`crypto/live_route.py`): the executing leg now has one
> autonomous caller, and `AUTONOMOUS_ROUTING_WIRED` is `True`. That was the last *build* item.
> What stands between here and an autonomous live order is now entirely operator state — the
> `MVP_LIVE_TRADING=real` opt-in, the confirmation phrase, the registered budget, the canary
> evidence — each of which the readiness board reports as its own computed row. **Wired is not
> permitted**, and the board deliberately does not fold the routing row into `ready`.
>
> **The grant came off the same day** (Thomas, 2026-07-28): the live surface is gated by the env
> opt-in alone. The gate no longer expires, so nothing turns live trading off but the operator —
> and the halt that acts on a *running* scheduler is the console `kill` verb, not the env var.
>
> **And that halt now arrives during an analysis rather than after it** (#329, 2026-07-29). The
> sentence above was true and incomplete: the operator loop is the only process that receives
> Telegram and it does not poll while it drains, so for the length of an analysis `/kill` existed
> **nowhere in the runtime** — and the control state file is what `live_route` re-reads in the
> other container immediately before a live entry. This loop's responsiveness had quietly become a
> dependency of the money path. A mid-run peek that reads without claiming now writes the halt at
> the next stage boundary. It does **not** stop the running analysis; that is decision K4, open.

Keep it current — when a milestone ships, tick its box or delete it here in the same PR.

Authoritative detail for each item lives in the linked roadmap docs; this file is the index.

Its counterpart is [`BUILD_HISTORY.md`](BUILD_HISTORY.md) — what has already been **delivered**, and
why each piece is shaped the way it is. The two together are the whole picture; `CLAUDE.md` states
the rules and deliberately claims no status, so that status has as few owners as possible.

---

## In-flight PRs

```
gh pr list
```

**The list that used to live here is deleted, on its own recommendation.** Two passes running it
reached the same conclusion in writing — "this section was accurate when written and wrong before
it merged", "the next pass should consider deleting it rather than refreshing it again" — and then
refreshed it anyway. `main` advances several times an hour from parallel machines; a hand-written
snapshot of open PRs is stale before the PR carrying it is reviewed.

What the section was actually for is worth keeping, and it is one line: **before starting anything
below, run `gh pr list` and confirm nobody else has taken it.** That has cost real work twice —
#229 duplicated a check #268 had already merged after falling 131 commits behind, and #175 wrote a
second order adapter beside the one already on `main`, which is the exact ambiguity ("which code
can send an order") this repo spends most of its rules preventing.

The lesson generalises past PRs: **this whole file is a claim about a `main` that has since
moved.** Everything below was true when written. Check before acting on it.

---
## A. Prediction-market trading — **CLOSED 2026-08-02, not deferred**

This track is over. Prediction-market trading is not a lane this project may operate under
**Korean domestic regulation** (Thomas, 2026-08-02), so the whole PM-series was **removed**
rather than paused: `runtime/mvp_runtime/predmarket/` (6,746 LOC), its nine test files, the
`pm_scan` scheduler kind and both its cadences, the `/pred` console verb, the five venue
selectors in `docker-compose.yml`, the roadmap, and the 669 MB observation store.

**Deleted, not disabled, and that distinction is the point.** A disabled capability is one
grant away from running, and this one must not be reachable by an operator who finds a stale
`MVP_KALSHI_MARKET_DATA` in `.env` and wonders what it does.

**What PM1 had measured when it closed** — kept because it cost 5.7 days of a 14-day window
and is the only thing this section can still be useful for. 205,079 readings across 76
confirmed groups; ~0.995 pricing coverage; the fee-adjusted detector's own verdict was still
`INSUFFICIENT_WINDOW` at closure, so **PM1 never reached a go/no-go on whether the edge was
real** — the window was cut short by the regulatory finding, not by a negative result. Anyone
revisiting this must treat the question as unanswered rather than answered "no".

**The removal exposed a defect worth carrying forward** (fixed in the same change): the
scheduler's `_execute` had no final `else`, so a schedule of any unrecognised kind fell
through to the analysis pipeline and ran a real model call on its `request` text. Nothing
could reach it while the writer and reader of the store were the same version — removing a
kind is exactly when they stop being. An unknown kind is now refused by name.

The out-of-scope list that lived here (PM4 bounded autonomy, market making, directional/news
trading, leverage, US-context Polymarket access) is **moot**: the lane above all of them is
closed.

---

## B. LLM orchestration (M-series) — request → tiered model → verify → deliver

Roadmap: `docs/LLM_ORCHESTRATION_ROADMAP_V0.1.md` (on `main`).
**This track is code-complete.** Both open lines below are operator actions rather than builds:
M5b (a standing habit) and a provider key that is not the live operator's (a thing to obtain).

- [x] **M0** env cleanup (done 2026-07-24).
- [x] **M1** difficulty triage 상/중/하, observe-only — merged (PR #145).
- [x] **M2** difficulty → OpenRouter tier model — merged (PR #149). Per-tier grants + model slugs
      stay the local operator step; until minted, every run degrades cleanly to the base chain.
- [x] **M3** verify-fail → bounded LLM revision loop (opt-in `--revise`, hard cap 1) — merged (PR #150).
- [x] **M4a** crypto: second-pass win-rate + risk-reward ranking — merged (PR #148).
- [x] **M4b** crypto: the strategy proposer on a schedule (`crypto_propose` kind) — done. Per-run
      cap (existing) + unreviewed-backlog cap (distinct accepted-but-uninstalled families; skip +
      audit `skipped_backlog_full:N` past 12, 30-day window). Installing a family clears its
      backlog slot. Also registered the proposal ledger kind (a latent persist bug).
- [x] **M5a** correction → working-memory CANDIDATE — done. A successful M3 revision (REVISE→PASS)
      or a `/feedback bad <note>` mints a correction candidate (ALLOW-tier, audited on the
      memory-event stream, CANDIDATE-only). `runtime/mvp_runtime/memory.py`
      (`build_correction_candidate`/`build_learning_event`), wired in `pipeline.py` +
      `operator_feedback.py`.
- [ ] **M5b** Thomas promotes useful correction candidates to VALIDATED — **standing operator habit,
      not a build item.** The door already exists (`/memory` to list, `/promote` to approve, or
      `scripts/promote_memory_candidate.py`); nothing here is waiting on code. It stays open on
      purpose: M5a captures corrections as unverified `[M#]` candidates and M5c only feeds back what
      was promoted, so the loop produces nothing until someone says yes. That gate is the feature —
      it is what keeps a bad correction from entrenching itself as standing guidance.
- [ ] **A provider key that is not the live operator's** — for measuring anything against a real
      model. Not a build item and not urgent, but it has already cost something once, so it is
      here rather than only in `BUILD_HISTORY`.
      Establishing that the front-desk prompt never named the fields its parser requires (#336)
      took ~60 live calls, and those calls went through the **production** front desk's key.
      They exhausted the free-tier quota and left the live channel degraded until it reset —
      fail-closed, `/verbs` unaffected, no message lost, but degraded — and the confirming run on
      the text that actually shipped never completed. So the shipped prompt's 8/8 is measured on
      text equivalent in content but not byte-identical, and the reason it stayed that way is
      this missing key rather than any judgement about whether it was worth confirming.
      **The finding needed the live provider**: every test uses the scripted one, and a response
      discarded as `MALFORMED_RESPONSE` degrades the channel to the plain queue safely and
      silently, so nothing in the suite can see it. That makes live measurement a thing this
      repo will want again — and doing it on the operator's own key means the next one takes
      the front desk down the same way.
      Two adjacent questions belong with it rather than on their own: whether
      `MVP_FRONTDESK_PROVIDER` should be a failover **chain** like `MVP_HOSTED_PROVIDER` (under
      those 429s `FailoverProvider` would have fired — `PROVIDER_UNAVAILABLE` is exactly and only
      what it fails over on, unlike the `MALFORMED_RESPONSE` case where it provably would not
      help), and whether a measurement harness should be pointed at a separate provider entirely.
- [x] **M5c** a promoted VALIDATED correction feeds back as a correction to *apply* (`[V#]`,
      distinctly framed) — done. `promote_candidate` carries the correction marker forward;
      `worker._validated_context` frames it. Known limit: only revision-path corrections are
      promotable (they carry origin); feedback-path corrections stay `[M#]` until origin can be
      reconstructed from the delivered run.
- [x] **M5d** repeated identical corrections surface at the programization review as a read-only
      correction lineage — done 2026-07-25 (option C, reuse-first: `correction_lineage_for_pattern`
      + `programization_cli lineage <pattern_id>`; no new schema/counter). Codifying stays the
      existing operator-gated programization flow.

---

## C. Crypto live execution — the governance packet + the order code

> ### ⚠️ LIVE TRADING IS ARMED, AND AS OF 2026-08-04 IT HOLDS A POSITION WITH BOTH PROTECTIVE LEGS RESTING
>
> **Read this before anything else in this section.** Live trading is armed and reachable and has
> placed real orders without a person present. **The stop refusal that opened this section is
> resolved** — the paragraph below said the next attempt would answer it, and it did.
>
> Observed on the host 2026-08-04T05:00Z, from local records only:
>
> | | |
> |---|---|
> | position | ETHUSDT **SHORT** 0.022 @ 1859.14, notional 40.90 USDT |
> | opened | 2026-08-04T00:13:57Z — **held ~4.75h**, `holding_candles: 2` of a 4h spec |
> | `live_opened.bracket[0]` | SL @ 1900.5 — **`placed: true`, `status: NEW`** |
> | `live_opened.bracket[1]` | TP @ 1776.71 — **`placed: true`, `status: NEW`** |
> | `live_bracket_failures.json` | `consecutive: 0`, last failure 2026-08-03T04:28:58Z |
>
> The breaker was cleared 2026-08-03T15:51:37Z with the written reason *"#460 confirm-race fix
> deployed; cause addressed"*, and nothing has tripped it since. `placed: true` on the stop leg is
> the exact field that read `false` in the incident below, so this is the measurement that
> paragraph asked for rather than an inference from silence.
>
> **What this does NOT yet show, and the distinction is the whole remaining risk.** No live trade
> has ever *closed*: `live_outcomes.jsonl` **does not exist** on this machine. The entry and the
> bracket are demonstrated; the **exit** path — a stop or a target actually filling, and the
> outcome reaching the ledger — has never run end to end. Nor has the naked-close accounting from
> #470–#472, which merged *after* the last naked close, so it has never fired on a real one. The
> first live close is the next thing that answers something, and it is the one to watch for.
>
> **One position is not a fixed system.** The cause was addressed and one bracket rests; that is
> evidence, not a warranty. Two consecutive naked entries still shut the door
> (`LIVE_ENTRY_BRACKET_BREAKER_TRIPPED`), which is what makes it safe to let the next one run.
>
> ---
>
> **The incident this section was written for — 2026-08-02, kept as history:**
>
> Two entries, both ETHUSDT 4h SHORT, both `status: ENTRY_NAKED_CLOSED`:
>
> | when (UTC) | entry fill | closed at | notional |
> |---|---|---|---|
> | 2026-08-02T08:03:48Z | 1876.35 | 1876.36 | 58.17 USDT |
> | 2026-08-02T08:20:44Z | 1875.34 | 1875.35 | 58.14 USDT |
>
> **What happened each time.** The MARKET entry filled and reconciled. The `closePosition`
> `STOP_MARKET` protective leg came back `ORDER_REJECTED` (`placed: false`). The resting maker TP
> **did** place. `live_leg` then did exactly what it documents — cancelled the surviving TP and
> closed the exposure — so the book ended flat (`position: null`) and the account shows
> `positions: none`. **Cost: 0.12 USDT, all of it fees** (`realized 1d: pnl -0.00, fee -0.12`).
> The safety path is not in question; it worked twice.
>
> **The cause is UNKNOWN, and that is itself the finding.** Ruled out by inspection, so do not
> re-do this: the request shape matches the venue contract exactly (`stopPrice` + `closePosition`
> only — no `price`, no `quantity`, no `reduceOnly`; the `price` in the stored record is a local
> field, not sent); every ETHUSDT filter passes (`tickSize` 0.01 and 1907.98 is an exact multiple;
> `PERCENT_PRICE multiplierUp 1.05` puts the ceiling at 1970.17; `MIN_NOTIONAL 20` does not apply
> to a quantity-less order; `MAX_NUM_ORDERS 200`); `workingType: MARK_PRICE` is deliberate; and the
> entry was `RECONCILED` before the bracket was attempted, so it is not an ordering race.
>
> **Why it could not be determined:** the venue's own message was never persisted. The record
> carried `error: "ORDER_REJECTED"` — the same string for every rejection — and the container has
> since been recreated, so the logs are gone. #426 closed exactly that gap (`error_detail`, the
> venue's numeric code and text) and **is deployed as of 2026-08-02T15:59Z**.
>
> **So the next attempt answers it — and it did.** That was the agreed next step, chosen over
> guessing, and it is the reason this section could be closed by reading a record rather than by
> re-deriving a cause. The answer was the confirm race, fixed in #460; the bracket has rested
> since (see the block at the top of this section). The instruction that produced it, kept
> because it is the reusable part: watch
> `.runtime_governance_state/crypto/live_order_counter.json` for a new date key, then read
> `live_opened.bracket[].placed` and `.error_detail` on that cycle record.
>
> **This path had never run before.** The four canary orders are entry-only MARKET by
> construction (`place_canary_order`), so `STOP_MARKET closePosition` was executed for the first
> time on 2026-08-02 and failed on first use — the same shape as the six defects the canary path
> turned up, not a regression.
>
> **The one thing that was a build item — CLOSED, PR #439.** Nothing counted repeated bracket
> failures: `LIVE_BRACKET_FAILED` and `ENTRY_NAKED_CLOSED` appeared nowhere outside `live_leg.py`,
> so the loop *signal → fill → refuse → close* was bounded only by the registered budget's
> `2 orders/day` — about **0.12 USDT a day, indefinitely**, resuming at every UTC midnight. Small,
> but a bleed with no stop, and while it ran **live trading could not work at all** because every
> position was closed the moment it opened.
>
> Now counted durably in `live_bracket_failures.json` and read as a door in `plan_live_entry`
> (`LIVE_ENTRY_BRACKET_BREAKER_TRIPPED`). Two consecutive naked entries and new entries are
> refused; a bracket that actually rests ends the streak, an entry that never reached the bracket
> does not. It deliberately does **not** reset at UTC midnight — expiring on the same clock as
> the budget it bounds would bound nothing — so the only ways back are a working bracket or
> `python -m scripts.clear_bracket_breaker --cleared-by … --reason …`, which demands a written
> reason because the failure it follows is by definition one nobody had explained yet. The
> venue's own `error_detail` (#426) is copied onto the breaker record, which outlives the
> container that holds the logs. Shown on the readiness board as `bracket_breaker`.
>
> **This did not fix the rejection**, and was not meant to — #460 did. What this bought was that
> the next attempt was the *last* one that could cost anything if the answer had been "still
> broken", and it is why letting that attempt run was the cheap move rather than the brave one.
> The breaker stays exactly as it is: it is what makes the position at the top of this section
> something to observe rather than something to worry about.
>
> Evidence lives on the Docker host only (gitignored): the cycle records in
> `.runtime_governance_state/runtime_ledger/records.jsonl` (search `live_opened`), the audit
> events (which cover the ENTRY only — they do not carry the bracket rejection), and
> `live_order_counter.json`. A fresh machine has none of it.

Decision record: `docs/runtime-contracts/LIVE_EXECUTION_GOVERNANCE_V0.1.md` (decided 2026-07-23;
**the governance packet is fully implemented** — the last item, the LP4-coupled flag, flipped with
#184 on 2026-07-25; that doc's own step table remains the authority). Status:
`docs/runtime-contracts/CRYPTO_LIVE_EXECUTION_V0.1.md`.

**What is left in this section is no longer governance, no longer plumbing, and as of 2026-07-28
no longer a build decision either — it is operator state.** Cycle routing landed (below), so the
"one build decision" this paragraph used to name is gone; what remains is the per-machine
checklist the readiness board computes row by row, and none of it is code anyone can write here.

That inverts the old caveat rather than removing it. The canary row used to enable nothing
because the step it fed — cycle routing — was deliberately unbuilt; now that step exists, so the
canary evidence does gate something real. **Wired is still not permitted**: the routing row is
deliberately not folded into `ready`, and without `MVP_LIVE_TRADING=real` the leg returns
DISABLED having read no account and opened no socket.

*(That sentence said "with no `live_trading` grant" until 2026-07-29 — written on the 28th and
outlived by its own subject within a day, when Thomas removed the grant. Left visible rather
than silently swapped: this file's recurring defect is not being wrong, it is describing a door
that has since moved, and the banner above already carried the correction while this paragraph
did not.)*

**Everything above defers to the board, and on 2026-07-31 the board's own most emphatic line was
found to be wrong** (#381). Beneath the twelve computed rows it prints a dry-run of the real
guard, which its code calls *"the authoritative answer: whatever the rows above say, this is what
would actually happen"* — and that call omitted `allowed_symbols`, whose default is EMPTY and
blocks every symbol. So it reported `no symbol allowlist backs this order` on **every machine,
forever**, and told the operator to register the budget they had already registered.

The money path was never affected: both write doors (`live_route`, `place_canary_order`) read the
scope off the same budget the caps come from. Only the reader was omitted — which is the
dangerous direction here, because on this host, with the opt-in set and routing WIRED, the line
an operator would read before concluding *live trading is stopped* said it could not place an
order while it could.

**What that means for how this section is read:** the board is still the authority for what is
live on a machine, and it is still the only thing that can answer a per-machine question. But
"the board says so" was load-bearing for a line that no test could contradict — every dry-run
assertion in the suite ran on an empty `tmp_path`, where a block is correct, so the suite could
only have failed if the bug were fixed. A computed row is not self-verifying merely by being
computed.

**The canary row cleared on 2026-07-28, and it changed less than it sounds.** Running that path
end to end for the first time turned up six defects that had been sitting in code nobody had
executed — the documented opt-in value the gate never matched (#227), a crash between the venue
and the audit chain (#228), `--root` honoured everywhere but the account read (#231), the state
guard covering one CLI of seven (#233), and a `max_daily_order_count` that refused nothing
because the only door able to place an order never counted its own (#246). None were regressions.
Code that has never run is not working code; it is untested code, and this is what that looks
like when it is finally run.

**The evidence the routing decision would rest on moved too, and in the less comfortable
direction.** The promotion listing showed a stale top tier — twelve lineages rated `ROBUST` under
a rule that predated the out-of-sample gate, ranked above thirteen that had actually survived
unseen bars (#249). The backtest charged 2.5 bps taker where this account is charged 5.0, measured
(#257); re-deriving the 224 affected candidates at the real rate flips **thirteen from positive
expectancy to negative** (#260). And the board's own verdict on this runtime's record is now
`판단 불가` rather than a number: 60 closed trades at +0.08R, 95% interval `[−0.32, +0.48]`
(#292/#304).

**The cost basis became a door on 2026-07-28 (#309), because warning about it was not working.**
`--list` counted the split and printed "rank tiers are comparable within a basis, not across
them" — and then `rank_candidates` sorted straight across them, on keys (`champion_score`,
`edge_quality`, `expectancy`) every one of which is read off evidence scored at the old rate.
Same defect class as #249 in the same file, fixed the same way: order on the property instead of
describing it. The basis is now a tier that leads the sort, and `assert_promotable_cost_basis`
refuses stale evidence at **both** write doors — the ask as well as the install, since an
approval Thomas answers for a promotion the next step was always going to block spends his
attention on nothing. `--allow-stale-cost-basis` is the escape and the ledger records it.

The tier is a **direction** test, not equality, and that distinction is the whole design. Rows
scored at taker 5.0 with no maker leg charged their take-profit exit taker-plus-slippage where
the current model charges maker 2.0 and none — too *pessimistic*, so refusing them would have
made the escape hatch the normal door. What is refused is evidence scored more **cheaply** than
the venue charges, plus evidence that cannot say which way it errs at all. Measured on the
machine that has the store, 2026-07-28:

| tier | rows | promotable |
|---|---|---|
| `CURRENT` (taker 5.0 + maker 2.0 + slip 3.0) | 90 | yes |
| `conservative` (taker 5.0, pre-maker) | 90 | yes |
| `OPTIMISTIC` (taker 2.5) | 224 | **no** |
| `UNRECORDED` | 45 | **no** |

269 of 449 stop being promotable on their own evidence. That is the intent, and it is not a dead
end: the factory has already minted a generation at the current model, so the store converges by
re-minting without rewriting a byte of durable history. **What a rate change can never repair is
worth stating once** — `expectancy` re-derives exactly, but win-rate, realized reward:risk and
the robustness verdict all need per-trade signs the store does not keep. That is why the answer
is a gate at the door rather than a backfill.

**A fourth axis landed 2026-07-29, and it invalidates the table above rather than extending it:
the cost model now charges FUNDING.** These are perpetual futures — there is no expiry, and the
mechanism holding the contract near spot is a payment between longs and shorts every 8 hours,
charged on notional. The model had never charged it. `_EXIT_PARAMS` allows `max_holding_bars` up
to 48, so a 1d spec holds 12–48 **days**: 36 to 144 settlements at the venue's 1 bp base rate,
against a modelled ~10 bps of fees and slippage per trade. Measured on a 400-bar replay of the
same spec with and only with the carry, the per-trade carry (0.061R) exceeded fees and slippage
combined (0.052R) and expectancy fell 20%; on the real 1d book, whose holds are 14–39 bars rather
than the fixture's, the ratio is larger.

> **Correction 2026-08-02 — that ratio does not hold for the pool that exists.** The figure above
> is a **1d fixture holding 12–48 days**, which is where carry dominates by construction. Measured
> across the 240 candidates carrying the current basis, per trade: funding is **0.0006R at 15m,
> 0.0024R at 1h, 0.0025R at 4h** — **0.2% to 4% of total cost**, against taker fee plus slippage
> at **94%**. The paragraph above is kept because charging carry was still right and its
> *directional* property is real (a long pays, a short receives), but the weight it implies is
> wrong for every timeframe the runtime actually trades. **Do not re-optimize this term.** The one
> that decides whether a trade can pay for itself is the fixed bps against `stop_atr` × ATR.

Two properties make this different from a rate change:

- **It is directional.** A long pays and a short receives, so the factory had been ranking long
  and short lineages on one scale when their real carry differs by twice the figure above. Fees
  are direction-blind; carry is not.
- **It was never missing data.** The cycle already fetches `DEFAULT_FUNDING_RECORDS` real
  settlements per symbol for the `funding_rate`/`funding_zscore` features, so `backtest_spec`
  charges the venue's own history over the replay window. `cost_model.funding_source` records
  `venue_history` or `modelled_constant` per candidate, because those are different qualities of
  evidence.

**Every candidate minted before this is `OPTIMISTIC`** — including the 180 rows the table above
calls promotable. A missing cost cannot read as a cost of zero on an instrument that charges
every 8 hours, and the basis string says `+funding_uncharged` rather than dropping the term, so
the listing distinguishes "older model" from "priced a perpetual as free to hold". Shorts are
refused too, where the omission ran in their favour; that is a cost accepted deliberately,
because a tier whose meaning depended on the spec's direction would be a property of the trade
rather than of the cost model the tier ranks. The convergence path is unchanged: re-mint.

The judgement this hands back to the operator is the same one #260 did, one axis over: the
board's verdict on this runtime's record was already `판단 불가` at +0.08R over 60 trades with a
95% interval of `[−0.32, +0.48]`, and the omitted carry is R-material against that interval.

So the sequencing that reads honestly is: the *plumbing* is proven, the *edge* is not. Routing
existing (it now does) does not change that — it is why the routing row is deliberately not part
of `ready`. Automating today would automate a strategy set whose sign nobody can currently state.

The money path now carries its own governance record (**#200**): a P5 PermissionDecision built before
the order and an audit event after it, closing `p5_policy_gate`'s `post_action_report_and_audit`,
which was the one requirement in that gate with no implementation. Binding also means **no live order
without an active approved Core**.

The `feat/cost-budget-ledger` dependency this section once recorded is **void** — that branch was
never pushed and the sequencing was deliberately reversed (2026-07-24); the two claim different
scopes at different levels, so nothing was owed to it.

- [x] **Governance implementation** — steps 1, 2, 4, 5, 8, 9 done (PR #142): `permission_decision.v0.4`
      adds `FINANCIAL_APPROVED_TRADING_USE`; the scope is in `policy_dispositions.EXECUTE_AND_REPORT`;
      `p5_policy_gate` is defined; `permission.py` builds a live-order decision at P5; the v0.4
      validator + positive example exist; both replay bundles regenerated.
  - [x] Step 3 — `financial_transaction_execution_implemented: false → true` — **flipped 2026-07-25**
        with LP4 increment 2b (PR #184), i.e. only once LP4 could actually send. It moved in lockstep
        with `ORDER_PATH_IMPLEMENTED = True` and the readiness board, asserted to agree
        (`CRYPTO_LIVE_EXECUTION_VERIFICATION_V0.1.md`). `financial_executor_enabled` stays `false`
        and untouched. **Read this as a posture change, not a checkbox:** an order path now exists,
        so READY on the readiness board means a real order can be placed on that machine.
  - [x] New closed schema `live_trading_budget.v0.1` (registered trading caps, self-hashed) —
        done 2026-07-25 (schema + `live_budget.py` + `register_live_trading_budget.py`).
  - [x] Step 6b: the guard reads the registered budget as authoritative (over env caps) — done
        2026-07-25 (`resolve_live_order_limits` + `budget_registered` guard check + the readiness
        `registered_budget` row). No live order without a valid registered budget. Grants nothing.
  - [x] New narrow role `execution.live_trader` — P5, `external_action_allowed: true`, **candidate
        (non-routable)** — done 2026-07-25 (contract + index-only registry entry + hash; passes
        contract-consistency + release gate). Grants nothing; **activating** it (candidate →
        routable) is the separate remaining `ROLE_GOVERNANCE` approval.
  - [x] Validator assertions + the v0.4 positive example + **both replay bundles regenerated** —
        done with steps 8/9 (PR #142). Any future policy edit changes its SHA-256 and owes the
        bundles another rebuild (CRLF-normalized; `rebuild_bundle` has no CLI entrypoint).
- [~] **LP4** order adapter — **increment 1 (skeleton) done 2026-07-25**
      (`runtime/mvp_runtime/crypto/live_execution.py`: adapter protocol, DryRun default, gated
      stub, `submit_and_reconcile` + reconcile vocabulary; design record
      `LP4_ORDER_ADAPTER_DESIGN_V0.1.md`). **Increment 2a (the real signed transport +
      conditional order types) done 2026-07-25** — venue semantics verified against the official
      New Order / Query Order / error-code references (corrected: closePosition excludes both
      quantity and reduceOnly; -2013 = NOT_FOUND vs any other rejection = UNRECONCILABLE; no
      documented auto-cancel, so LP5 must cancel the surviving bracket leg).
      **Increment 2b done 2026-07-25** — `scripts/place_canary_order.py` (the deliberate
      single-canary path, entry-only, exposure read from the venue so the guard's one fail-open
      default is not used); a `canary=True` guard mode exempt from the promotion gate only (the
      chicken-and-egg: a canary earns that evidence) with its **own** confirmation phrase
      (`MVP_LIVE_CANARY_CONFIRMATION`), so neither phrase can authorize the other's capability;
      and the **lockstep governance flip** (`financial_transaction_execution_implemented: true`
      + `ORDER_PATH_IMPLEMENTED = True`, asserted to agree) with both replay bundles regenerated.
      `financial_executor_enabled` and every `runtime_effect`/`cutover` flag stay false.
      **LP4 is complete.** Nothing autonomous routes to the venue — that needs LP5.
- [~] **LP5** position kernel + cycle routing — design records `LP5_POSITION_KERNEL_DESIGN_V0.1.md`
      and `LP5_3_LIVE_LEG_DESIGN_V0.1.md`. **Everything except cycle routing has landed.** LP5.1, 5.2
      and 5.4 are complete, and 5.3 was split at the one line that matters — *decide* vs *send* —
      with **both halves now merged** (#193, #196). What remains is giving the executing leg a
      caller, which is the piece that changes the safety posture.
  - [x] **LP5.1 — position state + reconciliation** (PR #183, 2026-07-25):
        `crypto/live_position.py`. Live positions live in their own `live_positions/` namespace with
        `stage: "live"` (paper keys on `(venue, symbol, timeframe)` with the same `binance_futures`
        venue string, so a shared book would let the paper cycle settle a *real* position). The venue,
        not the store, is the truth: `reconcile_positions` returns RECONCILED / DRIFT /
        ACCOUNT_UNREADABLE, and on anything but RECONCILED entries are refused while **closes stay
        allowed** — being unable to see the account must never trap an open position. Concurrency
        caps: 2 open live positions, 1 per symbol.
  - [x] **LP5.1c — the one fail-open closed** (same PR): `evaluate_live_order_guard`'s
        `current_open_notional_usdt=0.0` default asserted "the account is flat" on no evidence. The
        argument is now **required**, and unknown exposure is reported *at the cap*
        (`compute_open_notional_usdt(None, at_cap=…)`), so not knowing blocks instead of permitting.
  - [x] **LP5.2 — sizing** (PR #186): `crypto/live_sizing.py`. `min(risk-based, budget cap)`, floored
        to the venue's lot step in integer space, then **re-checked** after flooring; a size that
        cannot satisfy both bounds is refused, never defaulted. Risk per trade is 1% of usable
        (available-balance) equity.
  - [x] **LP5.4 — the outcome bridge** (PR #187): a live outcome now carries `result_R`,
        `risk_usdt`, `created_at_utc` and strategy lineage (`candidate_id` /
        `strategy_rule_hash` / `strategy_generation_id`), so `guards.run_risk_guard`, `lifecycle`
        and the C6 feedback report read live results with **no live-specific branch** — a live loss
        streak can finally demote a strategy the way a paper one does. The load-bearing part is the
        **exclusion rule**: `guards._closed_rows` reads a missing `result_R` as `0.0`, i.e. a
        *breakeven*, so an R-less live loss would have **shortened** a loss streak. Rows whose risk
        was never recorded are excluded as `LIVE_OUTCOME_NO_RECORDED_RISK` rather than given a
        fabricated R, and stay fully visible to the daily-loss breaker (which needs no R).
        `live_analysis_summary` reports readable and excluded counts separately so live trades never
        silently re-define a previously reported paper expectancy.
  - [~] **LP5.3 — the live leg.** Split at the one line that matters: **decide** vs **send**.
    - [x] **The entry decision** (PR #193, Thomas 2026-07-25) — `live_filters.py` reads the venue's
          real lot step / minimums / price tick from `exchangeInfo` (on the existing
          `binance_futures` grant; the mock collector deliberately cannot answer, so a mock run
          refuses rather than sizing on invented numbers, and a MARKET order is bound by the
          stricter of `LOT_SIZE`/`MARKET_LOT_SIZE`). `live_entry.plan_live_entry` then assembles
          every door — C4 verdict, LP5.1 reconciliation, concurrency caps, filters, the tick-rounded
          protective bracket, LP5.2 sizing, LP3's final guard — into one auditable decision, with
          `ready` *derived* from the guard's own `approved` rather than asserted. **It contains no
          adapter and imports none**, so it cannot send; it also has no production caller yet, by
          design. The bracket is priced *before* the size so the quantity matches the stop that
          would actually be placed, and both legs round toward the entry so rounding can only shrink
          realised risk, never widen it (a stop that rounds onto the entry refuses, never repairs).
    - [x] **The executing leg** (Thomas 2026-07-25) — `live_leg.py`: `execute_live_entry` opens a
          position only on a RECONCILED fill, places both protective legs as `closePosition`
          conditionals, and **closes any exposure the venue reports if the bracket will not
          place**; `execute_live_exit` closes reduceOnly, cancels the surviving leg (the venue
          auto-cancels nothing), computes realized P&L from actual fills, and records the outcome
          **before** clearing the book. The adapter is **injected**, so the module cannot reach a
          venue on its own and every branch is tested with zero network. Also extended the risk
          guard to see live outcomes — they live in their own store, so the paper provenance split
          never saw them and the breaker would have ignored every live loss.
    - [x] **The cycle routing** — **done 2026-07-28** (`crypto/live_route.py`). The executing leg
          has a caller, and exactly one: `cycle.py` reaches the live stack only through that
          module, pinned by
          `test_the_cycle_reaches_the_live_order_path_through_exactly_one_module`, which
          *replaced* the old blanket tripwire rather than deleting it — the property being kept
          is that "which code can start a live order" has a single answer.
          `AUTONOMOUS_ROUTING_WIRED` is now `True` and is deliberately still **not** part of
          `ready`: wired is not permitted, and every door below it is unchanged. The gate comes
          first — without `MVP_LIVE_TRADING=real` the leg returns DISABLED having read no account
          and opened no socket, so a machine that has not been through the operator checklist
          behaves exactly as before. Live entries use the **same** `build_entry_plan` as paper
          and the same C4 verdict, so a live entry can never be permitted where a paper one was
          not.
    - [ ] **The maker fee rate is published, not measured, and it errs in the unsafe direction.**
          Since the take-profit exit became a resting `reduceOnly` LIMIT (2026-07-28) the backtest
          charges `DEFAULT_MAKER_FEE_BPS = 2.0` on that leg — Binance's **published** standard
          rate. Unlike the taker figure it has never been measured on this account, because no
          maker fill has ever happened. If the real rate is higher the backtest reports an edge
          **better** than reality, which is the wrong way for evidence that gates real money.
          Two of the three pieces are in place. `pool.expectancy_at` rescales the maker leg
          independently of the taker one (#309), so the eventual measurement converts every
          maker-scored candidate exactly instead of splitting the store a fourth time — done
          while no candidate carried a maker term, which was the cheap moment to do it. And the
          instrument exists (#313): `account.fill_history` reads `/fapi/v1/userTrades`, whose
          per-fill `commission` / `quoteQty` / `maker` fields give the split `/fapi/v1/income`
          cannot, since income totals a window with no leg attribution — which is exactly why the
          taker rate had to be hand-derived and why the maker rate could not be. Surface is
          `python -m runtime.mvp_runtime.crypto.account --fee-rates BTCUSDT`; with no maker fill
          it reports the absence and its reason, never `0.0` (the two are opposite claims about
          real money).
          **What is missing is a maker fill**, and it is now operator state rather than a build
          item: `place_canary_order` is entry-only MARKET by construction, so the only door that
          can produce one is the autonomous leg's resting target — which exists and is wired, and
          waits on the same live-trading checklist as everything else in this section. When one
          lands, replace the constant. If the measured rate comes in **above** 2.0, every
          candidate scored at the published rate becomes `OPTIMISTIC` and stops being promotable
          on its own evidence — intended behaviour of the gate above, not a regression.
          **Update 2026-08-02 — the checklist it was waiting on has cleared.** The live entry
          door reads OPEN (readiness READY, routing WIRED, breakers NORMAL, Gate 0 answered by
          the operator acknowledgement), so the first live take-profit fill is now a matter of a
          signal firing rather than of a step nobody has taken. (**2026-08-03:** the Gate 0 half
          of that sentence is gone — #473 removed it and the acknowledgement with it. The door
          is the bracket breaker alone, and it still reads OPEN.) This is the last unmeasured term
          in the cost model, and the only one that errs in the **unsafe** direction — section F
          reads it as such. Run `--fee-rates` after the first live TP, not before.
    - [x] **The grant's revocation was defeatable by the grant** — done 2026-07-29 (#324),
          reviewing #320 rather than running it. `assert_authorization` branched on
          `evidence_ref.startswith("env_only:")` to decide whether to re-read the grant file or
          the environment — and on a grant-backed authorization `evidence_ref` is copied verbatim
          out of the operator's activation record. The sentinel passes the path validation (no
          drive, not absolute, no `..`) and a file of that name is legal on Linux, so a record
          could name itself onto the env branch and skip its own file re-read. Reproduced: with
          the sentinel in place, **deleting the grant file left the authorization valid** — the
          one property `assert_authorization` promises about grants, broken on the providers
          #320 deliberately KEPT on a grant (`binance_futures_account`).
          Not privilege escalation — writing that record already takes the access to mint a normal
          grant — but persistence: the documented revocation silently stopped working. Fixed with
          an explicit `Authorization.env_gate` field only `env_only_authorization` sets, so no
          record content can reach it. Worth keeping as a shape rather than an incident: the
          weakening came from **one field carrying two meanings, where the meaning selected which
          security check ran**. #320's own reasoning ("a keyword would put 'no grant needed' one
          token away from the model provider") is the same instinct applied one level up.
          The containment test had a matching hole — it collected only `ast.Attribute`, so a
          caller written `from ..safety_gate import select_env_gated` scored zero, and that is
          the idiomatic import style in six modules under `runtime/`. A test that asserts an
          exact caller set can only fail on a *new* caller, so nothing in it would have noticed
          the matcher going blind to a whole calling convention; there is now a unit test on the
          matcher itself.
    - [x] **`execution.live_trader` named the removed `live_trading` grant** — done 2026-07-29
          (role `0.1.0` → `0.2.0`, registry hash refreshed). `live_trading_grant_revoked` →
          `live_trading_opt_in_cleared`, `live_trading_grant_absent_or_revoked` →
          `live_trading_opt_in_absent`. **The bigger find was in the prose, not the conditions:**
          the definition still said "the order adapter (LP4) and position kernel (LP5) do not
          exist yet" and that the canary count was "currently 0". LP4 shipped 2026-07-25 and LP5
          followed — a role definition asserting build status is exactly how status got four
          owners. It now states requirements only and points at the readiness board, which is the
          one thing that can answer a per-machine question. Two sibling docs carried the same
          dead claim and were corrected with it (`LP5_POSITION_KERNEL_DESIGN`,
          `CRYPTO_LIVE_EXECUTION_VERIFICATION`).
    - [x] **A live entry now tells the operator it happened** — done 2026-07-29 (#331). Cycle
          routing meant a scheduled run could open a real position **with nobody watching**, and
          nothing said so: the cycle recorded it and the audit chain recorded it, which are both
          places you have to go and look. Fine for a daily review, useless for the first
          autonomous entries this system has ever made. Sent for exactly two outcomes, `OPENED`
          and `INCIDENT` — a channel that pings every fifteen minutes is one nobody reads by the
          second day, and the routine picture already goes out daily in `crypto_report`. This
          changes nothing about what is permitted; it changes how long a surprise can go unseen.
    - [x] **Live now enforces the strategy's time exit** — done 2026-07-29, the LP5.1 record-shape
          increment this box asked for. The live position carries `timeframe`,
          `max_holding_bars` and a deduped holding counter; `live_route` advances the counter
          every cycle and closes at market (reduceOnly) when the count reaches the limit.
          `max_holding_bars` rides on the **position**, not re-read from the spec at exit time —
          paper's parity rule, so a spec edited mid-hold cannot move the exit of a trade already
          open. Legacy positions (opened before this shape) fall back to the timeframe table and
          say so (`LIVE_ROUTING_MAX_HOLD_FALLBACK`), so that gap stays attributable.
          Two properties carried over deliberately, because the counter *is* the rule: it
          advances on cycles that close nothing (persisted unconditionally, so a failed close
          cannot reset the clock), and one bar counts once — `paper.advance_holding` is now
          shared by both legs rather than copied, so they cannot drift on what "a bar passed"
          means.
          **What still differs, and is not a defect:** paper models the exit at the bar's close,
          live pays taker plus slippage on a market order. Same rule, different cost; `r_basis`
          keeps the populations labelled. So the evidence transfers **better** than before but
          still not exactly — do not read the residual gap as drift.
          One asymmetry worth knowing: a time exit that will not confirm is reported
          (`LIVE_ROUTING_TIME_EXIT_DEFERRED`) and retried next cycle, **not** escalated to a
          portfolio halt — unlike an unprotected position, it still has its bracket resting at
          the venue, so it is protected, merely held past its window.
    - [ ] **The paper record does not yet justify turning the routing on.** Carried across from
          the pre-merge version of the box above (#292), because it annotated a precondition
          rather than the build, and the build landing does not settle it.
          It read "6 closed trades at −0.39R, `INSUFFICIENT_SAMPLE`"; on 2026-07-28 it is **60
          closed trades at +0.08R**, and the sign is still unknown — 95% interval
          `[−0.32, +0.48]`, about 0.4 standard errors from zero, with roughly **3,100 trades**
          needed to separate the effect as observed. **More data moved the number and not the
          verdict.** At ~18 closed trades a day that is months for the observed effect, or about
          a month if the true edge is the ~+0.2R the backtests claim — and the distance between
          those two figures is the reason to keep collecting rather than to route.
          Note this compounded with the item above until 2026-07-29: the backtests that claim
          ~+0.2R were run with a time exit the live leg did not enforce. The live leg enforces it
          now, so the evidence transfers better — but the sample question this box is about did
          not move, and the exit's *cost* still differs (paper models the bar close, live pays
          taker plus slippage).
          **Update 2026-08-02 — the question this box asks now has a different owner and a
          different number.** Gate 0 became machine-readable and wired into `live_entry` (#409),
          scoped to the routable pool (#411), and answerable by a signed, expiring operator
          acknowledgement (#413). The 60-trade figure above is not the sample any more: the pool
          rotated on 2026-07-31 and Gate 0 counts only lineages that can still route, so the
          measured sample went to **0 of 20** — every one of the 86 own closed rows belongs to a
          retired lineage. It is **1 of 20** since #419 fixed a latch that had been reading every
          row this runtime mints as un-priceable. **The live door is OPEN meanwhile**, on Thomas's
          acknowledgement (valid to 2026-08-22), not on evidence — so this box no longer describes
          a gate holding routing shut. It describes a sample being collected while routing runs,
          and the honest read of that is in `crypto/live_candidate_ack.py`, not here.
          **Update 2026-08-03 (#473) — there is no gate here at all now, and no acknowledgement.**
          The sample could not be reached: the routable set is whichever batch was promoted last,
          promotions land every 1-3 days, and the acknowledgement voided on the same event that
          reset the sample — so 20 was ~32 days of a frozen pool away on a pool that does not
          stay frozen. Gate 0's runtime enforcement and the acknowledgement are both removed;
          it is an operator checklist item again. `live_candidate_eligible` is still computed
          and still on the cycle record, and nothing refuses on it. What gates live routing is
          the per-strategy ladder, which judges each strategy on its own record. Measurement:
          `docs/proposals/GATE0_CANNOT_BE_SATISFIED_V0.1.md`.
- [ ] **≥ 3 clean canary orders** before any autonomous run. **On the machine that ran them the
      board reads 4/4 (2026-07-28)**; this file still cannot tell *you* the count, because the
      evidence store is
      `.runtime_governance_state/live_canary_orders.jsonl` — per-machine and gitignored, like the
      Core pointer and the safety-flag grants — so a number written here is a claim about whichever
      machine last edited it. It said "0" until 2026-07-27; the fix was not a new number, it was to
      stop asserting one, and that still holds for any machine but the one that ran them.
      **On that machine the four are 2026-07-26T11:03Z and 14:05Z, then 2026-07-27T08:02Z and
      09:00Z** — and the split matters, because the first two were placed while #228 and #246 were
      still live and the second two after they merged. The 11:03Z order is the one whose audit
      event was lost; see the header. The 14:05Z order is the first that completed every step.
      Ask the machine: `python -m runtime.mvp_runtime.crypto.live_readiness`, the `canary_evidence`
      row. Two things that count differently from "how many did I place": only records with
      `clean: true` count, and a registry that fails **any** verification — line not JSON, self-hash
      mismatch, duplicate `canary_order_id` — counts as **zero** with a named reason rather than
      being partially trusted (`clean_canary_order_count`). A canary placed while the grant was not
      active also leaves no evidence at all: `DryRunCanaryRegistry` accepts and discards, because
      unbacked evidence here would unlock autonomous trading.
      (1 canary existed in the frozen source system and did not migrate.)
      **And all four count while being unable to say what size they were** (#328/#332,
      2026-07-29). The record carried `notional_usdt` — what the operator typed — with no
      quantity and no fill to check it against, and all four predate #268, which is when that
      declaration started being checked at all. So "65.0" cannot now be asked about. The record
      gained the venue's own numbers (`quantity`, `avg_price`, `executed_qty`, `cum_quote`), which
      `submit_and_reconcile` had been producing and the record discarding, so declared-versus-
      filled became a subtraction; and the board says when it cannot make that subtraction, which
      today is every row:
      `4/4 clean canary orders [4 of 4 cannot prove their size — no fill recorded]`.
      Stated beside the count, never folded into it — making a size disagreement block promotion
      would change what this box counts, and that decision has not been taken. **The previous four
      are not repairable**: their fills are not in the record, and inventing them is the failure
      the whole thing refuses. Whether evidence that cannot prove its own size should count toward
      a threshold is an operator decision, like the other two this box already defers.
      **Re-verification is owed for the two placed on 2026-07-26, and here is why.** Three
      separate defects sat on this path and were fixed afterwards, so earlier evidence cannot be
      read at face value:
      **#201** — `resolve_live_order_limits` dropped `canary_confirmation`, so the guard compared an
      empty phrase and refused every attempt (fail-*closed*, nothing unsafe, but the one live door
      there is did not work);
      **#228** — a filled canary was reported as a crash and its audit event never appended;
      **#246** — the daily counter was incremented only by the autonomous leg nothing may import, so
      the door that actually places orders never counted its own, and `max_daily_order_count`
      refused nothing.
      Since **#268** the door also **verifies the declared notional** against the venue's price
      rather than trusting the operator's arithmetic, which changes what a valid invocation looks
      like. Re-run rather than count anything placed before these landed.
      **Operator-only, real money** — `scripts/place_canary_order.py`
      on Thomas's machine with his own keys and its own confirmation phrase
      (`MVP_LIVE_CANARY_CONFIRMATION`, deliberately distinct from the live-trading phrase so neither
      authorizes the other's capability). It now also needs the **read-only market-data feed**
      (`MVP_MARKET_DATA=binance_futures` + a `network_access` grant) — the board reports it as
      `market_data_visibility`, and without it a canary refuses, so no evidence can be earned at
      all (#274). Claude does not run it.
- [x] The **symbol-starved router** finding — closed 2026-07-25 (PR #148). A crypto schedule with an
      empty request now fans out over every `(symbol, timeframe)` the pool routes on **plus** every
      context holding an open paper position (`cycle.run_pool_cycle`), and `route_entries` matches
      the whole `symbol_scope` rather than `symbol_scope[0]`, booking under the traded symbol. A
      named `SYMBOL [TIMEFRAME]` request is still a single-context operator override.
- [x] ⚠️ **Decided 2026-07-25 (Thomas): option (a)** — the C4 risk guard reads **this runtime's own
      outcomes only**; `run_lifecycle` keeps the full history. Found while correcting Gate 0:
      `import_crypto_history.py` deliberately routes the predecessor's closed outcomes into the
      store *"the C4 risk guard and C6 feedback read"*, and the measured effect was 112 imported
      rows worth **+266.8R** inside the rolling week — so the weekly-loss breaker could not trip
      however this runtime performed. Transient (those rows age out within days) but the cause was
      not, so the cause was fixed: `cycle.run_crypto_cycle` now passes
      `split_by_provenance(outcomes)[0]` to the guard. Lifecycle was left alone deliberately —
      imported outcomes carry strategy lineage, and promotion/demotion is a performance judgement,
      not a safety brake; a test pins the two call sites as scoped differently. The import script
      itself is untouched.

- [ ] **Intraday open interest — a store we own, because the vendor keeps 84 days.** The `oi_*`
      families are the strongest thing the factory currently mints (four of the ten lineages
      promoted 2026-07-29 are `oi_squeeze_long` / `oi_unwind_short`, and the top one scores
      +0.99R at current rates), and every one of them is reading a **daily** OI series.
      `CoinalyzeLiquidationFeed.open_interest_history` hardcodes `interval: "daily"`, and
      `_open_interest_event_columns` says so plainly: *"a change here is day against day
      regardless of the bar size the caller will align onto."* So a 1h or 4h `oi_*` entry is
      judging hour-scale timing on a value that steps once a day. That is a **collection**
      problem, not a vocabulary one — the raw level is deliberately unmintable (`open_interest`
      is evidence-only; the mintable pair is `open_interest_change_pct` and
      `open_interest_zscore`, the latter a rolling z-score of the level itself, which is exactly
      the per-symbol normalization a shared parameter space needs).

      **Measured on the vendor, 2026-07-29** (BTCUSDT, one read-only request per interval; the
      response truncates at ~2000 rows, so each window was sized under it and old windows were
      requested explicitly to tell a row cap from a retention wall):

      | interval | oldest row | effective retention |
      |---|---|---|
      | `daily` | 2025-03-17 | 500 d+ (as much as asked) |
      | `1hour` | 2026-05-06 | **~84 d** |
      | `15min` | 2026-07-08 | ~21 d |
      | `5min` | 2026-07-22 | ~7 d |

      `1hour` windows at 140–200, 340–400 and 460–520 days back all return **empty** — a wall,
      not a page boundary. Binance's own `futures/data/openInterestHist` is shallower still
      (500 rows = 21 d at `1h`, HTTP 400 on any older window), so there is no deeper source to
      switch to.

      **Why the switch cannot simply be made.** `factory_candle_target` replays **500 calendar
      days** at every timeframe below 1d (48,000 bars at 15m, 12,000 at 1h, 3,000 at 4h), and
      the source rule holds that the backtest and the live router read the same feature source.
      Point the feed at `1hour` today and 83% of every replay window has `open_interest = None`,
      the fail-closed evaluator leaves those bars indeterminate, and the family closes too few
      trades to earn a verdict — the switch would delete the evidence behind the strategies it
      was meant to improve. There is no partial-coverage escape: depth is global, not per family.

      **So the only path is to become the retainer, and that store now exists**
      (`crypto/oi_store.py`, wired into `cycle.attach_feeds`): seeded from the days the vendor
      still has, appended every cycle thereafter, keyed `(symbol, hour)` with latest-wins so a
      re-fetch is idempotent and a gap shorter than the vendor's window self-heals on the next
      read. The vendor request is throttled to **once per symbol per hour** inside the store, so
      the twenty contexts of a pool fan-out do not become twenty requests. **It feeds nothing** —
      `snapshot` is untouched, so features, backtest and live router all keep reading the daily
      series and stay identical to each other. A test pins that.

      **What is left is time, and then a decision.** The accumulation starts on the first cycle
      after this deploys — not when it merged — so the clock and the deploy are the same event.

      **The threshold, stated now so it is not re-litigated later:** a timeframe becomes eligible
      when the 1h store covers `FACTORY_DEPTH_DAYS` (500) for that symbol. From an 84-day seed
      that is **~416 days of accumulation** — roughly 2027-09 for a store started today. Slow,
      and the clock only starts when the store does, which is the whole argument for starting it
      before the feature change is wanted.

      **Eligibility is surfaced, never self-applied.** `oi_store.coverage_summary` reports the
      span per symbol and takes the **weakest** one (a switch is per timeframe across every
      symbol the pool trades, so a deep BTC series must not carry a thin DOGE one), and the
      daily board prints it next to the pool as a field rather than a warning — it will read
      "축적 중" for about a year, and a warning true every morning for a year is how a board
      teaches its reader to skip the warning block. Flipping the feature source stays an
      explicit change with Thomas reading the diff. An automatic flip would silently re-base
      the evidence under live-capable strategies mid-flight — the same class of silent widening
      this section spent two other items closing.

### An expired grant pinned the board's expiry warning, and named the wrong thing — closed 2026-08-04

**Found while answering "does the archive need a grant".** It does; `live_trading` does **not**,
and the file left over from when it did was still on disk and still being read — by the board, not
by the gate.

`.runtime_governance_state/safety_flag_activations/live_trading.json` was minted 2026-07-27T08:00
with a **four-hour** TTL (`expires_at: 2026-07-27T12:00:19Z`), one day before Thomas moved live
trading onto `safety_gate.select_env_gated` — the environment opt-in alone, no per-machine grant,
`expires_at` far-future by design. From that decision on, nothing in the live path read the file:
`select_env_gated` never calls `authorize`, and `assert_authorization` re-reads the **environment**
for an `env_gate` authorization rather than a record. Verified rather than assumed.

**`dashboard._grants` reads the directory, though**, and it takes `min()` by `expires_at` for the
"soonest expiry" line. So an inert file 8 days expired was the permanent minimum:

```
권한   12건 · 가장 이른 만료 2026-07-27 (live_trading) ⚠ -8일 남음      <- before
권한   11건 · 가장 이른 만료 2026-08-18 (telegram)                       <- after
```

Two failures from one file, and the second is the one that matters. The **warning was dead** —
past `GRANT_EXPIRY_WARNING_DAYS` forever, hiding every real grant's expiry behind it, which is how
a board teaches its reader to skip the block. And the row it named said **live_trading expired**,
which reads as live trading being off while `MVP_LIVE_TRADING=real` is set in the running
scheduler and the code path needs no grant at all — wrong in the **permissive** direction, and the
same shape as the Gate 0 acknowledgement #474 removed: inert, and it reads like live authority.

Removed as uid 10001 through the container, never as root on the host. **Deleting it revokes
nothing** — the code says so directly: for an env-gated authorization there is no record to
re-read, so "stopping a live scheduler means restarting it". That property is unchanged by this
and is worth knowing on its own.

**What this leaves open:** the board still has no row that says live trading is armed. It reports
grants, and live trading is not one, so its status is now absent rather than wrong — an
improvement, and not the fix. That belongs with the readiness board, which already learned this
lesson once (#382, process-scoped readings).

### Review findings — raised and closed 2026-07-26

A full review of the live stack raised six items. Recording them here because each is a rule with a
near-miss behind it, and the reasoning is more reusable than the fixes:

- [x] **The money path had no governance record** (#200). `p5_policy_gate` lists
      `post_action_report_and_audit` among its requirements, and it was the one with no
      implementation: `build_live_order_permission_decision` had **zero** production callers and
      `audit.py` had no financial builder. A repository that audits a memory promotion and a file
      write was about to move real money leaving only one registry row behind.
- [x] **A fail-open came back** (#200). `plan_live_entry(verdict=None)` skipped the C4 guards
      entirely when omitted — the same class as the `current_open_notional_usdt = 0.0` default
      LP5.1c closed, and worse in one way: the **test helper omitted it too**, so the unguarded path
      was the tested happy path. Now required, with a structural test on the signature.
- [x] **The readiness board drifted in the way it exists to prevent** (#200). Its computed rows were
      right; its **prose** described a shipped module as missing. Status moved into a computed row
      pinned to the real import graph, plus a test asserting the prose makes no build claim.
- [x] **Paper R and live R are different statistics sharing one pool** (#200). Paper R is measured on
      intended fills and is cost-free by design; live R on actual fills. Both records now carry
      `r_basis`. Not corrected — live R is the more pessimistic, so the distortion runs conservative.
- [x] **Four places claimed status** (#205). `CLAUDE.md`'s 32 KB Status section became
      `docs/BUILD_HISTORY.md` verbatim; the rules file now points instead of asserting.
- [x] **Nothing forced the executing leg to consume the decision record** — already true when raised:
      `execute_live_entry` takes the decision as its first argument and refuses unless `ready` *and*
      the guard approved.
- [x] **The canary phrase never reached the guard** (#201) — see the in-flight note above. The one
      live door there is had been refusing every attempt.
- [x] **A second source of caps was still reachable** (#203). Both guards defaulted `limits` to
      `LiveOrderLimits.from_env()`, so forgetting an argument fell back to env caps in a design whose
      whole point is that the registered budget is the only source. Now required — the same fix
      LP5.1c applied to `current_open_notional_usdt`, for the same reason.
- [x] **Stage tests covered every stage and no seam** (#203). Each stage of the live path built its
      own input, so a field one stage emits and the next reads could be renamed or dropped with the
      suite still green — which is exactly how #201's bug survived. There is now one end-to-end test
      walking a single trade through route → decide → submit → book → settle → the R consumers.
- [x] **Design records asserted a safety claim that had become false** (#203). Two of them still
      opened with "no code exists yet" and `ORDER_PATH_IMPLEMENTED = False` after LP4 shipped. A gate
      now refuses a record whose *header* disagrees with the policy or the code — header-scoped, so a
      record's body may still narrate history.

Two lessons worth carrying, both about **seams rather than units**: #201's bug survived because both
sides of a join were tested and the join was not, and the P5 gap survived because a policy
requirement had no test asserting any code satisfied it.

### Canary-path findings — raised and closed 2026-07-27

The first real canaries were placed on 2026-07-26, and placing them found four more defects on the
same door. Every one is a seam, which is now the established shape of this repository's bugs:

- [x] **The daily order cap counted nothing** (#246). `count_today` read a file only
      `live_leg.execute_live_entry` wrote — the autonomous leg no entry point may import — so the
      one door that can actually place an order never counted its own. Two real canaries went out
      and `live_order_counter.json` did not exist. The counter now increments in a `finally`: what
      spends daily budget is an order that **may** have reached the venue, so an ambiguous submit
      still counts. Over-counting a submit that never left is the safe direction for a risk limit.
- [x] **The daily-loss breaker measured a ledger nothing writes** (#247). Same shape, one number
      over: the local outcome ledger is written only by that same unreachable leg, so on this
      entry-only path the breaker read `0.0` forever and bounded nothing. It now reads the
      **venue's** realized figure off the account snapshot the tool was already fetching — fees and
      funding included, at no extra request.
- [x] **A filled canary was reported as a crash, and its audit event never appended** (#228, #232).
      The money had already moved; the record said otherwise.
- [x] **The per-order cap was checking a number the operator typed** (#268). `--quantity` reaches
      the venue, `--notional` is only what the caps are judged against, and nothing compared them —
      so an under-declared notional walked a larger real position past the per-order **and**
      exposure caps. Not hypothetical: the script's own documented example
      (`--quantity 0.001 --notional 60`) was written at BTC 60,000 and was ~7% short at 64,512, so
      *following the documentation produced the under-declaration*. Now verified against the venue's
      last closed 1m price; a synthetic, stale or absent price refuses rather than waving it through.
      **#229 fixed the same defect independently** on a branch 131 commits behind and was closed as
      redundant — see the in-flight note above for why that is worth remembering.

The pattern is now specific enough to design against: **a module that writes state the autonomous
leg owns, read by a door the autonomous leg cannot reach, is a counter that counts nothing.** Both
#246 and #247 are exactly that, and neither had a test because both halves worked.

> Real money. The full operator go-live checklist (grants, confirmation phrase, caps, kill switches)
> is in `CRYPTO_LIVE_EXECUTION_V0.1.md`. Claude does not run it, does not handle real keys, and does
> not enable live trading — every step there is Thomas's.

---

## D. Architecture design-vs-implementation gaps

Found 2026-07-27 by reading `docs/THOMAS_AUTONOMOUS_ORGANIZATION_ARCHITECTURE.md` (the Goal
document) against the code, rather than by working a roadmap — things the **design** specifies that
the build did not have. Listed here because a gap nobody wrote down is indistinguishable from a
decision.

**Most of this section closed within a day of being opened**, and the shape of what closed is worth
noting: §8.8 and §10.4 were real gaps and got built; §8.5 turned out to be one decision (activate
the roles) plus the routing to make activation non-inert; and the §8.4 risk-classification entry was
**written up wrong the first time** — the correction, not the fix, is the reusable part. What stays
open below is one of three things, and each box says which: an explicit Thomas decision
(`business.analysis`, `execution.live_trader`); a state that is now *correct rather than missing*
(the high-risk route, the PROGRAM route, `complexity`); or a mismatch between the design document
and the build where **the document is the thing to change** (§8.7). Do not read an open box here as
work waiting to be done.

**Re-audited 2026-07-29** against `main`, after the live-gate and cycle-routing changes. Two boxes
moved and both moved for the same reason — *a claim that was true when written, and was not
re-checked when the thing it described moved*: §8.4's high-risk box had a re-check condition phrased
around the router, and a RED action became autonomously reachable through a different door; §8.7 had
no box at all. Nothing else in this section changed.

The Target layers (§4–§5: Common Capability Organization, Opportunity & Business Creation, Business
Portfolio, Dynamic Strategic Board) are **not** listed here: §9 says do not build them now, so their
absence is compliance.

- [x] **§8.8 Core Candidate — the memory ladder's fourth rung** — done 2026-07-27. The ladder is
      Session → Working → Validated → Core Candidate → Thomas Core; three rungs existed. See
      `BUILD_HISTORY.md` for the shape and why. Promotion to Core stays unbuilt on purpose.
- [~] **§8.4 The Task Classifier routes one way of four.** `prime.py` hardcodes
      `selected_route: "ROLE"` and `program_request_ids: []`; `task.v0.3` models `PROGRAM`/`HYBRID`
      and no code path produces either. Partly closed 2026-07-27 — and the first reading of this
      item was **wrong in a way worth recording**, because the correction is the useful part.
  - [x] **Risk classification** — done 2026-07-27. It was first written up here as "the classifier
        returns a constant GREEN, so no task can ever be high-risk". Policy §10 says otherwise:
        risk classifies **the action**, and it lists "내부 분석" among its own GREEN examples. So
        GREEN was *correct* for the specialist's action and was never a stub. What was actually
        wrong was narrower and provable: §10 also says to evaluate every perspective and take the
        **highest**, and a run plans more than the analysis — so a run that created a file was
        still recorded as a plain read-only analysis, and the R8 write's own decision declared
        `GREEN` while carrying `EXECUTE_AND_REPORT`. Both fixed, plus a floor invariant at the one
        construction site (§10 read backwards) so no future action can be added below its
        disposition. See `BUILD_HISTORY.md`.
  - [ ] **The "High-risk Decision → Thomas Approval" route is still unreachable through the
        router — still correct, but the re-check condition below was too narrow and is
        rewritten.** No action on the **analysis** run path (intake → Prime → specialist →
        validation → audit) is priced ORANGE/RED, so no task classifies there. The
        approval-bearing ORANGE actions (memory promotion, candidate trial, program
        registration, strategy promotion) reach Thomas through R9/R10, not the router.

        **What the old wording missed, found re-auditing 2026-07-29.** It said to re-check "the
        day a run-path action is priced above YELLOW" — and that day already came through a door
        this box was not watching. Cycle routing (#302, 2026-07-28) gave `exchange.order.place`
        — `risk_level: RED`, the one action in the runtime that reaches money and a counterparty
        — an **autonomous caller**: scheduler → `crypto/cycle.py` → `crypto/live_route.py` → the
        P5 gate. So a RED action does now run without a human in the loop per-order, and it never
        touches the Task Classifier, which is why a box phrased around the router stayed quiet.

        **This is not a defect and nothing here needs building.** §10.5's pattern (specialists →
        independent risk review → Thomas approval → restricted execution) *is* implemented for
        that path — as the P5 live-execution gate plus an operator checklist (confirmation
        phrase, registered budget, ≥3 clean canary orders, both kill switches, the loss breaker),
        each a computed row on the readiness board. What differs from §8.4's row is the
        **mechanism**: a standing operator checklist rather than a per-action approval record.
        Worth stating because the two are not interchangeable — a checklist is set once and
        persists, an approval record is minted per action and is single-use.

        **Re-check condition, corrected:** re-open this the day an ORANGE/RED action becomes
        reachable **from any autonomous entry point**, not merely from the run path — and when
        one does, decide deliberately whether it belongs behind a per-action approval record or
        behind a standing gate. `exchange.order.place` chose the standing gate (Thomas,
        documented in `CRYPTO_LIVE_EXECUTION_V0.1.md`); that choice was never written down as a
        choice, which is how this box came to describe a world that had moved.
  - [ ] **The PROGRAM route — unbuilt, and *not* merely awaiting an approval.** An earlier
        version of this line said "blocked, not unbuilt"; that was wrong, and the correction is
        the useful part. Three things are missing, and the approval is the **last** of them:
        (1) **an executor** — nothing in `runtime/mvp_runtime/` runs a Program at all; the
        Executor is a *deferred* component (`deferred/executor/`, `program_execution_allowed:
        false`), plus the router emitting `PROGRAM`/`HYBRID`, which nothing does;
        (2) **an implementation** — both candidates (`schema.validator`, `document.parser`)
        declare `implementation_available: false`, so their definitions say what they would do
        and no code does it; (3) **activation** (`tool_or_program_activation:
        APPROVAL_REQUIRED`), which on its own would change nothing.
        Worth knowing that the *manufacturing* half is complete: programization runs observation
        → pattern → review → candidate → shadow → ACCEPTED → program request → **registry
        registration**, i.e. this repo can produce a Program candidate end-to-end and cannot run
        one. Deliberate — `program_request.py` builds every request as fail-closed BLOCK evidence.
        **Not recommended yet, for the same reason as `business.analysis`:** the MVP's only use
        case is business-idea analysis, which is judgment work, so there is no rule-based task to
        route. Building the executor now is §16's "for future possibilities". The signal to build
        is the programization counter catching a genuinely deterministic repetition.
  - [ ] `complexity` stays constant on purpose: nothing reads it, and deriving it from free
        request text would be a guess — §10's rule for a judgement made on insufficient
        information is to not lower the classification, so leaving it is the honest move until a
        consumer exists.
- [~] **§8.5 Routing to more than one Role.** `research.general` and `translation.general` were
      **activated by explicit Thomas decision 2026-07-27** (status/routable flipped in both the
      registry and the definitions, versions bumped, hashes refreshed; `execution.live_trader`
      deliberately **not** included — it is P5 with `external_action_allowed: true` and its
      activation is a live-trading decision). Activation alone routes nothing, so the same PR
      added `--kind` → capabilities → Role, and made the selected Role run against **its own**
      output contract. See `BUILD_HISTORY.md`.
      Recorded honestly: no `candidate_trial_report` backed the activation — trial records are
      per-machine and gitignored, and Thomas activated on his own authority rather than waiting
      for one. Legitimate, and the exception to trial → report → approval → activation, so it is
      written into the registry beside the flip.
  - [x] **Role-aware hosted response schema** — done 2026-07-27. Both vendor dialects are now
        derived per call with the Role's declared keys folded in, and providers expose
        `bind_role_output_keys` (a copy, not a mutation). A hosted run of a non-analysis kind
        works; a network provider that *cannot* bind is still refused by name, so the
        fail-closed direction is preserved. See `BUILD_HISTORY.md`.
  - [x] **Operator-channel kind markers** (`!번역` / `!조사` / `!분석`) — done 2026-07-27. Not
        "purely additive" as first written: the queue is durable, so the kind had to survive it
        (`task_registry_entry` **v0.2** adds `request_kind`; v0.1 rows read as `null`, which is
        the routing they ran under). One marker parser handles both marker families in either
        order, so the empty-request and hidden-command guards cannot cover one and miss the
        other. See `BUILD_HISTORY.md`.
  - [x] `content.general` + `development.general` **activated 2026-07-27** (explicit Thomas
        decision, option (b) of three offered), with their request kinds and operator markers so
        activation is not inert. See `BUILD_HISTORY.md`.
  - [ ] **`business.analysis` — deprioritized 2026-07-27, not blocked.** Thomas: business
        analysis does not need doing right now. Four options were put up (widen
        `general.specialist`'s output contract and retire the candidate / run the Candidate Trial
        / activate directly / leave it) and the answer was that none of them is worth the spend
        yet. Reasoning, the §13 scoring (two of six), and a price list for activation are in
        [`BUSINESS_ANALYSIS_ROLE_SPLIT_DESIGN_V0.1.md`](runtime-contracts/BUSINESS_ANALYSIS_ROLE_SPLIT_DESIGN_V0.1.md).
        **Read that before re-opening this** — the box stays here as an index entry, not as an
        open question. What would make it a priority: a real request the runtime cannot serve
        (options compared + a validation plan). Note the coupling it created: it is the last
        non-live candidate, so the trial suite rests on it staying one.
  - [ ] `execution.live_trader` stays a candidate and is **not** part of any routing decision —
        P5, `external_action_allowed: true`; its activation is a live-trading go/no-go.
- [ ] **§8.7 The registries hold neither of the design's example lists, and that was never
      written down.** Opened 2026-07-29 by re-auditing, not by working a roadmap.

      §8.7 names six "Initial Programs" (text format conversion, data validation, file saving,
      duplicate checking, schedule calculation, basic quality check) and five "Initial Tools"
      (LLM, Memory, File System, Search, Telegram). `05_REGISTRIES/PROGRAM_REGISTRY.yaml` holds
      **none of the six** — it holds `schema.validator` and `document.parser`, both `candidate`,
      `enabled: false`, `runtime_implementation_available: false`, under
      `status: active_registry_no_active_programs`. `TOOL_REGISTRY.yaml` is the same shape with
      `document.reader` and `search.readonly`.

      **Recorded as an observation, not as work owed**, and the distinction matters:

      * §8.7's actual *rule* — "Agents cannot arbitrarily use unregistered Programs or Tools" —
        **is enforced**, and fail-closed: `registry_resolution.py` refuses an entry with no
        `definition_sha256` and refuses on hash mismatch. That is the part that governs.
      * The five "Initial Tools" are mostly **not** registry tools at all, and reading them as
        owed items would be a category error. LLM is the provider behind the Safety-Flag Gate;
        Memory is the four-rung ladder; Telegram is the operator channel; Search runs as an
        `INTERNAL_READ` ALLOW action whose backend a `network_access` activation enables per
        machine — `SEARCH_READONLY_TOOL`'s own registry comment says flipping its fields would
        not and must not enable it. Each exists; none arrives through this registry.
      * The six Programs are genuinely absent, but building them is exactly what the **PROGRAM
        route** box above says not to do yet, and for the same reason: the MVP's one use case is
        judgment work, so there is no rule-based task to route. Six unrunnable definitions would
        be §16's "building for future possibilities" with extra hash maintenance.

      **So the honest state is: the lists in §8.7 are illustrative, the registries are real, and
      the design document does not say which its lists are.** The work here is one sentence in
      the architecture document marking those two lists as examples rather than an inventory —
      a Thomas edit, since §0 makes him the owner of that file. Until then this box exists so
      the mismatch is a recorded decision rather than a silent one.
- [x] **§10.4 multi-perspective judgement** — done 2026-07-27 in the form §10.4 permits for early
      MVP (*"one Agent may separate these perspectives internally"*): research / revenue / risk each
      reach their own verdict before the integrated answer, declared in the role's output contract
      and enforced by a validation check. The expensive form — perspectives as separate Agents —
      stays gated on §13's 3-of-6 separation criteria and is **not** owed: nothing yet shows one
      agent cannot hold the three. See `BUILD_HISTORY.md`.

Also raised and closed 2026-07-27: `docs/ACTIVE_ARCHITECTURE.md` — the document `CLAUDE.md` names
as the owner of current-implementation truth — still described the pre-R2 repository (baseline
I0.5.5, `runtime/mvp_runtime/` absent from its Source-of-Truth table, a Safety State block listing
implemented-and-gated capabilities as "remain disabled"). Same failure as #200's readiness-board
prose, one document over. Fixed by splitting Safety State at the seam it was blurring: *does the
code exist* vs *may this machine act*.

---

## E. Recorded deferrals — measured, decided, and deliberately not done

Not gaps and not work owed. Each entry is something that *looks* like a defect to anyone who
greps for it, was measured, and was then left alone on purpose. Written down for one reason: the
measurement is the expensive part, and without it here the next reader repeats it and reaches the
same answer.

- [ ] **`format: date-time` is declared on 110 fields and enforced nowhere — deferred by Thomas,
      2026-07-31, after measuring.** 54 schemas carry it (`date-time` is the only format the
      repository uses). Neither `requirements-validation.lock` nor `requirements-runtime.txt`
      carries a format-checking dependency, so `jsonschema`'s `FormatChecker` silently skips it.
      Confirmed inside the running container: its checker list is `date`, `email`, `idn-email`,
      `ipv4`, `ipv6`, `regex`, `time`, `uuid` — every format the repository does *not* use, and
      not the one it does. **This is not a CI-only switch**:
      `schema_cache` builds a `FormatChecker()` too, so enabling it changes what the live runtime
      accepts, and the two requirement files state they move in lockstep.

      **Measured 2026-07-31 across the live ledger and its archives, the per-machine stores,
      `examples/`, `generated/`, `THOMAS_CORE/` and `tests/fixtures/`, two independent ways** —
      each record validated with and without a real format checker and the results diffed, then
      every value at a `format: date-time` path tested directly against RFC 3339:

      ```
      262,656 timestamp values      0 invalid      (2026-07-31T04:00Z)
      268,686 timestamp values      0 invalid      (re-run ~90 minutes later)
        3,164 schema-bound records  0 that would newly fail
      ```

      Both rows are here on purpose. PM1 was still scanning when this was measured, so the
      denominator moved by the hour and any figure written down was stale before it was read —
      the **zero** is the finding, and it is the part that did not move. (That scan stopped
      2026-08-02 with the lane's removal and its 205,079 readings were deleted with it, so the
      denominator is now both smaller and still. The zero is unaffected: it is a property of
      `timeutil`, argued structurally two paragraphs down, not of this sample.)

      **Zero is structural rather than lucky.** `timeutil.utc_now_iso()` is the one authority
      (its own comment records consolidating two copies, "one of them with the `$` footgun"), it
      builds the string with `strftime`, and `FIXED_UTC_PATTERN` in the same module pins the
      shape. No writer in the runtime can emit a value that would fail.

      So enabling it today costs a dependency in two files and buys nothing measurable, and
      deferring costs nothing either — the cost to enable later is identical. **What reopens
      this:** a timestamp writer that does not go through `timeutil`, or an externally-supplied
      or model-supplied time string reaching a record. Then the 110 declarations should become a
      defence instead of documentation, and the measurement above is the thing to re-run first.

---
## F. The fee schedule is no longer what binds — re-measured 2026-08-04, and the answer moved

**As first measured, 2026-08-02**, across the 240 candidates then carrying the current cost
basis, per trade:

| tf | gross | taker fee | slippage | maker | funding | **total cost** | **net** |
|---|---|---|---|---|---|---|---|
| 15m | +0.0923 | 0.1707 | 0.0948 | 0.0106 | 0.0006 | **0.2768** | **−0.1845** |
| 1h | +0.1368 | 0.0939 | 0.0532 | 0.0058 | 0.0024 | **0.1553** | −0.0185 |
| 4h | +0.1517 | 0.0370 | 0.0207 | 0.0026 | 0.0025 | **0.0628** | **+0.0889** |

**The mechanism from that day is the one thing here that has not changed.** Gross edge is nearly
flat across the ladder; 1R is `stop_atr` × ATR and friction is a fixed ~10–16 bps, so the shorter
the bar the larger the share of the risk unit friction takes. Everything below is that mechanism
being fed a different denominator.

**Re-measured 2026-08-04** (`main` = `9c45d16`) over the **474** candidates now carrying that
basis — the population has doubled, and two of this section's three claims did not survive it:

| tf | gross | taker | slippage | maker | funding | **total cost** | **net** | was |
|---|---|---|---|---|---|---|---|---|
| 15m | +0.1037 | 0.1232 | 0.0739 | 0.0087 | +0.0006 | **0.2041** | **−0.0866** | −0.1845 |
| 1h | +0.1495 | 0.0730 | 0.0438 | 0.0057 | +0.0026 | **0.1230** | **+0.0241** | −0.0185 |
| 4h | +0.1661 | 0.0321 | 0.0193 | 0.0027 | +0.0005 | **0.0562** | **+0.1083** | +0.0889 |

- **1h pays.** The table above said it did not. Cost varies 3.6× now, not 4.4×.
- **The `stop_atr` floor was not second-order, and one day of evidence is why it read that way.**
  #420 landed 2026-08-02 and this section judged it the same day — against a store in which
  almost nothing had yet been minted at the new floor.
- **15m still does not pay at the median.** That claim survives, and only that one.

**By mint date, which is where #420 actually shows up** (`stop_atr` is the 15m median; the cost
and net columns are per trade):

| minted | `stop_atr` | 15m cost | 15m net | 1h net | 4h net |
|---|---|---|---|---|---|
| 2026-07-31 | 1.20 | 0.2625 | −0.2143 | +0.0085 | +0.1070 |
| 2026-08-01 | 1.20 | 0.2574 | −0.1449 | +0.0094 | +0.0880 |
| 2026-08-02 | 1.36 | 0.2218 | −0.0711 | +0.0479 | +0.1181 |
| 2026-08-03 | 1.33 | **0.1615** | −0.0693 | **+0.0783** | **+0.1320** |

15m friction fell **38%** across those four days while 15m gross stayed flat (+0.0772 → +0.0913),
so the move is the denominator and not the signal — the same mechanism, now running in the
favourable direction. A generational parameter change cannot be judged on the day it merges.

**Out of sample the fast end is the strongest rung, not the weakest.** Selecting on in-sample net
is not comparable across timeframes — at 4h it keeps 69% of the population and at 15m 36% — so
this cuts each timeframe at the same quantile and reads its holdout (median holdout expectancy,
share positive):

| cut by in-sample net | 15m | 1h | 4h |
|---|---|---|---|
| top 50% | −0.0006 (50%) | −0.0829 (39%) | −0.1539 (38%) |
| top 36% | **+0.0664 (67%)** | +0.0015 (50%) | −0.1565 (35%) |
| top 25% | +0.0947 (61%) | +0.1019 (58%) | −0.0678 (43%) |

Cuts tighter than 25% are not reported: below n≈16 per cell the numbers swing by more than the
effect (1h reads +1.17 at the top decile), which is noise wearing a trend's clothes.

**This whole section is Binance USD-M perps** (BTC/ETH/BNB/DOGE/SOL) and none of it transfers to
another venue on its own. Said here because the table above has already been cited across lanes
once — `EQUITY_PERP_S1_MEASUREMENTS_V0.1.md` (a) reads the 15m row as a reason to prioritise a
Hyperliquid HIP-3 equity-perp timeframe, where the fee schedule, the asset class and the funding
cadence (24/day against 3) are all different. That is the inference #461–#466 exist to prevent:
*a spec is judged against the venue it was mined on*. Cite the row with its venue attached, or
the argument it supports is about a market nobody measured.

**So the open question is answered, and its follow-on flips.** This venue's fee schedule does
permit a fast strategy — 1h clears it at the median today and 15m's deficit has more than halved
without the maker-entry lever being touched. And the proposal to restate the ladder's 20-trade
window and `MAX_DAYS_TO_LIFECYCLE_WINDOW` in trades-per-lineage **at 4h** would tighten the wrong
rung: 4h is the timeframe whose holdout holds a median of **23** closed trades against
`MIN_HOLDOUT_TRADES = 25`, where 15m holds 52 and 1h 44. Stated in trades, 4h is the rung that
cannot be judged.

**What binds instead: none of it reaches the door.** Of the 474, **zero** are ROBUST on the
verdict `pool.candidate_quality` recomputes — the stored `ROBUST` labels (26 of them) are the
stale kind `holdout_status` already documents, and every one recomputes to PROVISIONAL. The
holdout gate is where they stop:

- **216** carry no `stdev_r` on the holdout block, so no interval can be drawn. **Self-draining:**
  every mint before 2026-08-03 lacks it and all 120 mints on 2026-08-03 carry it.
- **192** hold fewer than `MIN_HOLDOUT_TRADES` closed trades.
- **66** compute an interval, and **all 66 are CONTRADICTED**.

**Do not read that last line as a threshold that is too strict.** The rows deep enough to be
judged are genuinely negative out of sample — the closest any candidate in the store comes to
confirmation is short by **0.1973R**, and the three deepest holdouts (n = 262, 428, 687) all
carry negative expectancy, which is not a sample-size complaint. The honest summary is that the
factory's in-sample edge has not yet reproduced forward at any timeframe.

**What is open, in the order it binds:**

1. **Nothing in this store survives its own holdout — diagnosed 2026-08-04, and it is not a
   gate.** Every mechanism was checked for a defect and each is behaving as designed; what the
   measurement found instead is below. This stays open because the finding is a direction, not
   a fix.
2. ~~**The board cannot say so.**~~ Closed by #477: `promotable_backlog` now returns a `refused`
   partition and `candidates_read`, and the daily board prints
   `승격 대기 0 (판정 후보 1140건 · cost_basis 546건 · holdout_insufficient 409건)` where it
   used to print nothing at all.
3. **The maker-entry lever is unchanged and still deferred** — `LP4_ORDER_ADAPTER_DESIGN_V0.1.md`,
   scope note 2026-08-02, three preconditions unstarted. Its premise (a resting entry selects
   against momentum/breakout) is untouched by anything above.

### F1. Why nothing survives its holdout — measured 2026-08-04, 480 current-basis rows and 394 parent-child pairs

**Four candidate explanations were tested and three are ruled out.** *Not a one-sided regime:*
long and short both degrade and both end negative out of sample (long HO −0.1694, short
−0.1547), so the holdout window is not simply a market that killed one side. *Not a steering
defect:* `champion_score` — which `elite_base_params` uses to place the search centre, and
which is composed entirely of in-sample terms — does predict the holdout, monotonically
(quintiles of judgeable rows: −0.3181, −0.2691, −0.1814, −0.1463, −0.1007; corr +0.363, and
+0.479 for in-sample expectancy). *Not a gate that is too strict:* nothing in the store clears
the selection-adjusted bar (z ≈ 3.37 at 64–70 attempts per context), and the nine rows that
clear the uncorrected 1.96 do so on 3–95 closed trades with holdouts of 2–24 — the exact
population `trades_per_parameter` and `MIN_HOLDOUT_TRADES` exist to refuse, and both refuse
them.

**What is left is that the search produces evidence it cannot confirm.** The first pass at this
compared the crossover population against the seeded one, which two stronger tests then
corrected — both are recorded below with what they replaced, because the corrected claim is
the sharper one and the way the first was wrong is worth not repeating.

**Paired against its own parents, fusion reproduces them and halves their evidence.** Every
crossover row in the store resolves both parents (394 of 394), so family, symbol, timeframe
and direction are controlled by construction rather than by matching:

| child vs its own parents | child | parent median | child lower |
|---|---|---|---|
| entry conditions | 5 | 4 | **0%** |
| in-sample trades | 54 | 104 | 91% |
| holdout trades | 25 | 35 | 71% |

The child closes **0.51×** the parent median's trades (p25 0.26, p75 0.81), fewer than *both*
parents in 66% of pairs and fewer than at least one in 94% — which is what a deduplicated
union under AND has to do, measured rather than assumed. Where both parents were judgeable,
**28%** of pairs produce a child that is not.

**And it buys nothing for that.** Restricted to the 131 pairs where the child and both parents
clear `MIN_HOLDOUT_TRADES`, the child is indistinguishable from its parents on both sides —
in-sample **−0.0034** (95% CI [−0.0097, +0.0031], child better in 47%), holdout **−0.0062**
(95% CI [−0.0200, +0.0114], 46%). Note the direction of the conditioning: this subset is the
one *most* favourable to fusion, since the children cut on depth are excluded from it, and
fusion still adds nothing there.

> **Corrects the earlier claim** that crossover "buys in-sample expectancy and no out-of-sample
> expectancy". That read the population difference (crossover IS +0.1385 against seeded
> −0.1049) as an effect of breeding. It is **parent selection**: rows used as a fusion parent
> already sit at IS +0.0450 / HO −0.1170 against the never-used seeded population's −0.1370 /
> −0.2137. Fusion starts from better rows and reproduces them.

**The condition count is monotone against judgeability — read within crossover, not pooled.**

| entry conditions (crossover) | n | in-sample trades | holdout trades | share judgeable |
|---|---|---|---|---|
| 4 | 42 | 74 | 32 | 81% |
| 5 | 72 | 72 | 31 | 64% |
| 6 | 66 | 48 | 14 | 30% |
| 7 | 38 | 19 | 6 | 16% |
| 8 | 22 | 7 | 5 | **0%** |

> **Corrects the earlier table**, which pooled both derivations and read as though condition
> count were the variable. It was not: on this population 2–3 conditions is **100% seeded** and
> 4–8 is **100% crossover**, so the pooled rows compared derivations wearing a condition
> count's label. The within-crossover series above is the real evidence and carries the same
> conclusion. Seeded draws span only 2–4 conditions and are not monotone there (53%, 85%, and
> too few rows above), which is the other half of why the pooled table could not mean what it
> appeared to.

**Between 41% and 47% of all mints produce a holdout that can never be confirmed** — 198 of the
480 current-basis rows, and 541 of all 1,140. The split by derivation is much wider on the
current basis (27% seeded / 56% crossover) than on the whole store (43% / 50%), so the
current-basis figure is the one that describes *today's* factory and the store figure is the
one that describes the population a reader will actually meet. Both are given because quoting
either alone overstates what it measures.

**The change this implies, stated so it can be decided rather than re-measured.** `fuse_specs`
bounds a child at `MAX_ENTRY_CONDITIONS = 8`, which is a validator bound copied verbatim from
source S3 — a statement about rule legality, not about whether the child can produce evidence.
The measurement puts 0 of 22 mints at that bound inside the judgeable band. The precedent for
refusing at mint is already in the same function: a child closing **no** trades is rejected
rather than stored, on the argument that it "would otherwise sit in the store as a scored
candidate that can never trade" — a child whose tail is too shallow to confirm is that defect
one notch weaker. Refusal is cheap: `mint_fusions` draws from `combinations(bucket, 2)` until
`pairs` children carry evidence, so a rejected pair redirects the draw instead of costing a
mint, and the rejection-reason list already exists to record it.

**Not done here, deliberately.** Refusing on holdout depth would reject 50–56% of crossover
children, which is a change to what the factory explores and trades exploration for
judgeability. That is a decision about the search space, and while the paired evidence above
is strong on the *mechanism* (394 pairs, controlled by construction) it is four days of mints
on the *consequence* — so it is written down with its numbers rather than merged into the
generation engine on the strength of one measurement.

**The narrower version is done** (`MAX_FUSION_ENTRY_CONDITIONS = 7`, `fuse_specs`). It removes
only the band measured at zero yield — 0 of 22 current-basis mints at 8 conditions are
judgeable, 0 of 25 across the whole store — and it is a **second** bound rather than a change
to `MAX_ENTRY_CONDITIONS`, which stays at source S3's 8 because it answers a different
question: that one is rule legality, this one is whether the child can produce evidence
anyone can judge. Both refusals fire and carry different reasons (`too_many_conditions`,
`holdout_unjudgeable`), so a later reader cannot mistake the tighter number for a revision of
the validator and raise it back to match.

Measured against the store it would have refused **25 of 394** fusions (6.3%), whose median
child closed **7** in-sample and **5** holdout trades, and the judgeable share of surviving
fusions moves 50% → 53%. That is the honest size of it: this buys back a band that was
producing nothing, not the 41–47% problem. **What reopens it** is a mint at 8 conditions that
reaches `MIN_HOLDOUT_TRADES`, which cannot happen at these signal rates without a longer
replay window — so re-measure `market_data.factory_candle_target` first, not this number.

**Do not re-open funding** (0.2–4% of cost; see the correction in section C) or reach for another
`stop_atr` tweak — the floor has now been credited with what it was worth and the next multiple
is not where the remaining gap is. What is still unmeasured is the *maker* rate, which the item
in section C is waiting on a live fill for.

### F2. "Cannot confirm" was hiding a negative, not a positive — measured 2026-08-04

F1 above ends on *"the search produces evidence it cannot confirm"*, which leaves the decisive
question open: is the edge real and merely un-provable at these sample sizes, or is there no
edge to prove? **Give the same rules five times the evidence and they are refuted, not
confirmed.** `factory.backtest_spec_pooled` replays one spec across several symbols' frames and
pools the outcomes, the tail and the cost legs; the stored single-symbol specs were re-scoped to
all five mined symbols and replayed against live frames (12 top-ranked single-family candidates
per timeframe, current cost basis, read-only — nothing appended):

| | 4h single → pooled | 1h single → pooled |
|---|---|---|
| holdout tail ≥ `MIN_HOLDOUT_TRADES` | 9/12 → **12/12** | 12/12 → 12/12 |
| median holdout trades | 32 → **169** | 49.5 → **268.5** |
| median holdout expectancy | −0.2694 → −0.2146 | −0.1173 → −0.1374 |
| CONFIRMED | 0 → **0** | 0 → **0** |
| CONTRADICTED | 9 → **12** | 12 → 12 |
| INSUFFICIENT | 3 → **0** | 0 → 0 |
| verdicts | — → 0 ROBUST / 12 PROVISIONAL / **0 FRAGILE** | — → 0 / 12 / 0 |

**Two things happen and only one was in doubt.** The arithmetic works exactly as it must: the
tail multiplies by ~5.3× and `INSUFFICIENT` disappears, because a spec scoped to N symbols is one
hypothesis fitted on all of them and its tail is all of their tails. `FRAGILE` disappears too —
`trades_per_parameter` clears the critical ratio once the sample pools, so the overfitting veto
stops firing. **And every row that stopped being unjudgeable became CONTRADICTED, not CONFIRMED.**
The estimate barely moves; what changes is that it stops being deniable.

**What this settles and what it does not.** It settles the reading of F1: the un-confirmability
was concealing a negative, so "re-mint deeper / wait for more evidence" is not a path to a
promotable candidate on these rules. It does **not** rule out pooled *minting* — these specs were
fitted on one symbol and then asked to transfer, which is strictly harder than searching for
parameters that hold across five from the start. A pooled mint is a different experiment and is
not run here; what is now cheap is running it, because the capability exists and
`build_spec_dict` takes a `symbol_scope`.

**`run_factory` is deliberately untouched.** Moving the rotation onto pooled specs would change
what the factory explores on the strength of one afternoon's measurement, and a mint-time change
is judged over generations, not days (the `#420` error). The capability landed; the decision did
not. What would justify taking it: a pooled *mint* batch that produces a CONFIRMED holdout where
the single-symbol search of the same family produced none.

---

## G. Codebase review backlog — measured 2026-08-02, three items open

A whole-codebase review for over-engineering, bottlenecks and improvement targets. Recorded
here rather than in a chat log because **this is the file that travels between machines**, and
every item below is a measurement someone would otherwise have to redo. Numbers are as of
`main` = `44c9b36`; each says how to re-measure rather than asking you to trust it.

Three of the review's findings are already closed and are named so they are not re-investigated:

- **Scheduler cadence drift** — closed by #431. `pm_scan` at a registered 120s ran at a measured
  p50 of 140s; two causes (claim-time anchoring, and a full sleep after the work) each removed.
- **The 694 MB prediction-market observation store, ~65% of it redundant** — moot: the whole lane
  was removed 2026-08-02 (section A), store deleted.
- **The audit chain's unrotated tip scan** — investigated and **not a problem.** `_tip()` streams
  the whole chain on every audited append and the chain is never rotated by design, but it is 904
  events / 2.7 MB / ~20 ms and grows ~20 events a day. Do not "fix" this; it would take years to
  matter and the no-rotation rule is load-bearing.

### G1. Crypto tunables have no owner — 602 constants across 42 modules ⚠️ highest value

```
grep -rhcE '^[A-Z_]{4,} *[:=]' runtime/mvp_runtime/crypto/*.py | paste -sd+ | bc
ls runtime/mvp_runtime/crypto/ | grep -iE 'config|policy|const|params'   # returns nothing
```

`features.py` 56, `market_data.py` 50, `paper.py` 42, `live_leg.py` 33, `live_execution.py` 33,
`factory.py` 29, `account.py` 25, `pool.py` 24, … and no module owns them.

CLAUDE.md's *"One concept = One authority = One source of truth"* is enforced hard for contracts,
schemas and registries, and **not at all for the numbers that decide money.** This is not
hypothetical: `MAX_DAYS_TO_LIFECYCLE_WINDOW = 14` sat in `pool.py` with a premise (15m is the
workhorse) that the cost model had already killed, and the board read **0 promotable against 900
candidates on file** until someone went looking (BUILD_HISTORY, 2026-08-02). 601 constants of the
same shape remain.

**Do not do this as one refactor.** It is the live money path, and a sweep that moves 602 values
is a sweep nobody can review. The tractable first slice is the class that already caused an
incident: constants that encode an **operator decision or a cost premise** (promotion thresholds,
lifecycle windows, fee/slippage assumptions, stop and sizing multiples) — perhaps 30-50 values.
Give those one owner with the premise recorded beside each, and leave pure mechanics
(buffer sizes, retry counts, format widths) where they are.

### G2. The dead capability lane — 3,311 LOC, zero importers

```
grep -rn 'read_only_entry\|protected_governance_state' --include=*.py runtime/mvp_runtime/
```

`runtime/read_only_entry/` (1,877) + `runtime/protected_governance_state/` (1,434) are not
imported by the live runtime at all — the single hit is a doc comment in `audit.py`.
`docs/ARCHITECTURE_REVIEW_RECORD.md` (finding C) already identified this as *"the only genuinely
safe, self-contained C slice"* and listed what must move in lockstep: `deferred/DEFERRED_ARCHITECTURE.yaml`
(`implementation_candidates`), `scripts/validate_i0_5_2/3/4/5*`, `scripts/build_i0_5_2/3/5*`, and
the CI patterns in `scripts/gate_matrix.py`. It triggers the full CI matrix.

Safe, mechanical, and **not urgent** — the material is governed and indexed, not loose. Do it when
something else already requires a full-matrix run.

### G3. Diagnostics have outgrown their index — 1,188 codes, 34 error classes

```
grep -rhoE '"[A-Z][A-Z0-9_]{6,}"' --include=*.py runtime/ | sort -u | wc -l
```

`audit.py` alone defines 84 distinct codes. A large `reason_code` vocabulary is correct for a
fail-closed system — the problem is that **there is no index**, and reading a code back to its
cause is the operator's main diagnostic path. Nothing checks for duplicate or near-duplicate
codes across modules either. A generated index (code -> module -> the condition that raises it)
would be cheap and is the thing missing, not fewer codes.

### Considered and deliberately NOT recommended

- **`live_*` decomposition** (14 modules, 7,142 LOC; `live_route` and `live_readiness` each import
  13 siblings). The boundaries look like PR order rather than responsibility — but this is the
  money path, and re-cutting it buys tidiness against real risk.
- **The eight functions over 300 lines** (`run_crypto_cycle` 475, `handle_operator_message` 423,
  `run_task` 409, `scheduler._execute` 387, …). Same reasoning; `_execute` did get its missing
  final `else` in #434, which was the part that was actually a defect.
- **Trimming the validation surface** (36 of 75 schemas and 44 of 94 contracts describe disabled
  or deferred capability). `ARCHITECTURE_REVIEW_RECORD.md` finding C parked exactly this and the
  reason still holds: the gates genuinely read it, so it is dormant-but-governed, not dead.

### One number that changed and should not be misread

`runtime/` is 53,806 LOC, of which `crypto/` is 24,642 and the read-only kernel — the
"governance core" of CLAUDE.md's *"strong governance core, thin deterministic runtime"* — is
**1,938**. The description has not matched the shape for some time. That is an observation about
the doc, not a proposal to restructure the code.

---
## H. Equity-perp lane (Hyperliquid HIP-3) — code merged, S0 answered, **waiting on one grant**

**This section exists because the lane was not in this file at all**, while three PRs of it
merged. A reader arriving on a fresh machine and starting here — which is what this file tells
them to do — would not learn that the lane exists, that `runtime/mvp_runtime/crypto/
candle_archive.py` is on `main`, or that a regulatory decision is what stands between it and
running. Its status lived only in `docs/proposals/`, and this file's own header warns that a
proposal is not the authority for status.

**What is merged** (2026-08-04): the archive store and `refresh_book` (#484), a
`record_sha256` check on read plus gap reporting (#488), a bounded-hash read path (#490), its
own selector axis `MVP_CANDLE_ARCHIVE` and the `candle_archive` scheduler kind (#486), and the
correction that kind is **not** exempt from the kill switch (#492). The measurements behind it
are in `EQUITY_PERP_S1_MEASUREMENTS_V0.1.md`.

**Nothing runs.** No schedule is registered, and the gate is closed twice over — verified by
running it rather than by reading the code, 2026-08-04:

```
MVP_CANDLE_ARCHIVE=''            -> NoCandleArchiveCollector, collect(): ARCHIVE_NOT_ENABLED
MVP_CANDLE_ARCHIVE='hyperliquid' -> SafetyGateBlocked: ACTIVATION_MISSING
                                    (no safety_flag_activations/hyperliquid.json)
```

The second line is the one worth knowing: **the env alone fails at selection**, before any
collector is constructed. This machine holds **eleven** grants and no `hyperliquid`; the count
is given rather than the list, because an inventory in this file goes stale the first time one
is issued or removed — which it did, within the hour of this section being written. Ask the
directory, or the board's 권한 line.

**One grant this section named is gone, and why is worth reading before assuming the grant model
is uniform: see section C, *"An expired grant pinned the board's expiry warning"*.** `live_trading`
does **not** take this path at all — Thomas moved it to `safety_gate.select_env_gated` on
2026-07-28, the environment opt-in alone, no per-machine grant and no expiry. So "gated by a
grant" describes the archive and every other capability, and not the one that moves real money.

**S0's record is complete as of 2026-08-04 — and it no longer blocks S1.** Both `〔확인 필요〕`
markers in Appendix A of `EQUITY_PERP_LANE_V0.1.md` were answered by Thomas, so the appendix is
no longer a draft and this file may cite it. Read the appendix for the wording; the two answers
in short:

1. **Strength: provisional, deliberately.** No legal check has happened and the record still
   rests on *"문제는 안 될 것 같다"*. The answer makes that the lane's operating condition rather
   than an open question — and draws the line it implies: **live orders (S3 and later) do not
   open until this is promoted to settled.** S1 is read-only with no order path, so it is
   unaffected.
2. **The reduction holds.** Offshore own-account derivatives trading is answered by this
   project's own practice — Binance USD-M live, and as of 2026-08-04 a held position with both
   protective legs — so the remaining question is the US-equity underlying alone. The venue-shape
   asymmetry (a builder-deployed DEX whose deployer holds `haltTrading` and `setOracle` against a
   centralized exchange account) is classed as **counterparty risk rather than a regulatory
   question**, and re-review conditions 1 and 2 already cover that axis.

**So what blocks archiving is now the grant alone**, and the ordering below is unchanged in
substance: it is one step shorter.

**No approval record names this lane.** 53 records in
`.runtime_governance_state/approvals/approvals.jsonl`, zero mentioning equity / Hyperliquid /
HIP-3 in any human-readable field; they are runtime action approvals
(`crypto.strategy_pool.retirement` and kin), which is a different thing from a lane decision.

**Nothing in code references S0, and that is the design rather than a gap.** S0 is enforced
*through* the grant — Thomas does not issue `hyperliquid` until it is ratified, and without the
grant the selector fails closed. What that leaves is worth stating: the enforcement is a human
commitment, and no record ties the grant to S0, so a grant issued later for any reason would
not reveal that S0 had been skipped. If that link should be durable, it is a line in the grant's
own record, not a new gate.

**In order, what is still needed to archive a single candle:** ~~S0 ratified~~ (done 2026-08-04)
→ the `hyperliquid` grant issued on this machine → `MVP_CANDLE_ARCHIVE=hyperliquid` in the
scheduler service → a registered `candle_archive` schedule. **Only the grant is Thomas's now**;
the last two are minutes.

**The clock is the reason this order matters.** `candleSnapshot` serves at most 5,000 candles
and nothing behind them, so 15m history older than ~52 days and 1h older than ~208 is
unrecoverable once it rolls — measured, not projected: `xyz:SP500` listed 2026-03-18 and the
venue already returns only 52 days of its 15m bars. Every day before the grant is a day of the
two timeframes the factory can otherwise never reach.

---

## Per-machine setup that does NOT travel via git

A fresh machine has the code but not the local runtime state (gitignored, per CLAUDE.md). To actually
*run* the agent there, re-do the local activation once:

- Core activation pointer: `.runtime_governance_state/CURRENT_CORE_RELEASE.yaml`
- Safety-flag grants: `.runtime_governance_state/safety_flag_activations/*.json`
- Control state + ledger + schedules under `.runtime_governance_state/`

None of this is "planned work" — it is per-machine state you re-establish with the CLAUDE.md
"Core activation" steps + `scripts/activate_safety_flag.py`.

**What the 2026-08-02 live incident leaves on the Docker host and nowhere else.** A fresh machine
cannot investigate it — it has the code but none of the evidence, and it is not armed to reproduce
it. All of the following are gitignored:

- `crypto/live_order_counter.json` — the daily order count, and the *only* field that said two
  live orders had gone out. `live_positions/` was empty and `live_outcomes.jsonl` absent, because
  a naked-close leaves no ordinary outcome, so both of those read as "nothing has traded".
- `runtime_ledger/records.jsonl` — the cycle records carrying `live_opened` (entry fill, bracket
  result, `naked_close`). Grep `live_opened`; the useful keys are `bracket[].error`/`error_detail`,
  `naked_close.result.fill`, and `status`.
- `runtime_ledger/audit_events.jsonl` — the P5 decision and the ENTRY event. It does **not** carry
  the bracket rejection, which is why the cause is still unknown.
- `crypto/crypto_live_candidate_ack.json` — **orphaned since 2026-08-03 (#473).** It was the
  operator acknowledgement holding Gate 0 open, and this line said the door closed without it.
  Neither is true now: Gate 0's runtime enforcement is removed, nothing reads this file, and the
  live door is the bracket breaker alone. The file is still on disk on any machine that signed
  one — inert, but it reads like live authority, so it is worth deleting as the service user:
  `docker exec thomas-scheduler rm .runtime_governance_state/crypto/crypto_live_candidate_ack.json`
- The rollback image tags (`thomas-agent-runtime:rollback-pre-<PR#>`) are in the host's Docker
  image store, not in git.

So: **do the live-order investigation on the Docker host, and use another machine for the code,
docs and factory work.** Section F and the promotion/economics questions travel fine; this one
does not.

**What the current deployment machine has, as of 2026-08-03** — so a new machine knows what it is
missing rather than discovering it one fail-closed error at a time. Twelve grants:
`google_ai_studio`, `groq`, `openrouter` (the provider chain), `tavily_search`, `telegram`,
`binance_futures`, `binance_futures_account`, `coinalyze_market_data`, `paper_trading`,
`live_trading`, `approval_consumption`, `workspace.writer`. **A grant is per-machine and
per-provider, and an env var alone fails closed** — so a new machine reads mocks until each one it
needs is minted locally, and nothing errors to tell you that. Three prediction-venue grants
(`kalshi_market_data`, `polymarket_market_data`, `binance_prediction`) were **deleted** 2026-08-02
with section A's lane and must not be re-minted.

Twenty-two schedules are registered here: `crypto_pipeline` (15 min), sixteen `crypto_factory`
(daily — fifteen are one per symbol × timeframe over BTC/ETH/BNB/SOL/DOGE × 15m/1h/4h, and the
sixteenth has an **empty request**, which is a different job rather than a duplicate),
`crypto_report`, `crypto_propose`, `crypto_breaker_watch` (hourly), `crypto_data_review` (weekly),
`ledger_rotate` (daily). These live in `.runtime_governance_state/schedules.jsonl` and do **not**
travel either — a fresh machine ticks nothing until they are re-added. Read them off the machine
(`scheduler_cli list`) rather than from this paragraph; it was written with fifteen factory rows
and was wrong within the hour, because another session added one.

The ledger does not travel, which has one consequence worth stating: **the canary-evidence count
and the paper P&L that gate the live door are per-machine**, so a new machine reads `0/3` and an
empty paper record however far along this one is. Ask the board
(`python -m runtime.mvp_runtime.crypto.live_readiness`), never this file.

---

## How to use this file from another computer

```
git pull
```

Then open this file, or just ask Claude Code "남은 작업이 뭐야?" — it will read
`docs/REMAINING_WORK.md` and list the unchecked items above.

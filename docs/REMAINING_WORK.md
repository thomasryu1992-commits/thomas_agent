# Remaining Work — canonical to-do list

**This is the single place to answer "what's left to build?" from any machine.**
It is committed to git on purpose: per-machine memory does not travel between computers,
so the durable hand-off lives here. On a fresh machine: `git pull`, then read this file.

Last updated: **2026-08-10** — **three sections moved and none of them said so here.** F9's
decision was taken and shipped (#638 + #651, 2026-08-09): the rotation runs cohort schedules and
F9 is now the working rather than an open ask — its first fire had not landed at 02:55Z
(`scripts.pooled_mint_check`: 0 pooled rows). Section **G is exhausted** (2026-08-09) and states
what would re-open it. Section **H's (a) cost re-derivation is done** (2026-08-09), leaving (b)
waiting on symbol age rather than on code.

**Read this before picking work: nothing in this file is currently an unblocked build item.**
Surveyed 2026-08-10 — A closed, B code-complete (two operator actions), C blocked on live
evidence that is not being produced (3 live outcomes total; nothing in the live tier), D's
PROGRAM route explicitly not recommended yet, F's remainder a direction plus a deferred lever,
G exhausted, H(b) a clock into 2027, I awaiting Thomas. That is a real state, not an oversight —
but it means the next build item comes from a decision or from new evidence, not from this list.

Earlier — **2026-08-06, the live round trip is complete.** The stop refusal that headed
section C since 2026-08-02 was resolved on 08-04 (#460, the confirm race), and on 08-06T04:44Z
the first two live trades **closed and recorded**: `live_outcomes.jsonl` exists, two rows, both
stops, **−1.1078R** (ETHUSDT, held 52.5h) and **−1.0000R** (DOGEUSDT). The exit path is
demonstrated end to end. What it leaves is narrower and is in section C: ETHUSDT's stop filled
**23.5 bps past its trigger** against a cost model that assumes 3.0, and neither a target nor a
time exit has ever run.

Earlier on 2026-08-04 — the live leg held a position again, observed 05:00Z with both bracket legs
`placed: true, status: NEW`.

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

And **section H**, added because the equity-perp lane was **not in this file at all** while
three PRs of it merged — a reader starting here, which is what this file tells them to do,
could not have learned it exists. **It now runs**: enabled 2026-08-04, archiving 100 books an
hour, 354 books held. `S0`'s two `〔확인 필요〕` markers were answered the same day — the strength
stays provisional by decision, which draws the line at live orders and leaves read-only S1
untouched, and the question-reduction holds.

> **This paragraph said "**runs nothing**: no `hyperliquid` grant exists on this machine and
> the selector fails closed at selection", and both halves were wrong by then.** The grant had
> already been removed by #496 — there is no `hyperliquid` grant to be missing — and the lane
> was two operator steps from running, not blocked. Corrected 2026-08-05. What the day of
> actually running it cost is in H0, and it is the part no amount of reading the code produced.

Earlier: **2026-08-03** (`main` = `44c9b36`), handing off to another machine. **The headline
is at the top of section C and nothing else in this file outranks it:** the runtime placed its
first two autonomous live orders on 2026-08-02 and the protective stop was refused both times, so
it could not hold a position. **That is resolved as of 2026-08-04** — the cause was the confirm
race (#460), and the runtime is holding an ETHUSDT SHORT opened 00:13:57Z with both bracket legs
`placed: true, status: NEW`. **The round trip completed on 2026-08-06**: two closes, both stops,
both recorded (−1.1078R and −1.0000R). What is open moved again with that — realized stop
slippage of 23.5 bps against a modelled 3.0, and no target or time exit has yet run. The one
build item — **nothing counted repeated bracket
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
> Observed on the host from local records only — first at 2026-08-04T05:00Z, **re-read
> 2026-08-06T02:15Z**:
>
> | | |
> |---|---|
> | position | ETHUSDT **SHORT** 0.022 @ 1859.14, notional 40.90 USDT |
> | opened | 2026-08-04T00:13:57Z — still open at the re-read, **held 50.0h**, `holding_candles: 11` of 26 |
> | `live_opened.bracket[0]` | SL @ 1900.5 — **`placed: true`, `status: NEW`** |
> | `live_opened.bracket[1]` | TP @ 1776.71 — **`placed: true`, `status: NEW`** |
> | `live_bracket_failures.json` | `consecutive: 0`, last failure 2026-08-03T04:28:58Z (unchanged across both reads) |
>
> **The re-read is what makes this a capability rather than an incident.** At the first
> observation the position had held 4.75h and two candles, which is consistent with a bracket
> that happened to place once. Eleven candles and two days later, on the same `position_id`,
> it is not: the leg holds, and the thing that used to close every entry within seconds is gone
> rather than quiet.
>
> The breaker was cleared 2026-08-03T15:51:37Z with the written reason *"#460 confirm-race fix
> deployed; cause addressed"*, and nothing has tripped it since. `placed: true` on the stop leg is
> the exact field that read `false` in the incident below, so this is the measurement that
> paragraph asked for rather than an inference from silence.
>
> **The exit path ran, 2026-08-06T04:44:13Z — and it recorded.** This paragraph asked whether a
> close would reach the ledger at all, and `live_outcomes.jsonl` now exists with two rows. Both
> stops, both `r_basis: filled`, both losses:
>
> | symbol | held | entry → exit | result | realized |
> |---|---|---|---|---|
> | ETHUSDT SHORT | 2026-08-04T00:13:57Z → 04:44:13Z (**52.5h**) | 1859.14 → 1904.96 | **−1.1078R** | −1.008 USDT |
> | DOGEUSDT | 2026-08-05T04:14:06Z → 04:44:13Z | 0.06975 → 0.07054 | **−1.0000R** | −0.849 USDT |
>
> **The −1.1078 is the number worth reading.** ETHUSDT's stop rested at 1900.5 and filled at
> 1904.96 — 4.46 adverse on a 41.36 risk unit, so **0.108R of slippage past the stop, 23.5 bps**
> against the cost model's `DEFAULT_SLIPPAGE_BPS = 3.0`. DOGEUSDT's filled exactly at its stop.
> Two fills are not a distribution and a stop-market on a fast move is the leg most prone to it,
> but that constant is INHERITED and unmeasured, and §G1 records these as the first two
> observations against it.
>
> **Both `closed_at_utc` are the same second**, which is the settlement pass stamping them
> together rather than two simultaneous fills — read that field as when the runtime recorded the
> close, not when the venue filled it.
>
> **What is still untested:** the naked-close accounting from #470–#472 merged *after* the last
> naked close and still has never fired on a real one, and no live trade has yet closed at a
> **target** or on `max_holding_bars` — both stops is one exit path of three.
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

### Three live closes on 2026-08-18 came from the Binance mobile app, not from this runtime — recorded 2026-08-18

`venue_external_close` is defined in `crypto/live_leg.py` as the close this runtime did not send and
no bracket leg performed. Three rows carry it on 2026-08-18, and the venue names their origin: each
one is a `reduceOnly` `MARKET` order whose `clientOrderId` carries Binance's **`ios_`** prefix — the
marker for an order placed by hand from the mobile app.

| position | strategy | opened | venue close | detected | close order | entry → exit | realized |
|---|---|---|---|---|---|---|---|
| `live_position_a73ca2db540ec074991a` | S005-GEN-833 (`cand_3b14f39ffeb1f246ce7b`) | 2026-08-17T23:59:11Z | **02:54:47Z** | 04:44:12Z | `ios_r0kk5Z07by52itCR0vX1` | 64521.0 → 64090.1 | +1.2927 USDT |
| `live_position_72764f6ca0d6063bef1e` | `PROBE-probe_batch_bbca200a67aae9570a36` | 03:23:25Z | **04:30:19Z** | 04:44:12Z | `ios_aZLlW33qFedZgYX2qlVe` | 75.37 → 75.69 | +0.0224 USDT |
| `live_position_a5c59aacb777bcebb801` | `PROBE-probe_batch_bbca200a67aae9570a36` | 06:32:37Z | **07:12:49Z** | 07:14:13Z | `ios_UUfL5f4VrL4jLMzGWkh7` | 75.94 → 76.09 | +0.0105 USDT |

**All three closes are Thomas's own, stated 2026-08-18.** This record is that statement, because the
outcome row cannot carry it: no tool writes an operator's reason onto a close.
`scripts/record_unreported_live_order.py` is narrow to a canary order's missing audit append and
refuses a general operator-note verb by construction, so §C is where it goes. What the ledger does
get right on its own is the exclusion — `venue_external_close` is deliberately kept out of the names
that feed a strategy's R statistics, so the +1.2927 does not credit S005-GEN-833 for a human's exit.

**Nothing here is unexplained venue behaviour.** The two probe closes were first reported as not
made by hand and then confirmed as Thomas's own within the same session; the venue's `ios_` marker
is the whole account of all three. The batch's poor sample yield on 2026-08-18 therefore has one
cause, and it is not the probe: a hand close ends the position before its stop can be touched, and a
stop that is never touched is the one thing this instrument cannot measure. Neither probe cost the
batch a cell — `resolve_open_cell` returns any non-`stop_loss` close to `EMPTY` — so what they cost
is the fire, not the sample slot.

**Read `closed_at_utc` as the detection pass, not the fill.** The gap ran 14 minutes on the probes
and **1h49m** on BTCUSDT. Same reading the 2026-08-06 pair above already required, now with the
venue's own `time` beside it to size the lag.

#### The position-mode switch this sits inside — and it has not taken effect

Thomas set the account to hedge (양방향) mode by hand at the venue. **It did not apply.** Read back
read-only from `GET /fapi/v1/positionSide/dual`, 2026-08-18:

    {"dualSidePosition": false}

The venue refuses a position-mode change while any position or open order exists, and BTCUSDT has
held a position plus two resting bracket legs continuously since 04:59:11Z. Consistent with that,
every entry in the window — the 04:59 BTCUSDT strategy entry, the 06:32 SOLUSDT probe — was accepted
without a `positionSide` parameter, which hedge mode rejects as `-4061`.

**If it ever does take, this runtime breaks.** `positionSide` and `dualSidePosition` appear nowhere
in the codebase; every order is built for one-way mode. `tunables.py` already names the exposure:
`MAX_LIVE_POSITIONS_PER_SYMBOL` is classified `VENUE` on the stated ground *"the venue nets per
symbol in one-way mode, so a second book cannot exist"*, with *"hedge mode, which would make this a
choice rather than a fact"* as what would reopen it. The per-symbol position store
(`live_positions/<SYMBOL>.json`, one book per symbol) rests on the same assumption. Hedge mode is
therefore a code change, not a venue setting — and until it is one, the setting must stay off.

> Real money. The full operator go-live checklist (grants, confirmation phrase, caps, kill switches)
> is in `CRYPTO_LIVE_EXECUTION_V0.1.md`. Claude does not run it, does not handle real keys, and does
> not enable live trading — every step there is Thomas's.

### One live outcome row is wrong and there is no way to correct it — recorded 2026-08-23

`live_out_e56cf310dcc07d9c6edd` (BTCUSDT, closed 2026-08-21T09:03:43Z) records
`realized_pnl_usdt 77.5357` / `result_R 398.02720739` for a trade that lost **0.1728 USDT,
-0.887R**. It is the only one of the 28 rows in `live_outcomes.jsonl` that does not satisfy
`(exit - entry) * qty` under its own sign convention; the other 27 match to eight decimals.

The recorded figure is exactly `0.002 * 77708.50 - 0.001 * 77881.30` — a close twice the size
of the open, priced against the book's entry quote. The stop leg is a `closePosition`
STOP_MARKET and the venue treats that as Close-All, so it closed what was actually open; the
book held half of it. Four and a half minutes earlier the cycle had halted the symbol with
`LIVE_ROUTING_BOOK_DRIFT` and `live_settled=null`, which is that same disagreement seen from
the other side. **The recurrence is fixed** (#752 gives the bracket-leg settlement the quantity
check its two sibling paths already had) and **the board no longer vouches for the row** (#753).
This entry is about the row itself, which both of those leave in place.

**There is no correction mechanism, and building one is the open item.** `live_pnl.py` has no
supersede path; re-appending the same `settlement_id` is rejected; two rows sharing an
`outcome_id` raise `LIVE_HISTORY_DUPLICATE`, which fails **every** live-history read closed —
the breakers, the risk guard and the promotion board at once. The ledger is fsync append-only.
And the row passes its own `record_sha256`, because the corruption happened before the hash was
taken, so no integrity check will ever flag it. A correction therefore needs a designed record
type (void / supersede) with its own schema and governance, not an edit.

**Hand-editing the value is the trap, not the shortcut.** It would pass the hash if recomputed,
and it would leave no record that anyone changed a money figure — on the one ledger whose whole
purpose is to be the thing nobody can quietly change.

**What the row still costs, after #752 and #753:**

| consumer | effect |
|---|---|
| daily / weekly loss breakers | **none, from 2026-08-24T00:00Z** — `_pnl_since` windows on `exit_time`, so the row leaves the weekly window at rollover and left the daily one on 08-22 |
| drawdown breaker | under-reports by **0.887R** against a 10R limit, permanently. The phantom entered `equity` and `peak` together, so it did not inflate the gap — it anchored the peak on itself and erased a real 0.887R drawdown |
| consecutive-loss breaker | none — `PROBE-` lineage is skipped in both directions |
| lifecycle / promotion / retirement / `live_allowance` | none — they read paper outcomes |
| scheduled dashboard report | none — paper only |
| `live_promotion` evidence board | flagged and excluded from its pass count by #753 |

**`drawdown_excluded_strategy_ids` is not the answer, and it was measured rather than assumed.**
That mechanism exists for a lineage an operator deliberately retired, and it fails three ways
here: it is semantically wrong (this is a bad number, not a retired strategy); it would drop the
17 other rows of the same probe batch; and it does not even produce the true figure — excluding
the batch gives a current drawdown of **-2.9901R** against a truth of **-0.8871R**, which is
further from correct than leaving it alone.

So the row stays, and the standing cost is 0.887R of drawdown headroom that is not real. **Do
not "fix" it by widening a limit**, and do not read any all-time live P&L total without
subtracting it — cumulative realized reads +80.88 USDT where the truth is +3.17.

---

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

- [ ] **The assistant can only restart the runtime by re-arming live trading — raised 2026-08-05,
      awaiting a Thomas decision.** `control.ControlState` has one global dimension, so the switch
      door's `enable` has exactly one effect: `CMD_RESUME`, which restores the analysis path and
      the live order path together. The assistant's only key to a halted runtime is therefore the
      **RED** `runtime.trading.enable` approval — correctly labelled, and used for doors it was
      not minted for. The design, the evidence it rests on (only two consumers of
      `execution_allowed` gate trading; exits are already ungated by construction) and the five
      decisions it needs are in
      [`ASSISTANT_RESUME_SCOPE_SPLIT_DESIGN_V0.1.md`](runtime-contracts/ASSISTANT_RESUME_SCOPE_SPLIT_DESIGN_V0.1.md).
      **Nothing is implemented, and nothing should be until D1–D5 are answered** — it adds a
      dimension to the one kill switch this runtime has, on a machine that trades live.
      Not the same thing as PR #535's `DOMAIN_EFFECT_MISMATCH` tripwire, which covers the
      *domain* axis of that same state and is already handled.

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

- [ ] **Answering a question the runtime asked costs a model call — observed 2026-08-06, left
      alone.** `notify_operator` pushes to the ONE registered private chat, and that chat is also
      the intake channel. So there is no way for Thomas to *reply* to a notification: a plain
      message from him is a task request, gets planned, runs, and comes back as an analysis of
      his own answer. The registry shows it — `"hi"`, 2026-07-29, `DELIVERED`.

      Found while sending the D1/D3/D4 decision request for
      [`ASSISTANT_RESUME_SCOPE_SPLIT_DESIGN_V0.1.md`](runtime-contracts/ASSISTANT_RESUME_SCOPE_SPLIT_DESIGN_V0.1.md).
      It is not a wording problem and no phrasing in the outbound message fixes it — the two
      roles share one transport.

      **Deliberately not fixed, and the cost of being wrong is small in both directions.** The
      damage is one model call plus a confusing reply; the answer itself lands in the task
      registry either way, so nothing is *lost*. A fix means teaching intake to tell an answer
      from a request, which is a new classification on the operator path — §16's "building for
      future possibilities" while the runtime asks Thomas something roughly monthly.

      **What reopens this:** the runtime starting to ask often enough that the replies are a
      recurring cost, or a question whose answer must not be planned as a task (anything where
      running an analysis over the answer would itself have an effect). Neither is true today.

- [ ] **Splitting the scheduler into risk / research / maintenance processes — measured
      2026-08-06, not taken.** An external architecture review (§4.3) asked for three loops on
      the premise that *"a long data job can delay position protection, and in the live stage a
      scheduler delay becomes position risk."* Measured against `main`, the premise does not
      hold here, for two separate reasons.

      **Most of the separation already exists inside the one loop.** `run_due` sorts
      `RISK_KINDS` first, `MAINTENANCE_PASS_BUDGET_SECONDS = 60` stops *starting* non-risk fires
      once a pass has spent its allowance, and every fire is wrapped so an exception is recorded
      as `failed:<code>` and the loop keeps turning. A research job that throws does not take
      the risk loop with it.

      **And protection is not on this loop at all.** `live_route._run_gated_live_leg` settles
      and protects open positions *before* it reads control state, and the protective bracket
      rests **at the venue**. A late cycle therefore delays bookkeeping and new entries — not
      protection. The review's safety argument is about a coupling this architecture does not
      have.

      Measured 2026-07-22 → 2026-08-06:

      | | |
      |---|---|
      | crypto fan-out interval (target 900s), n=727 | median **+4s**, p99 **+44s** |
      | over 2 minutes late | **3 / 727 (0.4%)**, worst +438s |
      | scheduler events | 12,554 |
      | abandoned runs (process died mid-fire) | **0** — reads unpaired-right-now, not ever; see below |
      | fire failures, all kinds, all time | **1** (`RemoteDisconnected`) |

      **The number that was absent is now on the ledger, and it says the budget bound five
      times.** When the above was written a deferral wrote nothing durable — it claims no
      occurrence, so the count lived in `run_due`'s return value and the container log, and
      neither outlives a recreation. #596 gave the deferral its own action; #598 put the pass
      **spend** and the budget it was spending against on the row, because a count alone cannot
      tell a budget sitting on its boundary from one that is not the lever. Re-read 2026-08-09
      across the whole ledger (12 files, 14,319 events, 2026-07-22 → 2026-08-09; deferral rows
      begin 08-08, the event being younger than the budget):

      | | |
      |---|---|
      | deferral rows / occurrences / passes that bound | **27 / 13 / 5** |
      | occurrences lost | **0** — each ran on a later pass |
      | by kind | `crypto_null_control` **25**, `crypto_factory` **2** |
      | pass spend where it bound (budget 60s) | min **62.6s**, median **70.1s**, max **98.8s** |
      | first deferral → serving fire | min **71s**, median **161s**, max **231s** |

      **Two of those rows change how the number should be read.** The dominant consumer is
      `crypto_null_control`, not the factory the budget was sized against: fifteen of its
      schedules fall due within seconds of each other and clear over three consecutive passes.
      And the largest bind is **1.65× the budget** — the "one group is longer than any budget
      worth setting" shape rather than a boundary case, so raising 60 would not stop it binding.
      The per-pass guarantee holds either way; what a deferred occurrence gets is not a one-tick
      slip but up to ~4 minutes.

      **Rows are not occurrences, and the difference is 2×.** A deferral leaves the schedule due,
      so an occurrence behind a burst is deferred again on every pass it waits: 27 rows are 13
      occurrences (five waited three passes, four waited two, four a single pass).
      `tests/test_mvp_runtime_scheduler.py` pins this, and a row count read as occurrences
      overstates what the budget cost.

      **And "the budget works" is observed now rather than inferred.** Counting a
      `crypto_pipeline` fire — a `RISK_KINDS` member, i.e. the thing the budget exists to protect
      — late when the gap to the previous one exceeded 960s against its 900s cadence, over 2,006
      gaps, with gaps beyond three cadences excluded as outages rather than lateness: **8 of the
      13 days before the budget had a late fire; the five days since have none, worst 923s**,
      which is tick jitter.

      **Three of the eleven late gaps are not lateness, and the first reading of this said they
      were.** The three largest — 1,854s / 1,830s / 1,806s — each *contain* an `abandoned`
      recovery row, so each spans a scheduler restart rather than a slow pass. Drop them and the
      worst genuine gap is **1,338s**, while the eight that remain cluster at 07:55–08:20Z, which
      is the factory morning burst the budget was built for. The mechanism reads *better* after
      the correction than before it, which is the reason to make it rather than leave the larger
      number standing: quoting 1,854s credits the budget with having fixed a container restart.

      **It stays observational, and the confound is real:** the factory rotation was cut in the
      same week (15m dropped 08-04, 1h frozen 08-06), which shortens passes on its own, and 08-04
      was already clean one day *before* the budget went live. The two changes are not separable
      from this data.

      **The `0` in the table above answers a different question than its label.** The ledger
      holds **15** `abandoned` rows inside that window (2026-07-25 → 08-06): 10 `pm_scan` from the
      since-deleted predmarket lane, 3 `crypto_pipeline`, 1 `crypto_propose`, 1 `candle_archive`.
      A live `find_abandoned_runs()` call returns 0 anyway, and correctly — `abandoned` is itself
      a terminal action, so recovery *pairs* the run it recovers and the helper reports only what
      is still unpaired **right now**. "Nothing is currently unrecovered" is not "no process ever
      died mid-fire", and the row's label promises the second. **Consequence for the condition
      below: the reopen test must read `action == "abandoned"` rows, not the helper**, which by
      construction cannot ever satisfy it. What the ledger cannot say is *why* a run died — the
      row is rebuilt from the orphaned `started` and carries no cause — and on a host that
      recreates containers several times a day a deploy is the likelier reading than the OOM the
      condition is aimed at. Likelier is not established, so this is written down rather than
      dismissed.

      **The one exposure the in-process design cannot close**, stated so the deferral is not
      read as "no risk": the budget bounds *starting*, never *duration*, and a fire already
      running is deliberately never interrupted (cutting a factory or archive mid-write trades a
      latency problem for a torn record). So a single hung fire blocks everything behind it in
      that pass, without limit. ~~Fifteen days say it has not happened — the worst observed is
      +438s~~ — no in-process change can make it impossible, and **it has now happened: twice in
      four days, attributed to the second, measured 2026-08-15.**

      #704 (merged 2026-08-12) added mint-time ablation, and on the days the drawn conjunctions
      make it expensive the 4h cohort factory fire runs ~6.5 minutes where it ran ~75s. Both
      times, `crypto_pipeline` — a `RISK_KINDS` member — was due mid-fire and fired the second
      the factory fire ended:

      | day | 4h factory fire | pipeline gap (960s = late) | attribution |
      |---|---|---|---|
      | 2026-08-12 | 391s, ended 08:19:10 | 1,202s, fired **08:19:10** | exact to the second |
      | 2026-08-15 | 402s, ended 08:19:44 | 1,225s, fired **08:19:44** | exact to the second |

      Three notes so this is read at its true size and no larger. The cost is ~5 minutes of
      entry/bookkeeping latency on a 4h path whose protective bracket rests at the venue —
      bounded, not an unprotected position. The long fires are the day's conjunction mix, not a
      new floor: 08-13/08-14 ran 72–124s and were clean. And the same window produced two more
      "late" gaps that the restart check above **discards**: 08-11's 1,799s contains an
      `abandoned` recovery row (a restart — exactly the check this item prescribes), and
      08-15's overnight 1,806s gap has no marker and no factory fire in its window, so it stays
      unattributed rather than counted.

      **What reopens this:** one fire that hangs rather than merely running long; one abandoned
      run **that no container restart explains** (the process dying mid-fire, which OOM would
      cause and `except Exception` cannot catch — read the `abandoned` rows, not
      `find_abandoned_runs()`, and check each against a restart before counting it); the p99 above
      moving from seconds into minutes; or **a day that shows late risk-kind fires and deferrals
      together** — the deferral rows now make that a query
      (`read_scheduler_events()` filtered to `action == "deferred"`, grouped by `created_at` for
      passes) rather than a judgement. Deferrals alone do not qualify and that is the point of
      recording them: the clustering above cost maintenance up to four minutes while
      `crypto_pipeline` held its cadence, so it is cosmetic, and the schedules should be left
      alone until the two appear on the same day.

      **The fourth condition is now met — 2026-08-12 and again 2026-08-15, the table above.**
      Both days also deferred maintenance (the 1d cohort fire waited two passes behind its 4h
      sibling), so "late risk-kind fires and deferrals together" holds literally. One precision
      that matters for the remedy: the deferrals those days were the factory's own sibling and
      the morning `crypto_null_control` burst, but the *lateness* was caused by fire
      **duration**, not by the burst — so the `--first-run-at` / reschedule capability this
      item's companion report points at targets the wrong mechanism for this instance, and the
      candidate remedies are duration-shaped (a duration-aware budget, splitting the ablation
      fire, or the process separation this item parked). Per this item's own closing sentence,
      one of those is now a fix rather than an investment. **Which, and whether — that is a
      Thomas decision; this paragraph records the evidence and decides nothing.**

      **Decided (Thomas 2026-08-17): process separation.** By then the re-measurement had
      moved the frequency from "twice in four days" to four of the last six (08-16 337s and
      08-17 384s joined, each with the same second-exact pipeline gap), so the exposure is
      the daily cost of lattice-era mints, not a bad-day tail. The duration-aware budget and
      the ablation-fire split were passed over, not refuted. Design and its own open
      decisions: `docs/proposals/FACTORY_FIRE_PROCESS_SEPARATION_V0.1.md` — the compute
      already has a pure seam (`run_factory`), the child writes nothing but a spool, and the
      scheduler stays the single writer. §3 was approved as proposed the same day and the
      separation is implemented: the fetch stays in-pass, the pure compute forks, and
      `_collect_factory_child` closes the bracket on a later pass. The exposure this item
      measured ends when an image carrying it is deployed.

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

**What that decision costs is half the mint budget, and the number was not written down.**
Everything above measures fusion's *effect*; this is its *share*. A fire mints
`DEFAULT_BATCH_SIZE = 4` seeded specs and `FACTORY_FUSION_PAIRS = 4` fused ones per context, so
it is **exactly half by construction** — not an emergent ratio. `scheduler.py` chose 4 knowing
it: *"4 makes the fire half-crossover; past that the seeded rotation — the only path by which a
NEWLY ADDED family ever enters the store — starts losing its share of each fire."* Measured over
the store, the share is a policy history rather than a constant:

| minted | total | crossover | share |
|---|---|---|---|
| 2026-07-23 → 07-25 | 4 / 64 / 64 | 0 | 0% |
| 2026-07-26 → 07-30 | 90–115 | 30–32 | 33% |
| 2026-07-31 → 08-03 | 120 | 60 | **50%** |
| 2026-08-04 (fire only) | 80 | 40 | **50%** |

The store-wide figure is **434 of 1,556 (28%)** and should not be quoted as the policy — it
averages in the three days that ran at 0%. The 2026-08-04 row is the fire alone: that day's raw
count is 416, of which **336 are `provenance: mvp_rescore`** appended by #503 and not mints at
all, so a naive by-day count reads 10% and understates the policy fivefold.

**Why the share belongs beside the effect.** The paired test says a child reproduces its parents
(IS −0.0034, HO −0.0062, both CIs spanning zero) while closing 0.51× their trades — so half the
budget buys no measured edge and costs judgeability. And the cost falls on the one path that
cannot be substituted: seeded draws are how a NEW family enters the store at all, which the
rotation fix (#489) has just made matter more. The first fire on the phased rotation minted 20
distinct base families across 10 contexts — but at 1–3 candidates each, because the seeded half
is what those 20 families were sharing.

**Still not a recommendation.** `FACTORY_FUSION_PAIRS = 4 → 2` would move the share to 33% and
roughly double the seeded supply per family, and that is the same kind of mint-time decision as
the depth refusal above — judged over generations, not days (the `#420` error). What is recorded
here is only the number the decision needs and did not have.

**And it should not be taken, because #523 solved it better — measured on the first full day,
2026-08-05.** `_fusion_improvement` refuses a child that does not beat the **maximum over its
parents** on `expectancy` *and* `champion_score`, so the budget is untouched and only the
children that earned their slot are stored. The first fire under it went 59 fused → **6**, and
the two new reasons account for the whole drop:

| fired | fused | rejections |
|---|---|---|
| 2026-08-03 | 60 | `duplicate_rule_hash` 31, `too_many_conditions` 3, `no_trades` 8 |
| 2026-08-04 | 59 | `duplicate_rule_hash` 65, `no_trades` 18, `too_many_conditions` 4, `holdout_unjudgeable` 3 |
| **2026-08-05** | **6** | **`no_expectancy_gain` 46, `champion_score_regression` 11**, `duplicate_rule_hash` 22, `holdout_unjudgeable` 7, `no_trades` 6 |

**~79% of fusion attempts (57 of 72) fail to improve on a parent** — the paired test in this
section, arriving as a production yield rather than a study. The effective crossover share of a
mint is now **13%** (6 of 46) against the 50% the constant still nominally allocates, so the
budget number above is now a ceiling that no longer binds. Lowering the constant would cut good
fusions with bad ones; this cuts only the ones that lost to a parent, which is what the
measurement actually said to do.

**"The budget is untouched" was the half of it that was wrong, and one more fire showed it —
2026-08-06.** Untouched by the *rule*, yes; unspent in fact. The allocation had no other
claimant, so every pair fusion refused was a mint the fire simply did not make:

| fired | contexts | seeded | fused | fused/context |
|---|---|---|---|---|
| 2026-07-31 → 08-03 | 15 | 60 | 60 | 4.00 |
| 2026-08-05 | 15 | 60 | 6 | 0.40 |
| **2026-08-06** | 5 | 20 | **0** | **0.00** |

The 08-06 fire recorded `generated=4/4 fused=0` on all five contexts — 20 rows where the budget
allowed 40 — and the binding constraint had already moved. On 08-05 it was the child bar (92
attempts, 6 stored, `no_expectancy_gain` 46 / `champion_score_regression` 11). On 08-06 it was
the **parent pool**: 10 attempts in total, because `holdout_permits_parenting` leaves 218 of the
store's 1,681 rows able to parent and only **24** of those are 1d, which was that fire's whole
rotation slot. Neither is a defect to loosen — both are the rules doing what they were merged to
do — and both leave the seeded half carrying the search alone.

**Closed by the shortfall draw** (`run_factory`, `seeded_topup_count`): whatever fusion does not
mint of its allocation is drawn again as seeded specs from **this fire's own rotation slice**,
so the cursor is untouched and the next fire steps one slice as it always did. It takes nothing
from fusion — the batch and `_fuse_batch` run first and unchanged, so fusion gets first refusal
on every pair — which is the distinction from `FACTORY_FUSION_PAIRS = 4 → 2` above: that one
cuts good fusions with bad, this one cannot cut any. **Judge it over generations, not days**
(the `#420` error, and this is a mint-time change). What it has to move is the count of rows a
fire produces that can be judged at all — `MIN_HOLDOUT_TRADES` was cleared by 84 of a fire's
rows on 07-31 and by 5 of 20 on 08-06.

**What it does not address, stated so the two are not confused.** This recovers the *volume* a
fire mints; it does nothing about the *share* of a mint that can be judged, which fell over the
same days for an unrelated reason. `MIN_HOLDOUT_TRADES` is one absolute constant and the
rotation moved off the tiers that can reach it — seeded median holdout trades by timeframe on
today's store: **15m 91–356, 1h 44–121, 4h 12–18.5, 1d 10–17.5**, against a floor of 25. The
08-06 fire's 1d cohort medians **14.5**. The rotation is `schedules.jsonl`, which is
per-machine, so that change has no trace in this repo and no section here owns it yet;
`backtest_spec_pooled` (F2: 4h holdout tail 32 → 169, judgeable 9/12 → 12/12) is the lever
already measured against it and still unused by the factory.

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

#### What landed on the strength of this section — two selection rules, merged 2026-08-04

F1's finding is that fusion **reproduces** its parents (IS −0.0034, HO −0.0062) and that the
apparent crossover advantage is **parent selection** rather than breeding. Nothing acted on that
at either end: the child side had no bar at all, and the parent side ranked on a score carrying
no out-of-sample term. Both ends now have a rule.

**The child must beat what it came from** — #523, `FUSION_IMPROVEMENT_METRICS` /
`_fusion_improvement`. `expectancy` strictly above the maximum over the parents, `champion_score`
not below it. Compared against the maximum on each metric **independently**, because the question
is "was fusing these two worth more than keeping either", which a child that beats the weaker
parent while losing to the stronger has not answered. The two legs are asymmetric on purpose: 45%
of the score's weight (`sample_adequacy` + `parameter_parsimony`) moves against a fused child by
construction — it is the same AND-union that produces the 0.51× above — so requiring it to *rise*
would refuse on the arithmetic of fusion rather than on merit, while requiring it not to *fall*
stops the one case expectancy cannot see alone, a higher return read off a handful of trades.
Both sides are **replayed on the child's own snapshot**: 890 of the 894 parent/child evidence
links in the store sit on different candle windows and 0 on the same one, so comparing stored
numbers would have scored the market's drift between two windows and called it lineage
improvement. Refusals are `no_expectancy_gain`, `champion_score_regression`,
`improvement_unmeasurable` — fail-closed on an unreadable metric, never a silent zero.

What that refuses, over the 447 stored children whose parents both resolve:

| child better than its best parent | share |
|---|---|
| `champion_score` | **9.8%** |
| `expectancy` | 30.9% |
| `closed_count` | **1.1%** (median −67 trades) |

and **101** of the children scoring at or below their best parent had already gone on to parent a
further generation.

**A parent must have survived out of sample** — #525, `holdout_permits_parenting`. A judgeable
holdout (`MIN_HOLDOUT_TRADES`, the promotion door's own floor) that did not lose
(`expectancy >= 0`), fail-closed on absence. `rank_fusion_parents` still orders by
`champion_score`, so the pool keeps one ranking currency: this is **one bit at zero**, deliberately
not a ranking on holdout magnitude, since the unit of independence is the market period and this
store carries about ten of them. Two measurements settled that shape. `holdout_status` is the
wrong authority — 0 of the 1,366 eligible rows read CONFIRMED, and 854 read INSUFFICIENT for a
missing `stdev_r`, which is a schema vintage, so filtering on it would select by mint date and
take fusion to zero fusable buckets. And **depth alone makes the pool worse**: filtering to a
judgeable holdout while still ranking by score moves the selected parents' median holdout
−0.098R → **−0.164R**, because within the judgeable subset `champion_score` is mildly
anti-selective.

**The evidence for the parent rule is not the table in its PR.** That table reported the selected
pool's median holdout and its share ≥ 0 — both computed over the very quantity the rule filters
on, so "100% non-negative" is arithmetic rather than a finding. The non-circular test is the
**children already bred**: selection reads the *parent's* holdout while the outcome measured is
the *child's*, which are different rows. Paired within `(symbol, timeframe)` to control the tier
confound — the qualifying group is 4h-heavy and the rest 1h-heavy, and R scale differs by tier:

| statistic | cells | selected − other | cells positive |
|---|---|---|---|
| median-based | 6 | **+0.1151R** | 5/6 |
| trade-weighted | 6 | **+0.1674R** | 5/6 |

Limits, because they bound the claim: 6 comparable cells at 2–8 rows per group; 38 of 330
resolved parents are `mvp_rescore` rows, so "parent holdout as stored today" is not exactly the
holdout the fusion saw at breeding time; and the smallest cell (BNBUSDT 15m) disagrees in sign
between the two statistics. Against ~10 independent market periods the gap clears the resolution
floor, but not by much.

**What is still owed.**

- **The split-half test has not run.** Select on the first half of the holdout periods and measure
  the last three — disjoint slices, so no row is scored on the bars that selected it. It could not
  run when #525 was reviewed: `period_r` / `period_trades` arrived with #518 at 10:46 UTC and the
  store's last write was 08:53 UTC, so **0 of its 1,595 rows carry a per-period breakdown**. It
  becomes measurable after the first fire on the new code.
- **Neither rule has been through a fire.** Both merged after the 2026-08-04 08:09 UTC fire, so
  the store this section measures is entirely pre-rule and the yield cost is predicted rather than
  observed: #523 puts **1 pair in 78** past the child bar over `_trending_snapshot`'s feature grid,
  and #525 empties every fusable bucket in **3 of the 15 live contexts** (BTCUSDT 1d, SOLUSDT 1d,
  SOLUSDT 1h). They compound — the parent pool refills from the seeded rotation more slowly than
  either change assumed alone — and this is a mint-time change, judged over generations rather
  than days (the `#420` error).
- **`factory.py`'s comment for the parent rule still rests on the circular table**, because the
  correction arrived on the PR at the moment it merged. It should be replaced by the
  paired-children figures above, in the change that carries the split-half result.

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

#### That batch was run, and it confirms — measured 2026-08-04, corrected the same day

The condition above is the whole of the decision, so it was tested rather than left standing.
Every mintable family at a timeframe, 8 freshly drawn parameter sets each, minted **pooled** —
one spec with `symbol_scope` set to the whole cohort, replayed across all five legs, so each is
ONE hypothesis at five symbols' data rather than five hypotheses at one symbol's each. Verdicts
are `robustness.holdout_status`, not a re-implementation of it. Read-only; nothing appended.

| pooled mint | specs | CONFIRMED | CONTRADICTED | INSUFFICIENT |
|---|---|---|---|---|
| 4h, 34 families × 8 | 272 | **11** (4.0%) | 218 | 43 |
| 1h, 34 families × 8 | 272 | **4** (1.5%) | 247 | 21 |

> **This replaces a first pass that reported 0 and 0, and the way it was wrong is the reusable
> part.** That run built its frames with `attach_htf` alone. The scheduler attaches four legs
> before a fire, so ten feed-dependent families — all four `oi_*`, `funding_fade_*`,
> `premium_fade_*`, `xs_reversion_*` — were scored over columns that were `None` on **every**
> row. They took no trades, returned INSUFFICIENT, and were written up as *"pooling does not fix
> judgeability everywhere"*. It was not a verdict on them; it was a frame with their inputs
> missing. With the legs attached those columns are 100% populated over the 4h replay
> (`open_interest_zscore` 3000/3000, `funding_zscore` 2990/3000). `unsuppliable_features` would
> have caught it inside `run_factory` — calling `backtest_spec_pooled` directly walked around
> the guard that exists for exactly this.

**F2's condition is met.** A pooled mint batch does produce CONFIRMED holdouts where the
single-symbol search produced none — 1 CONFIRMED in 1,595 stored single-symbol rows, and that
one PROVISIONAL, against 15 in 544 pooled mints.

**Two families do it at both timeframes, and they are the same two.**

| family | tf | HO trades | HO exp | t | IS exp |
|---|---|---|---|---|---|
| `oi_unwind_short` | 4h | 33 | +0.5347 | 3.15 | +0.4768 |
| `oi_unwind_short` | 1h | 28 | +0.6303 | 3.34 | +0.1702 |
| `oi_squeeze_long` | 4h | 38 | +0.5042 | 2.79 | +0.4234 |
| `oi_squeeze_long` | 1h | 74 | +0.3027 | 2.75 | +0.3054 |

Positive in-sample **and** out-of-sample, at two timeframes — which is the property every prior
candidate in this record failed. `premium_fade_short` also confirms 4 of 8 at 4h and is
**dismissed**: its in-sample expectancy is *negative* (−0.09 to −0.13) against a positive
holdout, which is the signature of an artifact rather than an edge, and it is CONTRADICTED 8/8
at 1h.

**And none of it clears the selection correction, because the attempt count is not per family.**
`SELECTION_CONTEXT` is `symbol_scope + timeframe`, and this batch is one symbol scope at one
timeframe — so the honest count is **272 attempts**, not 8 per family. That puts the bar at
**z = 3.74**, above the best `t` here (3.34). Reporting it per family, as the first pass did,
charges 2.73 and lets two rows read as clearing. They do not.

**So the finding is narrow and specific:** pooled minting reaches CONFIRMED where single-symbol
minting does not, and the confirmations concentrate in the `oi_*` families rather than being
spread across the library. What is *not* established is that they survive the attempt count they
were drawn from.

**What this does not test.** The draws are `mutate_params` around each template's own base;
`elite_base_params` steering was not reproduced, so this is an *unsteered* pooled mint — the one
difference from what `run_factory` would actually do. Eight draws per family is also modest.

**And it changes the price of a longer replay window.** The two families that confirm are
`oi_*`, which `factory._oi_feed_reaches` gates out of any rotation whose replay exceeds
`DERIVATIVE_HISTORY_DAYS = 520` days. Lengthening the window therefore removes **exactly the
families that produced the only confirmations this record has** — a cost that reads as
bookkeeping until these numbers exist, and as decisive once they do.

### F3. The htf families refuse evidence they could produce — measured 2026-08-04, 960 specs

F1 attributes the unjudgeable half of the store to fusion's AND-union. **The seeded templates
have the same defect from a different cause**, and `htf_trend_*` shows it exactly: the family
gates on a categorical, and the classifier behind that categorical returns before it ever tests
for a trend.

```python
# features.classify_market_regime
if atr_pct is not None:
    if atr_pct >= 0.80:
        return "HIGH_VOLATILITY"                              # <- returns first
    ...
if close > ma20 > ma50 and adx >= adx_threshold:
    return "TREND_UP"
```

So `htf_market_regime == "TREND_UP"` **cannot be true on the higher timeframe's top-quintile
volatility bars**, however cleanly they are trending. Three things go with it: those bars, a
searchable ADX floor (`ADX_TREND_THRESHOLD = 20.0` is hard-coded inside the label, so 19.9 and
20.1 switch the family off and on), and trend *strength* — a 0.1% ma20/ma50 separation and an 8%
one are the same label. The first is the one that costs: 1R is `stop_atr` × ATR against
fixed-bps friction, which is the premise `volatility_expansion_*` was added on, so the family
the store shows with its least-negative holdout is structurally excluded from the bars where
friction is cheapest.

**The counter-design holds every accounting constant.** `count_free_parameters` charges literals
and not `value_from`, so swapping the categorical for a normalized continuous column is free:

| | conditions | literals | `free_parameters` |
|---|---|---|---|
| `htf_trend_long` (current) | `regime=="TREND_UP"`, `close>ma20`, `adx>=p` | 2 | 5 |
| `htf_trend_strength_long` | `htf_ma20_distance_ma50>=p`, `close>ma20`, `adx>=p` | 2 | **5** |
| `htf_pullback_long` (current) | `regime=="TREND_UP"`, `rsi<=p` | 2 | 5 |
| `htf_pullback_ext_long` | `htf_price_distance_ma20>=p`, `rsi<=p` | 2 | **5** |

`htf_ma20_distance_ma50` is `(ma20-ma50)/ma50` and carries direction *and* strength in one
condition; both columns are already computed every cycle (`features.HTF_NUMERIC_COLUMNS`) and
neither template has ever read them.

**Method.** 5 symbols × 12 draws × long/short per family, `backtest_spec` per symbol against
live frames at the current cost basis, read-only — nothing appended. Exit parameters come from
their own rng keyed by draw index, so draw *i* carries identical `stop_atr` / `target_atr` /
`max_holding_bars` in every arm and the entry rule is the only difference between them. An
earlier unpaired pass is not reported: the extra entry parameter shifted the shared stream and
the arms' exits diverged, which is the confound F1 was itself corrected for. Single-symbol
rather than pooled because the deployed image predates `backtest_spec_pooled`.

**"Judgeable" here means `holdout.closed_count >= MIN_HOLDOUT_TRADES`, and the stricter reading
gives the same answer** — stated because on the stored candidates it does not. Over the store,
requiring `stdev_r > 0` as well cuts 599 rows to 66, and those 533 are rows that traded deep
enough but predate the field, so the strict set is a **schema vintage** rather than a depth
filter and any per-group comparison over it collapses onto the newest cohort. Every spec below
was replayed fresh, so all of them carry `stdev_r` and the two definitions coincide; the `t max`
column could not be computed otherwise.

| 4h | IS trades | IS exp | HO trades | **judgeable** | HO exp | HO+% | t max |
|---|---|---|---|---|---|---|---|
| `htf_trend_long` | 43 | −0.0918 | 16 | 12/60 (20%) | −0.0848 | 25% | 0.58 |
| `htf_trend_strength_long` | 64 | −0.0532 | 24 | **29/60 (48%)** | −0.1521 | 24% | 0.35 |
| `htf_trend_short` | 34 | −0.0578 | 15 | 10/60 (17%) | +0.0051 | 50% | 1.63 |
| `htf_trend_strength_short` | 72 | −0.0688 | 32 | **46/60 (77%)** | −0.1585 | 24% | 1.10 |
| `htf_pullback_long` | 21 | −0.3132 | 5 | **0/60** | n/a | n/a | n/a |
| `htf_pullback_ext_long` | 30 | −0.2562 | 14 | 5/60 (8%) | −0.1774 | 40% | 0.93 |
| `htf_pullback_short` | 22 | **+0.0660** | 6 | **1/60** | +0.4399 | 100% | 2.24 |
| `htf_pullback_ext_short` | 50 | +0.1130 | 19 | 14/60 (23%) | +0.2321 | 71% | 1.46 |

| 1h | IS trades | IS exp | HO trades | judgeable | HO exp | HO+% | t max |
|---|---|---|---|---|---|---|---|
| `htf_trend_long` | 120 | −0.0712 | 62 | 60/60 | −0.1789 | 23% | 1.53 |
| `htf_trend_strength_long` | 207 | −0.1053 | 70 | 58/60 | −0.1998 | 7% | 0.55 |
| `htf_trend_short` | 138 | −0.0229 | 60 | 60/60 | −0.2647 | 2% | 0.06 |
| `htf_trend_strength_short` | 224 | −0.0307 | 88 | 59/60 | −0.1578 | 2% | 0.32 |
| `htf_pullback_long` | 47 | −0.2378 | 30 | 42/60 | −0.1562 | 12% | 0.86 |
| `htf_pullback_ext_long` | 82 | −0.1750 | 31 | 37/60 | −0.2895 | 3% | 1.17 |
| `htf_pullback_short` | 58 | −0.1173 | 28 | 36/60 | −0.2840 | 3% | 0.29 |
| `htf_pullback_ext_short` | 106 | −0.1046 | 33 | 36/60 | −0.2106 | 0% | −0.09 |

**Trade count and judgeability move; the edge does not.** At 4h the trend pair goes 20% → 48%
and 17% → 77% judgeable, and the pullback pair 0% → 8% and 2% → 23%, for no extra parameter. At
1h the tails are already deep, so the same change only adds trades (120 → 207, 47 → 82) and
judgeability is flat to slightly down — the extension condition can only cut a sample that was
already confirmable.

**And nothing clears.** 0 of 960 specs clear the selection-adjusted bar (z = 3.34 at 60 attempts
per family). Exactly **one** clears the uncorrected 1.96, where ~24 are expected by chance at
that attempt count — so this population is not merely edgeless, it is thinner-tailed than noise.
The best-looking cell (4h `htf_pullback_ext_short`, HO +0.2321 at 71% positive) does not clear
1.96 and reads −0.2106 at 1h; it does not reproduce across the ladder.

**One finding that stands apart from the proposal.** `htf_pullback_long/short` produce **0 and 1
judgeable holdouts out of 60** at 4h, and `htf_pullback_short` does it while carrying a
*positive* in-sample expectancy (+0.0660). That is a family which looks good in sample and can
never be confirmed, at the timeframe where four of the five routable strategies live — F1's
mechanism, in a seeded template rather than a fused child.

**What it composes with, and the pair is worth more than either half.** F2 gives one set of rules
five times the evidence; this gives a different set of rules the evidence its own gate was
throwing away. Both arrive at CONTRADICTED rather than CONFIRMED. So the un-confirmability F1
found is not a property of how much evidence a spec carries, and it is not a property of which
rules were asked — two independent ways of removing it both remove it and leave a negative
behind. The value of a template change like this is therefore **diagnostic speed**, not a
promotable candidate: it buys a verdict at 4h in weeks instead of never. That is worth
something, it is not an edge, and the two should not be traded for each other in whatever
decides this.

**The trend half shipped in #497; the pullback half deliberately did not.** Shipping means
*replacing* rather than adding — a family is +1 hypothesis on the same data and
`selection_adjusted_z` charges every attempt, so adding one would raise the bar for every other
family in the store to buy this one's judgeability. So `htf_trend_strength_*` is minted and
`htf_trend_*` moved to `RETIRED_FAMILIES` on the `volatility_squeeze_*` precedent (builder kept,
rotation entry removed, the 11 existing candidates still readable under their own name), leaving
the library at 40 families and `free_parameters` at 5.

The pullback variant is **not** shipped and should not be on this evidence: it helps only at 4h,
hurts at 1h, and its one good number does not reproduce. `htf_pullback_long/short` remain in the
rotation producing 0 and 1 judgeable holdouts out of 60 at 4h — an open item, not a resolved one.

#### The pullback family's problem is not its rule — measured 2026-08-04

Two different replacements were tried and both say the same thing, so the item above is closed
in a direction it was not pointed at. **The rule was never what made it unjudgeable; one
symbol's evidence was too thin for a conjunction this rare.** Replayed pooled across the cohort
with `factory.backtest_spec_pooled` and the rule *unchanged*, at 4h:

| 4h, same rule | specs | IS trades | HO trades | judgeable | HO exp | pos | t max | z bar |
|---|---|---|---|---|---|---|---|---|
| `htf_pullback_long` single | 60 | 21 | 5 | **0/60** | n/a | n/a | n/a | 3.34 |
| `htf_pullback_long` pooled | 12 | 124 | 38 | **8/12** | −0.0823 | 25% | 1.25 | 2.87 |
| `htf_pullback_short` single | 60 | 22 | 6 | **1/60** | +0.4399 | 100% | 2.24 | 3.34 |
| `htf_pullback_short` pooled | 12 | 112 | 38 | **12/12** | **+0.1748** | **100%** | 1.87 | 2.87 |

So the judgeability defect is fully removed without touching a condition. That also answers, for
one family, the question F2 leaves open — a pooled *re-score* (not yet a pooled *mint*) turns
un-judgeable into judged, and what it judges is **not confirmed**: nothing clears the
selection-adjusted bar, and 1h is negative on both legs (long −0.1661, short −0.3332, 0% of
draws positive).

**The one result worth a second look, and the control that undercuts it.** 4h
`htf_pullback_short` is the only positive figure this investigation has produced twice. Walking
the holdout backwards in **adjacent, non-overlapping** windows (truncating the series by 0.7 each
step makes window *k+1* end exactly where window *k* begins):

| holdout window (4h) | bars | HO trades | judgeable | HO exp | pos | t max |
|---|---|---|---|---|---|---|
| `[2100,3000)` | 900 | 38 | 12/12 | +0.1748 | 100% | 1.87 |
| `[1470,2100)` | 630 | 62 | 12/12 | +0.1539 | 100% | **2.08** |
| `[1029,1470)` | 441 | 20 | 2/12 | +0.0338 | 100% | 0.16 |
| `[720,1029)` | 309 | 2 | 0/12 | — | — | — |

No negative window, and the two deep ones agree on magnitude. Its long sibling over the same
windows is uniformly negative (−0.0823, −0.7568, −0.5062, −0.1579), so the result is not an
artifact of the windowing.

**And it still does not clear.** `t max` peaks at 2.08 against a selection-adjusted 2.87, the 12
draws are correlated (one rule, adjacent parameters, one cohort, one window set) so "100% of
draws positive" is nearer one observation than twelve, and only two windows carry real depth.

The control is in the table above it. The **same rule at 1h** produces `[4116,5880)` at
**+0.4052 with t max 3.91** — clearing even a selection-adjusted bar — surrounded by
−0.3332, −0.2676 and −0.3338. If that window had been the only one sampled it would read as a
confirmed edge. It is the cleanest demonstration this record has that **one strong window is not
evidence here**, and it is why the 4h rows above are reported as suggestive rather than found.

**What would settle it** is more independent evidence, not more draws of the same twelve: a
replay window long enough to yield four non-overlapping tails that all reach
`MIN_HOLDOUT_TRADES` at 4h (today the third is 2/12 and the fourth 0/12), or forward paper
outcomes. **What would not:** loosening the rule to raise its trade count, which changes the
hypothesis rather than testing it.

**So the open item changes shape.** `htf_pullback_*` should not be replaced, and its
unjudgeability at 4h is a *sampling* fact rather than a rule defect — which makes it evidence
for the pooled-mint experiment F2 defers, not an argument for another template edit.

**What is still owed.** A mint-time change is judged over generations rather than days (the
`#420` error), so the shipped half is a *bet placed*, not a result: the check is the next fire's
`strategy_family` distribution, and a few generations later whether 4h `htf_trend_strength_*`
rows reach `MIN_HOLDOUT_TRADES` at the measured 48%/77%. One risk rides with it and is not
measured — opening the high-volatility bars means trading where `DEFAULT_SLIPPAGE_BPS = 3.0`, a
constant, is most likely optimistic, so the new family's backtest carries a favourable bias of
unknown size. Live is partly covered by `volatility_size_multiplier`; the backtest is not.

### F4. The replay window is our constant, not the venue's limit — measured 2026-08-04

F1 and F3 both end pointing at the same place. F1: *"What reopens it is a mint at 8 conditions
that reaches `MIN_HOLDOUT_TRADES`, which cannot happen at these signal rates without a longer
replay window — so re-measure `market_data.factory_candle_target` first."* F3: settling 4h
`htf_pullback_short` needs four non-overlapping tails that all reach the same floor, and today
only two do. So it was re-measured, against the live venue:

| BTCUSDT | asked | returned | span | oldest bar | fetch |
|---|---|---|---|---|---|
| 4h (today's window) | 3,000 | 3,000 | 500 d | 2025-03-22 | 0.4 s |
| 4h | 12,000 | 12,000 | 2,000 d | 2021-02-11 | 1.6 s |
| 4h | 24,000 | **15,130** | **2,522 d** | 2019-09-08 | 2.0 s |
| 1h (today's window) | 12,000 | 12,000 | 500 d | 2025-03-22 | 1.7 s |
| 1h | 48,000 | 48,000 | 2,000 d | 2021-02-11 | 6.1 s |

**Nothing external is binding.** 4h returns less than asked only at 15,130 bars, which is
BTCUSDT's own listing date — the true floor, and **5.0×** the window the factory replays. 1h
gives at least 4×. `MAX_CANDLES = 60,000` is nowhere near (its own comment already says it is
head-room, not a live limit). The window is set by **`FACTORY_DEPTH_DAYS = 500`**, a constant of
ours, and nothing else.

**What 5× would resolve.** The 0.7-truncation chain F3 uses gives 4h holdout tails of
900 / 630 / 441 / 309 bars today, of which two carry depth; at 15,130 bars the same chain gives
**4,539 / 3,177 / 2,224 / 1,557**, so all four clear `MIN_HOLDOUT_TRADES` comfortably and F3's
open item is decidable rather than merely open. For F1: an 8-condition mint closing 7 in-sample
and 5 holdout trades scales to roughly **35 / 25**, which lands on the floor rather than far
under it — the reopening condition F1 names becomes reachable.

**What it costs, and the cost is the reason not to take it.**
`DERIVATIVE_HISTORY_DAYS = 520` does not follow the window: at 2,000 days the OI and liquidation
series would cover **26%** of the replay, and funding — capped by `FUNDING_MAX_PAGES = 4` at
~1,314 days — about 66%. `factory._oi_feed_reaches` already computes this dynamically
(`replay_days <= DERIVATIVE_HISTORY_DAYS`), so lengthening the window **auto-gates the four
`oi_*` families out of the rotation** rather than mining them over a window that is
three-quarters empty; its docstring records why that matters (every trade lands in the newest
walk-forward slice, `temporal_consistency` is 0 by construction, and the family is retired
FRAGILE for a window that had no data in it). **`funding_fade_*` has no equivalent gate** and
would walk into exactly that failure — one code change an extension requires either way, since
it is a defect independent of the window.

#### F4a. The remedy landed, and 1d has no more room to buy — first measured 2026-08-06

`FACTORY_DEPTH_DAYS` is now **1000** (was 500 when F4 was written) and a floor was added beneath
it, `MIN_FACTORY_BARS = 2_000`, which binds **1d only** — the calendar span alone gave 1d too few
bars for the scorer. So F4's remedy is half-taken, and this is the first look at what it bought.

Measured over the 86 candidates minted on or after 2026-08-05 (the first two generations under
the new window):

| tf | `bars_replayed` med | trades med | `mean_reversion*` trades |
|---|---:|---:|---|
| 1h | 16,800 | 194.5 | 13, 38 |
| 4h | 4,200 | 41.5 | 4, 17 |
| 1d | 1,400 | 36.0 | **0, 0, 2, 3, 4, 5** |

The medians confirm the change is live (`bars_replayed` is 0.7× the target, the in-sample side of
F3's split; 1d's 1,400 is the 2,000-bar floor, not the 1,000-day span). **The median candidate is
now well fed at every tier.**

**What the floor did not fix is the sparse-signal family.** `MIN_HOLDOUT_TRADES` is 25 and the
holdout is the smaller side of the split; a 1d `mean_reversion` candidate closing 0–5 trades
in-sample is an order of magnitude short and cannot become judgeable at all. Every one of the six
minted since the change is in that state. Note what this is *not*: the same family closes 13 and
38 trades at 1h, so it is not a broken family — it is a family whose signal rate needs bars the
top of the ladder does not have.

**And 1d cannot buy them.** `MIN_FACTORY_BARS`'s own comment records why the floor is 2,000 and
not higher: the shortest history among the routed USD-M perpetuals is ~2.1k daily bars
(SOLUSDT), so a higher floor would collect short and score a window it never received. F4's
lever — a longer window — is therefore **already exhausted at 1d**, while it still has 4–5× of
room at 4h and 1h. Any fix for sparse families at 1d has to come from somewhere other than depth.

**Read this as one measurement, not a finding.** Six 1d candidates and two 4h ones is a thin
basis, and it is one family class. What would make it solid: the same table after a full rotation
(~10 days), and the same cut for the other low-signal families rather than `mean_reversion` alone.
Recorded now because the numbers cost a session to gather and the next reader would otherwise
re-derive them from the same two generations.

*(Recorded after a wrong turn worth naming: this started as "drop `mean_reversion` from 1d",
which the cross-timeframe control killed — the family is thin at 1h and 4h in the same
proportion, so excluding it at 1d would have hidden a ladder-wide property behind a
one-tier rule. The control was the whole of the work.)*

**There is no such trade, and the paragraph above nearly bought one.** Both numbers that appear
to force it are **ours, not the vendors'**. Measured 2026-08-04 against the live feeds:

| series | configured | vendor actually serves |
|---|---|---|
| OI daily (Coinalyze) | `DERIVATIVE_HISTORY_DAYS = 520` d | **2,200 d**, back to 2020-07-26 — returns exactly what is asked |
| funding (Binance) | `FUNDING_MAX_PAGES = 4` ≈ 1,314 d | ~333 d per page; 6–7 pages reach 2,000 d |

So a 2,000-day window is coverable on every axis at once — candles to 2,150 (SOL binding), OI to
2,200, funding to whatever `FUNDING_MAX_PAGES` is set to. Lengthening the replay costs the
`oi_*` families **only if `DERIVATIVE_HISTORY_DAYS` is left behind**, and it is a constant in
this repo. The gate in `_oi_feed_reaches` stays exactly as it is — it is the thing that makes
forgetting to raise them *safe* rather than silent.

**Which matters because `oi_*` is where the only confirmations are, and they are not yet
verified.** Everything measured below predates #529 and was taken at the **500-day**
window; see the closing paragraph for why that basis is load-bearing rather than incidental. F2's corrected batch has `oi_squeeze_long` and `oi_unwind_short` CONFIRMED at both
1h and 4h. Walked backwards through the same adjacent, non-overlapping windows F3 uses, at 4h:

| window | `oi_squeeze_long` | `oi_unwind_short` | `oi_unwind_long` (control) |
|---|---|---|---|
| `[2100,3000)` | +0.4240, **4 CONF**, 7/8 judgeable | +0.2565, **3 CONF**, 4/8 | −0.0761, 0 CONF, 6/8 |
| `[1470,2100)` | +0.0946, 0 CONF, 6/8 | +0.1133, 0 CONF, 3/8 | −0.1485, 0 CONF, 6/8 |
| `[1029,1470)` | +0.1024, 0 CONF, 2/8 | +0.2954, 1 CONF, 3/8 | −0.3189, 0 CONF, 5/8 |
| `[720,1029)` | +0.2781, 0 CONF, 3/8 | +0.6172, 1 CONF, 1/8 | −0.1388, 0 CONF, 2/8 |

**The confirmations concentrate in the newest window** — `oi_squeeze_long` confirms 4 of 8 there
and 0 in every older one — which is the shape F3's 1h control showed is not evidence. At 4h the
*sign* still held everywhere, which looked like something. **The 1h leg says it does not:**

| window | `oi_squeeze_long` | `oi_unwind_short` | `oi_unwind_long` (control) |
|---|---|---|---|
| `[8400,12000)` | +0.1829, 3 CONF, 8/8 | **+0.0039**, 0 CONF, 8/8 | −0.3453, 0 CONF, 8/8 |
| `[5880,8400)` | +0.1691, 0 CONF, 8/8 | **−0.0606**, 0 CONF, 5/8 | −0.1836, 0 CONF, 8/8 |
| `[4116,5880)` | **−0.1082**, 0 CONF, 8/8 | +0.1047, 0 CONF, 4/8 | −0.3801, 0 CONF, 8/8 |
| `[2881,4116)` | +0.5983, 4 CONF, 7/8 | +0.4014, 0 CONF, 3/8 | −0.0520, 0 CONF, 7/8 |

`oi_squeeze_long` turns **negative** in one window; `oi_unwind_short` is indistinguishable from
zero in the newest, negative in the second, and confirms in **none** of the four. Only
`oi_unwind_long` behaves consistently, and it is the negative control.

**The cross-timeframe agreement was never two observations.** The 0.7 chain is applied to series
that both span 500 days, so its windows are the *same calendar periods* at both timeframes —
checked, not assumed:

| tail | 4h bars | 1h bars | calendar |
|---|---|---|---|
| deepest | `[720,1029)` | `[2881,4116)` | 2025-07-20 → 2025-09-10 |
| third | `[1029,1470)` | `[4116,5880)` | 2025-09-10 → 2025-11-22 |

The largest positive at **both** timeframes — `oi_squeeze_long` +0.5983 (t = 4.12) at 1h and
+0.2781 at 4h, `oi_unwind_short` +0.4014 and +0.6172 — is one **52-day market period** sampled at
two resolutions. F2's "CONFIRMED at both 1h and 4h" is the same artifact one level up: a 30%
holdout is the last 150 days on either series, so that agreement is one period counted twice.
The unit of independence is the market period, not the trade, and not the timeframe either.

**These confirmations are not the defect `_periods_confirm` was built to catch, which is worth
saying because a reader who knows that gate landed will assume otherwise.** Fresh pooled mints
carry `period_r` and are judged by the period-level interval — verified directly: a CONFIRMED
4h `oi_squeeze_long` draw carries `period_r` with **n = 5**, so it cleared a t at 4 degrees of
freedom rather than a z over 43 trades. They pass that gate. What they do not survive is being
asked the same question about a *different* stretch of time.

**Verdict: `oi_*` is not verified.** Consistent direction at 4h, a sign flip at 1h, every
confirmation single-draw and window-local, and the strongest evidence at both timeframes
traceable to one 52-day window.

**And it says something specific about why, which #529 has since acted on.** The obvious
complaint is depth — the 0.7 chain shrinks geometrically, so on 3,000 bars the last two tails are
441 and 309 bars at 2/8 and 3/8 draws judgeable. That is the lesser problem. `HOLDOUT_PERIODS`
is a **fixed 5** and stays 5 at any window (bounded below by `MIN_HOLDOUT_PERIODS = 4`, the
smallest sample a two-sided t can fail on; bounded above because finer slices buy correlation,
not evidence). So a deeper window does not add periods — **it lengthens them**, and that is
exactly what was wrong:

| window | holdout tail | slice = tail / 5 | vs the ~50-day block scale the period test was built on |
|---|---|---|---|
| 500 d (when everything above was measured) | 150 d | **30 d** | below it — five slices that look independent and are not |
| 1,000 d (#529) | 300 d | **60 d** | above it |

**So every `oi_*` verdict on this page was drawn on slices finer than the correlation scale.**
That is a cleaner statement of the same defect the calendar table shows: the confirmations were
counted as five observations when the market was not offering five. `factory.HOLDOUT_PERIODS`'
own comment reached the conclusion first — *"at today's 500-day window this is not enough periods
to confirm anything… what reopens it is a deeper window, not a smaller number here"* — and #529
then doubled the window with `DERIVATIVE_HISTORY_DAYS` and `FUNDING_MAX_PAGES` moved to match.

**The open item, therefore, is a re-run rather than a new argument.** Every measurement in this
section predates #529 and should be repeated at 1,000 days before `oi_*` is called either way.
The prediction it would test is specific: if the confirmations were slice-correlation artifacts
they should thin out at 60-day slices, and if they are not they should survive with a wider
interval and fewer of them.

**One axis that re-run had is gone: 1h stopped minting on 2026-08-06.** The five `<SYM> 1h`
factory schedules were disabled and the slot re-registered as a mint-anchored null control
(`schedules.jsonl`, per-machine, so there is no repo trace — the same class of change as the
2026-08-04 15m→1d swap). Its own registration states the reason, and it is this section's
conclusion arriving from the other direction: *"the per-candidate holdout cannot resolve the
effect being hunted (needs +0.481R at z=3.53 vs observed p90 +0.209R) while this pooled
instrument has the sample."*

What that costs here is specific and small: **every 1h figure above stays reproducible**, since
they are replay measurements and `templates_for_timeframe("1h")` and `factory_candle_target("1h")`
are untouched — the 1h walk-forward, the calendar-identity table, and the `htf_pullback` control
can all be re-run on demand. What stops is **new 1h candidates**, so the cross-timeframe
comparison this section leans on has one live rung (4h) and one frozen one until the mint resumes.
Read the 1h rows as an archive of 2026-08-05 rather than as a series. And note the null control
measures nothing before ~2026-08-23 (`MIN_POST_MINT_DAYS = 30` against a 2026-07-23 oldest spec),
which its registration flags as accumulation rather than a fault.

Compute is not the obstacle, but **egress may be**: ~7 fetches per context (own + reference + 5
cohort peers) at 2.0 s each is ~14 s per context and ~2.5 minutes added to a daily fire over 10
contexts, and `build_feature_rows` is 6.0 s at 48,000 bars. What the arithmetic misses is the
venue's rate limiter — measuring the depths in this table **hit HTTP 429** (the venue asked for
17 s), and the factory shares an IP with the 15-minute cycle that trades. The live cycle was
unaffected that time (no `MARKET_DATA_DEGRADED` in the 400 ledger rows around it), but a 4×
fetch volume on the same address is the pattern that produced it, so an extension needs explicit
backoff and spacing rather than the same call pattern run four times as hard.

**The cohort's own history bounds the window before the venue does.** `backtest_spec_pooled`
pools by bar INDEX and states its precondition — "bar *i* is the same calendar window on every
leg" — which holds only while every leg is at least as long as the window. Measured per symbol
at 4h: SOLUSDT 12,901 bars (2,150 d, listed 2020-09-14) is the binding leg, then DOGEUSDT 13,296,
BNBUSDT 14,202, XRPUSDT 14,412, ETHUSDT 14,653, BTCUSDT 15,130. **2,150 days is the deepest
window that keeps every leg equal-length**; past it the pooled walk-forward slices start mixing
calendar windows across symbols. A 2,000-day target sits under that with head-room and makes the
whole ladder uniform, since 1d already replays 2,000 days via `MIN_FACTORY_BARS`.

**The real reason this is written down rather than done.** A longer window re-bases every number
in the store: candidates scored on 500 days and candidates scored on 2,522 are not comparable,
and ranking them together is the defect `EDGE_COST_BASIS_UNRECORDED` exists to mark, one axis
over. So an extension needs the same treatment the cost basis got — the window recorded **with**
each candidate's evidence, and `promotable_backlog` refusing to compare across bases — or it
silently invalidates 1,595 rows. That is a larger change than the constant it turns on.

**What an extension is, then, in full.** Not one constant — four, plus the basis machinery:
`FACTORY_DEPTH_DAYS` 500 → 2,000; `DERIVATIVE_HISTORY_DAYS` 520 → 2,000 (or the `oi_*` families
leave the rotation, which is now the expensive outcome rather than the bookkeeping one);
`FUNDING_MAX_PAGES` 4 → 7; a `funding_fade_*` gate mirroring `_oi_feed_reaches`, which is owed
regardless; backoff and spacing on the factory's fetches; and the replay window recorded with
each candidate's evidence so `promotable_backlog` refuses to rank across bases. The ceiling is
2,150 days (SOLUSDT's listing), so 2,000 leaves head-room on every axis measured here.

**Status 2026-08-06 — most of that list is done, and the remaining half should wait.**

| item | state |
|---|---|
| `FUNDING_MAX_PAGES` 4 → 7 | **done** (8) |
| `funding_fade_*` gate | **done** — the families and the timing comment are in `factory.py` |
| replay window on evidence | **done** — `backtest_evidence.bars_replayed`, read by `pool.evidence_depth_rank` |
| `FACTORY_DEPTH_DAYS` 500 → 2,000 | **half** (1,000) |
| `DERIVATIVE_HISTORY_DAYS` 520 → 2,000 | **half** (1,020) |

**The doubling landed 2026-08-05 and covers 5% of the store.** Measured over all 1,680
candidates carrying a readable window: **1,477 at 500 days, 86 at 1,000, 117 at 2,000**. By mint
date, 1h and 4h moved to 1,000 on 08-05; 08-04 was a mixed day mid-deploy.

**1d has been at 2,000 all along, and not through this constant.** `MIN_FACTORY_BARS = 2000`
floors it at 2,000 bars, which at a daily bar is 2,000 days — above the calendar target. So the
remaining half of this item would move **1h and 4h only**, and the 2,000-day figure it is aiming
at already exists on one rung as a side effect of a different constant.

**Doubling again now would be the `#420` error this file names three times.** The 1,000-day
window is two days old, 88% of the store still carries 500, and no lineage has completed a
rotation at the new depth. The condition to proceed is a full rotation at 1,000 with the 4h trade
counts read after it — not a calendar date.

**One trap, recorded because this measurement fell into it first.** `bars_replayed` is the
**70% training slice**, not the window: `HOLDOUT_FRACTION = 0.30` is withheld before scoring. A
4h row showing 4,200 bars is a 1,000-day window (700 train + 300 holdout), not a 700-day one.
Read as the window it understates every depth by 30%, which briefly turned a correctly-landed
change into a phantom defect ("4h is replaying half its target") on the first pass here.

### F5. The clamp collected the draws it was meant to bound — fixed 2026-08-05, owed a generation

`mutate_params` drew `base ± (hi − lo) × 0.35` and **clamped**, so any centre nearer a bound than
its own span sent every overshoot to exactly that bound: one value, one rule hash, a parameter
that had stopped varying rather than shifted. Measured across the library, **16 (parameter,
space, base) combinations do it from their own template base, covering 114 of 170 template
parameter slots** — `target_atr` 18.8%, `flow_ma_min` / `rel_min` 18.3%, `xs_dispersion_min`
14.3%, `htf_sep_min` 11.9%, `oi_change_min` 9.2%, `flow_z_min` 8.0%, `stop_atr` 5.4%, nine more
at 2.4%.

**The half that matters is the half nobody measured.** `generate_batch` centres half of every
batch on `elite_base_params`, so a pinned row becomes a centre ON the bound and re-pins half its
own children. Of the 48 real elite centres this store supplies for the trend space, **7 (14.6%)
sit exactly on `target_atr`'s 1.6 floor**, two re-pin >50%, sixteen more 20–50%, mean 15.0%.

That is what makes the previously recorded fix wrong rather than merely partial. `_EXIT_BASE`'s
note proposed raising the target base; measured from both centres it fixes the template half
(14.5% → 0.0%), leaves the elite half at 15.0%, and moves the median drawn target 3.12 → 3.86 —
half the effect, paid for by re-aiming the trend geometry toward the band F1's own table calls
its worst. `_fold_into_bounds` reflects instead: **both halves to 0.0%**, median target
3.12 → 3.03 and 3.22 → 3.25, median R:R 2.14 → 2.08 and 2.13 → 2.18 — inside the 0.05R nothing
in this store resolves.

**What was owed.** A mint-time change is judged over generations, not days (the `#420` error), and
this one was placed against a store whose 1,507 non-fade rows carried the old draw. The check was
the next fires' parameter distribution: the share of minted rows sitting exactly on a bound should
fall from ~12% toward zero. Nothing about edge was claimed or expected — 0 of 1,140 candidates
confirm out of sample and a random entry loses 0.13R here. What it buys is that the search covers
the space it is given.

#### Paid off — measured 2026-08-08, ~25 generations after the deploy

Every stored row's exit triple checked against its own family's space (the fold shipped
2026-08-05 and the containers restarted onto it at 15:54 UTC):

| generation block | rows | on a bound | share |
|---|---|---|---|
| GEN 600–779 | 923 | 136 | **14.7%** |
| GEN ≥ 780 | 199 | 6 | **3.0%** |

**And the residual six are all explained, none of them a pin.** Four are seeded rows from
GEN-781/782/783 — generations that fired *before* the containers restarted. One (GEN-804) is
`max_holding_bars` landing on 12, an integer taking a value it may legitimately take rather than a
pile-up; the test added with the fold allows integer parameters an 0.08 share for exactly this.
One (GEN-793) is a **crossover**, and that one is the clamp doing its documented job.

**From GEN-784 onward, zero seeded rows sit on a continuous bound** — 0 of ~190.

**The fusion path was checked and is NOT the same defect, which is worth stating because it
looks like it.** `_fused_exit_param` still clamps where `mutate_params` now folds — but a fused
value is the MIDPOINT of two parents, and a midpoint of two in-space parents is in-space by
convexity (the note on `test_fusion_cannot_carry_a_child_outside_the_space_it_mints_from` says so
directly). The clamp therefore fires only when a parent was minted under an OLDER space, which is
the GEN-793 row, and pinning a stale parent to the nearest legal value is the right operation
there — folding it would place it at an arbitrary interior point instead. **What is stale is the
docstring**, which still says *"Same clamp and same constants as `mutate_params`, so a fused
parameter and a mutated one can never land in different places"*; that stopped being true when the
fold landed, and the sentence is corrected in the same change as this note.

### F6. The regime label folds volatility over trend, and one live family still pays for it — audited 2026-08-05

F3 found the fold in `htf_trend_*` and #497 replaced that pair. **The same audit was never run
over the rest of the library.** It has been now. `classify_market_regime` tests volatility
before it tests trend, so every label it emits carries two variables and one hard-coded
threshold (`ADX_TREND_THRESHOLD = 20.0`, inside the label, where no search can reach it):

```
RANGE == (0.20 < atr_percentile < 0.80) AND adx < 20      # not "no trend"
```

Four template pairs gate on a categorical today. Three are already resolved:

| family | gate | verdict |
|---|---|---|
| `htf_pullback_long/short` | `htf_market_regime == TREND_UP/DOWN` | open under F3 — measured, and F3's own follow-up says the rule is not the defect |
| `oi_squeeze_long/short` | `market_regime != TREND_UP/DOWN` | **already fixed by this precedent** — `== "RANGE"` fired the full condition on 0.43% of ETHUSDT 1h bars against 4.88% for `!=`; the builder records it |
| `session_trend_long/short` | `session == <label>` | not a fold. Three named buckets with no order, deliberately categorical; `CATEGORICAL_FEATURES["session"]` argues it |
| `mean_reversion` / `_short` | `market_regime == "RANGE"` | **the one still paying** |

**And it is judgeability that it costs, not edge.** Measured on the store (latest-wins,
1,620 rows), against the fade families that share `_FADE_EXIT_PARAMS` and carry no regime gate
(`funding_fade_*`, `premium_fade_*`, `taker_absorption_*`, `oi_unwind_*`) as the control:

| timeframe | `mean_reversion_*` median closed | ungated fade median closed |
|---|---|---|
| 4h | **3.5** (n=20) | 32.0 (n=35) |
| 1d | **2.5** (n=2) | 24.5 (n=4) |
| 1h | 13.0 (n=18) | 78.0 (n=37) |
| 15m | 68.0 (n=20) | 301.0 (n=18) |

`MIN_HOLDOUT_TRADES` is 25. **At 4h and 1d this family cannot reach a verdict at all**, which
is F1's mechanism in a seeded template — the same finding F3 reported for `htf_pullback_*`, from
a different cause. RANGE is 4.3% of the control's traded bars; `HIGH_VOLATILITY`, which the
label excludes, is **37.9%**.

**The two excluded bands do not measure alike, and neither result is strong.** Paired within
(family, timeframe) on the control, R/trade:

- RANGE − `HIGH_VOLATILITY` = **−0.0085R**, 5 of 11 cells — a coin flip under the 0.05R floor
  nothing in this store resolves. So the band carrying 37.9% of the sample costs nothing to admit.
- RANGE − `LOW_VOLATILITY` = **+0.6454R**, 4 of 5 cells — large, but on 53 trades in total, and
  `LOW_VOLATILITY` is the worst band in the table (−0.4018R/trade). Excluding it is defensible.

**Limits, stated because they bound the proposal.** These are IN-SAMPLE per-regime breakdowns
(`backtest_evidence.regime_breakdown`), and the control's entries are z-score and flow rules
rather than `mean_reversion`'s RSI — so "how a fade performs in HIGH_VOLATILITY" is measured on
different entries than the one that would move.

**The proposal that was tested — and rejected, see below.** Replace the label with the trend
test it folds, at
**identical `free_parameters`** — `count_free_parameters` charges one literal either way
(verified: `literals = sum(1 for c in conditions if c.value is not None)`), and both `adx` and
`atr_percentile` are already in `NUMERIC_FEATURES`:

```python
# mean_reversion_long, today          # proposed
{"feature": "rsi", "<=": p},          {"feature": "rsi", "<=": p},
{"market_regime": "== RANGE"},        {"feature": "adx", "<=": p["adx_max"]},
```

This gives the search the 20.0 the label hard-codes and drops the volatility fold. On the
`volatility_squeeze_*` / #497 precedent it would REPLACE rather than add, since a family is
+1 hypothesis charged to `selection_adjusted_z` for every other family in the store.

#### The replay was run 2026-08-05, and it says no

Method as #497's: 12 paired draws × long/short × both arms at 4h and 1h, `backtest_spec_pooled`
over the 5-symbol cohort, `rsi` and the exit triple each drawn from their own rng keyed by draw
index so the second entry condition is the only difference between arms. 100 venue reads at
`ARCHIVE_REQUEST_INTERVAL_SECONDS` pacing, 2m55s wall, no `TOOL_RATE_LIMITED`. Read-only, in a
one-off container with every `MVP_LIVE_*` blanked; nothing appended to any store.

| | A `== RANGE` judgeable | B `adx <= p` judgeable | A HO exp | B HO exp |
|---|---|---|---|---|
| 4h long | **1/12** | **7/12** | +0.1605 *(n=1)* | −0.0295 |
| 4h short | **1/12** | **8/12** | −0.0521 *(n=1)* | +0.1913 |
| 1h long | 12/12 | 9/12 | −0.0826 | **−0.2112** |
| 1h short | 12/12 | 10/12 | +0.0784 | +0.0745 |

**The judgeability claim is confirmed and the change still fails its own bar.** At 4h the current
rule reaches `MIN_HOLDOUT_TRADES` in 1 draw of 12 — the store-based estimate (median 3.5 closed
trades) was right, and the family genuinely cannot earn a verdict there. B reaches it in 7 and 8.
The 4h expectancy columns are **not a comparison**: A's are single observations, which is the
defect rather than a result.

The pre-registered rule was *ship only if 4h judgeability rises AND 1h holdout expectancy does
not fall by more than 0.05R*. **1h long falls 0.1286R** — above the floor this store resolves,
so the rule stops it. 1h judgeability also drops (12/12 → 9/12 and 10/12): `adx <= p` draws
below 20 are stricter than the label's own `adx < 20`, so B is not uniformly the wider gate.

Nothing clears anything: best `t max` is +1.62 (4h short, arm B) against a selection-adjusted
bar near 2.87 at this attempt count. No edge was expected and none appeared.

**So `mean_reversion_*` keeps its gate, and this is the same answer F3 reached for the pullback
half** — helps at 4h, hurts at 1h, do not ship. What the item becomes is the one thing both
measurements agree on: the family is unjudgeable at 4h and 1d for a reason that is about SAMPLE,
which makes it evidence for the pooled-mint experiment F2 defers rather than for another template
edit. Re-open this only against a rule that does not cost the 1h leg, or once minting is pooled.

### F7. The hold is drawn in bars and the retiming only swaps the label — measured 2026-08-06, 86 rows

F1 ends on the count of rows a fire produces that can be judged at all, and that count fell as
the rotation moved: `MIN_HOLDOUT_TRADES` was cleared by 84 of a fire's rows on 07-31 and by 5 of
20 on 08-06. The cause is not the tier and not the entry rules. **`templates_for_timeframe`
returns `replace(t, timeframe=timeframe)`** — it swaps the label and leaves the generation space
alone — so `_EXIT_PARAMS`' `max_holding_bars` of 12–48 draws 12–48 **hours** at 1h and 12–48
**days** at 1d. Measured over the 86 rows minted on the current window, the holds that produced:

| timeframe | hold, bars (min/med/max) | the same hold in days |
|---|---|---|
| 1h | 9 / 24 / 34 | 0.4 / **1.0** / 1.4 |
| 4h | 6 / 16 / 36 | 1.0 / **2.8** / 6.0 |
| 1d | 6 / 26 / 37 | 6.0 / **26.0** / 37.0 |

**A hold occupies its bars, so the exit geometry sets a ceiling the entry rule cannot lift** —
`holdout_bars / max_holding_bars` is the most trades a tail can close:

| tf | holdout bars | median hold | **ceiling** | actual | ceiling used | **ceiling < floor** |
|---|---|---|---|---|---|---|
| 1d | 600 | 26 | **23** | 16 | 77% | **55%** |
| 4h | 1,800 | 16 | 109 | 17 | 22% | 0% |
| 1h | 7,200 | 24 | 307 | 91.5 | 39% | 0% |

**The two slow tiers fail for opposite reasons and only one of them is this.** At 1d the median
ceiling sits *below* `MIN_HOLDOUT_TRADES` itself, 55% of rows cannot reach the floor whatever
they signal, and the 77% utilisation says the entry rule is already firing near that ceiling —
nothing about entry conditions moves it. At 4h the ceiling is 109 against a floor of 25 and only
22% of it is used: that is a **signal-rate** problem, it is not addressed here, and the lever
already measured against it is F2's `backtest_spec_pooled` (4h holdout tail 32 → 169, judgeable
9/12 → 12/12), still unused by the factory.

**Deepening the window is not available at 1d.** `MIN_FACTORY_BARS` already floors it at 2,000
bars and that constant's own note records why it cannot rise — the shortest routed history is
~2.1k daily bars (SOLUSDT), so a higher floor would score a window the venue never served. F1's
*"re-measure `factory_candle_target` first"* is spent at this tier.

**What landed — the narrow version** (`judgeable_holding_bars`, `_judgeable_hold_space`). The
hold space is capped at `holdout_bars // MIN_HOLDOUT_TRADES`, which binds **1d only** on today's
ladder (600 // 25 = 24 against a space topping at 48; 4h yields 72 and 1h 288, both above their
spaces). It removes the band whose ceiling is below the floor and nothing else: the claim is
*"this spec's own geometry makes its holdout unjudgeable"*, which is `_fuse_batch`'s "scored
candidate that can never trade" one notch weaker, and the same shape as
`MAX_FUSION_ENTRY_CONDITIONS` — a second bound beside the validator's `MAX_HOLDING_BARS_RANGE`,
which answers whether the hold is *legal* rather than whether the result can be *judged*.
It bounds the DRAW rather than the centre, so a pre-bound 1d elite at 37 bars is folded back
inside rather than escaping through `elite_base_params`. Fusion needs no matching change: a
child's hold is its parents' midpoint, and a parent long enough to breach the cap cannot parent
at all — `holdout_permits_parenting` wants the judgeable holdout its own geometry denies it.

**The wide version is deliberately not taken.** Re-expressing the hold as a calendar span — the
`factory_candle_target` precedent, and the real fix for the *cause* — would move 4h and 1h too,
on a design intent nothing in this repo ever recorded. Which timeframe the 12–48 was chosen
against is not written down anywhere, and guessing it changes every tier.

**What this does not claim.** The bound removes rows that *cannot* reach the floor; it does not
make rows reach it. Whether a 1d spec closes 25 trades still depends on its signal rate, and at
the cap the ceiling is exactly 25 — a spec would have to trade back-to-back to hit it. Expect
the 1d judgeable share to rise off 22% and not to reach 1h's 83%. **Judged over generations, not
days** (the `#420` error; this is a mint-time change).

**One number to record so nobody chases it here.** The entry cost door refuses **26.2%** of 1h
triggers and **0%** at 4h and 1d — real, and not part of this.

**Read this beside #566, which landed the same day and moves what it applies to.** That record
froze the **1h** tier (5 `crypto_factory` schedules disabled, 15 → 10), on a null control showing
its entry contributes nothing — real −0.1059R against random −0.1093R over 135 contexts. So the
factory now mines **4h and 1d only**: the two tiers this section measures as broken, and the one
tier whose judgeable share was healthy (83%) is no longer minted. That raises the value of the
bound above and it does not change the finding.

It also bounds what the bound is worth. #566 measures the promotion gate as a tool that can only
confirm effects of **+0.48R or larger** after the selection correction, against a real target of
~+0.05R and a holdout median of 34 trades where confirming +0.05R would need ~9,208. Judgeability
is a precondition for learning anything, not a route to a verdict: of the 463 rows judgeable
today, **462 are CONTRADICTED and 0 CONFIRMED**. Nothing here should be read as expecting that to
move.

### F8. The whole store's edge lives inside 1.3 bps of an unmeasured constant — measured 2026-08-06

F3 ships a family that trades high-volatility bars and records the risk it could not size: *"the
new family's backtest carries a favourable bias of unknown size"*, because `DEFAULT_SLIPPAGE_BPS
= 3.0` is assumed. §G1 then indexed that constant as **INHERITED** — carried from the source
system, never measured here — with *"enough live fills to measure realized slippage"* as what
would reopen it. **The first live fills arrived the same day**, and the bias has a size now.

**The arithmetic is exact, not a model.** Slippage cost in R is `bps / risk_bps`, linear in the
rate, and `cost_summary.total_slippage_cost_r` is recorded per candidate — so re-pricing a
candidate at rate *r* is `net − slippage_per_trade × (r/3 − 1)`. No re-scoring, no re-replay.

**The median candidate at the current cost basis stops paying at 4.3 bps.** The model charges
3.0. Over the 973 current-basis candidates:

| | median net @3.0 | @10 bps | @23.5 bps | breakeven |
|---|---|---|---|---|
| current cost basis | **+0.0164** | −0.0692 | −0.2172 | **4.3 bps** |
| `oi_squeeze` | +0.3966 | +0.2008 | −0.1252 | 17.7 |
| `volatility_expansion` | +0.1380 | +0.0633 | −0.0923 | 14.6 |
| `trend_pullback` | +0.0584 | −0.1617 | −0.3104 | 10.0 |
| `breakout` | +0.0886 | −0.0869 | −0.2660 | 9.1 |
| `htf_trend_strength` (F3's) | −0.1116 | −0.1753 | −0.2982 | already ≤ 0 |

By timeframe the exposure runs the way the denominator does — 15m swings **−0.6575R** between
3.0 and 23.5, 1d only **−0.0500R** — because 1R is `stop_atr × ATR` and the fast end divides a
fixed bps by the smallest risk unit.

**And the exposure is largest exactly where the only confirmations came from.** F2 records
`oi_*` as the two families that confirm out of sample; they are also the ones carrying the widest
margin *and* the biggest absolute swing (−0.4830R at 23.5). A result that survives its holdout
and dies on a slippage re-price is not a result.

**23.5 bps is one observation, and it is the worst leg.** ETHUSDT's first live stop rested at
1900.5 and filled at 1904.96 (§C); DOGEUSDT's filled exactly at its stop. A stop-market on a fast
move is the most adverse fill this runtime places, and the model charges slippage on **both**
legs, so applying 23.5 to both is the pessimistic end. **The measured thing here is the
sensitivity, not the rate** — the column to read is that the store's median edge sits 1.3 bps
above its own assumption, whatever the true rate turns out to be.

**What this changes.** F3's unsized risk is sized: at any realized rate above ~4.3 bps the
store's median candidate is not profitable, and the families that confirm forward are the ones
with the most to lose. It does not say the rate is 23.5 — it says the assumption is load-bearing
and two fills is the entire evidence behind it. **What would settle it** is entry-leg slippage
measured against intended price over enough live fills to be a distribution, which is the same
thing `DEFAULT_SLIPPAGE_BPS`'s own entry in `crypto/tunables.py` already names as what reopens
it.

#### The instrument for that exists as of 2026-08-06 — and it is still empty

`scripts/measure_live_slippage.py` (#576), read-only, reports realized against modelled on every
leg where both prices exist. Building it found that **only one of the three legs had them**:

| leg | intended price | state |
|---|---|---|
| protective stop | the trigger the runtime chose (`bracket[].stop_price`) | measurable since the first close — **23.47 bps** on ETHUSDT, 0.00 on DOGEUSDT |
| strategy entry | **not recorded anywhere durable** | fixed by #585 |
| canary | **not recorded anywhere durable** | fixed by #589 |

**The entry leg was the gap that mattered**, because it is the one the cost model charges on
*every* trade. A MARKET entry recorded only `fill.avg_price` and the venue answers
`price: "0.00"` for a market order, so half of what the model charges had nothing to check it
against. The value existed one layer up all along —
`build_live_order_intent` carries `entry_price` from the plan, the same number
`paper.settle_trade_plan` settles at — and simply never reached anything durable. #585 lands it
on `submit_and_reconcile`, the one function holding both the intent and the fill.

**The canary is the instrument that works while live entries are held.** It is an entry-only
MARKET order placed to validate the path, so it is the only entry this runtime can make **without
routing a strategy signal from a pool where 0 of 1,140 candidates confirm out of sample**. #589
records the `reference_price` `place_canary_order` already read to check its declared notional —
used and discarded until now — beside the fill, plus the `side` without which the figure has a
magnitude and no direction.

**All three are recording-only.** No order changes shape, no gate reads the new fields, and a
test pins that the canary's `clean` — which gates autonomous live entry — is unmoved.

**It reads empty, and that is the honest state:**

```
stop fills: n=2  median 11.73 bps  worst 23.47  against 3.0 modelled (7.8x)
entries with no recorded intent: 2   canaries with none: 4   (both predate `intended_price`)
```

The six existing fills predate the fields and are **counted rather than assumed to have filled at
their intent**. Nothing accumulates on its own: with live entries held there are no new entries,
and there are no open positions left to close. **The only path that fills this is canaries placed
deliberately as measurement**, which is an operator action — real orders, Thomas's to place. Until
then §F8's sensitivity stands on one stop fill, and the constant it re-prices stays INHERITED.

### F9. Symbol pooling is built, unused, and the data it needs is already being bought — audited 2026-08-06, **decided and shipped 2026-08-09**

> **Status 2026-08-10 — the decision this section asks for was taken. Everything below is now
> the working, not an open ask.** Shape B shipped on 2026-08-09: **#638** let a factory schedule
> name a cohort (a comma is the whole opt-in) and **#651** gave the pooled-mint check its own
> pre-registration. `run_factory` has a pooled caller, so the premise line below — *"has no
> caller but the single-symbol path"* — is history. It is corrected here rather than deleted,
> because the reasoning under it is still the authority for **why** the shape is what it is.
>
> **What is open now is the first fire and its reading, not the decision.** The reading is
> pre-registered, which is the whole point of #651 — read it with the script rather than by
> eye, and do not re-derive the bar afterwards:
>
> ```
> docker exec thomas-scheduler python -m scripts.pooled_mint_check
> ```
>
> Run 2026-08-10T02:55Z: `store rows: 1961   pooled rows: 0` — *"the cohort schedules have not
> fired, or were reverted."* Both readings are live at that moment; the script cannot tell them
> apart and does not pretend to.
>
> **Which schedules are enabled is a per-machine fact this file cannot hold**, the same rule the
> deployed image gets at the top: `schedules.jsonl` is gitignored, so there is no repo trace and
> no amount of reading this file answers it. On the Docker host at that same timestamp the five
> single-symbol schedules were `enabled=False` and two cohort schedules (4h and 1d) were
> `enabled=True`. Reverting is those `enable` calls, not a code change.

#### The ask, in one screen — everything below this subsection is the working

`backtest_spec_pooled` is finished, carries eight tests, and **has no caller but the
single-symbol path**. Whether the rotation moves onto it is the decision. It is not a code
question — the effort is five items, all small (*What wiring it takes*, below) — it is a
**portfolio-shape** question, and no further measurement narrows it.

**What is settled, so it need not be re-litigated.** At 4h a single-symbol row closes a median
**9** trades against a floor of 25, so those families are unjudgeable *permanently* — rows do not
pool across generations and waiting adds nothing. Pooled, the same family was judged in an
afternoon. **And the judgement was negative**: the one result that ever cleared a
selection-adjusted bar (`oi_unwind_short`, t up to 5.16) decays to **−0.23…−0.31R** in earlier
adjacent windows, in **16 of 16 draws** across two seed namespaces. So the honest expected value
is *"finds out faster"*, never *"earns more"* — against a store whose holdout gross edge is
~0.012R and whose random-entry control loses 0.13R.

**Judgement 1 — what shape.** The live pool routes **5 strategies** (94 entries; the rest are in
non-occupying statuses): 1 at 1h, 4 at 4h. A pooled spec occupies every symbol context of its
timeframe in **one direction**, and `min(contexts, 2·min(long,short) + 4)` then decides the book:

| | contexts | long / short | fillable |
|---|---|---|---|
| today | 5 | 1 / 4 | **5** (100%) |
| 4h pooled **short** | 6 | 0 / 6 | **4** (67%) |
| 4h pooled **long** | 6 | 5 / 1 | **6** (100%) |

Direction is fixed at promotion and the pooled spec *is* the tier, so this cannot be tuned
afterwards; directional control drops from one lever per context to one per timeframe.

- **A — all timeframes pooled.** Reaches the lifecycle window ~5× sooner (the direct cause of
  today's 89 inert entries), and pays the table above plus one demotion emptying a whole tier.
- **B — 4h and 1d only.** Treats where the defect is; 1h already runs 12/12 judgeable
  single-symbol. Half the directional exposure, and 1h keeps buying slots back.
- **C — pooled EVIDENCE, single-symbol routing.** Portfolio shape unchanged entirely; pays in
  bookkeeping instead (a row carrying pooled evidence under a single-symbol scope). **Not in the
  original wiring list** — it is the option that separates judging from routing.

**Judgement 2 — what the door does with a window-test sign flip.** Wiring the window test is
*not* optional: without it the row described above is promotable on its face, and `period_r` does
not substitute for it (it partitions the tail; the reversal is 2,000+ bars earlier, and all ten
slices read positive). What is open is the response — **refuse**, **rank below a stable row**, or
**record and surface**. The last is the cheapest honest start and matches
`assert_promotable_evidence_depth`, which refuses only the unreadable case and ranks the rest.

**Doing nothing is also a choice**, and its cost is the status quo: 4h and 1d families stay
unjudgeable and the promotion door keeps yielding zero — F1's mechanism, unaddressed.

*A reading, marked as one rather than derived: **B is the smaller default and C the real
alternative**; A pays the whole directional lever for most of what B buys.*

---

F7 closes 1d's structural half and says outright that it does not touch 4h's, where the ceiling
is 109 against a floor of 25 and only 22% of it is used. F2 already measured the lever that moves
both: `backtest_spec_pooled` replays one spec across several symbols' frames and pools the tail,
the cost legs and the outcomes. This section is the audit of **why it is not wired**, because the
reasons a reader would assume turn out not to be the reasons.

**It is finished, not a sketch.** The function handles the cost-model precondition (a frame built
under a different `CostModel` is refused rather than used), takes the weakest funding source
across its legs rather than the best, and carries **eight** tests of its own — depth per symbol
rather than summed, the shallowest leg winning, the cost-model refusal, the single-frame identity
with `backtest_spec`. Its only caller is
`backtest_spec` — the single-symbol form, which delegates with a one-element list. Its own
docstring records the deferral: *"this does not decide what the factory mints — `run_factory` is
untouched, and moving the rotation onto pooled specs is a separate decision that wants
generations of evidence."*

**What F2 measured, restated with the part that constrains it:**

| | 4h single → pooled | 1h single → pooled |
|---|---|---|
| median holdout trades | 32 → **169** | 49.5 → **268.5** |
| holdout tail ≥ `MIN_HOLDOUT_TRADES` | 9/12 → **12/12** | 12/12 → 12/12 |
| CONFIRMED | 0 → **0** | 0 → **0** |
| CONTRADICTED | 9 → **12** | 12 → 12 |

It also lifts F7's ceiling directly, since the ceiling is per frame: 5 symbols take 1d's 23 to
~115 without touching the hold at all.

**The fetch objection is backwards — the data is already bought and thrown away.**
`cycle.attach_cross_section` reads every `CROSS_SECTION_UNIVERSE` peer at
`factory_candle_target(timeframe)` depth so the `xs_*` families can be ranked, and the factory
branch of `scheduler.py` calls it **without a `PeerCandleCache`** — the only one ever constructed
is at `cycle.py:1135`, in the trading fan-out. So a factory fire pages **5 peers × replay depth ×
5 contexts = 25 peer reads**, computes ranks from them, and discards the series. Pooling needs
**5 reads for the whole fire**. The leg's own docstring already names this: *"without one this
leg would be the largest source of redundant vendor reads in the runtime."* F4's HTTP 429 is a
real constraint and it does not apply here; if anything, pooling reduces the fire's fetch.

**The live end already supports it, and that is where the actual cost is — larger than it first
looks.** `routable_context_map` and `routable_directional_capacity` both iterate
`spec.symbol_scope` and both document it — *"a multi-symbol strategy occupies each of its
symbols"*. No code change is owed there. But `MAX_ROUTABLE_PER_CONTEXT` is 1 and the cohort is
the whole mined symbol set, so **a pooled spec occupies every context of its timeframe**: one
pooled 4h strategy *is* the 4h tier, where five single-symbol strategies fit today. Fully pooled,
the pool goes from up to five strategies per timeframe to **one**, each carrying five times the
evidence and one direction for the whole tier — which `routable_directional_capacity` then reads
as a maximally skewed book.

That also makes it a migration rather than a switch: the pool holds **5 occupied contexts right
now** (BTCUSDT 1h, and BTCUSDT/ETHUSDT/SOLUSDT/DOGEUSDT at 4h), so a pooled 4h spec needs four of
them vacated before it can route at all. None of this is a measurement — it is a portfolio-shape
decision, and it is the thing to decide rather than to derive.

#### The decision in numbers — read off the live pool 2026-08-08

Stated so the choice is made against arithmetic rather than against an impression. Nothing below
changes what the code does; it is what the code already does, applied to the pool as it stands.

**The live pool is five strategies, not ninety-four.** `OCCUPYING_STATUSES` is `PAPER_ACTIVE` /
`PROBATION` / `WARNING`; 89 of the 94 entries are in none of them and occupy nothing.

| id | tf | direction | symbol | family |
|---|---|---|---|---|
| S008 | 1h | short | BTCUSDT | `bollinger_breakdown_short+breakdown_short` |
| S005-GEN-700 | 4h | short | ETHUSDT | `breakdown_short` |
| S004-GEN-706 | 4h | short | SOLUSDT | `session_trend_short` |
| S005-GEN-697 | 4h | **long** | BTCUSDT | `breakout+macd_momentum` |
| S002-GEN-709 | 4h | short | DOGEUSDT | `xs_momentum_short` |

BNBUSDT 4h is unoccupied, so a pooled 4h spec picks up a context nothing fills today.

**The arithmetic that decides it** is `routable_directional_capacity`'s own:
`reachable = min(contexts, 2·min(long, short) + MAX_DIRECTIONAL_SKEW)`, with the cap at 4.

| configuration | contexts | long / short | reachable | utilisation |
|---|---|---|---|---|
| today | 5 | 1 / 4 | **5** | 100% |
| pooled 4h **short**, 1h unchanged | 6 | 0 / 6 | **4** | **67%** |
| pooled 4h **long**, 1h unchanged | 6 | 5 / 1 | **6** | 100% |

**So the tier's direction becomes a portfolio-level choice worth a third of the book, and it
cannot be adjusted afterwards** — `direction` is fixed at promotion and the pooled spec *is* the
tier. The general form is the part that outlives these five rows: directional control drops from
one lever per context (up to 15) to one per timeframe (3).

**Three shapes, not two.**

- **A — fully pooled per timeframe.** Buys judgeability: a pooled spec accrues trades ~5× faster
  and reaches `lifecycle.DEFAULT_WINDOWS[0]` (20 trades) ~5× sooner, which is the direct cause of
  today's 89 inert entries. Costs the directional arithmetic above, and one auto-demotion empties
  a whole tier.
- **B — pooled at 4h and 1d only, 1h left single-symbol.** Treats where the defect is: F2 measured
  1h single-symbol at 12/12 judgeable already, and it is 4h and 1d whose tails are too thin. Halves
  the directional exposure and keeps an opposite-direction 1h leg buying slots back.
- **C — pooled EVIDENCE, single-symbol routing.** Mint pooled to get a judgeable holdout, then
  promote the winning parameter set as one spec per symbol. **The portfolio shape does not change
  at all** — same contexts, same directional arithmetic, same demotion granularity. The cost is
  bookkeeping rather than shape: a row would carry pooled evidence under a single-symbol scope,
  and `evidence_depth_of` / `symbols_replayed` have to say so, because the unit the door judges
  and the unit it routes stop being the same. **This option is not in the four wiring items
  above**, and it is the one that separates the two things pooling has been treated as one thing.

**B is the smaller default and C is the real alternative; A pays the whole directional lever for
what B buys most of.** That reading is a judgement, not a measurement, and it is recorded as one.

**One statistical consequence that will read as a discount and is not.** `search_context_key` is
`(symbol_scope tuple, timeframe)`, so a pooled spec lands in a context no single-symbol spec
shares. `attempts_by_context` starts near zero there and `selection_adjusted_z` therefore starts
near **1.96** rather than the 3.4–3.9 the mined contexts now carry. This is correct — the pooled
hypothesis space genuinely is separate, and minting 120 pooled specs raises that context's bar by
exactly the same `sqrt(2 ln N)` — but a reader meeting a pooled row beside a single-symbol row
will see two different thresholds and the difference has to be written where they meet it, not
only here.

**What it does not buy — and this paragraph cited the wrong experiment until 2026-08-07.** The
table above is F2's **re-score**: existing single-symbol specs re-scoped to five symbols and
replayed. Its CONFIRMED 0 → 0 is a fact about specs fitted on one symbol and then asked to
transfer. F2 ran a second experiment for exactly this question — a pooled **mint** batch — and it
did not read 0. Quoting only the re-score left a reader of this section concluding that pooled
minting has never confirmed anything, which the record two subsections up contradicts.

#566 still sharpens what a confirmation is worth: after the selection correction the promotion
gate can only confirm effects of **+0.48R or larger** against a real target near +0.05R, and of
the 463 rows judgeable today **462 are CONTRADICTED**.

#### The pooled mint distribution, which F2 never published — re-run 2026-08-07

F2 reported 11 CONFIRMED of 272 at 4h and 4 of 272 at 1h and said they "concentrate in the `oi_*`
families" without giving the per-family split. That split is the whole question: 9 confirmations
sprinkled over 34 families is noise, and 5 in one family is not. Re-run at 8 draws per family,
pooled over the 5-symbol cohort, **all five legs attached** and post-`_fold_into_bounds` draws:

| status over 272 pooled specs | 4h | 1h |
|---|---|---|
| CONTRADICTED | 222 | 260 |
| INSUFFICIENT | 41 | 11 |
| **CONFIRMED** | **9** | **1** |

| family | 4h | 1h |
|---|---|---|
| `oi_unwind_short` | **5/8** | 1/8 |
| `htf_pullback_short` | 3/8 | 0/8 |
| `premium_fade_short` | 1/8 | 0/8 |

**It concentrates, and that is not a chance pattern.** Under a null of 9 confirmations spread
uniformly over 34 families, one family taking ≥5 has probability **8.5 × 10⁻⁵** — about 1 in
11,700. The earlier reading of F2's summary (that ~1 family confirming at both timeframes is what
chance predicts) was the right calculation on the wrong input; with the split in hand the
concentration is real.

**And four draws clear the selection-adjusted bar, which F2 said nothing did.** At 272 attempts
the bar is z = 3.740. Four `oi_unwind_short` draws are above it — t = 5.16, 4.50, 4.28, 4.18 at
holdout expectancies +0.44 to +0.70R over 33–141 trades. F2's best was 3.34 against the same bar.
This is the first time anything in this record has cleared a corrected bar.

**Three reasons to hold that at arm's length, in order of how much they could cost.**

1. **The open-interest column is a DAILY series held constant across the day — measured
   2026-08-08, and it is the one that matters.** The window was never the problem: coverage is
   100% over all 6,000 4h rows and all 24,000 1h rows, on every cohort symbol, train and holdout
   alike. What the coverage check found instead is the *resolution*. The feed returns **1,020
   records stamped at midnight** (`2023-10-23T00:00:00Z`, `2023-10-24T00:00:00Z`, …), and
   `build_feature_rows` carries each across the bars of its day:

   | | values | distinct | run length |
   |---|---|---|---|
   | 4h `open_interest_zscore` | 6,000 | **1,000** (16.7%) | **6** (998 of 1,000 runs) |
   | 1h `open_interest_zscore` | 24,000 | **1,000** (4.2%) | **24** (998 of 1,000 runs) |

   The run length is exactly bars-per-day at each timeframe. **Three consequences, and they
   compound:**

   - The `oi_*` families mine a threshold on **~1,000 independent observations**, not 6,000 or
     24,000. The other families in the same batch mine per-bar columns.
   - The entry is a **STATE, not an event** — once the daily value crosses, the condition is true
     for all six (or twenty-four) bars of that day. That is precisely the defect `macd_momentum` /
     `_short` were RETIRED for on 2026-08-04: *"a state family re-fires on every bar of a move
     rather than on the bar the relationship changed"*.
   - The third consequence is the one everybody will reach for — that the trades inside a day are
     near-duplicates, so `expectancy_t` divides by an inflated `sqrt(n)` and the clearance is an
     artifact. **Measured 2026-08-08: it is false.** Recorded because it is the natural inference
     and it does not survive contact.

   **The trades are not clustered inside days.** Distinct `(symbol, UTC day)` pairs behind each
   confirming draw, from trade stamps kept out of the same `_replay` the holdout uses and
   cross-checked against `holdout.closed_count`:

   | draw | n | symbol-days | market days | t | t·√(sym-days/n) | t·√(days/n) |
   |---|---|---|---|---|---|---|
   | 0 | 61 | 60 | 45 | +5.16 | **5.12** | **4.43** |
   | 1 | 49 | 47 | 35 | +3.70 | 3.62 | 3.13 |
   | 3 | 56 | 55 | 38 | +4.50 | **4.46** | 3.71 |
   | 4 | 33 | 33 | 26 | +4.28 | **4.28** | **3.80** |
   | 6 | 142 | 140 | 85 | +4.15 | **4.12** | 3.21 |

   n ≈ symbol-days almost exactly — 61 trades on 60 symbol-days, 33 on 33. **The mechanism the
   inference missed is that a position occupies the day it opens.** A fade holds 4–16 bars, so a
   state condition that stays true cannot re-fire inside the day it is already trading in. The
   `macd_momentum` analogy does not transfer, and the daily column does not inflate `n`.

   **So that correction leaves the clearance standing, and the stricter one splits it.** Against
   z = 3.740, 4 of 5 confirmed draws clear on the symbol-day unit. On the **market-day** unit —
   cross-symbol trades on one day share a market period, which is the unit this record's own
   effective-sample argument uses — only **2 of 5** clear (4.43 and 3.80), one is marginal at
   3.71, and two fall to 3.21 and 3.13.

   Where it stands: **not an artifact of the denominator, and not a clean clearance either.**
   `htf_pullback_short` is unaffected on any unit — it clusters more (117 trades on 107
   symbol-days, 65 market days) and its best t was 2.93, never near the bar.
2. **1h barely agrees.** 1/8 at t = 2.44, below the bar. The cross-timeframe replication F2
   leaned on is one draw here, not a second result.
3. **+0.44 to +0.70R per trade is enormous** against a store whose holdout gross edge is ~0.012R
   and whose random-entry control loses 0.13R. An effect that large is more often an instrument
   than an edge — and item 1 names the instrument.

#### It does not reproduce — measured 2026-08-08, and this closes the question

**The rotation cannot answer this and never will, which is worth stating before the measurement
that can.** "Wait for generations and see if `oi_unwind_short` holds" is the obvious plan and it
is void: `run_factory` mints single-symbol, so no store generation is ever the pooled hypothesis,
and at 4h this family's stored rows close a **median 9** trades against a floor of 25. Nine of
nine are unjudgeable. Rows do not pool across generations — each is its own hypothesis with its
own tail — so accumulating more never makes one judgeable. Waiting buys nothing here.

**What the store CAN judge says no.** Across the 18 stored `oi_unwind_short` rows:

| timeframe | rows | judgeable | median judgeable HO exp | positive |
|---|---|---|---|---|
| 15m | 3 | 2 | −0.2832 | **0/2** |
| 1h | 6 | 2 | −0.2184 | **0/2** |
| 4h | 9 | **0** | — | — |

Everywhere it can be judged it is judgeably negative, and that **agrees with the pooled 1h
result** (1/8, t = 2.44, below the bar). The two methods only diverge at 4h, where one has
evidence and the other structurally cannot.

**So the question goes to F3's instrument**, which is the one available: truncate the series by
0.7 each step so window *k+1*'s tail ends exactly where window *k*'s begins. Same five confirming
draws, same cohort, adjacent and non-overlapping:

| holdout window (4h) | draw 0 | draw 1 | draw 3 | draw 4 | draw 6 |
|---|---|---|---|---|---|
| `[4200,6000)` — the batch's own | **+0.62** (t 5.16) | +0.49 (3.70) | **+0.54** (4.50) | **+0.70** (4.28) | **+0.43** (4.15) |
| `[2940,4200)` | **+0.53** (4.51) | +0.20 (1.49) | +0.42 (3.48) | +0.62 (3.55) | +0.31 (3.04) |
| `[2058,2940)` | −0.03 | −0.06 | −0.06 | +0.00 | −0.02 |
| `[1440,2058)` | −0.31 | −0.25 | −0.23 | −0.25 | −0.25 |

**All five draws decay monotonically and cross zero in the same window.** The effect lives in the
newest ~3,000 bars (~500 days) and reverses before them. That is not one strong window — it is
two — but it is a *time gradient*, which is the same finding in a worse form: a rule whose sign
depends on when you look is not a rule the promotion door should take. OI coverage is not the
explanation; the feed starts 2023-10-23 and spans every window here.

**Those five were a SELECTED sample, and re-seeding removes the caveat rather than the finding —
measured 2026-08-08.** They were the draws that confirmed on window 0, which is the very window
the gradient starts from, so the pattern could have belonged to them rather than to the family.
Two arms settle it: the same eight original seeds (including the three that did *not* confirm),
and eight freshly seeded draws from the same space.

| arm | CONFIRMED on w0 | w0−w3 slope | oldest window |
|---|---|---|---|
| original 8 | 5/8 | median **+0.854**, positive **8/8** | median −0.247, negative 7/8 |
| fresh 8 | **7/8** | median **+0.869**, positive **8/8** | median −0.320, negative **8/8** |

**16 of 16 draws slope the same way and 15 of 16 end negative**, and the fresh arm confirms at a
*higher* rate than the batch did — so 5/8 was not a lucky draw either. The gradient is a property
of the family over this cohort and period, not of the rows selected for confirming. What it is
still not is evidence about any other cohort or period: both arms draw the same family from the
same space, which is what this question needed and is all it answers.

**So F9's conclusion stands, and now on evidence rather than on a citation.** Pooling *finds out
faster* — it turned a family the rotation could never judge at 4h into one judged in an afternoon,
and the judgement is that its confirmation is a property of the recent period. What is **not**
established, and what nothing here supports, is that pooled minting earns more. The remaining
honest use for the `oi_unwind_short` result is as a worked example of why the pooled door needs
the window test wired beside it, not as a candidate.

**One method correction worth more than the numbers.** `attach_feeds` reads OPEN INTEREST off the
`liquidation_feed` argument (`cycle.py`: `snapshot["open_interest"] =
liquidation_feed.open_interest_history(...)`), so passing `None` — which the name invites —
silently blanks all four `oi_*` families. The first run of this batch did exactly that and would
have reported "0 confirmations at 1h, 4 at 4h, none in `oi_*`". `unsuppliable_features`, called
per spec, named the four families instead of letting them fall into INSUFFICIENT. **That is the
same failure F2's first pass published as a finding**, caught this time only because the guard was
called; `backtest_spec_pooled` on its own walks around it.

**And F2's closing cost is already spent at the timeframes that matter.** F2 warns that
lengthening the replay window removes the `oi_*` families, citing `DERIVATIVE_HISTORY_DAYS = 520`.
It is **1020** now, against `FACTORY_DEPTH_DAYS` 1000 — the two moved together on 2026-08-04 — so
the gate binds at 1d only. `_oi_feed_reaches`'s own docstring still says 520 and is stale.

**What wiring it takes** — recorded so the decision is about the portfolio shape rather than about
unknown effort:

1. `run_factory` takes several snapshots instead of one; the `frames=` path into
   `backtest_spec_pooled` already exists and `run_factory` already builds one frame per fire.
2. The factory schedules move from one per `(symbol, timeframe)` to one per `timeframe` —
   `.runtime_governance_state/schedules.jsonl`, per-machine state, not code.
3. `build_spec_dict` puts the mined cohort in `symbol_scope` instead of `[symbol]`.
4. All five frames must carry one `CostModel`. `backtest_spec_pooled` already fails closed on a
   mismatch, and #556 made the cost model venue-aware — so that refusal is the **first** thing to
   exercise, not an afterthought.
5. **A window test wired beside the door**, and this one is not optional — see below.

#### Why item 5 exists, and why `period_r` is not it

The pooled door's first real output was five `oi_unwind_short` draws clearing a selection-adjusted
bar at 4h, and the window test above showed all five reversing sign two windows back. **A pooled
door without that test promotes exactly that row.** The value of pooling is that it makes rows
judgeable fast; wiring it without the instrument that judges *stability* turns a find-out-faster
lever into a promote-recent-regimes-faster one.

**The obvious reuse fails, measured 2026-08-08.** `period_r` / `period_trades` already split the
holdout into `HOLDOUT_PERIODS` slices, which reads like the same instrument. It is not: it
partitions the **tail**, and the reversal is 2,000+ bars before the tail begins. On the five
confirming draws every slice is positive —

| draw | per-trade R across the 10 holdout slices (oldest → newest) |
|---|---|
| 0 | +1.29 −0.05 +0.18 +1.05 +0.85 +0.52 +0.16 +0.94 +0.70 +1.33 |
| 6 | +0.80 +0.34 +0.01 +1.35 +0.87 +0.29 +0.03 +1.04 +0.07 +0.75 |

— so `period_r` hands a clean bill of health to a spec whose sign flips outside its window. The
two answer different questions: *"was the tail uniform"* and *"does the tail's answer hold before
it"*.

**What the test is**, concretely, since the shape is already proven: truncate the series by
`1 - HOLDOUT_FRACTION` each step so window *k+1*'s tail ends exactly where window *k*'s begins
(F3's construction), and score the same spec on three earlier windows. Reach is ~4× the tail.

**Where it belongs.** Computed at mint beside the holdout and stored on the candidate's evidence,
the way `period_r` is — never inside `backtest_spec_pooled`, whose docstring is explicit that it
decides nothing. The promotion door then reads a recorded fact instead of re-deriving one, the
same separation `holdout` already has.

**What it costs, and it is smaller than it looks.** The truncated frames are spec-INDEPENDENT, so
a batch pays three extra `build_replay_frame` calls in total, not three per spec — the property
that made the 272-spec batch affordable. Replay is ~1.5× the single-window cost (0.7 + 0.49 + 0.34
of the series). **No extra venue reads at all**: every window is a prefix of candles already
fetched.

**What to do with the answer is a decision, not a derivation.** A sign flip across adjacent
windows could refuse at the door, rank below a stable row, or only be recorded and surfaced.
Recorded-and-surfaced is the cheapest honest start and matches how a shallow window is already
handled — `pool.assert_promotable_evidence_depth` refuses only the unreadable case and ranks the
rest.

#### F9c. Pooling turned the crossover path off, and nothing recorded that as a decision — measured 2026-08-15

**The state.** Since the first pooled fire (2026-08-10T08:12:38Z) **every** factory fire has been
pooled, and `run_factory` skips fusion on a pooled fire (`if fusion_pairs > 0 and not pooled`).
So the crossover path has produced **zero children for six days, from zero attempted pairs** —
not a thin yield, an off switch. Before it, fusion was minting ~0.2 children per fire (~3.9 before
#523/#525 tightened it) and crossover rows were **26% of the eligible parent pool** while being
26% of all rows, at a 15.5% parent-eligibility rate against seeded's 9.0% and a 9.0% ROBUST rate
against seeded's 2.8%.

**The skip itself is right and is not the item.** #633's comment states it: `_fuse_batch`
re-scores each parent on this window through the one-frame `backtest_spec`, so a pooled child
would be scored on one leg while claiming five — and `fuse_specs` would pair pooled parents
happily, since their scopes match and `symbol_scope_mismatch` never fires. That is the wrong-number
shape the rest of the file exists to prevent. **What is missing is that the consequence was never
priced.** The skip arrived inside a PR about holdout windows; no line anywhere says "the crossover
path is now off", and F9's own decision screen does not count it among what pooling costs.

**Why it went unnoticed for six days, which is the transferable part.** `fused_count: 0` with an
empty `fusion_rejected` is exactly what a **dry parent pool** produces, so the record could not
distinguish "fusion ran and the store had no pair" from "fusion never ran". A weekly watch built
to catch a dry pool read straight past it — and its three triggers were all green at the time
(pool 205 distinct hashes and growing ~7/day, crossover share 26%, seeded pass rate 12–62%).
`fusion_skipped` (added alongside this entry) now names the reason, so the two are separable.

**The decision, which is Thomas's.** Either pooled fusion is a wanted increment or the crossover
path is retired in favour of pooled seeding — and if it is retired, #523's child bar and #525's
parent filter are governing a path that no longer runs and should be reconsidered on those terms.

**What the increment costs if it is wanted.** One thing: `_fuse_batch` has to score children and
re-score parents through `backtest_spec_pooled` over the fire's legs rather than `backtest_spec`
over one frame. The pieces already exist — `backtest_spec_pooled` is the scorer, and the parent
replays are already memoised per rule hash, so the cohort cost is paid once per parent per fire,
not once per pair. `fuse_specs` needs no change: pooled parents share a scope, so the union is
already well-defined. The materials are there too — pooled candidates are accumulating at 8/fire,
and two pooled parents in one bucket is all a pair needs.

**What NOT to do.** Do not re-enable fusion on pooled fires without moving the scorer first. The
guard is load-bearing exactly as written; removing it mints children whose evidence claims a
cohort it never replayed.

### F10. 4h does not signal rarely — five families do, and the rotation funds them equally — measured 2026-08-06

F7 closes 1d's structural half and hands 4h over as *"a signal-rate problem"* on a utilisation of
22%. **That reading was measured on the wrong population and the direction of the finding is the
other way round.** Normalised by each row's own `holdout.bars`, over 1,187 rows carrying a
readable tail, the entry rate **rises** with the timeframe:

| tf | n | entry rate p25 / median / p75 | median hold | share of bars in a position |
|---|---|---|---|---|
| 15m | 304 | 0.10% / **0.85%** / 2.85% | 24 | 20% |
| 1h | 374 | 0.50% / **1.25%** / 3.22% | 23 | 27% |
| 4h | 412 | 1.00% / **2.11%** / 3.67% | 23 | 50% |
| 1d | 97 | 2.17% / **3.00%** / 4.00% | 24 | 72% |

4h fires more often *per bar* than 1h or 15m. It closes few trades because 1,800 holdout bars is
few bars, which is F9's argument and not a property of the entry rules.

**What the 22% actually measured is the family mix of two rotation slices.** The 4h rows minted
since 2026-08-05 run at **0.94%** against the 4h store's **2.67%**, and the spread the rotation is
sampling from is enormous — at 4h, entry rate by family spans **0.00% to 8.44%**, about 100×:

| high | | low | |
|---|---|---|---|
| `macd_momentum` | 8.44% | `oi_unwind_short` | 0.50% |
| `breakout` | 5.00% | `mean_reversion_short` | 0.22% |
| `trend_pullback` | 4.11% | `mean_reversion` | 0.11% |
| `breakdown_short` | 4.00% | `taker_flow_long` | **0.00%** |

Those recent slices drew almost entirely from the low block — `taker_flow_long`/`_short` 5 rows
each, `oi_unwind_short` 5, `htf_pullback_*` 7 — while `trend_pullback` (4.11%) got **2**. So which
fires produce judgeable rows is decided by where the rotation cursor lands, and today's 08:09 fire
reading 48% judgeable against the 08-05 cohort's 27% is the same mechanism, not a trend.

**It is not the condition count and it is not a missing feed.** Within 4h seeded rows the count
does not order the rate (2 conditions 1.00%, 3 conditions 3.78%, 4 conditions 0.86%), while the
family spread survives *inside* each count — at 2 conditions 0.00% (`taker_flow_long`) to 5.44%
(`xs_momentum_long`), at 3 conditions 1.22% to 8.44%. And the quiet families' columns exist:
`taker_flow_ma`, `open_interest_change_pct` and `rsi` are all supplied, so this is a rare signal
rather than the `unsuppliable_features` defect wearing its clothes.

#### The confound this section owed, and it resolves three ways

A family's measured rate is taken over candidates whose thresholds were themselves mined, and half
of every batch centres on a prior elite (`elite_base_params`) — so a low median could be the
family's premise or a ratcheted threshold. The store separates them without a replay: **at 4h the
floor needs 25/1800 = 1.39%, so ask whether ANY draw a family has ever produced reached it.**

| verdict | families | what it means |
|---|---|---|
| **premise** | 5 | best draw ever produced still under 1.39% |
| **threshold** | 9 | some draws clear it, most do not |
| ok | 14 | majority clear it |

The premise five, with their best stored draw: `taker_flow_long` 0.78%, `taker_flow_short` 0.89%,
`mean_reversion` 0.56%, `mean_reversion_short` 0.33%, `htf_pullback_long` 1.33%. F6 reached this
for `mean_reversion` alone (*"at 4h and 1d this family cannot reach a verdict at all"*) and F3 for
the htf pair; the measurement says it is a library-wide property that nothing counts.

**So the obvious action is the wrong one for two thirds of the population.** A family × timeframe
mint gate — the analogue of `_judgeable_hold_space` — is justified for the five whose search space
contains nothing judgeable at 4h. Applied to the nine threshold-bound families it would delete a
premise that *is* reachable, which is the failure mode F7 named when it refused the calendar-span
rewrite: acting on the cause you assumed rather than the one you separated.

**Limits, because they bound this.** Five to thirteen rows per family. "Best draw ever" over n
draws underestimates a family's reachable maximum, so a *premise* verdict says only that the
search has spent those draws and produced nothing near the floor — not that nothing exists.
`htf_pullback_long` reads premise while `htf_pullback_short` reads threshold, which is either a
real directional asymmetry or small-n noise and this cannot tell them apart. And `mint_params` is
present on only **79 of 259** 4h seeded rows, so the direct elite-half/base-half split is not yet
measurable — it becomes so as rows minted under `mint_params` accumulate, and it is the sharper
version of this test.

**What pooling does and does not reach** (F9): multiplying the tail by the cohort lifts the nine
threshold-bound families over the floor at 4h and does **nothing** for a family at 0.00%.

## G. Codebase review backlog — measured 2026-08-02; **exhausted 2026-08-09**, G5 profiled

> G1 sliced twice, G2 removed, G3 indexed and twice corrected, G4a/b/c done. What is left is the
> three rejections and a three-site residue, both stated exactly at the end of this section, plus
> G5 (the scheduler profile). Read
> "The refactoring backlog is exhausted" before starting anything here.

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

### G1. Crypto tunables have no owner — ~~602 constants across 42 modules~~ ⚠️ highest value

> **The struck-through count was this item's own headline and it was wrong by about four**, which
> the second slice found and which is worth more than the number that replaced it. The grep below
> counts every upper-case module-level assignment; **most of them are strings** — reason codes,
> status labels, provenance markers, the package's vocabulary — not numbers that decide anything.
>
> No corrected figure is pinned here on purpose. The population moves (§G1 already records it
> growing by 54 in four days), so a precise count in a header is a claim that rots, which is
> exactly how the original got here. "Second slice done 2026-08-08" carries the measurement with
> the date it was taken, and the coverage test is what stays true between measurements.

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

**First slice done 2026-08-06 — `crypto/tunables.py`, 55 constants, and no value moved.**
The count was **656** by then, not 602: the population grew by 54 in four days, which is the
argument for the test rather than the index.

The index **imports** each constant from its owner instead of copying it, so it cannot be wrong
about a number, and carries the one thing the comment beside it does not say — **provenance**:
where the value came from and what would reopen it. Values stay put because §G1 says a 602-value
sweep is unreviewable, and for a second reason: in this package the argument lives in a dense
comment beside the constant, and moving the number would separate every value from its own
reasoning.

**What reading it the first time found.** Provenance splits **26 `INHERITED`** / 9 `DERIVED` /
9 `OPERATOR` / 6 `MEASURED` / 5 `VENUE` — and **the four numbers that stop the money are all
INHERITED**. `DAILY_MAX_LOSS_R`, `WEEKLY_MAX_LOSS_R`, `MAX_CONSECUTIVE_LOSSES` and
`MAX_DRAWDOWN_PCT` are the predecessor system's `config/settings.py` values, carried across and
never examined against this runtime's own record — while the cost model, the ladder and the
promotion door have each been re-measured more than once. That is not a bug and no number is
obviously wrong; it is that the halt thresholds are the least-examined values in the package,
and nothing said so before. A test pins the finding so it cannot stop being true silently.

**Second slice done 2026-08-08 — the sweep is the whole package now, and the headline was wrong.**

The first slice's boundary was twelve modules, named in the test so the rest was a decision rather
than an oversight. Closing it turned out to be small: across the other **31** modules there were
**29** decision-shaped constants with no owner, and **21 of those modules had none at all**. Twelve
are decisions and are indexed with provenance (index: 55 → **67**); seventeen are estimator warm-up
floors, collection budgets, display thresholds and output caps, and are named in `MECHANICS` with a
reason apiece.

`SWEPT_MODULES` is now **derived from the package** rather than hand-listed, with an `UNSWEPT` dict
where an exclusion would have to be argued in writing. A hand-maintained list was the right shape
while the answer was "some of them"; once the answer is "all of them" it is only a way to forget a
module, and a new module now arrives swept.

**And the item's own headline overstates it by a factor of four.** §G1's grep
(`^[A-Z_]{4,} *[:=]`) counts every upper-case module-level assignment; it reads **681** today. By
AST, **387 of those are strings** — reason codes, status labels, provenance markers, the package's
vocabulary — and only **155 are numeric**. So "602 constants across 42 modules … the numbers that
decide money" was mostly not numbers. Of the 155, the index now owns 67 and `MECHANICS` names the
rest of the decision-shaped ones. This is the same correction §G3 made to its own count, from the
same cause: a grep over upper-case names measures naming convention, not the thing being counted.

```
grep -rhcE '^[A-Z_]{4,} *[:=]' runtime/mvp_runtime/crypto/*.py | paste -sd+ | bc   # 681, of which
                                                                                  # 155 are numeric
```

**What stays true.** No value moved, again. The four INHERITED breakers below are still the
least-examined numbers in the package, and the second slice adds two more of the same shape to the
list of things nobody here has re-decided: `ADX_TREND_THRESHOLD` (the cutoff every regime label
turns on, carried from the source's `entry_policy`) and the digest's `±0.1R` trend band.

**The teeth are a coverage test, not the index.** A decision-shaped constant appearing in a swept
module must be indexed with its provenance or named in `MECHANICS` with a reason — so a new
threshold on the money path cannot be added without recording where the number came from. It
caught four on its first run. Twelve modules are swept and the rest of the package deliberately
is not; the boundary is a list in the test rather than an implication.

#### The four INHERITED breakers, measured 2026-08-06 — **measurement only, no value changed**

The index recorded that nobody here had decided them. This is what this runtime's own record says
about them. **Nothing is proposed and nothing moved**; changing a breaker needs Thomas.

Population: the **90 own closed paper outcomes** (2026-07-24 → 2026-08-05), imported
crypto_AI_System history excluded — `paper.split_by_provenance`. Metered exactly as `guards` does
(`cost.outcome_net_r`, falling back to stored `result_R`), and shown against the **stored** figure
the guard read before the 2026-07-30 cost work for contrast:

| | STORED (what it read then) | NET (what it reads now) |
|---|---|---|
| mean R/trade | **+0.0210** | **−0.5014** |
| cumulative R | +1.89 | −45.13 |
| days at or past `DAILY_MAX_LOSS_R` −2.0 | 2 of 10 | 4 of 10 |
| worst day | −6.20R | −17.21R |
| weeks at or past `WEEKLY_MAX_LOSS_R` −5.0 | **0 of 3** | **2 of 3** |
| worst week | −4.93R | −24.13R |
| losing runs reaching `MAX_CONSECUTIVE_LOSSES` 3 | 10 of 17 | 11 of 15 |
| longest losing run | 10 | 10 |
| max drawdown | −10.31R | −45.39R |
| against the 10R limit `MAX_DRAWDOWN_PCT` maps to | **103%** | **454%** |

**The thresholds were never wrong for the series they were written against — the series moved
underneath them.** On the gross figure the guard metered until 2026-07-30, the weekly breaker
never tripped once and drawdown grazed its limit at 103%. On the net figure it meters today, two
of three weeks blow through weekly and drawdown is **4.5× the limit**. Settlement charging plus
read-time conversion changed what an R means to these breakers by about **0.5R per trade**, and
the four numbers were not revisited. That is §G1's defect exactly: a premise that died somewhere
else.

**The conversion is not in question.** The measured mean of −0.5014R reproduces the −0.506R
`cost.py` recorded independently against 86 of the same rows.

**One threshold binds on both readings and deserves its own look.**
`MAX_CONSECUTIVE_LOSSES = 3` is reached by 10–11 of 15–17 losing runs whichever figure is read,
with a longest run of 10. A breaker that trips on two thirds of all losing streaks is either
doing most of the halting or being routinely overridden; which of those is happening is not
answered here.

**Values derived from this measurement are proposed in
`docs/proposals/RISK_BREAKER_UNIT_RESTATEMENT_V0.1.md` (DRAFT, awaiting Thomas).** It changes no
value. The argument in one line: the ladder's *shape* — two stops in a day, five in a week, ten
before the account is judged — is already principled, and what broke is the *unit*, because a
stopped-out trade costs **1.3576R net** (median of 59) where the design assumed 1.0R. Restated at
that unit the three capital thresholds read −2.72 / −6.79 / −13.58%, each inside its existing
relaxation bound. `MAX_CONSECUTIVE_LOSSES` derives differently — from evidence rather than
equity — and lands on **k = 10**, which is exactly the existing relaxation *ceiling*, so adopting
it leaves nothing for a registered config to relax. That is one of the four decisions the
proposal names rather than settles.

**And the first live fills touch a fifth INHERITED constant.** `DEFAULT_SLIPPAGE_BPS = 3.0` is
indexed as *"carried from the source system unmeasured"* with *"enough live fills to measure
realized slippage"* as what reopens it. The first two live stops (2026-08-06, §C) are the start of
that sample: ETHUSDT's stop rested at 1900.5 and filled at **1904.96** — 4.46 adverse, **23.5 bps**,
0.108R on a 41.36 risk unit — while DOGEUSDT's filled exactly at its stop for a clean −1.00R.
**n = 2, one of them at ~8× the modelled rate.** A stop-market on a fast move is the leg most
prone to slippage, so this is the worst case rather than an average, and two fills are not a
distribution — but the direction is the unsafe one and the constant said this is what would
reopen it.

**What is still open here:** the unswept modules (`features.py` alone holds 37 numeric
constants, mostly indicator windows), and the `INHERITED` breakers themselves — indexing them
records that nobody has decided them, which is not the same as deciding them.

### G2. The dead capability lane — **removed 2026-08-06**, and its premise was better than it read

`runtime/read_only_entry/` (1,877 LOC) + `runtime/protected_governance_state/` (1,434), the four
`scripts/validate_i0_5_2/3/4/5*` that imported them, and the entries naming them in
`deferred/DEFERRED_ARCHITECTURE.yaml` and `scripts/gate_matrix.py`. **The deferred design stays**
— its contracts, schemas, registries and examples are untouched, and the `family_constraints`
that record every capability as `false` are still there. What went is an *implementation* sitting
under a deferral, which is the anomaly: a deferred design is supposed to be a design.

**The "zero importers" grep was scoped to the live runtime and understated the surface**, which
is worth recording because it nearly stopped this. Four validators imported both packages, and
the deferral manifest lists them under `detailed_validators` — so the first read was "the gates
genuinely read this", which is the exact wording §G uses below to park the sibling item.

**Checked one step further, nothing executes them.** The release gate names all four in a
**comment block**; the deferred CI scope runs `scripts/validate_deferred_architecture.py`, which
runs `tests.test_deferred_architecture`, which does not touch the lane; pytest never imports it.
The `RELEASE_GATE_EVIDENCE.yaml` rows showing them run carry a `C:\Users\thomas\...` path — a
hand-run from another machine, not a gate. So the record said validated and nothing validated.

**The lockstep list was right about the mechanism and wrong about one item.**
`scripts/build_i0_5_2/3/5*` do not exist — only `build_i0_5_1_*`, which stays, as does
`validate_i0_5_1_runtime_promotion_readiness.py`; neither imports the lane. And the reason the
manifest had to move in the same commit is concrete: `lib/deferred_validation._all_references`
yields `implementation_candidates` and `detailed_validators` and `_path_exists` checks each, so
deleting the code alone would have failed the deferred gate on the very PR that touched it.

Verified after: deferred architecture gate **PASS**, `pytest` 4,349 passed / 210 skipped, release
gate `--full --check-only` **PASS**, and no manifest reference points at a path that no longer
exists.

**What this does not settle** is the sibling item in the not-recommended list below (36 of 75
schemas and 44 of 94 contracts describing disabled capability). That one rests on "the gates
genuinely read it" — which was true there and, as measured here, was **not** true of this lane.
The distinction to carry: a gate reading a *record about* code is not a gate reading the code.

Was, before this: safe, mechanical, and **not urgent** — the material is governed and indexed, not loose. Do it when
something else already requires a full-matrix run.

### G3. Diagnostics have outgrown their index — **indexed 2026-08-06**

`docs/DIAGNOSTIC_CODE_INDEX.md`, generated by `scripts/build_diagnostic_code_index.py`, kept true
by `tests/test_diagnostic_code_index.py`. Exactly what this item asked for — *"a generated index
(code → module → the condition that raises it)… the thing missing, not fewer codes."*

**The count was the first thing the index corrected.** This item's own
`grep -rhoE '"[A-Z][A-Z0-9_]{6,}"'` counts every upper-case string literal — record types, status
values, provenance labels — and reported 1,188. Walking the AST for calls to classes ending
`Error`/`Blocked`/`Refused` and taking the literal first argument or `reason_code=` gives the
codes that are actually *raised*: **384 distinct codes across 638 sites, 25 exception classes**
(34 before §G2 removed nine with the deferred lane). A further **192 sites build their code at
runtime** and are counted rather than guessed at — an index that invented a code would be worse
than one that admits a gap.

**The `condition` column is the "why", and it cannot rot.** It is the guarding `if`, unparsed
from the source, so it says what the code is actually behind rather than what someone wrote about
it once. That is the whole reason the index is generated rather than authored.

**The duplicate check this item asked for exists and has teeth.** **59 codes are raised from more
than one module.** That is not automatically a defect — `APPROVAL_EXPIRED` means one thing in all
seven modules that raise it — but the opposite case is indistinguishable from the outside: one
code, two meanings, and an operator reading it back reaches the wrong module while both raises
are individually correct. The 59 are declared as a **snapshot, explicitly not an audit**: nobody
has checked all of them, and the list exists so the *sixtieth* is a decision. A separate test
drops entries that stop being shared, so the declaration cannot outlive its codes.

Not placed in `generated/`: that tree is governed by `GENERATED_ARTIFACT_INDEX.yaml` and
registering there is a governance surface a reading aid does not need.

#### The index was counting 27 English sentences as reason codes — fixed 2026-08-09

Found while acting on §G4's note about `SpecParseError` and `FusionRefused`. That note said neither
appeared here; both did, and how they appeared was the defect. Those classes take a **message** as
their first positional argument, so the walk filed the message in the code column:

```
| `created_by must be a non-empty string` | `SpecParseError` | crypto/strategy.py | 373 | … |
```

**An entry whose key nobody can look up is worse than a missing one.** §G3 exists to answer "a code
came out of the runtime — where is it raised", and no operator greps an English sentence. Each also
counted as a distinct code, so the vocabulary was overstated by 27.

**The rule is that a code has no spaces**, and it splits the surface with nothing left over —
measured across every literal the walk finds: **751 `SCREAMING_CASE`, 9 `lower_snake`, 27
sentences**, and no fourth shape. The nine lowercase ones stay: `too_many_conditions`,
`holdout_unjudgeable` and seven `*_mismatch` names are `FusionRefused`'s mint-refusal vocabulary,
which §F already tabulates by name. A rule keyed on `SCREAMING_CASE` would have dropped all nine.

| | before | after |
|---|---:|---:|
| distinct codes | 456 | **429** |
| indexed sites | 787 | 760 |
| built at runtime, not indexable | 113 | 113 |
| **carry a message where a code goes** | counted as codes | **27, counted apart** |

The 27 are **counted, not dropped silently** — and counted *separately* from the 113, because they
are different gaps: a site that builds its code can be given one, while a site that raises with a
message has no code to find. Collapsing them would claim the index is missing 140 codes when 27 of
those paths do not have a code at all. Six of the 27 are in `read_only_kernel/`, so the never-modify
rule puts them permanently in that column.

**Not fixed here: giving `SpecParseError` a code vocabulary.** Its 34 raise sites are spec-parse
validation whose audience is the factory, not an operator diagnosing production — the same shape as
the tunables index's `MECHANICS`, where something looking like a decision is not one. Deciding that
is a separate item from making the index stop mislabelling it.

**What is not done:** near-duplicate detection (`ARCHIVE_NOT_ENABLED` against a future
`ARCHIVE_NOT_ENABLED_YET`) needs a similarity rule and a judgement about what counts as too
close, which is a different item from the exact-collision check landed here.

### G4. Which live code hand-rolls a helper it could import — measured 2026-08-08, `main` = `8e0dcb6`

*Reuse first — one concept = one authority = one source of truth* is enforced hard for contracts,
schemas and registries, and **had never been measured for the package's own helpers.** This is that
measurement: an AST walk over all 230 production modules (`runtime/` + `scripts/`, tests excluded)
counting sites that do locally what an existing shared module already owns, reported beside how many
files import that module — so a zero-adoption authority is visible rather than inferred.

| authority | bypass sites | crypto | runtime core | scripts | kernel | importers |
|---|---:|---:|---:|---:|---:|---:|
| `paths.repo_root` | 59 | 0 | **0** | 59 | 0 | 44 |
| `jsonl.*` | 44 | **35** | 4 | 5 | 0 | 9 |
| `integrity.sha256_*` | 35 | 4 | 1 | 30 | 0 | 64 |
| `timeutil.*` | 23 | 1 | **0** | 20 | 2 | 57 |
| `cli_common.EXIT_*` | 20 | 0 | **0** | 20 | 0 | 23 |
| `errors.MvpRuntimeError` | 18 | 2 | 1 | 11 | 4 | 110 |
| `schema_cache` | 16 | 0 | 0 | 16 | 0 | 15 |
| `filelock.locked` | **0** | 0 | 0 | 0 | 0 | 24 |

**The result is where the zeros are.** `runtime/mvp_runtime/*.py` — the core — kept the bargain each
authority's docstring records ("this lived twice; it lives here now"): zero repo-root hand-rolls,
zero timestamp hand-rolls, zero lock hand-rolls, four JSONL sites. **The two that did not are
`crypto/` and `scripts/`, and they want opposite fixes**, which is the reason this section splits
them rather than filing one "reduce duplication" item.

#### G4a. `crypto/` never adopted `jsonl` — 35 sites, 15 modules, **0 importers** — **reads done 2026-08-08**

> **Closed for the read half.** All five readers fold, one PR each in the order this section set:
> `counterfactual` (#623), `pool` (#626), then `paper` / `live_pnl` / `live_promotion` together
> once the decisions below were settled and the remaining three were the identical transformation.
> `grep -rn 'for i, line in enumerate(lines):' runtime/mvp_runtime/crypto/` now returns **0**.
> The write half stays hand-rolled on purpose — see the fsync paragraph below; a test in
> `test_mvp_runtime_crypto_paper.py` now fails if any of the three loses its `os.fsync`, so the
> "tidy-up" this section warns about cannot land quietly.
>
> **That grep was too narrow, and two readers hid behind it — corrected 2026-08-09.** It matches
> only the `enumerate` form. `oi_store.py` and `positioning_store.py` iterate `for line in lines:`
> over the same `read_text().splitlines()`, so they read as "0 hand-rolled readers" while being
> exactly the shape this item is about. **A claim checked with one spelling of a pattern is a claim
> about the spelling** — the reliable check is
> `grep -rn 'read_text(encoding="utf-8").splitlines()' runtime/mvp_runtime/crypto/`, which names
> the defect rather than one way of writing the loop after it.
>
> **They are fixed, and they are still not `jsonl` adoptions.** Both must **degrade** — the
> docstring says the reader answers with less rather than refusing, and `except ValueError:
> continue` is how — while `jsonl.iter_objects` fails closed on a bad line, correctly, for the
> outcome stores the breaker reads. So the fix is the streaming half alone, with the per-line
> tolerance untouched, and two tests pin the degrade contract because that is the property which
> stops the next reader of this section from "finishing the job" by folding them.
>
> **The win is real and modest, and the first measurement of it was wrong.** A micro-benchmark
> that discarded rows reported peak 9.71 MB → 0.16 MB; the real `read_rows` keeps all 16,899, and
> that dict is ~18 MB whatever the reader does. Measured on the live 4.5 MB store through the
> actual function: **peak 24.80 MB → 20.88 MB (−3.91 MB, 16%)**, 0.307s → 0.295s. The saving is
> the file's two transient copies and scales with the file; the floor is the rows the caller asked
> for. Row set and row content are identical across 16,899 and 13,356 real rows.
>
> These are the **second and third** largest crypto stores, not the largest — `strategy_candidates.jsonl`
> is 6.8 MB and `pool.read_candidates` was folded in #626.
>
> **What the fold turned up, and it outlives this item.** Delegating a read moves the reason code
> into a *parameter*, which `DIAGNOSTIC_CODE_INDEX.md` could not see — so #623 taught the
> extractor to read `read_code=` / `write_code=` / `exc_type=`, and that surfaced **~30 codes
> that had never been indexed**, `LEDGER_UNREADABLE` and `LEDGER_WRITE_FAILED` — the audit
> ledger's own failures — among them.
>
> ~~**What is still not indexed, and this is the residue worth picking up.**~~ **Done 2026-08-08.**
> A code passed as a *module-level constant* rather than a string literal was skipped:
> `LIVE_HISTORY_TAMPERED`, `LIVE_HISTORY_UNREADABLE` and `CANARY_HISTORY_UNREADABLE` were absent
> from the index and had been absent before this work too, counted among the sites the index
> reported as "built at runtime". They are not built at runtime — `NAME = "LITERAL"` at module
> scope has exactly one value, readable without executing anything, and those three are the live
> P&L ledger's and the canary registry's own tamper codes.
>
> The extractor now resolves them: **456 codes across 787 sites, and the not-indexable count falls
> 192 → 113.** Verified by running the old and new extractors over the same tree — **+81 rows, −0**,
> and every added row traces to a module constant whose name is written at the call.
>
> **The 113 that remain are genuinely built at runtime** and stay uncounted rather than guessed:
> 79 f-strings, 13 attribute references, 10 locals inside the delegating primitives (already
> indexed at their call sites), 8 call results, 2 starred. Two resolutions are refused on purpose —
> a name reassigned at module scope with a different value, and a name also bound inside some
> function — because either could make the index name the wrong code with full confidence, which
> is worse than the gap it replaces. Both refusals are pinned by
> `test_resolution_refuses_a_name_it_cannot_be_certain_of`.

```
grep -rn 'json.loads(line)\|json.loads(row)\|json.loads(raw)' runtime/mvp_runtime/crypto/ | wc -l   # 21 reads
grep -rn 'from \.\. import.*jsonl\|from \.\.jsonl import' runtime/mvp_runtime/crypto/ | wc -l        # 0
```

`crypto/` reuses the rest of the package — `timeutil` in 24 files, `filelock` 13, `paths` 9,
`coerce` 8, `errors` 16 — and imports `jsonl` from **none of its 45 modules**. The 35 sites are 21
per-line reads and 14 per-line writes. `live_promotion.py:362` states it outright: *"answering it
meant opening the jsonl by hand."*

**Only the 21 reads are duplication. The 14 writes are not, and folding them would be a
regression** — this is the first thing to know about this item, and it is not visible from the
count:

```
grep -rn 'os.fsync' runtime/mvp_runtime/crypto/ | wc -l   # 5 — paper, counterfactual,
grep -n 'fsync' runtime/mvp_runtime/jsonl.py              # 0   live_pnl, live_promotion, live_position
```

Five crypto stores `flush()` + `os.fsync()` every appended outcome; `jsonl.append_lines` does not,
and the audit ledger that uses it has never needed to. The crypto writes are therefore
`append_lines` **plus a durability guarantee it does not offer**, and the code says why —
`paper.py:1353`: *"A trade outcome is the one record the risk guard and feedback learn from;
leaving it in an OS buffer means a power loss can drop a trade that the position file already says
is closed."* `live_pnl.py:565` is blunter: *"…breaker forget a real loss across a crash. Force it
down."*

**Nothing would catch this.** fsync has no observable behavior except across a power loss or a
container kill, so a swap to `append_lines` passes every test, reviews as a tidy-up, and silently
downgrades the durability of the five stores the breakers read. The write side is either left
alone or handled by adding an opt-in `fsync=` to `append_lines` — a separate decision from the
reads, taken separately.

Five modules — `paper`, `live_pnl`, `pool`, `counterfactual`, `live_promotion` — carry the same
reader byte-for-byte apart from two arguments:

```python
except OSError as exc:
    raise ToolError("<CODE>", f"<label> unreadable: {exc.strerror}") from exc
for i, line in enumerate(lines):
    if not line.strip():
        continue
    try:
        record = json.loads(line)
```

That is `iter_objects(path, read_code=..., label=...)` with its arguments spelled out by hand, on
top of a `read_text().splitlines()` that holds two full copies of the store in memory — the exact
shape `iter_objects` was written to retire, and the one the crypto board was OOM-killed on.
`state_dir()` is duplicated verbatim in `paper.py:991` and `live_pnl.py:91` on top of it.

**Two things the reads carry that `jsonl` does not, and neither is cosmetic.** They raise
`ToolError` where `jsonl` raises `PersistenceError` (both descend from `MvpRuntimeError`, so an
`exc_type=` parameter is the small move); and every message names the offending **line index**,
which `jsonl` cannot produce — `iter_objects` yields objects, not positions. The line number is not
decoration here: it is how an operator finds the bad row in a 100k-line store, and the same index
is reused by the tamper and duplicate checks that follow the parse. **Folding the reads means
`iter_objects` grows an enumerate, or the callers keep their own counter and only the parse moves.**
That is the decision this item exists to make, and it should be made once, on the first module.

**One module per PR, and take `counterfactual` first** — it is purely observational by construction
(its own docstring: *"nothing here feeds a gate decision"*), so the error-type and line-index
decisions are settled entirely off the money path before `pool`, then `paper` and `live_pnl`, are
touched.

What this is **not**: the list below rejects `live_*` decomposition and splitting the 300-line
functions, both on money-path risk. This re-opens neither. It replaces a read body with a call —
it moves no boundary and re-cuts no module.

#### G4b. `scripts/` has no repo-root or timestamp authority of its own — 79 sites

```
grep -rn 'Path(__file__).resolve().parents\[' scripts/ | wc -l                              # 59
grep -rn 'datetime.now(timezone.utc)' scripts/ | wc -l                                      # 17, of which
grep -rnF 'datetime.now(timezone.utc).replace(microsecond=0).isoformat()' scripts/ | wc -l  # 10 four-call chains
```

59 scripts open with their own `ROOT = Path(__file__).resolve().parents[1]`. Ten more carry
`datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")` — output
byte-identical to `timeutil.utc_now_iso()`, via a four-call chain whose failure mode is silent: drop
`.replace(microsecond=0)` and the value gains microseconds the runtime's lexicographic expiry
compares are not correct for. That is the footgun `timeutil` exists to have removed once.

**But importing `runtime/` is the wrong fix here.** `runtime/mvp_runtime/_scripts_bridge.py` already
puts `scripts/` on `sys.path` so the runtime can reach `scripts/lib`. Having the 66 scripts that do
not already import `runtime` start doing so makes that dependency **bidirectional** — and most of
them are `validate_*` gates whose job is to validate the runtime. A gate importing what it gates is
a worse property than a duplicated two-line helper.

**So this one is a `scripts/lib` helper, not an import of `runtime/`** — the opposite conclusion
from G4a, reached from the same table.

##### Done 2026-08-08 for the timestamps. **The repo-root half is withdrawn, and the reason is the point.**

`scripts/lib/utctime.py` is the timestamp authority now. **Thirteen copies existed, twelve
collapsed into it, one is kept on purpose** — and the count above said *ten*, which is worth
recording because of how it was wrong. `scripts/lib/gate_runner.py` and
`scripts/lib/runtime_promotion_readiness.py` each carried their own *inside `scripts/lib` already*
— the tell that what was missing was a home, not a helper — and `gate_runner`'s copy is invisible
to the one-line grep in the block above, because it wraps the same four calls across five lines.
A measurement written as a single-line pattern undercounts exactly the copies that were reformatted.

Two of the twelve call sites went to `runtime.mvp_runtime.timeutil` instead, and the split is
principled rather than pragmatic: `promote_memory_candidate.py` (8 runtime imports) and
`activate_safety_flag.py` (5) are **runtime-adjacent CLIs, not gates**. The dependency-direction
argument above is about gates importing what they gate; a script that already imports
`runtime.mvp_runtime.store` has no such constraint, and giving it a *second* timestamp authority
when the runtime's own is already in scope would be the reuse violation, not the fix.
`tests/test_scripts_utctime.py` pins the two halves to the same output so the duplication cannot
become two formats.

**The 59 repo-root sites are not consolidatable, and "79 sites" above overstated the item.**
Measured before touching anything:

* **18 of the 59 use `ROOT` to bootstrap `sys.path`** (`sys.path.insert(0, str(ROOT))`) before their
  other imports run. A helper cannot supply the value that makes importing the helper possible.
* The remaining 41 could take an import, but **22 are standalone** — no `lib`, no `runtime`, no
  `sys.path` patch — and adding `from lib.…` costs them the ability to run under
  `python -m scripts.<name>`, which is the form CLAUDE.md mandates for state-writing CLIs. Verified,
  not assumed: `python -m scripts.activate_core_release` exits 1 with
  `ModuleNotFoundError: No module named 'lib'` while `python scripts/activate_core_release.py` exits 0,
  because `scripts/` has no `__init__.py` and the flat `from lib.x` spelling needs `scripts/` on the
  path. The repo runs scripts directly in 38 places and by module form in 5.
* And `ROOT = Path(__file__).resolve().parents[1]` has **no silent failure mode**. All 59 are
  `parents[1]`; a wrong depth fails immediately and loudly. The timestamp chain was worth removing
  because dropping one call changes the *value*; this changes nothing.

So the repo-root duplication stays. It is the case where the count is large and the risk is zero,
and the fix would trade a self-evident one-liner for an import coupling plus a lost invocation mode.

**One thing found on the way, not fixed here.** `scripts/lib/` modules are imported under **two
spellings** — `from lib.x` (44 uses) and `from scripts.lib.x` (6) — and they are not
interchangeable. `from scripts.lib.runtime_promotion_readiness import …` with only the repo root on
`sys.path` fails on **unmodified `main`**, at that module's own `from lib.safe_io import …`; its
three callers only work because they run in the direct form *and* insert `ROOT`, so both spellings
resolve at once. The two `scripts/lib` modules touched here use a **relative** `from .utctime import`,
which resolves under either spelling; the rest of `scripts/lib` still does not.

#### G4c. The exit-code slot means three different things — **fixed 2026-08-08**

| owner | code 2 | code 3 |
|---|---|---|
| `runtime/mvp_runtime/cli_common.py` (the authority) | `EXIT_BLOCKED` | `EXIT_USAGE` |
| 7 scripts (`promote_strategy_candidates`, `retire_strategies`, `import_crypto_history`, …) | `EXIT_USAGE` | `EXIT_BLOCKED` |
| `scripts/clear_bracket_breaker.py` | `EXIT_REFUSED` | — |

**Inverted, not merely divergent.** `clear_bracket_breaker.py` is the sharpest case: it imports
`force_utf8_io` *from* `cli_common`, then defines its own code-2 constant under a third name.

**Nothing consumes this across the boundary today, and that is checked rather than assumed.** The two
CI assertions that hard-code an exit value (`.github/workflows/docker-image.yml:86,97`, `-eq 2`) both
run a **runtime CLI**, where 2 is `EXIT_BLOCKED` — correct. `scripts/lib/gate_runner.py` only tests
`!= 0`. So this is a hazard with no current victim: the day a script is wired into a check that reads
2 as "blocked", it reports a usage error as a fail-closed block and both sides are individually
right.

##### Fixed 2026-08-08 — and re-measuring first found it was worse than this table said

Eight scripts, not seven. Slot **2** meant `EXIT_BLOCKED` (runtime), `EXIT_USAGE` (six scripts) and
`EXIT_REFUSED` (one); slot **3** meant `EXIT_USAGE` (runtime), `EXIT_BLOCKED` (six) and
`EXIT_REJECTED` (one); and `list_resting_orders.py` skipped 2 altogether, putting `EXIT_BLOCKED` on
3 and a finding on 4.

`cli_common` is the fixed point and the scripts moved, because two things outside them already
depend on its numbers: `.github/workflows/docker-image.yml:86,97` asserts `-eq 2` for a fail-closed
runtime CLI, and `tests/test_place_canary_order_audit_report.py` imports `EXIT_BLOCKED` by name. All
eight already import `runtime.mvp_runtime.*`, so taking `EXIT_*` from `cli_common` costs them no new
coupling — the §G4b dependency-direction argument is about *gates* importing what they gate, and
none of these is one.

**Script-specific codes stay, above the shared range.** `EXIT_REJECTED` and `EXIT_UNOWNED` are
*findings* — the command ran and reports what it saw — which is a different claim from "refused to
act". `EXIT_REJECTED` moved 3 → 4 so it cannot be read as a refusal; `EXIT_UNOWNED` was already 4.

**`clear_bracket_breaker.py` was not just a naming problem.** Its single `EXIT_REFUSED` covered
*both* a missing `--cleared-by`/`--reason` **and** a caught `MvpRuntimeError` — and the second
branch literally prints `BLOCKED:` while the first is an operator typo. Those are now `EXIT_BLOCKED`
and `EXIT_USAGE`, and the fail-closed branch has a test it never had.

**Five operator messages said the wrong word too.** `print("BLOCKED: --candidate-ids is required")`
on a branch returning a usage code is the same confusion one layer up; they say `USAGE:` now.
Checked for consumers first — nothing greps them.

`tests/test_scripts_exit_codes.py` keeps it: no script may redefine a `cli_common` name, and no
script-specific code may land on a shared number. Structural rather than value assertions, because
restating the values would just be a second copy of the thing that diverged.

#### Measured and deliberately **excluded** from the actionable list

- **`schema_cache`, 16 sites — not a bypass.** The `validate_*` gates build
  `Draft202012Validator(schema, format_checker=FormatChecker())`; `schema_cache` builds it *with a
  `Registry`* that resolves `$ref` across the schema directory. Different operation — and its
  process-lifetime cache buys nothing in a script that validates once and exits. Two validators exist
  on purpose; what is missing is a line saying so, not a merge.
- **`integrity`, 35 sites — three file-hash conventions, and folding them would be wrong.**
  `integrity.sha256_file` normalizes `\r\n`→`\n` and prefixes `sha256:`;
  `registry_resolution.raw_file_sha256` is raw and bare; the scripts' `hashlib.sha256(read_bytes())`
  is raw and prefixed. Moving any site to another convention **changes stored digests**. This is a
  naming gap — the raw case has an owner nobody imports — and gets documented, not swept. The repo
  has already paid once for a cross-platform I/O assumption (#572, path separator).
- **`errors`, 18 classes — 16 are out of scope by rule or convention.** Four are in
  `read_only_kernel/` (never modify); eleven are one-per-module in `scripts/lib`, a separate package
  that does not carry `reason_code`; one is `registry_resolution`'s. The genuine gaps are **two**:
  `crypto/strategy.py:45` `SpecParseError` (raised 34×) and `crypto/factory.py:3344` `FusionRefused`,
  both plain `ValueError` — so *"every failure path raises a typed error with a stable
  `reason_code`"* is not true of them, and ~~neither appears in `DIAGNOSTIC_CODE_INDEX.md`~~ —
  **wrong, and the truth was worse; corrected 2026-08-09.** Both appeared, 11 rows and 9. Because
  they take a *message* as their first argument, the extractor filed the message in the code
  column: `created_by must be a non-empty string`. Not a missing entry — an entry whose key
  nobody can look up. See §G3.

#### Two notes on the instrument, because both would have closed a question wrongly

**The first run reported `paths`: 0** — a clean bill of health for the single most-duplicated snippet
in `scripts/`. The AST chain-walker dropped its accumulated attributes when a chain bottomed out in a
call, so `Path(__file__).resolve().parents` read as `Path().resolve` and matched nothing. A
measurement that silently returns zero is worse than no measurement: it closes the question. **Every
zero above was cross-checked by grep before being believed.**

**And `filelock`'s zero is true but narrower than it reads.** All five `fcntl.flock`/`msvcrt.locking`
sites are inside `filelock.py` itself, so nobody hand-rolls that lock. What the detector cannot see
is that `scripts/lib/safe_io.exclusive_lock` is a **second lock authority with a different
mechanism** — `O_EXCL` + polling + stale-lock expiry, against `filelock`'s advisory `flock`. Two
locking implementations, neither hand-rolled, and no document saying which is for what. That is a
`scripts/lib`-vs-`runtime` boundary question of the same shape as G4b, not a duplication to remove.

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

### One number that changed and should not be misread — re-measured 2026-08-09

| | 2026-08-02 | 2026-08-09 | |
|---|---:|---:|---|
| `runtime/` | 53,806 | **61,506** | +14% |
| of which `crypto/` | 24,642 | **32,006** | +30% |
| read-only kernel | 1,938 | **1,938** | unchanged |

The "governance core" of CLAUDE.md's *"strong governance core, thin deterministic runtime"* has
not moved by a line in a week while the runtime around it grew by 7,700. The description has not
matched the shape for some time and now matches it less. Still an observation about the doc, not
a proposal to restructure the code — but the direction is worth watching, because the gap widens
on its own.

**The description was aligned 2026-08-10 (#677), Thomas-approved.** CLAUDE.md now says
"policy-thin" and states what keeps the *core* thin while lanes grow — application-of-chokepoints,
zero domain modules in the core import graph (module-level domain imports only in a lane's own
door, `knowledge_bridge*.py`), lanes removable whole. The import property was measured before it
was written down and is pinned both ways by `tests/test_mvp_runtime_domain_isolation.py`, so this
paragraph's successor is a red suite rather than another stale sentence. The number above stays
worth watching; what no longer drifts with it is the doc.

### G5. The scheduler was profiled, and its hot path must not be optimised — measured 2026-08-09

Read off the production record rather than a benchmark: `scheduler_events.jsonl` carries
`duration_ms` on every `fired` event. ~2,490 completed fires over 2026-08-01 → 08-09, read at
08-09T08:1x. **The ledger is live, so re-running this moves the counts** — the shares and the p50s
are what matter and they are stable.

**Current window (from 08-05, after the PM lane's removal stopped `pm_scan`):**

| kind | fires | p50 | p95 | per day | share |
|---|---:|---:|---:|---:|---:|
| `candle_archive` | 103 | **125.1s** | 129.1s | **~49 min** | **~64%** |
| `crypto_pipeline` | 416 | 14.9s | 22.3s | ~21 min | ~28% |
| `crypto_factory` | 50 | 12.6s | 47.4s | ~3 min | ~4% |
| `crypto_null_control` | 45 | 7.3s | 49.2s | ~3 min | ~3% |
| everything else | ~400 | ≤3.1s | — | <1 min | <1% |

The scheduler works about **76 minutes a day**, and one job is two thirds of it.

Re-measure with the walk in this section's commit message, or just:
`jq -s 'map(select(.action=="fired" and .duration_ms))' scheduler_events.jsonl` — except jq is not
on this host, so use Python. **`pm_scan` at 40% of the whole 8-day record is not a finding**: that
lane was removed 2026-08-02 and its last fire is 08-02T15:35Z. Windowing matters here.

**Do not optimise it.** `ARCHIVE_BOOKS_PER_PASS` (100) × `ARCHIVE_REQUEST_INTERVAL_SECONDS`
(1.1) = **110 of those 125 seconds are deliberate sleeping**, and both numbers are load-bearing:

* The pace is **the venue's own wall, measured**. On the first real pass (2026-08-04) the loop
  issued 352 reads as fast as it could; roughly 70 answered and **282 came back
  `TOOL_RATE_LIMITED`**. Going faster does not archive more, it archives less.
* The per-pass cap exists to **protect the live position path**. `run_due` runs due schedules
  sequentially, so a full 352-book pass would hold the tick — the same tick the live leg's
  `_settle_or_protect` runs on — for ~6.5 minutes. The cap trades archive latency (hours,
  against a window that rolls at 52 days) for tick latency (minutes, against an open position).

Both trades are already argued in the code beside each constant. This section records only that
someone went looking for the runtime's biggest cost, found it, and confirmed the cost is the
point. Same disposition as §G's audit-chain tip scan: **investigated, not a problem, do not fix.**

**What the profiling did find is a governance gap, and it is the reason this subsection exists.**
None of the three constants governing 64% of the scheduler's work appeared in the tunables index,
because the coverage test's name pattern had no `_CEILING`, `_INTERVAL_SECONDS` or `_PER_PASS`.
Neither did **`live_budget.HARD_CEILING_USDT`** — the number bounding what a registered budget may
declare on the live money path, which Thomas set to 200 at bring-up and raised to 500 on
2026-08-08. A pattern that misses a ceiling and a pace misses the two shapes an operator most
often reaches for. Pattern extended, four constants indexed (67 → **71**), four page budgets named
in `MECHANICS`. **`HARD_CEILING_USDT` is the find worth naming**: the tunables index exists so a
number that decides money carries its provenance, and the one bounding live order size did not.

### The refactoring backlog is exhausted — closed 2026-08-09

Every item above is measured and dispositioned: **G1** sliced twice (the sweep is the whole
package), **G2** removed, **G3** indexed and then corrected twice, **G4a/b/c** done. What remains
is the three rejections below and a residue small enough to state exactly, so nobody re-derives it.

**The residue, measured rather than estimated.** CLAUDE.md says every failure path raises a typed
error with a stable `reason_code`. Twelve sites in `runtime/` raise a builtin directly:

```
ast walk over runtime/**/*.py for `raise ValueError|TypeError|RuntimeError|KeyError|OSError(...)`
```

| | n | |
|---|---:|---|
| in `read_only_kernel/` | 3 | never-modify rule |
| a documented design decision | 1 | `timeutil.parse_iso` — its docstring says callers catch it and re-raise their own |
| argument validation / programming error | 5 | a dataclass `__post_init__` invariant, `authority_invariant_holds`' P0–P6 check |
| **genuine candidates** | **3** | `live_governance` ×2, `factory`'s cost-model-mismatch guard |

**And the three are a diagnostics gap, not a safety one** — checked, not assumed: the live route
already wraps these calls in `except Exception  # noqa: BLE001 — the order is at the venue;
report, never raise`, so a bare `ValueError` does not escape a handler designed for
`MvpRuntimeError`. What is lost is the named code in the record, not the containment.

**What would re-open this section.** A new crypto module arriving unswept (the tunables test
fails), a decision-shaped constant with no provenance (same test), a reason code that is really a
message (the index test), a reader that hand-rolls a store read
(`grep -rn 'read_text(encoding="utf-8").splitlines()' runtime/mvp_runtime/crypto/`), or a script
minting its own `EXIT_*`. Each of those is a test now rather than a thing to remember, which is
the actual output of this section — the greps that found them are in the subsections above and
every one of them has since been kept true by a test instead.

**The pattern worth carrying, because it cost the most to learn.** Three times the obvious
consolidation was wrong and only measurement said so: the crypto **writes** must keep an `fsync`
that `jsonl.append_lines` does not do; `scripts/`' 59 repo-root snippets are 18 bootstrap paradoxes
and 22 lost invocation modes for a one-liner with no failure mode; and a code that vanished from
the diagnostic index wanted the extractor fixed, not the index overwritten. Two of this section's
own headline counts were also wrong by roughly four (§G1's constants, §G3's codes) — both because
a grep over upper-case names measures naming convention rather than the thing being counted. **Do
not act on a count in this file without re-running the command beside it.**

---
## H. Equity-perp lane (Hyperliquid HIP-3) — **S1 is running as of 2026-08-04**; next is S2

> **Header rewritten 2026-08-05.** It read *"waiting on two env-level steps"* and both were
> taken on 2026-08-04: `MVP_CANDLE_ARCHIVE=hyperliquid` in the scheduler service, and
> `schedule_62466c9b92a1206c2f82` (`candle_archive`, 3600s). The archive has run every hour
> since. What that first day cost is recorded below, because **every defect it found was
> invisible until the thing actually ran** — the code, its tests and this file all described a
> working archive while the first real pass was losing 80% of its work and reporting COMPLETED.
>
> Measured 2026-08-05T16:00Z: **354 books, 89 symbols, 788,553 candles**, 100 books per hourly
> pass, `degraded=0` for 25 consecutive passes. Depth per book: 15m ≈4,750 bars (~49 days
> against the 52-day ceiling), 1h ≈3,100 (~125 against 208), 4h ≈840, 1d ≈140. 354 rather than
> 356 because `xyz:UNITREE` listed today and has no completed 4h or 1d bar yet — not a gap.

### H0. What running it found, and what each fix cost (2026-08-04 → 08-05)

Three defects, all shipped, all live now. They are listed because the shape repeats: **a
bounded loop with a fixed order silently starves whatever sits past the boundary, and reports
`degraded=0` while doing it.**

| | Defect | Fix |
|---|---|---|
| #524 | The first real pass fired 352 reads as fast as it could; ~70 answered and 282 came back `TOOL_RATE_LIMITED`. It reported COMPLETED, because `ARCHIVE_ALL_BOOKS_DEGRADED` needs *all* books degraded. 80% loss reached nobody. | Pace 1.1s; latch on `TOOL_RATE_LIMITED` and stop (a 429 is the step before a 418 ban); bound a pass to 100 books so it cannot hold the tick the live leg's `_settle_or_protect` runs on; rotate the start offset; raise `ARCHIVE_RATE_LIMITED`. |
| #526 | A bounded pass walking symbol-major gave 25 symbols all four timeframes and 63 symbols nothing — spending budget on 1d books that cannot lose a bar while unarchived 15m books shed ~12 an hour. | Order by perishability: every first fill before every refresh, fast timeframes first inside the first fills. |
| #544 | #526 ranked the **whole** list, so once every book had a file, 4h sat at work-list index 176 and 1d at 264 — permanently past a 100-book budget. Measured on deployed code: `within budget {'15m': 88, '1h': 12}`, `never attempted {'1h': 76, '4h': 88, '1d': 88}`. 176 books would never have been refreshed again. | Refreshes rotate as one list; first fills keep absolute priority. Deliberately *not* ranked — no refresh is near its ceiling, so ranking them optimises a quantity with no deadline while reintroducing starvation. |

**#544 is #524's defect rebuilt one PR later on a different axis**, by the same author, in the
PR that was supposed to improve the ordering. The lesson worth carrying is not "rotate things"
but **that `degraded=0` is not evidence of coverage** — both bugs were invisible in the pass
summary and only showed up when the work list was enumerated directly against the live store.

Verified after deploy by running the ordering inside the built image rather than by reading it:
24 hourly passes reach `['15m', '1h', '4h', '1d']`, where the pre-#544 code reaches
`['15m', '1h']`.

**One operational note that is not in any commit.** The schedule was disabled by hand for
~1h on 2026-08-05 (repeated hourly bursts of ~280 rate-limited reads escalate toward a 418 IP
ban) and re-enabled after the #524 deploy. The disable left **no trace anywhere** — that gap is
what #522 closed, and the re-enable at 16:10:36Z is the first `enabled` event the scheduler
ledger has ever carried.

### H1. Original record (kept — the reasoning below is still the authority for *why*)

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

**~~Nothing runs.~~ Superseded 2026-08-04 — see H0.** The schedule is registered and the gate is
open. The measurement below still describes the gate correctly and is kept for that; only the
"no schedule is registered" half is dead:

```
MVP_CANDLE_ARCHIVE=''            -> NoCandleArchiveCollector, collect(): ARCHIVE_NOT_ENABLED
MVP_CANDLE_ARCHIVE='hyperliquid' -> HyperliquidCollector          # no grant required
```

> **This block previously read `SafetyGateBlocked: ACTIVATION_MISSING` on the second line, and
> the paragraph under it said "gated by a grant" describes the archive "and not the one that
> moves real money".** #496 (Thomas, 2026-08-04) moved the archive to
> `safety_gate.select_env_gated` — the same env-only door `live_trading` took on 2026-07-28 —
> so the contrast that sentence drew no longer exists. The archive is the **second** capability
> on that door and the first that is not live trading.
>
> Why: the grant is TTL-capped at 30 days and archiving is worth nothing unless it runs for
> months against a window that rolls, so a renewal gap is not a pause — it is a hole nothing
> can fill. Half of `live_trading`'s argument does not transfer (nothing here can be trapped
> open) and neither does its kill-switch counterpart; what makes the trade acceptable is the
> side live trading did not have — public candles, no key, sends nothing, orders nothing, and
> **feeds nothing**. The full reasoning is at `select_candle_archive_collector`, and
> `test_the_env_only_gate_has_exactly_the_capabilities_thomas_named` pins the caller set at two.

This machine's grant inventory is no longer part of this lane's answer. The count is given
elsewhere and goes stale the first time one is issued or removed; ask the directory, or the
board's 권한 line.

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

**So nothing governance-shaped blocks archiving any more.** What remains is two environment
steps, both minutes long and both the operator's.

**No approval record names this lane.** 53 records in
`.runtime_governance_state/approvals/approvals.jsonl`, zero mentioning equity / Hyperliquid /
HIP-3 in any human-readable field; they are runtime action approvals
(`crypto.strategy_pool.retirement` and kin), which is a different thing from a lane decision.

**Nothing in code references S0, and the mechanism this section said enforced it is gone.** It
read: *"S0 is enforced through the grant — Thomas does not issue `hyperliquid` until it is
ratified."* #496 removed that grant, so that sentence no longer describes anything. It cost
nothing here only because S0 was answered the same day (#505) and S1 is read-only — but a
reader should not carry away that a grant is holding this lane shut.

**What still holds the line that matters is a different mechanism, and it is a real one.** S0's
answer draws it at live orders, not at collection: *"live orders (S3 and later) do not open
until this is promoted to settled."* That is enforced by the budget, not by any archive gate —
`schemas/live_trading_budget.v0.1.schema.json` pins `venue` to `enum: ["binance_futures"]` and
`live_budget.SUPPORTED_VENUE` refuses anything else with `BUDGET_INVALID`. **An equity live
order cannot register a budget at all**, and widening that enum is a schema bump that has to be
argued for. Collection is ungated by governance and the money path is gated by a schema — which
is the right way round, and worth stating because this section previously implied the reverse.

**In order, what was needed to archive a single candle — all of it now done:** ~~S0 ratified~~
(2026-08-04) → ~~the `hyperliquid` grant~~ (no longer exists, #496) →
~~`MVP_CANDLE_ARCHIVE=hyperliquid` in the scheduler service~~ → ~~a registered `candle_archive`
schedule~~ (both 2026-08-04, `schedule_62466c9b92a1206c2f82`). The estimate that the last two
were "minutes" was right about the keystrokes and wrong about the work: the two steps took
minutes and the three defects they exposed took a day (H0).

**What is left in this lane is S2, not S1.** S1 — the venue seam and the read-only collector —
is running. S3 and later stay shut on S0's provisional strength, enforced by the budget schema
rather than by anything in this lane; that mechanism is unchanged and described below.

**S2 splits into a part that is buildable and a part that is a clock, and the split matters
more than the label.** §8b is the authority; the measured state as of 2026-08-08:

**(a) cost re-derivation — DONE 2026-08-09.** All three legs are measured: funding and the order
book off the venue, the deployer configuration off `perpDexs`, and the base fee table from
Hyperliquid's published schedule. What (a) produced is not a pass mark — see the floor check
below.

**Fees, and the multiplier that decides them.** Published Tier 0 is **taker `0.045%` / maker
`0.015%`** (4.5 / 1.5 bp). The HIP-3 scaling is in the docs' own formula:

```
scaleIfHip3 = deployerFeeScale < 1 ? deployerFeeScale + 1 : deployerFeeScale * 2
```

`xyz` runs `deployerFeeScale = 1.0`, so the scale is **exactly 2** and the effective rates a
trader pays are **taker 9.0 bp / maker 3.0 bp**. Costing this venue at the base table halves it.

**The tight spread does not make this venue cheap — the multiplier dominates it:**

| | TP exit (maker) | stop exit (taker both legs) |
|---|---|---|
| HIP-3 effective | **12.1 – 12.5 bp** | **18.3 – 18.9 bp** |
| Binance model today | 10.0 bp | 16.0 bp |

**Floor check against `MAX_ENTRY_COST_R = 0.25`**, with R = `stop_atr` 1.45 × the archive's
measured median ATR (1h 47 bp / 4h 128 bp / 1d 463 bp):

| `max_holding_bars` | 1h | 4h | 1d |
|---|---|---|---|
| 12 | 0.193 – 0.205 | 0.088 – 0.099 | 0.056 – 0.072 |
| **24 (base)** | **0.209 – 0.227** | 0.111 – 0.132 | 0.093 – 0.126 |
| 48 (the space's ceiling) | **0.240 – 0.271 ⚠︎** | 0.156 – 0.196 | 0.168 – 0.233 |

**The floor check passes** — §8b stops the lane only if *every* timeframe fails it, and 4h and
1d clear it with room even at the ceiling. **But 1h has no margin and breaches under two
conditions that are both real:** `max_holding_bars = 48`, which `_EXIT_PARAMS` actually permits
and the search can select; and `assetToFundingMultiplier` going 0.5 → 1.0, which doubles carry
and breaches at the *base* holding period (0.271) — a setting of exactly the kind the deployer
changed on 2026-08-06.

So (a)'s output is a constraint rather than a verdict: **on this venue 1h cannot use the top of
its own parameter space, and what pins that is deployer configuration, not the strategy.**

| | Binance USD-M (the model today) | Hyperliquid HIP-3 (measured) |
|---|---|---|
| funding settlements | 3/day | **24.1/day** |
| rate per settlement, \|r\| | 0.37 – 0.58 bp | **0.088 – 0.124 bp** |
| **daily carry** | **1.1 – 1.7 bp/day** | **2.1 – 3.0 bp/day (~1.8x)** |
| spread | — | **0.26 – 0.91 bp** |
| depth within 5 bp | — | **$8.6k – $268k** |

The headline is a correction: **§8b's "24 settlements a day" is right and the 8x it implies is
not.** Eight times the settlements at a fifth of the rate is ~1.8x the carry. Costing this venue
by settlement count overstates it fivefold; the daily carry is what binds. Constants are
deliberately NOT changed — `cost.py` is Binance-scoped, the budget schema blocks any equity
order, so there is no consumer, and editing them now would only disturb the basis crypto
evidence was scored under.

> **Two corrections to the paragraph above, both made 2026-08-09, and the second matters more.**
>
> **The deployer fee share is NOT unobtainable.** This file said it "cannot be derived from code
> or a public endpoint". `perpDexs` — which `live_symbols` already calls — carries the whole
> configuration: `deployerFeeScale` (1.0), `feeRecipient`, `deployer`,
> `assetToFundingMultiplier`, `assetToFundingInterestRate`. The claim was made without looking
> at the response's keys; looking cost one call. What genuinely remains outside is only the base
> schedule that `deployerFeeScale` multiplies, which needs Hyperliquid's published fee table.
>
> **The 1.8x is a setting, not a property of the venue.** `assetToFundingMultiplier` is **0.5**
> on 107 of `xyz`'s 108 assets, and the measured carry already has that 0.5 in it. If the
> deployer raises it to 1.0 the carry doubles and the ratio becomes **3.6x, not 1.8x**. These
> are levers the deployer holds and uses: `xyz`'s scale changed at **2026-08-06T15:08:37** —
> two days after this archive started — `para`'s on 2026-08-07, and `hyna` runs a scale of
> 0.1111. Same axis as §6's counterparty items. Every number in the table above is a reading
> with a timestamp, not a constant, and S3 must re-read rather than inherit them.

**(b) the reproducibility gate — unevaluable, and not by a margin that code can close.**

| tf | deepest book | median | vs the 500-day gate | reaches 500 |
|---|---|---|---|---|
| 15m | 56d | 56d | 11% | ~2027-10 |
| 1h | **212d** | 121d | 42% | ~2027-05 |
| 4h | 298d | 121d | 60% | ~2027-02 |
| 1d | 299d | 120d | 60% | ~2027-02 |

**1h has passed the venue's 208-day ceiling** — the first hard evidence that archiving earns
what §2 claimed for it, four days in. But what holds the gate shut is **symbol age, not the
archive**: history that never existed cannot be collected, and depth grows one day per day. The
dates above are for the single deepest symbol; on the median they are late 2027.

**Do not shorten it by minting on shallow data.** The coverage-gate comment in `factory.py`
spells out the mechanism: a shallow window puts every trade in the newest walk-forward slice,
`temporal_consistency` is 0 by construction, and the family retires as FRAGILE — blamed for a
window that had no data in it. Early evaluation does not produce a weak answer, it produces a
wrong one.

**The clock is the reason this order matters.** `candleSnapshot` serves at most 5,000 candles
and nothing behind them, so 15m history older than ~52 days and 1h older than ~208 is
unrecoverable once it rolls — measured, not projected: `xyz:SP500` listed 2026-03-18 and the
venue already returns only 52 days of its 15m bars.

**Of those two, 1h is the one that still matters, and the reason changed on 2026-08-04.** This
paragraph used to lean on section F's "the holdout is strongest at the fast end"; #487 measured
the full 426 holdout blocks and 15m came out **net −0.2691R, the worst rung rather than the
best**, so #501 withdrew that argument from the lane's proposals. The factory rotation has also
moved off 15m onto {1h, 4h, 1d}. What survives is narrower and does not depend on any strategy
being right: **1h is both inside the rotation and permanently unservable at factory depth
(208-day ceiling against 500), and its window rolls every day.** Archiving is the only path to
depth there.

---

## I. The family proposer asks for a decision on the thinnest evidence in the system — designed 2026-08-05, **awaiting a Thomas decision**

> **Status 2026-08-06: the one prerequisite this section named is closed (#545), so what remains
> is the judgement alone.** The quarantine that I2 called *"the one piece that is not optional"* —
> the promotion door refusing an unrecognised `derivation_type` — exists and is fail-closed by
> omission rather than by clause. Nothing else here is built and nothing should be: I3 states
> that a declarative family is a second authority for *what a family is*, against the standing
> one-concept-one-authority guardrail, **which is why this section asks rather than proposes.**
> Building I2 without that answer would be taking the decision by writing it.

Nothing here is built. It is a design with its costs named, recorded because the alternative is
that the same reasoning gets re-derived from scratch, and because **the choice it turns on is
explicitly not the runtime's to make** (`proposer.py`: "Adding a family to `factory.TEMPLATES`
stays a human code change in Thomas's PR").

### I0. Why this matters now, and the number that says so

The promotion door yields zero — 0 of 1,140 candidates confirm out of sample (F1, F2) — and the
generator is the only lever left. Measured 2026-08-05 over the 461 `(family, timeframe, symbol)`
contexts the store can centre: only **43 are centred by a row with a positive holdout**, 227 by
one the tail judgeably refuted (#532 closed the second half of that). The library is what the
search searches, and the library only grows through this door.

### I1. The bottleneck is not typing, it is that the decision has no evidence behind it

Every other decision in this system is made on accumulated out-of-sample evidence. Promotion
reads holdouts; fusion reads parent evidence; retirement reads thousands of trades. **Family
installation is the only one made from a rationale sentence and one backtest on one snapshot** —
which is all `evaluate_proposal` can produce, because a proposal is a single spec rather than a
family with a parameter space.

`MAX_UNREVIEWED_BACKLOG = 12` exists because of this ("proposals accumulate faster than anyone
reviews", 2026-07-24). The cap is a symptom: the queue does not drain because draining one entry
means authoring a `StrategyTemplate` — an `entry_builder` callable, a parameter space, tests —
on the strength of a paragraph.

**An option that does not work, recorded so it is not re-proposed:** "let an accepted proposal
be minted as a candidate and accrue evidence." A candidate's evidence is computed once at mint
and never accumulates. Family-level evidence only exists because a family is minted repeatedly
across contexts and generations, and that requires the rotation. A proposal that is not in the
rotation can never earn the evidence its own install decision needs.

### I2. Design — trial rotation slots

**Piece 1: a declarative family.** Every `entry_builder` in `TEMPLATES` is "fixed conditions with
param-substituted thresholds", which is expressible as data:

```python
@dataclass(frozen=True)
class ConditionPattern:
    feature: str
    comparison: str
    param: str | None = None            # threshold comes from this param
    value: float | str | None = None    # or it is a literal
    value_from: str | None = None       # or another feature
```

`entry_builder` becomes pattern rendering. **Data does not become code**: what the data chooses
is a feature name, a comparison and which param feeds the threshold — three closed sets
`known_features(venue)` and `_NUMERIC_COMPARISONS` already judge. Builders doing arithmetic on a
param (`_xs_reversion_long_entry`'s `0.5 - p[...]`, `_session_trend_long_entry`'s label lookup)
are **not** expressible and stay code; an LLM proposal is always the simple shape, so this does
not bind.

**Piece 2: admission reuses the approval machinery, and adds no Gate.** A trial family file
carries the declaration and its content hash; the runtime loads it only when an approval record
for that hash exists in `THOMAS_CORE/approvals/` and is unrevoked. Absent, mismatched or revoked
→ not in the rotation. Thomas still decides; the decision becomes *approve this hash* instead of
*author this family*.

**Piece 3: quarantine from the live path — the load-bearing part.**

- A slot cap (4 is the suggested start), so trials can never crowd out the proven library.
- Minted rows carry `derivation_type: "trial_family"`, registered in
  `pool.DERIVATION_TYPES` — a **closed set** (`seeded_template`, `crossover`, `mutation`) that
  `validate_candidate_lineage` refuses at the append door, with its parent-count rule in
  `_PARENT_COUNT_RULES` (a trial family is fresh generation, so `(0, 0)` like a seeded row).
  This is a feature, not an obstacle: the quarantine tag is schema-enforced at the store's own
  door rather than being a convention a writer can forget.
- ~~**`scripts/promote_strategy_candidates.py` must refuse them by default.**~~ **Closed by #545
  on 2026-08-05**, the day after this was written, and it is worth reading before deciding the
  rest. `pool.PROMOTABLE_DERIVATION_TYPES` is an **allowlist** and `assert_promotable_derivation`
  refuses at the ask; `promotable_backlog` carries a matching `derivation` axis in its refusal
  partition, so the board cannot advertise what the door would refuse. Both refuse **nothing
  today** — the allowlist equals the set the store admits — which is exactly the point: a fourth
  derivation type is quarantined by *omission* rather than by remembering to add a clause.
  **So the piece this section called "not optional" is done, and a `trial_family` tag would land
  outside the live path by default.** What is left in §I is only the guardrail judgement in I3.
- A trial graduates into `TEMPLATES` — real code, Thomas's PR — only after producing confirmable
  holdout evidence. The builder gets written for a family that has already earned it.

### I3. What it costs, stated rather than discovered later

- **A declarative family is a SECOND authority for "what a family is"**, against the standing
  "one concept = one authority" guardrail. The mitigation is that `TEMPLATES` remains the
  authority for *proven* families and trials are a capped staging area with no live path — but
  that is a judgement about the guardrail, which is why this section asks rather than proposes.
- It widens what the model influences: still never code, but now what gets **mined**, not only
  what gets suggested.
- **It will almost certainly confirm nothing.** 0 of 1,140 confirm out of sample and a random
  entry loses 0.13R here. What this buys is that failure becomes cheap and legible, not edge.
  Any version of this pitched as "more families will find an edge" is misreading F1.

### I4. The smaller alternative, if I2 is too much

The proposer renders an accepted proposal into `StrategyTemplate` code plus a test and opens a
PR. No new authority, no declarative form, buildable today; Thomas reviews a diff instead of
authoring one. **Weaker on the actual problem** — the decision is still made on one backtest, and
review is still per-proposal, so the queue still does not drain. It removes the typing, which
I1 argues is not what is blocking.

---

## Per-machine setup that does NOT travel via git

A fresh machine has the code but not the local runtime state (gitignored, per CLAUDE.md). To actually
*run* the agent there, re-do the local activation once:

- Core activation pointer: `.runtime_governance_state/CURRENT_CORE_RELEASE.yaml`
- Capability opt-ins in the deploy `.env` — since 2026-08-10 the environment is the gate
  (grants retired); see the `_GATE_ENV_VARS` roster in `tests/conftest.py` for the full list
- Control state + ledger + schedules under `.runtime_governance_state/`

None of this is "planned work" — it is per-machine state you re-establish with the CLAUDE.md
"Core activation" steps + the `.env`. One piece of planned work does follow from 2026-08-10:
the retired grant machinery (`safety_gate.authorize` / `select_gated*` /
`build_activation_record`, `scripts/activate_safety_flag.py`, and the unit tests that mint
activation records) has zero runtime callers — the containment test enforces that — and
awaits deletion as its own reviewed change.

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

**What the current deployment machine has** — so a new machine knows what it is missing rather
than discovering it one inert mock at a time. Until 2026-08-10 this meant twelve per-machine
grants; since then (Thomas) **the environment is the gate**: a new machine reads mocks until
each opt-in var it needs is set in the deploy `.env`, and nothing errors to tell you that — an
unset opt-in is indistinguishable from a deliberate mock by design. Copy the `MVP_*` opt-ins
(and their key vars) off the running machine's `.env`, not from memory. Leftover
`safety_flag_activations/*.json` files are inert either way. The three prediction-venue
capabilities (`kalshi_market_data`, `polymarket_market_data`, `binance_prediction`) were
**deleted** 2026-08-02 with section A's lane and must not be re-enabled.

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

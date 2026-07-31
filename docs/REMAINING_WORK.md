# Remaining Work — canonical to-do list

**This is the single place to answer "what's left to build?" from any machine.**
It is committed to git on purpose: per-machine memory does not travel between computers,
so the durable hand-off lives here. On a fresh machine: `git pull`, then read this file.

Last updated: **2026-07-31** (`main` = `128ed20`), adding **section E** — deferrals that were
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

## A. Prediction-market trading (Kalshi / Polymarket / Binance) — PM1 **running**, 3.7 of 14 days

Roadmap: [`docs/PREDICTION_MARKET_ROADMAP_V0.1.md`](PREDICTION_MARKET_ROADMAP_V0.1.md) (on `main`).
**PM1's code is complete and the window is open.** Three venue adapters, screening, the
deterministic matcher with operator confirmation, the fee-adjusted detector, the observation store,
both scheduler cadences (watch and discovery), the proposal record and the exit report all live
under `runtime/mvp_runtime/predmarket/`. PM2 and PM3 are untouched.

**Measured 2026-07-31T03:00Z** (ask the machine rather than trusting these — they go stale by
the hour):

```
verdict   INSUFFICIENT_WINDOW      window 3.6987 of 14 days required
readings  86,598 / 87,127 priced   coverage 0.9939   82 pairing(s)
totals    29,598 opportunity readings, 100 episodes
incidents MARKET_NOT_LISTED=268   (frozen — the dead group was retired, see below)
```

Most confirmed groups pair **binance×polymarket**, with kalshi×polymarket next and a handful
involving kalshi×binance or all three. That distribution is itself an open decision — see the
Binance box below.

**What the run has cost so far is six defects, none of which a green test suite showed.** Each was
found by reading what the deployed thing actually produced: Gamma was paginating past 400 rows it
claimed to include (#318), a zero-depth touch counted toward frequency (#321), a quoted Polymarket
read dropped five fields (#348), three separate changes to what counts as an opportunity all
shipped under `predmarket_opportunity.v0.1` (#350), the sheet offered one market four partners
of which three were impossible (#371), and **the exit report stopped being able to read its own
evidence** (#383).

That last one is the phase's own success turning into its failure, so it is worth stating in
full. The store is cumulative by design — a reading already banked cannot be un-banked — and at
day four it was **290 MB across ~87,000 rows**. `build_pm1_report` materialized all of it:
measured, that needed **over 1.2 GB** against roughly 1 GB free on the host *also* running the
scan that writes it, and the day-14 volume projected to ~5 GB on a 3 GB machine. The artifact the
whole fourteen days exists to produce was on track to be unreadable before the window closed, and
nothing would have said so until someone ran it at the end. It now streams and keeps a projection
of each row rather than the row: **51 MB for the full store**, with output verified byte-identical
against the old reader on a 30,000-row slice of the real data.

Two things to carry from it. The same whole-file-read shape had already OOM-killed the crypto
board once and was repaired **at that one caller** (`crypto/dashboard.py`) rather than at the
shared primitive, which left it in place for the next store to grow into — so the fix this time
went into `jsonl.read_objects` itself. And **the store growing is not a bug to be trimmed**: the
window is cumulative, so the thing that had to change was the reader, never the evidence.

Two threads run through them and both are worth carrying into PM2. **Anything a person has to
keep up to date drifts** — a hand-listed field rebuild and a hand-bumped version constant were
two of the five, and both fixes made the code state its own shape rather than restate it.
**A guard downstream is not a guard**: #371 was proposing pairings that `pairs.py` would refuse
anyway, which sounds harmless until you notice the refusal is first-come — confirm the impostor
and the correct pairing is refused instead, silently and permanently.

Trust the boxes below over this paragraph — a prose summary of a moving track is how the previous
version came to say "no code exists yet" above a list of shipped modules.

Phasing: observe (no money) → paper (no external effect) → approval-gated live (per-order approval).

- [ ] **PM0 — venue access** (operator-only, no code): Kalshi international signup (KYC), Polymarket
      Polygon/USDC wallet, and the **Korean regulatory judgment call** (grey area). Blocks PM3 only,
      not PM1/PM2.
- [~] **PM1 — observe-only pipeline** (no money; no account except Binance's key): **code complete
      2026-07-27, running since 2026-07-27T09:49Z.** Every box below is ticked except the operator
      steps and two deliberate deferrals. What remains is calendar time — the window cannot be
      shortened, only waited out — plus the confirmation sessions that keep feeding it.
  - [x] Read-only venue adapters (Kalshi REST; Polymarket Gamma + CLOB) behind
        `kalshi_market_data` / `polymarket_market_data` safety flags, DEGRADED semantics —
        done 2026-07-26 (`runtime/mvp_runtime/predmarket/market_data.py`). One normalized
        shape (YES-side probability in `(0,1)`, sizes in contracts) so no venue's vocabulary
        reaches the comparison. Field names verified against both API references on the day,
        which is how we learned **Kalshi now serves decimal-dollar strings**
        (`yes_bid_dollars`), not the integer cents an older API used — a parser written from
        memory would have read nothing. Polymarket is quoted from the **CLOB book only**;
        Gamma's `outcomePrices` are a derived figure, and a market whose book was not read
        comes back *unquoted* rather than priced. **No bid is not a bid of zero**: every
        price is `float | None`, and 0 / ≥1 / unparseable all read as "not quoted".
  - [x] Event-pair matching — auto candidate generation + **operator confirmation per pair**
        — done 2026-07-26 (`predmarket/matching.py`, `pairs.py`, `pairs_cli.py`).
        Deterministic only: normalized token overlap + close-date proximity, with **numeric
        tokens as their own gate** (token overlap alone scores "BTC above 100k" against
        "Bitcoin above 90k" at 0.71 — boilerplate outvotes the one token that IS the
        question). Unknown is never mismatch: a Kalshi market has no category, so a missing
        one is excluded from the decision rather than counted against it. Confirmation
        **requires a note comparing how both venues resolve the event** — the risk no text
        comparison can see — and one market belongs to at most one **group** (`pairs.py`,
        `MARKET_ALREADY_GROUPED`). Since #371 the *candidate list* honours that too, greedily
        and per venue pair: proposing one market against several counterparts from the same
        venue offers options of which at most one can ever exist, and because the refusal is
        first-come, confirming an impostor refuses the right pairing permanently. Scoped to the
        venue pair rather than globally, because a group spanning three venues is three pairings
        over three legs and a global claim set keeps one — the first attempt did exactly that
        and an existing test caught it. Every judgement,
        including near-misses, records which gate failed and by how much: that record is what
        makes decision #2's LLM-gap loop able to *fix* the rules rather than just widen them.
  - [x] Third venue **Binance prediction markets** (markets are Predict.fun's on BNB Chain) —
        done 2026-07-26; **quoted** since the order-book routing below, and **proposable** since
        #262, which found it had been quoting markets for a week that the matcher could never
        propose. It also carries the venue's own `polymarketConditionIds` cross-reference, which
        the matcher
        treats as evidence outranking the wording gate. **New operator precondition:** unlike
        Kalshi and Polymarket it is key-authenticated (`MVP_PREDICTFUN_API_KEY`, Discord
        ticket), so PM1's "no account needed" property does not extend to it; a missing key is
        reported as `PREDMARKET_API_KEY_MISSING`, never as an outage. Its fee schedule is
        unread, so its legs report **no knowable cost** rather than a guessed one.
  - [x] Order book + fee — resolved 2026-07-26 by routing through **Binance's**
        Prediction Trading REST API instead of the venue directly (Thomas's call; funding is
        why). Binance publishes a real `/order-book` and a per-topic `feeRateBps`, so this
        venue is now **quoted**, not merely listed. All endpoints are signed. The fee
        *formula* is still unpublished, so the bps rate is applied flat on notional (the
        pessimistic reading) and every leg records `fee_model` saying so.
  - [ ] Confirm the Binance prediction fee formula (flat vs `P x (1-P)`), then drop the
        assumption. Until then costs are over-estimated, which skips observations rather than
        inventing them. **Since 2026-07-28 there is a schedule row** rather than a hole: a flat
        `BINANCE_OBSERVED_TAKER_RATE = 0.02` with `flat: True, verified: False`, so a Binance leg
        prices pessimistically and every record says the schedule is unverified. The box stays open
        because "unverified" is a caveat, not a rate.
  - [ ] ⚠️ **Decide whether Binance belongs in a multi-week observation at all** — a venue
        question, not a bug, and the reason it is written down is that the alternative is
        rediscovering it in three weeks with an empty report.
        **⚠️ This box has been overtaken by events, and is corrected rather than rewritten,
        because how it was overtaken is the finding.** 40 of the 66 confirmed groups now contain a
        Binance leg (binance×polymarket 37, binance×kalshi 2, all-three 1). Nobody decided that:
        the sheet proposed them, they passed verification, and they were confirmed — by Claude,
        on 2026-07-28/29, at the operator's instruction to work the candidate queue down. **The
        decision below is still formally open and is still Thomas's, but it is being answered by
        default, in the direction of "keep Binance", by a window that already depends on it.**
        Retiring those 40 groups later would not recover their history; the readings are already in
        the denominator.
        Two of the concerns that motivated this box were re-measured on 2026-07-30 and one of them
        is simply wrong as written:
        (a) **`feeRateBps` still rides on the listing and nowhere else.** A confirmed leg captures
        its rate at confirmation (#287) or never, because the by-id path is Binance's order-book
        endpoint and that response carries **no market metadata at all** — no title, no close time,
        no rules, no volume, and no fee rate. That is structural to the endpoint, not an adapter
        gap. So a Binance leg that has since left the listing cannot be repaired: re-confirming
        captures nothing. One group was retired for exactly this after producing `readable 0/1`
        from confirmation onward.
        (b) **The predicted aging-out has not happened.** Over 48,754 readings across ~3 days,
        **zero** carry a missing Binance leg. Every `MARKET_NOT_LISTED` incident in the window is
        **Polymarket** (252 of them, and 248 are one dead token — see the box below). The
        corollary is that the old "listing returns 40 markets regardless of `limit`" claim was
        wrong: discovery reads `DISCOVERY_MARKET_LIMIT = 300` from Binance and screens it (300 →
        ~186 observable on a recent run). The 40 is `BINANCE_DISCOVERY_DETAIL_LIMIT`, a budget on
        per-topic **detail** calls inside one discovery run, not a ceiling on the listing.
        Aging-out remains plausible over 14 days; it is now a thing to watch rather than a
        measured problem, and the watch is `MARKET_NOT_LISTED` by venue in `pairs_cli report`.
        **The options are venue-shaped, and Thomas's:** ratify the status quo (Binance stays, and
        the 40 groups stand), treat it as discovery-only from here (propose from it, never confirm
        it again) and let the existing groups run out, or retire the Binance groups and rebuild the
        window on Kalshi×Polymarket alone — which costs the ~3 days already banked on those 40.
        Whichever, record it here — "we tried Binance and the report was empty" is not a finding
        about prediction markets.
  - [x] **A dead Polymarket leg was retired** — done 2026-07-30.
        `predmarket_event_group_b18136e1e77430110726` had produced `MARKET_NOT_LISTED` on **248
        consecutive readings**, every scan since 2026-07-27. Checked directly against Gamma: the
        CLOB token returned **zero rows even with the `active`/`closed` filters removed**, so it
        was neither a filter artifact nor an outage — the token was gone from the venue and the
        group could never price again. Retired with that evidence in the reason, on Thomas's
        instruction.
        **What retiring does and does not do:** it stops further unreadable rows entering the
        denominator; it does not remove the 248 already banked, because the window is cumulative.
        So coverage did not jump on retirement (0.9895 → 0.9915 came from new readings) and
        `MARKET_NOT_LISTED=268` is now a frozen historical count rather than a growing one. The
        Binance leg of that group was readable throughout and is not implicated.
        The second affected group (`...6a68084e6504`, Puffpaw FDV) was **left alone** on purpose:
        its token is live, `active: true, closed: false`, expiring 2027-01-01, with only 4
        incidents — transient, and retiring the wrong one is the mistake that note existed to
        prevent.
  - [x] Observation store + `pm_scan` scheduler — done 2026-07-26
        (`predmarket/observations.py`, scheduler kind `pm_scan`). A watch scan reads **only
        the venues a confirmed group needs**, prices every pairing inside every group, and
        appends self-hashed rows to `observations.jsonl`. **A non-reading is still a row** —
        "how often" is a ratio whose denominator is the attempts, so a scan that dropped the
        times it could not price a group would claim it was observable when it was not. A
        venue outage and a delisted market are recorded as different reasons. The scan
        confirms nothing: it holds no writer for the group store.
  - [x] **Market screening** — `predmarket/screening.py`. The listings were not broken, they
        were full of markets this pipeline cannot use (parlays, sub-horizon expiries,
        unquotable legs). Screened out **loudly**: every run prints how many each venue listed,
        how many survived, and the reason counts for the rest, because "empty because
        everything was a parlay" and "empty because nothing matched" are different findings.
  - [x] `discovery` cadence — done 2026-07-27 (#267, root fix #269). It runs on a schedule
        rather than on demand, because the question is not "what is pairable right now?" but
        "what became pairable while nobody was looking?" — a pairing that appeared and resolved
        between two hand-run `propose` commands leaves no trace it was ever missed. Each run
        appends a `proposals.jsonl` record (`predmarket/proposals.py`) counting only what no
        earlier run proposed, so an operator is not re-reading forty unchanged pairings every
        six hours and learning to skip the list the new one arrives in.
  - [x] **The exit report** — done 2026-07-27 (#265, `predmarket/report.py`, `pairs_cli
        report`). Frequency × net margin × **persistence**, and it says plainly whether the
        window it had is the exit artifact or a progress check. The load-bearing part is the
        three ways a duration can lie: a single sighting is not "zero seconds", an episode must
        not be stitched across an outage, and one still running has not ended. **The
        denominator is readings, not scans** — dividing by attempts would let an outage read as
        a quiet market.
  - [~] **Run it.** Started **2026-07-27T09:49Z** on this Docker host. Both `pm_scan` schedules are
        registered and firing, 66 groups are confirmed, and the report reads
        `INSUFFICIENT_WINDOW` at 2.82 of 14 days — which is the verdict working, not a problem.
        **This is still the whole of what PM1 owes, and it is now waiting rather than building.**
        Two things learned from running it that the plan did not anticipate:
        (a) **The candidate queue empties, and refills only on rotation.** Discovery samples a head
        plus a tail that rotates every 6 hours (00/06/12/18 UTC), so working the sheet to zero is
        normal and means "come back after the next flip", not "no pairings exist". A confirmation
        session is therefore ~4 short passes a day, not one long one.
        (b) **Automating those passes failed, and the failure was silent.** A scheduled task fired
        on time (`lastRunAt` advanced, `nextRunAt` moved on) and did no work at all — the sheet was
        never regenerated, nothing in the repo was touched. Both a cron entry and a one-off
        `fireAt` behaved this way. What makes it worth writing down is that **the scheduler's
        bookkeeping advances identically whether the run did anything or not**, so an unattended
        pass has no failure signal: it would read as "candidates are being confirmed" while nothing
        happened. The task is disabled and the passes are manual. Anything that automates this
        later needs an external check — the sheet's mtime is the cheap one.
  - [ ] LLM-assisted widening pass on a schedule + gap lineage (decision #2's second half;
        needs the deterministic matcher above, or "missed" has no meaning).
  - [ ] **The wording gates are blind to single-character distinctions.** Measured 2026-07-30 on
        "2026 Balance of Power: D Senate, R House" against "… D Senate, D House": `opposing_terms`
        and `subject_mismatch` both return `False`, and the pair scores **0.857**. The whole
        question is carried by the tokens `d` and `r`, six tokens of shared template outvote them,
        and `subject_mismatch`'s rarity test fails because both letters are *common* across a
        political corpus rather than rare — the exact inversion of what that gate looks for.
        #371 bounds the damage (one market gets one candidate per venue pair, so the impostors
        cannot displace the correct pairing) but does not close it: **when the right counterpart
        is absent from the sample, the best remaining pairing still wins its legs**, and only the
        operator reading both settlement texts stands between that and a confirmation. Adding
        `d`/`r` to the party sets is the obvious move and is *not* obviously safe — single letters
        appear innocently — so this is written down rather than guessed at.
  - [x] Fee-adjusted opportunity detector — done 2026-07-26 (`predmarket/fees.py`,
        `opportunity.py`). Both fee models verified against the venues' own docs, which
        **corrected the roadmap**: Polymarket charges a taker fee of the same
        `rate·P·(1−P)` shape by category (crypto 0.07 … tech 0.04, geopolitical exempt), not
        "gas + spread". Both peak at 50/50, so a mid-priced crypto pair costs ~3.5¢/contract
        across the two legs and a 2¢ gross gap is a **loss** — pinned by the first test.
        The edge reduces to `yes_bid_B − yes_ask_A` (holding YES on one venue and NO on the
        other pays $1 either way), so only the YES side of both books is needed. Both
        directions are judged and the better **net** one wins, since each leg's fee depends
        on its own price. Unpriceable legs and no-depth touches are recorded as
        non-readings, never as zeros.
  - [x] **The synthetic-source guard** — the flag `MockPredMarketCollector` sets honestly had
        **no consumer** anywhere in `predmarket/`, so the Safety-Flag gate defaulting to the
        mock was silent. A watch scan then filed its legs as `MARKET_NOT_LISTED` — "the market
        is gone", about a venue it never reached — and a group whose ids came from the mock was
        *priced*, putting a number derived from `(venue, index)` into the store the report's
        persistence figure is computed from. Both doors closed plus the report's incident tally
        (#271). The rule is crypto's `guards.BLOCK_SYNTHETIC_DATA_FOR_TRADING`, one package over.
  - [x] **A confirmed leg keeps the fee rate the by-id re-read cannot carry** — done 2026-07-27
        (#287). `confirm` captures the venue's stated rate from the listing; the scan fills it in
        where the live read has none. **Fallback, never override** — a rate the venue states now
        always wins, because `live_sizing`'s *venue filters are an input, never a memory* is the
        rule this bends and a rate captured weeks ago is exactly that memory. Found on the one
        group confirmed at the time: both legs quoted, `gross_edge` computed, `net_edge: null` on
        every row since confirmation. The seam was invisible because everything upstream looked
        healthy. `_quote_known`'s docstring claimed the leg was then "priced at the pessimistic
        default"; it was not — Binance has no schedule entry, so the leg priced at *no knowable
        cost*. Intent and implementation had drifted. See the open box above for what this does
        **not** fix.
- [ ] **PM2 — paper trading** (1–2 PRs): pessimistic fill model (taker + book depth + fees), virtual
      portfolio, **hold-to-resolution** (also measures cross-venue resolution mismatch).
  - [ ] ⚠️ Thomas sets **PM3 entry criteria as numbers** before PM2 ends.
- [ ] **PM3 — approval-gated live orders** ⚠️⚠️ — **triple-blocked**: PM0 done + PM2 criteria met +
      the live-execution governance packet (section C) implemented. Per-order R9 approval +
      single-use consumption behind `kalshi_trade` / `polymarket_trade` grants. Third consumption
      scope decision required.
- [ ] Resolve the roadmap's decision register — **3 of 7 remain open** and none blocks PM1.
      Decided 2026-07-26: #1 `pm_scan` template (cadence + market cap) and #2 LLM-assisted matching
      (deterministic-only is the default). Still open: **#3 PM3 entry criteria as numbers** (must
      precede PM2's end), **#4 trading-budget record shape** for prediction venues, and **#5 third
      consumption scope** (per-order spend) — all three are PM2/PM3 gates. #6 the Korean regulatory
      judgment is Thomas's, outside the repo; #7 PM4 bounded autonomy is not on the table.

**Out of scope (each its own future decision):** PM4 bounded autonomy, market making, directional/news
trading, leverage, any US-context Polymarket access.

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

      Both rows are here on purpose. PM1 is still scanning, so the denominator moves by the hour
      and any figure written down is stale before it is read — the **zero** is the finding, and
      it is the part that did not move.

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

## Per-machine setup that does NOT travel via git

A fresh machine has the code but not the local runtime state (gitignored, per CLAUDE.md). To actually
*run* the agent there, re-do the local activation once:

- Core activation pointer: `.runtime_governance_state/CURRENT_CORE_RELEASE.yaml`
- Safety-flag grants: `.runtime_governance_state/safety_flag_activations/*.json`
- Control state + ledger + schedules under `.runtime_governance_state/`

None of this is "planned work" — it is per-machine state you re-establish with the CLAUDE.md
"Core activation" steps + `scripts/activate_safety_flag.py`.

---

## How to use this file from another computer

```
git pull
```

Then open this file, or just ask Claude Code "남은 작업이 뭐야?" — it will read
`docs/REMAINING_WORK.md` and list the unchecked items above.

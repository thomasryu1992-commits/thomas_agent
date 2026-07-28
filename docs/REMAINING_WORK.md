# Remaining Work — canonical to-do list

**This is the single place to answer "what's left to build?" from any machine.**
It is committed to git on purpose: per-machine memory does not travel between computers,
so the durable hand-off lives here. On a fresh machine: `git pull`, then read this file.

Last updated: **2026-07-27** (second pass, `main` = `58bf3af`), after the predmarket wave
(#262/#264/#265/#267/#269), the canary-notional fix (#268), and the role activations
(#258/#272). Previously **2026-07-27** (section D) and **2026-07-26** (the review round and the
CLAUDE.md split).
Every claim below was re-checked against `main` and against the code it describes, not carried over.
That pass is why section A lost two boxes: it was ticking observation records as done in one line
and open in another, and the newer line was the stale one.

> **The one thing to read first:** an order path **exists**, and so does the **executing leg**
> that opens, protects and closes a live position. `financial_transaction_execution_implemented`
> is `true`. Live trading still cannot start, and the reasons are now entirely structural rather than
> "the code is missing": **no autonomous entry point may import the order path** (a test enforces it,
> and the readiness board reports it as a computed row), `financial_executor_enabled` is `false`,
> the clean-canary evidence threshold is not met **on any machine this file can speak for** (the
> count is per-machine state — ask the board, below), and every egress needs the operator's
> per-machine `live_trading` grant, order key, confirmation phrase and registered budget.
>
> **The canary count is per-machine and is currently unknown rather than zero.** Two real canaries
> went out on 2026-07-26; the daily counter did not record them, because the only door that can
> place an order never incremented it (#246). The registry that feeds `clean_canary_order_count` is
> a different store and may hold them, so the honest statement is: **re-read the board on the
> machine that placed them** (`python -m runtime.mvp_runtime.crypto.live_readiness`) rather than
> trusting a number written here. On a fresh checkout it reads `0/3`.
>
> **Cycle routing landed 2026-07-28** (`crypto/live_route.py`): the executing leg now has one
> autonomous caller, and `AUTONOMOUS_ROUTING_WIRED` is `True`. That was the last *build* item.
> What stands between here and an autonomous live order is now entirely operator state — the
> per-machine `live_trading` grant, the confirmation phrase, the registered budget, the canary
> evidence — each of which the readiness board reports as its own computed row. **Wired is not
> permitted**, and the board deliberately does not fold the routing row into `ready`.

Keep it current — when a milestone ships, tick its box or delete it here in the same PR.

Authoritative detail for each item lives in the linked roadmap docs; this file is the index.

Its counterpart is [`BUILD_HISTORY.md`](BUILD_HISTORY.md) — what has already been **delivered**, and
why each piece is shaped the way it is. The two together are the whole picture; `CLAUDE.md` states
the rules and deliberately claims no status, so that status has as few owners as possible.

---

## In-flight PRs

**One** (verified 2026-07-27, on the `main` this pass merged into — two of the three named when
it was written have already resolved, which is the section's own standing hazard):

- **#271** — predmarket's `is_synthetic` flag had no consumer, so a scan degraded to the mock filed
  its legs as `MARKET_NOT_LISTED` (the market is gone) instead of "we never looked", and a
  mock-derived confirmed group produced *priced* observations out of `(venue, index)`.

Resolved while this pass was open:

- **#239** — this file's own predmarket header contradicting its boxes. **Closed as superseded by
  this pass**, which fixes the same contradiction against a `main` that had moved well past the
  one #239 was written on.
- **#274** — **merged.** The readiness board grew a `market_data_visibility` row with #268 and the
  go-live runbook never mentioned `MVP_MARKET_DATA`, so the board failed on a precondition the
  checklist did not ask for. The runbook names it now.

A hand-maintained list of open PRs cannot stay true in a repository whose `main` advances several
times an hour — this section was accurate when written and wrong before it merged. Prefer
`gh pr list` over trusting it; the next pass should consider deleting it rather than refreshing it
again.

Two PRs were **closed unmerged**, and both are the same lesson:

- **#175** — a second LP4 order adapter written in a worktree without re-checking `main`, which by
  then already carried `live_execution.py` with its reviewed design record. Two adapters must not
  coexist: "which code can send an order" is exactly the question this repo keeps unambiguous.
- **#229** — the declared-notional check, written on a branch that fell **131 commits** behind while
  **#268** implemented the same check independently and merged first. Its code was redundant on
  arrival; only its documentation was still owed, and that is #274.

> **This section goes stale within hours.** Check `gh pr list` rather than trusting it — parallel
> machines open and merge PRs continuously, and on 2026-07-27 that produced two independent fixes
> for one defect (#229/#268) plus a review document whose P0 list was already merged. Before
> starting anything below, confirm nobody else has taken it.

---

## A. Prediction-market trading (Kalshi / Polymarket / Binance) — PM1 built, not yet run

Roadmap: [`docs/PREDICTION_MARKET_ROADMAP_V0.1.md`](PREDICTION_MARKET_ROADMAP_V0.1.md) (on `main`).
**PM1's code is essentially complete** — three venue adapters, screening, the deterministic matcher
with operator confirmation, the fee-adjusted detector, the observation store, both scheduler
cadences (watch and discovery), the proposal record, and the exit report all live under
`runtime/mvp_runtime/predmarket/`. **What is missing is the run**: nothing has been confirmed and
observed for long enough to answer the three questions the phase exists for. PM2 and PM3 are
untouched.

Trust the boxes below over this paragraph — a prose summary of a moving track is how the previous
version came to say "no code exists yet" above a list of shipped modules.
Phasing: observe (no money) → paper (no external effect) → approval-gated live (per-order approval).

- [ ] **PM0 — venue access** (operator-only, no code): Kalshi international signup (KYC), Polymarket
      Polygon/USDC wallet, and the **Korean regulatory judgment call** (grey area). Blocks PM3 only,
      not PM1/PM2.
- [~] **PM1 — observe-only pipeline** (no money; no account except Binance's key): **code complete
      2026-07-27, unrun.** Every box below is ticked except the operator steps and two deliberate
      deferrals. The remaining work is a confirmation session and a calendar, not a build.
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
        comparison can see — and one market belongs to at most one pair. Every judgement,
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
        inventing them.
  - [ ] ⚠️ **Decide whether Binance belongs in a multi-week observation at all** — a venue
        question, not a bug, and the reason it is written down is that the alternative is
        rediscovering it in three weeks with an empty report.
        Its **listing returns 40 markets regardless of `limit`, and rotates.** Two consequences,
        both measured on 2026-07-27:
        (a) `feeRateBps` rides on the listing and nowhere else — the watch scan re-reads a
        confirmed leg by id, and that response is a price alone — so a leg captures its rate at
        confirmation (#287) or never. **A market that has since left the listing cannot be
        repaired**: re-confirming captures nothing, and the group returns `net_edge: null`
        forever. One group was retired for exactly this after producing `readable 0/1` from
        confirmation onward.
        (b) The same rotation means a **confirmed Binance leg silently ages out of the corpus**
        the pipeline can describe, which is a broader problem than the fee: a two-to-four week
        window assumes the legs stay knowable for its duration.
        Kalshi and Polymarket are unaffected — their fees come from a verified schedule rather
        than the market row, and their listings are not the only source of anything the scan
        needs. So the observation window can proceed on those two while this is decided.
        **The options are venue-shaped, and Thomas's:** treat Binance as discovery-only (propose
        from it, never confirm it), confirm Binance legs only for short-dated events that resolve
        inside the window, or drop it from PM1 and revisit at PM2. Whichever, record it here —
        "we tried Binance and the report was empty" is not a finding about prediction markets.
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
  - [ ] **Run it.** The 2–4 week observation window, on the machine that will host it: confirm
        event groups (`pairs_cli confirm`, per event, each with its resolution-criteria note),
        register the `pm_scan watch` and `discovery` schedules, then leave it alone and read
        `pairs_cli report`. **This is the whole of what PM1 still owes** — calendar time and an
        operator session, not code.
  - [ ] LLM-assisted widening pass on a schedule + gap lineage (decision #2's second half;
        needs the deterministic matcher above, or "missed" has no meaning).
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
**This track is code-complete.** The only open line is M5b, which is an operator action, not a build.

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

**What is left in this section is no longer governance, and no longer plumbing — it is one build
decision and one operator action:** cycle routing (below), and three clean canary orders on the
machine that would run. Neither is blocked on a contract, a schema, or a gate — and clearing the
canary row **enables nothing on its own**: it satisfies one precondition of a step (cycle routing)
that is still deliberately unbuilt.

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
          first — with no `live_trading` grant the leg returns DISABLED having read no account
          and opened no socket, so a machine that has not been through the operator checklist
          behaves exactly as before. Live entries use the **same** `build_entry_plan` as paper
          and the same C4 verdict, so a live entry can never be permitted where a paper one was
          not.
    - [ ] **Live does not enforce the strategy's time exit, and the backtest evidence assumed it.**
          Found reviewing the routing PR; recorded rather than fixed because fixing it changes
          LP5.1's record shape, which is its own increment.
          `build_entry_plan` puts `max_holding_bars` in the plan, and its own comment says why:
          *"a strategy promoted on max_holding_bars=12 must not hold 48."* The **paper** leg
          enforces it. The **live** leg does not — `live_route` states plainly that there is no
          time-based exit, because a live position record carries no holding count and no
          timeframe, so a max-hold rule there would be inventing state rather than reading it.
          Consequence, stated so it is not rediscovered from a divergent R curve: a live trade
          ends only at its stop or its target, where a paper trade of the same strategy may also
          end on time. **The promotion evidence was built with the time exit in force, so it does
          not fully transfer to live**, and the direction is unfavourable — a time exit usually
          cuts losers, so live holds them longer. Do not compare live R against backtest
          expectancy without pricing this in (`r_basis` already keeps the two populations
          separately labelled).
          The fix is a live position record that carries entry bar/timeframe — LP5.1's shape, not
          routing's.
- [ ] **≥ 3 clean canary orders** before any autonomous run. **This file cannot tell you the count**
      and no longer pretends to: the evidence store is
      `.runtime_governance_state/live_canary_orders.jsonl` — per-machine and gitignored, like the
      Core pointer and the safety-flag grants — so a number written here is a claim about whichever
      machine last edited it. It said "0" until 2026-07-27, when Thomas reported canaries placed on
      his own machine; the fix is not a new number, it is to stop asserting one.
      Ask the machine: `python -m runtime.mvp_runtime.crypto.live_readiness`, the `canary_evidence`
      row. Two things that count differently from "how many did I place": only records with
      `clean: true` count, and a registry that fails **any** verification — line not JSON, self-hash
      mismatch, duplicate `canary_order_id` — counts as **zero** with a named reason rather than
      being partially trusted (`clean_canary_order_count`). A canary placed while the grant was not
      active also leaves no evidence at all: `DryRunCanaryRegistry` accepts and discards, because
      unbacked evidence here would unlock autonomous trading.
      (1 canary existed in the frozen source system and did not migrate.)
      **Re-verification is owed for anything placed before 2026-07-27, and here is why.** Three
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
open below is either an explicit Thomas decision (`business.analysis`, `execution.live_trader`) or a
state that is now *correct rather than missing* (the high-risk route, `complexity`), and each says
which it is. Do not read an open box here as work waiting to be done.

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
  - [ ] **The "High-risk Decision → Thomas Approval" route is still unreachable — and that is now
        a correct state, not a gap.** No action on the run path is priced ORANGE/RED, so no task
        classifies there. The approval-bearing actions that *are* ORANGE (memory promotion,
        candidate trial, program registration) reach Thomas through R9/R10 rather than through the
        router. This box stays open only as the place to re-check the day a run-path action is
        priced above YELLOW; there is nothing to build today.
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

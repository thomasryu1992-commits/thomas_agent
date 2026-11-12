# 2026-11-12 — the hypothesis-trial channel: no cap, a pace screen, a hypothesis form, and a prompt that knows what happens next

`runtime/mvp_runtime/crypto/factory.py` (`_screen_trials`, `expected_forward_trades`, `TRIAL_FREQUENCY_HORIZON_DAYS`),
`forward_trial.py` (`idle_deadline_days`, `trial_report`), `proposer.py` (`build_proposal_prompt` v2, `hypothesis_form`,
`clean_refusals`, `noting_screen_refusals`), `pipeline_worker.py`, `scheduler.py`, `scripts/hypothesis_trial.py`.

**What it is for.** The only path in the factory that starts from a hypothesis is the model proposer: the model
proposes a rule, deterministic code judges it, and one accepted proposal per cohort fire becomes a trial walked
forward beside a coin-flip twin. That path was shut from 2026-09-28 to 2026-11-12: four trials held the four slots,
three of them never traded, and a slot freed only when Thomas closed one by hand.

**The cap is gone, and what it stood for is asked directly.** Thomas (2026-11-12): do not cap the number of
strategies; name what the count stands in for. Here it stood for "a trial that can reach a verdict". So
`MAX_OPEN_TRIALS` is removed, and a proposal is refused at the mint (`too_slow`) when its own backtest pace would
not reach the forward judge's trade floor inside 180 days. One fire still screens three and mints one — those are
compute bounds, not counts of strategies.

**The idle deadline is a mark, not a close.** The decision said "give idle trials a deadline, and ask again about the
period and about who closes". With no cap an idle trial blocks nothing, so the PR marks it and leaves the close with
Thomas. A flat 30 days was wrong against the pace screen itself: a daily trial expecting ten trades in 180 days has
no trade in its first 30 days about one time in five. The deadline is the wait after which silence would be a
1-in-100 event at the trial's own pace, never under 30 days. "Never entered" reads the positions book, not only
settled rows — the first version keyed the book by the wrong field and would have marked a trial overdue while it
held its first position; the test written for that case caught it.

**The form is presence, not truth.** A proposal now carries six fields (payer, persistence, size, falsifier, nearest
record, prior) or is rejected as `form`. Nothing checks that the answers are good. What it changes is what the model
is asked to do first, and what a reader of a trial row can see. Acceptances recorded before the form stay queued.

**The prompt is told only what the generator side already knows.** What the trial screen refuses (no trade, the
floor and the horizon), which families are retired, and which families the screens refused in the last 30 days and
why. Refusal reasons are structural or trade-count; none is a function of a holdout or forward return. Closed
trials' reasons, per-timeframe gross returns and the research map are NOT passed — the first two would let the
search fit what the judge has yet to read, and the map is a document the runtime does not read. Refused family names
are model-written text re-entering a prompt, so the worker's door checks their shape and refuses anything else.

**Not new.** That an "acceptance" ignores results and that 120 bars say nothing was measured on 2026-09-24
(`HYPOTHESIS_TRIAL_V0.1` §1); that closed classes should not be re-proposed was already G5. What was read from code
this time is what the prompt contained. I told Thomas the earlier reviews had not covered the input side; that
overstated it, and §9.6 of the proposal says so.

**Timing.** F6, M1 and Q5 (d) all said "after the 2026-11-14 mint-share read". Thomas asked what differs. Nothing:
the read counts non-trial 1h/4h mints and leaves trial rows out, this PR does not touch those mints
(`test_the_rotation_is_what_it_was_without_the_trial`), and the read's result does not change the PR. The date was
my recommendation, stretched from "do not change the factory before the read" to a channel the read does not count.
The PR was built before the read; merging it before the read is asked as T1, and deploy waits for the window's last
fire on 2026-11-13.

**Not verified.** No real model has answered the v2 prompt; a truncated answer degrades the fire to zero proposals.
How many real proposals the pace screen refuses is unknown — the proposal ledger is not readable from here, and the
read door shows a proposer fire only as "fired".

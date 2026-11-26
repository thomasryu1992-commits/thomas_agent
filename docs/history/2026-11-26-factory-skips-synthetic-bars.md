# 2026-11-26 — a scheduled factory fire mines nothing on bars not known to be real

`runtime/mvp_runtime/scheduler.py` (the `KIND_FACTORY` branch of `_execute`),
`runtime/mvp_runtime/crypto/market_data.py` (docstring only), `tests/_helpers.py`, `tests/conftest.py`,
`tests/test_mvp_runtime_crypto_factory.py`, `tests/test_mvp_runtime_crypto_hypothesis_trial.py`,
`tests/test_mvp_runtime_crypto_positioning_store.py`, `tests/test_mvp_runtime_scheduler_factory_child.py`.

**What was open.** The collector selection hands a machine whose market-data opt-in is unset the mock collector.
Its bars are marked `is_synthetic`, and that mark stops trading (the data-health guard) and the candle archive.
Nothing else read it. The factory fire skipped on degraded data only, a candidate record carries no data-source
field, and no promotion or forward-cohort gate asks where the evidence came from. So a candidate mined on mock
bars would enter the store with a backtest computed on bars nobody traded, looking like any other. It could be
told apart afterwards only by comparing its `evidence_input_sha256` with the mock candles' hash.

`2026-11-26-mock-collector-untied.md` recorded this as a follow-up and widened it: the mock used to feed two of
the twelve structure families and now feeds all twelve.

Whether a factory fire ever ran on mock bars on the deployed host is not known. The maintenance lane, which owns
the factory kind, is handed `MVP_MARKET_DATA` in `docker-compose.yml`; the value is in the host's `.env`, which
this change did not read.

**Thomas, 2026-11-26: "예, 지어 줘"** — build the skip. What he was shown: three places skip on degraded data
(factory, proposer, null control) and only the factory stores candidates; the status string is free text, so a
new one is no schema change; a two-line skip in a throwaway tree broke 13 tests in four files, all of them firing
the factory through the scheduler on the default collector; the skip does not cover the proposer, the null
control or candidates already stored.

**The change.** In the factory fire, as soon as a leg's bars are collected:

- a leg that does not say `is_synthetic: False` is returned bare, before `attach_mining_legs`, so that leg's feeds
  are not read and its vendor rows are not kept. One collector serves every leg, so in practice a skipped fire
  reads no feed at all. A cohort that mixed real and synthetic legs would attach the real legs it reaches first
  (the mixed-cohort test does exactly that);
- the fire then returns `skipped_market_data_synthetic`, for the primary leg and for any cohort leg. One
  synthetic leg skips the whole fire, as one degraded leg already did.

The test is "anything but an explicit `False`": a snapshot with no flag at all skips. `collect_market_data` always
writes the flag, so this costs the real path nothing. It reads what the bars say, not which class served them.

The placement is earlier than the one the throwaway run measured (that run put the two lines after the legs were
attached). The set of tests reaching the skip was measured again on the built placement, by logging the test name
at the skip over the full suite: the same 13, and no test that reaches the skip and still passes.

A skipped fire is a closed occurrence: `fired`, not `spawned`, no spool file, no child, and the next day's fire is
claimed as usual.

**What a skip looks like to the operator.** The schedule's `last_status` reads `skipped_market_data_synthetic` and
the scheduler event carries it. It is not a failure, so no notification is sent (a degraded skip sends none
either). On a host where the opt-in is set this status never appears. If it does appear, the opt-in is unset or
unrecognised for the maintenance lane, and minting has stopped until it is set. The cycle's records and the
readiness board already show `SYNTHETIC` in that state.

**The tests.**

- The 13 that fire the factory through the scheduler are about what happens after the bars (the spawn and
  collect passes, a child that raises, the diagnostics file, fusion, trial rows, the positioning gate). They now
  ask for `venue_shaped_bars`: the mock's deterministic bars marked as a venue's are (`VenueShapedCollector` in
  `tests/_helpers.py`). The fixture replaces the selection function and never sets the opt-in variable, and it is
  requested by name, never autouse, so every other test keeps the default selection.
- New, on the default selection: a single-symbol and a cohort fire mine nothing, attach no leg, spawn no child,
  store no candidate, write no factory ledger row, and the next day's fire answers the same.
- New: a collector that is not the mock class but marks its bars synthetic is skipped; a cohort whose second leg
  is synthetic is skipped whole (its real first leg is attached, no child is spawned); bars with no flag are
  skipped. Each stores no candidate and writes no factory ledger row.
- Five deliberate breakages of the new lines each fail at least one of these: the early return removed, the
  primary check removed, the cohort check removed, a missing flag let through, the flag inverted.

**Not covered, on purpose.**

- The proposer fire and the null-control fire still run on whatever the collector returns. The proposer's rows
  are trial rows, which are not promotable and cannot parent a fusion; the null control stores no candidate.
- Candidates already in the store. The candle-hash comparison above is the audit for those; it was not run.
- Two operator scripts read collector bars and do not ask about the mark: `scripts/seed_forward_book.py` and
  `scripts/rescore_stale_holdout_candidates.py`. What they write on synthetic bars was not read here.
- `run_factory` itself is unchanged and still pure: it mines whatever frame it is given, which is what its own
  tests rely on. In the runtime the scheduler is its only caller. Two research replays call it directly
  (`scripts/research/forward_replay.py`, `forward_replay_heldout.py`), on stored lake bars that are marked real
  (`scripts/research/lake.py`); they do not pass through the scheduler and are untouched.

**Deploy.** Not deployed by this change. On deploy, the first factory fire should read `generated=…` as before.

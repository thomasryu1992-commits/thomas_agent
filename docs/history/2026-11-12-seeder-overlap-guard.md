# 2026-11-12 — the forward-book seeder refuses a walk that contradicts its own store

`scripts/seed_forward_book.py` (`record_seed`), `runtime/mvp_runtime/crypto/forward_book.py` (`seed_divergence`,
`assert_seed_agrees`, `FORWARD_SEED_DIVERGED`).

**What was found.** The pool overlap measurement (2026-11-12, https://github.com/thomasryu1992-commits/thomas_agent/pull/1458)
counted, from times and directions only, how often the pool's strategies are in one symbol together. It turned up
something it was not looking for: one DOGE 4h lineage held a seeded row open from 2026-08-24 to 08-29 with three
more seeded rows of its own inside that span. The book's unit is one virtual position per lineage per symbol and
timeframe, so those four rows cannot be one history.

**Why it happened.** The seeder ran twice. The first run (2026-08-30) wrote the three inner rows. The second
(2026-09-02) came hours after #822 lowered the liquidation guard's assumed leverage from 20 to 5, which widened the
room for a stop from 4.6% of entry to 19.6%. The long row's stop sits 5.72% from entry: refused by the first walk,
admitted by the second. The second walk took that entry, held it for 35 bars and never had room for the three later
ones. A row's id is seeded by its strategy, entry price and open time (`trade_plan.open_position`), so the rows both
walks agreed on were skipped as duplicates and the one new row was appended beside the three it replaces. No row before it in the whole book has a stop wider than
4.6%. The forward judge counts every closed row opened after selection and does not look at overlap, so that lineage
is counted on 27 rows where either past alone gives 24 or 26.

**What the docstrings promised and what was true.** "A re-run mints identical settlement ids" holds while the two
runs see the same entry doors, exit rules and data. Nothing checked that, and nothing said when it failed. The
docstrings now state the condition.

**The guard.** Before a walk's rows are written, the seeder asks whether any NEW row (an id the store lacks) is open
while a STORED row of the same lineage, symbol and timeframe is open. If so the whole walk is refused for that
context: no row appended, no counter merged into the book, `FORWARD_SEED_DIVERGED` named with the open times on both
sides. The other contexts of the run still seed, and the run ends `EXIT_BLOCKED` — dry runs too, so `--list` answers
"would this apply cleanly". Any other per-context error ends the run as it always did.

**Three choices in the comparison.**
- *Strict at both ends.* A position that opens on the bar another closed on is the kernel's own order (settle, then
  enter). The live book has such a pair at 08-25 23:59:59.
- *Only stored rows opened at or after the walk's start.* The seed start moved to the selecting row on 2026-09-23,
  and the store still holds 31 rows from the earlier, longer walks, one of them open across its lineage's
  selection. The judge does not count those rows, so they are not this guard's evidence either. This does not
  promise a clean first run: a walk that starts without a position the earlier walk was holding can enter sooner
  and clash with that lineage's stored rows from after the start, and that is a real second past, refused like any
  other. Seven lineages hold such rows. Run `--list` first.
- *Stored rows are never compared with each other.* The DOGE lineage already holds both pasts. Today's walk mints
  only ids the store has, so it finds nothing new and exits clean. The guard prevents the next case; it does not
  re-litigate this one on every run.

**Where the check sits.** In the seeder, not in `append_sealed_rows`. The live cycle and the forward cohort append
through that function and are untouched; a test says so.

**What it does not do.**
- It does not change what the forward judge counts. Tightening that is a judgement-rule question.
- It does not touch the four existing rows. The store is sealed and append-only; what to do with them is Thomas's
  decision, and so is what to do with a context the guard refuses. The tool offers no override.
- It does not see divergence without an impossible position: a walk that adds a row in a gap the stored rows leave,
  or one that no longer reproduces a stored row. Both pass.
- It was not run against the live store. Claude does not run the seeder.

**Tests.** `tests/test_mvp_runtime_crypto_forward_book.py`: the real mechanism (two `walk_seed_span` calls with the
liquidation door refusing one entry in the first) through `record_seed`, dry and applied; the pure comparison's
edges; the start-of-walk rule; the live append left alone; the same case
through the real `seed_lineage` with only the fetch replaced; and `main` going on past a refusal to the other
contexts and ending blocked. Twenty-two mutations of the rule and of the seeder's handling were each caught.

**Decided after the merge (Thomas 2026-11-12, "기본값대로").** Both open questions took their defaults. The four
existing rows stay as they are, on record only: the store is sealed and append-only, and what the judge counts is
not changed, so that lineage is still counted at 27 rows where one past gives 24 or 26. A context the guard refuses
stays refused, with no override added: it gets no further seeded rows, its live rows accrue as before, and since the
seeder is a hand-run tool that re-walks every context, each run names it again and ends ``EXIT_BLOCKED`` until the
walk and the store agree. Deploying the guard is not part of this decision.

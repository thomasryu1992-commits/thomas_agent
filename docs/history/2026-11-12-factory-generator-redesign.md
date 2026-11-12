# 2026-11-12 — the generator redesign proposal: what "keep the validation" leaves room for

`docs/proposals/FACTORY_GENERATOR_REDESIGN_V0.1.md` (DRAFT). No factory change.

**It is a proposal that was already owed.** The 2026-11-04 decision said: rebuild the generator, keep the
validation, measure a market-neutral cross-section first, and on a LEAD raise "what goes into the factory: `xs_*`
features over a wider universe, or basket templates". The 30-day momentum basket came out LEAD and that second
proposal was never written. The system review listed it as option E; this is it.

**Written two days early, on Claude's reading.** The review's R5 put it after the 2026-11-14 mint reading. Thomas
remarked that the reading will come out as expected, since the same code has been running on schedule. Writing a
proposal changes nothing in the factory, so it was moved up, and the document says that this was Claude's reading
of the remark rather than an instruction.

**The scope decides more than the ideas do.** The only shape that showed size is a basket held across many coins.
The code was read for what the kept validation consumes: per-trade R over an ATR stop, and a twin that keeps the
exits and flips only the entry. A basket has neither, the position book has one slot per symbol and timeframe, and
the promotable candidate kinds sit inside the judgement fingerprint. So "the factory mints baskets" is not a
generator change. It is a new judge, a new twin and a new authority on entry, behind an order Thomas already fixed
(a stage-2 pass first). The proposal says so and does not ask to change that order.

**What is left inside the scope.** Families and the feature vocabulary are outside the fingerprint, so the search
space can move by reviewed code. Two moves looked worth a separate PR: a liquidation family pair (the features exist
and no template reads them; five symbols, daily, from 2024-01, with a recorded reason not to add templates that this
reopens), and requiring the hypothesis form of model-proposed trials. Each direction is tabled against the
fingerprint, new authority, the record it reopens and when it could get a verdict, and the yardstick is two
measures that already exist, fixed before the directions were listed.

**The pre-checks changed the answer, which is what they were for.** F3 said the liquidation pair would be filed
only if a depth and resolution check passed. It did not. The series reaches the 1h and 4h replay but not 1d's
2,000 days; since the 2026-10-21 look-ahead fix a bar reads the previous day's total, which cannot express "absorb
a liquidation as it happens"; the same vendor's daily open interest lost all thirteen confirmations under the same
fix; and a liquidation trial had already been minted and contradicted in September. So the pair is withdrawn and
the 10% prior is recorded as too high.

**Looking at the trial channel before changing its prompt found the real fault.** G5 was to require the hypothesis
form of model-proposed trials. The research export shows six trials minted in four days in late September, two
closed, and the remaining four sitting at the cap ever since: three have made no forward trade in six weeks and one
has made four. A slot frees only when Thomas closes a trial, and no rule ever asks him to. The factory's one
hypothesis-first generation path has therefore been idle since 2026-09-28. Fixing that (retire the idle three, then
a mint-time frequency screen and an idle-trial deadline) is a generator change outside the judgement fingerprint,
and it matters more than what the proposals say.


**Thomas's reply reframed the fix.** Asked to approve an idle-trial deadline under a cap of four, he said the cap
itself is the wrong tool: carry as many strategies as possible and work out what many strategies bring. A second
document (`MANY_STRATEGIES_V0.1.md`) therefore inventories every count limit in the lane and sorts them by what
they actually limit. "How many" turned out to be three different questions. How many are made and observed: the
cohort already has no cap, and the trial cap can go. How many may enter per context: one, and the live order plan is
built from that same choice, so it cannot be lifted before a layer that nets signals on one symbol. How many live
positions: set by the venue's one-way netting and the registered budget, and left alone.

**The recorded cost of count goes first.** The pool once held 67 strategies over 20 contexts and none could be
demoted; a full pool once silenced a signal that was on 81% of the time; the LIVE bar rises with pool occupancy.
Lifting caps without the three things caps stood in for (family-level judgement, a risk unit, rules instead of
per-item decisions) would repeat those.

**Closing the idle trials touched the 11-14 reading.** The reading registers eight mints per fire over 10-24 to
11-13, and no trial could mint in that window until the slots were freed on 11-12. A ninth row on the last two days
is a trial, not a fault; the proposal says how to count it. Claude had recommended the closes without checking this
first.

**The overlap was measured before anything was built on it.** The netting design does work only when strategies
overlap on a symbol, so the first step was a count: the pool's forward book, times and directions only, every other
field dropped at parse (`scripts/research/pool_overlap.py`). The cohort was left out, since it sits under a judgement
read once on 2027-03-22. Of 329 closed virtual positions, 171 opened while another strategy was already in on that
symbol, and 106 of those were the same direction: netting would stack more often than it offsets, so the risk budget
matters before the fee saving does. Overlapped time mixes timeframes on four of five symbols.

**Three definitions were fixed before the first count, and one claim was wrong.** The proposal had not said what a
close time is, what "already open" means for entries stamped in one cycle, or what happens when one strategy's rows
touch. They were written into the record before the run. The proposal had also said a strategy cannot hold two
positions on one symbol; one seeded DOGE 4h strategy has a row spanning three of its own. The tool counts that once
and reports it.

**Those rows are two seeder runs that walked different pasts.** The three inner rows came with the first seed on
2026-08-30. The long one came alone on 2026-09-02, hours after #822 lowered the liquidation guard's assumed leverage
from 20 to 5: its stop sits 5.72% from entry, refused at 20x and admitted at 5x. The second walk took that entry and
held it, so the three later entries had no room; a row's id is seeded by its strategy, entry price and open time, so only the new row was appended and both
pasts stayed. The seeder is idempotent only while the doors, exits and data are the same between runs, and nothing
reports when they are not. The forward judge counts every closed row after selection and does not look at overlap.
One lineage, four rows; no fix was made here. The guard that refuses the next such walk is its own PR (#1459).

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

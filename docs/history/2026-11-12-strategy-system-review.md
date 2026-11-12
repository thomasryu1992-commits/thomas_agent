# 2026-11-12 — a review of the strategy system itself, after a day of looking for the next signal

`docs/proposals/STRATEGY_SYSTEM_REVIEW_V0.1.md` (DRAFT). No code, rule or schedule changes.

**How it came about.** Asked to "keep investigating strategy formulation", Claude surveyed new data sources and
proposed a probe. Thomas answered that the question was a different one: how to build a good strategy system. He
was right. Twenty-two basket attempts and a data-source survey had all asked "what to measure next" and none had
asked whether the way hypotheses are produced and the way evidence is gathered could yield a strategy at all.

**The first diagnosis was overstated, and the doc says so.** Six points were given in conversation before the
factory's own record was read. Four did not survive it as worded: payer-clear hypotheses have failed too, the frame
was varied across tracks, pooled judgement is already decided on the factory side, and the factory measured its
costs and keeps a research record. The review carries a section listing those corrections, because Thomas had
already read the first version as findings.

**What the review stands on.** The 2026-11-04 decision (rebuild the generator, keep the validation apparatus, do
not shrink the lane) is taken as given. The validation is strong on both tracks; the weak places are what reaches
it and how evidence accumulates. The pipeline's power was computed for the first time: a strategy whose true
annual Sharpe is 1.0 clears the entry rule, the screen and stage 2 about one time in five, while a zero-skill one
does so about one time in a thousand. And forward evidence, the scarcest resource, accrues only to the four
candidates that cleared the past.

**Why every option defaults to "write it up as its own proposal".** Thomas often answers with the defaults, so a
default that changed a rule would be a rule change by habit. The options that touch decided research-side rules say
which rule they reopen; canary, limit entry, lane shrink and loosening before an epoch boundary are listed as not
raised.

**The diagnosis is not new, and the review now says so.** Checking the review's factory figures against their owner
documents turned up two older records that reach the same conclusion: the 2026-07-30 research record ("the families
mint recombinations of one price series") and the 2026-10-01 scorecard ("the filters score nine, what they filter
scores two to three"). The prescriptions of those records were carried out (more information sources: gross near
zero three times; a true cross-sectional book: the research track, two candidates with size). The review therefore
claims only what it adds: the research track's score, the power of the whole pipeline, and options for what comes
after those two prescriptions.

**The forward panel is narrower than the option that was approved.** The review described a ledger of every
registered definition, written daily, and said a missed day never comes back. Writing the proposal showed three
errors. Nothing needs to run daily, because every definition is a function of archive files; a record visible daily
is peeking. Strategies with an official read ahead, and their relatives (M04's two inputs, the trend book beside the
momentum basket), cannot be computed early without seeing that read. And a judgement rule written once the record
exists is post hoc, so the panel has no judgement at all: sixteen closed definitions, as registered, one read after
twelve months, three readings fixed now.

**The map is an index.** `TRADING_ALPHA_RESEARCH_RECORD.md` and each proposal already own their figures. The map
lists what was measured, on what universe, with a one-phrase result and a pointer, so that a new hypothesis is
checked against it before it is written.

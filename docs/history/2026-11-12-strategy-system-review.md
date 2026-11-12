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

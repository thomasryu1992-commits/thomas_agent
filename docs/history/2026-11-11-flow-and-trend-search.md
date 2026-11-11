# 2026-11-11 — two more searches in the only class large enough for the size floor

`docs/proposals/FLOW_AND_TREND_SEARCH_V0.1.md`, `scripts/research/flow_basket_search.py`,
`scripts/research/trend_book_search.py`, `basket_search.clears_entry` / `check_development_span`.

**Why these and not others.** Thomas set the stage-2 size floor at 15% a year on total position and asked to keep
looking. From the repo's own records only the alt cross-section baskets ever showed that magnitude; the carry
measurements earn about one percent. So the entry rule now carries the floor (Q2), and the next attempts stay in
the basket class.

**Two things said wrongly when this was proposed.** The flow columns were called unused signals: they sit in the
metrics search's own feature list, and that record closed with its fifth attempt unspent, so running them reopens
it. The trend book was called never measured as a portfolio: the regime sets ran trend rules on this universe and
confirmed none. Both corrections are the first section of the proposal.

**Why the flow search excludes the divergence.** It is the top traders' share minus the all-account share, and the
all-account share is one of M04's two features. A pass built on it would be M04 on a second screen.

**Why the trend book is judged as an excess.** Long-what-rises was long through 2020–2021, so its Sharpe on
2020–2024 is partly the market. Trend minus always-long at the same sizing is the short book on falling members:
what trend-following adds over holding. The directions and the two lookbacks were fixed before any run.

**Why development stops at 2024-12-31 in code.** The confirm span was read once by M04 and its breakdown is known;
the record said later candidates are checked forward only. `check_development_span` makes both tools refuse a span
that reaches it. Using it as development data is asked as a question, not taken.

**Result.** Nothing cleared the entry rule. Flow baskets: F01 −0.769, F02 0.782, F03 0.575. Trend excess: T01
0.056, T02 −0.365. The full trend book at 30 days shows a Sharpe of 1.361 beside an always-long 0.677, and at 90
days 0.398 beside 0.666: one lookback, one bear year. It was a shown-only number under a rule fixed before the run,
and it is recorded as that. Both searches are closed and no forward slot is asked for.

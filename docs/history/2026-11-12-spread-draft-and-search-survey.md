# 2026-11-12 — a spread observation draft and a survey of what is left to search

`docs/proposals/SPREAD_OBSERVATION_V0.1.md`, `docs/proposals/SEARCH_CANDIDATE_SURVEY_V0.1.md`. Both DRAFT; nothing is
built, fetched or measured.

**Why a draft for costs rather than another search.** After the depth search closed, the map of untried signals
was used up. What separates the remaining options is time, not ideas: no clean confirm span exists before 2028-07,
and the first screen verdict is early 2027-04. Spread is the one thing that cannot be recovered later. The venue
keeps no history of its book, the public archive's `bookTicker` files stop on 2024-03-30, and the stressed coins
that carried M04's confirm return were listed after that. So the draft asks to start writing down the best
bid and ask of every symbol now, on the research side, as one more endpoint of the sandbox's public cache.

**The runtime already records spread — for six majors.** `orderbook_store` has kept `spread_bps` every 15 minutes
since 2026-08-15. The research export shows medians of 0.01–1.2 bps there, far inside the engine's 3 bps. That says
nothing about the coins in question, and widening the runtime store would touch the cohort machinery before the
11-14 reading. Hence a research-side sampler, not a runtime change.

**The survey started from the closed records, not from ideas.** A previous round called two things new that were
not (the flow columns, trend rules). This time an inventory of every measured or closed signal across the proposals,
the research scripts and the factory's template list was built first, and each candidate's data was checked on disk.
Two survive: spot share of volume and average trade size. Liquidations do not (five symbols, from 2024), coin age
does not (it is a hypothesis shaped by M04's diagnostic), and other venues were not checked, so they are listed as
unknown instead of as options.

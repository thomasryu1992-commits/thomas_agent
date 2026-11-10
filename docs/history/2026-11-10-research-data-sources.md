# 2026-11-10 — Which new data source to open for screen candidates: a decision packet

**What.** `docs/proposals/RESEARCH_DATA_SOURCES_V0.1.md`, a DRAFT decision packet. Thomas asked for a review of new
data sources ("둘다해봐") after two scouts found no further screen candidate on data the sandbox already holds.
It compares Deribit option data, token-unlock calendars and BNB holder-reward history, and recommends opening
only Deribit's public market data, to the research sandbox, in three stops (check coverage, IV–RV observation
under D-A6, a separate pre-registration).

**Why the sandbox and not the runtime gate.** The sandbox's public collection (`RESEARCH_SANDBOX_V0.1.md` §4.2) is
how every research read works today: no key, the `claude` account, a rate budget. The runtime never reads this
source. The 2026-10-06 IV–RV record said a new egress is named at the env-only gate; the packet reads that as the
runtime case and asks Thomas (Q1) instead of deciding it.

**Why unlocks and BNB rewards are not recommended.** An unlock calendar fetched today is the revised schedule, not
the one known at the time, so a historical test would look ahead. BNB rewards have no dataset and their
execution is an account write.

**Decided the same day.** Thomas took the defaults. Stage 1 then ran as about 12 keyless requests (03:03–03:10 UTC, counts
and dates only): expired BTC option trades are public on `history.deribit.com` back to at least 2019-06 (with
`iv`, `mark_price`, `index_price` per trade), and DVOL daily starts 2021-03-24 for BTC and ETH. Nothing was cached.
DefiLlama and Tokenomist were never called. No code changed.

# 2026-11-10 — IV–RV observation, stage 2: DVOL collected keyless, observed forward only

**What.** Stage 2 of `docs/proposals/RESEARCH_DATA_SOURCES_V0.1.md` (D-A6, answered 2026-11-10). Added:
- `scripts/research/fetch_deribit.py` — host-side, keyless DVOL daily bars for BTC and ETH into
  `~/research/cache/deribit/dvol/`, paced one request a second, no retry, closed days only, never in 08–09 UTC.
- `scripts/research/iv_rv_observe.py` — sandbox-only report: DVOL close against the Binance index's realized
  volatility over the next 30 days.
- `tests/test_research_iv_rv.py`.
- `RESEARCH_SANDBOX_V0.1.md` §4.2 names the source; `CLAUDE.md`'s IV–RV sentence now says where it is read.

**Why forward only.** Implied minus realized volatility over past days is the gross return of a short-variance
position. The packet's stage 3 says no return is computed before a strategy is pre-registered, so the report
values only days from 2026-11-10 on and shows earlier days as dates and counts. A test holds that line.

**Why not the env-only gate.** Thomas's Q1 put this read in the sandbox's keyless collection; the runtime never
calls Deribit. The 2026-10-06 record expected an env-gated read because it assumed the runtime would collect.
`safety_gate` and its call-site enumeration are unchanged.

**First run.** 6 requests wrote 2,057 days per currency (2021-03-24 – 2026-11-09). The observation printed zero
observed days: the first value needs 30 forward closes, so it appears after 2026-12-10.

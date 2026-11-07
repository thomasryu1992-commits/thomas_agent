# The cross-section basket measurement is built: an archive symbol list with the delisted perps, and a sandbox-only reader

- **Decided:** `docs/proposals/MARKET_NEUTRAL_CROSS_SECTION_V0.1.md` (Thomas 2026-11-04). Q1 is the monthly top 10
  by volume plus the symbols Thomas names. Q2 fixes three signals. Q3 is 1d only. Q4 has the archive fetched as
  `claude`.
- **Built:**
  - `scripts/binance_archive.py list-symbols` and `fetch-monthly`. The symbol list comes from the archive's own
    bucket listing, because `exchangeInfo` drops some delisted perps (SRMUSDT, LUNAUSDT) and leaving them out
    would bring survivorship back.
  - `scripts/research/cross_section_basket.py`. `universe` prints the point-in-time universes. `run` measures, in
    the research sandbox only.
- **Details fixed in code before any data was read:**
  - The last held day is 2026-10-30, because the October file ends at 10-31's bar.
  - A universe under four members has no basket.
  - Weekly p values use the normal approximation, as H2 does.
  - The proposal's "지은 것" section lists the rest.
- **Tests:** 32 new tests and 4 archive tests. Ten deliberate bugs were each caught. The funding-sign bug first
  survived, because the test's long and short legs cancelled; the test was fixed.
- **Not done here:** fetching the data and the official run. The run has to use code on `origin/main`, so it waits
  for this merge.

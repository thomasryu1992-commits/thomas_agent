# 2026-11-04 — Cross-section universe: coin perps only, amended before the official run

**What.** `MARKET_NEUTRAL_CROSS_SECTION_V0.1` §4 now reads "USDT perp" as a coin perp: `contractType: PERPETUAL`
in Binance's `exchangeInfo`. `scripts/binance_archive.py contract-types` snapshots every USDT symbol's
`contractType` to `<archive>/contract_types.json`. `scripts/research/cross_section_basket.py` leaves out
anything else and stops BLOCKED when the snapshot is missing.

**Why.** The first universe drawn from the fetched archive had gold, silver, crude oil and single-stock perps
(`TRADIFI_PERPETUAL`, listed since 2025-12-11) in the monthly top 10 from 2026-04 on. That was up to 5 of the
10 slots in 2026-08, across 8 symbols over 7 months. The measurement exists to steer the **coin** generator,
and 2026 is one of the seven years the LEAD rule counts. The bars themselves were sound: daily, with no
zero-volume days. So the cut is about what is measured, not a data defect. It was made before the one
official run, so it is a pre-registration amendment and not a second look.

**The absent-symbol rule.** `exchangeInfo` lists only what exists today. A symbol the snapshot lacks was
delisted before 2026-11-04 and is kept as a coin. 31 of the archive's 903 are absent. Four of them traded
after 2025-12-11 (`BDXNUSDT`, `SXPUSDT`, `AERGOUSDT`, `BTCSTUSDT`), and all four are coin tokens, checked by name.

**Second public host.** `fapi.binance.com/fapi/v1/exchangeInfo` is read by the operator script, keyless, and
the proposal already cited it (§3). It is not a runtime egress: the runtime reads only the file.

**Effect.** Members ever went from 96 to 92. Out: the 8 TradFi symbols. In: `AKEUSDT`, `STOUSDT`, `TAOUSDT`,
`TUTUSDT`. Only 2026-04 through 2026-10 changed. Universe hash `d508c21381bc431d` became `3265bbf26982294d`.

**Tests.**
- A TradFi perp is left out however large its volume, and naming it does not let it in.
- A symbol the snapshot lacks is kept.
- Both commands stop BLOCKED on a missing, unparsable or empty snapshot.
- `contract_types` keeps only USDT symbols with a contract type, and refuses an empty answer.
- Three mutants are caught: no coin check, absent symbols dropped, and no BLOCK.

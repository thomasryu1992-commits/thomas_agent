# D-C step 1: US Treasury share and KRW/USD/JPY sensitivity, measured against a pre-registration

- **What:** `TOTAL_ASSET_ALLOCATION_FX_MEASUREMENT_V0.1.md` (RECORD). Five bond-sleeve variants of the current
  targets on public monthly data, 2005-01 to 2026-09 and the pre-2023 subsample:
  - US Treasury (IEF) share of the bond 37: 0, a quarter, a half;
  - unhedged or hedged.
  Approved by Thomas 2026-10-20 as the default plan.
- **Why it is shaped this way:** the 2026-10-16 decision asked that values and reading criteria be written before
  measuring. The pre-registration was committed and pushed (`c7bf2c4b`) before any data was fetched. Weights are
  not optimised (validation §1 rule). Return is reported but is not a criterion.
- **Data surprises:**
  - Five monthly closes of `KRW=X` on Yahoo are wrong-unit values (~0.11). Two cleaning rules were fixed and both
    passed the reproduction check before any variant result was read. The verdicts are the same under both rules.
  - FRED CSV times out from this host, so the rates come from OECD SDMX, the source behind the FRED series. US
    3-month is OECD `IR3TIB`, not `TB3MS`, and the two were not compared.
- **Result:** no variant is a structural improvement; the unhedged half is a structural deterioration. Every variant
  deepens the 2022 drawdown. Unhedged Treasuries lift the GFC window sharply, which the pre-registration keeps out of
  the verdict. KRW/JPY sensitivity is indistinguishable from KRW/USD.
- **Not done:** no target constant changed (`holdings/allocation.py`); only one maturity proxy; the script and raw data
  stay outside the repository, like the validation record's.

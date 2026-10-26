# The rebalancing calculator: `holdings_board --rebalance` (PR-A of RB1–RB6)

- **Why:** the board showed each class's weight, target, band and drift, but turning "one class outside its
  band returns every class to target" (TOTAL_ASSET_ALLOCATION §6) into orders in the §8.5 tax order was
  Thomas's arithmetic. Thomas decided RB1–RB6 on 2026-10-26 (`REBALANCING_PROPOSAL_V0.1.md`).
- **What it is:** `holdings/rebalance.py` is pure. It reads no file, socket or clock. Given the last fire's
  class totals and positions, Thomas's approved candidates and the year's overseas gain when known, it returns
  a plan:
  - Sells come first. Each over-target class gives up its excess in tax order: tax-free (2), then overseas
    inside the deduction (3), then taxed (4). Within a step, the smallest gain ratio goes first.
  - Buys go into the approved candidates only. Each class is water-filled, smallest holding first. A class
    with no approved candidates is listed as "not bought", never guessed.
  - Domestic orders are whole shares; overseas orders are amounts.
  - Orders under 50,000 KRW are carried (RB4'). The carry is not stored: the next check finds it again in
    the drift.
  - Coin gets one manual line per side (it is held outside Toss).
  - The engine margin is left out of the NAV and the bands (RB5).
  - Tax is an estimate. An unknown overseas gain is taxed in full and said so; PR-B fills it in.
- **Where its input lives:** each classified Toss holding of a fire goes to its own local file,
  `holdings_positions.json` (`store.positions_path`). The board and the snapshot stay free of any symbol, which
  the H3 tests pin. `--rebalance` refuses when:
  - the positions file comes from another fire (`as_of` differs),
  - the allocation is partial,
  - or the fire is older than the stale limit.
- **Candidates:** `--approve-candidates CLASS=ID[,ID...]` writes `candidates` into the classification file,
  beside the entries and versioned with them.
  - It is allowed for global_equity, domestic_equity, bonds and gold only.
  - Each class takes at most four distinct `toss:` ids. An empty list withdraws the class.
  - Writing entries keeps the candidates, and writing candidates keeps the entries.
- **Boundary:** both new modes are terminal-only. They are in CLAUDE.md's list, in the hook's mode regex and
  in its `TERMINAL_ONLY` test table. The hook's file regex now also covers `holdings_positions.json`. The plan
  is printed only: it is never saved, never sent to a door or channel, and never put in a model prompt.
- **Not built yet:** PR-B (the overseas YTD realised gain from Toss order history), PR-C (the candidate
  screening report), PR-D (the monthly band notice), and per-instrument tax-kind overrides (`plan` takes
  them; nothing writes them yet).
- **Checked:**
  - Planner cases run on synthetic portfolios, and the wiring runs over a temporary state directory.
  - Seven mutations were each caught: tax order ignored, sell whole shares ignored, buy whole shares ignored,
    a plan on a partial NAV, the 50,000 KRW carry ignored, buys outside the approved list, and the engine
    counted in the bands.
  - Cash has no band: cash alone off target is not due (pinned by a test).
  - The first whole-share test was conditional on the flag it should have pinned. An uneven-price test
    replaced it.

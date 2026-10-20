# D-C step 3: won/dollar exposure on the local board only, with an optional currency per classification entry

- **What:**
  - `holdings/allocation.py` adds a `currency_exposure` sub-block (won and dollar amounts, weights when
    complete).
  - `holdings/classification.py` lets an entry name `currency` (`krw`/`usd`); the terminal `--classify` takes
    `ID=CLASS:CURRENCY`.
  - `disclosure.LOCAL_ONLY_ALLOCATION_KEYS` gains `currency_exposure`.
  - `board.render_allocation` prints a `currency` line when the block carries it.
- **Why local only:** H3 lets exactly one breakdown leave the process. A won/dollar split of the same money is
  a second one. The dollar total minus the published dollar classes yields the stablecoin figure H3 keeps
  local, and a single Treasury fund's amount could follow. Thomas first chose to export it, then chose
  local only once the conflict was shown (2026-10-20).
- **How it counts:**
  - by exposure, not listing currency, so a won-listed Treasury ETF is dollars;
  - each class has a default (`CURRENCY_DEFAULT`), and bonds have none, so an untagged bond makes the
    line partial rather than guessed;
  - Binance assets, Toss dollar cash and the futures margin are dollars;
  - there is no yen line: the D-C measurement (#1262) found yen indistinguishable from the dollar, and
    nothing yen-denominated is held.
- **Checked:** new synthetic tests cover the classification field, the CLI, the exposure rules, the partial
  cases and the boundary (local file has it, doors' file does not). Six planted faults were each caught.
  The full suite has 3 failures; two pipeline tests fail on `main` too, and the third was the diagnostic index,
  regenerated here.
- **Not seen:** the real board. Claude does not read the local file; Thomas sees the line on `--local` (the stored local file), not on the live `--full` read.

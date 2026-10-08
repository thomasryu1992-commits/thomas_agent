# H3-min: the doors' holdings file carries one table, and no single instrument's amount

- **What this PR changes:**
  - `holdings/disclosure.py` (new):
    - The single-holding rule: `withheld`, `whole_is_one_instrument`.
    - `external_view`, which turns a full fire into the stored snapshot.
  - `holdings/store.py`:
    - Each fire writes two files. `holdings_snapshot.json` is what the doors read: one table under the
      rule. `holdings_local.json` is the whole fire, for the terminal only.
    - The drawdown alert, a Telegram message, is built from the external block.
  - `holdings/allocation.py`: `allocate_with_parts` also returns each row's makeup (instruments and
    cash balances), kept in-process.
  - `holdings/binance_wallet.py`: the snapshot carries `class_assets` in-process. Asset names are
    never stored.
  - `holdings/board.py` draws only the keys a view has, and labels withheld rows.
  - `scripts/holdings_board.py --local` renders the local file. Claude does not run it.
  - Tests: `tests/test_mvp_runtime_holdings_disclosure.py`, the property over decided and 3,000 random
    shapes. The store and wallet tests now read breakdowns from the local file.
- **Why:** with the wallet on, the stored snapshot would have carried the BTC and ETH sub-class
  amounts. The doors read that snapshot, so it would have reached Telegram and a model provider. Thomas
  set the invariant: "No externally visible aggregate may reveal a single instrument's amount by
  construction." He chose to hide single-instrument classes, and to send one table only.
- **Shaped this way because:**
  - **One table.** Two breakdowns of the same money subtract into a single instrument. For example,
    the cash class minus the Toss cash gives the stablecoins; spot plus Earn minus the stablecoins gives
    the coins. A per-table rule cannot stop that, and removing every other breakdown does.
  - **Secondary suppression.** A hidden row equals the total minus the published rows, so the hidden
    set grows until it covers two holdings. When the whole table is one instrument, the total goes too.
  - **Makeup, not counts.** The same asset in spot and in Earn is one instrument. A value whose makeup
    is unknown counts as one instrument, so it is withheld rather than assumed safe.
- **What it deliberately does not do:**
  - No crypto per-asset terminal view. It needs the wallet key in that process, so it waits for wallet
    activation.
  - No change to the peak, the drawdown rule or the H2 checks.
  - The Toss market lines leave the Telegram and Hermes boards. This narrows appendix B. Today's
    account holds cash only, so no class is withheld.

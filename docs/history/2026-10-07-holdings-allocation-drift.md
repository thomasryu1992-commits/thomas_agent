# Holdings board: allocation against the target mix, display only (Q9)

- **What this PR changes:** `holdings/allocation.py` (new). Each hourly `holdings_refresh` stores an
  `allocation` block next to `combined`, holding per-class totals, weights, targets, drift and the 5/25
  band facts. The board, `/holdings` and Hermes' `holdings_status` render it. `render_full` (terminal only)
  names the codes the table is missing.
- **Why:** `TOTAL_ASSET_ALLOCATION_V0.1.md` Q9 (display only, inside D4), built to its §11.1 design.
  Thomas answered three open choices on 2026-10-07:
  - the table ships empty;
  - Binance stablecoins count as cash;
  - the block reaches Hermes.
- **Choices this PR made:**
  - **Bands:** stock is judged as one 40% sleeve at ±5pp (§6's table). Global 30 and domestic 10 are shown
    only. Cash has no band.
  - **Incomplete totals:** an unclassified symbol, a Toss partial read, a stale or missing Binance futures
    file, a failed wallet read or an unread rate each make the total partial, with no band verdict.
  - **Wallet with its gate off:** it is off the board, said in a note. This is the P2 total's rule.
- **Not done:** no alert, no state file, no door reads it. Q2 and Q4 were dropped as limits the same day
  (#1172). No new env var, gate, registry or schema. The table and the targets are module constants.
- **The boundary:** this widens what leaves the process. The new keys are `allocation`
  (`ALLOCATION_KEYS`, `CLASS_ROW_KEYS`, `BAND_ROW_KEYS`, all pinned by tests). They are class totals,
  never per-symbol. The stored-file test still finds no symbol, name or price in the file.
- **Hermes:** the read shim passes the door's text and data through unchanged, so no shim, MANIFEST,
  `/new` or REBIND.
- **What the board says today:** engine margin is about 99.9% against a 0% target, and every band is OUT.
  That is the Q10 decision made visible (Toss holds 145 KRW and nothing else). A note says so.

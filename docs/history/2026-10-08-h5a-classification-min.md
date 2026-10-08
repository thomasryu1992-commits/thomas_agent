# H5a: Thomas's classification entries, kept on this host, place what the code table leaves out

- **What this PR changes:**
  - `holdings/classification.py` (new): the host-local entries file (`holdings_classification.json`).
    - Each entry is an instrument id and one of six classes (`ASSIGNABLE_CLASSES`).
    - The file has a `mapping_version`, and each entry records `approved_at`.
    - `load` fails closed on an unreadable file. `apply` refuses a bad entry before writing.
  - `holdings/allocation.py`:
    - The entries place Toss symbols and wallet assets the built-in placement leaves out.
    - The wallet is placed asset by asset (`asset_usdt`, in-process), so the unclassified count is
      assets.
    - A contradicting entry, or an unreadable file, withholds the band verdict.
    - The block carries `mapping_version`.
  - `holdings/store.py` loads the file each fire.
  - `holdings/binance_wallet.py` carries `asset_usdt` in-process.
  - `scripts/holdings_board.py`: `--unclassified`, `--classify ID=CLASS`, `--unclassify ID`. These
    are Thomas's terminal commands.
  - Tests: `tests/test_mvp_runtime_holdings_classification.py`.
- **Why:** the first complete NAV left 8 wallet altcoins unclassified, which withheld the band verdict.
  The entries name holdings, and names are LOCAL_ONLY under H3. A code table would put them in the
  repository, on GitHub and in Claude's diffs, so Thomas chose a host-local file that he writes himself.
- **Shaped this way because:**
  - **Running the command is the approval.** It runs as the service user on the host and records the
    time; no PR carries names.
  - **No new registry.** It extends the existing tables, and the file is a state file like the peak.
  - **A conflict is refused, not resolved.** An entry that contradicts BTC/ETH → coin or
    stablecoins → cash is a gap, never a silent override.
- **What it does not do:** it does not classify anything. The content is Thomas's. It also does not
  build the full instrument master (H7).

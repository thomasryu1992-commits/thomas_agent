# Thomas can name one instrument's tax kind for the rebalancing plan

- **Why:** RB3 (`REBALANCING_PROPOSAL_V0.1.md` §2.5, Thomas 2026-10-26) sets each instrument's tax kind from its
  market and class, with a per-instrument override for a product the table gets wrong. The calculator took
  overrides (`rebalance.tax_kind`), but nothing wrote or passed them. It was the last decided item left unbuilt.
  Thomas took it next on 2026-11-03.
- **What changed:**
  - `holdings/classification.py`: a `tax_kinds` block in the host-local classification file. Toss ids only, and
    the kind is one of the calculator's three. `set_tax_kind` writes one override or removes it, and moves
    `mapping_version` once.
    - All three writers (`apply`, `set_candidates`, `set_tax_kind`) now rewrite through one `_write`. Each
      carries every block it does not change. Before this, `apply` would have dropped a block it did not know.
    - A bad block makes the file unreadable, as a bad entry already did.
  - `store.load_rebalance` hands the overrides to the plan. An unreadable file now raises there, so the plan is
    refused. Planning without the overrides would misstate the tax.
  - The plan notes how many held instruments it taxed by Thomas's kind.
  - `scripts/holdings_board.py --tax-kind ID=KIND` (`ID=` removes it). It runs as the service user and refuses a
    host-side root run.
    - Like every mode but the one table, it is terminal-only. It is listed in the guard hook, the guard test's
      enumeration and CLAUDE.md's list.
- **Not changed:** the §2.5 table, the tax order, the orders, and what leaves the process. No door reads the
  file.
- **Checked:**
  - Tests: the kinds are pinned to `rebalance.TAX_RATE`. Each writer keeps the other blocks, and `ID=` removes an
    override. Bad ids and kinds are refused before anything is written. The plan's estimated tax drops to 0 for
    an overridden taxable sell, and the note appears. An unreadable block refuses the plan without naming the
    instrument. The command writes, refuses and removes.
  - Each of 6 mutations was caught: a writer dropping the block, the store not passing it, `plan_from_local` not
    passing it, any kind accepted, an unreadable file swallowed, no note.

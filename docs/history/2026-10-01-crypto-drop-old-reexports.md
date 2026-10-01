# The crypto lane drops the re-exports nobody read (crypto refactor plan PR-16, step N10)

- **What changed:** the lane's splits (PR7, PR-04, PR-07~13) left each old module importing every
  moved name, as the same object, so no caller broke. 379 names were imported from a sibling and never
  used by the module that imported them. A census over `runtime/`, `scripts/` and `tests/` (imports,
  attribute reads, patches, dotted strings), the non-Python files and the importers outside the repo
  found 64 that nothing reads through that module. Their import lines are gone, from `factory`,
  `live_order`, `live_execution`, `live_pnl`, `paper`, `cycle`, `live_route`, `live_readiness`,
  `live_entry` and `cost`. No code moved, and every name is still defined where it was.
- **What stays:** `pool`, a facade by design with its own baseline test. `factory`'s template
  builders (`_<family>_entry`), which the retirement tests look up by a computed name. Every
  re-export something still reads, about 140 of them read only by tests. Moving those readers to
  the defining module is not part of this step.
- **Pinned:** `tests/test_mvp_runtime_crypto_reexport_roster.py` holds the surviving re-exports per
  module and source, exactly, and checks that each one is the source's own object. The split pins
  that asserted "every name is re-exported" now check that the names still offered are the same
  objects. The header comments that said "re-exports every name" now say which names stay.

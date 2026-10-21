# Research sandbox, step 1: an allowlisted export of the crypto research records

- **Why:** C6b took Claude off docker to keep secrets, the live path and holdings out of reach. It also took
  away running any measurement. On 2026-10-20 the OI lookahead measurement needed two deploys plus Thomas
  running and copying it by hand, inside the live scheduler, which has no memory limit.
  `RESEARCH_SANDBOX_V0.1.md` splits keyed collection from secret-free computation (Thomas 2026-10-20, defaults
  approved).
- **What:** `scripts/ops/export_research_lake.py`.
  - Root's cron runs it hourly. It copies only the files in `ALLOWED` from the top of the crypto state directory
    into `/var/lib/thomas-measure/research-lake/<UTC id>/`, root:claude, files 0640 and directories 0750.
  - `ALLOWED` covers the research stores: candidates, cohorts, trials, nulls, forward and paper books,
    counterfactuals, and the OI, positioning and orderbook stores.
  - Each snapshot is written under a temporary name and renamed into place. `current` points at the newest, and
    three are kept. A manifest records each file's sha256.
- **What never leaves:** account, live trading, budget, risk limits, execution stage, venue contract, testnet,
  probe plan, the strategy pool, marks, locks, the `venues/` tree, and everything outside `crypto/`, holdings
  among it. Symlinks are not followed.
- **Why standalone:** it uses the standard library only, because root runs a pinned copy with the host `python3`.
  The primary checkout can be far behind `origin/main`.
- **Tests:**
  - the allowlist equals the owners' filename constants;
  - no sensitive constant is allowed;
  - only allowed files land, with no secret-looking, holdings, outside or testnet bytes;
  - the source is untouched;
  - manifest hashes and modes;
  - pruning and `current`;
  - the size cap;
  - refused configurations;
  - the CLI.

  Every test passes explicit temporary paths. Mutation checks (a sensitive name allowed, symlinks followed,
  chmod dropped) each fail a test.
- **Install (Thomas, root):** after the merge, copy the script to `/usr/local/lib/thomas/` and add one crontab
  line. The commands are in the script's docstring.

# One page for the whole system: `docs/SYSTEM_MAP.md`, and the entry points that pointed elsewhere

- **Why:** Thomas asked to organize the design documents for the whole system (2026-10-26). The readers are Thomas
  and a Claude session starting in this repository. The form is one new map, with the entry points cleaned up and
  no decision document rewritten. The scope is all of operations, with no holdings numbers.
  - The top-level entry was split across four documents, and none of them said what runs today: the organization
    goal (2026-07-27, MVP-era), the active architecture, the investment target map (DRAFT) and the retired Hermes
    design.
  - `README.md` and `DEPLOYMENT.md` still said "eight services" and listed the four doors and `knowledge/`, a day
    after #1307 removed them.
- **What:**
  - `docs/SYSTEM_MAP.md` is navigation only, with no authority. It covers the goal against today, a one-screen
    picture, the roles, the five services and sixteen schedule kinds by lane, the lanes, the governance spine, the
    parts outside the repository (the vault, server scripts and cron, Mac routines, backups, the C6a runner, the
    two bots), procedures, the retired parts, and where to find things.
  - `docs/README.md` lists the map first, puts the target architecture beside the organization goal, and moves
    Hermes V0.2 under the retired records.
  - The organization doc gains a dated, non-normative pointer to the 2026-10-25 audit and the map.
  - `README.md` and `DEPLOYMENT.md` now say five services. `DEPLOYMENT.md` no longer claims scheduler-maint holds
    no venue credential: it holds read-only ones.
- **Checked:**
  - Every fact on the map was read from the code or the host on 2026-10-26: the kinds and lanes from
    `scheduler.KINDS`, the services and commands from the compose file, the roles from the registry, the policy
    version, the cron lines, the timezone (UTC), the script locations, and every cited path.
  - The execution stage line comes from Thomas's own `--show` output that day.
  - A cohort cadence remembered from earlier work could not be found in code, so the map states
    `COHORT_LIFETIME_DAYS` instead.

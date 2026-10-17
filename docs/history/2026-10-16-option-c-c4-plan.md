# Option C C4: the plan for verifying the separation outside production (draft)

**What changed** (documents only). `docs/proposals/HOST_PRIVILEGE_SEPARATION_C4_PLAN_V0.1.md` is new,
in DRAFT. It plans C4, the phase that tests the separation C3's synthetic tests cannot: accounts, file
permissions, socket peers, real Docker socket access and install integrity. C4 runs on a throwaway
environment with a fake `.env` and synthetic keys.

**Why it is only a plan.** Three things are undecided:
- the environment (D-8). A VM is recommended, but this host has about 1.3 GB of memory free;
- its cost;
- C3b, a service entry point that C3a does not have. Without it there is nothing to install: no
  config loader, no genesis writer, no systemd units.

The plan maps N-1–N-14, the C2X/N1X/GAPX candidates that need real permissions, and the values
CR-8b/CR-10/11/19 wait on.

**Approval basis, stated as it stands.**
- Opened on 2026-10-16 inside the 8-hour autonomous grant, with no explicit approval for the repository
  change.
- Ratified on 2026-10-17 (not recorded as a prior approval).
- The only edits since: N1X-03's expected code is now `ACTOR_PEER_NOT_PERMITTED` (C2 §6.1b, as the
  corrected #1229 answers), and P-2's status.

**What it does not do.** It runs nothing, creates nothing, and grants nothing.

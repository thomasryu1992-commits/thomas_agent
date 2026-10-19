# Option C C6a: the deploy runner is installed on the production host

**What changed:** records only.
- `HOST_PRIVILEGE_SEPARATION_V0.1.md`:
  - the status line;
  - two §4.25.1 rows (C6a execution; C6b prerequisites 2 and 3);
  - the §4.25.5 install record;
  - D-2 and D-10 in §8.
- The C5 plan:
  - the status line;
  - prerequisites 2 and 3 marked decided.
- `STATUS.md`, regenerated.

**What happened** (Thomas 2026-10-19):
- The signing key was decided as recommended: `SHA256:NmnnkWsI…`, which the Mac's ssh-agent holds.
- One production dry-run on a clean tree at main `1bad6dc1` passed, and the host was unchanged afterwards.
- Thomas then ran `deploy_runner_c6a.py install` in the server shell. It finished at 10:00:56Z.
- Read back after the install:
  - the three accounts and their groups;
  - path owners and modes;
  - both units active, with no restarts;
  - both sockets;
  - the trust anchor's fingerprint;
  - all ten containers healthy.
- a9's lines (the `claude` inspection passes; root's gets `PEER_NOT_PERMITTED`) are only on Thomas's screen, so they are not recorded here.

**Decided the same day:** C6b prerequisite 2. After C6b, Thomas deploys and rolls back with the deploy skill. Claude
stops at PR, verification and asking the runner for `prepare_candidate`.

**Not done:** C6b and its prerequisite 1 (the Claude read door in production). Claude still runs as root.

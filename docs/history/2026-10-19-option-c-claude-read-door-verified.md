# Option C C6b prerequisite 1 in production: Claude's read door deployed and checked

**What changed:** records only.
- `HOST_PRIVILEGE_SEPARATION_C6B_READ_DOOR_V0.1.md`: the status line moves to IMPLEMENTED, and a new §5 holds the
  O-1 to O-9 results.
- `STATUS.md`, regenerated.

**What happened** (Thomas 2026-10-19):
- **Code.** #1254 (the door's service and client) and #1255 (preflight `--first-start`) merged.
- **Host directories.** Thomas made them under `/var/lib/thomas-claude-door`.
- **Deploy.** One deploy under a single lift of the DEPLOYMENT_HOLD:
  - `rollback-pre-1255` = the prior image;
  - `candidate-1255` promoted at 10:56:57Z;
  - all 11 containers healthy;
  - the first post-deploy fires (10:59:02Z) recorded.
- **Health watch.** The host copy was reinstalled with the new roster.

**O-1 to O-9** (read-only, 11:05Z). O-1 to O-7 PASS, O-8 and O-9 NOT_TESTED.
- **O-2.** The `claude` uid cannot reach the shared doors at all. `/root` and the state root are 0700, so the
  connection fails with `EACCES` before any door could refuse it.
- **O-6.** Both profile reads answer `ok` for uid 2101, and a held read (`holdings_status`) is refused.
- **O-7.** Root is refused and logged. The request log records every answer, and uid 2101 can neither read nor
  write it.
- **O-8 and O-9** need a real factory failure.

**Not done:** C6b itself. Claude still runs as root.

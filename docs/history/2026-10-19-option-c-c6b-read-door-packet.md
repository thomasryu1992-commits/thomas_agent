# Option C C6b prerequisite 1: the design packet for running the Claude read door

**What changed:** a new proposal, `docs/proposals/HOST_PRIVILEGE_SEPARATION_C6B_READ_DOOR_V0.1.md` (DRAFT), and the
regenerated `STATUS.md`. No code, compose or host change.

**Approval** (Thomas 2026-10-19): `승인: C6b 선행 1 Claude 전용 read door 운영화 설계 패킷 작성(문서만, compose·배포 제외)`.

**What it says.**
- The door's code is on main since #1223: the `claude` profile, projected replies, the request log and the uid
  allowlist.
- What is left:
  - one compose service, with literal uid and gid 2101 and the state root mounted `:ro`;
  - a socket outside `bridge/`, so Hermes cannot reach it;
  - a small client for Claude, built on `socket_door.call_door`;
  - two host directories;
  - a deploy, under the DEPLOYMENT_HOLD.
- The profile serves two reads, `schedules` and `scheduler_events`. Claude's current `docker exec` reads of holdings
  and readiness do not move to it.
- RD-1 recommends going ahead with two reads and having Thomas relay holdings and readiness after C6b. Even with its
  contract, `holdings_status` carries freshness only.
- RD-2 to RD-4 cover the directories, the request-log retention and how the work is split.

**Decided the same day** (Thomas 2026-10-19): RD-1 to RD-4 = A. The door runs with two reads, and Thomas relays
holdings and readiness after C6b. The four held reads each join after their contract and their own approval. The
directories are under `/var/lib/thomas-claude-door`. Retention is decided after b8 measures the log. The work goes in
the order code PR, host directories, one deploy, then O-1 to O-9, each step approved separately.

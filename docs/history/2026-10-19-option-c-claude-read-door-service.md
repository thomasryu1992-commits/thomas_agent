# Option C C6b prerequisite 1, step 1: Claude's read door as a compose service, and its client

**What changed:**
- `docker-compose.yml` gains the service `claude-read-bridge` (container `thomas-claude-read-bridge`).
- New `scripts/claude_read.py` and `tests/test_claude_read_door_service.py`.
- `scripts/ops/health_watch.sh` adds the container to its roster.
- Two preflight tests learn the new service: `THOMAS_CLAUDE_DOOR_DIR` joins the path variables, and the door is
  the one runtime service that mounts state `:ro`.
- The win32 skip ceiling goes from 607 to 609, for two unix-socket round trips.

**Approval** (Thomas 2026-10-19): `승인: read door 코드 PR 생성(scripts/claude_read.py 클라이언트, compose claude-read-bridge
서비스, 고정 시험; 병합·호스트 디렉터리·배포 제외)`. This is step 1 of RD-4. The design is
`docs/proposals/HOST_PRIVILEGE_SEPARATION_C6B_READ_DOOR_V0.1.md`.

**The service** runs the existing read door under `--profile claude`. Its shape is what the tests pin:
- uid and gid 2101 are written as literals, never the `${MVP_BRIDGE_CLIENT_*}` the other doors share;
- 2101 appears in no other door;
- the state root is mounted read-only;
- the socket and the request log live under `${THOMAS_CLAUDE_DOOR_DIR:-/var/lib/thomas-claude-door}`, outside
  `bridge/`;
- hermes mounts neither;
- it has no `env_file`, no secrets and no `depends_on`.

Six compose mutations were each caught by one failing test:
- the shared uid variable;
- a writable state root;
- a socket under `bridge/`;
- no profile;
- the hermes uid admitted;
- an `env_file`.

**The client** sends one of the profile's two reads and prints the reply as the door sent it. It refuses any
other name before connecting. A test holds its read list equal to the profile's. It runs under the system
`python3 -I` with no venv, because `socket_door` and `errors` are standard library underneath. Two tests do a real
round trip against a profiled door on a temporary socket:
- an admitted peer's reply, with its row in the request log;
- a peer the door refuses, which gets `PEER_NOT_PERMITTED`.

**Not done:**
- **Host directories.** They come before the first start, from root: `sock/` 10001:2101 0750 and `log/`
  10001:10001 0700.
- **The deploy.** It needs its own approval under the DEPLOYMENT_HOLD. At the promote, `deploy_preflight`
  stops on a container that compose names but that is not running, so the first deploy of this service needs
  that check handled.
- **Health watch on the host.** The host copy at `/root/backups/health-watch.sh` gets the new roster only when it
  is reinstalled. Reinstall it after the door is up, or the watch reports the container missing.
- **O-1 to O-9.**

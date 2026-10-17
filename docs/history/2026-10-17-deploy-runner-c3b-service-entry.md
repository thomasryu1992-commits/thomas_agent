# Option C deploy runner C3b: the service entry point, its configuration, the install tool, the unit templates

**What changed:**
- `runtime/mvp_runtime/deploy_runner/config.py` and `serve.py`;
- `schemas/deploy_runner_config.v0.1.schema.json`;
- `scripts/ops/deploy_runner_install.py` and two unit templates;
- one test file.

**Approval.** `승인: C3b 서비스 진입점·설정 로더·설치 도구·systemd unit 템플릿 구현 및 PR 생성(합성 시험만;
설치·실행·CI 변경 제외) (Thomas 2026-10-17)`. Nothing is installed, enabled or run as a service. The
install tool and the units were exercised only on synthetic paths.

C3a built the two processes but nothing that starts them. The C4 plan (#1232, §2 P-3) named that gap.
This increment closes it in code:

- **`config.py`.** One root-owned JSON file per service.
  - It is opened without following a link and checked on its descriptor: a regular file, one link,
    owned by the expected owner, and writable by nobody else.
  - It is parsed strictly (ASCII, no duplicate keys, no non-finite numbers) and held to the closed
    `deploy_runner_config.v0.1` schema.
  - It names the environment, the literal uid list for the socket, every path, the time policy, the
    `store_id` the install wrote, and the expected **environment fingerprint**.
  - The fingerprint (CR-8 condition 6) is the Python version plus one hash over every installed
    distribution's name and version. A different interpreter or package set is
    `RUNNER_ENVIRONMENT_MISMATCH`.
  - The trust anchor (CR-12 T3) is a separate root-owned file with one `ssh-ed25519` key per line,
    in the `revoked_keys` format and read with the same parser. It must name at least one key.
  - Any doubt about the file is `RUNNER_STARTUP_BLOCKED`. Nothing is read from the process
    environment; a test checks the whole package for that.
- **`serve.py`.** `python -I -m runtime.mvp_runtime.deploy_runner.serve --role gate|actor --config …`.
  - The order is: config → fingerprint → for X, the time-policy invariants, the anchor and the trust
    files against it, the instance lock, and the startup check → the socket.
  - G's socket answers in the caller's shape. X's answers in `thomas.actor_reply.v1` with
    `ACTOR_PEER_NOT_PERMITTED`.
  - A latched X still starts, and refuses every request.
  - `--check` runs everything but the socket and releases the lock: what an install verifies before
    enabling a unit.
  - A refusal prints the role and the code, nothing else.
  - Clock synchronisation has no decided source, so X is built with it unconfirmed and
    `prepare_candidate` answers `CLOCK_UNTRUSTED`.
- **`scripts/ops/deploy_runner_install.py`.** For Thomas at C5/C6, as root on the target host; never
  run by Claude on a real host.
  - `fingerprint` prints the value a config must hold, measured from the interpreter it runs under.
  - `genesis` writes the consumption store's first row with a fresh 128-bit `store_id` (C2 §3.4: the
    install makes it, X never does).
    - It opens the actor's directory through a descriptor and requires it to be the actor's and
      private.
    - It creates the store exclusively, at mode 0600, hands it to the actor, and never replaces one.
  - `render-units` fills the templates. Account names and paths are held to closed forms. G and X must
    differ (D-10a), nothing may point into the runtime state root, and an existing unit file is never
    overwritten.
  - `check-config` is `serve --check`.
- **Units** (`scripts/ops/deploy_runner_units/*.service.in`).
  - Common to both:
    - `python -I` from the install directory and a fixed working directory;
    - `PATH`/`LANG` only, with `MVP_BRIDGE_CLIENT_GID`/`UID`, `PYTHONPATH`, `PYTHONHOME` and
      `DOCKER_HOST` unset;
    - `UMask=0077`, `NoNewPrivileges`, `ProtectSystem=strict`, `PrivateTmp`, an empty capability set,
      and write access only to the service's own directory.
  - Only X's unit has the `docker` group. G reaches X's socket through X's group. Who reaches G's socket
    is the gate group's membership, which is account setup (C5).

**Not built here.**
- The hash-locked dependency file and the exact Python 3.12.N with its source (CR-8b). These are
  Thomas's decision before C4; generating the lock needs a network read.
- Any CI workflow change (CR-20).
- The clock source.
- Installing anything.

**Not verified by this increment (C4's to measure).**
- Whether the unit hardening lets X reach the Docker socket and run `git status` in its trees under
  `ProtectSystem=strict`.
- Whether the group arrangement gives exactly G, and only G, X's socket.

**One rename on the way.** The config's executable paths are `docker_bin`, `git_bin` and
`ssh_keygen_bin`. The C3a-1 guard test forbids the bare string `docker` anywhere in the package's code,
and the test was kept as it is.

**Numbers.**
- **Tests:** `tests/test_mvp_runtime_deploy_runner_service.py`, 68 cases.
- **Fault injection** (scratch, outside the suite): each of 25 injected faults made a test fail. Four
  were missed on the first pass and closed with dedicated cases:
  - a valid config for the other role;
  - a duplicate key inside an otherwise valid config;
  - the fingerprint check skipped in the entry point;
  - Docker added to G's unit beside another group.
- **Windows skips:** +52 (simulated: 16 run, 52 skip). The win32 ceiling moves 507 → 559, predicted;
  this PR's Windows run is the measurement.

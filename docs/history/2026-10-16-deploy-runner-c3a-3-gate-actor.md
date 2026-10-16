# Option C deploy runner C3a-3: the gatekeeper, the actor, their sockets, and the closed Docker argv

**What changed** (`runtime/mvp_runtime/deploy_runner/`, three schemas, one test file).

The third and last C3a increment of the Option C deploy runner (Thomas 2026-10-16: C3a-3
implementation and PR creation; merge, install and operation not approved). C3a-1 decided whether an
approval counts and C3a-2 holds the records that make it count once. This increment adds the two
processes of the N1 design and the only commands the actor may run. Nothing is wired into a service
or installed, and no Docker command runs in any test.

- **`docker_argv.py`** holds the closed templates (CR-12 T3, conditions 1–3).
  - The only variable parts of any argv are: an image id the actor measured, a tag computed from a
    PR number, a container name from the fixed list of eight runtime services, and the runner's own
    tree path computed from that number.
  - There is no `run`, `exec`, `cp`, mount, environment, network or build-arg option, and no pull,
    fetch or clone.
  - Every builder validates its input and refuses with `ARGUMENT_INVALID`.
- **`commands.py`** is the one place a process starts. It runs by argv, without a shell. The
  environment is `PATH` and `LANG` only: no `DOCKER_*` or `GIT_*` from the caller. Stdin is closed,
  stderr is discarded, and stdout is bounded and must be ASCII.
- **`actor.py` (X)** decides, spends and acts. It trusts nothing G says:
  - every message is re-validated against `deploy_actor_request`;
  - the request fingerprint is X's own, and G's sequence number is kept out of it;
  - X verifies the approval itself.
  - In order:
    1. A latched runner refuses everything, inspection included. A latch file it cannot read counts
       as a latch.
    2. `validate_candidate`, `promote` and `rollback` are refused with `OPERATION_BLOCKED_BY_POLICY`.
       The set of enabled operational verbs is an empty code constant (CR-7).
    3. `inspect_candidate` takes no approval and claims nothing. It is rate-limited and answers fixed
       fields only.
    4. `prepare_candidate`:
       - claims the request id, verifies the approval, checks the clock, and takes the operation lock;
       - measures the tree (clean, `HEAD` equal to `origin/main`) and the running images (all eight on
         one image and labelled with this environment's compose project, no existing candidate or
         rollback tag);
       - checks the measured binding, spends the approval, and writes CONSUMED;
       - tags the measured running image **by id** as `rollback-pre-<PR>`, then builds the runner's
         own tree into `candidate-<PR>` under the action's deadline;
       - checks that the candidate tag exists.
  - A failure after the spend is never retried. If the actor can re-measure, the request is
    `partial`; if it cannot, `unknown`. Either way the actor latches, in memory and on disk, so
    deleting the LATCH file changes nothing for the running actor or for the next start.
  - A spend whose CONSUMED row cannot be written latches on `RECONCILIATION_MISMATCH`, as the next
    startup would.
  - **One deviation from C2 §6.3, by the closed contract.** §6.3 says a partial failure's reply
    `data` carries what was measured. The closed reply schema (C3a-1) forbids `data` on a refusal, so
    the measurement goes into the audit row's `target` instead and the reply is the code alone.
- **`gate.py` (G)** has no Docker, no secret, no verifier, and no final judgement.
  - It checks the frame's closed shape and arguments, so a malformed request costs the actor nothing.
  - It writes `FORWARDED` to its own hash-chained intake log and `fsync`s it before calling the actor.
  - It holds the actor's reply to `deploy_actor_reply`. A refusal is rebuilt with the table's
    sentence, never relayed. The result is held to `deploy_runner_reply`.
  - The actor never reads the intake log (GAP-2C).
- **`door.py`** puts both sockets on the shared `socket_door` transport, as the C2 design says
  (§0N, §664). Three of the shared door's defaults are replaced:
  - the uid list is literal and never empty (the shared door serves the group alone when
    `MVP_BRIDGE_CLIENT_UID` is unset);
  - every reply is a code and its fixed sentence, because the shared handler returns `str(exc)`;
  - busy is `RUNNER_BUSY`.
  - **Still inherited:** the shared constructor parses `MVP_BRIDGE_CLIENT_UID` (then discards it)
    and, when `MVP_BRIDGE_CLIENT_GID` is set, hands the socket and its directory to that group. The
    runner's unit environment is C4 install work and must not set the second.

**Two C3a-1 guard tests were narrowed, not removed.**
- `socket_door` may be imported by `door.py` and `gate.py` only, and neither may call
  `resolve_socket_path`, which defaults under the repository.
- A process may be started by `approval_verify` (ssh-keygen) and now `commands` (the docker argv),
  and nowhere else.

**Shared codes.** `ARGUMENT_INVALID`, `AUDIT_WRITE_FAILED`, `PEER_NOT_PERMITTED` and
`REQUEST_ID_REQUIRED` are now raised from a second module, each with its existing meaning. They are
declared in `tests/test_diagnostic_code_index.py` with the reasoning.

**Not built here.**
- The runner's tree must already exist. Cloning or fetching it is a network read that no decision
  covers, so `ORIGIN_FETCH_FAILED` has no emitter yet.
- `FRAME_TOO_LARGE` has no emitter either: the shared transport drops an oversized frame without
  answering.
- No clock-synchronisation source is decided, so by default `prepare_candidate` is `CLOCK_UNTRUSTED`
  until one is wired.
- RECOVERY_INBOX, install, accounts and systemd units are C4 and later.

**What this does not protect.** X holds Docker and is root-equivalent if compromised (CR-12: X
compromise is NOT_PROTECTED). The tests prove code paths; permission separation is C4 and C6.

**Numbers.**
- **Tests:** `tests/test_mvp_runtime_deploy_runner_gate_actor.py`, 108 cases.
  - Every argv the actor can run is enumerated and checked element by element.
  - A test fails if `docker_argv` grows a builder the enumeration does not cover.
  - Both sockets are exercised end to end with the kernel's peer credentials.
  - Text Docker printed (a synthetic secret, a path) reaches neither the reply nor the audit.
- **Fault injection** (scratch, outside the suite): each of 50 injected faults made a test fail.
  - Three were missed on the first pass and closed with dedicated cases:
    - a build that exits non-zero after tagging;
    - the request state after a spend whose audit row was lost;
    - an intake row renumbered with its hash recomputed.
- **Windows skips:** +83. Records and sockets need POSIX, so those cases skip; 25 run. The win32
  ceiling moves 403 → 486, predicted; this PR's Windows run is the measurement.

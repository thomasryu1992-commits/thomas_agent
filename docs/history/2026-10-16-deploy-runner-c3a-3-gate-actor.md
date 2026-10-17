# Option C deploy runner C3a-3: the gatekeeper, the actor, their sockets, and the closed Docker argv

**What changed** (`runtime/mvp_runtime/deploy_runner/`, three schemas, one test file).

The third and last C3a increment of the Option C deploy runner.

**Approval basis, stated as it stands.**
- This PR was built and opened on 2026-10-16 inside Thomas's 8-hour autonomous grant. That grant did
  not lift the earlier requirement that C3 implementation needs its own explicit approval, and no
  approval preceded the work.
- On 2026-10-17 Thomas ratified keeping the work and approved the contract corrections below. The
  ratification is not recorded as a prior approval.
- Merge, install and operation are not approved.

C3a-1 decided whether an
approval counts and C3a-2 holds the records that make it count once. This increment adds the two
processes of the N1 design and the only commands the actor may run. Nothing is wired into a service
or installed, and no Docker command runs in any test.

- **`docker_argv.py`** holds the closed templates (CR-12 T3, conditions 1–3).
  - The only variable parts of any argv are: an image id the actor measured, a tag computed from a
    PR number, a container name from the fixed list of eight runtime services, and the runner's own
    tree path computed from that number.
  - There is no `run`, `exec`, `cp`, mount, environment, `--network` or build-arg option, and no
    clone or fetch.
  - The build itself does reach the network. `--pull=false` only skips refreshing a base image
    already present: a missing one is fetched, and the Dockerfile's own steps (`apt-get`) download.
    That is the effect §6.3 accepts; no request value shapes it.
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
  - A `FAILED_EFFECT_PARTIAL` reply carries the measured ids and nothing else (see the contract
    alignment below).
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

**Contract alignment with C2 v0.5 (Thomas 2026-10-17).** The first version of this PR departed from the
closed contract in five places. A 2026-10-17 review found them, and Thomas approved the corrections,
with the specification first:

1. **X's peer refusal.** X's socket refuses a peer off its list with `ACTOR_PEER_NOT_PERMITTED`
   (§6.1b, N1X-03). G's keeps `PEER_NOT_PERMITTED`. Each socket answers in its own closed shape.
2. **The compose label.** §6.3 item 7 compares the running services' compose project label and
   named no code for a mismatch; the first version borrowed `RUNNER_ENVIRONMENT_MISMATCH`, which
   §9 reserves for the startup environment fingerprint.
   - **Minimal C2 revision (Thomas 2026-10-17):** one new reply code, `COMPOSE_PROJECT_MISMATCH`.
   - Nothing else in the contract changes.
   - The code is added to the reply, audit and latch enums of C3a-1 and C3a-2, which are derived
     from the code table.
3. **Message fields.**
   - The caller's frame carries `approval_document` (§6.1a).
   - G→X is `thomas.actor_request.v1`:
     `{schema, request_id, verb, args, approval_document_b64?, approval_signature?, gate_intake_seq}`.
     X gets no `client_id`: its audit records null, and G's intake row with the same
     `gate_intake_seq` holds the attribution (GAP-2).
   - X→G is `thomas.actor_reply.v1`: `{ok, reason_code, request_id, state, data, replayed}`. X sends
     no sentence and no error reference.
   - **`state` (defined 2026-10-17).** It is X's request record for this id: `claimed`,
     `completed`, `refused_before_consume`, `unknown` or `partial`. It is null when no record
     answers it: an inspection, or a refusal before any claim. G does not pass it to the caller,
     because §6.1a has no such field.
4. **The partial failure.** §5.2 and §6.3 put the measured ids in a `FAILED_EFFECT_PARTIAL` reply.
   - The reply carries `{pr, git_commit_sha, rollback_image_id, candidate_image_id}`, each value a
     validated form or null.
   - The C3a-1 caller reply schema (`deploy_runner_reply.v0.1`) admitted no `data` on any refusal.
     It now admits this closed shape on `FAILED_EFFECT_PARTIAL` only, and nothing on any other
     refusal.
   - No Docker text, secret, environment value, traceback or path can enter it: every value is held
     to a pattern on both sides.
5. **Container state.** Only the six states of §6.2 are accepted. Anything else is
   `IMAGE_STATE_UNAVAILABLE`. An absent container is null in `running` and has no entry in
   `containers`.

**Two C3a-1 guard tests were narrowed, not removed.**
- `socket_door` may be imported by `door.py` and `gate.py` only, and neither may call
  `resolve_socket_path`, which defaults under the repository.
- A process may be started by `approval_verify` (ssh-keygen) and now `commands` (the docker argv),
  and nowhere else.

**Shared codes.** `ARGUMENT_INVALID`, `AUDIT_WRITE_FAILED` and `REQUEST_ID_REQUIRED` are now raised
from a second module, each with its existing meaning. They are
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
- **Tests:** `tests/test_mvp_runtime_deploy_runner_gate_actor.py`, 130 cases.
  - Every argv the actor can run is enumerated and checked element by element.
  - A test fails if `docker_argv` grows a builder the enumeration does not cover.
  - Both sockets are exercised end to end with the kernel's peer credentials.
  - Text Docker printed (a synthetic secret, a path) reaches neither the reply nor the audit.
- **Fault injection** (scratch, outside the suite): each of 64 injected faults made a test fail. That
  is 50 on the first version and 14 more on the contract points: X's peer code and reply shape,
  partial data dropped or widened, each `state` value, the absent entry, the state enum, G dropping
  partial data, leaking `state`, forwarding `client_id`, accepting a foreign `request_id`, and the
  label code.
  - Three were missed on the first pass and closed with dedicated cases:
    - a build that exits non-zero after tagging;
    - the request state after a spend whose audit row was lost;
    - an intake row renumbered with its hash recomputed.
- **Windows skips:** the first version measured 486 in CI, exactly as predicted. The contract
  corrections add 21 POSIX-only cases, so the simulated run gives 104 skipped and 26 run in this
  file. The win32 ceiling moves 486 → 507, predicted; this head's Windows run is the measurement.

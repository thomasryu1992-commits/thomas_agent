# Host privilege separation: the Secret boundary has to be the OS, not a rule

- **Why** (Thomas 2026-10-12):
  - The operational Secret policy for C7 PR-D was decided as Option A (keep the current policy).
  - Exceptions 3-1 (Claude runs compose with the operational `.env`) and 3-2 (the preflight's Python
    process receives the interpolated compose config) were rejected, and B′ was not adopted. The
    emergency rollback permission and the deploy stay NOT_GRANTED.
  - The reason: Claude runs as root with Docker access. Command limits, output suppression and log
    masking do not stop it reaching the secrets.
  - Thomas named Option C, separating Claude's operating rights from secret access, as the long-term
    fix, and asked for its design and non-operational preparation.
- **What this adds:**
  - `docs/proposals/HOST_PRIVILEGE_SEPARATION_V0.1.md` (DRAFT): the current structure classified by
    evidence, a threat model, the C-1 to C-6 design, a comparison of four alternatives, a C0–C8
    roadmap, a deploy checklist, and ten open decisions.
  - `HOLDINGS_C7_PR_D_DEPLOY_READINESS_V0.1.md` §4.5 records the decision and points to the design.
- **What the survey found:**
  - The 2026-10-09 LOCAL_ONLY guard record already said OS separation is the boundary that holds. It
    proposed sudo wrappers. This design keeps its four steps but recommends one minimal runner over
    sudo wrappers. Transport (`socket_door` with a mandatory `SO_PEERCRED` allowlist), idempotency,
    audit hashing and the preflight's judgement code are reused. Only the verb table, the approval
    check and the reply schema are new.
  - No existing approval path resists a root writer: R9's trust root is a file in the state root,
    and Core approval takes its identity from CLI flags. After separation R9 can no longer be forged
    by Claude, but host root still can. An SSH signature from Thomas's Mac key cannot be forged even by
    root, but root can still skip the check: edit the verifier or call Docker directly (corrected in C1,
    below). The channel is left for Thomas to choose.
  - The runner must own its git clone and fetch it itself. `check_tree` trusts the tree's own
    `origin/main` ref, which the tree's owner controls.
- **Host facts read** (metadata only):
  - `id` and the group and account lists; process owners and names; file owners and modes (including the state root); `ptrace_scope`;
    the `/proc` mount options.
  - Two line counts from the root crontab, without printing it.
  - No secret content, runtime state, container or Docker daemon was touched.
- **Not done:** no runner code, no host change, no negative test run. Each is its own approval (roadmap
  C3, C6).
- **C1, 2026-10-13** (Thomas's C1 instruction; docs only, nothing run):
  - §4.7 lists 30 tasks that depend on root or Docker (W-01–W-30), each with its source line and a
    replacement class:

    | Class | Meaning | Rows |
    |---|---|---|
    | A | non-root direct | 5 |
    | B | read-bridge | 4 |
    | C | runner | 7 |
    | D | not done by Claude | 10 |
    | E | needs evidence | 7 |

    Some rows fall in two classes. No required task was left without a path.
  - **read-bridge (§4.8).**
    - `holdings_status` is the same call as `holdings_board --json`.
    - `schedules`, `scheduler_events` and `runtime_status` cover the console reads.
    - `crypto_readiness` is not `live_readiness` run in the scheduler: READY is judged per process.
    - The Claude account cannot reach the socket yet. It sits under `/root` and the 0700 state root, and
      the uid allowlist defaults to 10000.
  - **Automation (§4.9).** The three crontab lines that mention "claude" stay NOT_VERIFIED; the crontab
    was not re-read. A script in Claude's own scripts directory reads the bot token from `.env` today
    (`hermes-notify.sh`, called by the Mac over SSH). Claude could edit that script, so it is the same
    kind of path Option A closes. Decision D-12 is to move it.
  - **Approval security (§4.10).** Forging an approval and bypassing its check are now separate
    questions. "No deploy without approval" holds only against non-root actors, whatever the
    channel, and file permissions on the runner, its config and Docker make it hold.
    - D-2's rationale was corrected.
    - Negative tests N-13–N-15 cover import-path poisoning, verifier-config writes and Guard settings.
  - **Tests** (run in this worktree; the import path resolves to it, not to the primary checkout):
    - `build_proposal_status.py --check`: OK
    - `test_design_record_lifecycle.py` + `test_build_history_fragments.py`: 330 passed
    - those two + `test_deployment_env_passthrough.py` + `test_dockerignore_excludes_secrets.py`: 571 passed
    - release gate `--full --check-only`: PASS
    - No Docker, runtime or full-suite run: the change is docs only.
- **W-14 and W-15, 2026-10-13** (static source comparison only):
  - Nothing was run: neither the two CLIs nor any read-bridge read. No state file or real output was
    opened.
  - **W-14 `register_execution_stage --show`:**
    - `crypto_status` carries no execution stage.
    - `crypto_readiness` returns `data.execution_stage` from the same resolver over the same state
      root. That resolver reads no environment, and with no `approval_store` given it falls back to the
      same default store. That makes the stage verdict class B.
    - The ledger and anchor diagnostics, the running policy hash and the record's testnet cycle id are
      not on the door. They are used when repairing the stage ledger (`--sync-anchor`, a state write),
      so they are class D.
  - **W-15 `promote_strategy_candidates --list`:**
    - `crypto_status` gives only candidate aggregates: backlog count, funnel, fusion-parent count.
    - The per-candidate rows and the door-refusal warnings exist only in `--list`. That makes it class D.
    - A new read-bridge read would be class C and a governance change (D-15).
  - The E class drops from 7 rows to 5.
  - **Still NOT_VERIFIED:**
    - Whether read-bridge and the scheduler run the same image today.
    - Whether the other `live_readiness` checks and the `pool` warning helpers are free of side effects.
  - **Tests** (this worktree): `build_proposal_status.py --check` OK; lifecycle + history fragments +
    env passthrough + dockerignore + local-only guard 671 passed; release gate `--check-only` PASS.
- **W-11 and W-12 against the dispatch door, 2026-10-13** (static source comparison only):
  - Nothing was run: no CLI, door, approval request or state read.
  - **Both stay E.** The door expresses the request text, four of the five kinds and the Naver seeds.
    It is not equivalent in four ways:
    1. **Attribution.** The CLI passes no requester, so its runs record `thomas` / `real_thomas` /
       authenticated. A Claude `docker exec` run is therefore filed as Thomas. The door records
       `assistant_bridge`, the same as Hermes, and keeps no peer uid.
    2. **Assurance.** The door's validation policy is fixed on the worker's command (`auto` +
       `revise`). It has no priority HIGH and no model-tier selector.
    3. **Scope.** The door has no `--write-output` and no `development` kind.
    4. **Door reach.** The same socket also takes v3 workflow commands and `schedule.propose_change`.
       The policy's `assistant_schedule` allows those mutations inside its scope, so adding Claude's uid
       would grant more than the CLI does.
  - **Security reading.**
    - Unknown keys are refused at the door and again at the worker.
    - There is no path key and no subprocess.
    - `actor_profile` cannot be set from outside.
    - `client_id` is free text and is not authenticated.
  - Options are recorded under D-14: (a) Claude does not run intake (D, recommended); (b) a Claude-only
    profile with a v2-only path (C). No decision was taken and no code was changed.
  - **NOT_VERIFIED:**
    - Where the CLI refuses an unknown `--kind`.
    - Whether every consumer of `requester_type` ignores it for authority.
    - Whether the door reports a ledger persist failure.
    - The live door's command line.
  - **Tests** (this worktree): `build_proposal_status.py --check` OK; lifecycle + history fragments +
    env passthrough + dockerignore + local-only guard 671 passed; release gate `--check-only` PASS.
- **W-13 READY paths, 2026-10-13** (static source comparison):
  - Nothing was run against live state: no `live_readiness` CLI, no read-bridge read, no operator
    channel. Two readiness test files that use only `tmp_path` and a cleared env were run (279 passed).
  - **`crypto_status` has no READY at all.** Only `domain_console` and the CLI import the readiness
    board. The order path re-judges the stage and the guard itself and reads no readiness result.
  - **One board, three answers:**

    | Answer | What it means | Scheduler CLI vs read-bridge |
    |---|---|---|
    | `ready` | All 22 checks pass, five of them from the running process's env: "can THIS process place an order". It is the CLI exit code. | Always False on the read-bridge, so **not equivalent** |
    | `live_entry_possible` | A three-valued AND of 11 components. Off the trading process, it is answered from that process's last recorded fire: one interval late, None when stale. | **Partly equivalent** |
    | `execution_stage` | The shared resolver. | **Equivalent** |

  - None of the three is an approval or an order authorisation.
  - **W-13 stays E.** Recommended under D-14: entry state via the read-bridge (B, after D-11); the
    process `ready` stays D.
  - **NOT_VERIFIED:**
    - The 11 components against each door's refusal conditions.
    - Side effects of the lower readers and the account module.
    - The live container env.
  - **Tests** (this worktree): readiness 279 passed; `build_proposal_status.py --check` OK;
    lifecycle + history fragments + env passthrough + dockerignore + local-only guard 671 passed;
    release gate `--check-only` PASS.
- **W-20 `docker logs`, 2026-10-13** (static source comparison):
  - Nothing was run: no Docker command, no log, container or read-bridge read.
  - **Uses.** Eight uses were found in the docs and sources. Past records in `BUILD_HISTORY.md` and
    `docs/history/` were excluded, and the runbook line that only notes logs are not backed up was
    left out.

    | Purpose | Uses | Class |
    |---|---|---|
    | Startup config banners: door uid allowlist, worker validation flags, frontdesk mode | 3 | C: fixed patterns in the runner's post-deploy check, returning only the matched token (D-16) |
    | Watching the scheduler after a deploy, and budget-deferral frequency | 2 | B: `heartbeat`, `scheduler_events`, `runtime_status`. Deferrals have been ledger events since August. |
    | Incident stacks, door tracebacks (kept from the assistant by design, only an `error_ref` returns) and the operator alert text | 3 | D |

  - Raw logs are not needed for any purpose found, and are never the default for Claude: what reaches
    a service log cannot be shown to be secret-free from source.
  - W-20 leaves E, and E drops to 4 (W-11, W-12, W-13, W-30). D-16 was added.
  - **NOT_VERIFIED:**
    - Whether service logs carry secrets.
    - Whether the watchdog record is visible through the read-bridge.
    - The live banners.
    - The live log retention.
  - **Tests** (this worktree): `build_proposal_status.py --check` OK; lifecycle + history fragments +
    env passthrough + dockerignore + local-only guard 671 passed; release gate `--check-only` PASS.
- **Decision preparation for D-11, D-16 and D-14, 2026-10-13** (design §4.12; sources only):
  - **This round ran:** a read-only state check (branch, HEAD, remote main via `ls-remote`, hashes); the
    same five document test files; `build_proposal_status.py --check`; the release gate
    `--check-only`. Nothing else.
  - **D-11. The earlier default (a) is withdrawn.**
    - One `.env` value, `MVP_BRIDGE_CLIENT_UID`, feeds the uid allowlist of the read, dispatch,
      knowledge and switch doors (`docker-compose.yml:488,547,732,798`).
    - Adding Claude's uid there would open the stop/start door and the schedule-delegating door too.
    - The new default is (d): a dedicated read-bridge instance. The socket path is already
      configurable (`read_bridge_cli.py:31-38`), and the allowlist would be a per-service literal, as
      the pipeline worker has.
    - Open under D-11:
      - the policy `assistant_read` actor;
      - making the allowlist mandatory;
      - a read audit log;
      - whether a read-only state mount works.
    - **Found:** `scheduler_events` returns raw event rows in `data` (unclipped `status`). Their
      content per kind is NOT_VERIFIED, so it becomes a test condition before admission.
  - **D-16.** Both alternatives were compared on eight criteria, and they split.
    - Recommended: the runner's fixed patterns for the three startup banners. That is moot until a
      runner exists.
    - The state file plus a new read is the path if the runner is declined.
  - **D-14.**
    - W-11, W-12 and W-13 stay E.
    - **W-30 is reclassified to D.** One root variable sets both the operational paths and the
      `.claude` paths. Moving Claude's home would silently drop the Claude config from core and leave
      transcripts backing up stale data, or failing. A second root variable is a C5 item and a C6 stop
      condition, and the installed copy is NOT_VERIFIED.
    - E is now 3. No D-item status changed.
  - **Tests:** `build_proposal_status.py --check` OK; lifecycle + history fragments + env passthrough
    + dockerignore + local-only guard 671 passed; release gate `--check-only` PASS.
- **Pre-approval verification, 2026-10-13** (design §4.12.4; sources, compose, policy and tests only):
  - **This round ran:** a read-only state check, the five document test files, the status check and
    the release gate check-only.
  - **D-11.**
    - The dedicated instance can pin its allowlist as a per-service literal, as the pipeline worker does
      (`docker-compose.yml:637-638`). The group grant is shared too, so the gid must be a literal as well.
    - A read-only state mount works only partly: the `tasks` and `history` reads take a lock file with
      `open("a")`.
    - Only the existing `assistant_read` clause is enforced by code. A separate Claude clause would be
      documentation without a code change.
    - Doors keep no per-request log.
  - **`scheduler_events`.** All writers were enumerated. A factory child failure records
    `failed:FACTORY_CHILD:` plus the first 160 characters of `"<type>: <message>"`
    (`scheduler.py:2296,2466-2475`), so exception text can reach the read. Mutation events carry the
    schedule's `request` and `reason` text, and success statuses are per-kind summaries (sampled, not
    exhaustive). Field projection is recommended, and no test pins it today.
  - **W-30** stays D, with a legend note that a D row marked with a prerequisite still needs a change
    before the split. The cron account is NOT_VERIFIED.
  - **D-16** depends on D-1 and D-3, both undecided. Until then only document-level specification is
    possible. A fail-closed table and the audit and no-raw-text conditions were written.
  - No decision status changed.
  - **Tests:** `build_proposal_status.py --check` OK; lifecycle + history fragments + env passthrough
    + dockerignore + local-only guard 671 passed; release gate `--check-only` PASS.
- **Scheduler exposure and D-11 conditions, 2026-10-13** (design §4.12.5; sources and tests only):
  - **This round ran:** a read-only state check, the five document test files, the status check and
    the release gate check-only.
  - **The factory failure text.** The text (`"<type>: <message>"`, first 160 characters) goes four ways
    from `_finish_factory_occurrence`:

    | Destination | Who reads it | Clipped? |
    |---|---|---|
    | The scheduler event ledger | read-bridge `scheduler_events` (`data.events` is the raw row) | No |
    | The schedule's `last_status` | read-bridge `schedules`; a removal event carries it back to the ledger | 60 characters on the read |
    | The operator failure alert | Thomas | No |
    | Scheduler stderr | The service log | No |

    The existing test checks only the prefix and the exception type.
  - **Minimum response contract (specification only).**
    - Rows are rebuilt from closed lists of actions and kinds and typed fields.
    - `status` becomes `outcome` by fixed N-rules: a failure keeps only its first reason code, and a
      success is `fired` with no summary.
    - `request`, `reason`, `last_status`, `proposed_by` and the raw `status` are always dropped.
    - `projection: v1|v1-partial` marks loss.
    - Applying it only to the Claude instance needs a per-instance mode, because the hermes contract
      would otherwise change.
  - **D-11 conditions** are split into required decisions (a–f), implementation and operational checks.
    The projection (c) is a precondition for admitting Claude. Tests X-1 to X-10 are specified on
    synthetic data.
  - **Separate audit issue:** hermes already receives the raw text.
  - No code changed and no decision status changed.
  - **Tests:** `build_proposal_status.py --check` OK; lifecycle + history fragments + env passthrough
    + dockerignore + local-only guard 671 passed; release gate `--check-only` PASS.
- **Implementation readiness pass, 2026-10-13** (design §4.13–§4.15; no repository code changed):
  - **§4.13: scheduler read response contract v1.** It replaces the §4.12.5 draft.
    - Fields, types and closed lists, with each enum read from source.
    - The `outcome` rules for every action/status pair the source emits. `skipped` has a single status,
      `skipped_not_active`; the other `skipped_*` strings are summaries of `fired` events.
    - `schedules` maps `last_status` to `last_outcome`. A removal event cannot carry the raw text back.
    - The meaning of `v1-partial`.
    - A draft JSON Schema.
    - The difference between minimising the response and minimising the ledger.
    - Tests X-0 to X-10 in implementable form.
  - **Contract check outside the repository.** A throwaway reference projection, the schema and the
    synthetic tests were run in the session scratchpad from the worktree root, where `runtime`
    resolves. Result: 10 passed, including a check that the contract's lists equal the source
    constants. With a raw-`status` leak injected into the projection, 8 of the 10 failed: the two
    that passed do not use the event projection. This is evidence that the spec is implementable and
    that its tests detect a leak. It is not an implementation.
  - **§4.14: D-11 decision packet.** Decisions a to f, each with options, trade-offs, dependencies,
    files to change, tests, operational checks, a recommendation and an alternative. The recommended
    combination is a1 + b3 + c1 + d2 + e2 (separately) + f2. f2 rests on source showing no lock
    or write in the six reads Claude needs.
  - **§4.15: remaining items.** A standard table of the CLI-versus-door differences for W-11 and W-12.
    The record-based versus process-state judgement for W-13. W-30 unchanged. The fixed-pattern
    specification for D-16: P1 to P8, verdict rules and audit fields. Its regexes were checked against
    banners rendered with the source's own `describe_admission`, outside the repository.
  - No decision status or classification changed. The E items remain W-11 to W-13.
  - **Tests:** `build_proposal_status.py --check` OK; lifecycle + history fragments + env passthrough
    + dockerignore + local-only guard 671 passed; release gate `--check-only` PASS.
  - **Six-read write probe (D-11 f2), outside the repository.** The session runs as root, so the probe
    intercepted every write-capable call (write-mode `open`, write-flag `os.open`, `mkdir`, `rename`,
    `replace`, `unlink`, `touch`) and socket `connect` in process. The state was synthetic on
    `tmp_path`: one schedule, one event and a copy of the committed policy file. The six reads f2
    keeps all returned `ok` with zero write attempts and zero connects, while `tasks` and `history`
    did attempt their lock-file write. Limit: data-dependent paths and other write channels such as
    sqlite or mmap were not covered, so a real `:ro` mount check stays with C4 and C6. 8 passed.
  - **Hermes shim.** The repository's hermes read shim calls `schedules` and `scheduler_events`
    without `with_data` (`integrations/hermes/mcp/read_bridge_mcp.py:74-86,161-173`). The hermes
    model therefore sees only the 60-character-clipped `reply`; the raw `data` stops at the shim
    process. This narrows the hermes audit issue and makes option c2 less disruptive than assumed.
    Whether the installed shim matches the repository copy is NOT_VERIFIED.
  - **W-30 change specification for C5** (document only; the script is unchanged). A second root
    variable defaults to today's value, so behaviour before the split is identical.
    - The transcripts selection and checks, and the Claude config members in core, move to the
      Claude root. Archive member paths stay the same, so the restore script still fits.
    - The credential-file check applies to both roots.
    - Four synthetic-directory tests are specified.
- **Consistency review, 2026-10-13** (documents only):
  - **Contradictions fixed:**
    - §4.11 said B 5 and §4.12.1 said B 4, both for the same six-entry list. The tally is B 6.
    - Three passages could read as if response projection (c) normalised the ledger (e): §4.14 c's
      dependency line, §4.14 e1, and the e row in §4.12.5. They now say c changes only what leaves
      the door and e only what is stored.
    - The f row in §4.12.5 said "read-list reduction instead of `:ro`". §4.14 f2 is "reduce to six
      and mount `:ro`".
  - **Test specification:**
    - X-5 split into X-5a (no ledger, `SCHEDULER_LEDGER_UNAVAILABLE`), X-5b (a corrupt line, where
      whether the refusal text echoes the line is NOT_VERIFIED) and X-5c (a peer refused, reusing the
      existing socket-door test).
    - Added X-11 (the f2 subset) and X-12 (the count limit is preserved).
    - Decision-dependent tests are marked: X-6 on d, X-11 on f, X-10 on c1 versus c2.
    - The §4.12.5 draft table is marked as superseded by §4.13.7. No duplicates were found.
  - **Added:** §4.14.1, a per-decision review table (rationale, cost of the alternatives, effect on
    Claude's data access, required tests, operations-only checks, and what Thomas must decide). It
    keeps c, e and f separate. Also §4.14.2, the verification commands with expected results, and
    §4.14.3, operational checklist O-1 to O-9.
  - No decision status or classification changed.
  - **Tests:** `build_proposal_status.py --check` OK; lifecycle + history fragments + env passthrough
    + dockerignore + local-only guard 671 passed; read-bridge + factory child + policy clause 74
    passed; release gate `--check-only` PASS.
- **Pre-approval packet finalisation, 2026-10-13** (documents only):
  - **Git baseline.** The branch, HEAD and remote main are all `1b45ed57`. The two tracked changes were
    traced to their source in this record:
    - the readiness document carries the 2026-10-12 Option A decision (§4.5, a status-line sentence,
      two §10 lines);
    - `STATUS.md` is generator output.
    The design document and this record are untracked. Nothing was reverted.
  - **Label clash resolved.** The access options (a) to (d) in §4.8 and §4.12.1 now map explicitly to
    a3, a4, a2 and a1 in §4.14, and the D-11 row says "(d) (= §4.14 a1)".
  - **§4.14.4:** each of a to f gets its key claims graded (VERIFIED, SOURCE_SUPPORTED, SCRATCH,
    INFERRED, OPS_REQUIRED, NOT_VERIFIED), its pre-approval prohibitions, and the code and settings it
    would change kept apart from the tests it would add. It states that approving c does not approve
    e or f.
  - **§4.14.5:** X-0 to X-12 and O-1 to O-9 by stage (specification only, synthetic outside the
    repository, needs repository implementation, needs operations). No test has run in the repository,
    and the existing regression results are not evidence for the new behaviour.
  - Cross-document check: the readiness pointers, status line, tally and test counts agree.
  - No decision status changed.
  - **Tests:** `build_proposal_status.py --check` OK; lifecycle + history fragments + env passthrough
    + dockerignore + local-only guard 671 passed; release gate `--check-only` PASS.
- **Final review, exposure paths and approval checklists, 2026-10-13** (documents only):
  - **§4.14.6.** Each recommendation reads the same in all three places it appears. The minimum set
    that would admit Claude is c1 + a1: c is a precondition, and a2 leaves nothing to admit. d2 and f2
    narrow audit and access, and rejecting them does not block admission. b does not block it either.
  - **§4.14.7.** e2's exposure paths are enumerated:

    | Path | What it is |
    |---|---|
    | F0 | The child result spool file, deleted after collection |
    | F1 | The ledger. Past rows stay: it is append-only and every row carries a hash |
    | F2 | `last_status`, the `schedules` read and the operator CLI |
    | F3 | The operator alert, which leaves the host |
    | F4 | Scheduler stderr |
    | F5 | The encrypted daily core backup, which leaves the host |
    | F6 | Hermes |

    Still to decide under e2: where the diagnostic record lives, whether the alert keeps the message,
    and no retroactive rewrite.
  - **§4.14.8.** Separate approval checklists CL-c1, CL-a1, CL-d2, CL-f2, CL-e2 and CL-b3. Each has
    the decision, the implementation approval, the repository tests and the operational checks. All
    boxes are empty.
  - **§4.14.9.** The hold until decisions are taken is restated.
  - No decision status changed.
  - **Tests:** `build_proposal_status.py --check` OK; lifecycle + history fragments + env passthrough
    + dockerignore + local-only guard 671 passed; release gate `--check-only` PASS.
- **Approval Gate Matrix, 2026-10-13** (design §4.16; documents only):
  - **Baseline.** The values match the previous report: HEAD and remote main `1b45ed57`, two tracked
    changes of known origin, two untracked files, 16 of 16 decisions undecided, three E rows.
  - **§4.16.1, one row per decision.**
    - Rows: D-11 a to f, e-1 to e-4 (e-4 is an implementation condition, not a decision), D-1, D-3,
      D-2, D-4, D-10, D-16, the two D-14 rows and W-30.
    - Each row has the 13 fields: options, recommendation (not approval), evidence grade,
      dependencies, effect on access and security, implementation scope, required repository tests
      with run state, operational checks, approval state, next permitted step and blocker.
    - No new status value: the state stays `미결정`, and the test stages and results reuse §4.14.5
      and §4.6. The requested labels are mapped onto them; REPO_TESTED and OPERATIONAL_TESTED have
      zero entries.
    - A gate sequence G1 to G7 is stated with no skipping. Claude's read-bridge access is marked
      BLOCKED until c and a, and d and f if chosen, pass G1 to G6.
  - **§4.16.2–§4.16.3.** X and O items are linked to the decisions they support. Sub-controls for a,
    d and f are tracked separately: UID and GID, socket, allowlist, actor, projection, audit location,
    fields, retention, failure behaviour, list reduction, lock handling and `:ro`.
  - **§4.14.7** gains "remaining risk" and "evidence needed" columns, and spool residue (e-4). Source
    shows the orphan sweep deletes a result unread but keys on meta files, so a meta-less result could
    linger; whether that can happen is NOT_VERIFIED.
  - **Fix:** D-2's grade read "VERIFIED (design)", which is now DESIGN_ASSUMPTION.
  - **ID check:** every X, O and D id in §4.16 is defined.
  - No decision status changed.
  - **Tests:** `build_proposal_status.py --check` OK; lifecycle + history fragments + env passthrough
    + dockerignore + local-only guard 671 passed; release gate `--check-only` PASS.
- **Recommendations accepted in bulk, and repository implementation, 2026-10-13** (§4.17; nothing committed):
  - **Thomas, 04:02Z:** "권고대로 진행해주고 3시간정도 자리를 비워야되서 결정이 필요한 것들을 수집해놓고 순서대로 작업을
    진행해줘".
    - Recorded as G1 for the items the previous report asked Thomas to decide (corrected 2026-10-15 to
      PROVISIONALLY_ACCEPTED: a one-sentence bulk acceptance, item-by-item confirmation pending): D-11 a1, b3, c1, d2, f2
      and e2 (with e-1 to e-3 as recommended), D-1 = alternative C, D-3 scoped to §4.3.
    - Recommended values outside that list stay pending.
    - G2 for repository-only implementation is a reading of "진행해줘", not a separate approval.
      The revert command is in §4.17.4.
  - **Built, every read-door option off by default:**
    - `store_reads`: projection v1, with an in-code JSON Schema gate (`REPLY_SCHEMA_INVALID`).
    - `read_bridge`: `projection`, `allowed_reads` (with `_READS` unchanged), the mandatory-allowlist
      option and the request log. The log records the peer uid seen by `authorize_peer`, and a failed
      write withholds the reply.
    - `read_bridge_cli`: four flags. The default banner is unchanged.
    - `scheduler` (e2): a factory child's failure status keeps the exception type only, the message
      goes to `diagnostics/factory_child_errors.jsonl`, and meta-less leftovers are swept unread.
  - **Tests:**
    - Specification tests X-0 to X-12, 23 cases. With a leak injected, 6 fail.
    - e2 tests, 3 tests with 8 cases. With the old format restored, 5 fail.
    - Read-door regressions 968 passed; scheduler, data review and cycle 306 passed.
    - Full suite, run #1: 10,948 passed and 2 failed, both diagnostic-index tests (a stale index, and
      `PROJECTION_UNKNOWN` raised from two modules). Fixed by making `store_reads` the single raiser
      and regenerating `docs/DIAGNOSTIC_CODE_INDEX.md`.
    - Full suite, run #2: 10,950 passed, 2 skipped, `TEST_RUN_EXIT=0`; release gate PASS.
    - X-5b is now verified: a corrupt line raises `LEDGER_UNREADABLE` with no line content.
  - **Not done:**
    - Compose: the a1 service, `:ro` and the log mount, since uid, gid and paths are pending.
    - Host and Docker steps, the runner, policy and `schemas/`.
    - Commit and PR.
    - Claude's read-bridge access stays BLOCKED.
  - **Pending:** 18 items are listed with recommended defaults (§4.17.5).
  - **Slip:** a `build_proposal_status.py` call once ran with the primary checkout as its working
    directory. That tree stayed clean (verified with `git status`).
- **Security review fixes, 2026-10-15** (§4.18; nothing committed):
  - **Why:** the static review found that the four Claude-door options were independent, and dropping
    `--projection` alone served raw `last_status` (R-01). It also found that refused peers never
    reached the request log, and the test named "…refused_and_logged…" asserted the opposite (R-02).
    The decision record had shown the 10-13 bulk acceptance as item-by-item decisions (S-1, S-2).
  - **Thomas, 2026-10-15:** adopt R-01 alternative (a), one profile; log `PEER_NOT_PERMITTED`,
    `PEER_CREDENTIALS_UNAVAILABLE`, decode failures and `BRIDGE_BUSY`; correct the wording. Scope is
    repository code, docs and synthetic tests. No operational, PR or merge authority, and no final
    approval of the 10-13 items.
  - **Profile:**
    - `read_bridge.PROFILES` holds one frozen `DoorProfile`. `apply_read` accepts only that object,
      by identity.
    - `open_door(profile=, request_log=)` refuses in each of these cases: an unknown profile, a
      missing log, an empty uid allowlist, a log given without a profile, or a log it cannot write
      before listening.
    - The CLI now has `--profile` and `--request-log` only.
  - **Audit:**
    - A `socket_door._Handler` subclass logs every answer in `_reply` before sending it.
    - The busy refusal is logged by the door.
    - Rows carry the kernel uid, a read name from `_READS` or `<not_a_read>`, and the outcome.
    - `socket_door` is unchanged. Connections that end with no answer (timeout, oversize frame,
      early hang-up) are still not logged.
  - **R-03:** traced the other four f2 reads. All answer raw:
    - `heartbeat` puts `"<type>: <message>"` in `detail`;
    - `runtime_status` carries `updated_by` and the operator's `reason`;
    - `crypto_readiness` renders stored free text and may make an account GET if its container has
      the account env;
    - `holdings_status` passes snapshot keys through.

    The profile now serves only the two projected reads, and the four are held. Admitting them needs
    reply contracts, which is a pending decision.
  - **LOW fixes:** R-04, R-05, R-06 (`$` → `\Z`), R-09 and R-10.
  - **Reviewed only:** R-07 (`O_NOFOLLOW`) and R-08 (rotation). e2 is unchanged; E-01 to E-04 are
    listed as follow-ups.
  - **Wording:**
    - §4.14.8 decision boxes are unticked and marked PROVISIONALLY_ACCEPTED.
    - The §4.16, §4.17 and §8 states, and the code and test comments, are reworded.
    - The status vocabulary is in §4.18.1.
  - **Tests:** the new test file has 41 cases. Each of 16 injected faults makes a test fail.
- **Recommendations adopted, R-07 and the R-03 contracts, 2026-10-15 (second round)** (§4.19; nothing committed):
  - **Thomas, 2026-10-15:** adopted the following, scoped to the repository:
    - A: keep the two projected reads and hold the other four;
    - B: design reply contracts for the four;
    - C: keep the audit gap for connections that end with no answer;
    - D: `O_NOFOLLOW`;
    - E: document R-08 only;
    - F: keep E-01 to E-04 as follow-ups.

    The 10-13 items stay PROVISIONALLY_ACCEPTED. No host, compose, runner, commit or PR authority.
  - **R-07:**
    - The request log no longer uses `filelock`'s sidecar. The sidecar opened with `Path.open("a")`,
      which follows links, and also created the parent directory.
    - `filelock` is shared by 43 modules and 834 call sites, so it was left as it is.
    - The log is now opened with `O_NOFOLLOW|O_NONBLOCK|O_CLOEXEC`, and the descriptor is checked:
      a regular file, one link, owned by the euid, no group or other write.
    - It is locked with `flock` on the log's own descriptor and written with a single `os.write`.
    - A missing directory is refused rather than created.
    - Platforms without `O_NOFOLLOW` or `fcntl` refuse.
    - 13 synthetic tests were added. Each of 8 injected faults makes a test fail.
  - **R-03:**
    - Contracts v1 are in §4.19.2. Each is rebuilt from a typed source: heartbeat from its checks,
      runtime_status from `ControlState`, crypto_readiness from `readiness_data` (its builder must
      not be the account-reading one), and holdings_status from freshness fields only.
    - The reference implementation and 18 tests are in the scratchpad, not the repository.
    - The raw fixtures come from the real readers run over a synthetic `tmp_path` state. Crypto is
      a hand-built view, since its builder was not run.
    - Each of 8 injected leaks makes a test fail.
    - Nothing was added to the profile.
  - **Follow-up for the PR:** the win32 skip ceiling (258) needs remeasuring on Windows CI.
- **Fixed error text, and the heartbeat and runtime_status contracts, 2026-10-15 (third round)** (§4.20;
  nothing committed):
  - **Thomas, 2026-10-15:** adopted R3-5, R3-1 and R3-2 for the repository and synthetic tests only.
    R3-3 and R3-4 stay held, R-08 is undecided, and E-01 to E-04 stay follow-ups. No profile
    registration, operations, commit or PR.
  - **R3-5:**
    - Why: a profiled door used to send `str(exc)` and `exc.data` for typed refusals. A ledger read
      error carried the absolute path (`jsonl.py:324`), shown in a synthetic test. Unexpected errors
      carried the pid in `error_ref`, refusals carried the peer uid and the parser text, and the busy
      text carried the door's ceiling.
    - Now `_AuditedHandler._reply` rebuilds every refusal from `PROFILE_ERROR_TEXT` (11 codes with
      fixed text). Any other code becomes `READ_UNAVAILABLE`.
    - Only well-formed `proto`, `client_id` and `request_id` are echoed.
    - The audit log keeps the real code.
    - If the rebuild fails, the constant `BRIDGE_ERROR` is sent.
    - `socket_door` and the hermes path are unchanged.
    - 15 tests. Each of 5 injected faults makes a test fail.
  - **R3-1:** `heartbeat.inspect_heartbeat` returns `(report, failure_code)` and leaves
    `check_heartbeat`'s output unchanged. `store_reads.project_heartbeat_check` and
    `read_heartbeat(projection=)` implement the contract with a closed schema.
  - **R3-2:** `store_reads.read_runtime_status` projects `ControlStore.load()` (read-only), never
    the `/status` text. `apply_read` routes both reads to their contracts for a projecting door. No
    profile carries them.
  - **Tests:** 14, and each of 10 injected faults makes a test fail.
  - **R-07 review:** a short or failed write left torn bytes that the next row was appended to. It is
    now truncated back under the lock (2 tests, injection-checked).
  - **Windows:** 36 of the 71 tests in the new projection test file skip there (static count). The
    win32 ceiling is unchanged and NOT_VERIFIED.
- **PR-B merged; this record synchronized, 2026-10-16** (§4.22; this document is still uncommitted):
  - **Thomas, 2026-10-16:** three separate approvals:
    - confirmed D-11 a1 (code part), c1, d2 and f2 for the repository implementation;
    - approved creating PR-B;
    - approved squash-merging PR #1223 at a named HEAD.
  - **#1223:** squash-merged at 03:12:35Z as `8979e702`. Its parent is `1b45ed57`, and its tree
    equals the approved HEAD `47d84eeb` (9 files, +1,996/−35).
  - **First HEAD:** `96080873` failed 9 Windows cases because two tests that also run on Windows
    called `os.getuid()`. They now use a synthetic uid; no skip was added.
  - **CI:** the required 5 checks passed on the PR HEAD and again on main. Windows: 10,712 passed,
    294 skipped (at the ceiling, 258 + 36 measured). Ubuntu: 11,006 passed, 0 skipped. CPython 3.12.
  - **Not done:** no deploy, compose, account, socket or log directory. Claude's read door stays
    BLOCKED and operations stay on HOLD.
  - **Still open:** D-1, D-3, D-11 b3 and e2 stay PROVISIONALLY_ACCEPTED. PR-C and PR-A are not
    created.
  - **Decision material for PR-C:** whether the crypto-pause "bug fixes" exemption applies, and E-01
    options. E1-a is recommended now: Thomas reads the diagnostics file on the host.
- **PR-C security fix approved and implemented in the repository, 2026-10-16** (§4.23; nothing committed):
  - **Thomas, 2026-10-16:**
    - D-11 e2 EXPLICITLY_APPROVED, for the repository security implementation only.
    - The crypto research pause exemption APPROVED for security bug fixes only. No strategy,
      signal, SL/TP, risk-gate or stage change, and no orders.
    - E-01 approved as E1-a, documentation only: an operator-only host read procedure.
    - E-02 to E-04 approved as repository security fixes only.
  - **E-03:** only an ASCII class name before the child's `": "` separator becomes the status type.
    Anything else is `failed:FACTORY_CHILD:UNKNOWN`, except the parent's own `CORRUPT_RESULT`.
  - **E-02:** the broad catch lives only in `_record_factory_child_error`.
    - A failed diagnostics write logs the fixed code `FACTORY_CHILD_DIAGNOSTICS_UNAVAILABLE` with the
      occurrence ids, never the message. The occurrence stays failed and keeps its terminal event.
    - Rows are written as ensure_ascii JSON, and a torn write is cut back.
  - **E-04:** the directory is created 0700 and the file 0600.
    - Both are opened with no-follow, the file relative to the directory's descriptor, and checked on
      the descriptor: type, owner, link count (file), no group or other bits.
    - Nothing existing is chmod-ed.
    - A platform without O_NOFOLLOW, O_DIRECTORY or dir_fd refuses.
  - **Tests:**
    - The factory child file has 45 cases (it had 14). Each of 16 injected faults makes a test fail.
    - Regression set: 814 passed.
    - Simulated Windows: 33 passed and 12 skipped; real Windows NOT_VERIFIED, predicted win32
      294 → 306.
  - **Not created:** PR-C and PR-A. No operational change.
- **PR-C merged; the design record prepared as PR-A, 2026-10-16** (§4.24):
  - **#1224:** Thomas approved the squash merge at a named HEAD. It merged at 04:32:28Z as `0b6fd69c`
    (parent `8979e702`, tree equal to the approved HEAD `c5e641fe`, 5 files).
  - **CI:** the required 5 checks passed on the PR HEAD and on main. Windows: 10,738 passed, 306
    skipped, at the ceiling (294 + 12 measured). Ubuntu: 11,044 passed, 0 skipped. CPython 3.12.
  - **PR-A is cut from main `0b6fd69c` in its own worktree.** It carries only the four documents: this
    design record, the readiness doc's Option A record, this history and STATUS. The old-version code
    in the original Option C worktree is not part of it.
  - **§4.24 records:**
    - both merges and the final decision table;
    - explicit: D-11 a1/c1/d2/f2/e2 (repository), the crypto exemption (security fixes only), E-01
      E1-a (documentation only), E-02 to E-04 (code merged);
    - provisional: D-1, D-3 and D-11 b3;
    - what is still undecided: real OS separation, operating the Claude door, R-08, diagnostics-file
      isolation, deploys.
  - **No deploy.** Operations stay on HOLD and the Claude read-bridge stays BLOCKED.
  - **Reading earlier entries:** phrases like "nothing committed", "not created" and "uncommitted" are
    true as of each entry's own date. The current state is §4.24.
  - **Principles:** CODE_MERGED != OPERATION_APPROVED; CI_PASS != DEPLOYMENT_APPROVED.

# Deploy preflight: a fact it cannot read is never a PASS

- **Why** (Thomas 2026-10-12):
  - Before the PR-D candidate run, Thomas asked whether the deploy preflight can run safely under
    the LOCAL_ONLY guard. The question covered its `.env`-interpolated compose config, the scheduler
    state files it reads, and its Docker reads.
  - He also asked for its checks to be classified by what data they need.
  - The review read only code, tests, the guard rules and the compose file. The preflight was not
    run, and no Docker command was run.
- **Guard:** no conflict.
  - The guard blocks the holdings state directory and holdings file names only. It names
    `schedules.jsonl` as allowed, and `runtime_ledger/scheduler_events.jsonl` is outside its rules.
  - A holdings fire's status line holds state words, exception names and reason codes, never
    amounts. The preflight prints only kind, a schedule-id suffix and a time from it.
  - The Docker reads and the state reads are still production evidence, so running the preflight
    stays an approval of its own.
- **What it found:** five places where an unknown passed or ended in a WARN that let the run end
  `DEPLOY_PREFLIGHT=OK`.
  - A failed `docker images` read as "no tags", so `tags` printed PASS "free".
  - Unreadable scheduler state was a WARN on both runs.
  - An unpaired `started` with an unparseable time was ignored.
  - Unreadable compose labels were a WARN.
  - A gh or compose answer that was not the expected JSON crashed with a traceback.
- **What changed:**
  - Each unknown is now a STOP where its answer decides the step:
    - the image list in every check that uses it;
    - at `--promote`, the scheduler state, the unparseable start and the labels.
  - The first run keeps the WARNs it had, including a factory fire due on the first run.
  - Bad JSON is a STOP that never echoes the output, which holds `.env` values.
  - No check was removed, no STOP became a WARN, and no new command runs.
  - The deploy skill is unchanged, and nothing in it became false.
- **Tests:**
  - The fake host now carries a synthetic `.env` value in the compose output. It is checked absent
    from stdout and stderr on the PASS path, a bad bind, a missing state mount and unparseable output.
  - Each unknown has a test, including a test that `main()` exits 1 at promote.
  - A further test pins the full check list of both runs.
  - Fifteen of the new tests fail on the old code.
- **Second round** (independent review, Thomas decision 2026-10-12):
  - A label mismatch (project or env file) was still only a WARN, so a promote could end OK while
    compose up would act on another stack. It is now a STOP at `--promote`, and still a WARN first.
  - A failed `docker ps` became an empty list and then a WARN. Failure and an empty list are now told
    apart.
  - At `--promote`, any difference between the running `thomas-` containers and the tree's compose
    services is a STOP: a missing service, or a `thomas-` container the file no longer names. That
    includes hermes, since `up -d` would start it. Other projects' containers are not compared.
  - Nine of the new tests fail on the first-round head `907faf64`.
- **Third round** (Thomas decision 2026-10-12):
  - An enabled `crypto_factory` schedule whose `next_run_at` was missing, null, empty or unparseable
    was skipped, so a promote could pass while that fire was due. It is now a WARN on the first run
    and a STOP at `--promote`. Only the schedule id's suffix is named, never the value.
  - `_parse_time` also returns None for a time without a zone. Such a value used to raise
    `TypeError` against the aware `now` and crash the run. A zoneless `started` time is now the
    unreadable-start case.
  - Disabled schedules, other kinds and the latest-record rule are unchanged.
  - Twelve of the new tests fail on `85379e41`.
- **Secret-free path** (SECRET_FREE_PARTIAL_VALIDATION_ONLY):
  - Tests now pin the raw compose bind contract without reading `.env`:
    - 26 sources, each `${PATH_VAR:-default}`, over six path variables and no credential;
    - the eight runtime services take the state root from `THOMAS_STATE_DIR`;
    - an unset variable falls inside the tree, where the volume check stops.
  - What the six variables resolve to lives only in `.env`, and compose cannot interpolate a subset.
    So the deploy-time volume check is still required, and the execution decision stays open.
- **Still open:** who runs the operational preflight, given that it handles the interpolated `.env`
  in memory. This is PREFLIGHT_EXECUTION_POLICY_DECISION_REQUIRED and Thomas decides
  (`HOLDINGS_C7_PR_D_DEPLOY_READINESS_V0.1.md` §4.4).

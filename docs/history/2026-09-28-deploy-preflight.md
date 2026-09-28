# A read-only deploy preflight, and CLAUDE.md's compose line matched to how the stack is run

- **Why:** the deploy procedure's checks read the host, and each depended on a session reading
  it at the right moment.
  - Is the PR merged? #890 sat OPEN with every check green for four days while a note called
    it fixed.
  - Does `latest` name the running image? Another session may have built without deploying.
  - Did another session take this PR's tags? Two sessions built candidate-811 at once
    (2026-08-30).
  - Did anything redeploy between the tag and the promote?
  - Also, CLAUDE.md said `docker compose up -d`, which this host does not run. The running
    containers' compose labels read `-p thomas_agent`,
    `--env-file /root/thomas_agent/.env` and `-f /root/deploy-996/docker-compose.yml`. A bare
    `up -d` in the primary checkout (30 commits behind origin/main on 2026-09-28) would compose
    that branch's file.
- **What changed:**
  - New `scripts/ops/deploy_preflight.py <PR#> [--promote --tree <tmp>]`, which prints
    PASS/WARN/STOP and exits 1 on any STOP.
    - The first run checks merge state, that this PR's tags are free, and which tag names the
      running image.
    - The `--promote` run checks that `rollback-pre-<N>` still names the running image and that
      `candidate-<N>` exists.
    - Both read the running stack's compose labels and the primary checkout's lag.
    - `--tree` resolves that tree's compose file. It stops on any bind source that is relative
      or inside the tree, and it lists services that differ from the running containers.
  - CLAUDE.md's code block calls the preflight twice and composes with the explicit flags.
  - A `deploy` skill walks the steps in order.
- **Tests:** `tests/test_ops_deploy_preflight.py`, 18 cases against a fake host, platform-neutral
  (no skip ceiling change). Five hand mutations were each caught: ignoring a redeploy in the
  promote window, accepting a relative bind, ignoring taken tags, dropping the rollback-tag
  lookup, and a relative bind beside a good state mount. Run against the live host, it gave STOP
  for #996 (already deployed: its tags exist) and OK for #1005.
- **Deliberately not done:** it tags, builds and composes nothing. It prints only verdict lines,
  never `docker compose config`, which carries `.env` values.

# C7 PR-D deployment readiness: the global guard is verified, the running image and its eight services are identified

- **What this PR changes** (Thomas 2026-10-12, docs only): `HOLDINGS_C7_PR_D_DEPLOY_READINESS_V0.1.md` takes
  the evidence gathered since #1218. `STATUS.md` is regenerated.
- **Global Guard → GLOBAL_GUARD_VERIFIED for installation and hook wiring** (Thomas's operator evidence):
  - The installed guard, the kit copy and main's guard all have sha256 `88addc9d…690fc86`.
  - `refresh_guard.py check` exit 0, and `test_guard.py` ALL PASS with every `gap?` reading deny.
  - In a new session, three probes were refused: cat by the Bash hook, Read by the permission deny rule,
    Monitor by the hook.
  - The told-name DENY and snapshot ALLOW rest on byte identity with main and main's CI tests
    (TOLD_FILENAME_PROTECTION_VERIFIED_BY_CODE_IDENTITY_AND_CI). That is indirect.
  - Gate 2 lists a direct synthetic DENY/ALLOW. That was not run on the installed guard
    (DIRECT_TOLD_FILENAME_PROBE_NOT_PERFORMED), and no decision accepts the indirect evidence instead.
  - So Gate 2 is partly met. The condition is kept, not relaxed. Whether indirect evidence suffices is
    Thomas's decision, and a direct probe needs its own approval.
  - The README's `cp -p … .prev` was not used, because it overwrites the old backup. The old guard went to a
    new name, `.pre-1217`, and `.prev` stayed as it was.
- **Running image → IDENTIFIED_METADATA_ONLY** (read-only, 2026-10-12T06:15Z; only chosen `docker ps`,
  `docker inspect` and `docker image ls` fields, no env, no `exec`/`run`):
  - All eight runtime containers run `67d9b622d648` = `candidate-1210` = `latest`.
  - They all started together at 2026-10-10T10:13:44Z, are healthy, and have never restarted.
  - They share one compose project, `thomas_agent`, with `/root/deploy-1210/docker-compose.yml` and
    `/root/thomas_agent/.env`.
  - `hermes` runs on its own image.
  - The image was created at 10:12:45Z, after `b6d4bf30` (09:55:29Z) and before the next main commit
    (16:27:04Z). That fits a build from `b6d4bf30`, but timing is not content: the code inside the image was
    not checked.
- **Two baselines** (review correction, same PR):
  - `1f328ffd` (#1217) is the design baseline, kept as the historical reference for the scope analysis.
  - `d97db39c` (#1218) is this update's review baseline.
  - Neither is the running image's code version; the image has no SHA label.
- **Still HOLD:**
  - Rollback is ROLLBACK_NOT_VERIFIED. The source is identified (the running image, which `latest` names),
    but no `rollback-pre-<N>` tag exists yet and no restore was run.
  - The in-image validation path is unresolved.
  - The candidate tag is undecided.
  - Gate 2's direct told-name check is not performed.
  - There is no deploy approval.

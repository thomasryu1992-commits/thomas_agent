# I0.5.1 CI Validation Boundary v0.1 — Rev2

**Status:** `Review-Only Safety Boundary`

The GitHub Actions workflow runs the Repository Full Gate on Windows and Linux with `--check-only`. Runtime source and the workflow definition are included in the Gate-owned source fingerprint and normalized to LF for cross-platform reproducibility.

It may:

- install pinned validation dependencies;
- read Repository source;
- run validators and isolated self-tests;
- fail a Pull Request or push check;
- expose non-secret run/job metadata through the GitHub API.

It may not:

- write Release Gate evidence during CI;
- create Releases;
- approve or activate Core;
- activate Runtime;
- enable Tool, Program, or Executor paths;
- use Repository write permissions;
- read application secrets;
- perform external or financial actions.

A workflow file in the Repository is not proof that a workflow run passed. A free-form `PASS` value is not proof. Readiness requires `github_ci_evidence.v0.1`, collected from the live GitHub API and bound to the exact local Repository, HEAD Commit, workflow SHA-256, Ubuntu job, and Windows job.

The canonical collection command is:

```powershell
python scripts/collect_github_ci_evidence.py `
  --run-id <RUN_ID>
```

The collector relies on existing `gh` authentication but must not read, print, or store credential values.

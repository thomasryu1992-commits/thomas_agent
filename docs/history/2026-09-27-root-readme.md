# A root README for the public repository

- **What was delivered:** `README.md` at the repository root. It covers what the system is, the safety
  boundary (fail-closed, capabilities off in code, secrets metadata-only, the execution-stage ladder),
  the current stage with the commands that read it on the machine, the top-level layout, how to run
  the tests and gate, and where to read next.
- **Why:** the repository is public and had no root README. A visitor landed on a directory listing
  and could not tell what the system does, whether it trades real money, or where to start.
- **Deliberately not done:** the README states no authority and duplicates no rule. The stage line is
  dated and points at `register_execution_stage --show`, because the stage is a runtime record and
  the repository cannot know it. `docs/README.md` stays the documentation index.

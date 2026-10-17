# Option C C4: measuring what CR-10, CR-11 and CR-19 need

**What changed:**
- `scripts/ops/deploy_runner_c4.py`: three measurements and an `rtt` subcommand;
- the workflow: `--measure-repo "$GITHUB_WORKSPACE"`, timeout 20 → 35 minutes;
- one test;
- the win32 skip ceiling 572 → 573.

**Approval:** `승인: CR-10·11·19 측정을 위한 C4 하네스 확장(실제 Dockerfile 빌드 시간 측정) PR 생성(병합·운영 제외) (Thomas 2026-10-17)`.

**Why.**
- CR-19 (each action's deadline) and CR-10 (the approval's maximum lifetime, which must be at least the
  longest deadline) were left to "C4 measurements".
- C4 so far builds only a `FROM scratch` image, which says nothing about the real one.
- CR-11 (clock tolerance) needs the clock's error bounds on a real host.

**What is measured, on the runner, as INFO rows — values, never verdicts.**
- **CR-19, the real build.** The repository's own Dockerfile, from a copy of the checkout, built by root
  to throwaway tags that are removed afterwards. Nothing of it reaches the actor.
  - Cold: `--no-cache`; the base image pulled.
  - Warm: `--pull=false` after one runtime file changes. That is what a typical `prepare_candidate` does
    on a host that has built before.
- **CR-19, the round trip.** Five `inspect_candidate` calls through the gate, 1.2 s apart (the actor's
  rate limit is 1 s): median and maximum milliseconds as the caller sees them.
- **CR-11, the clock.** The runner kernel's `maxerror` and `esterror` (µs) from `adjtimex`.

**What this does not do.**
- It decides no value. The results go into a decision packet for CR-10, CR-11 and CR-19.
- A GitHub runner is not the production host. Its build time is an input, not the production figure.
- No merge, no install, nothing on the production host.

# Option C C4: the separation tests as a workflow on a throwaway GitHub-hosted runner

**What changed:**
- `.github/workflows/option-c-c4-separation.yml`;
- `scripts/ops/deploy_runner_c4.py` (the harness);
- `tests/test_deploy_runner_c4_harness.py`;
- the win32 skip ceiling 559 → 562 (three new tests need POSIX opens or AF_UNIX).

**Decision and approval (Thomas 2026-10-17):**
- `결정: CR-8b Python 3.12.13, 인터프리터 출처 python-build-standalone x86_64(SHA256 설치 기록 고정, 없으면 재보고); D-8 C4 환경 GitHub Actions 임시 러너(무료 한도 안, 비필수 workflow — CI 변경은 별도 승인)`.
- `승인: C4 시험 workflow 구현 및 PR 생성(GitHub Actions 임시 러너, 비필수, Python 3.12.13 PBS SHA256 고정, N-1~N-14·누출 검사; 병합·운영 제외)`.

**Why a workflow.** C4 must test accounts, file modes, socket peers and systemd units on a kernel that is not
the project's host (C4 plan §1). Creating accounts or units on the host is a host privilege change that no
approval covers. A GitHub-hosted runner is a fresh VM, root via sudo, systemd as PID 1, Docker installed,
and is discarded after the job.

**The interpreter (CR-8b).**
- File: `cpython-3.12.13+20260807-x86_64-unknown-linux-gnu-install_only.tar.gz`.
- SHA256: `5bd6f36fd7ef02b909234c94dca9994ef0da06ace3bc3cece4fe27870e9cdbbe`. Checked two ways on 2026-10-17:
  the release's published `SHA256SUMS` line, and `sha256sum` of the downloaded file. They agree.
- The workflow checks it with `sha256sum -c` before `tar` opens it.

**What the job does.**
1. Builds the install at `/opt/thomas-deploy/<sha>`, root-owned and not group- or world-writable:
   - the interpreter;
   - a venv with `requirements-validation.lock`;
   - `runtime/`, `schemas/` and `scripts/` from `git archive HEAD`.
2. Runs the harness as root with that interpreter (`-I`). The harness then:
   - creates `claude-c4`, `thomas-deploy-gate` and `thomas-deploy-actor` with fixed ids;
   - writes the root-owned configs and synthetic trust files (an ed25519 key made on the runner);
   - writes a fake `.env` holding a random sentinel, and a synthetic state root;
   - writes the actor's store and the units with the C3b install tool (`genesis`, `render-units`);
   - runs `serve --check` as each account, then starts both units under systemd.
3. Tests, each probe running as the account it is about (`setpriv`):

| ID | What | Expected |
|---|---|---|
| N-1 | `claude-c4` reads the fake `.env` | denied |
| N-2 | `claude-c4` and G connect to the Docker socket | denied. X may connect (control) |
| N-3 | each account runs `sudo -n true` | refused |
| N-4 | each account creates a file in the install, the venv and the trees; `claude-c4` and G write into X's records | denied. X may write its own directory |
| N-5 | `claude-c4` and G read X's `/proc/<pid>/environ` and `ptrace` it; `claude-c4` does the same to G | denied |
| N-6 | `claude-c4` writes into the synthetic state root or reaches its holdings directory | denied |
| N-7–N-10, N-12 | frames sent by `claude-c4` through `gate.sock` | the codes the C3 synthetic tests give (below) |
| N-13 | services run `-I` with no `PYTHONPATH` and cwd at the install; modules planted by `claude-c4` stay unimported under `-I`; units restart with them present | as stated |
| N-14 | each account appends to the trust files, configs, the store and the unit files, or creates files in `/etc/thomas-deploy` or `/etc/systemd/system` | denied |
| Leak | the sentinel and the private-key bodies, searched in X's and G's records and both units' journals | 0 hits |

The frames and their expected codes:
- N-7:
  - an unknown verb: `VERB_NOT_PERMITTED`;
  - an extra argument key, or an extra frame key: `ARGUMENT_NOT_ACCEPTED`;
  - a path as the PR: `ARGUMENT_INVALID`.
- N-8:
  - `promote`: `OPERATION_BLOCKED_BY_POLICY`;
  - unsigned `prepare_candidate`: `APPROVAL_REQUIRED`;
  - a key that is not trusted: `SIGNATURE_INVALID`.
- N-9: an expired approval: `APPROVAL_EXPIRED`.
- N-10:
  - another PR or another commit: `APPROVAL_BINDING_MISMATCH`;
  - another environment: `APPROVAL_ENVIRONMENT_MISMATCH`.
- P-5: a valid approval: `CLOCK_UNTRUSTED`.
- N-12:
  - bytes that are not a frame: `MALFORMED_REQUEST`;
  - a frame over 8,192 bytes: closed with no reply (the `FRAME_TOO_LARGE` gap, Option C §4.25.3);
  - the actor frozen with SIGSTOP: `ACTOR_UNAVAILABLE` at the gate's deadline;
  - the consumption store made unreadable: the actor does not come up.

Two further checks:
- Docker's image list must be identical before and after all of these.
- The store must still hold only its genesis row.

**What it cannot test, reported as such.** CLOCK-SOURCE is undecided, so the actor checks the clock right
after the approval and before the lock, the measurement and the spend. Every validly signed request
therefore stops at `CLOCK_UNTRUSTED` (C4 plan P-5). These are reported BLOCKED:
- the measured half of N-10 (tree and running image);
- N-11 (reuse of a spent approval);
- N-12's Docker failure and timeout after the spend.

N-12's "actor reply outside its schema" needs a modified actor and is NOT_TESTED here; the C3a-3 synthetic
tests cover it. N-15 and the C2X/N1X/GAPX items are outside this approval's N-1–N-14 scope.

**A finding before the first run.** The C3b units start `python -I -m runtime…` with
`WorkingDirectory=<install>`. Under `-I`, Python 3.12 does not put the working directory on `sys.path`.
The repository has no package metadata, so a unit rendered exactly as C3b ships it cannot import `runtime`.

The workflow puts the install's tree on the path with a root-owned `.pth` in the install's own
site-packages. That also makes N-13 meaningful: imports come only from the install. The install tool
does not write this file today. Whether it should is for C5.

**The harness refuses to run elsewhere.** `run` needs all of these, or it exits 3 before starting any
process:
- `--throwaway-runner`;
- `GITHUB_ACTIONS=true` and `RUNNER_ENVIRONMENT=github-hosted`;
- root;
- systemd as PID 1.

The suite pins that refusal.

**Tests here** (`tests/test_deploy_runner_c4_harness.py`, 9):
- every frame gets its expected code from the real gate and actor, in-process, with a real `ssh-keygen`
  and throwaway keys. Docker is never called and nothing is spent. Changing one expected code fails the
  test;
- `run` refuses off the runner;
- probes report kinds and leave nothing behind;
- `send` returns codes only;
- the workflow keeps the interpreter pinned and checked before extraction, uses no secret, keeps
  `contents: read` and `persist-credentials: false`, and pins every action to a commit.

**Not done.**
- No run results are claimed here. The first run's results get their own record.
- The workflow is not a required check, and branch protection is untouched (Q14).
- Nothing ran on the project's host: no account, unit, Docker, `.env` or key.
- Merge is not included.

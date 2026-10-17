# Option C: the actor's clock source is the kernel (CLOCK-SOURCE = A), and C4 reaches past the clock

**What changed:**
- `runtime/mvp_runtime/deploy_runner/clock_source.py` (new);
- `serve.py` (the served actor uses it);
- `scripts/ops/deploy_runner_c4.py` (the effect tests);
- tests;
- the C4 plan and the Option C record;
- the win32 skip ceiling 565 → 566.

**Decision and approval** (Thomas 2026-10-17, "권고안대로 승인할게"):
- `결정: CLOCK-SOURCE = A(커널 adjtimex, STA_UNSYNC 해제 + maxerror ≤ 16초), DESIGN_ONLY`.
- `승인: CLOCK-SOURCE A 구현(C3 코드 PR, serve 연결·합성 시험) 및 C4 하네스 확장(합성 트리·이미지·8개 컨테이너, N-10·N-11·N-12 재시험) PR 생성(병합·운영 설치 제외)`.

Both are in one PR, in two commits. The C4 workflow runs on a change under `deploy_runner/`, so one run
tests the source and the harness together.

**The source.** `kernel_sync_confirmed()` reads `adjtimex(2)` with `modes = 0` (a read). It confirms only
when all of these hold:
- the state is not `TIME_ERROR`;
- `STA_UNSYNC` is clear;
- `maxerror` ≤ 16 s.

Anything that cannot be read is "not confirmed":
- another platform;
- no C library;
- a failing call;
- a non-integer answer.

The kernel is where chrony and systemd-timesyncd both report. This host runs chrony, and the timesyncd
marker file the third option needed does not exist here; that is why that option was dropped. Nothing is
spawned and no bus is asked, so it works inside the actor's sandbox.

16 s is the kernel's own limit, past which it marks the clock unsynchronised by itself. The decision
packet first called it "systemd's" value; that was corrected before the decision. That limit is from
memory and was not checked against kernel source in this session. CR-10, CR-11 and CR-19 (the approval
window numbers) are still deferred.

**What it changes.**
- `serve.build_actor` now passes the kernel source, so on a synchronised host a validly signed
  `prepare_candidate` goes on to the lock, the measurement and the spend.
- Before this, every such request ended `CLOCK_UNTRUSTED`.
- No service runs the actor on the production host. Its install is C5 and C6, and not approved.

**C4 past the clock.**
- On the runner the harness now builds:
  - a `FROM scratch` image tagged `latest` (no pull);
  - the eight service names as `created` containers of it, under the compose project;
  - two runner trees (root-owned, clean, `HEAD` = `origin/main`).
- It records the kernel's clock verdict.
- When the clock is confirmed, it sends:
  - **N-10 measured:** an approval for a commit the tree is not at, and one for another running image.
    Expected: `APPROVAL_BINDING_MISMATCH`, nothing spent, no tag.
  - **P-5:** a valid approval. Expected: OK. The rollback point is the running image and the candidate
    exists.
  - **N-11:**
    - the same request again replays;
    - the spent approval under a new request id answers `TAG_ALREADY_EXISTS` while its tags are there;
    - once root removes the tags, it answers `APPROVAL_ALREADY_CONSUMED`. The tag check comes before the
      consumption check in the actor's order, so both are pinned.
  - **N-12:** a build that fails after the spend. Expected: `FAILED_EFFECT_PARTIAL`, then
    `RUNNER_LATCHED`, one more consumption row.
- If the runner's kernel does not report synchronisation, those rows are BLOCKED, with that reason.
- The refusal frames stay refusals before the clock; the suite pins that.
- The gate's actor deadline in the harness config goes from 5 s to 60 s (a prepare builds an image).

**Install concerns found while writing it, for C5.**
- X runs `git` with only `PATH` and `LANG`. A root-owned tree is then "dubious ownership" unless the
  system git config names it a safe directory; the harness writes that.
- X runs `docker image build` with no `HOME`. Whether the CLI or BuildKit needs a writable home is
  NOT_VERIFIED until the run.

**What this PR's runs found (runs 39027923410 and 39028074862).**
- The kernel source confirmed the runner's clock (state 0, `STA_UNSYNC` clear, maxerror within the limit).
- The synthetic fixture came up.
- Both measured mismatches refused before the spend, with nothing spent and no tag.
- The valid approval was spent and the rollback point tagged. Then the build failed
  (`FAILED_EFFECT_PARTIAL`, audit cause `BUILD_FAILED`), and the actor latched, as designed.
- Re-run as X with the actor's exact environment (`PATH`, `LANG` only):
  - the tag succeeds;
  - the build fails with `ERROR: mkdir /nonexistent: permission denied`. Docker's BuildKit client needs a
    writable home, and X has none.
- **This is a defect in the runner as designed (C3a's fixed command environment against X's account), not
  in the harness.** Fixing it is a decision: the command environment, X's home, or the builder. It is
  reported and not fixed here.
- N-11 and N-12 stay BLOCKED until it is.

**The fix (BUILD-HOME = A).**

Thomas 2026-10-17:
- `결정: BUILD-HOME = A(실행기 명령 환경에 DOCKER_CONFIG=<X 기록 디렉터리>/docker-config 추가, 값은 root 소유 설정에서;
  DOCKER_HOST 등 그 밖의 DOCKER_*는 계속 차단), git safe.directory는 C5 설치 절차에 포함, DESIGN_ONLY`;
- `승인: BUILD-HOME A 구현을 #1240에 커밋으로 추가(합성 시험·결함 주입, C4 재실행으로 P-5·N-11·N-12 확인; 병합·운영 설치 제외)`.

What changed:
- `commands.run_command` takes `docker_config`. The environment is then exactly `PATH`, `LANG` and
  `DOCKER_CONFIG`. A path that is not absolute and plain (checked before `Path` folds `//`) starts nothing.
- `serve.build_actor` passes `<X's records directory>/docker-config`, from the root-owned configuration.
  X's records directory is X's own 0700 directory and the unit's only writable path, so the unit does not
  change.
- No code enumerates that directory, so the subdirectory does not disturb the startup check.
- Every other `DOCKER_*` stays out, `HOME` included.
- Fault injection: 6 of 6 caught (the variable missing, `DOCKER_HOST` leaking, the parent environment
  inherited, the path unchecked, serve unwired, a directory outside X's records).
- The git `safe.directory` entry belongs to the C5 install procedure; the harness writes it on the runner.
- win32 skip ceiling 566 → 570.

**Tests.**
- `tests/test_mvp_runtime_deploy_runner_clock_source.py`:
  - every kernel answer is held to its verdict with an injected `adjtimex` (read mode asserted);
  - the buffer size;
  - off-Linux;
  - one real call that must return a verdict.
- The service test that pinned "built unconfirmed" now pins the wiring: the source is asked at each
  request, and the default is the kernel source.

**Not done.** No merge, no install, nothing on the production host, no CR-10/11/19 values.

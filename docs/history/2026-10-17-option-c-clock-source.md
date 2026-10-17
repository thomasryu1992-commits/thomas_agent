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

**Tests.**
- `tests/test_mvp_runtime_deploy_runner_clock_source.py`:
  - every kernel answer is held to its verdict with an injected `adjtimex` (read mode asserted);
  - the buffer size;
  - off-Linux;
  - one real call that must return a verdict.
- The service test that pinned "built unconfirmed" now pins the wiring: the source is asked at each
  request, and the default is the kernel source.

**Not done.** No merge, no install, nothing on the production host, no CR-10/11/19 values.

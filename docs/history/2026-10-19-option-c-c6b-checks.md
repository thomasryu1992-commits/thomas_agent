# Option C C6b P-6: the b8 checks the `claude` account runs on itself after the switch

**What changed:**
- New `scripts/ops/c6b_checks.py`.
- New `tests/test_c6b_checks.py`: 24 synthetic cases. The module is POSIX-only, so the win32 skip ceiling goes from 609
  to 633.

**Approval** (Thomas 2026-10-19): `승인: C6b P-6 b8 시험 스크립트 코드 PR 생성(claude 계정용 읽기 전용 probe — N-1~N-6·N-13~N-15·
guard·read door·inspect, C4 하네스 probe 형식 재사용, 합성 시험; 병합·운영 실행 제외)`. The packet is
`docs/proposals/HOST_PRIVILEGE_SEPARATION_C6B_EXECUTION_V0.1.md`.

**What it does.** It runs in a Claude session that is already the `claude` account (b8, after b1). It refuses to
run as any other uid, because as root every refusal would be a false pass. It uses the C4 harness's probes
(`deploy_runner_c4.OPS`), which record the kind of answer, never content, against the production targets.

Must be refused:
- N-1: `.env` and its five copies;
- N-2: the Docker socket;
- N-3: sudo;
- N-4: the runner's install, trees and audit;
- N-5: both runner processes;
- N-6: the state root and holdings;
- N-14: the runner's trust files and units;
- N-15: the managed settings.

Must hold:
- N-13: no `PYTHONPATH`, working directory or interpreter this account can write;
- the managed settings name both hooks as root-owned copies, and the guard copy denies a holdings read when fed
  one;
- no Claude process runs as root.

Must be reached:
- the read door answers `schedules`;
- the deploy gate answers `inspect_candidate` with anything but `PEER_NOT_PERMITTED`.

**Checked.**
- 24 synthetic cases pass. One of them feeds the repository's real guard the same payload and requires a deny.
- Nine mutations were each caught by a failing test:
  - a refusal that always passes;
  - the copies skipped;
  - any uid allowed to run;
  - the guard not checked;
  - root processes not counted;
  - the gate's refusal taken as an answer;
  - N-13 ignoring a writable path;
  - a missing process accepted;
  - hook ownership ignored.
- Run as root on the host, it refuses with exit 2 and probes nothing.
- The tests' fake runner pid is one no process can have, so a mutation that reached the real probes could not
  attach to a live process.

**Not done:** running it. That is b8, inside the C6b execution approval.

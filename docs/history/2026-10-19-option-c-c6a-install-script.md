# Option C C6a: the install script Thomas runs, and the C4 runner running it first

**What changed:**
- new `scripts/ops/deploy_runner_c6a.py`;
- the C4 workflow and harness (`--installed`);
- new `tests/test_deploy_runner_c6a.py` and one changed harness test;
- an Option C decision row;
- the win32 skip ceiling 586 → 587.

**Decision and approval** (Thomas 2026-10-19):
- `결정: C6a 실행 방식 = A(Claude가 설치·되돌리기 스크립트 PR, Thomas가 서버 셸에서 실행), 서명 키 = Thomas가 root
  authorized_keys 4개 중 지문으로 선택(K1), host_label = ubuntu-4gb-sin-1, DESIGN_ONLY`.
- `승인: C6a 설치·되돌리기 스크립트 구현 및 PR 생성(--dry-run, 단계별 확인·중단, 합성 시험과 C4 러너 검증; 운영 실행·병합 제외)`.

Asked the same day whether the existing key could be reused: yes. K1 is that reuse, restricted to the
approval namespace. The script takes the chosen key's fingerprint and finds the line in root's
`authorized_keys` itself. Only the key type and the key go into the trust files, so nothing has to be
pasted.

**The script.**
- `install` runs the C5 plan's a0–a9 (`--dry-run` lists them and changes nothing).
- Before a0, a preflight refuses unless all of these hold:
  - root;
  - a clean tree at `--sha`;
  - uids and names 2101–2103 free;
  - the docker group present;
  - no runner path or unit present;
  - systemd as PID 1;
  - exactly one `ssh-ed25519` key with the given fingerprint.
- Each step checks itself and stops, naming the step and the reversal. It never repairs.
- The interpreter's SHA256 is checked before it is extracted.
- a9 sends one inspection as `claude` and requires root to get `PEER_NOT_PERMITTED`.
- `uninstall` reverses a8–a0:
  - the actor's and gate's records are moved to a backup directory first;
  - the `safe.directory` entries under the trees go;
  - the `claude` account stays once C6b has started.
- `status --expect-absent` fails if anything is left.
- Standard library only, so it runs under the system `python3` before the runner's interpreter exists.
- It names no `.env` or runtime state path and runs no `docker exec` or `compose`.

**C4.** The workflow now:
1. makes the synthetic keys;
2. runs this script with `--dry-run`, then for real;
3. runs the harness against that install (`--installed`: only the test fixtures are its own);
4. uninstalls with the script and requires `status --expect-absent`.

So the script that will run on the production host has run end to end, reversal included, on a fresh VM.

**Tests.**
- 21 synthetic cases:
  - the fingerprint equals ssh-keygen's;
  - the chosen key is found among options and comments;
  - zero or two matches stop;
  - the configurations hold their schema and the decided policy;
  - each preflight refusal does nothing;
  - the dry-run order of install and uninstall;
  - an interpreter with the wrong SHA256 is never extracted;
  - what `absent` means;
  - no secret path in the script.
- Fault injection (scratch): 11 of 11 caught.

**Not done.** The script has not run on the production host. That is C6a's execution, a separate approval.

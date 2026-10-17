# Option C C5 preparation: the install tool does what C4 found an install must do

**What changed:**
- `scripts/ops/deploy_runner_install.py`:
  - `link-install`, a new subcommand;
  - `trust-tree`, a new subcommand;
  - `genesis` now also makes `docker-config`.
- The C4 workflow and harness use the tool where they had done these steps by hand.
- Tests.
- The win32 skip ceiling 572 → 585.

**Approval:** `승인: C5 준비 — 설치 도구에 .pth·git safe.directory·docker-config 처리 추가, C4 workflow가 설치 도구를 쓰도록
변경, PR 생성(병합·운영 설치 제외) (Thomas 2026-10-17)`.

**The three findings and the tool's answer.**

| C4 finding | Tool step | Rules |
|---|---|---|
| The units start `python -I`, which keeps the cwd off `sys.path`, and the repository has no package metadata, so a unit cannot import `runtime` | `link-install --app <tree>`: writes `thomas_deploy_install.pth` naming the tree into the site-packages of the interpreter running it | Run from the tree without `-I`, the one time the tree is importable that way. The directory must hold `deploy_runner/serve.py` and be absolute, plain, outside the state root. Never over an existing file; mode 0644 |
| X runs `git` with no `HOME` against a root-owned tree: "dubious ownership" | `trust-tree --tree <path>`: `git config --system --add safe.directory <path>` | One exact absolute plain path per call, never a wildcard. The same path again adds nothing. Fixed argv and environment. A git failure refuses |
| X's `docker image build` needs a writable config directory (BUILD-HOME = A) | `genesis` also makes `<X's records directory>/docker-config` | 0700, owned by X, made through the directory descriptor genesis already holds. An existing one refuses: an install never reuses one |

**C4.**
- The workflow calls `link-install` instead of writing the `.pth` itself.
- The harness calls `trust_tree` for its trees.
- Two new SETUP rows check what the tool left:
  - the root-owned `.pth` names the install's tree;
  - `docker-config` is a 0700 directory owned by X.
- This PR's own run is the evidence that the tool works under systemd on a fresh VM.

**Tests.**
- 13 new cases in `test_mvp_runtime_deploy_runner_service.py`:
  - `genesis` makes `docker-config` and never reuses one;
  - `link-install` writes once and refuses a directory that is not the tree;
  - `trust-tree`: fixed argv and environment, once only, no wildcard or relative path, a git failure refuses.
- Fault injection (scratch): 9 of 9 caught.

**Not done.**
- No install anywhere.
- No merge.
- The production install procedure (C5 proper) and its host steps remain separate approvals.

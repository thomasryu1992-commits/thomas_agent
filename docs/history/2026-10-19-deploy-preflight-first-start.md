# Deploy preflight: `--first-start` lets a service the tree adds start once, by name

**What changed:**
- `scripts/ops/deploy_preflight.py` takes `--first-start CONTAINER`. It is repeatable and needs `--tree`.
- Six cases in `tests/test_ops_deploy_preflight.py`.
- One paragraph in the deploy skill's step 6.

**Approval** (Thomas 2026-10-19): `승인: 배포 preflight가 새 서비스(thomas-claude-read-bridge) 첫 기동을 허용하도록 고치는
코드 PR 생성(병합·배포 제외)`.

**Why.** Since 2026-10-12 the promote run stops on any difference between the running `thomas-` containers and
the tree's compose services. A service the merged tree adds is such a difference: it is in the compose file and
is not running. So the first deploy of Claude's read door (`claude-read-bridge`, #1254) could not pass the
preflight.

**How it stays narrow.**
- **Exact names only.** A name passed to `--first-start` is let through only while it is exactly a container the
  tree's compose names and that is not running. It is reported as a WARN, never a PASS.
- **A wrong name stops.** If the container is already running, is not in the compose file, or is misspelled,
  the promote run stops, so a stale flag cannot quietly widen what the promote accepts.
- **Other differences still stop.** Any other service that is down, and any `thomas-` container the file no
  longer names, stop as before.

**Checked.** All 100 preflight tests pass. Five mutations were each caught:
- the misnamed check removed;
- the flag ignored;
- the first start reported silently;
- a misnamed flag only warning;
- `--tree` not required.

**Not done.** The deploy itself, under the DEPLOYMENT_HOLD, has its own approval.

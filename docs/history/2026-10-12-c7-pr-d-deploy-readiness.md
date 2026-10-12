# C7 PR-D deployment readiness: what ships, the gates, and why it is not ready yet (review packet)

- **What this PR adds** (Thomas 2026-10-12, docs only):
  - `docs/proposals/HOLDINGS_C7_PR_D_DEPLOY_READINESS_V0.1.md` (DRAFT) is the review packet for deploying
    #1216. It does not deploy, build or tag anything.
  - `STATUS.md` is regenerated.
- **Facts it rests on (read from the repository at `1f328ffd`):**
  - A deploy ships origin/main whole. Since #1210 (`b6d4bf30`), four files in the image's `COPY` paths
    changed:
    - #1216: `cash_flows.py`, `store.py`, `scheduler.py`;
    - #1212: the `toss_continuity*` code and `holdings_board --toss-continuity`, reachable from the terminal
      only.
  - #1217 and the docs PRs are not in the image.
  - All eight `thomas-agent-runtime` services are recreated on `up -d`.
  - Behaviour changes in the maintenance lane's holdings fire. Operator and read-bridge show one changed
    value, `cash_flows.error`, through `load_holdings_view`.
- **The deploy skill's step 5 meets the guard:**
  - Step 5 asserts in the image with an inline `python -c`.
  - PR-D's code is in `runtime.mvp_runtime.holdings`, and the LOCAL_ONLY guard refuses inline Python that
    imports it.
  - The packet records this as a decision instead of working around it. The default: a byte check of the
    changed files (Claude), and a synthetic behaviour check in a scratch container (Thomas's terminal).
- **Verdict: PREPARATION_COMPLETE_PENDING_EVIDENCE.**
  - The global guard is PENDING. The installed copy is still `865e9eec…`, against main's `88addc9d…`.
  - The running image is UNKNOWN in this task. It was last observed at 03:17Z as `candidate-1210`.
  - The rollback is not verified.
  - No safety invariant is in doubt, so it is not HOLD.
- **Measurement limit, recorded, not fixed:** a lock refusal on the told write shows as `told not
  recorded:PersistenceError`. The name `STATE_LOCKED` does not reach the status line.

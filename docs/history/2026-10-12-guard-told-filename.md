# The LOCAL_ONLY guard knows C7 PR-D's told record by name

- **Why** (Thomas 2026-10-12, review PR only):
  - C7 PR-D (#1216) added a LOCAL_ONLY file to the holdings state directory,
    `holdings_cash_flow_blocked_told.json`.
  - The guard's path rules covered it there: `runtime_governance_state/+holdings`, and `holdings/+holdings_`
    for a reference relative to the state root.
  - The by-name rule did not. Its pattern `holdings_cash_flows?(_state)?\.jsonl?` wants `.json` right after
    `holdings_cash_flow` or `_state`. So a bare name, or a copy at another path, went through.
  - #1216 recorded this as GUARD_COVERAGE_PARTIAL. A synthetic run on `52b744e9` confirms it: with the rule
    removed again, the seven by-name payloads below are allowed.
- **What changed:**
  - `.claude/hooks/guard-local-only.sh`: the by-name rule takes one exact alternative,
    `holdings_cash_flow_blocked_told\.json`. Nothing else in the hook moved.
  - `tests/test_claude_local_only_guard.py` refuses the told record in nine forms. The name is read from
    `cash_flows.BLOCKED_TOLD_FILENAME`, so a rename cannot leave the rule behind unnoticed.
    - The seven the new rule closes:
      - a bare `cat`, and a `cp` out;
      - a copy at another path;
      - `Read`, `Grep` and `Glob` on that copy;
      - a `Monitor` tail.
    - Two the path rules already covered.
  - The allowed list gains the one table named on its own (`cat holdings_snapshot.json`), pinning that it
    still passes. A second test keeps another `*_told.json` name, the increment's history entry, the
    lane's tests and its source allowed.
- **What it deliberately does not do:**
  - No wider pattern. A `holdings_[a-z_]+\.json` would refuse `holdings_snapshot.json`, the one table
    allowed out.
  - **The global guard is not changed.** `/root/.claude/hooks/guard-local-only.sh` is a separate copy on
    the host. It had the same sha256 as the repo hook before this PR (`865e9eec…`). Merging this PR does not
    update it, and only Thomas installs it. Until he does and records the check, the host's global guard
    keeps the old by-name rule.
  - Other names the by-name rule does not know are left as they are, a follow-up for a decision:
    - `holdings_peak.json`: `peak_total_krw`, `peak_at`, `scope_version`. Those figures are in the
      outward table already.
    - `holdings_drawdown_told.json`: `told_state`, `told_at`.
    - `holdings_refresh.json`: `attempted_at`.
    - The path rules cover all three inside the state directory.
  - The settings wrapper's fallback (when the hook is missing) still matches paths and `holdings_board`
    flags only. No bare name is in it, for any file. Unchanged.
- **Limits, as before:** the guard reads command text and is a tripwire, not a boundary (its header). A
  name assembled at run time, a script on disk, or `docker exec` by a member of the docker group passes it.
  A passing test shows the rule trips. It does not show the file cannot leave.

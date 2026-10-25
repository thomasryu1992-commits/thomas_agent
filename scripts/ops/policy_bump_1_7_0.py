#!/usr/bin/env python3
"""The 1.6.2 -> 1.7.0 governance policy bump, mechanically — the 1.6.2 script's procedure, five removals.

Thomas retired the assistant (Hermes) on 2026-10-25, its container first (#1305) and then the four doors it
reached the runtime through. This bump removes the clauses that described those doors and nothing else:

- `authority.assistant_dispatch_gate` and `control_channel.assistant_dispatch` (the dispatch door),
- `control_channel.assistant_switch` (the switch door; a SAFETY section, so the fingerprint moves),
- `control_channel.approval_notification_mirror` (switch-door asks copied into the assistant's window),
- `control_channel.assistant_schedule` (the dispatch door's delegated schedule changes).

**`control_channel.assistant_read` stays, unchanged:** Claude's read door (`claude-read-bridge`) serves its
policy-gated reads (`holdings_status`, `lane_digest`) under that clause (`control.granted_read_verbs`).

It also moves the validator's literals and drops the doc tokens of the removed clauses, every example
binding and fixture carrying `policy_version: 1.6.2`, both replay bundles (rebuilt with the validator's own
`rebuild_bundle` and the kernel's own fingerprint payload), the one comment that cites the version
(`policy_fingerprint.py`), and writes the pin test.

    python scripts/ops/policy_bump_1_7_0.py --check --state-root /root/thomas_agent   # what would change; non-zero if anything is off
    python scripts/ops/policy_bump_1_7_0.py --apply --state-root /root/thomas_agent   # write it (refuses unless --check would pass)

It refuses when: the policy is not at 1.6.2; an approval is PENDING or APPROVED-unspent and not expired;
a literal site outside the known file classes carries the old version; the runtime still reads a removed
clause; or an anchor is not where 1.6.2 left it.

**A REBIND follows**, as for 1.6.1 and 1.6.2, and for two reasons this time: the execution stage binds the
policy version, and `assistant_switch` is one of the safety sections the stage fingerprints. After the
deploy the stage reads READ_ONLY until Thomas approves a REBIND of the same rung.

Decision Q2 (2026-09-03): policy edits are written together and applied on Thomas's decision. Nothing here
runs on its own.
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OLD, NEW = "1.6.2", "1.7.0"
POLICY_REL = "governance/GOVERNANCE_POLICY.yaml"
VALIDATOR_REL = "scripts/validate_permission_approval_contracts.py"
COMMENT_SITES = ("runtime/mvp_runtime/policy_fingerprint.py",)      # cites the version in prose only
# Literals that are NOT pins of the committed policy (see policy_bump_1_6_0.py and policy_bump_1_5_2.py
# for why each is here).
NOT_PINS = ("tests/test_mvp_runtime_operator_cli.py",
            "tests/test_policy_bump_1_5_1.py", "tests/test_mvp_runtime_crypto_readiness_state.py")
BUNDLES = (
    "examples/read_only_runtime/input/read_only_runtime_input_bundle_v0.1.yaml",
    "examples/read_only_runtime/input/read_only_runtime_input_bundle_tool_request_blocked_v0.1.yaml",
)
LITERAL_ROOTS = ("examples", "tests/fixtures")
PIN_TEST_REL = "tests/test_policy_assistant_doors_retired.py"
SELF_REL = "scripts/ops/policy_bump_1_7_0.py"
OTHER_BUMPS_REL = ("scripts/ops/policy_bump_1_5_0.py", "scripts/ops/policy_bump_1_5_1.py",
                   "scripts/ops/policy_bump_1_5_2.py", "scripts/ops/policy_bump_1_6_0.py",
                   "scripts/ops/policy_bump_1_6_1.py", "scripts/ops/policy_bump_1_6_2.py")
FINGERPRINT_SCHEMA = "read_only_runtime_input_bundle_fingerprint_payload.v0.1"

# --- the removals: each is the text from its lead comment up to (not including) the next anchor -----------
REMOVALS: tuple[tuple[str, str], ...] = (
    ("  # The assistant's dispatch gate (Thomas approved 2026-08-30;", "\naction_identity:"),
    ("  # The assistant's dispatch lane (Thomas approved 2026-08-30).", "  # The assistant's read lane"),
    ("  # A switch-door ask is also SENT on the assistant's bot", "\napproval_lifetime:"),
)
REMOVED_KEYS = (("authority", "assistant_dispatch_gate"), ("control_channel", "assistant_dispatch"),
                ("control_channel", "assistant_switch"), ("control_channel", "approval_notification_mirror"),
                ("control_channel", "assistant_schedule"))

READ_LEAD_OLD = (
    "  # The assistant's read lane (door API v2, 2026-09-04). Named for the reason 1.4.0 named the\n"
    "  # switch: with dispatch and switch described, a policy-only reader concludes those are the\n"
    "  # assistant's lanes — and misses the one that reads the approvals store. The verb list is\n"
    "  # CLOSED and pinned by test: every verb here is served (read_bridge._READS), and a served verb\n"
    "  # missing here is one the door carries dormant and refuses by name (POLICY_GATED_READS).\n"
    "  # No verb here changes anything; schedule enable/disable/remove live in scheduler_cli and,\n"
    "  # within assistant_schedule's delegated scope only, the dispatch door's\n"
    "  # schedule.propose_change (1.6.0); the approval read is a summary and never the record.\n"
)
READ_LEAD_NEW = (
    "  # The read lane (door API v2, 2026-09-04). The assistant's until it was retired (Thomas\n"
    "  # 2026-10-25, 1.7.0 removed its dispatch, switch, mirror and schedule clauses); kept because\n"
    "  # Claude's read door (claude-read-bridge) serves its policy-gated reads under it. The verb list\n"
    "  # is CLOSED and pinned by test: every verb here is served (read_bridge._READS), and a served verb\n"
    "  # missing here is one the door carries dormant and refuses by name (POLICY_GATED_READS).\n"
    "  # No verb here changes anything; schedule enable/disable/remove live in scheduler_cli, and the\n"
    "  # approval read is a summary and never the record.\n"
)

# Doc tokens the validator requires of the policy text that only the removed clauses carried.
REMOVED_DOC_TOKENS = ('"assistant_schedule:"', '"financial_kinds_delegable: false"', '"out_of_scope: proposal_only"',
                      '"emergency_close: approval_required_always"', '"approval_notification_mirror:"',
                      '"decision_source: false"')

PIN_TEST = '''\"\"\"The policy no longer describes the assistant's retired doors, and the runtime reads none of them (1.7.0).\"\"\"
from pathlib import Path

import yaml

from runtime.mvp_runtime import control, policy_fingerprint

POLICY = Path(__file__).resolve().parents[1] / "governance" / "GOVERNANCE_POLICY.yaml"


def _policy():
    return yaml.safe_load(POLICY.read_text(encoding="utf-8"))


def test_the_retired_doors_clauses_are_gone_and_the_read_lane_stays():
    policy = _policy()
    assert "assistant_dispatch_gate" not in policy["authority"]
    for key in ("assistant_dispatch", "assistant_switch", "approval_notification_mirror", "assistant_schedule"):
        assert key not in policy["control_channel"], key
    read = policy["control_channel"]["assistant_read"]
    assert {"holdings_status", "lane_digest"} <= set(read["verbs"])
    assert read["mutation_allowed"] is False and read["gate_grants_authority"] is False


def test_the_runtime_reads_no_removed_clause():
    assert not hasattr(control, "granted_switch_verbs")
    assert ("control_channel", "assistant_switch") not in policy_fingerprint.SAFETY_SECTIONS
    assert {"holdings_status", "lane_digest"} <= control.granted_read_verbs()
'''


def _load_validator_module():
    spec = importlib.util.spec_from_file_location(
        "validate_i0_5_read_only_runtime", ROOT / "scripts/validate_i0_5_read_only_runtime.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _literal_sites() -> list[Path]:
    pattern = re.compile(r"policy_version:\s*[\"']?" + re.escape(OLD) + r"[\"']?\b")
    found: list[Path] = []
    for root in LITERAL_ROOTS:
        for path in sorted((ROOT / root).rglob("*")):
            if path.is_file() and path.suffix in {".yaml", ".yml", ".json", ".md"}:
                if pattern.search(path.read_text(encoding="utf-8", errors="ignore")):
                    found.append(path)
    return found


def _stray_sites(known: set[Path]) -> list[Path]:
    """Every tracked text file mentioning the old version outside the known classes."""
    stray: list[Path] = []
    skip_dirs = {".git", ".venv", "node_modules", "__pycache__", ".runtime_governance_state"}
    skip_rel = {POLICY_REL, VALIDATOR_REL, SELF_REL, *OTHER_BUMPS_REL, *COMMENT_SITES, *NOT_PINS}
    for path in ROOT.rglob("*"):
        if any(part in skip_dirs for part in path.parts) or not path.is_file():
            continue
        if path.suffix not in {".py", ".yaml", ".yml", ".json"}:
            continue
        if path in known or path.relative_to(ROOT).as_posix() in skip_rel:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if re.search(r"policy_version[\"']?:\s*[\"']?" + re.escape(OLD), text):
            stray.append(path)
    return stray


STATE_ROOT: Path = ROOT       # --state-root: the checkout whose .runtime_governance_state is the DEPLOYED state


def _outstanding_live() -> tuple[list[str], str | None]:
    """(ids of every approval still PENDING or APPROVED-unspent and not expired, problem). A version
    bump leaves both unspendable, so both refuse. An absent or unreadable store is a problem, never
    "nothing outstanding"."""
    from runtime.mvp_runtime import approval
    from runtime.mvp_runtime.approval_store import ApprovalStore
    from runtime.mvp_runtime.timeutil import utc_now_iso
    store = ApprovalStore.default(STATE_ROOT)
    if not store.path.is_file():
        return [], (f"no approval store at {store.path} — pass --state-root /root/thomas_agent (the deployed state) "
                    "so zero-outstanding is checked against the real store")
    try:
        now = utc_now_iso()
        return [f"{a['approval_id']} ({a.get('status')})" for a in store.current().values()
                if a.get("status") in (approval.STATUS_PENDING, approval.STATUS_APPROVED)
                and not approval.is_expired(a, now=now)], None
    except Exception as exc:  # noqa: BLE001 — unreadable is not zero
        return [], f"the approval store at {store.path} cannot be read ({exc.__class__.__name__}: {exc})"


def _removed_policy_text(policy: str) -> str:
    for start, end in REMOVALS:
        i = policy.index(start)
        j = policy.index(end, i)
        if end.startswith("\n") and policy[i - 2:i] == "\n\n":
            i -= 1                     # the blank line before the block goes with it; `end` brings its own
        policy = policy[:i] + policy[j:]
    return policy.replace(READ_LEAD_OLD, READ_LEAD_NEW, 1)


def check() -> tuple[list[str], list[str]]:
    """(plan, problems)."""
    plan: list[str] = []
    problems: list[str] = []
    policy = (ROOT / POLICY_REL).read_text(encoding="utf-8")
    if f"policy_version: {OLD}\n" not in policy:
        problems.append(f"{POLICY_REL} is not at {OLD} — already bumped, or a different baseline")
    for start, end in REMOVALS:
        if policy.count(start) != 1 or end not in policy[policy.find(start):]:
            problems.append(f"the removal anchored at {start.strip()!r} is not where {OLD} left it")
    if policy.count(READ_LEAD_OLD) != 1:
        problems.append("the assistant_read lead comment is not where 1.6.2 left it")
    plan.append(f"{POLICY_REL}: header {OLD}->{NEW}; remove {', '.join('.'.join(k) for k in REMOVED_KEYS)}")

    validator = (ROOT / VALIDATOR_REL).read_text(encoding="utf-8")
    n_lit = validator.count(f'"policy_version": "{OLD}"') + validator.count(f'"policy_version: {OLD}"')
    if n_lit != 3:
        problems.append(f"{VALIDATOR_REL}: expected 3 version literals, found {n_lit}")
    for token in REMOVED_DOC_TOKENS:
        if validator.count(f"            {token},\n") != 1:
            problems.append(f"{VALIDATOR_REL}: doc token {token} not found once")
    plan.append(f"{VALIDATOR_REL}: 3 literals; -{len(REMOVED_DOC_TOKENS)} doc tokens")

    for rel in COMMENT_SITES:
        if f"policy_version: {OLD}" not in (ROOT / rel).read_text(encoding="utf-8"):
            problems.append(f"{rel}: the version comment is not where {OLD} left it")
    plan.append(f"{len(COMMENT_SITES)} comment site(s): {OLD}->{NEW}")

    sites = _literal_sites()
    bundle_paths = {ROOT / b for b in BUNDLES}
    plain = [p for p in sites if p not in bundle_paths]
    plan.append(f"{len(plain)} example/fixture files: policy_version {OLD}->{NEW}")
    for b in BUNDLES:
        if not (ROOT / b).exists():
            problems.append(f"bundle missing: {b}")
    plan.append(f"{len(BUNDLES)} replay bundles rebuilt (sha256.governance_policy, governance_binding, integrity.bundle_sha256)")

    stray = _stray_sites(set(sites))
    if stray:
        problems.append("old version literal outside the known classes: " + ", ".join(p.relative_to(ROOT).as_posix() for p in stray))

    # The removal must leave nothing reading what it removes, and must keep what Claude's door reads.
    try:
        from runtime.mvp_runtime import control, policy_fingerprint
        if hasattr(control, "granted_switch_verbs"):
            problems.append("control.granted_switch_verbs still reads assistant_switch in this checkout")
        if ("control_channel", "assistant_switch") in policy_fingerprint.SAFETY_SECTIONS:
            problems.append("policy_fingerprint.SAFETY_SECTIONS still names assistant_switch in this checkout")
        if not callable(getattr(control, "granted_read_verbs", None)):
            problems.append("control.granted_read_verbs, which Claude's read door relies on, is missing")
        for module in ("switch_bridge", "dispatch_bridge", "knowledge_bridge", "workflow_manager"):
            if (ROOT / "runtime" / "mvp_runtime" / f"{module}.py").exists():
                problems.append(f"runtime/mvp_runtime/{module}.py still exists; retire the door before its clause")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"the runtime could not be inspected: {exc}")
    plan.append(f"write {PIN_TEST_REL}")

    outstanding, store_problem = _outstanding_live()
    if store_problem:
        problems.append(store_problem)
    if outstanding:
        problems.append(f"{len(outstanding)} live PENDING/APPROVED approval(s): {', '.join(outstanding)} — "
                        "bump at zero outstanding")
    return plan, problems


def apply() -> None:
    policy_path = ROOT / POLICY_REL
    policy = policy_path.read_text(encoding="utf-8")
    policy = policy.replace(f"policy_version: {OLD}\n", f"policy_version: {NEW}\n", 1)
    policy_path.write_text(_removed_policy_text(policy), encoding="utf-8", newline="\n")

    validator_path = ROOT / VALIDATOR_REL
    validator = validator_path.read_text(encoding="utf-8")
    validator = validator.replace(f'"policy_version": "{OLD}"', f'"policy_version": "{NEW}"')
    validator = validator.replace(f'"policy_version: {OLD}"', f'"policy_version: {NEW}"')
    for token in REMOVED_DOC_TOKENS:
        validator = validator.replace(f"            {token},\n", "", 1)
    validator_path.write_text(validator, encoding="utf-8", newline="\n")

    for rel in COMMENT_SITES:
        path = ROOT / rel
        path.write_text(path.read_text(encoding="utf-8").replace(f"policy_version: {OLD}", f"policy_version: {NEW}"),
                        encoding="utf-8", newline="\n")

    bundle_paths = {ROOT / b for b in BUNDLES}
    pattern = re.compile(r"(policy_version:\s*[\"']?)" + re.escape(OLD) + r"([\"']?\b)")
    for path in _literal_sites():
        if path in bundle_paths:
            continue
        text = path.read_text(encoding="utf-8")
        path.write_text(pattern.sub(lambda m: m.group(1) + NEW + m.group(2), text), encoding="utf-8", newline="\n")

    v = _load_validator_module()
    for rel in BUNDLES:
        path = ROOT / rel
        bundle = v.load_yaml(path)
        v.rebuild_bundle(ROOT, bundle)
        payload = {
            "schema_version": FINGERPRINT_SCHEMA,
            "bundle_id": bundle.get("bundle_id"),
            "run_mode": bundle.get("run_mode"),
            "refs": bundle["refs"],
            "sha256": bundle["sha256"],
            "governance_binding": bundle["governance_binding"],
            "constraints": bundle["constraints"],
            "created_at": bundle.get("created_at"),
        }
        bundle["integrity"] = {
            "hash_schema": FINGERPRINT_SCHEMA,
            "bundle_fingerprint_payload": payload,
            "bundle_sha256": v.sha256_value(payload),
        }
        v.write_yaml(path, bundle)

    (ROOT / PIN_TEST_REL).write_text(PIN_TEST, encoding="utf-8", newline="\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--state-root", type=Path, default=ROOT,
                        help="the checkout whose .runtime_governance_state holds the DEPLOYED approval store "
                             "(e.g. /root/thomas_agent); the zero-outstanding check reads it")
    args = parser.parse_args(argv)
    global STATE_ROOT
    STATE_ROOT = args.state_root
    plan, problems = check()
    print(f"policy bump {OLD} -> {NEW}")
    for line in plan:
        print(f"  - {line}")
    if problems:
        print("NOT READY:")
        for line in problems:
            print(f"  ! {line}")
        return 1
    if args.check:
        print("READY — run with --apply, then the validators listed in POLICY_1_7_0_DRAFT.md.")
        return 0
    apply()
    print(f"APPLIED. Now: python {VALIDATOR_REL} && python scripts/validate_i0_5_read_only_runtime.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

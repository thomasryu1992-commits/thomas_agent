#!/usr/bin/env python3
"""The 1.4.0 -> 1.5.0 governance policy bump, mechanically — for Thomas to run himself.

`docs/runtime-contracts/POLICY_1_5_0_DRAFT.md` is the draft this script applies. It does the
1.4.0 procedure (commit 8c1cb02) row by row: the YAML header, the additive `assistant_read` and
`approval_notification_mirror` blocks, the validator's literals and doc tokens, every example
binding and fixture carrying `policy_version: 1.4.0`, both replay bundles rebuilt with the
validator's own `rebuild_bundle` and the kernel's own fingerprint payload, and the pin test.

    python scripts/ops/policy_bump_1_5_0.py --check    # what would change; non-zero if anything is off
    python scripts/ops/policy_bump_1_5_0.py --apply    # write it (refuses unless --check would pass)

It refuses when: the policy is not at 1.4.0 (already bumped, or a different baseline); an
approval is PENDING and not expired (an ask must not straddle two policy versions — the 1.3.0
and 1.4.0 discipline); a literal site outside the known file classes carries the old version;
or the anchor lines the additive block is inserted at are not where 1.4.0 left them.

Decision Q2 (2026-09-03): policy edits are written together and APPLIED BY THOMAS. This script
is the "written together" half. Nothing here runs on its own, and the bundle hashes it writes
are the ones `scripts/validate_i0_5_read_only_runtime.py` recomputes and checks.
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

OLD, NEW = "1.4.0", "1.5.0"
POLICY_REL = "governance/GOVERNANCE_POLICY.yaml"
VALIDATOR_REL = "scripts/validate_permission_approval_contracts.py"
BUNDLES = (
    "examples/read_only_runtime/input/read_only_runtime_input_bundle_v0.1.yaml",
    "examples/read_only_runtime/input/read_only_runtime_input_bundle_tool_request_blocked_v0.1.yaml",
)
LITERAL_ROOTS = ("examples", "tests/fixtures")
PIN_TEST_REL = "tests/test_policy_assistant_read_clause.py"
SELF_REL = "scripts/ops/policy_bump_1_5_0.py"
FINGERPRINT_SCHEMA = "read_only_runtime_input_bundle_fingerprint_payload.v0.1"

SWITCH_TAIL = "    gate_grants_authority: false          # same invariant as every gate in this file\n"
LIFETIME_HEAD = "approval_lifetime:\n"
DISPATCH_AUDIT_OLD = "      - post_dispatch_audit               # actor=assistant_bridge, one ledger record per dispatch\n"
DISPATCH_AUDIT_NEW = (
    "      - post_dispatch_audit               # actor=assistant_bridge, one ledger record per dispatch\n"
    "                                          # and one task_registry entry, origin AGENT (PR8)\n"
)

READ_BLOCK = """  # The assistant's read lane (door API v2, 2026-09-04). Named for the reason 1.4.0 named the
  # switch: with dispatch and switch described, a policy-only reader concludes those are the
  # assistant's lanes — and misses the one that reads the approvals store. The verb list is
  # CLOSED and pinned by test to read_bridge.READ_VERB_AUTHORITY <-> _READS (both directions).
  # No verb here changes anything; schedule enable/disable/remove live in scheduler_cli and
  # reach no socket; the approval read is a summary and never the record.
  assistant_read:
    actor: assistant_bridge
    authority:
      - policy_dispositions.ALLOW.INTERNAL_READ
      - kill_switch.kill_allows.read_only_status
    verbs:                                # closed; see tests/test_policy_assistant_read_clause.py
      - runtime_status
      - crypto_status
      - crypto_readiness
      - crypto_paper
      - crypto_funds
      - tasks
      - history
      - result
      - memory
      - schedules                         # v2: rows only; enable/disable/remove stay in scheduler_cli
      - scheduler_events
      - heartbeat
      - approval_status                   # v2: summary only; approvals/ records are never exposed
    mutation_allowed: false
    approval_status_exposes:              # never the action snapshot, never the fingerprint
      - approval_id
      - status_recorded
      - status_effective
      - expires_at
      - issued_at
      - target_prefix
      - permission_scope
    gate_grants_authority: false          # same invariant as every gate in this file
  # A switch-door ask is also SENT on the assistant's bot (PR11, decision Q1-b). A second sink,
  # not a second source: the copy carries no /approve invitation, the operator's channel on
  # that token is send-only by construction (MIRROR_IS_SEND_ONLY), and nothing typed in that
  # window can answer an ask — invalid_approval_sources above is unchanged.
  approval_notification_mirror:
    sink: assistant_bot_private_chat      # operator sends on HERMES_BOT_TOKEN, send-only
    decision_source: false
    mirrored_asks: switch_door_only       # the same filter as announce_pending_approvals
"""

NEW_DOC_TOKENS = ('"assistant_read:"', '"mutation_allowed: false"',
                  '"approval_notification_mirror:"', '"decision_source: false"')

PIN_TEST = '''"""The policy's assistant_read clause and the read door's inventory name the same verbs."""
from pathlib import Path

import yaml

from runtime.mvp_runtime import read_bridge, store_reads

POLICY = Path(__file__).resolve().parents[1] / "governance" / "GOVERNANCE_POLICY.yaml"


def _control_channel():
    return yaml.safe_load(POLICY.read_text(encoding="utf-8"))["control_channel"]


def test_the_policy_verbs_and_the_read_doors_inventory_are_the_same_set_both_ways():
    verbs = set(_control_channel()["assistant_read"]["verbs"])
    assert verbs == set(read_bridge.READ_VERB_AUTHORITY) == set(read_bridge._READS)


def test_the_clause_grants_nothing_and_the_mirror_is_a_sink():
    channel = _control_channel()
    clause = channel["assistant_read"]
    assert clause["actor"] == "assistant_bridge"
    assert clause["mutation_allowed"] is False and clause["gate_grants_authority"] is False
    assert channel["approval_notification_mirror"]["decision_source"] is False
    assert "telegram_group" in channel["invalid_approval_sources"]      # unchanged by 1.5.0


def test_approval_status_exposes_only_what_store_reads_renders():
    exposed = set(_control_channel()["assistant_read"]["approval_status_exposes"])
    assert exposed <= set(store_reads.APPROVAL_STATUS_FIELDS)
    assert not ({"approved_action_snapshot", "action_fingerprint", "permission_decision_id"} & exposed)
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
    for path in ROOT.rglob("*"):
        if any(part in skip_dirs for part in path.parts) or not path.is_file():
            continue
        if path.suffix not in {".py", ".yaml", ".yml", ".json"}:
            continue
        if path in known or path.relative_to(ROOT).as_posix() in {POLICY_REL, VALIDATOR_REL, SELF_REL}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if re.search(r"policy_version[\"']?:\s*[\"']?" + re.escape(OLD), text):
            stray.append(path)
    return stray


def _pending_live() -> list[str]:
    from runtime.mvp_runtime import approval
    from runtime.mvp_runtime.approval_store import ApprovalStore
    from runtime.mvp_runtime.timeutil import utc_now_iso
    try:
        store = ApprovalStore.default()
        now = utc_now_iso()
        return [str(a["approval_id"]) for a in store.pending() if not approval.is_expired(a, now=now)]
    except Exception as exc:  # noqa: BLE001 — a missing store is "nothing pending" on a clean checkout
        print(f"  (approval store not readable here: {exc.__class__.__name__}; treating as none pending)")
        return []


def check() -> tuple[list[str], list[str]]:
    """(plan, problems)."""
    plan: list[str] = []
    problems: list[str] = []
    policy = (ROOT / POLICY_REL).read_text(encoding="utf-8")
    if f"policy_version: {OLD}\n" not in policy:
        problems.append(f"{POLICY_REL} is not at {OLD} — already bumped, or a different baseline")
    if policy.count(SWITCH_TAIL + "\n" + LIFETIME_HEAD) != 1:
        problems.append("the assistant_switch tail / approval_lifetime head anchor is not where 1.4.0 left it")
    if policy.count(DISPATCH_AUDIT_OLD) != 1:
        problems.append("the post_dispatch_audit comment line is not where 1.4.0 left it")
    if "assistant_read:" in policy:
        problems.append("assistant_read already present")
    plan.append(f"{POLICY_REL}: header {OLD}->{NEW}; insert assistant_read + approval_notification_mirror; audit comment")

    validator = (ROOT / VALIDATOR_REL).read_text(encoding="utf-8")
    n_lit = validator.count(f'"policy_version": "{OLD}"') + validator.count(f'"policy_version: {OLD}"')
    if n_lit != 3:
        problems.append(f"{VALIDATOR_REL}: expected 3 version literals, found {n_lit}")
    if '"one_time_use_required: true",' not in validator:
        problems.append(f"{VALIDATOR_REL}: require_doc_tokens anchor not found")
    plan.append(f"{VALIDATOR_REL}: 3 literals; +{len(NEW_DOC_TOKENS)} doc tokens")

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

    try:
        from runtime.mvp_runtime import store_reads
        if not hasattr(store_reads, "APPROVAL_STATUS_FIELDS"):
            problems.append("store_reads.APPROVAL_STATUS_FIELDS is missing — the pin test needs it (PR12 adds it)")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"cannot import store_reads: {exc}")
    plan.append(f"write {PIN_TEST_REL}")

    pending = _pending_live()
    if pending:
        problems.append(f"{len(pending)} live PENDING approval(s): {', '.join(pending)} — bump at zero PENDING")
    return plan, problems


def apply() -> None:
    policy_path = ROOT / POLICY_REL
    policy = policy_path.read_text(encoding="utf-8")
    policy = policy.replace(f"policy_version: {OLD}\n", f"policy_version: {NEW}\n", 1)
    policy = policy.replace(SWITCH_TAIL + "\n" + LIFETIME_HEAD, SWITCH_TAIL + READ_BLOCK + "\n" + LIFETIME_HEAD, 1)
    policy = policy.replace(DISPATCH_AUDIT_OLD, DISPATCH_AUDIT_NEW, 1)
    policy_path.write_text(policy, encoding="utf-8", newline="\n")

    validator_path = ROOT / VALIDATOR_REL
    validator = validator_path.read_text(encoding="utf-8")
    validator = validator.replace(f'"policy_version": "{OLD}"', f'"policy_version": "{NEW}"')
    validator = validator.replace(f'"policy_version: {OLD}"', f'"policy_version: {NEW}"')
    tokens = "".join(f"            {t},\n" for t in NEW_DOC_TOKENS)
    validator = validator.replace('            "one_time_use_required: true",\n',
                                  '            "one_time_use_required: true",\n' + tokens, 1)
    validator_path.write_text(validator, encoding="utf-8", newline="\n")

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
    args = parser.parse_args(argv)
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
        print("READY — run with --apply, then the validators listed in POLICY_1_5_0_DRAFT.md §3.")
        return 0
    apply()
    print(f"APPLIED. Now: python {VALIDATOR_REL} && python scripts/validate_i0_5_read_only_runtime.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

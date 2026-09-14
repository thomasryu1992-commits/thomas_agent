#!/usr/bin/env python3
"""The 1.5.0 -> 1.6.0 governance policy bump, mechanically — for Thomas to run himself.

`docs/runtime-contracts/POLICY_1_6_0_DRAFT.md` is the draft this script applies, by the 1.5.0
procedure (`scripts/ops/policy_bump_1_5_0.py`, commit 235051b): the YAML header, the additive
`assistant_schedule` block, the validator's literals and doc tokens, every example binding and
fixture carrying `policy_version: 1.5.0`, both replay bundles rebuilt with the validator's own
`rebuild_bundle` and the kernel's own fingerprint payload, the one comment that cites the version
(`policy_fingerprint.py`), and the pin test.

    python scripts/ops/policy_bump_1_6_0.py --check    # what would change; non-zero if anything is off
    python scripts/ops/policy_bump_1_6_0.py --apply    # write it (refuses unless --check would pass)

It refuses when: the policy is not at 1.5.0; an approval is PENDING and not expired (an ask must
not straddle two policy versions); a literal site outside the known file classes carries the old
version; or the anchor the additive block is inserted at is not where 1.5.0 left it.

What the clause switches on: `schedule_delegation.load_delegation` returns None while the clause
is absent and the dispatch door refuses every `schedule.propose_change` as
SCHEDULE_DELEGATION_DISABLED. After `--apply` the door applies changes inside the named scope and
records proposals outside it (V0.2 §1.3; tests/test_mvp_runtime_workflow_schedules.py). The
services read the policy at start, so the door picks the clause up on its next restart.

Decision Q2 (2026-09-03): policy edits are written together and APPLIED BY THOMAS. This script
is the "written together" half. Nothing here runs on its own.
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

OLD, NEW = "1.5.0", "1.6.0"
POLICY_REL = "governance/GOVERNANCE_POLICY.yaml"
VALIDATOR_REL = "scripts/validate_permission_approval_contracts.py"
COMMENT_SITES = ("runtime/mvp_runtime/policy_fingerprint.py",)      # cites the version in prose only
# Literals that are NOT pins of the committed policy and must not move with it: the operator CLI
# test's stubbed policy-check result and its self-contained "1.4.0 -> 1.5.0" announcement
# fixture, and the Hermes manifest, which records what the HOST was measured with (the host
# stays at the old version until the runtime that reads the new policy is deployed).
NOT_PINS = ("tests/test_mvp_runtime_operator_cli.py", "integrations/hermes/MANIFEST.yaml")
BUNDLES = (
    "examples/read_only_runtime/input/read_only_runtime_input_bundle_v0.1.yaml",
    "examples/read_only_runtime/input/read_only_runtime_input_bundle_tool_request_blocked_v0.1.yaml",
)
LITERAL_ROOTS = ("examples", "tests/fixtures")
PIN_TEST_REL = "tests/test_policy_assistant_schedule_clause.py"
SELF_REL = "scripts/ops/policy_bump_1_6_0.py"
PREVIOUS_BUMP_REL = "scripts/ops/policy_bump_1_5_0.py"
FINGERPRINT_SCHEMA = "read_only_runtime_input_bundle_fingerprint_payload.v0.1"

MIRROR_TAIL = "    mirrored_asks: switch_door_only       # the same filter as announce_pending_approvals\n"
LIFETIME_HEAD = "approval_lifetime:\n"

SCHEDULE_BLOCK = """  # The assistant's schedule lane (sequence 2, P09; V0.2 §1.3 — the conditional amendment of
  # invariant 3). Until this clause existed no door carried a schedule-changing verb and every
  # schedule change was Thomas's, in the container (scheduler_cli). This clause DELEGATES a
  # closed scope and nothing else: the dispatch door's `schedule.propose_change` applies a change
  # only when every line below holds, records a PROPOSAL and applies nothing when one does not,
  # and refuses a financial kind outright — no delegation reaches the money path's schedules,
  # and no proposal record is written for them either. The code is the same with or without
  # this clause; the clause is the switch (schedule_delegation.load_delegation).
  assistant_schedule:
    actor: assistant_bridge
    authority:
      - policy_dispositions.ALLOW.INTERNAL_ANALYSIS
      - policy_dispositions.ALLOW.DRAFT_CREATION
    mutation_allowed: true                # inside the scope below, and only there
    delegated_kinds:                      # closed; every other kind is a proposal, every crypto kind a refusal
      - analysis_task
      - workflow_plan
    min_interval_seconds: 3600            # no delegated cadence tighter than hourly
    max_active: 3                         # enabled schedules created by the assistant, at once
    max_validity_days: 30                 # a delegated schedule ends itself (expires_at); Thomas renews by hand
    max_model_calls_per_run: 6            # a workflow_plan's budget per occurrence
    financial_kinds_delegable: false      # crypto_* and candle_archive: refused, never proposed
    out_of_scope: proposal_only           # recorded as a `proposed` scheduler event; nothing applied
    gate_grants_authority: false          # same invariant as every gate in this file
"""

NEW_DOC_TOKENS = ('"assistant_schedule:"', '"financial_kinds_delegable: false"', '"out_of_scope: proposal_only"')

PIN_TEST = '''"""The policy's assistant_schedule clause and the runtime's delegation scope name the same limits."""
from pathlib import Path

import yaml

from runtime.mvp_runtime import schedule_delegation as sd, scheduler

POLICY = Path(__file__).resolve().parents[1] / "governance" / "GOVERNANCE_POLICY.yaml"


def _clause():
    return yaml.safe_load(POLICY.read_text(encoding="utf-8"))["control_channel"]["assistant_schedule"]


def test_the_clause_loads_as_the_scope_the_door_enforces():
    scope = sd.load_delegation()
    clause = _clause()
    assert scope is not None and scope.kinds == frozenset(clause["delegated_kinds"])
    assert scope.min_interval_seconds == clause["min_interval_seconds"] and scope.max_active == clause["max_active"]
    assert scope.max_validity_days == clause["max_validity_days"]
    assert scope.max_model_calls_per_run == clause["max_model_calls_per_run"]


def test_the_scope_reaches_no_financial_kind_and_grants_nothing_else():
    clause = _clause()
    assert clause["actor"] == "assistant_bridge" and clause["gate_grants_authority"] is False
    assert clause["financial_kinds_delegable"] is False and clause["out_of_scope"] == "proposal_only"
    assert not (set(clause["delegated_kinds"]) & sd.FINANCIAL_KINDS)
    assert set(clause["delegated_kinds"]) <= scheduler.MAINTENANCE_KINDS
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
    skip_rel = {POLICY_REL, VALIDATOR_REL, SELF_REL, PREVIOUS_BUMP_REL, *COMMENT_SITES, *NOT_PINS}
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
    if policy.count(MIRROR_TAIL + "\n" + LIFETIME_HEAD) != 1:
        problems.append("the approval_notification_mirror tail / approval_lifetime head anchor is not where 1.5.0 left it")
    if "assistant_schedule:" in policy:
        problems.append("assistant_schedule already present")
    plan.append(f"{POLICY_REL}: header {OLD}->{NEW}; insert assistant_schedule after approval_notification_mirror")

    validator = (ROOT / VALIDATOR_REL).read_text(encoding="utf-8")
    n_lit = validator.count(f'"policy_version": "{OLD}"') + validator.count(f'"policy_version: {OLD}"')
    if n_lit != 3:
        problems.append(f"{VALIDATOR_REL}: expected 3 version literals, found {n_lit}")
    if '"one_time_use_required: true",' not in validator:
        problems.append(f"{VALIDATOR_REL}: require_doc_tokens anchor not found")
    plan.append(f"{VALIDATOR_REL}: 3 literals; +{len(NEW_DOC_TOKENS)} doc tokens")

    for rel in COMMENT_SITES:
        if f"policy_version: {OLD}" not in (ROOT / rel).read_text(encoding="utf-8"):
            problems.append(f"{rel}: the version comment is not where 1.5.0 left it")
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

    try:
        from runtime.mvp_runtime import schedule_delegation
        schedule_delegation.Delegation.from_clause(__import__("yaml").safe_load(SCHEDULE_BLOCK)["assistant_schedule"])
    except Exception as exc:  # noqa: BLE001
        problems.append(f"the clause does not load as a delegation scope: {exc}")
    plan.append(f"write {PIN_TEST_REL}")

    pending = _pending_live()
    if pending:
        problems.append(f"{len(pending)} live PENDING approval(s): {', '.join(pending)} — bump at zero PENDING")
    return plan, problems


def apply() -> None:
    policy_path = ROOT / POLICY_REL
    policy = policy_path.read_text(encoding="utf-8")
    policy = policy.replace(f"policy_version: {OLD}\n", f"policy_version: {NEW}\n", 1)
    policy = policy.replace(MIRROR_TAIL + "\n" + LIFETIME_HEAD, MIRROR_TAIL + SCHEDULE_BLOCK + "\n" + LIFETIME_HEAD, 1)
    policy_path.write_text(policy, encoding="utf-8", newline="\n")

    validator_path = ROOT / VALIDATOR_REL
    validator = validator_path.read_text(encoding="utf-8")
    validator = validator.replace(f'"policy_version": "{OLD}"', f'"policy_version": "{NEW}"')
    validator = validator.replace(f'"policy_version: {OLD}"', f'"policy_version: {NEW}"')
    tokens = "".join(f"            {t},\n" for t in NEW_DOC_TOKENS)
    validator = validator.replace('            "one_time_use_required: true",\n',
                                  '            "one_time_use_required: true",\n' + tokens, 1)
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
        print("READY — run with --apply, then the validators listed in POLICY_1_6_0_DRAFT.md §3.")
        return 0
    apply()
    print(f"APPLIED. Now: python {VALIDATOR_REL} && python scripts/validate_i0_5_read_only_runtime.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

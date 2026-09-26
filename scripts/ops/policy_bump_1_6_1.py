#!/usr/bin/env python3
"""The 1.6.0 -> 1.6.1 governance policy bump, mechanically — for Thomas to run himself.

`docs/runtime-contracts/POLICY_1_6_1_DRAFT.md` is the draft this script applies, by the procedure the
1.5.x and 1.6.0 scripts established: the YAML header, the additive grant (`lane_digest` in
`control_channel.assistant_read.verbs`), one comment correction (the clause's pin is no longer "the same
set both ways"), the validator's literals and one doc token, every example binding and fixture carrying
`policy_version: 1.6.0`, both replay bundles rebuilt with the validator's own `rebuild_bundle` and the
kernel's own fingerprint payload, the one comment that cites the version (`policy_fingerprint.py`), and
the pin test.

    python scripts/ops/policy_bump_1_6_1.py --check --state-root /root/thomas_agent   # what would change; non-zero if anything is off
    python scripts/ops/policy_bump_1_6_1.py --apply --state-root /root/thomas_agent   # write it (refuses unless --check would pass)

It refuses when: the policy is not at 1.6.0; an approval is PENDING or APPROVED-unspent and not expired
(an ask must not straddle two policy versions — a version bump leaves either unspendable); a literal
site outside the known file classes carries the old version; the runtime does not carry the dormant
read this grant switches on; or an anchor is not where 1.6.0 left it.

What the grant switches on: the read door's `lane_digest` (system review D8, Thomas 2026-09-26: "주간
다이제스트는 Hermes가 돌린다") — per-lane run counts over the run ledger, the same fold
`scripts.lane_digest` prints, and never a record. It refuses as CONTROL_VERB_NOT_GRANTED while the
committed policy does not list it, and answers after the image that carries this policy is deployed.

**A REBIND follows even though no safety section moves.** `assistant_read` is not in
`policy_fingerprint.SAFETY_SECTIONS`, so the safety fingerprint is unchanged; but the execution stage
binds the policy VERSION as well, so once the image with this policy is deployed the stage reads
READ_ONLY — new live entries refused, the slippage probe included — until Thomas approves a REBIND of
the same rung (`docs/runtime-contracts/EXECUTION_STAGE_V0.1.md` §4). One bump, one REBIND: fold any other
pending policy change into the same image rather than following it with a second.

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

OLD, NEW = "1.6.0", "1.6.1"
POLICY_REL = "governance/GOVERNANCE_POLICY.yaml"
VALIDATOR_REL = "scripts/validate_permission_approval_contracts.py"
COMMENT_SITES = ("runtime/mvp_runtime/policy_fingerprint.py",)      # cites the version in prose only
# Literals that are NOT pins of the committed policy (see policy_bump_1_6_0.py and policy_bump_1_5_2.py
# for why each is here).
NOT_PINS = ("tests/test_mvp_runtime_operator_cli.py", "integrations/hermes/MANIFEST.yaml",
            "tests/test_policy_bump_1_5_1.py", "tests/test_mvp_runtime_crypto_readiness_state.py")
BUNDLES = (
    "examples/read_only_runtime/input/read_only_runtime_input_bundle_v0.1.yaml",
    "examples/read_only_runtime/input/read_only_runtime_input_bundle_tool_request_blocked_v0.1.yaml",
)
LITERAL_ROOTS = ("examples", "tests/fixtures")
PIN_TEST_REL = "tests/test_policy_lane_digest_read_grant.py"
SELF_REL = "scripts/ops/policy_bump_1_6_1.py"
OTHER_BUMPS_REL = ("scripts/ops/policy_bump_1_5_0.py", "scripts/ops/policy_bump_1_5_1.py",
                   "scripts/ops/policy_bump_1_5_2.py", "scripts/ops/policy_bump_1_6_0.py")
FINGERPRINT_SCHEMA = "read_only_runtime_input_bundle_fingerprint_payload.v0.1"

# --- the grant ---------------------------------------------------------------------------------
GRANT_ANCHOR_OLD = (
    "      - approval_status                   # v2: summary only; approvals/ records are never exposed\n"
    "    mutation_allowed: false\n"
)
GRANT_ANCHOR_NEW = (
    "      - approval_status                   # v2: summary only; approvals/ records are never exposed\n"
    "      - lane_digest                       # 1.6.1, review D8 (Thomas 2026-09-26): per-lane run\n"
    "                                          # counts over the run ledger, never a record — the\n"
    "                                          # weekly lane evidence the assistant relays\n"
    "    mutation_allowed: false\n"
)
# Since D8 the door can carry a read dormant (read_bridge.POLICY_GATED_READS), so the pin is no longer
# "the same set both ways"; the comment says what the test now holds.
PIN_COMMENT_OLD = (
    "  # assistant's lanes — and misses the one that reads the approvals store. The verb list is\n"
    "  # CLOSED and pinned by test to read_bridge.READ_VERB_AUTHORITY <-> _READS (both directions).\n"
)
PIN_COMMENT_NEW = (
    "  # assistant's lanes — and misses the one that reads the approvals store. The verb list is\n"
    "  # CLOSED and pinned by test: every verb here is served (read_bridge._READS), and a served verb\n"
    "  # missing here is one the door carries dormant and refuses by name (POLICY_GATED_READS).\n"
)

NEW_DOC_TOKENS = ('"- lane_digest"',)

PIN_TEST = '''"""The policy lists the assistant's lane digest read, and the runtime acts on it (policy 1.6.1)."""
from pathlib import Path

import yaml

from runtime.mvp_runtime import control, read_bridge

POLICY = Path(__file__).resolve().parents[1] / "governance" / "GOVERNANCE_POLICY.yaml"


def _read_clause():
    return yaml.safe_load(POLICY.read_text(encoding="utf-8"))["control_channel"]["assistant_read"]


def test_the_grant_lists_the_digest_under_a_clause_that_still_grants_nothing():
    clause = _read_clause()
    assert "lane_digest" in clause["verbs"]
    assert clause["mutation_allowed"] is False and clause["gate_grants_authority"] is False


def test_the_runtime_reads_the_committed_grant():
    assert "lane_digest" in read_bridge.POLICY_GATED_READS
    assert "lane_digest" in control.granted_read_verbs()
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
    bump leaves both unspendable, so both refuse — the 1.6.0 draft checked the APPROVED half by hand.
    An absent or unreadable store is a problem, never "nothing outstanding"."""
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


def check() -> tuple[list[str], list[str]]:
    """(plan, problems)."""
    plan: list[str] = []
    problems: list[str] = []
    policy = (ROOT / POLICY_REL).read_text(encoding="utf-8")
    if f"policy_version: {OLD}\n" not in policy:
        problems.append(f"{POLICY_REL} is not at {OLD} — already bumped, or a different baseline")
    if policy.count(GRANT_ANCHOR_OLD) != 1:
        problems.append(f"the assistant_read verbs anchor is not where {OLD} left it")
    if policy.count(PIN_COMMENT_OLD) != 1:
        problems.append(f"the assistant_read pin comment is not where {OLD} left it")
    if "- lane_digest" in policy:
        problems.append("a lane_digest verb is already present in the policy")
    plan.append(f"{POLICY_REL}: header {OLD}->{NEW}; assistant_read.verbs + lane_digest; the clause's pin "
                "comment names the dormant reads")

    validator = (ROOT / VALIDATOR_REL).read_text(encoding="utf-8")
    n_lit = validator.count(f'"policy_version": "{OLD}"') + validator.count(f'"policy_version: {OLD}"')
    if n_lit != 3:
        problems.append(f"{VALIDATOR_REL}: expected 3 version literals, found {n_lit}")
    if '            "one_time_use_required: true",\n' not in validator:   # the exact anchor apply() replaces
        problems.append(f"{VALIDATOR_REL}: require_doc_tokens anchor not found")
    plan.append(f"{VALIDATOR_REL}: 3 literals; +{len(NEW_DOC_TOKENS)} doc token")

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

    # The grant must switch on something that exists: the runtime has to carry the dormant read.
    try:
        from runtime.mvp_runtime import control, read_bridge
        if "lane_digest" not in getattr(read_bridge, "POLICY_GATED_READS", ()):
            problems.append("the read door does not carry lane_digest as a policy-gated read in this checkout")
        if not callable(getattr(control, "granted_read_verbs", None)):
            problems.append("control.granted_read_verbs, which reads this grant, is missing in this checkout")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"the runtime does not carry the read this grant switches on: {exc}")
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
    policy = policy.replace(GRANT_ANCHOR_OLD, GRANT_ANCHOR_NEW, 1)
    policy = policy.replace(PIN_COMMENT_OLD, PIN_COMMENT_NEW, 1)
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
        print("READY — run with --apply, then the validators listed in POLICY_1_6_1_DRAFT.md §3.")
        return 0
    apply()
    print(f"APPLIED. Now: python {VALIDATOR_REL} && python scripts/validate_i0_5_read_only_runtime.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

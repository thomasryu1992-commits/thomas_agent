#!/usr/bin/env python3
"""The 1.5.1 -> 1.5.2 governance policy bump, mechanically — for Thomas to run himself.

`docs/runtime-contracts/POLICY_1_5_2_DRAFT.md` is the draft this script applies, by the procedure the
1.5.0, 1.5.1 and 1.6.0 scripts established: the YAML header, the additive grant (`emergency_close` in
`control_channel.assistant_switch.verbs`), one comment correction (the `disable` modes), the
validator's literals and one doc token, every example binding and fixture carrying
`policy_version: 1.5.1`, both replay bundles rebuilt with the validator's own `rebuild_bundle` and the
kernel's own fingerprint payload, the one comment that cites the version (`policy_fingerprint.py`),
and the pin test.

    python scripts/ops/policy_bump_1_5_2.py --check --state-root /root/thomas_agent   # what would change; non-zero if anything is off
    python scripts/ops/policy_bump_1_5_2.py --apply --state-root /root/thomas_agent   # write it (refuses unless --check would pass)

It refuses when: the policy is not at 1.5.1; an approval is PENDING and not expired (an ask must
not straddle two policy versions); a literal site outside the known file classes carries the old
version; the runtime does not carry the dormant verb this grant switches on; or an anchor is not
where 1.5.1 left it.

What the grant switches on: the assistant's ask for the emergency close (Thomas decision 49,
2026-09-19: the assistant only asks). The switch door's `emergency_close` mints the ask
`scripts.emergency_close --request` mints and nothing more; Thomas approves it on the control
channel and the operator spends it in the scheduler container (`--confirm`). It refuses as
CONTROL_VERB_NOT_GRANTED while the committed policy does not list it, and acts after the image that
carries this policy is deployed.

**A REBIND follows.** `assistant_switch` is one of the policy's safety sections
(`policy_fingerprint.SAFETY_SECTIONS`) and the version moves too, so once the image with this policy
is deployed the execution stage reads READ_ONLY until Thomas approves a REBIND
(`docs/runtime-contracts/EXECUTION_STAGE_V0.1.md`). One bump, one REBIND: fold any other safety
change into this one rather than following it with a second.

Order with 1.6.0: apply THIS one first. It is independent of the schedule-delegation clause, and
`policy_bump_1_6_0.py` accepts a 1.5.2 baseline. If 1.6.0 has already been applied this script refuses
("not at 1.5.1") and the grant needs a 1.6.1 script instead. One bump at a time, each at zero PENDING.

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

OLD, NEW = "1.5.1", "1.5.2"
POLICY_REL = "governance/GOVERNANCE_POLICY.yaml"
VALIDATOR_REL = "scripts/validate_permission_approval_contracts.py"
COMMENT_SITES = ("runtime/mvp_runtime/policy_fingerprint.py",)      # cites the version in prose only
# Literals that are NOT pins of the committed policy (see policy_bump_1_6_0.py for the first two), and
# two more since 1.5.1: the 1.5.1 bump's own test asserts what THAT bump writes, and the readiness-state
# size test carries a sample stage record whose version is any string of that length.
NOT_PINS = ("tests/test_mvp_runtime_operator_cli.py", "integrations/hermes/MANIFEST.yaml",
            "tests/test_policy_bump_1_5_1.py", "tests/test_mvp_runtime_crypto_readiness_state.py")
BUNDLES = (
    "examples/read_only_runtime/input/read_only_runtime_input_bundle_v0.1.yaml",
    "examples/read_only_runtime/input/read_only_runtime_input_bundle_tool_request_blocked_v0.1.yaml",
)
LITERAL_ROOTS = ("examples", "tests/fixtures")
PIN_TEST_REL = "tests/test_policy_emergency_close_request_grant.py"
SELF_REL = "scripts/ops/policy_bump_1_5_2.py"
OTHER_BUMPS_REL = ("scripts/ops/policy_bump_1_5_0.py", "scripts/ops/policy_bump_1_5_1.py",
                   "scripts/ops/policy_bump_1_6_0.py")
FINGERPRINT_SCHEMA = "read_only_runtime_input_bundle_fingerprint_payload.v0.1"

# --- the grant ---------------------------------------------------------------------------------
# `disable`'s comment gains `hard` (decision 47; the door has carried it since PR6a), and the verb list
# gains the ask. The value is `enable`'s: the verb only MINTS an ask, and the approval is required.
GRANT_ANCHOR_OLD = (
    "      disable: fail_safe_immediate        # kill|pause|soft, reason required, NO approval — the\n"
    "                                          # assistant may always stop; a stop needing sign-off\n"
    "                                          # is not a fail-safe (S3: the halt door lives here)\n"
    "      enable: approval_required_always    # S2 REJECTED (Thomas 2026-08-01): approval is\n"
    "                                          # required regardless of gate state — the assistant\n"
    "                                          # reads the untrusted web and cannot tell a user's\n"
    "                                          # request from an injected one, so \"start\" is never\n"
    "                                          # its own call. The verb only MINTS the ask.\n"
    "    enable_scopes:\n"
)
GRANT_ANCHOR_NEW = (
    "      disable: fail_safe_immediate        # kill|pause|soft|hard, reason required, NO approval — the\n"
    "                                          # assistant may always stop; a stop needing sign-off\n"
    "                                          # is not a fail-safe (S3: the halt door lives here)\n"
    "      enable: approval_required_always    # S2 REJECTED (Thomas 2026-08-01): approval is\n"
    "                                          # required regardless of gate state — the assistant\n"
    "                                          # reads the untrusted web and cannot tell a user's\n"
    "                                          # request from an injected one, so \"start\" is never\n"
    "                                          # its own call. The verb only MINTS the ask.\n"
    "      emergency_close: approval_required_always  # 1.5.2, Thomas decision 49 (2026-09-19): the\n"
    "                                          # assistant only ASKS. Mints the ask to close every\n"
    "                                          # booked live position at market, reduceOnly, under\n"
    "                                          # the HARD halt in effect; Thomas approves on the\n"
    "                                          # control channel and the operator spends it once in\n"
    "                                          # the scheduler container. This door never spends it.\n"
    "    enable_scopes:\n"
)

NEW_DOC_TOKENS = ('"emergency_close: approval_required_always"',)

PIN_TEST = '''"""The policy lists the assistant's ask for the emergency close, and the runtime acts on it (policy 1.5.2)."""
from pathlib import Path

import yaml

from runtime.mvp_runtime import control, switch_bridge

POLICY = Path(__file__).resolve().parents[1] / "governance" / "GOVERNANCE_POLICY.yaml"


def _policy():
    return yaml.safe_load(POLICY.read_text(encoding="utf-8"))


def test_the_grant_lists_the_ask_as_approval_required():
    verbs = _policy()["control_channel"]["assistant_switch"]["verbs"]
    assert verbs["emergency_close"] == "approval_required_always"
    assert set(verbs) == {"status", "disable", "enable", "emergency_close"}


def test_the_runtime_reads_the_committed_grant():
    assert switch_bridge.CMD_EMERGENCY_CLOSE in switch_bridge.POLICY_GATED_COMMANDS
    assert switch_bridge.CMD_EMERGENCY_CLOSE in control.granted_switch_verbs()
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


def _pending_live() -> tuple[list[str], str | None]:
    """(live PENDING ids, problem). An absent or unreadable store is a problem, never "nothing pending"."""
    from runtime.mvp_runtime import approval
    from runtime.mvp_runtime.approval_store import ApprovalStore
    from runtime.mvp_runtime.timeutil import utc_now_iso
    store = ApprovalStore.default(STATE_ROOT)
    if not store.path.is_file():
        return [], (f"no approval store at {store.path} — pass --state-root /root/thomas_agent (the deployed state) "
                    "so zero-PENDING is checked against the real store")
    try:
        now = utc_now_iso()
        return [str(a["approval_id"]) for a in store.pending() if not approval.is_expired(a, now=now)], None
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
        problems.append(f"the assistant_switch verbs anchor is not where {OLD} left it")
    if "emergency_close:" in policy:
        problems.append("an emergency_close verb is already present in the policy")
    plan.append(f"{POLICY_REL}: header {OLD}->{NEW}; assistant_switch.verbs + emergency_close; the disable "
                "comment names hard")

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

    # The grant must switch on something that exists: the runtime has to carry the dormant verb.
    try:
        from runtime.mvp_runtime import control, switch_bridge
        if getattr(switch_bridge, "CMD_EMERGENCY_CLOSE", None) not in switch_bridge.POLICY_GATED_COMMANDS:
            problems.append("the switch door does not carry emergency_close as a policy-gated verb in this checkout")
        if not callable(getattr(control, "granted_switch_verbs", None)):
            problems.append("control.granted_switch_verbs, which reads this grant, is missing in this checkout")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"the runtime does not carry the ask this grant switches on: {exc}")
    plan.append(f"write {PIN_TEST_REL}")

    pending, pending_problem = _pending_live()
    if pending_problem:
        problems.append(pending_problem)
    if pending:
        problems.append(f"{len(pending)} live PENDING approval(s): {', '.join(pending)} — bump at zero PENDING")
    return plan, problems


def apply() -> None:
    policy_path = ROOT / POLICY_REL
    policy = policy_path.read_text(encoding="utf-8")
    policy = policy.replace(f"policy_version: {OLD}\n", f"policy_version: {NEW}\n", 1)
    policy = policy.replace(GRANT_ANCHOR_OLD, GRANT_ANCHOR_NEW, 1)
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
                             "(e.g. /root/thomas_agent); the zero-PENDING check reads it")
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
        print("READY — run with --apply, then the validators listed in POLICY_1_5_2_DRAFT.md §3.")
        return 0
    apply()
    print(f"APPLIED. Now: python {VALIDATOR_REL} && python scripts/validate_i0_5_read_only_runtime.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

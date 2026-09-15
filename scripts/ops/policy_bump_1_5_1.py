#!/usr/bin/env python3
"""The 1.5.0 -> 1.5.1 governance policy bump, mechanically — for Thomas to run himself.

`docs/runtime-contracts/POLICY_1_5_1_DRAFT.md` is the draft this script applies, by the procedure the
1.5.0 and 1.6.0 scripts established: the YAML header, the additive grant (`halt_trading` in
`control_channel.local_operator_console.emergency_controls_allowed` and `/halt_trading` in
`kill_switch.commands`), three comment corrections, the validator's literals and one doc token,
every example binding and fixture carrying `policy_version: 1.5.0`, both replay bundles rebuilt
with the validator's own `rebuild_bundle` and the kernel's own fingerprint payload, the one comment
that cites the version (`policy_fingerprint.py`), and the pin test.

    python scripts/ops/policy_bump_1_5_1.py --check --state-root /root/thomas_agent   # what would change; non-zero if anything is off
    python scripts/ops/policy_bump_1_5_1.py --apply --state-root /root/thomas_agent   # write it (refuses unless --check would pass)

It refuses when: the policy is not at 1.5.0; an approval is PENDING and not expired (an ask must
not straddle two policy versions); a literal site outside the known file classes carries the old
version; the runtime does not carry the dormant verb this grant switches on; or an anchor is not
where 1.5.0 left it.

What the grant switches on: the Trading Soft Halt (Thomas decision 7, 2026-09-15) — `console_cli
halt_trading`, Telegram `/halt_trading`, and the switch door's `disable mode=soft`. They refuse as
CONTROL_VERB_NOT_GRANTED while the committed policy does not list the verb, and act after the
image that carries this policy is deployed.

Order with 1.6.0: apply THIS one first. It is independent of the schedule-delegation clause, and
`policy_bump_1_6_0.py` accepts a 1.5.1 baseline. If 1.6.0 has already been applied this script refuses
("not at 1.5.0") and the grant needs a 1.6.1 script instead. One bump at a time, each at zero PENDING.

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

OLD, NEW = "1.5.0", "1.5.1"
POLICY_REL = "governance/GOVERNANCE_POLICY.yaml"
VALIDATOR_REL = "scripts/validate_permission_approval_contracts.py"
COMMENT_SITES = ("runtime/mvp_runtime/policy_fingerprint.py",)      # cites the version in prose only
# Literals that are NOT pins of the committed policy (see policy_bump_1_6_0.py for each one's reason).
NOT_PINS = ("tests/test_mvp_runtime_operator_cli.py", "integrations/hermes/MANIFEST.yaml")
BUNDLES = (
    "examples/read_only_runtime/input/read_only_runtime_input_bundle_v0.1.yaml",
    "examples/read_only_runtime/input/read_only_runtime_input_bundle_tool_request_blocked_v0.1.yaml",
)
LITERAL_ROOTS = ("examples", "tests/fixtures")
PIN_TEST_REL = "tests/test_policy_trading_soft_halt_grant.py"
SELF_REL = "scripts/ops/policy_bump_1_5_1.py"
OTHER_BUMPS_REL = ("scripts/ops/policy_bump_1_5_0.py", "scripts/ops/policy_bump_1_6_0.py")
FINGERPRINT_SCHEMA = "read_only_runtime_input_bundle_fingerprint_payload.v0.1"

# --- the grant ---------------------------------------------------------------------------------
GRANT_ANCHOR_OLD = "      - audit\n      - recovery\n      # resume: explicit Thomas decision"
GRANT_ANCHOR_NEW = (
    "      - audit\n      - recovery\n"
    "      # halt_trading: the Trading Soft Halt (Thomas decision 7, 2026-09-15; 1.5.1). Refuses new\n"
    "      # live entries and leaves the runtime ACTIVE, so open positions keep being settled,\n"
    "      # protected, time-exited and reconciled — the halt `kill`/`pause` are not (they stop the\n"
    "      # scheduler too). From PAUSED/KILLED the authenticated operator's /halt_trading moves the\n"
    "      # runtime straight to that state; the assistant's switch door (`disable mode=soft`) can\n"
    "      # halt entries but never release a stop. No approval: a stop must be cheap.\n"
    "      - halt_trading\n"
    "      # resume: explicit Thomas decision"
)
COMMANDS_ANCHOR_OLD = "kill_switch:\n  commands:\n    - /pause\n    - /stop <task_id>\n    - /kill\n    - /resume\n"
COMMANDS_ANCHOR_NEW = ("kill_switch:\n  commands:\n    - /pause\n    - /stop <task_id>\n    - /kill\n    - /resume\n"
                       "    - /halt_trading                      # soft halt: entries only (1.5.1); not a kill_blocks stop\n")

# --- comment corrections (review of the execution-authority audit, 2026-09-15) ------------------
P5_COMMENT_OLD = (
    "      # Revocation did not go with it: `console_cli kill` is file-based, instant, checked by the\n"
    "      # order guard, and deliberately exempted by the close path — so it stops new entries\n"
    "      # without trapping a position, which is what the grant could not do.\n"
)
P5_COMMENT_NEW = (
    "      # Revocation did not go with it: the file-based halts are instant and checked by the order\n"
    "      # guard. `console_cli halt_trading` (1.5.1) stops new entries without trapping a position,\n"
    "      # which is what the grant could not do. `console_cli kill` stops entries too, but its\n"
    "      # kill_blocks include scheduler_execution: a KILLED runtime never reaches the close path,\n"
    "      # so it stops settlement and protection as well (1.5.0 said it exempted closes; corrected).\n"
)
STOP_COMMENT_OLD = "  #     close path, stranding open positions). The runtime stop is `console_cli kill`.\n"
STOP_COMMENT_NEW = ("  #     close path, stranding open positions). The runtime stop that keeps positions managed is\n"
                    "  #     `console_cli halt_trading`; `console_cli kill` stops management too (1.5.1).\n")
SWITCH_COMMENT_OLD = "      disable: fail_safe_immediate        # kill|pause, reason required, NO approval — the\n"
SWITCH_COMMENT_NEW = "      disable: fail_safe_immediate        # kill|pause|soft, reason required, NO approval — the\n"

NEW_DOC_TOKENS = ('"- halt_trading"',)

PIN_TEST = '''"""The policy grants the Trading Soft Halt, and the runtime acts on the grant (policy 1.5.1)."""
from pathlib import Path

import yaml

from runtime.mvp_runtime import control, switch_bridge

POLICY = Path(__file__).resolve().parents[1] / "governance" / "GOVERNANCE_POLICY.yaml"


def _policy():
    return yaml.safe_load(POLICY.read_text(encoding="utf-8"))


def test_the_grant_names_the_soft_halt_on_both_lists():
    policy = _policy()
    assert "halt_trading" in policy["control_channel"]["local_operator_console"]["emergency_controls_allowed"]
    assert "/halt_trading" in policy["kill_switch"]["commands"]


def test_the_runtime_reads_the_committed_grant():
    assert control.CMD_HALT_TRADING in control.granted_emergency_controls()
    assert switch_bridge._DISABLE_MODES["soft"] == control.CMD_HALT_TRADING


def test_the_soft_halt_acts_and_keeps_the_runtime_active(tmp_path):
    store = control.ControlStore(tmp_path)
    store.save(control.ControlState(mode=control.ACTIVE, updated_by="op", updated_at="2026-09-15T00:00:00Z",
                                    reason="armed", trading_armed=True))
    out = control.apply_command(store, control.CMD_HALT_TRADING, actor="op", now="2026-09-15T00:00:00Z")
    assert out["changed"] is True
    assert (store.load().mode, store.load().trading_armed) == (control.ACTIVE, False)
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
    for label, anchor in (("emergency_controls_allowed audit/recovery/resume", GRANT_ANCHOR_OLD),
                          ("kill_switch.commands", COMMANDS_ANCHOR_OLD),
                          ("p5_policy_gate revocation comment", P5_COMMENT_OLD),
                          ("live-execution runtime-stop comment", STOP_COMMENT_OLD),
                          ("assistant_switch disable comment", SWITCH_COMMENT_OLD)):
        if policy.count(anchor) != 1:
            problems.append(f"the {label} anchor is not where 1.5.0 left it")
    if "halt_trading" in policy:
        problems.append("halt_trading already present in the policy")
    plan.append(f"{POLICY_REL}: header {OLD}->{NEW}; grant halt_trading (+ /halt_trading); 3 comment corrections")

    validator = (ROOT / VALIDATOR_REL).read_text(encoding="utf-8")
    n_lit = validator.count(f'"policy_version": "{OLD}"') + validator.count(f'"policy_version: {OLD}"')
    if n_lit != 3:
        problems.append(f"{VALIDATOR_REL}: expected 3 version literals, found {n_lit}")
    if '            "one_time_use_required: true",\n' not in validator:   # the exact anchor apply() replaces
        problems.append(f"{VALIDATOR_REL}: require_doc_tokens anchor not found")
    plan.append(f"{VALIDATOR_REL}: 3 literals; +{len(NEW_DOC_TOKENS)} doc token")

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

    # The grant must switch on something that exists: the runtime has to carry the dormant verb.
    try:
        from runtime.mvp_runtime import control, switch_bridge
        if control.CMD_HALT_TRADING not in control.POLICY_GATED_COMMANDS:
            problems.append("control.CMD_HALT_TRADING is not a policy-gated verb in this checkout")
        if switch_bridge._DISABLE_MODES.get("soft") != control.CMD_HALT_TRADING:
            problems.append("the switch door does not map disable mode=soft to halt_trading in this checkout")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"the runtime does not carry the soft halt this grant switches on: {exc}")
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
    for old, new in ((GRANT_ANCHOR_OLD, GRANT_ANCHOR_NEW), (COMMANDS_ANCHOR_OLD, COMMANDS_ANCHOR_NEW),
                     (P5_COMMENT_OLD, P5_COMMENT_NEW), (STOP_COMMENT_OLD, STOP_COMMENT_NEW),
                     (SWITCH_COMMENT_OLD, SWITCH_COMMENT_NEW)):
        policy = policy.replace(old, new, 1)
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
        print("READY — run with --apply, then the validators listed in POLICY_1_5_1_DRAFT.md §3.")
        return 0
    apply()
    print(f"APPLIED. Now: python {VALIDATOR_REL} && python scripts/validate_i0_5_read_only_runtime.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Operator tool: the machine's execution stage (PR1a; Thomas decisions 1, 4, 8, 9, 2026-09-15).

    # What stage is this machine at, and does the record bind?
    python -m scripts.register_execution_stage --show

    # 1) ASK Thomas for a transition (stores the PENDING approval; changes nothing).
    #    The first record may only be SHADOW or PAPER and names its evidence; after that, one rung
    #    up (CLIMB) or the same rung again (REBIND — after a policy change, or to renew a LIVE
    #    stage's end date). No skip.
    python -m scripts.register_execution_stage --request --to PAPER --registered-by thomas \
        --reason "initial stage" --attest "paper ledger since 2026-07, counterfactual shadow book"

    # 2) Thomas answers /approve <id> on the verified control channel.
    # 3) Spend the approval once and write the record:
    python -m scripts.register_execution_stage --confirm --approval-id approval_abc123

    # Demote — any rung down, NO approval, immediate (decision 8):
    python -m scripts.register_execution_stage --demote --to READ_ONLY --registered-by thomas --reason "..."

Run it in the scheduler container as the service user (it writes governed state):
``docker exec -u 10001 thomas-scheduler python -m scripts.register_execution_stage ...``.

**PR1a records and reports; nothing enforces the stage yet** (PR1b). A stage gates new exposure
only and never a close. There is no ``--without-approval``: a climb is Thomas's, every time.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime import approval as approval_mod  # noqa: E402
from runtime.mvp_runtime import permission, timeutil  # noqa: E402
from runtime.mvp_runtime.approval_store import ApprovalStore  # noqa: E402
from runtime.mvp_runtime.audit import build_approval_request_audit  # noqa: E402
from runtime.mvp_runtime.binding import bind_task_to_core  # noqa: E402
from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK, EXIT_USAGE, force_utf8_io  # noqa: E402
from runtime.mvp_runtime.control import ControlStore  # noqa: E402
from runtime.mvp_runtime.crypto import execution_stage as es  # noqa: E402
from runtime.mvp_runtime.crypto import live_promotion  # noqa: E402
from runtime.mvp_runtime.errors import ApprovalBlocked, MvpRuntimeError  # noqa: E402
from runtime.mvp_runtime.intake import build_task  # noqa: E402
from runtime.mvp_runtime.policy_fingerprint import policy_safety_identity  # noqa: E402
from runtime.mvp_runtime.state_guard import assert_not_foreign_root_run  # noqa: E402
from runtime.mvp_runtime.store import LEDGER_REL, LedgerStore  # noqa: E402


def _stores(root: Path | None) -> tuple[Path, ApprovalStore, LedgerStore]:
    base = root if root is not None else ROOT
    return base, ApprovalStore.default(base), LedgerStore(base / LEDGER_REL)


def run_show(*, root: Path | None, now: str, as_json: bool) -> int:
    base, approvals, _ = _stores(root)
    status = es.resolve_execution_stage(base, now=now, approval_store=approvals)
    identity = policy_safety_identity()
    try:
        record = es.read_registered_stage(base)
    except MvpRuntimeError:
        record = None
    if as_json:
        sys.stdout.write(json.dumps({"status": status.as_dict(), "record": record, "policy": identity},
                                    ensure_ascii=False, indent=2) + "\n")
        return EXIT_OK
    lines = [
        f"execution stage : {status.stage}" + ("" if status.valid else f"  (READ_ONLY because {status.reason_code})"),
        f"recorded stage  : {status.recorded_stage or 'none'}",
        f"stage id        : {status.stage_id or 'none'}",
        f"valid until     : {status.valid_until or '-'}",
        f"bound to policy : {status.policy_version or '-'}",
        f"running policy  : {(identity or {}).get('policy_version')} {(identity or {}).get('policy_safety_sha256')}",
        "enforcement     : none yet (PR1b makes the entry doors read this)",
    ]
    sys.stdout.write("\n".join(lines) + "\n")
    return EXIT_OK


def run_request(*, root: Path | None, now: str, target: str, registered_by: str, reason: str,
                attestation: str | None, valid_days: int | None) -> dict:
    base, approvals, ledger = _stores(root)
    assert_not_foreign_root_run(root)
    status = es.resolve_execution_stage(base, now=now, approval_store=approvals)
    clean, canary_error = (None, None)
    if target == es.ExecutionStage.LIVE_AUTONOMOUS.value:
        clean, canary_error = live_promotion.clean_canary_order_count(root)
    content = es.plan_transition(
        status, target=target, now=now, registered_by=registered_by, reason=reason,
        valid_days=valid_days, attestation=attestation, clean_canary_orders=clean,
        canary_registry_error=canary_error,
    )
    # `root` is the STATE root (stage record, approvals, ledger, control). The Core binding, the
    # policy and the schemas come from the image's own tree, like every other ask (repo_root=None).
    task = build_task(
        f"실행 단계 전이 검토: {content['from_stage']} -> {content['to_stage']} ({content['transition']})",
        now=now, channel="manual", requester_type="real_thomas", requester_id="Thomas",
        authenticated=True,
    )
    _binding, bound = bind_task_to_core(task, now=now)
    decision = permission.build_execution_stage_permission_decision(bound, content=content, now=now)
    request = approval_mod.build_approval_request(decision, now=now)
    approvals.append_permission_decision(decision)
    approvals.append([request])
    try:
        ledger.append_audit_events(build_approval_request_audit(
            request, now=now, genesis_previous_hash=ledger.last_audit_hash(),
        ))
    except MvpRuntimeError as exc:
        sys.stderr.write(f"WARNING: request audit failed ({exc.reason_code}); the request stands\n")
    return {"approval_id": request["approval_id"], "expires_at": request["validity"]["expires_at"],
            "content": content, "status": status.as_dict()}


def run_confirm(*, root: Path | None, now: str, approval_id: str) -> dict:
    base, approvals, ledger = _stores(root)
    assert_not_foreign_root_run(root)
    control_state = (ControlStore(root) if root is not None else ControlStore.default()).load()
    approval, decision, snapshot = approval_mod.validate_spendable_approval(
        approvals, approval_id, now=now, control_state=control_state,
        expected_scope=permission.EXECUTION_STAGE_PERMISSION_SCOPE,
        kill_action="spending an execution-stage grant", refusal_phrase="the stage is not changed",
        scope_refusal="is not an execution-stage grant",
    )
    if not str(snapshot.get("target_ref") or "").startswith(permission.EXECUTION_STAGE_TARGET_PREFIX):
        raise ApprovalBlocked("EXECUTION_STAGE_NOT_A_STAGE_GRANT",
                              f"{approval_id} is not an execution-stage grant")
    content = dict(snapshot.get("normalized_parameters") or {})
    status_now = es.resolve_execution_stage(base, now=now, approval_store=approvals)
    # Built (and refused) BEFORE anything is spent: a grant that cannot write its record stays APPROVED.
    record = es.record_from_approved(content, status_now=status_now, approval_id=approval_id,
                                     action_fingerprint=str(approval["action_fingerprint"]), now=now)
    with approval_mod.spend_lock(approvals, approval_id):
        fresh = approvals.get(approval_id)
        consumed = approval_mod.build_consumed_record(
            fresh, decision, consumed_at=now, consumption_ref=es.consumption_ref(record["stage_id"]),
        )
        approvals.append([consumed])
    es.write_stage_record(record, base)
    ledger.append_control(es.transition_event(record, previous=status_now, now=now))
    after = es.resolve_execution_stage(base, now=now, approval_store=approvals)
    return {"record": record, "status": after.as_dict()}


def run_demote(*, root: Path | None, now: str, target: str, registered_by: str, reason: str) -> dict:
    base, approvals, ledger = _stores(root)
    assert_not_foreign_root_run(root)
    status = es.resolve_execution_stage(base, now=now, approval_store=approvals)
    record = es.demote_record(status, target=target, registered_by=registered_by, reason=reason, now=now)
    es.write_stage_record(record, base)
    ledger.append_control(es.transition_event(record, previous=status, now=now))
    return {"record": record, "status": es.resolve_execution_stage(base, now=now, approval_store=approvals).as_dict()}


def main(argv: list[str] | None = None) -> int:
    force_utf8_io()
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--show", action="store_true")
    mode.add_argument("--request", action="store_true")
    mode.add_argument("--confirm", action="store_true")
    mode.add_argument("--demote", action="store_true")
    parser.add_argument("--to", choices=es.LADDER)
    parser.add_argument("--registered-by")
    parser.add_argument("--reason")
    parser.add_argument("--attest", help="the evidence a first record stands on")
    parser.add_argument("--valid-days", type=int, help=f"a LIVE stage's end date, 1..{es.MAX_LIVE_VALIDITY_DAYS}")
    parser.add_argument("--approval-id")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--root", type=Path, default=None, help="state root (defaults to the repo)")
    args = parser.parse_args(argv)
    now = timeutil.utc_now_iso()
    try:
        if args.show:
            return run_show(root=args.root, now=now, as_json=args.json)
        if args.request or args.demote:
            if not (args.to and args.registered_by and args.reason):
                sys.stderr.write("USAGE: --request/--demote need --to, --registered-by and --reason\n")
                return EXIT_USAGE
        if args.request:
            out = run_request(root=args.root, now=now, target=args.to, registered_by=args.registered_by,
                              reason=args.reason, attestation=args.attest, valid_days=args.valid_days)
            sys.stdout.write(
                f"ASKED: {out['content']['from_stage']} -> {out['content']['to_stage']} "
                f"({out['content']['transition']}); approval {out['approval_id']} until {out['expires_at']}\n"
                f"Thomas answers /approve {out['approval_id']} (or /reject); then run --confirm --approval-id "
                f"{out['approval_id']}. Nothing has changed.\n"
            )
            return EXIT_OK
        if args.confirm:
            if not args.approval_id:
                sys.stderr.write("USAGE: --confirm needs --approval-id\n")
                return EXIT_USAGE
            out = run_confirm(root=args.root, now=now, approval_id=args.approval_id)
        else:
            out = run_demote(root=args.root, now=now, target=args.to, registered_by=args.registered_by,
                             reason=args.reason)
    except MvpRuntimeError as exc:
        sys.stderr.write(f"BLOCKED {exc.reason_code}: {exc}\n")
        return EXIT_BLOCKED
    status = out["status"]
    sys.stdout.write(
        f"WRITTEN: {out['record']['transition']} {out['record']['previous_stage']} -> {out['record']['stage']} "
        f"({out['record']['stage_id']}); the machine now reads {status['stage']}"
        + ("" if status["valid"] else f" because {status['reason_code']}") + "\n"
    )
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())

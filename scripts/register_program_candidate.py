#!/usr/bin/env python3
"""Operator tool: register an ACCEPTED program candidate in the Program Registry.

The last link before activation (explicit Thomas decision 2026-07-22), on the C8b
verified-never-spent pattern (``promote_strategy_candidates.py``):

    # 1) See what is registrable, author the definition substance, then ASK Thomas:
    python scripts/register_program_candidate.py --list
    python scripts/register_program_candidate.py --request \\
        --candidate-id progcand_abc --definition-input definition.yaml

    # 2) Thomas answers /approve <id> (or /reject) on the verified control channel.

    # 3) Apply the approved registration (the approval is VERIFIED, never consumed):
    python scripts/register_program_candidate.py \\
        --candidate-id progcand_abc --definition-input definition.yaml \\
        --approval-id approval_xyz --registered-by Thomas \\
        --reason "reviewed slice, register as candidate" --confirm

The definition-input YAML supplies ``purpose`` / ``inputs`` / ``outputs``; everything
load-bearing is pinned by the runtime (status candidate, enabled false, no
implementation, effects false). Applying writes the definition file + registry entry
into the WORKING TREE and self-checks them through the canonical resolver — committing
the change is Thomas's PR (no direct main commits). Registration grants nothing:
activation stays a separate APPROVAL_REQUIRED decision, and there is deliberately no
without-approval escape on this door.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from runtime.mvp_runtime import approval as approval_mod  # noqa: E402
from runtime.mvp_runtime import registration as registration_mod  # noqa: E402
from runtime.mvp_runtime import timeutil  # noqa: E402
from runtime.mvp_runtime.approval_store import STORE_REL as APPROVAL_STORE_REL  # noqa: E402
from runtime.mvp_runtime.approval_store import ApprovalStore  # noqa: E402
from runtime.mvp_runtime.audit import build_approval_request_audit  # noqa: E402
from runtime.mvp_runtime.control import ControlStore  # noqa: E402
from runtime.mvp_runtime.errors import MvpRuntimeError  # noqa: E402
from runtime.mvp_runtime.programization import ProgramizationStore  # noqa: E402
from runtime.mvp_runtime.store import LEDGER_REL, LedgerStore  # noqa: E402

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_BLOCKED = 3


def _load_definition_input(path_str: str) -> dict:
    data = yaml.safe_load(Path(path_str).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit("BLOCKED DEFINITION_INPUT_INVALID: definition input must be a mapping")
    return data


def run_request(*, candidate_id: str, definition_input: dict,
                root: Path | None = None, store: ProgramizationStore | None = None,
                now: str | None = None) -> dict:
    """Build + store + audit the R9 ask for this registration (the C8b pattern)."""
    now = now or timeutil.utc_now_iso()
    store = store if store is not None else ProgramizationStore.default()
    prepared = registration_mod.request_registration(
        store, candidate_id, definition_input, now=now, repo_root=root,
    )
    approval_store = ApprovalStore(root / APPROVAL_STORE_REL) if root is not None else ApprovalStore.default()
    approval_store.append_permission_decision(prepared["permission_decision"])
    approval_store.append([prepared["approval_request"]])

    ledger = LedgerStore((root if root is not None else ROOT) / LEDGER_REL)
    try:
        ledger.append_audit_events(build_approval_request_audit(
            prepared["approval_request"], now=now, genesis_previous_hash=ledger.last_audit_hash(),
        ))
    except MvpRuntimeError as exc:
        sys.stderr.write(f"WARNING: request audit failed ({exc.reason_code}); the request stands\n")
    return prepared


def run_apply(*, candidate_id: str, definition_input: dict, approval_id: str,
              registered_by: str, reason: str,
              root: Path | None = None, store: ProgramizationStore | None = None,
              now: str | None = None) -> dict:
    """Verify the APPROVED ask, then write the registration into the working tree."""
    now = now or timeutil.utc_now_iso()
    base = root if root is not None else ROOT

    # Kill switch first: registration mutates governance source.
    state = ControlStore(base).load()
    if not state.execution_allowed:
        raise SystemExit(f"BLOCKED: runtime is {state.mode}; registration refused ({state.refusal_reason_code()})")

    store = store if store is not None else ProgramizationStore.default()
    # Re-derive the definition from CURRENT state (lineage re-checked) — the approval must
    # match this exact content, or anything that changed since Thomas's yes refuses.
    candidate, request = registration_mod._lineage(store, candidate_id)
    definition = registration_mod.build_program_definition(request, definition_input)
    approval_store = ApprovalStore(root / APPROVAL_STORE_REL) if root is not None else ApprovalStore.default()
    try:
        registration_mod.verify_registration_approval(
            approval_store.get(approval_id), definition=definition, now=now,
        )
        applied = registration_mod.apply_registration(definition, repo_root=root)
    except MvpRuntimeError as exc:
        raise SystemExit(f"BLOCKED {exc.reason_code}: {exc.reason}")

    ledger = LedgerStore(base / LEDGER_REL)
    ledger.append_programization_event(registration_mod.build_registration_event(
        applied["entry"], candidate_id=candidate_id, approval_id=approval_id,
        registered_by=registered_by, reason=reason, now=now,
    ))
    return applied


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Register an ACCEPTED program candidate in the Program Registry.")
    parser.add_argument("--list", action="store_true", help="list ACCEPTED candidates + their requests and exit")
    parser.add_argument("--request", action="store_true",
                        help="ASK Thomas: build + store + audit the R9 approval request, then exit")
    parser.add_argument("--candidate-id", help="the ACCEPTED programization candidate id")
    parser.add_argument("--definition-input", help="YAML file with purpose/inputs/outputs")
    parser.add_argument("--approval-id", help="APPROVED approval id from the /approve answer (verified, never consumed)")
    parser.add_argument("--registered-by", help="operator identity")
    parser.add_argument("--reason", help="operator reason (the report)")
    parser.add_argument("--confirm", action="store_true", help="actually write; refused without it")
    args = parser.parse_args(argv)

    if args.list:
        store = ProgramizationStore.default()
        requests = {row.get("candidate_id"): row.get("request", {}) for row in store.read_requests()}
        for candidate_id, c in sorted(store.latest_candidates().items()):
            if c.get("status") != "ACCEPTED":
                continue
            request = requests.get(candidate_id) or {}
            resource = request.get("resource", {})
            print(f"{candidate_id}  pattern={c.get('pattern_id')}  "
                  f"request={request.get('program_request_id') or '-'}  "
                  f"program={resource.get('program_id') or '-'}@{resource.get('program_version') or '-'}")
        return EXIT_OK

    if not (args.candidate_id and args.definition_input):
        print("BLOCKED: --candidate-id and --definition-input are required (or use --list)")
        return EXIT_USAGE
    definition_input = _load_definition_input(args.definition_input)

    if args.request:
        prepared = run_request(candidate_id=args.candidate_id, definition_input=definition_input)
        request = prepared["approval_request"]
        print(approval_mod.request_message(request, prepared["permission_decision"], history=None))
        print(f"\nSTORED: {request['approval_id']} is PENDING until {request['validity']['expires_at']}.")
        print("Thomas answers /approve <id> or /reject <id> on the verified control channel; then re-run "
              f"with --approval-id {request['approval_id']} --confirm.")
        return EXIT_OK

    if not (args.approval_id and args.registered_by and args.reason):
        print("BLOCKED: --approval-id, --registered-by, and --reason are required to apply "
              "(there is no without-approval escape on this door)")
        return EXIT_USAGE
    if not args.confirm:
        print("BLOCKED: registration requires --confirm (an approval is never auto-execution)")
        return EXIT_BLOCKED

    applied = run_apply(
        candidate_id=args.candidate_id, definition_input=definition_input,
        approval_id=args.approval_id, registered_by=args.registered_by, reason=args.reason,
    )
    entry = applied["entry"]
    print(f"REGISTERED: {entry['program_id']}@{entry['version']} -> Program Registry "
          f"(status: candidate, enabled: false; definition: {applied['definition_path']})")
    print("Working-tree change only — commit via branch + PR. Activation stays a separate approval.")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())

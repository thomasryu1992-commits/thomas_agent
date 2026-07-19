#!/usr/bin/env python3
"""Operator helper: activate an OFF-by-default safety flag locally (per-machine).

The MVP keeps every governed safety flag OFF — ``model_invocation``, ``network_access``,
``filesystem_write`` (R8), and ``approval_consumption`` (R10); the capable implementation
is only reachable once a local, integrity-checked activation record exists (see
``runtime/mvp_runtime/safety_gate.py`` and CLAUDE.md "Safety flags are gated"). This
script is the turnkey way an operator writes that record: it records an operator-decision
evidence file, appends the activation to the durable ledger (the activation itself is
audited, not just its later uses), mints the tamper-evident activation record via
``safety_gate.build_activation_record`` (which computes the ``content_sha256``), and
writes it to the gitignored per-machine path the gate reads. TTL is capped at 30 days —
a longer standing enablement is a governance change, not an activation. Deleting the
written record is a live revocation: every gated capability re-checks it at egress.

It never touches any secret: the provider/tool reads its API key from its own env var at
call time; the activation record only authorizes the flag, it does not carry the key.

Example — enable the R3 read-only Brave search for two hours:

    python scripts/activate_safety_flag.py \
        --provider-id brave_search --flags network_access \
        --authority-level P1 --ttl-minutes 120 \
        --reason "Operator decision: enable read-only Brave search for R3 live validation."

Then set the key and run the CLI with the real tool:

    export BRAVE_SEARCH_API_KEY=...            # setx on Windows
    MVP_SEARCH_TOOL=brave_search python -m runtime.mvp_runtime.cli "..."

Nothing here is committed: the evidence file and the activation record live under
``.runtime_governance_state/`` (gitignored). Re-run to refresh an expired activation.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime import safety_gate  # noqa: E402
from runtime.mvp_runtime.errors import MvpRuntimeError, SafetyGateBlocked  # noqa: E402
from runtime.mvp_runtime.events import stamped_event  # noqa: E402
from runtime.mvp_runtime.store import LEDGER_REL, LedgerStore  # noqa: E402

GOV_STATE_REL = ".runtime_governance_state"
EVIDENCE_DIR_REL = f"{GOV_STATE_REL}/safety_flag_evidence"

# The longest grant this script will mint. Enabling a capability is a per-decision,
# expiring act — an uncapped --ttl-minutes let one careless flag turn "OFF by default"
# into a de-facto standing grant (10 years in a one-liner). 30 days matches the longest
# grant actually used in deployment; a longer standing enablement is a governance change,
# not an activation, and must not be expressible here.
MAX_TTL_MINUTES = 30 * 24 * 60


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--provider-id", required=True,
                        help="the provider/tool the activation authorizes, e.g. brave_search / google_ai_studio")
    parser.add_argument("--flags", required=True,
                        help="comma-separated safety flags to enable (model_invocation, "
                             "network_access, filesystem_write, approval_consumption)")
    parser.add_argument("--authority-level", required=True, help="P0..P6 authority level for the activation")
    parser.add_argument("--reason", required=True, help="operator-decision reason recorded as evidence")
    parser.add_argument("--ttl-minutes", type=int, default=120,
                        help=f"activation lifetime in minutes (default 120, max {MAX_TTL_MINUTES} = 30 days)")
    parser.add_argument("--root", type=Path, default=ROOT,
                        help="repository root (default: this repo); the record is written under <root>/.runtime_governance_state")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    flags = [f.strip() for f in args.flags.split(",") if f.strip()]
    if not flags:
        print("ERROR: --flags must list at least one safety flag", file=sys.stderr)
        return 2

    if not 1 <= args.ttl_minutes <= MAX_TTL_MINUTES:
        print(f"ERROR TTL_OUT_OF_RANGE: --ttl-minutes must be 1..{MAX_TTL_MINUTES} "
              f"(30 days), got {args.ttl_minutes}; a standing enablement is a governance "
              "change, not an activation", file=sys.stderr)
        return 2

    root = args.root.resolve()
    now = datetime.now(timezone.utc)
    activated_at = _fmt(now)
    expires_at = _fmt(now + timedelta(minutes=args.ttl_minutes))

    # 1) Record the operator-decision evidence the activation references (a real file the
    #    gate verifies exists). Filename is content-derived so re-runs are stable per reason.
    evidence_dir = root / EVIDENCE_DIR_REL
    evidence_dir.mkdir(parents=True, exist_ok=True)
    evidence_stem = safety_gate.integrity.short_id("evidence", {"provider": args.provider_id, "reason": args.reason})
    evidence_ref = f"{EVIDENCE_DIR_REL}/{evidence_stem}.md"
    (root / evidence_ref).write_text(
        f"# Safety-Flag Activation — Operator Decision\n\n"
        f"Provider/tool: {args.provider_id}\n"
        f"Flags: {', '.join(flags)}\n"
        f"Authority level: {args.authority_level}\n\n"
        f"{args.reason}\n",
        encoding="utf-8",
    )

    # 2) Mint the tamper-evident activation record (computes content_sha256).
    try:
        record = safety_gate.build_activation_record(
            flags=flags,
            provider_id=args.provider_id,
            activated_at=activated_at,
            expires_at=expires_at,
            evidence_ref=evidence_ref,
            authority_level=args.authority_level,
        )
    except SafetyGateBlocked as exc:
        print(f"ERROR: {exc.reason_code}: {exc.reason}", file=sys.stderr)
        return 2

    # 3) Record the activation to the durable ledger BEFORE the grant goes live. Enabling
    #    a capability was the one governance transition with no audit trail at all — the
    #    ledger only ever saw the later *uses*. Event-first is the fail-closed order: a
    #    live grant always has its event, and if the record write below fails instead,
    #    the ledger names an activation that never took effect (loud, not dangerous).
    ledger = LedgerStore(root / LEDGER_REL)
    try:
        ledger.append_control(stamped_event(
            "safety_flag_activation_event.v0", action="safety_flag_activated",
            provider_id=args.provider_id, flags=flags,
            authority_level=args.authority_level, evidence_ref=evidence_ref,
            activated_at=activated_at, expires_at=expires_at,
            activation_sha256=record["content_sha256"], created_at=activated_at,
        ))
    except MvpRuntimeError as exc:
        print(f"ERROR {exc.reason_code}: the activation could not be recorded to the durable "
              f"ledger ({exc.reason}); an unauditable activation is refused (fail-closed)",
              file=sys.stderr)
        return 1

    # 4) Write it to this provider's own gitignored path. One grant per provider, so
    #    activating a second provider leaves the first exactly as it was.
    try:
        activation_path = safety_gate.activation_path(root, args.provider_id)
    except SafetyGateBlocked as exc:
        print(f"ERROR: {exc.reason_code}: {exc.reason}", file=sys.stderr)
        return 2
    activation_path.parent.mkdir(parents=True, exist_ok=True)
    activation_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

    # 5) Verify the gate now authorizes it (fail loudly if not).
    try:
        safety_gate.authorize(flags, provider_id=args.provider_id, now=activated_at, root=root)
    except SafetyGateBlocked as exc:
        print(f"ERROR: wrote a record the gate rejects ({exc.reason_code}): {exc.reason}", file=sys.stderr)
        return 1

    print(f"Activated {args.provider_id} [{', '.join(flags)}] until {expires_at}")
    print(f"  activation: {activation_path.relative_to(root).as_posix()}")
    print(f"  evidence:   {evidence_ref}")
    print(f"  audited:    safety_flag_activated -> {LEDGER_REL}/control_events.jsonl")
    print("The activation/evidence files are gitignored (local, per-machine). Never commit them.")
    print("To revoke before expiry: delete the activation file — every gated capability")
    print("re-checks it at egress, so a running process stops at its next call.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

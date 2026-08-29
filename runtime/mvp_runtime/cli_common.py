"""Shared plumbing for the runtime's CLI entry points.

Six CLIs (intake, operator loop, emergency console, scheduler, memory, approval) each
carried their own copy of the exit codes, the Windows UTF-8 stdio fix, the fail-closed
BLOCKED reporter, and the Safety-Flag Gate authorization banners. Copies drift — the
worst case being a gated capability added to one CLI without its operator-visible
authorization notice. One construction site each, imported everywhere.
"""

from __future__ import annotations

import sys
from typing import Any

from .errors import MvpRuntimeError

EXIT_OK = 0
EXIT_BLOCKED = 2
EXIT_USAGE = 3


def force_utf8_io() -> None:
    """Reconfigure stdio to UTF-8 so non-ASCII I/O survives Windows cp949 consoles."""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except (ValueError, OSError):
                pass


def report_block(exc: MvpRuntimeError) -> int:
    """Report a fail-closed block on stderr and return the BLOCKED exit code."""
    sys.stderr.write(f"BLOCKED {exc.reason_code}: {exc.reason}\n")
    return EXIT_BLOCKED


def gate_banners(**implementations: Any) -> None:
    """Write the operator-visible SAFETY_GATE notice for each capable implementation selected.

    Keyed on the capability attributes every implementation already declares
    (``network_egress``, ``filesystem_write``, and ``model_id`` for a thing that invokes a
    model) rather than an isinstance ladder of concrete classes. The ladder reintroduced the
    exact failure this module exists to prevent — a newly added capable implementation
    silently printing no authorization notice until someone remembered to extend the list.

    The **kwarg names are free-form**, and that is the fix rather than a convenience. This
    used to take one named parameter per role (``channel``/``provider``/``search_tool``/
    ``writer``), which meant the "banners itself by construction" property held for exactly
    the four roles already thought of, and the fifth capability had to be remembered after
    all. It was not: the live order adapter — the only implementation in this repository that
    can move real money — printed nothing, and two more providers in ``operator_cli`` were
    hand-writing their own notices beside the call. Pass any capability under any name and it
    announces itself; the name becomes the label, so callers say what the thing is.

    **Silence is the thing that must be opted into.** ``model_invocation`` used to be claimed
    only for something that *already* declared egress or disk write — because a mock declares a
    ``model_id`` too and must stay quiet — which meant an implementation invoking a real model
    while making no network call announced nothing at all. That is this function's own failure
    mode one level down: a gated capability, silent, because nobody had thought of its shape.
    So carrying a ``model_id`` is now enough to announce, and an implementation that holds one
    without invoking a model says so with ``model_invocation = False``. A forgotten declaration
    now produces a spurious notice rather than a missing one — noisy, but never quiet about a
    real capability.

    An implementation declaring no capability at all is inert and stays silent, so a default
    run is quiet. ``None`` is skipped, so an unselected optional capability needs no guard at
    the call site.
    """
    for name, impl in implementations.items():
        if impl is None:
            continue
        flags: list[str] = []
        model_id = getattr(impl, "model_id", None)
        if getattr(impl, "model_invocation", model_id is not None):
            flags.append("model_invocation")
        if getattr(impl, "network_egress", False):
            flags.append("network_access")
        if getattr(impl, "filesystem_write", False):
            flags.append("filesystem_write")
        if not flags:
            continue
        detail = f"{model_id}; " if model_id is not None else ""
        sys.stderr.write(
            f"SAFETY_GATE: {name.replace('_', ' ')} authorized ({detail}{', '.join(flags)})\n"
        )


def record_audit_gap(ledger: Any, gap_kind: str, exc: MvpRuntimeError, *,
                     subject_ref: str, now: str) -> None:
    """Durably note that something happened whose audit event could not be written.

    Best-effort by construction: this runs *because* a ledger write already failed, so it
    may fail too. The stderr warning stays either way — this only adds the durable half
    when it can. One copy for every asking CLI; it was copied verbatim between the
    approval and trial CLIs before it lived here.
    """
    from .audit import build_audit_gap_record  # local: cli_common stays a leaf for the CLIs

    try:
        ledger.append_block(build_audit_gap_record(
            gap_kind, reason_code=exc.reason_code, subject_ref=subject_ref,
            now=now, detail=exc.reason,
        ))
    except MvpRuntimeError:
        sys.stderr.write("WARNING: the audit gap itself could not be recorded\n")


def store_and_present_approval_request(permission_decision: Any, request: Any, *,
                                       now: str) -> None:
    """Persist, audit, and render one R9 ask — the block every asking CLI repeated.

    The order is the contract: the ask becomes durable in the approval store FIRST, so an
    audit failure demotes to a durable gap record plus a warning while the request stands;
    the decision-history enrichment is advisory, so its failure is reported inline and
    never blocks the ask. Callers add their own trailing STORED guidance — that text is
    what differs between the surfaces.
    """
    from . import approval  # local: cli_common stays a leaf for the CLIs
    from .approval_store import ApprovalStore
    from .audit import build_approval_request_audit
    from .store import LedgerStore

    store = ApprovalStore.default()
    store.append_permission_decision(permission_decision)
    store.append([request])

    ledger = LedgerStore.default()
    try:
        ledger.append_audit_events(build_approval_request_audit(
            request, now=now, genesis_previous_hash=ledger.last_audit_hash(),
        ))
        sys.stderr.write(f"LEDGER: approval request audited to {ledger.root}\n")
    except MvpRuntimeError as exc:
        # The ask is already durable in the approval store, so it stands — but the gap must
        # not live only in a stderr line nobody keeps. Record it durably (different file, so
        # a broken audit ledger does not take this with it).
        record_audit_gap(ledger, "approval_request", exc, subject_ref=request["approval_id"], now=now)
        sys.stderr.write(f"WARNING: request audit failed ({exc.reason_code}); the request stands\n")

    history = None
    history_failure = ""
    try:
        history = approval.decision_history(store, request)
    except MvpRuntimeError as exc:
        history_failure = f"\n과거 유사 결정: 조회 실패 ({exc.reason_code}) — 이력 없이 요청합니다\n"

    sys.stdout.write(approval.request_message(request, permission_decision, history=history) + "\n")
    if history_failure:
        sys.stdout.write(history_failure)

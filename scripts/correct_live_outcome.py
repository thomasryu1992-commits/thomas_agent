#!/usr/bin/env python3
"""Correct one live outcome row — the governed door.

    1) See what could be corrected (read-only):
        python -m scripts.correct_live_outcome --list

    2) Ask Thomas (stores the PENDING approval; performs nothing):
        python -m scripts.correct_live_outcome --request \
            --outcome-id live_out_e56cf310dcc07d9c6edd --disposition SUPERSEDE \
            --reason "Close-All exit filled 0.002 against a book of 0.001."

    3) Execute the approved correction (the approval is VERIFIED, never consumed):
        python -m scripts.correct_live_outcome --confirm \
            --outcome-id live_out_e56cf310dcc07d9c6edd --disposition SUPERSEDE \
            --reason "..." --approval-id approval_abc123 --corrected-by thomas

**The corrected figures are never arguments.** A `DERIVED` correction recomputes them from
the target row — `(exit - entry) * quantity` over the row's own `risk_usdt` — and the read
path refuses the record if they disagree. What is asked for and approved is the judgement:
which row, void or supersede, and why.

Design: `docs/proposals/LIVE_OUTCOME_CORRECTION_RECORD_V0.2.md`. The refusing half lives in
`runtime/mvp_runtime/crypto/live_correction.py`.

**The ask is built here rather than in that module on purpose.** `live_correction` is imported
by `live_pnl.read_live_outcomes`, which every consumer of the live history passes through;
putting the task/permission/approval machinery in it would pull `permission.py` and its graph
into the breaker, the cycle and the promotion board, none of which ever ask for anything.
Measured before writing it this way: that graph is not in the read path today.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime import timeutil  # noqa: E402
from runtime.mvp_runtime.approval_store import STORE_REL as APPROVAL_STORE_REL  # noqa: E402
from runtime.mvp_runtime.approval_store import ApprovalStore  # noqa: E402
from runtime.mvp_runtime.audit import build_approval_request_audit  # noqa: E402
from runtime.mvp_runtime.binding import bind_task_to_core  # noqa: E402
from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK, EXIT_USAGE, force_utf8_io  # noqa: E402
from runtime.mvp_runtime.crypto import live_correction as LC  # noqa: E402
from runtime.mvp_runtime.crypto.live_pnl import read_live_outcomes_raw, state_dir  # noqa: E402
from runtime.mvp_runtime.errors import MvpRuntimeError  # noqa: E402
from runtime.mvp_runtime.intake import build_task  # noqa: E402
from runtime.mvp_runtime.permission import (  # noqa: E402
    build_live_outcome_correction_permission_decision,
)
from runtime.mvp_runtime import approval as approval_mod  # noqa: E402
from runtime.mvp_runtime.state_guard import assert_not_foreign_root_run  # noqa: E402
from runtime.mvp_runtime.store import LEDGER_REL, LedgerStore  # noqa: E402


def _num(value: object) -> str:
    """A figure, or ``?`` where the row does not have one.

    The rows this screen is FOR are the rows something is wrong with, and a row can be missing
    the very field being printed — the two live rows with no recorded risk carry
    ``result_R: None``. Formatting those with ``:+.4f`` raised `TypeError` and took the whole
    listing down at the seventh row, on the real ledger, in the deployed runtime. The screen an
    operator opens first must survive the data it exists to describe.
    """
    try:
        return f"{float(value):+.4f}"          # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "?"


def _target(outcome_id: str, root: Path | None) -> dict:
    """The row this correction is about, read through the VERIFIED file read.

    `read_live_outcomes_raw`, not the corrected view: a row that already carries a correction
    must present its ORIGINAL hash here, or the second correction would pin a hash that is not
    in the file and rule 2 would refuse it at the next read — after Thomas had approved it.
    """
    for row in read_live_outcomes_raw(root):
        if row.get("outcome_id") == outcome_id:
            return row
    raise MvpRuntimeError(LC.CORRECTION_TARGET_MISSING,
                          f"no live outcome {outcome_id} in the history")


def _corrections_path(root: Path | None) -> Path:
    return state_dir(root) / LC.CORRECTIONS_FILENAME


def run_list(*, root: Path | None = None) -> list[dict]:
    """Every closed row, with what its own prices say — the disagreements first.

    Read-only and gate-free: this is the screen an operator reads BEFORE deciding anything,
    so it states the arithmetic and never a recommendation. A row that disagrees with its own
    prices is a candidate for correction, not a verdict that it should be corrected.
    """
    rows = []
    corrected = set()
    path = _corrections_path(root)
    if path.exists():
        corrected = {c["corrects_outcome_id"] for c in LC.read_corrections(path)}
    for row in read_live_outcomes_raw(root):
        if row.get("outcome_closed") is not True:
            continue
        recomputed = LC._recompute(row)
        recorded = row.get("realized_pnl_usdt")
        entry = {
            "outcome_id": row.get("outcome_id"), "symbol": row.get("symbol"),
            "recorded_realized_pnl_usdt": recorded, "recorded_result_R": row.get("result_R"),
            "already_corrected": row.get("outcome_id") in corrected,
        }
        if recomputed is None:
            entry.update(derivable=False, agrees=None)
        else:
            realized, result_r = recomputed
            agrees = (isinstance(recorded, (int, float)) and not isinstance(recorded, bool)
                      and abs(recorded - realized) <= LC._tolerance(row))
            entry.update(derivable=True, agrees=agrees,
                         derived_realized_pnl_usdt=round(realized, 8),
                         derived_result_R=round(result_r, 8))
        rows.append(entry)
    rows.sort(key=lambda r: (r["agrees"] is not False, str(r["outcome_id"])))
    return rows


def run_request(*, outcome_id: str, disposition: str, reason: str,
                root: Path | None = None, now: str | None = None) -> dict:
    """Build + store + audit the ask. Performs nothing."""
    now = now or timeutil.utc_now_iso()
    target = _target(outcome_id, root)
    # Built here so the content hash binds the figures the CONFIRM will recompute. If the row
    # changes between ask and confirm, the rebuilt record hashes differently and the approval
    # stops verifying — which is rule 2 asserted one door earlier, at the ask.
    preview = LC.build_correction(
        target=target, disposition=disposition, reason_code="OUTCOME_QUANTITY_MISMATCH",
        reason=reason, approval_id="pending", corrected_by="pending",
        previous_record_sha256=None, now=now,
    )
    content = LC.content_sha256(preview)
    task = build_task(
        f"라이브 아웃컴 정정 검토: {outcome_id} ({disposition})",
        now=now, channel="manual", requester_type="real_thomas", requester_id="Thomas",
        authenticated=True, repo_root=root,
    )
    _binding, bound = bind_task_to_core(task, repo_root=root, now=now)
    decision = build_live_outcome_correction_permission_decision(
        bound, corrects_outcome_id=outcome_id,
        corrects_record_sha256=str(target.get("record_sha256")),
        disposition=disposition, reason=reason, content_sha256=content,
        now=now, repo_root=root,
    )
    request = approval_mod.build_approval_request(decision, now=now, repo_root=root)
    store = ApprovalStore(root / APPROVAL_STORE_REL) if root is not None else ApprovalStore.default()
    store.append_permission_decision(decision)
    store.append([request])
    ledger = LedgerStore((root if root is not None else ROOT) / LEDGER_REL)
    try:
        ledger.append_audit_events(build_approval_request_audit(
            request, now=now, genesis_previous_hash=ledger.last_audit_hash()))
    except MvpRuntimeError as exc:
        sys.stderr.write(f"WARNING: request audit failed ({exc.reason_code}); the request stands\n")
    return {"approval_request": request, "permission_decision": decision,
            "content_sha256": content, "preview": preview}


def run_confirm(*, outcome_id: str, disposition: str, reason: str, approval_id: str,
                corrected_by: str, root: Path | None = None, now: str | None = None) -> dict:
    """Verify the approval binds THIS correction, then append it. No escape hatch.

    `promote_strategy_candidates` has `--without-approval` because a pool change can be a
    recovery action on a runtime that is already stuck. This has none: a correction is never
    the thing standing between an operator and a working system — the uncorrected figure is
    what the runtime has been reading all along — so an escape here would only ever be a way
    to change a money figure without being asked to justify it.
    """
    now = now or timeutil.utc_now_iso()
    target = _target(outcome_id, root)
    record = LC.build_correction(
        target=target, disposition=disposition, reason_code="OUTCOME_QUANTITY_MISMATCH",
        reason=reason, approval_id=approval_id, corrected_by=corrected_by,
        previous_record_sha256=None, now=now,
    )
    store = ApprovalStore(root / APPROVAL_STORE_REL) if root is not None else ApprovalStore.default()
    # The same check the read path will make on every read from now on, made once here so a
    # correction that would fail it is never written — a written-but-refused correction fails
    # the WHOLE live history closed, which is a far worse place to discover the mismatch.
    LC._verify_approval(record, {approval_id: store.get(approval_id) or {}})
    LC.append_correction(_corrections_path(root), record)
    return {"correction": record}


def main(argv: list[str] | None = None) -> int:
    force_utf8_io()
    parser = argparse.ArgumentParser(description="Correct one live outcome row (governed).")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--list", action="store_true", help="show what could be corrected")
    mode.add_argument("--request", action="store_true", help="ask Thomas")
    mode.add_argument("--confirm", action="store_true", help="execute an approved correction")
    parser.add_argument("--outcome-id")
    parser.add_argument("--disposition", choices=[LC.VOID, LC.SUPERSEDE])
    parser.add_argument("--reason")
    parser.add_argument("--approval-id")
    parser.add_argument("--corrected-by")
    args = parser.parse_args(argv)
    assert_not_foreign_root_run()

    try:
        if args.list:
            for row in run_list():
                mark = "OK " if row["agrees"] else ("?? " if row["agrees"] is None else "!! ")
                extra = ""
                if row.get("derivable"):
                    extra = (f" | prices give {_num(row['derived_realized_pnl_usdt'])} USDT"
                             f" / {_num(row['derived_result_R'])}R")
                if row["already_corrected"]:
                    extra += "  [corrected]"
                print(f"{mark}{row['outcome_id']} {row['symbol']} "
                      f"recorded {_num(row['recorded_realized_pnl_usdt'])} USDT"
                      f" / {_num(row['recorded_result_R'])}R{extra}")
            return EXIT_OK
        missing = [n for n, v in (("--outcome-id", args.outcome_id),
                                  ("--disposition", args.disposition),
                                  ("--reason", args.reason)) if not v]
        if missing:
            sys.stderr.write(f"BLOCKED: {', '.join(missing)} required\n")
            return EXIT_USAGE
        if args.request:
            prepared = run_request(outcome_id=args.outcome_id, disposition=args.disposition,
                                   reason=args.reason)
            request = prepared["approval_request"]
            print(f"approval requested: {request.get('approval_id')}")
            print(f"  content_sha256 {prepared['content_sha256']}")
            preview = prepared["preview"]
            if args.disposition == LC.SUPERSEDE:
                print(f"  would record {preview['corrected_realized_pnl_usdt']:+.4f} USDT"
                      f" / {preview['corrected_result_R']:+.4f}R (DERIVED)")
            print("  Thomas: /approve to authorize; then re-run with --confirm --approval-id")
            return EXIT_OK
        if not args.approval_id or not args.corrected_by:
            sys.stderr.write("BLOCKED: --confirm needs --approval-id and --corrected-by\n")
            return EXIT_USAGE
        result = run_confirm(outcome_id=args.outcome_id, disposition=args.disposition,
                             reason=args.reason, approval_id=args.approval_id,
                             corrected_by=args.corrected_by)
        print(f"correction appended: {result['correction']['correction_id']}")
        return EXIT_OK
    except MvpRuntimeError as exc:
        sys.stderr.write(f"BLOCKED: {exc.reason_code}: {exc}\n")
        return EXIT_BLOCKED


if __name__ == "__main__":
    raise SystemExit(main())

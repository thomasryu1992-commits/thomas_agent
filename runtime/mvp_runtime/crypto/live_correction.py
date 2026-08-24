"""Corrections for live outcome rows that are wrong and cannot be edited.

`live_outcomes.jsonl` is fsync append-only and every row carries a self-hash, so a row whose
numbers are wrong stays wrong: `live_out_e56cf310dcc07d9c6edd` records `+398.03R` against a
true `-0.887R`, and it passes its own `record_sha256` because the corruption happened before
the hash was taken. Editing it would pass a recomputed hash and leave no record that anyone
changed a money figure, on the one ledger whose purpose is to be unchangeable.

So a correction is a record of its own, in a file of its own, and the outcome read path applies
it without the outcome file moving. Design: `docs/proposals/LIVE_OUTCOME_CORRECTION_RECORD_V0.2.md`.

**This module refuses; it does not write.** Producing a correction is a governed action
(`--request` → Thomas `/approve` → `--confirm`), and its door is a separate increment. What is
here is the half that has to exist first: until a bad correction can be refused, there must be
no way to write one.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping

from runtime.read_only_kernel import integrity

from .. import jsonl, timeutil
from ..errors import ToolError
from ..filelock import locked

CORRECTIONS_FILENAME = "live_outcome_corrections.jsonl"
SCHEMA_VERSION = "live_outcome_correction.v0.2"
CORRECTION_ACTION_TYPE = "crypto.live_outcome.correction"

# Reading the corrections file itself. Distinct from the outcome file's codes so an operator
# reading a halted runtime knows which of the two stores failed — they fail the same consumers.
CORRECTIONS_UNREADABLE = "LIVE_CORRECTIONS_UNREADABLE"
CORRECTION_TAMPERED = "LIVE_CORRECTION_TAMPERED"
CORRECTION_SCHEMA_INVALID = "LIVE_CORRECTION_SCHEMA_INVALID"

# The five rules of V0.2 §2-6.
CORRECTION_TARGET_MISSING = "CORRECTION_TARGET_MISSING"
CORRECTION_TARGET_CHANGED = "CORRECTION_TARGET_CHANGED"
CORRECTION_AMBIGUOUS = "CORRECTION_AMBIGUOUS"
CORRECTION_UNAPPROVED = "CORRECTION_UNAPPROVED"
CORRECTION_ARITHMETIC_DISAGREES = "CORRECTION_ARITHMETIC_DISAGREES"

VOID = "VOID"
SUPERSEDE = "SUPERSEDE"
DERIVED = "DERIVED"
ATTESTED = "ATTESTED"

# Closed schema. A key not named here is a refusal rather than something carried along, for the
# reason the whole record type exists: this file changes money figures, so a field nobody
# validated must not ride in beside the ones that were.
_REQUIRED = frozenset({
    "schema_version", "correction_id", "corrects_outcome_id", "corrects_record_sha256",
    "disposition", "basis", "reason_code", "reason", "approval_id", "corrected_by",
    "created_at_utc", "previous_record_sha256", "record_sha256",
})
_SUPERSEDE_ONLY = frozenset({"corrected_realized_pnl_usdt", "corrected_result_R"})
_OPTIONAL = _SUPERSEDE_ONLY | {"evidence"}


def content_sha256(record: Mapping[str, Any]) -> str:
    """What an approval binds: the effect, not the paperwork.

    Deliberately excludes `correction_id`, `created_at_utc`, `reason`, `evidence`, the chain
    fields and the record hash — none of those change what happens to the money, and binding
    them would mean an approval could not survive a re-issued request that says the same thing.
    It DOES include `corrects_record_sha256`: an approval that named only the outcome id would
    still verify after the target row changed underneath it, which is exactly the drift rule 2
    exists to stop.
    """
    return integrity.sha256_record({
        "action_type": CORRECTION_ACTION_TYPE,
        "corrects_outcome_id": record.get("corrects_outcome_id"),
        "corrects_record_sha256": record.get("corrects_record_sha256"),
        "disposition": record.get("disposition"),
        "basis": record.get("basis"),
        "corrected_realized_pnl_usdt": record.get("corrected_realized_pnl_usdt"),
        "corrected_result_R": record.get("corrected_result_R"),
    })


def _validate_shape(record: Mapping[str, Any], lineno: int) -> None:
    """The closed schema, plus the two shapes `disposition` and `basis` imply."""
    keys = set(record)
    missing = _REQUIRED - keys
    if missing:
        raise ToolError(CORRECTION_SCHEMA_INVALID,
                        f"correction line {lineno} is missing {sorted(missing)}")
    unknown = keys - _REQUIRED - _OPTIONAL
    if unknown:
        raise ToolError(CORRECTION_SCHEMA_INVALID,
                        f"correction line {lineno} carries unknown keys {sorted(unknown)}")
    if record["schema_version"] != SCHEMA_VERSION:
        raise ToolError(CORRECTION_SCHEMA_INVALID,
                        f"correction line {lineno} is {record['schema_version']!r}, not {SCHEMA_VERSION!r}")
    if record["disposition"] not in (VOID, SUPERSEDE):
        raise ToolError(CORRECTION_SCHEMA_INVALID,
                        f"correction line {lineno} has disposition {record['disposition']!r}")
    # ATTESTED is designed and deliberately NOT built (V0.2 §5-1): it is the path where nothing
    # but the approval hash vouches for the numbers, and the one correction this runtime needs
    # is DERIVED. Refusing it is how the deferral is enforced rather than merely intended — a
    # schema that accepted it would let an unverifiable correction through the moment someone
    # wrote one, which is the door V0.2 argued should not be opened before it has a use.
    if record["basis"] == ATTESTED:
        raise ToolError(CORRECTION_SCHEMA_INVALID,
                        f"correction line {lineno} is ATTESTED, which is deferred by design "
                        f"(V0.2 §5-1) — no code path verifies its figures")
    if record["basis"] != DERIVED:
        raise ToolError(CORRECTION_SCHEMA_INVALID,
                        f"correction line {lineno} has basis {record['basis']!r}")
    supplied = keys & _SUPERSEDE_ONLY
    if record["disposition"] == SUPERSEDE:
        if supplied != _SUPERSEDE_ONLY:
            raise ToolError(CORRECTION_SCHEMA_INVALID,
                            f"correction line {lineno} SUPERSEDEs without both corrected_* figures")
        for key in sorted(_SUPERSEDE_ONLY):
            value = record[key]
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ToolError(CORRECTION_SCHEMA_INVALID,
                                f"correction line {lineno} has non-numeric {key}")
    elif supplied:
        # A VOID row carrying figures is two instructions in one record, and the file cannot
        # say which was meant. Refusing is cheaper than picking.
        raise ToolError(CORRECTION_SCHEMA_INVALID,
                        f"correction line {lineno} VOIDs but supplies {sorted(supplied)}")


def read_corrections(path: Path) -> list[dict[str, Any]]:
    """Every correction, oldest first, self-hash and CHAIN verified. Missing file = empty.

    **Why a chain here when the outcome file has none.** A correction is the only record whose
    ABSENCE changes a money figure: delete the row that supersedes
    `live_out_e56cf310dcc07d9c6edd` and the runtime silently returns to `+398.03R`, and that
    reversion is indistinguishable from a runtime that was never corrected. Per-row self-hashes
    cannot see a deletion — only a chain can. The cost is the drift risk of two hashing schemes
    in one subsystem, which is real and was weighed; the file holds single-digit rows, so the
    chain is cheap where it would not be on the outcome store.

    Convention borrowed from the audit ledger — `previous_record_sha256`, genesis ``None`` —
    but not its machinery, which is shaped around audit events' fingerprint payloads and does
    not generalise to a plain record.
    """
    corrections: list[dict[str, Any]] = []
    previous: str | None = None
    seen_ids: set[str] = set()
    for lineno, record in jsonl.iter_numbered(
        path, read_code=CORRECTIONS_UNREADABLE, label="live outcome corrections", exc_type=ToolError,
    ):
        if not isinstance(record, dict):
            raise ToolError(CORRECTION_SCHEMA_INVALID, f"correction line {lineno} is not an object")
        _validate_shape(record, lineno)
        stored = record["record_sha256"]
        body = {k: v for k, v in record.items() if k != "record_sha256"}
        if not isinstance(stored, str) or integrity.sha256_record(body) != stored:
            raise ToolError(CORRECTION_TAMPERED, f"correction line {lineno} fails its self-hash")
        if record["previous_record_sha256"] != previous:
            raise ToolError(
                CORRECTION_TAMPERED,
                f"correction line {lineno} chains to {record['previous_record_sha256']!r}, "
                f"but the record before it hashes to {previous!r} — a correction has been "
                f"removed or reordered",
            )
        correction_id = record["correction_id"]
        if correction_id in seen_ids:
            raise ToolError(CORRECTION_TAMPERED, f"duplicate correction_id: {correction_id}")
        seen_ids.add(correction_id)
        previous = stored
        corrections.append(record)
    return corrections


def _recompute(outcome: Mapping[str, Any]) -> tuple[float, float] | None:
    """`(realized, R)` re-derived from the target row alone, or ``None`` if it cannot be.

    The same expression `live_promotion._pnl_agrees_with_prices` evaluates on every row of the
    evidence board: ``realized == (exit - entry) * quantity``, negated when the row closed a
    SHORT. `side` is the CLOSING order's side, so ``SELL`` closed a LONG. This is what makes a
    DERIVED correction verifiable — the figures are the row's own arithmetic, not a number
    imported from outside the ledger.
    """
    quantity, entry = outcome.get("quantity"), outcome.get("entry_price")
    exit_price, risk = outcome.get("exit_price"), outcome.get("risk_usdt")
    values = (quantity, entry, exit_price, risk)
    if any(not isinstance(v, (int, float)) or isinstance(v, bool) for v in values):
        return None
    if not risk:
        return None
    direction = 1.0 if str(outcome.get("side")).upper() == "SELL" else -1.0
    realized = (float(exit_price) - float(entry)) * float(quantity) * direction
    return realized, realized / float(risk)


def _tolerance(outcome: Mapping[str, Any]) -> float:
    """#753's tolerance, not a second constant for the same arithmetic.

    `max(notional * 1e-6, 1e-6)`. The measurement that set it holds unchanged here: across the
    28 rows on this machine the 27 consistent ones sit within 2e-16 of notional and the odd one
    misses by 99.8%, so this clears float noise by ten orders of magnitude and still catches an
    error a millionth the size of the one that prompted the record type.
    """
    try:
        return max(abs(float(outcome["quantity"]) * float(outcome["entry_price"])) * 1e-6, 1e-6)
    except (KeyError, TypeError, ValueError):
        return 1e-6


def verify_correction(
    correction: Mapping[str, Any],
    *,
    outcomes_by_id: Mapping[str, Mapping[str, Any]],
    approvals: Mapping[str, Mapping[str, Any]] | None,
) -> None:
    """Rules 1, 2, 4 and 5 of V0.2 §2-6, in order. Raises `ToolError`, or returns.

    Rule 3 (ambiguity) is not here because it is a property of the SET, not of one record;
    `apply_corrections` holds it.
    """
    target = outcomes_by_id.get(correction["corrects_outcome_id"])
    if target is None:
        raise ToolError(CORRECTION_TARGET_MISSING,
                        f"correction {correction['correction_id']} names outcome "
                        f"{correction['corrects_outcome_id']}, which is not in the history")
    if target.get("record_sha256") != correction["corrects_record_sha256"]:
        raise ToolError(CORRECTION_TARGET_CHANGED,
                        f"correction {correction['correction_id']} pins "
                        f"{correction['corrects_record_sha256']}, but the row now hashes to "
                        f"{target.get('record_sha256')}")
    _verify_approval(correction, approvals)
    if correction["disposition"] != SUPERSEDE:
        return
    recomputed = _recompute(target)
    if recomputed is None:
        raise ToolError(CORRECTION_ARITHMETIC_DISAGREES,
                        f"correction {correction['correction_id']} is DERIVED, but the target "
                        f"row does not carry the terms to re-derive its figures")
    realized, result_r = recomputed
    tolerance = _tolerance(target)
    if abs(float(correction["corrected_realized_pnl_usdt"]) - realized) > tolerance:
        raise ToolError(CORRECTION_ARITHMETIC_DISAGREES,
                        f"correction {correction['correction_id']} says realized "
                        f"{correction['corrected_realized_pnl_usdt']}, the row's own prices give "
                        f"{realized:.8f}")
    # R inherits the tolerance divided by the risk it is expressed in, so one tolerance governs
    # both figures rather than a second constant governing the second.
    risk = abs(float(target["risk_usdt"]))
    if abs(float(correction["corrected_result_R"]) - result_r) > tolerance / risk:
        raise ToolError(CORRECTION_ARITHMETIC_DISAGREES,
                        f"correction {correction['correction_id']} says {correction['corrected_result_R']}R, "
                        f"the row's own prices give {result_r:.8f}R")


def _verify_approval(
    correction: Mapping[str, Any], approvals: Mapping[str, Mapping[str, Any]] | None
) -> None:
    """Rule 4. The approval must exist, be APPROVED, snapshot a correction, and bind THIS one.

    **The validity window is deliberately not checked.** A promotion approval is verified once,
    at the install, so its 15-minute window is the right question there. A correction is
    verified on EVERY read of the live history, forever — checking expiry would mean the
    correction stops applying a quarter of an hour after it was made and the whole live history
    fails closed from then on, permanently, for having been correctly authorized. The window
    governs when a correction may be WRITTEN, which is the door's question, not this one.

    ``approvals`` of ``None`` means the caller could not read the store. That is a refusal, not
    a pass: an unreadable approval store must not be the reason an unapproved correction takes
    effect.
    """
    approval_id = correction["approval_id"]
    if not isinstance(approval_id, str) or not approval_id:
        raise ToolError(CORRECTION_UNAPPROVED,
                        f"correction {correction['correction_id']} carries no approval id")
    if approvals is None:
        raise ToolError(CORRECTION_UNAPPROVED,
                        f"correction {correction['correction_id']} cannot be checked — the "
                        f"approval store is unreadable")
    approval = approvals.get(approval_id)
    if approval is None:
        raise ToolError(CORRECTION_UNAPPROVED, f"no approval record {approval_id}")
    if approval.get("status") != "APPROVED":
        raise ToolError(CORRECTION_UNAPPROVED,
                        f"approval {approval_id} is {approval.get('status')}, not APPROVED")
    snapshot = approval.get("approved_action_snapshot") or {}
    if snapshot.get("action_type") != CORRECTION_ACTION_TYPE:
        raise ToolError(CORRECTION_UNAPPROVED,
                        f"approval {approval_id} snapshots {snapshot.get('action_type')!r}, "
                        f"not a live outcome correction")
    if snapshot.get("content_sha256") != content_sha256(correction):
        raise ToolError(CORRECTION_UNAPPROVED,
                        f"approval {approval_id} binds a different correction (target row, "
                        f"disposition, basis or figures changed)")


def apply_corrections(
    outcomes: Iterable[Mapping[str, Any]],
    corrections: Iterable[Mapping[str, Any]],
    *,
    approvals: Mapping[str, Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    """The corrected VIEW of the live history. The outcome file is not touched.

    Every correction is verified before any is applied, so a file with one bad row corrects
    nothing rather than corrects partially — a half-applied set is a history no consumer can
    reason about, and every consumer of this one is a risk decision.
    """
    rows = [dict(o) for o in outcomes]
    pending = list(corrections)
    if not pending:
        return rows
    by_id = {o.get("outcome_id"): o for o in rows if isinstance(o.get("outcome_id"), str)}
    # Rule 3 first, over the WHOLE set. Two corrections for one row cannot both be right and the
    # file does not say which supersedes which — a chain orders them but says nothing about
    # intent. Checked in its own pass so the answer does not depend on which of the two also
    # happens to fail some other rule: "you have two corrections for this row" is the finding,
    # and an approval error reported in its place would send the operator to the wrong file.
    targets: dict[str, Mapping[str, Any]] = {}
    for correction in pending:
        target_id = correction["corrects_outcome_id"]
        if target_id in targets:
            raise ToolError(CORRECTION_AMBIGUOUS,
                            f"outcome {target_id} has more than one correction "
                            f"({targets[target_id]['correction_id']}, {correction['correction_id']})")
        targets[target_id] = correction
    for correction in pending:
        verify_correction(correction, outcomes_by_id=by_id, approvals=approvals)
    corrected: list[dict[str, Any]] = []
    for row in rows:
        correction = targets.get(row.get("outcome_id"))
        if correction is None:
            corrected.append(row)
            continue
        if correction["disposition"] == VOID:
            continue
        row["realized_pnl_usdt"] = correction["corrected_realized_pnl_usdt"]
        row["result_R"] = correction["corrected_result_R"]
        # Stamped so a consumer holding the corrected view can tell it apart from the file, and
        # name the record that changed it, without re-reading either store.
        row["corrected_by_correction_id"] = correction["correction_id"]
        corrected.append(row)
    return corrected


def build_correction(
    *,
    target: Mapping[str, Any],
    disposition: str,
    reason_code: str,
    reason: str,
    approval_id: str,
    corrected_by: str,
    previous_record_sha256: str | None,
    now: str | None = None,
) -> dict[str, Any]:
    """A DERIVED correction for ``target``, with its figures computed HERE.

    The caller supplies the judgement — which row, void or supersede, why — and never the
    numbers. That is what `basis: DERIVED` means and what rule 5 enforces on the way back in;
    building the record the same way closes the loop, so the door cannot submit figures that
    the read path will then refuse.
    """
    if disposition not in (VOID, SUPERSEDE):
        raise ToolError(CORRECTION_SCHEMA_INVALID, f"disposition {disposition!r}")
    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "correction_id": integrity.short_id("live_corr", {
            "outcome_id": target.get("outcome_id"), "disposition": disposition,
            "at": now or timeutil.utc_now_iso(),
        }),
        "corrects_outcome_id": target.get("outcome_id"),
        "corrects_record_sha256": target.get("record_sha256"),
        "disposition": disposition,
        "basis": DERIVED,
        "reason_code": reason_code,
        "reason": reason,
        "approval_id": approval_id,
        "corrected_by": corrected_by,
        "created_at_utc": now or timeutil.utc_now_iso(),
        "previous_record_sha256": previous_record_sha256,
    }
    if disposition == SUPERSEDE:
        recomputed = _recompute(target)
        if recomputed is None:
            raise ToolError(CORRECTION_ARITHMETIC_DISAGREES,
                            f"outcome {target.get('outcome_id')} does not carry the terms to "
                            f"re-derive its figures, so no DERIVED correction can be built")
        realized, result_r = recomputed
        record["corrected_realized_pnl_usdt"] = round(realized, 8)
        record["corrected_result_R"] = round(result_r, 8)
        record["evidence"] = {
            "quantity": target.get("quantity"), "entry_price": target.get("entry_price"),
            "exit_price": target.get("exit_price"), "risk_usdt": target.get("risk_usdt"),
            "side": target.get("side"),
            "recorded_realized_pnl_usdt": target.get("realized_pnl_usdt"),
            "recorded_result_R": target.get("result_R"),
        }
    record["record_sha256"] = integrity.sha256_record(record)
    return record


def append_correction(path: Path, record: Mapping[str, Any]) -> None:
    """Append one correction, chained onto whatever the file ends with RIGHT NOW.

    The chain link is recomputed here, under the lock, rather than trusted from the caller.
    The caller read the tip when it started building; between then and now another process may
    have appended, and two records both pointing at the same stale tip is a FORK — an honest
    file that `read_corrections` reports as tampered, indistinguishable from a real deletion.
    The audit ledger re-anchors its segments at persist time for exactly this reason
    (`audit.rechain_events`); this is the same rule on a smaller record.

    Re-verifies the whole file after linking and before writing, so a correction can never be
    appended onto a chain that is already broken — the append would otherwise bury the break
    under a valid-looking record.

    fsync'd like the outcome ledger it corrects: a correction that reaches the disk buffer but
    not the disk would let the breaker read the uncorrected figure again after a crash, which
    is the state this whole record type exists to leave behind.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with locked(path.with_suffix(".lock"), code="LIVE_STATE_LOCKED",
                label="live outcome corrections"):
        existing = read_corrections(path)
        body = {k: v for k, v in record.items() if k != "record_sha256"}
        body["previous_record_sha256"] = existing[-1]["record_sha256"] if existing else None
        body["record_sha256"] = integrity.sha256_record(
            {k: v for k, v in body.items() if k != "record_sha256"}
        )
        _validate_shape(body, len(existing) + 1)
        with open(path, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(dict(body), ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

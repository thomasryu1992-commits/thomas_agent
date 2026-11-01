"""H6b: the cash-flow ledger — shadow only. Records what moved money; changes no verdict.

``docs/proposals/PORTFOLIO_CASH_FLOW_LEDGER_V0.1.md`` (Thomas 2026-10-08, D-H6-1–10). Each holdings fire
reads the venues' cash-flow histories (H6a) and appends what it has not seen to
``holdings_cash_flows.jsonl``, a :mod:`chained_log`. Nothing here issues or redeems a unit, computes a
NAV per unit or touches the drawdown: that is H6c, after the shadow period (D-H6-9). Every line carries
``accounting_mode = shadow``.

**Exactly once (D-H6-5).** An event's identity is ``source_event_key`` (``<source>:<the venue's id>``).
Every append goes through :func:`chained_log.append_unique`, which re-verifies the chain and drops a key
the ledger already holds under the ledger lock, so a fire that crashed after its append and runs again
writes nothing twice (H6d-min). Each source is re-read from :data:`OVERLAP_MS` before its cursor, which
catches a flow the venue confirms late. A row the venue still calls pending is not written; the cursor
stays behind it. A recorded success that later reads as failed is answered by a ``flow_reversed`` line.

**One movement, two records.** ``economic_event_id`` is the key unless another source recorded the same
asset and amount within :data:`CROSS_SOURCE_WINDOW_MS`; then both are ``possible_duplicate`` and HELD —
never merged by guess.

**Three statuses (D-H6-6).** ``source_confidence`` (CONFIRMED from a venue record; UNRESOLVED for a Toss
residual), ``valuation_status`` (UNVALUED throughout H6b: nothing is priced yet) and
``accounting_status`` (READY, or HELD with a reason). Fiat card/bank payments are HELD: whether one was
paid from outside the declared scope is not yet known (D-H6-7's caution, applied).

**Schema is earned, not assumed.** Each source starts ``UNVERIFIED_NO_ROWS``; the first row that
normalizes makes it ``VERIFIED``; a row that does not is a ``malformed_source_event`` (HELD) and the
source becomes ``MISMATCH``.

**Toss is SHADOW_ONLY (D-H6-7).** Each fire writes one ``toss_window_observed`` line: which closed orders
filled in the window (as hashes), which settled, and whether each pocket's cash change was explained.
The Toss semantics are recomputed from those lines per semantics epoch (H6d-min), never from a counter
that can only grow. A residual beyond one minor unit is ``toss_residual`` (UNRESOLVED); a KRW/USD pair
that offsets at the read's rate is ``internal_exchange``. Late evidence is a ``toss_window_amended`` line
that can only withdraw a window from the evidence, never add to it.

**Exceptions (H6d-min, Thomas 2026-10-09).** An event after the cutover that nothing explains blocks the
readiness until Thomas closes it from his terminal with a ``flow_resolved`` line. Closing a review is
not evidence and not accounting eligibility: in H6b nothing is eligible (nothing is valued).

**The ledger is the authority; the state file is a cache.** Cutover, resolutions, epochs, Toss windows,
the verified shadow dates and the first verified fire are read back from the ledger. The state file
keeps cursors, source health, the Toss cash point and what was told. Writers take the state lock, then
the ledger lock (never the reverse), re-read both and patch only what they own.

**Local only.** Lines carry assets and amounts (as strings: the chain refuses floats) and order hashes.
No door reads the ledger or the state file; the stored snapshot gets counts (:func:`summary`).
"""

from __future__ import annotations

import ast
import hashlib
import inspect
import json
import os
import re
import textwrap
import time
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from .. import timeutil
from ..errors import MvpRuntimeError, ToolError
from ..filelock import locked
from . import chained_log

FILENAME = "holdings_cash_flows.jsonl"
STATE_FILENAME = "holdings_cash_flow_state.json"
RECORD_TYPE = "holdings_cash_flow_event.v1"
LEDGER_TAMPERED = "HOLDINGS_CASH_FLOW_LEDGER_TAMPERED"
LEDGER_LOCKED = "HOLDINGS_CASH_FLOW_LEDGER_LOCKED"
STATE_LOCKED = "HOLDINGS_CASH_FLOW_STATE_LOCKED"
EVENT_INVALID = "HOLDINGS_CASH_FLOW_EVENT_INVALID"
MALFORMED_SOURCE_EVENT = "MALFORMED_SOURCE_EVENT"

ACCOUNTING_MODE = "shadow"
ACCOUNTING_METHOD = "hourly_approximation_v1"
START_LOOKBACK_MS = 7 * 86_400_000   # the first read reaches back 7 days (Thomas 2026-10-08)
OVERLAP_MS = 86_400_000              # every later read re-reads the last day
CROSS_SOURCE_WINDOW_MS = 10 * 60_000
READ_BUDGET_SECONDS = 20.0           # the shadow histories share the maintenance pass; the rest wait an hour
# H6b-hardening (Thomas 2026-10-09): the transfer histories gate this fire's coherence, so they are read
# first and outside the shadow budget; the shadow histories then get READ_BUDGET_SECONDS.
CRITICAL_SOURCES = ("transfer_spot_to_futures", "transfer_futures_to_spot")
PRE_CUTOVER, POST_CUTOVER, UNKNOWN_PHASE = "PRE_CUTOVER", "POST_CUTOVER", "UNKNOWN"
OBSERVED_ONLY, ELIGIBLE = "OBSERVED_ONLY", "ELIGIBLE"
SHADOW_MIN_DAYS = 7
SETTLEMENT_MIN_WINDOWS = 12
# H6b readiness-hardening (Thomas 2026-10-09): what readiness opens is the unit shadow, not H6c.
READY_NAME = "H6b_shadow_ready"

CONFIRMED, RECONCILED, UNRESOLVED = "CONFIRMED", "RECONCILED", "UNRESOLVED"
READY, HELD = "READY", "HELD"
UNVALUED = "UNVALUED"
SCHEMA_NO_ROWS, SCHEMA_VERIFIED, SCHEMA_MISMATCH = "UNVERIFIED_NO_ROWS", "VERIFIED", "MISMATCH"
PASS, FAIL, WAITING, RUNNING, UNCHECKED = "PASS", "FAIL", "WAITING", "RUNNING", "UNCHECKED"
# The readiness checks and their values, closed: the Claude door's `holdings_status` carries exactly these
# (HOLDINGS_STATUS_READ_AND_AUTO_FLOWS R1). A check added to :func:`readiness` joins here or the door's test fails.
READINESS_STATUSES = (PASS, FAIL, WAITING, RUNNING, UNCHECKED)
READINESS_CHECKS = ("binance_access", "binance_real_schema", "cross_source_dedupe", "current_health",
                    "cutover_boundary", "history_completeness", "internal_transfer_guard", "ledger_chain",
                    "ledger_state_consistency", "post_cutover_exceptions", "shadow_observation",
                    "toss_cash_semantics")

EVENT_FLOW = "flow_recorded"
EVENT_INTERNAL = "internal_transfer"
EVENT_REVERSED = "flow_reversed"
EVENT_MALFORMED = "malformed_source_event"
EVENT_TOSS_RESIDUAL = "toss_residual"
EVENT_TOSS_EXCHANGE = "internal_exchange"
EVENT_DUPLICATE_LINK = "duplicate_linked"
EVENT_CUTOVER_SET = "cutover_set"
EVENT_CUTOVER_MIGRATED = "cutover_migrated"
# H6d-min (Thomas 2026-10-09)
EVENT_RESOLVED = "flow_resolved"
EVENT_TOSS_WINDOW = "toss_window_observed"
EVENT_TOSS_AMENDED = "toss_window_amended"
EVENT_EPOCH = "toss_semantics_epoch_started"
EVENT_SHADOW_DATE = "shadow_date_verified"
EVENT_FIRST_FIRE = "first_verified_fire"
EVENT_LEGACY = "legacy_state_imported"
# C7 PR-B (Thomas 2026-10-17: D2 = option C). A stretch no collector read gets a line saying so, with the
# reason, so a gap with a line is explained and a gap without one is not (HOLDINGS_LEDGER_CHECKPOINT §4 (a)).
EVENT_TOSS_SKIPPED = "toss_window_skipped"
EVENT_FLOW_SKIPPED = "flow_window_skipped"
SKIP_SNAPSHOT_UNUSABLE = "snapshot_unusable"
SKIP_CASH_POINT_BEHIND = "cash_point_behind_ledger"
SKIP_BEYOND_DEPTH = "beyond_query_depth"
# C7 PR-C (D3, Thomas 2026-10-11): the operator's adoption of a ledger the checkpoint refuses — the one line
# a refused ledger may still take, written only by ``adopt_ledger`` at Thomas's terminal (C7 §7).
EVENT_LEDGER_ADOPTED = "ledger_adopted"
ADOPT_REFUSED = "HOLDINGS_CASH_FLOW_ADOPT_REFUSED"
NO_CUTOVER = "NO_CUTOVER"
CUTOVER_REFUSED = "HOLDINGS_CASH_FLOW_CUTOVER_REFUSED"
CUTOVER_EVENTS = (EVENT_CUTOVER_SET, EVENT_CUTOVER_MIGRATED)

# --- resolution (H6d-min) ------------------------------------------------------------------------------
RESOLVE_REFUSED = "HOLDINGS_CASH_FLOW_RESOLVE_REFUSED"          # the operator conditions
RESOLVE_NO_TARGET = "HOLDINGS_CASH_FLOW_RESOLVE_NO_TARGET"
RESOLVE_INVALID_TARGET = "HOLDINGS_CASH_FLOW_RESOLVE_INVALID_TARGET"
RESOLVE_DUPLICATE = "HOLDINGS_CASH_FLOW_RESOLVE_DUPLICATE"
RESOLVE_NOT_OPEN = "HOLDINGS_CASH_FLOW_RESOLVE_NOT_OPEN"
RESOLVE_KIND = "HOLDINGS_CASH_FLOW_RESOLVE_KIND_NOT_ALLOWED"
EPOCH_REFUSED = "HOLDINGS_CASH_FLOW_EPOCH_REFUSED"

CATEGORY_PAYMENT, CATEGORY_DUPLICATE, CATEGORY_REVERSED = "unverified_payment", "possible_duplicate", "reversed"
CATEGORY_MALFORMED, CATEGORY_HELD, CATEGORY_TOSS = "malformed", "held_other", "toss_residual"
EXCLUDE = "acknowledged_excluded"
# What each exception may be closed as. Duplicates, reversals, malformed rows and other holds only as
# excluded: an operator's look does not make them evidence. A payment's kind must match the direction
# the venue gave it. A Toss residual's external kinds exclude its window from the semantics; a
# semantics mismatch keeps it there (MIXED until a new epoch).
ALLOWED_KINDS: dict[str, tuple[str, ...]] = {
    CATEGORY_PAYMENT: ("deposit", "withdrawal"),
    CATEGORY_DUPLICATE: (EXCLUDE,), CATEGORY_REVERSED: (EXCLUDE,), CATEGORY_MALFORMED: (EXCLUDE,),
    CATEGORY_HELD: (EXCLUDE,),
    CATEGORY_TOSS: ("deposit", "withdrawal", "income", "fee", "semantics_mismatch", "read_incomplete"),
}
ALL_KINDS = frozenset(kind for kinds in ALLOWED_KINDS.values() for kind in kinds)
TOSS_EXTERNAL_KINDS = frozenset({"deposit", "withdrawal", "income", "fee", "read_incomplete"})
EXCEPTION_EVENTS = (EVENT_FLOW, EVENT_INTERNAL, EVENT_MALFORMED, EVENT_TOSS_RESIDUAL)

# D5 (Thomas 2026-10-09): the event types H6d-min adds carry these fields, or the line is refused before
# it reaches the chain. Older lines are read as written.
REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    EVENT_RESOLVED: ("resolves", "kind", "reason", "requested_by", "timestamp", "target_event", "category",
                     "review_status", "evidence_status", "accounting_eligibility"),
    EVENT_TOSS_WINDOW: ("window_start", "window_end", "epoch", "reconciliation_fingerprint", "buy_orders",
                        "sell_orders", "settlements", "residual_status", "orders_truncated", "exchange_pair",
                        "order_hash_basis", "identity_stable", "orders_query"),
    EVENT_TOSS_AMENDED: ("amends", "kind", "window_start", "window_end"),
    EVENT_EPOCH: ("epoch", "reason", "requested_by", "change_ref", "reconciliation_fingerprint",
                  "reconciliation_digest", "legacy_transition", "previous_epoch_status", "timestamp"),
    EVENT_SHADOW_DATE: ("kst_date", "cutover_at", "cutover_generation"),
    EVENT_FIRST_FIRE: ("fire_at", "cutover_at", "cutover_generation"),
    EVENT_LEGACY: ("toss_evidence", "shadow_success_dates", "first_verified_fire_at", "cutover_at",
                   "cutover_generation", "provenance"),
    EVENT_TOSS_SKIPPED: ("window_start", "window_end", "reason"),
    EVENT_FLOW_SKIPPED: ("flow_source", "window_start", "window_end", "reason"),
    EVENT_LEDGER_ADOPTED: ("adopted", "replaced_checkpoint", "detector", "told_keys_dropped", "requested_by",
                           "reason", "timestamp"),
}
ALLOWED_VALUES: dict[str, dict[str, frozenset]] = {
    EVENT_RESOLVED: {"kind": ALL_KINDS, "review_status": frozenset({"CLOSED"}),
                     "evidence_status": frozenset({"NOT_VALIDATED"}),
                     "accounting_eligibility": frozenset({"NOT_ELIGIBLE"})},
    EVENT_TOSS_AMENDED: {"kind": frozenset({"late_fill", "settlement_revised", "identity_unstable"})},
    EVENT_TOSS_SKIPPED: {"reason": frozenset({SKIP_SNAPSHOT_UNUSABLE, SKIP_CASH_POINT_BEHIND})},
    EVENT_FLOW_SKIPPED: {"reason": frozenset({SKIP_BEYOND_DEPTH})},
    EVENT_LEDGER_ADOPTED: {"detector": frozenset({"checkpoint_ledger_shorter", "checkpoint_ledger_replaced",
                                                  "checkpoint_malformed", "told_exception_missing",
                                                  "detection_marker"})},
}


def ledger_path(state_dir: Path) -> Path:
    return state_dir / FILENAME


def state_path(state_dir: Path) -> Path:
    return state_dir / STATE_FILENAME


def verify(state_dir: Path) -> list[dict[str, Any]]:
    return chained_log.verify(ledger_path(state_dir), record_type=RECORD_TYPE, tamper_code=LEDGER_TAMPERED)


def load_state(state_dir: Path) -> dict[str, Any]:
    """The cache. Missing is a fresh start (the ledger rebuilds what matters); unreadable is a refusal."""
    target = state_path(state_dir)
    if not target.exists():
        return {"sources": {}, "toss": None}
    try:
        body = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ToolError(LEDGER_TAMPERED, f"{STATE_FILENAME} does not parse") from None
    if not isinstance(body, dict):
        raise ToolError(LEDGER_TAMPERED, f"{STATE_FILENAME} is not an object")
    return body


def _save_state(state_dir: Path, body: Mapping[str, Any]) -> None:
    target = state_path(state_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(body, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    tmp.replace(target)


@contextmanager
def _state_lock(state_dir: Path) -> Iterator[None]:
    """The first lock of every write (H6d-min). The ledger lock is taken inside it, never around it."""
    state_dir.mkdir(parents=True, exist_ok=True)
    with locked(state_path(state_dir).with_suffix(".lock"), code=STATE_LOCKED, label="holdings cash-flow state"):
        yield


# --- normalization: one venue row -> one event, or None while it is pending -------------------------

def _num(value: Any) -> str:
    """A decimal kept as the venue's own string; refuses anything that is not a finite number."""
    text = str(value).strip()
    float(text)
    if text.lower() in ("nan", "inf", "-inf", "infinity", "-infinity"):
        raise ValueError(text)
    return text


def _ms(value: Any) -> int:
    if isinstance(value, (int, float)) and value > 0:
        return int(value)
    text = str(value).strip()
    if text.isdigit():
        return int(text)
    # Binance's withdraw history writes "YYYY-MM-DD HH:MM:SS" in UTC.
    return int(timeutil.parse_iso(text.replace(" ", "T") + ("" if text.endswith("Z") else "Z")).timestamp() * 1000)


def _ms_iso(ms_value: int) -> str:
    return timeutil.format_iso(datetime.fromtimestamp(ms_value / 1000, tz=timezone.utc))


def _iso_ms(at: Any) -> int | None:
    try:
        return int(timeutil.parse_iso(str(at)).timestamp() * 1000)
    except (ValueError, TypeError):
        return None


def _event(direction: str, asset: Any, amount: Any, at_ms: int, *, fee: Any = None) -> dict[str, Any]:
    return {"direction": direction, "asset": str(asset).strip(), "amount": _num(amount),
            "fee": None if fee in (None, "") else _num(fee), "event_ms": at_ms}


# Each: row -> (venue id, event or None if not terminal, failed: bool). A KeyError/ValueError/TypeError
# is a schema mismatch. Status vocabularies are Binance's documented ones.
def _crypto_deposit(row: Mapping[str, Any]):
    status = int(row["status"])            # 0 pending, 6 credited, 1 success, 7 wrong, 8 waiting
    event = _event("in", row["coin"], row["amount"], _ms(row["insertTime"])) if status in (1, 6) else None
    return str(row["id"]), event, status == 7


def _crypto_withdraw(row: Mapping[str, Any]):
    status = int(row["status"])            # 6 completed; 1 cancelled, 3 rejected, 5 failure
    event = (_event("out", row["coin"], row["amount"], _ms(row.get("completeTime") or row["applyTime"]),
                    fee=row.get("transactionFee")) if status == 6 else None)
    return str(row["id"]), event, status in (1, 3, 5)


def _fiat_order(direction: str):
    def normalize(row: Mapping[str, Any]):
        status = str(row["status"])
        event = (_event(direction, row["fiatCurrency"], row["indicatedAmount"] if "indicatedAmount" in row
                        else row["amount"], _ms(row.get("updateTime") or row["createTime"]), fee=row.get("totalFee"))
                 if status == "Successful" else None)
        return str(row["orderNo"]), event, status in ("Failed", "Expired")
    return normalize


def _fiat_payment(direction: str):
    def normalize(row: Mapping[str, Any]):
        status = str(row["status"])
        asset, amount = ((row["cryptoCurrency"], row["obtainAmount"]) if direction == "in"
                         else (row["cryptoCurrency"], row["sourceAmount"]))
        event = _event(direction, asset, amount, _ms(row.get("updateTime") or row["createTime"]),
                       fee=row.get("totalFee")) if status == "Completed" else None
        return str(row["orderNo"]), event, status in ("Failed", "Refunded")
    return normalize


def _pay(row: Mapping[str, Any]):
    amount = _num(row["amount"])
    direction = "out" if amount.startswith("-") else "in"
    event = _event(direction, row["currency"], amount.lstrip("-+"), _ms(row["transactionTime"]))
    return str(row["transactionId"]), event, False


def _transfer(row: Mapping[str, Any]):
    status = str(row["status"])
    event = (_event("internal", row["asset"], row["amount"], _ms(row["timestamp"])) if status == "CONFIRMED"
             else None)
    return str(row["tranId"]), event, status == "FAILED"


NORMALIZERS: dict[str, Callable[[Mapping[str, Any]], tuple[str, dict[str, Any] | None, bool]]] = {
    "crypto_deposit": _crypto_deposit,
    "crypto_withdraw": _crypto_withdraw,
    "fiat_deposit": _fiat_order("in"),
    "fiat_withdraw": _fiat_order("out"),
    "fiat_buy": _fiat_payment("in"),
    "fiat_sell": _fiat_payment("out"),
    "pay": _pay,
    "transfer_spot_to_futures": _transfer,
    "transfer_futures_to_spot": _transfer,
}
# Payments may be paid from a fiat balance outside the declared scope: HELD until that is known.
# Pay's sign convention is unverified on a real row: HELD until the first one is read.
HELD_BY_SOURCE = {"fiat_buy": "fiat_payment_scope_unverified", "fiat_sell": "fiat_payment_scope_unverified",
                  "pay": "pay_direction_unverified"}


# --- the ledger: validated, unique appends inside the state lock ------------------------------------------

def _validate(row: Mapping[str, Any]) -> None:
    """D5: an H6d-min line carries its required fields with allowed values, or nothing is written."""
    event = row.get("event")
    missing = [name for name in REQUIRED_FIELDS.get(event, ()) if name not in row or row[name] is None]
    if missing:
        raise ToolError(EVENT_INVALID, f"a {event} line lacks {', '.join(missing)}")
    for name, allowed in ALLOWED_VALUES.get(event, {}).items():
        if row.get(name) not in allowed:
            raise ToolError(EVENT_INVALID, f"a {event} line has {name}={row.get(name)!r}")
    if event == EVENT_TOSS_WINDOW and not set(row["residual_status"].values()) <= {"OPEN", "CLEAR"}:
        raise ToolError(EVENT_INVALID, "a toss window's residual status is OPEN or CLEAR")
    if event == EVENT_TOSS_RESIDUAL and "quiet_window_declined" in row \
            and row["quiet_window_declined"] not in QUIET_WINDOW_DECLINES:
        raise ToolError(EVENT_INVALID, f"a {event} line has quiet_window_declined={row['quiet_window_declined']!r}")


def _stamp(rows: list[Mapping[str, Any]], now: str) -> list[dict[str, Any]]:
    stamped = [{"at": now, "accounting_mode": ACCOUNTING_MODE, "accounting_method": ACCOUNTING_METHOD, **row}
               for row in rows]
    for row in stamped:
        _validate(row)
    return stamped


def _append_unique(state_dir: Path, build: Callable[[list[dict[str, Any]]], list[Mapping[str, Any]]], *,
                   now: str) -> list[dict[str, Any]]:
    """The ledger lock's one door. Callers hold the state lock already (state -> ledger)."""
    return chained_log.append_unique(ledger_path(state_dir), record_type=RECORD_TYPE, tamper_code=LEDGER_TAMPERED,
                                     lock_code=LEDGER_LOCKED, label="holdings cash-flow ledger",
                                     build=lambda existing: _stamp(list(build(existing)), now))


def _append(state_dir: Path, rows: list[dict[str, Any]], *, now: str) -> list[dict[str, Any]]:
    if not rows:
        return []
    return _append_unique(state_dir, lambda _existing: rows, now=now)


# --- C7 PR-A: the checkpoint — the ledger the state last saw, detected, never enforced ---------------------
# HOLDINGS_LEDGER_CHECKPOINT_V0.1.md §3 (D1, Thomas 2026-10-11: PR-A detects only; refusing is PR-C's). The
# state keeps the ledger's length and last hash as of its last save. Because the ledger is a chain, line N's
# hash proves lines 1…N, so a ledger that still holds that line at that position is the same history, grown;
# one that is shorter or holds another line there was put back from elsewhere (C7-4, C7-6). The writes go on
# while it stands (detect-only): the state records a marker, the checkpoint stays frozen so PR-D's alert key
# does not change every fire, and readiness reads FAIL. No checkpoint at all (every state saved before this
# code, or a state file that was lost) is not judged here: the history check is PR-B's (readiness note R-3,
# option (가); D2: absent reads UNCHECKED there). PR-A writes no ledger line of its own.
#
# R-2 and R-3 as Thomas decided them (2026-10-17):
# - R-2: a detection carries its own code, LEDGER_MISMATCH_DETECTED, never LEDGER_TAMPERED. That code means
#   a write was refused and nothing written; here the writes go on.
# - R-3 (가-수정): every checkpoint records its ``basis``. PR-A only ever writes ``observed``: the checkpoint
#   began where this code first saw the ledger (``observed_at``), so continuity since then is checked and the
#   history before it is not. ``observed`` survives every move, so a checkpoint first recorded over a
#   restored or lost state never turns into a verified one by being moved. ``absent`` and ``observed`` are
#   both UNVERIFIED history; only a later step that verifies the history (PR-B, an operator adoption) may
#   write another basis.
#
# C7 PR-C (D1, D3; Thomas 2026-10-17: refuse on a mismatch, adopt in two steps) turns detection into refusal.
# A write that finds the ledger not continuing the checkpoint — or a PR-A marker still standing — raises
# LEDGER_TAMPERED and writes nothing, to the ledger or the state, so nothing is laid on a mixed history. No
# marker is written any more: ``blocked_state`` judges the refusal itself, and PR-D's alert tells it. Only
# ``adopt_ledger`` (Thomas's terminal, two steps, C7 §7) writes past it, and an adoption restarts the
# checkpoint's ``observed_at`` at the adoption: the adopted history is not the one first observed, so
# ``history_completeness`` reads UNCHECKED until the cutover is moved past the adoption (D3: adoption alone
# never passes the history).
CHECKPOINT_KEY = "ledger_checkpoint"
LEDGER_MISMATCH_DETECTED = "HOLDINGS_CASH_FLOW_LEDGER_MISMATCH_DETECTED"
BASIS_OBSERVED = "observed"
HISTORY_UNVERIFIED = "UNVERIFIED"
CHECKPOINT_SHORTER = "checkpoint_ledger_shorter"
CHECKPOINT_REPLACED = "checkpoint_ledger_replaced"
CHECKPOINT_MALFORMED = "checkpoint_malformed"
TOLD_KEY_MISSING = "told_exception_missing"


def _checkpoint_problem(rows: list[Mapping[str, Any]], state: Mapping[str, Any]) -> str | None:
    """Why ``rows`` do not continue the state's checkpoint, or None (also when there is no checkpoint)."""
    checkpoint = state.get(CHECKPOINT_KEY)
    if checkpoint is None:
        return None
    lines = checkpoint.get("lines") if isinstance(checkpoint, Mapping) else None
    digest = checkpoint.get("sha256") if isinstance(checkpoint, Mapping) else None
    if not isinstance(lines, int) or isinstance(lines, bool) or lines < 1 or not isinstance(digest, str) or not digest:
        return CHECKPOINT_MALFORMED
    if len(rows) < lines:
        return CHECKPOINT_SHORTER
    if rows[lines - 1].get("sha256") != digest:
        return CHECKPOINT_REPLACED
    return None


def _told_key_problem(rows: list[Mapping[str, Any]], state: Mapping[str, Any]) -> str | None:
    """A told exception whose line the ledger no longer holds is a proven loss (C7 §3, second detector).

    Compared against every exception line, resolved or explained since, not only the open ones: a told
    exception that was closed later is still in the ledger and is no loss."""
    told = (state.get("exceptions") or {}).get("told_keys") if isinstance(state.get("exceptions"), Mapping) else None
    if not told:
        return None
    present = {_key_digest(str(row.get("source_event_key"))) for row in rows if row.get("event") in EXCEPTION_EVENTS}
    return TOLD_KEY_MISSING if any(key not in present for key in told) else None


def checkpoint_basis(state: Mapping[str, Any]) -> str:
    """``absent`` or ``observed``. PR-A writes no other basis, so whatever else a state file says — no
    basis, or one PR-A never wrote — reads ``observed``: the state cannot vouch for its own history."""
    return "absent" if not isinstance(state.get(CHECKPOINT_KEY), Mapping) else BASIS_OBSERVED


def ledger_problem(rows: list[Mapping[str, Any]], state: Mapping[str, Any]) -> str | None:
    """The first detector that fires for this ledger and state, or None."""
    return _checkpoint_problem(rows, state) or _told_key_problem(rows, state)


DETECTORS = frozenset({CHECKPOINT_SHORTER, CHECKPOINT_REPLACED, CHECKPOINT_MALFORMED, TOLD_KEY_MISSING})
MARKER_DETECTOR = "detection_marker"


def standing_problem(rows: list[Mapping[str, Any]], state: Mapping[str, Any]) -> str | None:
    """Why every ordinary write refuses (PR-C), or None: a PR-A marker that still stands — it stands until an
    adoption, whatever the ledger reads now — else the first detector that fires."""
    marker = state.get(DETECTION_MARKER_KEY)
    if marker is not None:
        reason = marker.get("reason") if isinstance(marker, Mapping) else None
        return reason if reason in DETECTORS else MARKER_DETECTOR
    return ledger_problem(rows, state)


def _frozen_checkpoint(state: Mapping[str, Any]) -> dict[str, Any]:
    """The checkpoint a refusal stands on: the state's, else a PR-A marker's, else none (``None`` values)."""
    marker = state.get(DETECTION_MARKER_KEY)
    for candidate in (state.get(CHECKPOINT_KEY), marker.get("checkpoint") if isinstance(marker, Mapping) else None):
        if isinstance(candidate, Mapping) and (candidate.get("lines") is not None or candidate.get("sha256")):
            return {"lines": candidate.get("lines"), "sha256": candidate.get("sha256")}
    return {"lines": None, "sha256": None}


def _unsaved_adoption(rows: list[Mapping[str, Any]], state: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """C7 §7.6: the adoption line the state never caught up with, or None.

    An adoption appends its line and then saves the state. A stop in between leaves a state that still holds
    the checkpoint the adoption replaced. When the last adoption line names exactly that checkpoint, the
    ledger continues the adoption (the chain carries it) and the next write finishes it."""
    adoption = next((row for row in reversed(rows) if row.get("event") == EVENT_LEDGER_ADOPTED), None)
    if adoption is None:
        return None
    replaced = adoption.get("replaced_checkpoint")
    if not isinstance(replaced, Mapping):
        return None
    if {"lines": replaced.get("lines"), "sha256": replaced.get("sha256")} != _frozen_checkpoint(state):
        return None
    return adoption


def _told_keys_present(rows: list[Mapping[str, Any]], state: Mapping[str, Any]) -> tuple[list[str], int]:
    """The told exception keys the ledger still holds, and how many it does not."""
    exceptions = state.get("exceptions") if isinstance(state.get("exceptions"), Mapping) else {}
    told = list(exceptions.get("told_keys") or [])
    present = {_key_digest(str(row.get("source_event_key"))) for row in rows if row.get("event") in EXCEPTION_EVENTS}
    kept = [key for key in told if key in present]
    return kept, len(told) - len(kept)


def _apply_adoption(state: dict[str, Any], after: list[Mapping[str, Any]], adoption: Mapping[str, Any], *,
                    now: str) -> None:
    """The state an adoption leaves: no marker, the lost told keys dropped, and a checkpoint at the new end
    whose ``observed_at`` is the adoption (the adopted history is not the one first observed)."""
    state.pop(DETECTION_MARKER_KEY, None)
    kept, _dropped = _told_keys_present(after, state)
    if isinstance(state.get("exceptions"), dict) and "told_keys" in state["exceptions"]:
        state["exceptions"]["told_keys"] = kept
    state[CHECKPOINT_KEY] = {"lines": len(after), "sha256": after[-1]["sha256"], "at": now,
                             "basis": BASIS_OBSERVED, "observed_at": adoption.get("at") or now}


def _apply_checkpoint(state: dict[str, Any], after: list[Mapping[str, Any]], *, now: str) -> None:
    """After a write that the checkpoint allowed: move it to the new end. The basis and the first sighting
    carry over unchanged: moving a checkpoint verifies the lines added since, never the history before
    ``observed_at``."""
    if not after:
        return
    previous = state.get(CHECKPOINT_KEY)
    observed_at = previous.get("observed_at") if isinstance(previous, Mapping) else None
    state[CHECKPOINT_KEY] = {"lines": len(after), "sha256": after[-1]["sha256"], "at": now,
                             "basis": BASIS_OBSERVED, "observed_at": observed_at or now}


def _commit(state_dir: Path, *, now: str,
            rows: Callable[[list[dict[str, Any]], dict[str, Any]], list[Mapping[str, Any]]],
            patch: Callable[[dict[str, Any], list[dict[str, Any]]], None] | None = None) -> list[dict[str, Any]]:
    """One write: state lock, fresh state, ledger lock (re-verified, unique), then the state patch.

    ``rows(existing, state)`` builds the lines from the verified ledger and the fresh state;
    ``patch(state, ledger_after)`` changes only the fields this writer owns. A crash between the append
    and the save leaves the facts in the ledger and the cache behind; the next run re-derives them.

    C7 PR-C: a ledger that does not continue the state's checkpoint, or a standing PR-A marker, refuses the
    write with LEDGER_TAMPERED under both locks, before anything is built — nothing reaches the ledger or
    the state. The one exception is an adoption whose state save was lost (§7.6): the write finishes it."""
    with _state_lock(state_dir):
        state = load_state(state_dir)
        after: list[dict[str, Any]] = []
        resumed: list[Mapping[str, Any]] = []

        def build(existing: list[dict[str, Any]]) -> list[Mapping[str, Any]]:
            # Under both locks (C7 §3).
            problem = standing_problem(existing, state)
            if problem is not None:
                adoption = _unsaved_adoption(existing, state)
                if adoption is None:
                    raise ToolError(LEDGER_TAMPERED, f"the ledger does not continue the state's checkpoint "
                                                     f"({problem}); nothing written. Only an adoption at "
                                                     "Thomas's terminal (holdings_board --adopt-ledger) reopens it")
                resumed.append(adoption)
            out = [*_legacy_import_rows(existing, state), *rows(existing, state)]
            after.extend(existing)
            return out

        written = _append_unique(state_dir, build, now=now)
        after.extend(written)
        if any(row.get("event") == EVENT_LEGACY for row in written):
            for name in ("toss_evidence", "shadow_success_dates", "first_verified_fire_at"):
                state.pop(name, None)
        state["cutover_at"] = cutover_at(after)
        state["cutover_ms"] = _iso_ms(state["cutover_at"]) if state["cutover_at"] else None
        if resumed:
            _apply_adoption(state, after, resumed[0], now=now)
        else:
            _apply_checkpoint(state, after, now=now)
        if patch is not None:
            patch(state, after)
        _save_state(state_dir, state)
    return written


# --- C7 PR-C: the operator's adoption (C7 §7, D3) -----------------------------------------------------------
# Two steps at Thomas's terminal: the first reads and shows what would be adopted — the ledger's length and
# the first 8 characters of its last hash, the checkpoint it would replace, the detector — and writes
# nothing; the second takes those 8 characters back as ``confirm`` and, under both locks, re-verifies the
# chain and refuses unless the ledger still ends there. The adoption line is append-only and erases nothing;
# its key is ``ledger_adopted:<end hash>``, so a second run on the same end adds no line. Claude never runs
# either step (D3). Nothing here names an amount, an asset or a venue event.
_CONFIRM_LEN = 8


def _short(digest: Any) -> str | None:
    """The first 8 hex characters of a line hash (``sha256:<hex>``), the form step two takes back."""
    if not isinstance(digest, str) or not digest:
        return None
    return digest.removeprefix("sha256:")[:_CONFIRM_LEN]


def _adoption_conditions(*, interactive: bool, requested_by: str, reason: str) -> None:
    if not interactive:
        raise ToolError(ADOPT_REFUSED, "an adoption runs at an interactive terminal")
    if not (requested_by or "").strip() or not (reason or "").strip():
        raise ToolError(ADOPT_REFUSED, "an adoption needs --by and --reason")


def _adoptable(rows: list[Mapping[str, Any]], state: Mapping[str, Any]) -> str:
    """The detector an adoption would answer, or ADOPT_REFUSED with the reason it cannot."""
    if legacy_import_refusal(rows, state):
        raise ToolError(ADOPT_REFUSED, "a pre-H6d import is pending; an adoption does not answer it")
    if not rows:
        raise ToolError(ADOPT_REFUSED, "the ledger is empty; there is nothing to adopt")
    problem = standing_problem(rows, state)
    if problem is None:
        raise ToolError(ADOPT_REFUSED, "the ledger continues the state's checkpoint; there is nothing to adopt")
    return problem


def adoption_preview(state_dir: Path, *, interactive: bool, requested_by: str, reason: str) -> dict[str, Any]:
    """Step one: what an adoption would write, and the 8 characters step two needs. Writes nothing.

    A chain that does not verify is not adoptable: ``verify`` raises LEDGER_TAMPERED here, as for any
    reader."""
    _adoption_conditions(interactive=interactive, requested_by=requested_by, reason=reason)
    state = load_state(state_dir)
    rows = verify(state_dir)
    problem = _adoptable(rows, state)
    frozen = _frozen_checkpoint(state)
    _kept, dropped = _told_keys_present(rows, state)
    return {"ledger_lines": len(rows), "confirm": _short(rows[-1]["sha256"]), "detector": problem,
            "replaced_checkpoint": {"lines": frozen["lines"], "sha256": _short(frozen["sha256"])},
            "told_keys_dropped": dropped}


def adopt_ledger(state_dir: Path, *, confirm: str, requested_by: str, reason: str, interactive: bool,
                 now: str | None = None) -> dict[str, Any]:
    """Step two: adopt the ledger as it ends now, if it still ends where step one showed (``confirm``).

    Under the state lock and then the ledger lock (the chain re-verified by ``_append_unique``): refuses
    unless the end's first 8 characters equal ``confirm``. Writes one ``ledger_adopted`` line and then the
    state: no marker, the told keys the ledger lost dropped (counted on the line), and the checkpoint at the
    new end with ``observed_at`` at the adoption. ``history_completeness`` is not raised by this (D3). Run
    again on the same end — the adoption line is now the end and names it — it writes no line and finishes
    the state, which also covers a stop between the line and the save (§7.6)."""
    _adoption_conditions(interactive=interactive, requested_by=requested_by, reason=reason)
    typed = (confirm or "").strip().lower()
    if len(typed) != _CONFIRM_LEN or any(c not in "0123456789abcdef" for c in typed):
        raise ToolError(ADOPT_REFUSED, f"the confirmation is the {_CONFIRM_LEN} characters step one printed")
    now = now or timeutil.utc_now_iso()
    with _state_lock(state_dir):
        state = load_state(state_dir)
        before: list[dict[str, Any]] = []
        earlier: list[Mapping[str, Any]] = []

        def build(existing: list[dict[str, Any]]) -> list[Mapping[str, Any]]:
            before.extend(existing)
            last = existing[-1] if existing else None
            adopted = last.get("adopted") if last is not None and last.get("event") == EVENT_LEDGER_ADOPTED else None
            if isinstance(adopted, Mapping) and _short(adopted.get("sha256")) == typed:
                earlier.append(last)                       # the same end, adopted before: no second line
                return []
            problem = _adoptable(existing, state)
            if _short(last["sha256"]) != typed:
                raise ToolError(ADOPT_REFUSED, "the ledger no longer ends where step one showed, or the "
                                               "confirmation is wrong; nothing written")
            _kept, dropped = _told_keys_present(existing, state)
            return [{"event": EVENT_LEDGER_ADOPTED, "source": "operator",
                     "source_event_key": f"{EVENT_LEDGER_ADOPTED}:{last['sha256']}",
                     "adopted": {"lines": len(existing), "sha256": last["sha256"]},
                     "replaced_checkpoint": _frozen_checkpoint(state), "detector": problem,
                     "told_keys_dropped": dropped, "requested_by": requested_by.strip(),
                     "reason": reason.strip(), "timestamp": now}]

        written = _append_unique(state_dir, build, now=now)
        after = [*before, *written]
        adoption = written[0] if written else earlier[0]
        state["cutover_at"] = cutover_at(after)
        state["cutover_ms"] = _iso_ms(state["cutover_at"]) if state["cutover_at"] else None
        _apply_adoption(state, after, adoption, now=now)
        _save_state(state_dir, state)
    return {"written": bool(written), "adopted_lines": adoption["adopted"]["lines"],
            "adopted": _short(adoption["adopted"]["sha256"]), "detector": adoption["detector"],
            "told_keys_dropped": adoption["told_keys_dropped"], "ledger_lines": len(after)}


# --- what the ledger says: cutover, shadow dates, legacy --------------------------------------------------

def _cutover_rows(rows: list[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [row for row in rows if row.get("event") in CUTOVER_EVENTS]


def cutover_at(rows: list[Mapping[str, Any]]) -> str | None:
    """The boundary the ledger records last; the state's copy is a cache of it."""
    found = _cutover_rows(rows)
    return str(found[-1]["cutover_at"]) if found else None


def cutover_generation(rows: list[Mapping[str, Any]]) -> int:
    """How many times the boundary was set or moved. Shadow dates belong to one generation, so a
    migration restarts them (H6b-hardening) and moving back cannot revive the old ones."""
    return len(_cutover_rows(rows))


def _legacy(rows: list[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    return next((row for row in rows if row.get("event") == EVENT_LEGACY), None)


def _legacy_import_rows(existing: list[dict[str, Any]], state: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Once (H6d-min): what the pre-H6d state file held — the Toss counters, the verified dates, the
    first verified fire — becomes one ledger line, so a lost cache cannot lose it. Counters only."""
    if _legacy(existing) is not None:
        return []
    counters, dates, first = _legacy_fields(state)        # first: a falsy 0 or False is refused, not "nothing"
    if not any(state.get(name) for name in ("toss_evidence", "shadow_success_dates", "first_verified_fire_at")):
        return []
    return [{"event": EVENT_LEGACY, "source": "ledger", "source_event_key": "legacy_import:v1",
             "toss_evidence": counters, "shadow_success_dates": dates, "first_verified_fire_at": first,
             "cutover_at": cutover_at(existing) or "", "cutover_generation": cutover_generation(existing),
             "provenance": "state_file_before_h6d_min"}]


_KST_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
_LEGACY_COUNTERS = ("buy_explained", "sell_explained", "activity_unexplained")


def _is_kst_day(value: Any) -> bool:
    """A date as H6b wrote it (``date.isoformat()``): the shape, and a day the calendar has."""
    if not isinstance(value, str) or not _KST_DAY.fullmatch(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _legacy_fields(state: Mapping[str, Any]) -> tuple[dict[str, Any], list[str], str]:
    """The pre-H6d fields as the H6b code wrote them, or a refusal: a counter that is not a whole number,
    a day that is not a KST date (shape and calendar), a first fire that is not a time in the Z form.
    Absent means the key is missing or null, the only empty values H6b left; any other falsy value (0,
    False, "", [], {}) is a refusal, not "never recorded". Nothing is coerced, so nothing is imported from
    a cache that does not say what H6b meant (it raises inside the ledger write, so the refusal leaves the
    ledger and the state as they were)."""
    def refuse(what: str) -> ToolError:
        return ToolError(LEDGER_TAMPERED, f"{STATE_FILENAME}: the pre-H6d {what} is malformed; nothing imported")

    def count(value: Any, what: str) -> int:
        if value is None:
            return 0
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise refuse(what)
        return value

    raw = state.get("toss_evidence")
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise refuse("toss_evidence")
    counters: dict[str, Any] = {name: count(raw.get(name), f"toss_evidence.{name}")
                                for name in _LEGACY_COUNTERS}
    days = raw.get("settlement_days")
    if days is None:
        days = {}
    if not isinstance(days, dict):
        raise refuse("toss_evidence.settlement_days")
    settlement: dict[str, Any] = {}
    for day, row in days.items():
        if not _is_kst_day(day) or not isinstance(row, dict):
            raise refuse("toss_evidence.settlement_days")
        settlement[day] = {name: count(row.get(name), f"toss_evidence.settlement_days.{name}")
                           for name in ("windows", "unexplained")}
    counters["settlement_days"] = settlement
    dates = state.get("shadow_success_dates")
    if dates is None:
        dates = []
    if not isinstance(dates, list) or not all(_is_kst_day(d) for d in dates):
        raise refuse("shadow_success_dates")
    first = state.get("first_verified_fire_at")
    if first is None:
        first = ""
    elif not isinstance(first, str) or not first.endswith("Z") or _iso_ms(first) is None:
        raise refuse("first_verified_fire_at")              # the Z form H6b wrote: shadow_record compares text
    return counters, sorted(dates), first


def legacy_import_refusal(rows: list[Mapping[str, Any]], state: Mapping[str, Any]) -> str | None:
    """Why the pending pre-H6d import would be refused, or None (imported already, nothing to import,
    or well formed). The readiness reads it, so a refused import is a FAIL there, not a silent stall."""
    if _legacy(rows) is not None:
        return None
    try:
        _legacy_import_rows(list(rows), state)  # type: ignore[arg-type]
    except ToolError as exc:
        return exc.reason_code
    return None


def shadow_record(rows: list[Mapping[str, Any]]) -> tuple[str | None, list[str]]:
    """(first verified fire, verified KST dates) for the current cutover generation, from the ledger."""
    at, generation = cutover_at(rows), cutover_generation(rows)
    if not at:
        return None, []
    first = [str(r["fire_at"]) for r in rows if r.get("event") == EVENT_FIRST_FIRE
             and r.get("cutover_generation") == generation]
    dates = {str(r["kst_date"]) for r in rows if r.get("event") == EVENT_SHADOW_DATE
             and r.get("cutover_generation") == generation}
    legacy = _legacy(rows)
    if legacy is not None and legacy.get("cutover_generation") == generation and legacy.get("cutover_at") == at:
        dates |= set(legacy.get("shadow_success_dates") or [])
        if legacy.get("first_verified_fire_at"):
            first.insert(0, str(legacy["first_verified_fire_at"]))
    start = _kst_date(at)
    return (min(first) if first else None), sorted(d for d in dates if d >= start)


# --- the Binance pass --------------------------------------------------------------------------------

def collect_binance(feed: Any, state_dir: Path, *, now: str, now_ms: int | None = None,
                    clock: Callable[[], float] = time.monotonic) -> dict[str, Any]:
    """Read every history from its cursor (less the overlap) outside any lock, then commit: append what
    is new under the ledger lock and move the cursors. Returns ``{"written": n, "internal_ms": [...],
    "errors": {source: code}}``. A source whose read fails keeps its cursor and its status says why."""
    from .binance_wallet import FLOW_SOURCES

    now_ms = now_ms if now_ms is not None else int(timeutil.parse_iso(now).timestamp() * 1000)
    known = load_state(state_dir).get("sources") or {}
    started = clock()
    reads: dict[str, list[Mapping[str, Any]]] = {}
    health: dict[str, dict[str, Any]] = {}
    errors: dict[str, str] = {}
    skipped: list[dict[str, Any]] = []
    ordered = [*CRITICAL_SOURCES, *(name for name in FLOW_SOURCES if name not in CRITICAL_SOURCES)]
    for source in ordered:
        _path, _fixed, _window, days = FLOW_SOURCES[source]
        info = dict(known.get(source) or {"access": "UNREAD", "schema": SCHEMA_NO_ROWS, "cursor_ms": None})
        health[source] = info
        if source in CRITICAL_SOURCES:
            started = clock()       # the shadow budget starts once the critical reads are done
        elif clock() - started > READ_BUDGET_SECONDS:
            errors[source] = "READ_BUDGET"
            info["access"] = "SKIPPED READ_BUDGET"     # a past PASS must not stand for this fire
            continue
        had_cursor = info.get("cursor_ms")
        cursor = had_cursor or now_ms - START_LOOKBACK_MS
        start = max(cursor - OVERLAP_MS, now_ms - days * 86_400_000 + 60_000)
        try:
            reads[source] = list(feed.flow_history(source, start_ms=start, end_ms=now_ms))
        except MvpRuntimeError as exc:
            info["access"] = f"FAIL {exc.reason_code}"
            errors[source] = exc.reason_code
            continue
        info["access"] = "PASS"
        if not info.get("covered_from_ms"):
            info["covered_from_ms"] = start      # PR-B: where this source's reading began (state, not ledger)
        if had_cursor and had_cursor < start:
            # The cursor fell behind the query depth: [cursor, start) is never read. Say so (C7 §12.3).
            skipped.append({"event": EVENT_FLOW_SKIPPED, "source": "ledger", "flow_source": source,
                            "source_event_key": f"{source}:skipped:{had_cursor}..{start}",
                            "window_start": _ms_iso(had_cursor), "window_end": _ms_iso(start), "reason": SKIP_BEYOND_DEPTH})
    internal_ms: list[int] = []
    malformed_new: list[int] = [0]

    def rows(existing: list[dict[str, Any]], _state: dict[str, Any]) -> list[Mapping[str, Any]]:
        built, internal = _normalize_reads(reads, health, existing, now_ms=now_ms)
        internal_ms.extend(internal)
        malformed_new[0] = sum(1 for r in built if r.get("event") == EVENT_MALFORMED)
        return [*built, *skipped]

    def patch(state: dict[str, Any], _after: list[dict[str, Any]]) -> None:
        sources = state.setdefault("sources", {})
        for source, info in health.items():
            sources[source] = info
        state["updated_at"] = now
        state["last_binance_pass"] = {"at": now, "errors": dict(errors), "malformed_new": malformed_new[0]}

    written = _commit(state_dir, now=now, rows=rows, patch=patch)
    return {"written": len(written), "internal_ms": sorted(internal_ms), "errors": errors,
            "malformed_new": malformed_new[0]}


def _normalize_reads(reads: Mapping[str, list[Mapping[str, Any]]], health: dict[str, dict[str, Any]],
                     ledger: list[dict[str, Any]], *, now_ms: int) -> tuple[list[dict[str, Any]], list[int]]:
    """The read rows as ledger lines, against the ledger as it stands under the lock."""
    boundary = cutover_at(ledger)
    cutover_ms = _iso_ms(boundary) if boundary else None
    seen = {row.get("source_event_key") for row in ledger}
    recorded = {row.get("source_event_key"): row for row in ledger if row.get("event") in (EVENT_FLOW, EVENT_INTERNAL)}
    pending_rows: list[dict[str, Any]] = []
    internal_ms: list[int] = []
    for source, rows in reads.items():
        info = health[source]
        oldest_pending = None
        for row in rows:
            try:
                venue_id, event, failed = NORMALIZERS[source](row)
            except (KeyError, ValueError, TypeError):
                info["schema"] = SCHEMA_MISMATCH
                digest = hashlib.sha256(json.dumps(row, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:20]
                key = f"{source}:malformed:{digest}"
                if key in seen:
                    continue
                seen.add(key)
                pending_rows.append({"event": EVENT_MALFORMED, "source": source, "source_event_key": key,
                                     "accounting_status": HELD, "held_reason": MALFORMED_SOURCE_EVENT})
                continue
            if info.get("schema") != SCHEMA_MISMATCH:
                info["schema"] = SCHEMA_VERIFIED
            key = f"{source}:{venue_id}"
            if failed and key in recorded and not any(
                    r.get("reverses") == key for r in ledger + pending_rows):
                pending_rows.append({"event": EVENT_REVERSED, "source": source, "source_event_key": f"{key}:reversal",
                                     "reverses": key, "accounting_status": HELD, "held_reason": "venue_reversed"})
                continue
            if event is None:
                if not failed:
                    t = _row_time(row)
                    oldest_pending = t if oldest_pending is None or (t and t < oldest_pending) else oldest_pending
                continue
            if event["direction"] == "internal":
                internal_ms.append(event["event_ms"])
            if key in seen:
                continue
            seen.add(key)
            internal = event["direction"] == "internal"
            held = HELD_BY_SOURCE.get(source)
            phase = (NO_CUTOVER if cutover_ms is None
                     else PRE_CUTOVER if event["event_ms"] < cutover_ms else POST_CUTOVER)
            pending_rows.append({
                "event": EVENT_INTERNAL if internal else EVENT_FLOW, "source": source, "source_event_key": key,
                "economic_event_id": key, **event, "source_confidence": CONFIRMED, "valuation_status": UNVALUED,
                "accounting_status": HELD if held else READY, "held_reason": held,
                "cutover_phase": phase, "accounting_eligibility": ELIGIBLE if phase == POST_CUTOVER else OBSERVED_ONLY,
            })
        # The cursor stops behind the oldest row still pending, so it is read again until it settles.
        info["cursor_ms"] = min(now_ms, oldest_pending) if oldest_pending else now_ms
    pending_rows.extend(_mark_cross_source(pending_rows, ledger, seen))
    return pending_rows, internal_ms


def _row_time(row: Mapping[str, Any]) -> int | None:
    for name in ("insertTime", "applyTime", "createTime", "transactionTime", "timestamp"):
        if name in row:
            try:
                return _ms(row[name])
            except (ValueError, TypeError):
                return None
    return None


def set_cutover(state_dir: Path, *, at: str, requested_by: str, reason: str | None = None,
                migrate: bool = False, shadow_started_at: str | None = None, now: str | None = None) -> dict[str, Any]:
    """Record the accounting boundary once (Thomas 2026-10-09). A second call is refused; moving it needs
    ``migrate`` and a reason, and writes a ``cutover_migrated`` line naming the old and the new. The
    refusal is decided against the ledger, under its lock."""
    if _iso_ms(at) is None:
        raise ToolError(CUTOVER_REFUSED, f"not a time: {at!r}")

    def rows(existing: list[dict[str, Any]], _state: dict[str, Any]) -> list[Mapping[str, Any]]:
        previous = cutover_at(existing)
        if previous and not migrate:
            raise ToolError(CUTOVER_REFUSED, f"the cutover is already set ({previous}); moving it is a migration")
        if migrate and not (reason or "").strip():
            raise ToolError(CUTOVER_REFUSED, "a cutover migration needs a reason")
        event = EVENT_CUTOVER_MIGRATED if previous else EVENT_CUTOVER_SET
        generation = cutover_generation(existing) + 1
        key = f"{event}:{at}" if generation == 1 else f"{event}:{at}:{generation}"
        return [{"event": event, "source": "ledger", "source_event_key": key, "cutover_at": at,
                 "previous_cutover_at": previous, "reason": (reason or "").strip() or None,
                 "requested_by": requested_by, "cutover_generation": generation}]

    def patch(state: dict[str, Any], _after: list[dict[str, Any]]) -> None:
        if shadow_started_at and not state.get("shadow_started_at"):
            state["shadow_started_at"] = shadow_started_at

    _commit(state_dir, now=now or timeutil.utc_now_iso(), rows=rows, patch=patch)
    return load_state(state_dir)


def _kst_date(at: str) -> str:
    return (timeutil.parse_iso(at) + timedelta(hours=9)).date().isoformat()


def mark_fire_verified(state_dir: Path, *, now: str) -> None:
    """Called once a fire's histories all read and its ledger, Toss and readiness passes ended cleanly
    (Thomas 2026-10-09). The first such fire after the cutover is the boundary's evidence; each KST date
    with one counts toward the shadow observation. Both are ledger lines (H6d-min): one per date and
    cutover generation, so a restart neither loses nor doubles them."""
    def rows(existing: list[dict[str, Any]], _state: dict[str, Any]) -> list[Mapping[str, Any]]:
        at = cutover_at(existing)
        if not at or (_iso_ms(now) or 0) < (_iso_ms(at) or 0):
            return []
        generation = cutover_generation(existing)
        common = {"source": "ledger", "cutover_at": at, "cutover_generation": generation}
        return [{"event": EVENT_FIRST_FIRE, "source_event_key": f"first_verified_fire:{generation}",
                 "fire_at": now, **common},
                {"event": EVENT_SHADOW_DATE, "source_event_key": f"shadow_date:{generation}:{_kst_date(now)}",
                 "kst_date": _kst_date(now), **common}]

    _commit(state_dir, now=now, rows=rows)


# --- exceptions, resolution and the three judgements -----------------------------------------------------

def _window_bounds(row: Mapping[str, Any]) -> tuple[int | None, int | None]:
    """A Toss residual's window: its own fields, or (lines before H6d-min) its key,
    ``toss:residual:<pocket>:<start>..<end>``."""
    start, end = row.get("window_start"), row.get("window_end")
    if not (start and end):
        key, prefix = str(row.get("source_event_key") or ""), f"toss:residual:{row.get('pocket')}:"
        if key.startswith(prefix) and ".." in key:
            start, end = key[len(prefix):].split("..", 1)
    return _iso_ms(start), _iso_ms(end)


def event_phase(row: Mapping[str, Any], cutover_ms: int | None) -> str:
    """One rule for every source (H6d-min): the time the economic event happened, against the boundary.
    Binance: the venue's ``event_ms``. Toss: the observed window — wholly before or after, or UNKNOWN
    when the boundary falls inside it (never POST by default). A malformed row: when it was first seen,
    the only time there is. No readable time is UNKNOWN."""
    if cutover_ms is None:
        return NO_CUTOVER
    event = row.get("event")
    if event == EVENT_TOSS_RESIDUAL:
        start, end = _window_bounds(row)
        if start is None or end is None:
            return UNKNOWN_PHASE
        return PRE_CUTOVER if end <= cutover_ms else POST_CUTOVER if start >= cutover_ms else UNKNOWN_PHASE
    moment = row.get("event_ms") if event in (EVENT_FLOW, EVENT_INTERNAL) else _iso_ms(row.get("at"))
    if not isinstance(moment, int):
        return UNKNOWN_PHASE
    return PRE_CUTOVER if moment < cutover_ms else POST_CUTOVER


def resolutions(rows: list[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    return {str(r["resolves"]): r for r in rows if r.get("event") == EVENT_RESOLVED}


def exception_category(row: Mapping[str, Any], rows: list[Mapping[str, Any]]) -> str:
    event, key = row.get("event"), row.get("source_event_key")
    if event == EVENT_TOSS_RESIDUAL:
        return CATEGORY_TOSS
    if event == EVENT_MALFORMED:
        return CATEGORY_MALFORMED
    if any(r.get("event") == EVENT_REVERSED and r.get("reverses") == key for r in rows):
        return CATEGORY_REVERSED
    if row.get("held_reason") == "possible_duplicate" or any(
            r.get("event") == EVENT_DUPLICATE_LINK and key in (r.get("event_a"), r.get("event_b")) for r in rows):
        return CATEGORY_DUPLICATE
    if row.get("held_reason") in HELD_BY_SOURCE.values():
        return CATEGORY_PAYMENT
    return CATEGORY_HELD


def detected_exceptions(rows: list[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    """Every event after (or straddling) the cutover that nothing explains yet, resolved or not: a
    Binance event held for any reason, a malformed row, a Toss residual (Thomas 2026-10-09)."""
    at = cutover_at(rows)
    if not at:
        return {}
    cutover_ms = _iso_ms(at)
    status = effective_status(rows)
    found: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        event, key = row.get("event"), row.get("source_event_key")
        if event not in EXCEPTION_EVENTS or event_phase(row, cutover_ms) == PRE_CUTOVER:
            continue
        if event in (EVENT_FLOW, EVENT_INTERNAL) and status.get(key) != HELD:
            continue
        found[str(key)] = row
    return found


def open_exceptions(rows: list[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    closed = resolutions(rows)
    return {key: row for key, row in detected_exceptions(rows).items() if key not in closed}


def post_cutover_exceptions(rows: list[dict[str, Any]], state: Mapping[str, Any] | None = None) -> int:
    """How many events block the readiness: detected after the cutover and not closed by Thomas."""
    return len(open_exceptions(rows))


def lifecycle(rows: list[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Each exception's three judgements, kept apart (H6d-min): review closure (Thomas classified it),
    evidence validation (a source proves the meaning) and accounting eligibility. In H6b the last two are
    never true: no resolution is evidence, and nothing is valued, so nothing is eligible."""
    closed = resolutions(rows)
    return {key: {"category": exception_category(row, rows),
                  "review": "CLOSED" if key in closed else "OPEN",
                  "kind": (closed.get(key) or {}).get("kind"),
                  "evidence_validated": False, "accounting_eligible": False}
            for key, row in detected_exceptions(rows).items()}


def _allowed_kinds(row: Mapping[str, Any], rows: list[Mapping[str, Any]]) -> tuple[str, ...]:
    category = exception_category(row, rows)
    kinds = ALLOWED_KINDS[category]
    if category == CATEGORY_PAYMENT:
        return ("deposit",) if row.get("direction") == "in" else ("withdrawal",) if row.get("direction") == "out" else ()
    if category == CATEGORY_TOSS:
        sign = _canon(row.get("residual"))
        signed = (("deposit", "income") if sign is not None and sign > 0
                  else ("withdrawal", "fee") if sign is not None and sign < 0 else ())
        return (*signed, "semantics_mismatch", *(("read_incomplete",) if row.get("orders_truncated") else ()))
    return kinds


def resolve(state_dir: Path, *, target: str, kind: str, reason: str, requested_by: str,
            interactive: bool, now: str | None = None) -> dict[str, Any]:
    """Close one exception's review with a ``flow_resolved`` line (H6d-min D2: Thomas, at his terminal).

    Refused without an interactive terminal, ``--by`` or ``--reason`` — guards against slips and leaves
    an audit trail, not authentication. Decided against the ledger under its lock: the target must be an
    open exception; a second resolution, an unknown or wrong event, an applied event and a kind the
    category does not allow are refused. The original line is never touched, and the event stays HELD:
    closing a review neither validates evidence nor makes it eligible for accounting."""
    if not interactive:
        raise ToolError(RESOLVE_REFUSED, "resolving runs at an interactive terminal")
    if not (requested_by or "").strip() or not (reason or "").strip():
        raise ToolError(RESOLVE_REFUSED, "resolving needs --by and --reason")
    if kind not in ALL_KINDS:
        raise ToolError(RESOLVE_KIND, f"unknown kind {kind!r}")
    now = now or timeutil.utc_now_iso()

    def rows(existing: list[dict[str, Any]], _state: dict[str, Any]) -> list[Mapping[str, Any]]:
        matches = [r for r in existing if r.get("source_event_key") == target]
        if not matches:
            raise ToolError(RESOLVE_NO_TARGET, "no ledger event has that key")
        row = matches[0]
        if row.get("event") not in EXCEPTION_EVENTS:
            raise ToolError(RESOLVE_INVALID_TARGET, f"a {row.get('event')} line is not an event to resolve")
        current = resolutions(existing).get(target)
        # A rule's resolution (``QUIET_WINDOW_RULE``) is Thomas's to re-close, once; his own never is.
        overriding = current is not None and str(current.get("requested_by") or "").startswith("rule:")
        if current is not None and not overriding:
            raise ToolError(RESOLVE_DUPLICATE, "that event is already resolved")
        if row.get("accounting_status") == "APPLIED":
            raise ToolError(RESOLVE_INVALID_TARGET, "an applied event is not resolved this way")
        if not overriding and target not in open_exceptions(existing):
            raise ToolError(RESOLVE_NOT_OPEN, "that event does not block the readiness (before the cutover, "
                                              "or not held)")
        allowed = _allowed_kinds(row, existing)
        if kind not in allowed:
            raise ToolError(RESOLVE_KIND, f"{kind!r} is not allowed here; allowed: {', '.join(allowed) or 'none'}")
        line = {"event": EVENT_RESOLVED, "source": "ledger",
                "source_event_key": f"resolved:{target}" + (":override" if overriding else ""),
                "resolves": target, "target_event": row.get("event"), "category": exception_category(row, existing),
                "kind": kind, "reason": reason.strip(), "requested_by": requested_by.strip(), "timestamp": now,
                "review_status": "CLOSED", "evidence_status": "NOT_VALIDATED",
                "accounting_eligibility": "NOT_ELIGIBLE"}
        if overriding:
            line["supersedes"] = current.get("source_event_key")
        return [line]

    written = [r for r in _commit(state_dir, now=now, rows=rows) if r.get("event") == EVENT_RESOLVED]
    if not written:      # the unique check dropped it: a concurrent resolution got there first
        raise ToolError(RESOLVE_DUPLICATE, "that event is already resolved")
    return written[0]


def flows_view(state_dir: Path) -> list[dict[str, Any]]:
    """Open and closed exceptions for Thomas's terminal (``--flows``): key, category, phase, review,
    allowed kinds and direction. No amount. LOCAL — never a door, never a message."""
    rows = verify(state_dir)
    at = cutover_at(rows)
    cutover_ms = _iso_ms(at) if at else None
    life = lifecycle(rows)
    closed = resolutions(rows)
    windows = {r.get("source_event_key"): r for r in rows if r.get("event") == EVENT_TOSS_WINDOW}
    by_rule = {r.get("resolves") for r in rows
               if r.get("event") == EVENT_RESOLVED and r.get("requested_by") in RULE_IDS}
    out = []
    for key, row in detected_exceptions(rows).items():
        sign = _canon(row.get("residual")) if row.get("event") == EVENT_TOSS_RESIDUAL else None
        by = str((closed.get(key) or {}).get("requested_by") or "")
        # Why the quiet-window rule left a KRW residual open: as recorded, or derived for an older line.
        declined, derived = None, False
        if row.get("event") == EVENT_TOSS_RESIDUAL and row.get("pocket") == "KRW" and key not in by_rule:
            declined = row.get("quiet_window_declined")
            if declined is None and key.startswith("toss:residual:KRW:"):
                window = windows.get("toss:window:" + key[len("toss:residual:KRW:"):])
                declined, derived = _derived_decline(row, window), True
        out.append({"key": key, "event": row.get("event"), "category": life[key]["category"],
                    "phase": event_phase(row, cutover_ms), "review": life[key]["review"],
                    "kind": life[key]["kind"], "allowed": list(_allowed_kinds(row, rows)),
                    # Who closed it: a rule's resolution is Thomas's to re-close once (``resolve``).
                    "closed_by": None if not by else "rule" if by.startswith("rule:") else "thomas",
                    "direction": row.get("direction") or (None if sign is None else "in" if sign > 0 else "out"),
                    "rule_declined": declined, "rule_declined_derived": derived})
    return out


# --- the Toss window continuity diagnostic (C7 D2, Thomas 2026-10-11) ------------------------------------
# Terminal only (``holdings_board --toss-continuity``). It asks one question of the ledger: do the observed
# Toss windows tile the stretch from the latest cutover to the last window? Not the Toss cash semantics, not
# history completeness, not readiness; it writes nothing and enables nothing. Each window runs from the
# previous fire's cash point to this fire, so a downtime is one long window, and a gap is a fire that
# recorded no window (an unusable snapshot, a moved cache) — not proof that a record was lost. Since C7 PR-B
# such a fire writes a ``toss_window_skipped`` line naming the stretch and why; a gap a skipped line covers
# is explained, and only an uncovered gap is UNEXPLAINED_GAP. Skipped lines never count as windows.
# Worst first: LEDGER_INVALID, UNCHECKED, UNEXPLAINED_GAP, INCOMPLETE_EVIDENCE.
TOSS_CONTINUOUS = "CONTINUOUS"
TOSS_UNEXPLAINED_GAP = "UNEXPLAINED_GAP"
TOSS_INCOMPLETE_EVIDENCE = "INCOMPLETE_EVIDENCE"
TOSS_LEDGER_INVALID = "LEDGER_INVALID"
TOSS_UNCHECKED = "UNCHECKED"
# The ledger is LOCAL_ONLY: the check runs at Thomas's interactive terminal, refused before it reads anything
# otherwise. A slip guard like the resolve and epoch ones — not a boundary against root or the docker group.
TOSS_CONTINUITY_REFUSED = "HOLDINGS_CASH_FLOW_TOSS_CONTINUITY_REFUSED"


def toss_continuity(rows: list[Mapping[str, Any]], *, now: str) -> dict[str, Any]:
    """The continuity verdict over verified ledger rows, with its counts and gap bounds. No amount."""
    at = cutover_at(rows)
    cutover_ms = _iso_ms(at) if at else None
    result: dict[str, Any] = {"status": TOSS_UNCHECKED, "reasons": [], "cutover_generation": cutover_generation(rows),
                              "windows": 0, "windows_before_cutover": 0, "epochs": 0, "gaps": [], "gap_hours": 0.0,
                              "overlaps": 0, "duplicates": 0, "truncated": 0, "unreadable": 0, "amended_windows": 0,
                              "orphan_amendments": 0, "leading_hours": None, "longest_window_hours": None,
                              "trailing_hours": None, "skipped": 0}
    if cutover_ms is None:
        result["reasons"] = ["empty_ledger" if not rows else "no_cutover"]
        return result
    hours = lambda ms: round(ms / 3_600_000, 1)                                             # noqa: E731
    windows: list[tuple[int, int, str, str]] = []
    keys: set[str] = set()
    epochs: set[Any] = set()
    for row in rows:
        if row.get("event") != EVENT_TOSS_WINDOW:
            continue
        keys.add(str(row.get("source_event_key")))
        start, end = _iso_ms(row.get("window_start")), _iso_ms(row.get("window_end"))
        if start is None or end is None or end < start:
            result["unreadable"] += 1
            continue
        if end <= cutover_ms:
            result["windows_before_cutover"] += 1
            continue
        windows.append((max(start, cutover_ms), end, at if start < cutover_ms else str(row["window_start"]),
                        str(row["window_end"])))
        epochs.add(row.get("epoch"))
        result["truncated"] += bool(row.get("orders_truncated"))
    amended = {str(r.get("amends")) for r in rows if r.get("event") == EVENT_TOSS_AMENDED}
    result["amended_windows"] = len(amended & keys)
    result["orphan_amendments"] = len(amended - keys)
    result["epochs"] = len(epochs)
    skips: list[tuple[int, int, str, str]] = []
    for row in rows:
        if row.get("event") != EVENT_TOSS_SKIPPED:
            continue
        start, end = _iso_ms(row.get("window_start")), _iso_ms(row.get("window_end"))
        if start is None or end is None or end < start:
            result["unreadable"] += 1
            continue
        if end > cutover_ms:
            skips.append((max(start, cutover_ms), end, at if start < cutover_ms else str(row["window_start"]),
                          str(row["window_end"])))
    result["skipped"] = len(skips)
    windows.sort()
    seen: set[tuple[int, int]] = set()
    cursor: tuple[int, str] | None = None
    unique: list[tuple[int, int, str, str]] = []
    for start, end, start_at, end_at in windows:
        if (start, end) in seen:
            result["duplicates"] += 1
            continue
        seen.add((start, end))
        unique.append((start, end, start_at, end_at))
        result["windows"] += 1
        if cursor is not None and start < cursor[0]:
            result["overlaps"] += 1                      # two observed windows over one stretch
        if cursor is None or end > cursor[0]:
            cursor = (end, end_at)
    # Coverage: observed windows and named skips together; a gap is a stretch neither covers.
    covered: tuple[int, str] | None = None
    for start, end, start_at, end_at in sorted(unique + skips):
        if covered is not None and start > covered[0]:
            result["gaps"].append({"from": covered[1], "to": start_at, "hours": hours(start - covered[0])})
        if covered is None or end > covered[0]:
            covered = (end, end_at)
    reasons: list[str] = []
    if result["gaps"]:
        reasons.append("unexplained_gap")
        result["gap_hours"] = round(sum(g["hours"] for g in result["gaps"]), 1)
    if not windows:
        reasons.append("no_window_since_cutover")
    else:
        lead = min(start for start, _e, _s, _x in unique + skips) - cutover_ms
        result["leading_hours"] = hours(lead)
        result["longest_window_hours"] = hours(max(end - start for start, end, _s, _e in windows))
        result["trailing_hours"] = hours(max(0, (_iso_ms(now) or 0) - cursor[0])) if cursor else None
        if lead > 0:
            reasons.append("no_window_before_first")     # the stretch before window lines existed: not covered
    for name, reason in (("overlaps", "overlap"), ("duplicates", "duplicate_window"), ("truncated", "orders_truncated"),
                         ("unreadable", "window_unreadable"), ("orphan_amendments", "orphan_amendment")):
        if result[name]:
            reasons.append(reason)
    result["reasons"] = reasons
    result["status"] = (TOSS_UNEXPLAINED_GAP if result["gaps"] else TOSS_INCOMPLETE_EVIDENCE if reasons
                        else TOSS_CONTINUOUS)
    return result


def toss_continuity_view(state_dir: Path, *, now: str, interactive: bool) -> dict[str, Any]:
    """For Thomas's terminal (``--toss-continuity``): the verdict over the verified ledger. A chain that
    does not verify is LEDGER_INVALID with its reason code. Refused without an interactive terminal, before
    the ledger is opened. LOCAL — never a door, never a message."""
    if not interactive:
        raise ToolError(TOSS_CONTINUITY_REFUSED, "the continuity check runs at an interactive terminal "
                                                 "(it reads the LOCAL_ONLY ledger)")
    try:
        rows = verify(state_dir)
    except ToolError as exc:
        return {**toss_continuity([], now=now), "status": TOSS_LEDGER_INVALID, "reasons": [exc.reason_code]}
    return toss_continuity(rows, now=now)


def _canon(amount: Any) -> Decimal | None:
    """The amount as a number for comparison: "1", "1.0" and "1.00000000" are one amount. The ledger keeps
    the venue's own string; only comparisons use this."""
    try:
        return Decimal(str(amount)).normalize()
    except (InvalidOperation, ValueError):
        return None


def _mark_cross_source(new_rows: list[dict[str, Any]], ledger: list[dict[str, Any]],
                       seen: set[Any]) -> list[dict[str, Any]]:
    """Same asset, same amount (as a number), same direction, two sources, within the window: the new
    row is HELD and a ``duplicate_linked`` line names both, so :func:`effective_status` holds the older
    one too — the ledger is never edited. Returns the link lines."""
    flows = [r for r in new_rows if r.get("event") == EVENT_FLOW]
    pool = [r for r in ledger if r.get("event") == EVENT_FLOW] + flows
    links: list[dict[str, Any]] = []
    for row in flows:
        for other in pool:
            if other is row or other.get("source") == row.get("source"):
                continue
            if (other.get("asset") == row.get("asset") and _canon(other.get("amount")) == _canon(row.get("amount"))
                    and other.get("direction") == row.get("direction")
                    and abs(int(other.get("event_ms") or 0) - int(row["event_ms"])) <= CROSS_SOURCE_WINDOW_MS):
                row.update({"accounting_status": HELD, "held_reason": "possible_duplicate",
                            "possible_duplicate_of": other.get("source_event_key")})
                pair = sorted([str(row["source_event_key"]), str(other.get("source_event_key"))])
                key = f"link:{pair[0]}|{pair[1]}"
                if key not in seen:
                    seen.add(key)
                    links.append({"event": EVENT_DUPLICATE_LINK, "source": "ledger", "source_event_key": key,
                                  "event_a": pair[0], "event_b": pair[1], "effective_status": HELD})
    return links


def effective_status(rows: list[dict[str, Any]]) -> dict[str, str]:
    """Each event's accounting status after the later lines that answer it: a duplicate link or a
    reversal holds both sides, whatever the original line said. A resolution changes nothing here."""
    status = {r["source_event_key"]: r.get("accounting_status") for r in rows
              if r.get("event") in (EVENT_FLOW, EVENT_INTERNAL, EVENT_MALFORMED)}
    for row in rows:
        if row.get("event") == EVENT_DUPLICATE_LINK:
            for key in (row.get("event_a"), row.get("event_b")):
                if key in status:
                    status[key] = HELD
        elif row.get("event") == EVENT_REVERSED and row.get("reverses") in status:
            status[row["reverses"]] = HELD
    return status


# --- the Toss shadow pass ------------------------------------------------------------------------------

def _pocket_effect(order: Mapping[str, Any]) -> tuple[str, float] | None:
    """(currency, cash effect) of one closed order's fills, or None if it filled nothing readable."""
    execution = order.get("execution") or {}
    try:
        filled = float(execution.get("filledAmount") or 0)
        commission = float(execution.get("commission") or 0)
        tax = float(execution.get("tax") or 0)
    except (TypeError, ValueError):
        return None
    if not filled:
        return None
    sign = -1.0 if str(order.get("side")).upper() == "BUY" else 1.0
    return str(order.get("currency") or "?").upper(), sign * filled - commission - tax


ORDER_ID_FIELDS = ("orderId", "orderNo", "id")
# Without an id: only what an order cannot change once placed. Fill, settlement and status fields move
# between reads (a settlement revision, a re-read, a late fill), so they never name the order.
ORDER_IDENTITY_FIELDS = ("side", "currency", "orderedAt")
ORDER_SYMBOL_FIELDS = ("symbol", "stockCode")
UNSTABLE = "unstable"


def order_identity(order: Mapping[str, Any]) -> tuple[str, list[str]]:
    """(basis, hashes) naming one order without its id or contents (H6d-min). Up to two hashes: the
    venue's id, and side + currency + order time + symbol. Two reads are one order when any hash is shared,
    so a re-read that drops or adds the id still matches. With neither, the order cannot be told apart from
    another: no hash (``unstable``), and a window it touches is no evidence. LOCAL: ledger only."""
    hashes: list[str] = []
    ident = next((order.get(n) for n in ORDER_ID_FIELDS if order.get(n) not in (None, "")), None)
    if ident is not None:
        hashes.append(_order_digest(f"id:{ident}"))
    symbol = next((order.get(n) for n in ORDER_SYMBOL_FIELDS if order.get(n) not in (None, "")), None)
    values = [order.get(n) for n in ORDER_IDENTITY_FIELDS]
    if symbol is not None and all(v not in (None, "") for v in values):
        hashes.append(_order_digest(json.dumps([str(values[0]).upper(), str(values[1]).upper(),
                                                str(values[2]), str(symbol)])))
    basis = UNSTABLE if not hashes else "id+fields" if len(hashes) == 2 else "id" if ident is not None else "fields"
    return basis, sorted(hashes)


def _order_digest(material: str) -> str:
    return hashlib.sha256(f"toss-order|{material}".encode("utf-8")).hexdigest()[:24]


def _same_order(a: Any, b: Any) -> bool:
    return bool(set(a or ()) & set(b or ()))


def _distinct_orders(entries: list[list[str]]) -> int:
    """How many orders, counting entries that share a hash once."""
    groups: list[set[str]] = []
    for entry in entries:
        merged = set(entry)
        for group in [g for g in groups if g & merged]:
            merged |= group
            groups.remove(group)
        groups.append(merged)
    return len(groups)


def reconcile_window(previous: Mapping[str, Any], current: Mapping[str, Any], orders: list[Mapping[str, Any]],
                     *, since: str, until: str, rate: float | None) -> dict[str, Any]:
    """One window's reconciliation: the pockets' change less what closed orders filled inside it explain.
    Pure — :func:`reconciliation_digest` runs it over fixed cases, so a change to its behaviour is visible."""
    start, end = timeutil.parse_iso(since), timeutil.parse_iso(until)
    today_kst = (end + timedelta(hours=9)).date().isoformat()
    explained = {"KRW": 0.0, "USD": 0.0}
    fills: dict[str, dict[str, list[list[str]]]] = {"BUY": {"KRW": [], "USD": []}, "SELL": {"KRW": [], "USD": []}}
    settlements: list[dict[str, Any]] = []
    bases: set[str] = set()
    for order in orders:
        currency = str(order.get("currency") or "?").upper()
        side = str(order.get("side")).upper()
        basis, digest = order_identity(order)
        execution = order.get("execution") or {}
        if side == "SELL" and currency in explained and str(execution.get("settlementDate") or "") == today_kst:
            bases.add(basis)
            if digest:
                settlements.append({"order": digest, "day": today_kst, "pocket": currency})
        try:
            when = timeutil.parse_iso(str(execution.get("filledAt"))) if execution.get("filledAt") else None
        except ValueError:
            when = None
        effect = _pocket_effect(order)
        if when is None or effect is None or not (start < when <= end) or effect[0] not in explained:
            continue
        explained[effect[0]] += effect[1]
        bases.add(basis)
        if side in fills and digest:
            fills[side][effect[0]].append(digest)
    residual = {"KRW": (current["KRW"] - previous["KRW"]) - explained["KRW"],
                "USD": (current["USD"] - previous["USD"]) - explained["USD"]}
    tolerance = {"KRW": 1.0, "USD": 0.01}
    open_ = {p: abs(v) > tolerance[p] for p, v in residual.items()}
    exchange = bool(open_["KRW"] and open_["USD"] and rate and residual["KRW"] * residual["USD"] < 0
                    and abs(abs(residual["KRW"]) - abs(residual["USD"]) * rate) <= 0.01 * abs(residual["KRW"]))
    return {"residual": residual, "open": open_, "exchange": exchange, "buy": fills["BUY"], "sell": fills["SELL"],
            "settlements": settlements, "identity_stable": UNSTABLE not in bases,
            "basis": ",".join(sorted(bases)) or "none"}


def toss_epoch(rows: list[Mapping[str, Any]]) -> int:
    """The current semantics epoch: 0 until Thomas starts one (H6d-min)."""
    return sum(1 for r in rows if r.get("event") == EVENT_EPOCH)


def collect_toss(feed: Any, snapshot: Any, state_dir: Path, *, now: str) -> dict[str, Any]:
    """One observed window since the last fire's cash point: a ``toss_window_observed`` line, residual or
    exchange lines, and amendments for late evidence on earlier windows. The order read happens outside
    the locks; the commit re-checks that the cash point it started from is still the one on file, and
    writes nothing if another writer moved it (a stale cache never doubles a window)."""
    rate = getattr(snapshot, "usd_krw_rate", None)
    domestic, overseas = getattr(snapshot, "domestic", None), getattr(snapshot, "overseas", None)
    krw = getattr(domestic, "cash_krw", None)
    usd = (getattr(overseas, "cash_krw", None) / rate) if rate and getattr(overseas, "cash_krw", None) is not None else None
    current = {"at": now, "KRW": krw, "USD": usd, "open_orders": _open_order_count(feed)}
    previous = load_state(state_dir).get("toss")
    usable = bool(previous and krw is not None and usd is not None and previous.get("KRW") is not None
                  and previous.get("USD") is not None and previous.get("at"))
    orders: list[Mapping[str, Any]] = []
    truncated = False
    query: dict[str, str] = {}
    if usable:
        since, until = timeutil.parse_iso(previous["at"]), timeutil.parse_iso(now)
        kst = timedelta(hours=9)
        # A week back: a sell settles days after its fill, and the settlement is evidence (D-H6-7).
        query = {"date_from": (since + kst - timedelta(days=7)).date().isoformat(),
                 "date_to": (until + kst).date().isoformat()}
        orders, truncated = feed.closed_orders(**query)
        orders = list(orders)

    def rows(existing: list[dict[str, Any]], state: dict[str, Any]) -> list[Mapping[str, Any]]:
        on_file = state.get("toss")
        if (on_file or {}).get("at") != (previous or {}).get("at"):
            raise _Moved()
        if not usable:
            # No window, but the stretch since the last cash point is named and explained (C7 PR-B).
            if previous and previous.get("at") and _iso_ms(previous["at"]) is not None:
                return [_toss_skip(previous["at"], now, SKIP_SNAPSHOT_UNUSABLE)]
            return []
        windows = [r for r in existing if r.get("event") == EVENT_TOSS_WINDOW and _iso_ms(r.get("window_end"))]
        last = max(windows, key=lambda r: _iso_ms(r["window_end"]), default=None)
        if last is not None and _iso_ms(last["window_end"]) > (_iso_ms(previous["at"]) or 0):
            # The ledger has a later window than this cash point: a stale cache, start over from now. The
            # stretch between that window's end and now gets no window; it is named, not left silent.
            return [_toss_skip(str(last["window_end"]), now, SKIP_CASH_POINT_BEHIND)]
        return _toss_rows(existing, previous, current, orders, truncated, query, rate=rate, now=now)

    def patch(state: dict[str, Any], _after: list[dict[str, Any]]) -> None:
        state["toss"] = current

    try:
        written = _commit(state_dir, now=now, rows=rows, patch=patch)
    except _Moved:
        return {"written": 0, "skipped": "cash point moved by another writer"}
    return {"written": len(written)}


def _open_order_count(feed: Any) -> int | None:
    """How many orders are open at this cash point (quiet_window.v2: a pending buy holds buying power), or
    None when it could not be read — which only declines the rule for the windows this point bounds."""
    reader = getattr(feed, "open_orders", None)
    if reader is None:
        return None
    try:
        return len(list(reader()))
    except Exception:  # noqa: BLE001 — an unread list declines the rule; it never costs the Toss pass
        return None


def _toss_skip(start: str, end: str, reason: str) -> dict[str, Any]:
    return {"event": EVENT_TOSS_SKIPPED, "source": "toss", "source_event_key": f"toss:skipped:{start}..{end}",
            "window_start": start, "window_end": end, "reason": reason}


class _Moved(Exception):
    """The Toss cash point changed between the read and the commit."""


# Toss deposits and withdrawals by rule (HOLDINGS_STATUS_READ_AND_AUTO_FLOWS F1 A, Thomas 2026-10-26). Toss has no
# cash-ledger endpoint (re-read 2026-10-26), so a residual's meaning can only come from the window's shape. A
# window whose KRW change is a round amount is closed as a deposit or withdrawal in the same write that records
# it, by this rule rather than by Thomas; anything else stays an exception for his terminal. Thomas can re-close
# a rule's resolution once (:func:`resolve`), e.g. as income.
#
# v2 (Thomas 2026-10-28): a window with a fill or a settlement is no longer excluded — what filled is already out
# of the residual — and "round" is the nearest 10,000 KRW with a remainder the day's fills can explain (a fee or
# settlement adjustment: 0.1% of the KRW filled that KST day plus 100 KRW, at most 1,000 KRW). A window that
# starts or ends with an open order is excluded instead: a pending buy holds buying power. The amount and the
# remainder go on the resolution line in the LOCAL ledger only; nothing outward carries them.
QUIET_WINDOW_RULE_V1 = "rule:quiet_window.v1"
QUIET_WINDOW_RULE = "rule:quiet_window.v2"
RULE_IDS = frozenset({QUIET_WINDOW_RULE_V1, QUIET_WINDOW_RULE})
QUIET_WINDOW_V1_STEP_KRW = 1000
QUIET_WINDOW_STEP_KRW = 10_000
QUIET_WINDOW_TOLERANCE_BASE_KRW = 100.0
QUIET_WINDOW_TOLERANCE_RATE = 0.001
QUIET_WINDOW_TOLERANCE_CAP_KRW = 1000.0


# Why the rule left a KRW residual open (Thomas 2026-10-28): the first condition that failed, by name — never an
# amount. Written on the residual line as ``quiet_window_declined`` and shown on ``--flows``. ``settlement`` and
# ``fill`` are v1's (a v1 line's derived reason); ``open_order`` and ``too_small`` are v2's.
QUIET_WINDOW_DECLINES = ("orders_truncated", "exchange_pair", "identity_unstable", "settlement", "fill",
                         "open_order", "krw_not_open", "usd_moved", "too_small", "not_round")
# A line written before the field existed: the reason is derived from the stored window by v1, the rule then in
# force. The stored amounts are rounded, so a decline below their precision (under one won, under a cent) — or
# a window older than the rule — cannot be told from a pass.
QUIET_WINDOW_UNDETERMINED = "undetermined"


def round_split(krw: float, day_fills_krw: float) -> tuple[float, float, float]:
    """``(amount, remainder, tolerance)``: the nearest whole 10,000 KRW, what is left, and how much of it the
    day's fills may explain (0.1% of them plus 100 KRW, at most 1,000 KRW)."""
    amount = round(krw / QUIET_WINDOW_STEP_KRW) * QUIET_WINDOW_STEP_KRW
    tolerance = min(QUIET_WINDOW_TOLERANCE_CAP_KRW,
                    QUIET_WINDOW_TOLERANCE_BASE_KRW + QUIET_WINDOW_TOLERANCE_RATE * abs(day_fills_krw))
    return float(amount), krw - amount, tolerance


def quiet_window_verdict(result: Mapping[str, Any], *, truncated: bool, open_orders: tuple[Any, Any],
                         day_fills_krw: float) -> tuple[str | None, str | None]:
    """``(kind, None)`` when v2 closes the window, else ``(None, reason)`` from :data:`QUIET_WINDOW_DECLINES`.

    ``open_orders`` is the open-order count at the window's start and end (None: not read, which declines).
    Kept: an untruncated and stable order read, no exchange pair, the KRW pocket open and the USD pocket
    untouched (a US dividend lands in USD). Pure, like ``reconcile_window``."""
    if truncated:
        return None, "orders_truncated"
    if result.get("exchange"):
        return None, "exchange_pair"
    if result.get("identity_stable") is not True:
        return None, "identity_unstable"
    if any(count is None or count > 0 for count in open_orders):
        return None, "open_order"
    opened, residual = result.get("open") or {}, result.get("residual") or {}
    if not opened.get("KRW"):
        return None, "krw_not_open"
    if opened.get("USD") or abs(float(residual.get("USD") or 0.0)) >= 0.005:
        return None, "usd_moved"
    amount, remainder, tolerance = round_split(float(residual.get("KRW") or 0.0), day_fills_krw)
    if amount == 0:
        return None, "too_small"
    if abs(remainder) > tolerance:
        return None, "not_round"
    return ("deposit" if amount > 0 else "withdrawal"), None


def _v1_decline(result: Mapping[str, Any], *, truncated: bool) -> str | None:
    """v1's first failed condition (2026-10-26 to 2026-10-28), for :func:`_derived_decline` only."""
    if truncated:
        return "orders_truncated"
    if result.get("exchange"):
        return "exchange_pair"
    if result.get("identity_stable") is not True:
        return "identity_unstable"
    if result.get("settlements"):
        return "settlement"
    for side in ("buy", "sell"):
        if any((result.get(side) or {}).get(pocket) for pocket in ("KRW", "USD")):
            return "fill"
    opened, residual = result.get("open") or {}, result.get("residual") or {}
    if not opened.get("KRW"):
        return "krw_not_open"
    if opened.get("USD") or abs(float(residual.get("USD") or 0.0)) >= 0.005:
        return "usd_moved"
    krw = float(residual.get("KRW") or 0.0)
    whole = round(krw)
    if whole == 0 or abs(krw - whole) >= 0.5 or whole % QUIET_WINDOW_V1_STEP_KRW:
        return "not_round"
    return None


def _derived_decline(row: Mapping[str, Any], window: Mapping[str, Any] | None) -> str:
    """v1's verdict re-run on a KRW residual line written before ``quiet_window_declined`` existed."""
    if window is None:
        return QUIET_WINDOW_UNDETERMINED
    status = window.get("residual_status") or {}
    result = {"exchange": window.get("exchange_pair"), "identity_stable": window.get("identity_stable", True),
              "settlements": window.get("settlements"), "buy": window.get("buy_orders"),
              "sell": window.get("sell_orders"), "open": {p: s == "OPEN" for p, s in status.items()},
              "residual": {"KRW": float(_canon(row.get("residual")) or 0), "USD": 0.0}}
    return _v1_decline(result, truncated=bool(window.get("orders_truncated"))) or QUIET_WINDOW_UNDETERMINED


def _day_fills_krw(orders: list[Mapping[str, Any]], *, since: str, until: str) -> float:
    """The KRW filled on the window's KST day(s), buys and sells alike: what a fee or settlement adjustment
    in this window can come from."""
    days = {_kst_date(since), _kst_date(until)}
    total = 0.0
    for order in orders:
        execution = order.get("execution") or {}
        if str(order.get("currency") or "").upper() != "KRW" or not execution.get("filledAt"):
            continue
        try:
            if _kst_date(str(execution["filledAt"])) in days:
                total += abs(float(execution.get("filledAmount") or 0))
        except ValueError:
            continue
    return total


def _rule_resolution(target: str, kind: str, *, now: str, split: tuple[float, float, float]) -> dict[str, Any]:
    amount, remainder, tolerance = split
    return {"event": EVENT_RESOLVED, "source": "ledger", "source_event_key": f"resolved:{target}",
            "resolves": target, "target_event": EVENT_TOSS_RESIDUAL, "category": CATEGORY_TOSS, "kind": kind,
            "reason": "round KRW change: the nearest 10,000 KRW, the remainder within the day's fill tolerance; "
                      "no open order, exchange or USD change",
            "requested_by": QUIET_WINDOW_RULE, "timestamp": now, "review_status": "CLOSED",
            "evidence_status": "NOT_VALIDATED", "accounting_eligibility": "NOT_ELIGIBLE",
            # LOCAL ledger only (Q5): the amount the rule took as the flow, and what it left as an adjustment.
            "rule_amount_krw": f"{amount:.0f}", "rule_remainder_krw": f"{remainder:.0f}",
            "rule_tolerance_krw": f"{tolerance:.0f}"}


def _toss_rows(existing: list[dict[str, Any]], previous: Mapping[str, Any], current: Mapping[str, Any],
               orders: list[Mapping[str, Any]], truncated: bool, query: Mapping[str, str], *, rate: float | None,
               now: str) -> list[dict[str, Any]]:
    window = f"{previous['at']}..{now}"
    result = reconcile_window(previous, current, orders, since=previous["at"], until=now, rate=rate)
    residual, open_ = result["residual"], result["open"]
    bounds = {"window_start": previous["at"], "window_end": now}
    out: list[dict[str, Any]] = [{
        "event": EVENT_TOSS_WINDOW, "source": "toss", "source_event_key": f"toss:window:{window}", **bounds,
        "epoch": toss_epoch(existing), "reconciliation_fingerprint": reconciliation_fingerprint(),
        "buy_orders": result["buy"], "sell_orders": result["sell"], "settlements": result["settlements"],
        "residual_status": {p: "OPEN" if flag else "CLEAR" for p, flag in open_.items()},
        "orders_truncated": bool(truncated), "exchange_pair": result["exchange"],
        "order_hash_basis": result["basis"], "identity_stable": result["identity_stable"], "orders_query": dict(query),
        "source_confidence": UNRESOLVED if any(open_.values()) else RECONCILED, "accounting_status": "SHADOW_ONLY"}]
    if result["exchange"]:
        out.append({"event": EVENT_TOSS_EXCHANGE, "source": "toss", "source_event_key": f"toss:exchange:{window}",
                    **bounds, "source_confidence": RECONCILED, "accounting_status": "SHADOW_ONLY",
                    "krw_residual": f"{residual['KRW']:.0f}", "usd_residual": f"{residual['USD']:.2f}"})
    else:
        kind, declined = quiet_window_verdict(
            result, truncated=bool(truncated), open_orders=(previous.get("open_orders"), current.get("open_orders")),
            day_fills_krw=_day_fills_krw(orders, since=previous["at"], until=now))
        for pocket, flag in open_.items():
            if flag:
                out.append({"event": EVENT_TOSS_RESIDUAL, "source": "toss",
                            "source_event_key": f"toss:residual:{pocket}:{window}", "pocket": pocket, **bounds,
                            "residual": f"{residual[pocket]:.{0 if pocket == 'KRW' else 2}f}",
                            "orders_truncated": bool(truncated), "source_confidence": UNRESOLVED,
                            "valuation_status": UNVALUED, "accounting_status": "SHADOW_ONLY",
                            **({"quiet_window_declined": declined} if pocket == "KRW" and declined else {})})
        if kind is not None:
            out.append(_rule_resolution(f"toss:residual:KRW:{window}", kind, now=now,
                                        split=round_split(residual["KRW"],
                                                          _day_fills_krw(orders, since=previous["at"], until=now))))
    if not truncated:
        out.extend(_amendments(existing, orders))
    return out


def _amendments(existing: list[dict[str, Any]], orders: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Late evidence on earlier windows (H6d-min §7): an order that filled inside a window but was not in
    it, or a sale whose settlement date changed. Each is one keyed line, so re-reading adds nothing; the
    window itself is never edited. An amendment only ever withdraws a window from the evidence."""
    # Only windows that end after the earliest fill read can hold one of these orders; a settlement entry
    # sits in a window ending on its settlement day, after the fill. So the scan stays a week wide.
    fills = [_iso_ms((o.get("execution") or {}).get("filledAt")) for o in orders]
    earliest = None if None in fills else min(fills, default=None)      # an unreadable fill: scan them all
    windows = [r for r in existing if r.get("event") == EVENT_TOSS_WINDOW and not r.get("orders_truncated")
               and (earliest is None or (_iso_ms(r.get("window_end")) or 0) >= earliest)]
    out: list[dict[str, Any]] = []
    for order in orders:
        _basis, digest = order_identity(order)
        execution = order.get("execution") or {}
        effect = _pocket_effect(order)
        when = _iso_ms(execution.get("filledAt")) if execution.get("filledAt") else None
        side = str(order.get("side")).upper()
        for w in windows:
            start, end = _iso_ms(w["window_start"]), _iso_ms(w["window_end"])
            wkey = f"{w['window_start']}..{w['window_end']}"
            bounds = {"window_start": w["window_start"], "window_end": w["window_end"], "amends": w["source_event_key"]}
            inside = when is not None and start is not None and end is not None and start < when <= end
            if not digest:
                # An order that cannot be named cannot be matched against what the window saw: the
                # window is withdrawn from the evidence (fail-closed), without calling it a mismatch.
                if inside:
                    out.append({"event": EVENT_TOSS_AMENDED, "source": "toss", **bounds,
                                "source_event_key": f"toss:amend:{wkey}:identity_unstable", "kind": "identity_unstable"})
                continue
            if (effect is not None and when is not None and start is not None and end is not None
                    and start < when <= end and side in ("BUY", "SELL")
                    and not any(_same_order(digest, seen)
                                for seen in (w.get(f"{side.lower()}_orders") or {}).get(effect[0], []))):
                out.append({"event": EVENT_TOSS_AMENDED, "source": "toss", **bounds,
                            "source_event_key": f"toss:amend:{wkey}:late_fill:{digest[0]}", "kind": "late_fill",
                            "order": digest, "pocket": effect[0]})
            settled = str(execution.get("settlementDate") or "")
            for entry in w.get("settlements") or []:
                if _same_order(entry.get("order"), digest) and settled and entry.get("day") != settled:
                    out.append({"event": EVENT_TOSS_AMENDED, "source": "toss", **bounds,
                                "source_event_key": f"toss:amend:{wkey}:settlement_revised:{digest[0]}:{settled}",
                                "kind": "settlement_revised", "order": digest, "pocket": entry.get("pocket"),
                                "day": entry.get("day"), "revised_day": settled})
    return out


def _window_verdicts(rows: list[Mapping[str, Any]], epoch: int) -> list[dict[str, Any]]:
    """Each window of ``epoch`` with what it may count for, after amendments and resolutions."""
    amended: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        if row.get("event") == EVENT_TOSS_AMENDED:
            amended.setdefault(str(row.get("amends")), []).append(row)
    closed = resolutions(rows)
    out = []
    for w in rows:
        if w.get("event") != EVENT_TOSS_WINDOW or w.get("epoch") != epoch:
            continue
        key, span = w["source_event_key"], f"{w['window_start']}..{w['window_end']}"
        status = w.get("residual_status") or {}
        late = amended.get(key, [])
        unexplained: set[str] = set()
        problem = False
        for pocket in ("KRW", "USD"):
            active = ((w.get("buy_orders") or {}).get(pocket) or (w.get("sell_orders") or {}).get(pocket)
                      or any(s.get("pocket") == pocket for s in w.get("settlements") or [])
                      or not w.get("identity_stable"))
            resolution = closed.get(f"toss:residual:{pocket}:{span}")
            external = resolution is not None and resolution.get("kind") in TOSS_EXTERNAL_KINDS
            if active and status.get(pocket) == "OPEN":
                problem = True
                if not external:
                    unexplained.add(pocket)
            # A fill found later in a window whose change was already explained contradicts that window.
            if any(a.get("kind") == "late_fill" and a.get("pocket") == pocket for a in late) \
                    and status.get(pocket) == "CLEAR":
                unexplained.add(pocket)
                problem = True
        clean = (not w.get("orders_truncated") and not late and not w.get("exchange_pair")
                 and w.get("identity_stable") is True)
        out.append({"row": w, "unexplained": unexplained, "late": late, "problem": problem,
                    "clean": {p: clean and status.get(p) == "CLEAR" for p in ("KRW", "USD")},
                    "end_kst": _kst_date(str(w["window_end"]))})
    return out


def toss_evidence(rows: list[Mapping[str, Any]], *, epoch: int, today_kst: str) -> dict[str, Any]:
    """D-H6-7 from the windows of one epoch (H6d-min): buys and sells only from explained, untruncated,
    unamended windows, each order once; a settlement day only with :data:`SETTLEMENT_MIN_WINDOWS` windows
    on it and no residual, truncation or revision that day. A window closed as an external flow is left
    out — it never becomes trade evidence."""
    verdicts = _window_verdicts(rows, epoch)
    # MIXED recovers only on new evidence: a window that was unexplained — still open, or closed by Thomas
    # as an external flow — voids the evidence gathered up to its end. Closing it lifts the MIXED; it
    # does not bring the earlier evidence back.
    problems = [_iso_ms(v["row"]["window_end"]) or 0 for v in verdicts if v["problem"]]
    after = max(problems, default=0)
    counted = [v for v in verdicts if (_iso_ms(v["row"]["window_start"]) or 0) >= after]
    buys = [h for v in counted for p in ("KRW", "USD") if v["clean"][p]
            for h in (v["row"].get("buy_orders") or {}).get(p, [])]
    sells = [h for v in counted for p in ("KRW", "USD") if v["clean"][p]
             for h in (v["row"].get("sell_orders") or {}).get(p, [])]
    days: dict[str, dict[str, Any]] = {}
    for v in counted:
        w = v["row"]
        for entry in w.get("settlements") or []:
            day = days.setdefault(str(entry.get("day")), {"windows": set(), "spoiled": False})
            day["windows"].add(w["source_event_key"])
            if not v["clean"].get(str(entry.get("pocket")), False):
                day["spoiled"] = True
        for a in v["late"]:
            if a.get("kind") == "settlement_revised":
                days.setdefault(str(a.get("day")), {"windows": set(), "spoiled": False})["spoiled"] = True
    for v in verdicts:      # a residual, a truncated read or an exchange on the day spoils it too
        if v["end_kst"] in days and not all(v["clean"].values()):
            days[v["end_kst"]]["spoiled"] = True
    if after:               # and no day the problem window touched comes back
        for day in {v["end_kst"] for v in verdicts if v["problem"]}:
            if day in days:
                days[day]["spoiled"] = True
    clean_days = sorted(d for d, row in days.items()
                        if d < today_kst and len(row["windows"]) >= SETTLEMENT_MIN_WINDOWS and not row["spoiled"])
    unexplained = sum(len(v["unexplained"]) for v in verdicts)
    return {"buy_explained": _distinct_orders(buys), "sell_explained": _distinct_orders(sells), "clean_settlement_days": clean_days,
            "activity_unexplained": unexplained, "windows": len(verdicts),
            "problem_windows": sum(1 for v in verdicts if v["problem"])}


def _legacy_semantics(counters: Mapping[str, Any], today_kst: str) -> tuple[str, str]:
    clean = [d for d, row in (counters.get("settlement_days") or {}).items()
             if d < today_kst and row.get("windows", 0) >= SETTLEMENT_MIN_WINDOWS and not row.get("unexplained")]
    if counters.get("activity_unexplained"):
        return "MIXED", f"{counters['activity_unexplained']} window(s) with a fill or settlement left a residual"
    missing = [name for name, ok in (("a buy", counters.get("buy_explained")),
                                     ("a sell", counters.get("sell_explained")), ("a settlement day", clean)) if not ok]
    return ("PASS", "buy, sell and settlement explained") if not missing else ("WAITING", "needs " + ", ".join(missing))


def toss_semantics(rows: list[Mapping[str, Any]], state: Mapping[str, Any], *, today_kst: str) -> tuple[str, str]:
    """(PASS / WAITING / MIXED, why) for the current epoch.

    Epoch 0 is the pre-H6d model: the counters imported from the state file once (or, before that import,
    still on file) decide it, and any window recorded since can only make it MIXED. From epoch 1 the
    windows alone decide. MIXED is a fill or settlement the change did not match: it waits for Thomas's
    reading (a resolution of the residual, or a new epoch); it never passes."""
    epoch = toss_epoch(rows)
    evidence = toss_evidence(rows, epoch=epoch, today_kst=today_kst)
    if epoch == 0:
        legacy = _legacy(rows)
        if legacy:
            counters = legacy.get("toss_evidence")
        else:
            try:                               # not imported yet: read through the import's own check
                counters = _legacy_fields(state)[0]
            except ToolError:
                return "WAITING", "the pre-H6d counters on file are malformed; nothing was imported"
        verdict, why = _legacy_semantics(counters or {}, today_kst)
        if evidence["activity_unexplained"]:
            return "MIXED", f"{evidence['activity_unexplained']} pocket-window(s) with activity left a residual"
        if evidence["problem_windows"] and verdict == "PASS":
            return "WAITING", "a residual with activity was closed; the frozen counters are not new evidence"
        return verdict, why
    if evidence["activity_unexplained"]:
        return "MIXED", f"epoch {epoch}: {evidence['activity_unexplained']} pocket-window(s) left a residual"
    missing = [name for name, ok in (("a buy", evidence["buy_explained"]), ("a sell", evidence["sell_explained"]),
                                     ("a settlement day", evidence["clean_settlement_days"])) if not ok]
    return (("PASS", f"epoch {epoch}: buy, sell and settlement explained") if not missing
            else ("WAITING", f"epoch {epoch}: needs " + ", ".join(missing)))


# What the Toss check still waits for, as a closed list a door can carry (HOLDINGS_STATUS_READ_AND_AUTO_FLOWS R3,
# Thomas 2026-10-26). ``toss_semantics`` says the same in a sentence; nothing parses that sentence.
TOSS_NEEDS = ("buy", "sell", "settlement_day", "review")


def toss_needs(rows: list[Mapping[str, Any]], verdict: str, *, today_kst: str) -> list[str]:
    """[] once the Toss cash check passes; ``review`` while it is MIXED or waits on something no trade supplies
    (Thomas's reading of a residual, or a new epoch); otherwise the trade evidence the current epoch lacks."""
    if verdict == "PASS":
        return []
    if verdict == "MIXED":
        return ["review"]
    evidence = toss_evidence(rows, epoch=toss_epoch(rows), today_kst=today_kst)
    missing = [name for name, ok in (("buy", evidence["buy_explained"]), ("sell", evidence["sell_explained"]),
                                     ("settlement_day", evidence["clean_settlement_days"])) if not ok]
    return missing or ["review"]


# --- semantics epochs (H6d-min D4) -------------------------------------------------------------------------

# Fixed synthetic windows the reconciliation is run over; their outputs are the behaviour an epoch names.
_DIGEST_CASES: tuple[tuple[dict[str, float], dict[str, float], list[dict[str, Any]], float], ...] = (
    ({"KRW": 1_000_000.0, "USD": 0.0}, {"KRW": 900_000.0, "USD": 0.0},
     [{"orderId": "c1", "side": "BUY", "currency": "KRW",
       "execution": {"filledAmount": "100000", "commission": "0", "tax": "0", "filledAt": "2026-01-01T00:30:00Z"}}],
     1400.0),
    ({"KRW": 1_000_000.0, "USD": 0.0}, {"KRW": 1_098_000.0, "USD": 0.0},
     [{"orderId": "c2", "side": "SELL", "currency": "KRW",
       "execution": {"filledAmount": "100000", "commission": "1500", "tax": "500",
                     "filledAt": "2026-01-01T00:30:00Z", "settlementDate": "2026-01-01"}}],
     1400.0),
    ({"KRW": 1_000_000.0, "USD": 0.0}, {"KRW": 1_050_000.0, "USD": 0.0}, [], 1400.0),
    ({"KRW": 1_000_000.0, "USD": 0.0}, {"KRW": 860_000.0, "USD": 100.0}, [], 1400.0),
)


def _function_shape(function: Callable[..., Any]) -> str:
    tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr) \
                and isinstance(getattr(body[0], "value", None), ast.Constant) and isinstance(body[0].value.value, str):
            node.body = body[1:] or [ast.Pass()]
    return ast.dump(tree, annotate_fields=False)


def reconciliation_fingerprint() -> str:
    """The shape of the reconciliation code (comments and docstrings aside). Recorded beside the digest;
    supporting evidence only — a changed shape with an unchanged output is no behaviour change."""
    shapes = "|".join(_function_shape(f) for f in (reconcile_window, _pocket_effect, order_identity, _window_verdicts,
                                                   toss_evidence))
    return hashlib.sha256(shapes.encode("utf-8")).hexdigest()[:16]


def reconciliation_digest() -> str:
    """What the reconciliation does to :data:`_DIGEST_CASES`, hashed. An epoch needs this to differ from
    the last epoch's: an unrelated code change leaves it as it was."""
    outputs = []
    for previous, current, orders, rate in _DIGEST_CASES:
        result = reconcile_window(previous, current, orders, since="2026-01-01T00:00:00Z",
                                  until="2026-01-01T01:00:00Z", rate=rate)
        result["residual"] = {p: round(v, 6) for p, v in result["residual"].items()}
        outputs.append(result)
    window = {"event": EVENT_TOSS_WINDOW, "epoch": 1, "source_event_key": "toss:window:a..b",
              "window_start": "2026-01-01T00:00:00Z", "window_end": "2026-01-01T01:00:00Z",
              "buy_orders": outputs[0]["buy"], "sell_orders": outputs[1]["sell"],
              "settlements": outputs[1]["settlements"], "residual_status": {"KRW": "CLEAR", "USD": "CLEAR"},
              "orders_truncated": False, "exchange_pair": False}
    outputs.append(toss_evidence([{"event": EVENT_EPOCH}, window], epoch=1, today_kst="2026-01-02"))
    return hashlib.sha256(json.dumps(outputs, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]


CHANGE_REF = re.compile(r"^(#\d+|[0-9a-f]{7,40})$")


def start_semantics_epoch(state_dir: Path, *, requested_by: str, reason: str, change_ref: str, confirm: str,
                          interactive: bool, now: str | None = None) -> dict[str, Any]:
    """Start a new Toss semantics epoch (H6d-min D4): the earlier windows, their MIXED and every
    unresolved exception stay as they are; the new epoch starts WAITING and gathers its own evidence.

    Needs an interactive terminal, ``--by``, ``--reason``, a change reference (a PR number or a commit),
    and the first 8 characters of the reconciliation digest typed back. The digest must differ from the
    last epoch's — a change that leaves the reconciliation's output as it was is not a reason. The one
    exception is the first epoch, the transition from the pre-H6d counters (allowed once). That the
    regression tests passed is not something this process can see: the change reference names the merged
    change whose required checks did."""
    if not interactive:
        raise ToolError(EPOCH_REFUSED, "an epoch starts at an interactive terminal")
    if not (requested_by or "").strip() or not (reason or "").strip():
        raise ToolError(EPOCH_REFUSED, "an epoch needs --by and --reason")
    if not CHANGE_REF.match((change_ref or "").strip()):
        raise ToolError(EPOCH_REFUSED, "--change-ref names the reconciliation change: #<PR> or a commit")
    fingerprint, digest = reconciliation_fingerprint(), reconciliation_digest()
    if (confirm or "").strip() != digest[:8]:
        raise ToolError(EPOCH_REFUSED, "the typed confirmation does not match the reconciliation digest")
    now = now or timeutil.utc_now_iso()

    def rows(existing: list[dict[str, Any]], state: dict[str, Any]) -> list[Mapping[str, Any]]:
        epochs = [r for r in existing if r.get("event") == EVENT_EPOCH]
        if epochs and epochs[-1].get("reconciliation_digest") == digest:
            raise ToolError(EPOCH_REFUSED, "the reconciliation's output is unchanged since the last epoch: "
                                           "no behaviour change to start one on")
        today = (timeutil.parse_iso(now) + timedelta(hours=9)).date().isoformat()
        status, _why = toss_semantics(existing, state, today_kst=today)
        number = len(epochs) + 1
        return [{"event": EVENT_EPOCH, "source": "ledger", "source_event_key": f"toss_epoch:{number}",
                 "epoch": number, "previous_epoch": number - 1, "previous_epoch_status": status,
                 "legacy_transition": not epochs, "reason": reason.strip(), "requested_by": requested_by.strip(),
                 "change_ref": change_ref.strip(), "reconciliation_fingerprint": fingerprint,
                 "reconciliation_digest": digest, "timestamp": now,
                 "open_exceptions_carried": len(open_exceptions(existing))}]

    written = [r for r in _commit(state_dir, now=now, rows=rows) if r.get("event") == EVENT_EPOCH]
    if not written:
        raise ToolError(EPOCH_REFUSED, "another epoch with this number was started at the same time")
    return written[0]


# --- C7 PR-B: history completeness (Thomas 2026-10-17: D2 = option C) --------------------------------------
# HOLDINGS_LEDGER_CHECKPOINT_V0.1.md §2: a separate readiness key from ``ledger_state_consistency``. That one
# asks whether today's ledger continues what the state last saw; this one asks whether the history since the
# cutover is whole. Under option C the cutover is moved to after the checkpoint exists, so "whole" is shown by
# three things together, never by any one alone:
# - the checkpoint was first observed at or before the cutover (``observed_at`` ≤ cutover), so every write
#   since the cutover was checked against it (PR-A), and no detection stands;
# - Toss: the observed windows and named skips tile the stretch since the cutover (``CONTINUOUS``);
# - Binance: every history source began reading at or before the cutover, has read past it, and has no named
#   skip (a cursor that fell behind the query depth) ending after it.
# Anything else is UNCHECKED, with the reason. It is PASS or UNCHECKED, never upgraded by time or approval.
HISTORY_COMPLETE = "COMPLETE"
HISTORY_INCOMPLETE = "INCOMPLETE"


def _binance_coverage(rows: list[Mapping[str, Any]], state: Mapping[str, Any], cutover_ms: int) -> dict[str, str]:
    from .binance_wallet import FLOW_SOURCES

    sources = state.get("sources") if isinstance(state.get("sources"), Mapping) else {}
    late_skips = {str(r.get("flow_source")) for r in rows if r.get("event") == EVENT_FLOW_SKIPPED
                  and (_iso_ms(r.get("window_end")) or 0) > cutover_ms}
    out: dict[str, str] = {}
    for name in FLOW_SOURCES:
        info = sources.get(name) if isinstance(sources.get(name), Mapping) else {}
        began, cursor = info.get("covered_from_ms"), info.get("cursor_ms")
        whole = (isinstance(began, int) and began <= cutover_ms and isinstance(cursor, int) and cursor >= cutover_ms
                 and name not in late_skips)
        out[name] = HISTORY_COMPLETE if whole else HISTORY_INCOMPLETE
    return out


def history_completeness(rows: list[Mapping[str, Any]], state: Mapping[str, Any], *, now: str) -> tuple[str, dict[str, Any]]:
    """``(PASS | UNCHECKED, detail)``. The detail names the first reason it is not PASS; no amount, no key."""
    detail: dict[str, Any] = {"reason": None, "toss": None, "binance": None}
    at = cutover_at(rows)
    cutover_ms = _iso_ms(at) if at else None
    checkpoint = state.get(CHECKPOINT_KEY)
    if cutover_ms is None:
        detail["reason"] = "no_cutover"
    elif not isinstance(checkpoint, Mapping):
        detail["reason"] = "no_checkpoint"                                   # D2: absent reads UNCHECKED
    elif state.get(DETECTION_MARKER_KEY) is not None or ledger_problem(rows, state):
        detail["reason"] = "ledger_state_inconsistent"
    elif (_iso_ms(checkpoint.get("observed_at")) is None
          or _iso_ms(checkpoint.get("observed_at")) > cutover_ms):
        detail["reason"] = "checkpoint_after_cutover"                        # history before it: UNVERIFIED
    if detail["reason"] is not None:
        return UNCHECKED, detail
    toss = toss_continuity(rows, now=now)
    detail["toss"] = toss["status"]
    binance = _binance_coverage(rows, state, cutover_ms)
    detail["binance"] = HISTORY_COMPLETE if all(v == HISTORY_COMPLETE for v in binance.values()) else HISTORY_INCOMPLETE
    if toss["status"] != TOSS_CONTINUOUS:
        detail["reason"] = f"toss_{toss['status'].lower()}"
    elif detail["binance"] != HISTORY_COMPLETE:
        detail["reason"] = "binance_incomplete"
    return (PASS if detail["reason"] is None else UNCHECKED), detail


# --- readiness and what is told ---------------------------------------------------------------------------

def readiness(state_dir: Path, *, now: str, current: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Whether the evidence H6's next step needs is in (Thomas 2026-10-09). It enables nothing: the next
    step (NAV/unit in shadow) waits for Thomas's approval.

    History comes from the ledger; health comes from this fire. ``current`` is what the calling fire saw
    (``binance``: every history read; ``toss``: the Toss pass ended; ``freshness``/``coherence``: the
    combined NAV's own checks). Without it — any call outside a fire — the access, schema, transfer and
    health checks are UNCHECKED, so a past PASS never stands for the present (H6d-min §11)."""
    checks: dict[str, str] = {}
    # The state first, the ledger second (C7 §3): a write between the two reads can only lengthen the
    # ledger past the checkpoint read, never leave the checkpoint ahead of the ledger read.
    try:
        state = load_state(state_dir)
        state_ok = True
    except ToolError:
        state, state_ok = {"sources": {}}, False
    try:
        rows = verify(state_dir)
        checks["ledger_chain"] = PASS
    except ToolError:
        rows, checks["ledger_chain"] = [], FAIL
    if state_ok and checks["ledger_chain"] == PASS and legacy_import_refusal(rows, state):
        state_ok = False                       # a pre-H6d cache the import refuses is not a consistent state
    if state_ok and (state.get(DETECTION_MARKER_KEY) is not None or ledger_problem(rows, state)):
        state_ok = False                       # PR-A: a detected mixed restore is not a consistent state
    boundary = cutover_at(rows)
    checks["ledger_state_consistency"] = (PASS if state_ok and checks["ledger_chain"] == PASS
                                          and (state.get("cutover_at") or None) == boundary else FAIL)
    history, history_detail = ((UNCHECKED, {"reason": "ledger_unreadable", "toss": None, "binance": None})
                               if not (state_ok and checks["ledger_chain"] == PASS)
                               else history_completeness(rows, state, now=now))
    checks["history_completeness"] = history
    first_fire, dates = shadow_record(rows)
    checks["cutover_boundary"] = PASS if boundary and first_fire else WAITING
    sources = state.get("sources") or {}
    access = [info.get("access") for info in sources.values()]
    schemas = [info.get("schema") for info in sources.values()]
    last = state.get("last_binance_pass") or {}
    fired = current is not None and last.get("at") == now
    if not fired:
        checks["binance_access"] = checks["binance_real_schema"] = checks["internal_transfer_guard"] = UNCHECKED
    else:
        checks["binance_access"] = (PASS if current.get("binance") and access and all(a == PASS for a in access)
                                    and not last.get("errors") else FAIL)
        checks["binance_real_schema"] = (FAIL if SCHEMA_MISMATCH in schemas or last.get("malformed_new")
                                         else PASS if SCHEMA_VERIFIED in schemas else WAITING)
        checks["internal_transfer_guard"] = (PASS if all(sources.get(n, {}).get("access") == PASS
                                                         and n not in (last.get("errors") or {})
                                                         for n in CRITICAL_SOURCES) else FAIL)
    checks["current_health"] = (UNCHECKED if current is None
                                else PASS if fired and all(current.get(k) for k in ("binance", "toss", "freshness",
                                                                                      "coherence")) else FAIL)
    today_kst = (timeutil.parse_iso(now) + timedelta(hours=9)).date().isoformat()
    toss, toss_why = toss_semantics(rows, state, today_kst=today_kst)
    checks["toss_cash_semantics"] = PASS if toss == "PASS" else FAIL if toss == "MIXED" else WAITING
    checks["cross_source_dedupe"] = PASS
    exceptions = open_exceptions(rows)
    checks["post_cutover_exceptions"] = PASS if not exceptions else FAIL
    # Seven days since the cutover, and a verified fire on seven KST dates: elapsed time alone is not a
    # shadow that worked. One fire a date, not every hour, so a passing outage does not block it forever.
    days = 0.0
    if boundary:
        days = (timeutil.parse_iso(now).timestamp() * 1000 - (_iso_ms(boundary) or 0)) / 86_400_000
    checks["shadow_observation"] = PASS if days >= SHADOW_MIN_DAYS and len(dates) >= SHADOW_MIN_DAYS else RUNNING
    ready = all(value == PASS for value in checks.values())
    return {"checks": checks, "toss_note": toss_why, "toss_epoch": toss_epoch(rows),
            "toss_needs": toss_needs(rows, toss, today_kst=today_kst), "shadow_days": round(days, 1),
            "shadow_success_dates": len(dates), "exceptions": len(exceptions), "ready": ready,
            "open_exception_keys": sorted(exceptions),
            # R-3 (가-수정): ``absent`` and ``observed`` both mean the history before the checkpoint is
            # UNVERIFIED; a consistency PASS beside them is not a verified restore (PR-B's check).
            "checkpoint": checkpoint_basis(state),
            # PR-B: the history since the cutover is VERIFIED only when history_completeness PASSes; the basis
            # itself stays "observed" — the checkpoint never vouches for what came before it.
            "checkpoint_history": "VERIFIED" if history == PASS else HISTORY_UNVERIFIED,
            "history": history_detail,
            "next_step": "H6b-shadow (NAV per unit computed beside the board, no verdict)"}


def _key_digest(key: str) -> str:
    return hashlib.sha256(f"exception|{key}".encode("utf-8")).hexdigest()[:16]


def update_readiness(state_dir: Path, *, now: str, current: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """:func:`readiness`, with its edges kept under the state lock.

    Readiness: each false->true starts a new epoch, and a message is owed once per epoch. Exceptions
    (H6d-min §10): each open exception is told once, by its key's digest — a new one after another was
    resolved is told even though the count did not grow. The untold digests are pinned under a token;
    :func:`mark_exceptions_told` records exactly those once the message was delivered."""
    ready = readiness(state_dir, now=now, current=current)
    keys = {_key_digest(k) for k in ready.pop("open_exception_keys", ())}
    with _state_lock(state_dir):
        state = load_state(state_dir)
        record = state.setdefault("readiness", {"epoch": 0, "ready": False, "told_epoch": 0})
        if ready["ready"] and not record.get("ready"):
            record["epoch"] = int(record.get("epoch") or 0) + 1
        record["ready"] = ready["ready"]
        owed = state.setdefault("exceptions", {})
        if "told_keys" not in owed:
            # The pre-H6d record kept a count. Told at least as many as are open now: take them as told;
            # fewer: which ones is unknown, so all are told again (a repeat, never a miss).
            owed["told_keys"] = sorted(keys) if int(owed.get("told_count") or 0) >= len(keys) > 0 else []
        untold = sorted(keys - set(owed["told_keys"]))
        token = None
        if untold:
            token = hashlib.sha256("|".join(untold).encode("utf-8")).hexdigest()[:16]
            pending = owed.setdefault("pending", {})
            pending[token] = untold
            for stale in list(pending)[:-8]:      # a few undelivered sets at most
                pending.pop(stale, None)
        owed["count"] = len(keys)
        _save_state(state_dir, state)
    ready["epoch"] = record["epoch"]
    ready["told"] = record.get("told_epoch") == record["epoch"]
    ready["exceptions_untold"] = bool(untold)
    ready["exceptions_new"] = len(untold)
    ready["exceptions_token"] = token
    return ready


def mark_readiness_told(state_dir: Path, *, now: str) -> None:
    with _state_lock(state_dir):
        state = load_state(state_dir)
        record = state.setdefault("readiness", {"epoch": 0, "ready": False, "told_epoch": 0})
        record["told_epoch"], record["told_at"] = record.get("epoch", 0), now
        _save_state(state_dir, state)


def mark_exceptions_told(state_dir: Path, *, token: str | None, now: str) -> None:
    """Record the exceptions one delivered message named. Others found meanwhile stay untold."""
    with _state_lock(state_dir):
        state = load_state(state_dir)
        owed = state.setdefault("exceptions", {})
        keys = (owed.get("pending") or {}).pop(str(token), None)
        if keys:
            owed["told_keys"] = sorted(set(owed.get("told_keys") or []) | set(keys))
            owed["told_at"] = now
        _save_state(state_dir, state)


def rule_flows_untold(state_dir: Path) -> tuple[str | None, dict[str, int]]:
    """The Toss flows the rule (:data:`RULE_IDS`) closed and Thomas was not yet told of (F2: direction and count only).

    A rule resolution Thomas has since re-closed is no longer the rule's and is not told. The untold set is
    pinned under a token, like the exceptions' (:func:`mark_rule_flows_told` records it once delivered), so a
    flow found while the message was in flight is still told."""
    rows = verify(state_dir)
    current = resolutions(rows)
    ruled = {_key_digest(key): line["kind"] for key, line in current.items()
             if line.get("requested_by") in RULE_IDS}
    with _state_lock(state_dir):
        state = load_state(state_dir)
        owed = state.setdefault("rule_flows", {})
        untold = sorted(set(ruled) - set(owed.get("told_keys") or []))
        if not untold:
            return None, {}
        token = hashlib.sha256("|".join(untold).encode("utf-8")).hexdigest()[:16]
        owed.setdefault("pending", {})[token] = untold
        _save_state(state_dir, state)
    counts = {"deposit": 0, "withdrawal": 0}
    for digest in untold:
        counts[ruled[digest]] = counts.get(ruled[digest], 0) + 1
    return token, counts


def mark_rule_flows_told(state_dir: Path, *, token: str | None, now: str) -> None:
    """Record the rule flows one delivered message named."""
    with _state_lock(state_dir):
        state = load_state(state_dir)
        owed = state.setdefault("rule_flows", {})
        keys = (owed.get("pending") or {}).pop(str(token), None)
        if keys:
            owed["told_keys"] = sorted(set(owed.get("told_keys") or []) | set(keys))
            owed["told_at"] = now
        _save_state(state_dir, state)


def summary(state_dir: Path) -> dict[str, Any]:
    """What may leave the process: counts and statuses, no asset, no amount, no key."""
    rows = verify(state_dir)
    state = load_state(state_dir)
    effective = effective_status(rows)
    return {
        "mode": ACCOUNTING_MODE,
        "events": len(rows),
        "external_flows": sum(1 for r in rows if r.get("event") == EVENT_FLOW),
        "internal_transfers": sum(1 for r in rows if r.get("event") == EVENT_INTERNAL),
        "held": sum(1 for status in effective.values() if status == HELD),
        "pre_cutover": sum(1 for r in rows if r.get("cutover_phase") == PRE_CUTOVER),
        "toss_residuals": sum(1 for r in rows if r.get("event") == EVENT_TOSS_RESIDUAL),
        "toss_windows": sum(1 for r in rows if r.get("event") == EVENT_TOSS_WINDOW),
        "resolved": sum(1 for r in rows if r.get("event") == EVENT_RESOLVED),
        "toss_epoch": toss_epoch(rows),
        "sources": {name: {"access": info.get("access"), "schema": info.get("schema")}
                    for name, info in sorted((state.get("sources") or {}).items())},
    }


# --- C7 PR-D: BLOCKED — a ledger or state every writer refuses, told on its own path ----------------------
# HOLDINGS_LEDGER_CHECKPOINT_V0.1.md §6 (Thomas 2026-10-11: D1 direction, PR-D approved for a review PR).
# Every other H6 message is computed from a readable ledger, so a broken one used to send nothing (C7-D).
# This judgement reads the ledger and the state itself, read-only and outside every writer, so it runs
# whatever the holdings fire did before it: an early return, a failed Toss read. It writes nothing but the
# told file below, and that only after a delivery, under the state lock. The lock codes (STATE_LOCKED,
# LEDGER_LOCKED) are transient and never BLOCKED; the judgement takes no lock, so it never meets one.
BLOCKED_TOLD_FILENAME = "holdings_cash_flow_blocked_told.json"
BLOCKED_KEY_STATE = "v1:state"          # PR-D: every commit is refused, so the state file stays as it was
BLOCKED_KEY_CHECKPOINT = "v2:ckpt"      # a checkpoint refusal keys on the checkpoint it stands on (§6)
# The marker PR-A's detect-only code left in the state (§3, readiness note R-2; ``reason`` names the
# detector). Since PR-C nothing writes it: one that stands refuses every write until an adoption clears it.
DETECTION_MARKER_KEY = "ledger_checkpoint_mismatch"
BLOCKED_TOKEN_TOLD = "blocked"
BLOCKED_TOKEN_RESOLVED = "resolved"


def blocked_told_path(state_dir: Path) -> Path:
    return state_dir / BLOCKED_TOLD_FILENAME


def _state_fingerprint(state_dir: Path) -> str:
    """The state file's bytes as a short digest, or why there is none. LOCAL_ONLY: it lives in the told
    file and in the token, never in a message, a snapshot or a status line."""
    try:
        data = state_path(state_dir).read_bytes()
    except FileNotFoundError:
        return "missing"
    except OSError:
        return "unreadable"
    return hashlib.sha256(data).hexdigest()[:16]


def _blocked(state_dir: Path, reason_code: str, category: str) -> dict[str, Any]:
    key = f"{BLOCKED_KEY_STATE}:{category}:{_state_fingerprint(state_dir)}"
    return {"blocked": True, "reason_code": reason_code, "category": category, "key": key}


def _refusal_blocked(rows: list[Mapping[str, Any]], state: Mapping[str, Any]) -> dict[str, Any]:
    """C7 PR-C: the writes refuse on the checkpoint (a mismatch, or a PR-A marker still standing). Keyed on
    the checkpoint the refusal stands on — the state's, else the marker's, else the ledger's end — never on
    the state's bytes: readiness and the told records still save the state while the writes refuse, so a
    state digest would be a new failure every fire. ``refused`` in the key keeps it apart from PR-A's
    detect-only key, so a detection told before PR-C is told once more, as the refusal it now is."""
    frozen = _frozen_checkpoint(state)
    lines, digest = frozen["lines"], frozen["sha256"]
    if lines is None and not digest and rows:
        lines, digest = len(rows), rows[-1].get("sha256")
    if isinstance(lines, int) and lines >= 0 and isinstance(digest, str) and digest:
        key = f"{BLOCKED_KEY_CHECKPOINT}:refused:{lines}:{digest[:16]}"
    else:
        key = f"{BLOCKED_KEY_CHECKPOINT}:refused:malformed"
    return {"blocked": True, "reason_code": LEDGER_TAMPERED, "category": "checkpoint", "key": key}


def blocked_state(state_dir: Path) -> dict[str, Any]:
    """Whether the ledger or its state is in a condition every writer refuses (C7 §6). Read-only.

    ``{"blocked": False, "verified_since"}``, or ``{"blocked": True, "reason_code", "category", "key"}``
    where the category is one of ``verify`` (the chain), ``load_state`` (the cache), ``legacy`` (the
    pending pre-H6d import), ``checkpoint`` (PR-C: the writes refuse on the checkpoint, or on a PR-A marker
    still standing; an adoption whose state save was lost is not BLOCKED, the next write finishes it) or
    ``check`` (this judgement itself failed: a
    check that cannot run never reads as fine). No ledger and no state is a lane that never ran, not a
    failure. A missing state beside a readable ledger is the C7-11 recovery path, not BLOCKED.

    ``verified_since`` is the ``at`` of the first line of a ledger whose chain was re-derived here, or None
    when there was nothing to verify (no ledger, or an empty one). Not BLOCKED is not the same as verified:
    a missing ledger verifies vacuously, so only ``verified_since`` can clear a told failure."""
    if not (ledger_path(state_dir).exists() or state_path(state_dir).exists()):
        return {"blocked": False, "verified_since": None}
    try:
        try:
            rows = verify(state_dir)
        except ToolError as exc:
            return _blocked(state_dir, exc.reason_code, "verify")
        except OSError as exc:
            return _blocked(state_dir, type(exc).__name__, "verify")
        try:
            state = load_state(state_dir)
        except ToolError as exc:
            return _blocked(state_dir, exc.reason_code, "load_state")
        refusal = legacy_import_refusal(rows, state)
        if refusal:
            return _blocked(state_dir, refusal, "legacy")
        if standing_problem(rows, state) is not None and _unsaved_adoption(rows, state) is None:
            return _refusal_blocked(rows, state)
    except Exception as exc:  # noqa: BLE001 — a judgement that cannot finish is BLOCKED, never fine
        return _blocked(state_dir, type(exc).__name__, "check")
    return {"blocked": False, "verified_since": rows[0].get("at") if rows else None}


def read_blocked_told(state_dir: Path) -> tuple[dict[str, Any], str | None]:
    """The told record — ``key``, ``told_at``, ``resolved_told_at`` — and why it could not be read. An
    unreadable record reads as never told: the alert repeats rather than goes missing."""
    path = blocked_told_path(state_dir)
    if not path.exists():
        return {}, None
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {}, f"told record unreadable ({type(exc).__name__})"
    if not isinstance(body, dict):
        return {}, "told record unreadable (not an object)"
    return {name: body.get(name) for name in ("key", "told_at", "resolved_told_at")}, None


def blocked_alert(state_dir: Path, *, now: str) -> dict[str, Any]:
    """The BLOCKED edge since the last DELIVERED message: ``{"alert": (token, text) | None, "note"}``.

    One message when a failure appears (its key), none while that told failure stands, one when it clears,
    and one again if a failure appears after the clearing was told — the transitions are explicit in the
    told record, so a recurrence with the same state bytes is still told. The text names the reason code
    and the check, never the key, an amount, an asset or an account."""
    judgement = blocked_state(state_dir)
    told, note = read_blocked_told(state_dir)
    standing = bool(told.get("key")) and bool(told.get("told_at")) and not told.get("resolved_told_at")
    if judgement["blocked"]:
        if standing and told.get("key") == judgement["key"]:
            return {"alert": None, "note": note}
        if judgement["category"] == "checkpoint":
            # C7 PR-C: the writes refuse on the checkpoint, and only an adoption reopens them (D3).
            what = ("원장과 상태가 서로 다른 시점에서 복원된 것으로 보여 원장·상태 쓰기가 거부되었습니다(아무것도 "
                    "쓰지 않음). H6 기록과 예외 알림이 멈춰 있고, Thomas 터미널의 원장 채택(두 단계)으로만 다시 "
                    "열립니다")
        else:
            what = "원장·상태 쓰기가 거부되어 H6 기록과 예외 알림이 멈춰 있습니다"
        text = (f"[보유 자산] H6 현금흐름 원장 BLOCKED — {judgement['reason_code']} ({judgement['category']}), "
                f"{now} 확인. {what}. "
                "금액과 계정은 담지 않습니다. Thomas 터미널에서 원인을 확인해 주세요.")
        return {"alert": (f"{BLOCKED_TOKEN_TOLD}|{judgement['key']}", text), "note": note}
    if standing:
        withheld = _clearing_withheld(judgement, told)
        if withheld:
            return {"alert": None, "note": "; ".join(part for part in (note, withheld) if part)}
        text = f"[보유 자산] H6 현금흐름 원장 BLOCKED 해소 — {now} 기준 원장과 상태 검증이 다시 통과합니다."
        return {"alert": (f"{BLOCKED_TOKEN_RESOLVED}|{told['key']}", text), "note": note}
    return {"alert": None, "note": note}


def _clearing_withheld(judgement: Mapping[str, Any], told: Mapping[str, Any]) -> str | None:
    """Why a told failure that no longer reads BLOCKED is not told as cleared, or None. Nothing to verify
    is not a verification: a ledger that is gone, or one that began after the failure was told (refresh
    starts a new chain where the old one vanished), is not the ledger that failed, repaired. The told
    record stays standing, so nothing is sent until the ledger that was told verifies again. A ledger
    restored or repaired in place keeps its first line, so it clears; one whose first line is gone does
    not (C7 PR-A's checkpoint is what tells a truncated ledger from a repaired one)."""
    since = _iso_ms(judgement.get("verified_since"))
    if since is None:
        return "clearing withheld (nothing verified)"
    told_at = _iso_ms(told.get("told_at"))
    if told_at is None or since > told_at:
        return "clearing withheld (the ledger began after the failure was told)"
    return None


def mark_blocked_told(state_dir: Path, *, token: str, now: str) -> bool:
    """Record a delivered BLOCKED or clearing message — called only after delivery. Writes the told file
    alone (a tmp file of its own → replace); a write that fails raises, so the caller never counts it as
    recorded and the next fire offers the message again.

    Returns False, writing nothing, for a token it does not know and for a stale one: a record already
    written by a later fire (an older ``now`` never overwrites a newer record), or a clearing of a key
    that is not the standing one. Holdings fires are serial in the deployment (one maintenance lane); two
    overlap only when an operator ticks by hand meanwhile. Then a stale token loses here and the next fire
    re-judges, so the cost is a duplicate message, never a failure recorded as told when it was not.

    The read, the check and the replace are one step under the state lock (the existing sidecar every
    state write takes first), so a stale writer cannot pass its check and replace after a newer one. A
    lock that cannot be had raises STATE_LOCKED like any other failed write: not recorded, offered again.
    The judgement above takes no lock; only this record does."""
    kind, _, key = str(token).partition("|")
    if kind not in (BLOCKED_TOKEN_TOLD, BLOCKED_TOKEN_RESOLVED) or not key:
        return False
    with _state_lock(state_dir):
        return _write_blocked_told(state_dir, kind=kind, key=key, now=now)


def _write_blocked_told(state_dir: Path, *, kind: str, key: str, now: str) -> bool:
    """The body of :func:`mark_blocked_told`, under its lock."""
    told, _note = read_blocked_told(state_dir)
    at = _iso_ms(now)
    latest = [ms for ms in (_iso_ms(told.get("told_at")), _iso_ms(told.get("resolved_told_at"))) if ms is not None]
    if at is None or (latest and at < max(latest)):
        return False
    if kind == BLOCKED_TOKEN_TOLD:
        body = {"key": key, "told_at": now, "resolved_told_at": None}
    else:
        if told.get("key") != key or not told.get("told_at") or told.get("resolved_told_at"):
            return False
        body = {"key": key, "told_at": told["told_at"], "resolved_told_at": now}
    path = blocked_told_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(json.dumps({"record_version": 1, **body}, sort_keys=True), encoding="utf-8")
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)
    return True

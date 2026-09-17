"""PR2b — the one gate every order that opens exposure passes immediately before it is sent.

Thomas's directive: every real order passes ONE pre-order risk gate immediately before submission,
the gate re-verifies everything even when earlier gates passed, and its result is an immutable
snapshot the order references by hash. Three decisions shape it (2026-09-16/17): the "approved
profile" is a composite of the records that already authorize trading, not a new record (17);
snapshots persist to one append-only store per venue (19); the signed testnet entry passes the
same gate (20).

What this module is:

- **the gate** (:func:`evaluate_pre_order_gate`) — pure. A door hands it every check it re-derived
  from the facts it read (the live guard's, the entry decision's, the probe's refusals, the testnet
  guard's), and the gate adds its own: the intent's identity follows from its fields, its lineage is
  complete, and the approved profile is whole. The result is sealed: ``approved`` only when every
  check passed, and one self-hash over all of it, the intent's fingerprint included.
- **the binding** (:func:`verify_and_persist`) — what ``live_execution.submit_and_reconcile`` runs
  before any order that is not reduce-only: the snapshot is approved and intact, it names THIS
  intent, the intent names it back, and it has been written to its venue's store. Anything else,
  and nothing is sent.
- **the store** (:class:`PreOrderSnapshotStore`) — an append-only JSONL per venue, behind that
  venue's switch, fsynced before the order leaves. Only orders about to be sent are written, so a
  row means "an order was about to leave under these facts". A crash between the write and the
  send leaves a row with no order — never an order with no row.

What it is not: a second guard. The checks it records are the doors' own, re-run on the facts the
door read. Closes, brackets and cancels reduce risk and never pass through here; a gate that could
refuse a close could trap a position.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from runtime.read_only_kernel import integrity
from runtime.read_only_kernel.schema_validation import RuntimeSchemaError

from .. import safety_gate, timeutil
from ..errors import ToolError
from ..filelock import locked
from ..paths import repo_root as _repo_root
from ..safety_gate import Authorization
from ..schema_cache import validate_against_schema
from .execution_stage import PURPOSE_AUTONOMOUS, PURPOSE_PROBE, PURPOSE_TESTNET
from .live_order import enrich_order_identity
from .state import VENUE_MAINNET, venue_state_dir

GATE_ID = "pre_order_gate.v1"
SNAPSHOT_VERSION = "pre_order_risk_snapshot.v0.1"
SNAPSHOT_SCHEMA_FILE = "pre_order_risk_snapshot.v0.1.schema.json"
SNAPSHOT_FILENAME = "pre_order_risk_snapshots.jsonl"
INTENT_FINGERPRINT_VERSION = "pre_order_intent.v1"

RISK_SNAPSHOT_MISSING = "RISK_SNAPSHOT_MISSING"
RISK_SNAPSHOT_NOT_APPROVED = "RISK_SNAPSHOT_NOT_APPROVED"
RISK_SNAPSHOT_TAMPERED = "RISK_SNAPSHOT_TAMPERED"
RISK_SNAPSHOT_INTENT_MISMATCH = "RISK_SNAPSHOT_INTENT_MISMATCH"
RISK_SNAPSHOT_VENUE_MISMATCH = "RISK_SNAPSHOT_VENUE_MISMATCH"
RISK_SNAPSHOT_NO_STORE = "RISK_SNAPSHOT_NO_STORE"
RISK_SNAPSHOT_STORE_UNREADABLE = "RISK_SNAPSHOT_STORE_UNREADABLE"
RISK_SNAPSHOT_STORE_TAMPERED = "RISK_SNAPSHOT_STORE_TAMPERED"
RISK_SNAPSHOT_ID_CONFLICT = "RISK_SNAPSHOT_ID_CONFLICT"
RISK_SNAPSHOT_INVALID = "RISK_SNAPSHOT_INVALID"

# The gate's own checks, added to whatever the door re-derived.
CHECK_DOOR_CHECKS = "door_checks_present"
CHECK_OPENS_EXPOSURE = "intent_opens_exposure"
CHECK_INTENT_IDENTITY = "intent_identity"
CHECK_LINEAGE = "lineage_complete"
CHECK_PROFILE = "approved_profile_complete"

# What the snapshot binds of the intent: its identity, its material terms, and the lineage it will
# be judged by. A change to any of them after the gate is a different order.
INTENT_BOUND_FIELDS = (
    "idempotency_key", "client_order_id", "order_intent_id",
    "symbol", "direction", "side", "order_type_exchange",
    "quantity", "order_notional_usdt", "reduce_only", "connectivity_test",
    "entry_price", "stop_loss", "take_profit",
    "strategy_id", "candidate_id", "strategy_rule_hash", "strategy_generation_id",
    "position_id", "candle_time", "timeframe",
)

# The identifiers each purpose's lineage must name before an order may leave.
LINEAGE_FIELDS: dict[str, tuple[str, ...]] = {
    PURPOSE_AUTONOMOUS: ("strategy_id", "candidate_id", "strategy_rule_hash",
                         "strategy_generation_id", "timeframe", "candle_time", "order_intent_id"),
    PURPOSE_PROBE: ("strategy_id", "batch_id", "cell_index", "order_intent_id"),
    PURPOSE_TESTNET: ("strategy_id", "cycle_id", "order_intent_id"),
}

# Which authority each purpose's profile names, beside the stage record (decision 17).
AUTHORITY_LIVE_ARM = "live_arm"
AUTHORITY_PROBE_PLAN = "probe_plan"
AUTHORITY_TESTNET_CAPS = "testnet_caps"
_AUTHORITY_FOR = {
    PURPOSE_AUTONOMOUS: AUTHORITY_LIVE_ARM,
    PURPOSE_PROBE: AUTHORITY_PROBE_PLAN,
    PURPOSE_TESTNET: AUTHORITY_TESTNET_CAPS,
}
RISK_LIMITS_DEFAULT_SOURCE = "default"


def check(check_id: str, ok: bool, detail: Any = None) -> dict[str, Any]:
    """One named check, as every door and the gate record it."""
    return {"check": check_id, "ok": bool(ok), "detail": detail}


def _missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _clean(value: Any) -> Any:
    """A JSON-safe copy: non-finite numbers and foreign types become strings, so a snapshot can
    always be sealed and a NaN can never be what stops the record of why an order was refused."""
    if isinstance(value, float):
        return value if math.isfinite(value) else repr(value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, Mapping):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [_clean(v) for v in value]
        return sorted(items, key=repr) if isinstance(value, (set, frozenset)) else items
    return str(value)


def intent_fingerprint(intent: Mapping[str, Any]) -> str:
    """What the snapshot says it approved, as one hash over :data:`INTENT_BOUND_FIELDS`."""
    return integrity.sha256_record(_clean({
        "fingerprint_version": INTENT_FINGERPRINT_VERSION,
        **{field: intent.get(field) for field in INTENT_BOUND_FIELDS},
    }))


# --- the approved profile (decision 17) --------------------------------------------------------

def stage_component(stage: Any) -> dict[str, Any]:
    """The execution stage record an order is placed under — the rung and its approval."""
    return {
        "stage": getattr(stage, "stage", None),
        "valid": bool(getattr(stage, "valid", False)),
        "stage_id": getattr(stage, "stage_id", None),
        "record_sha256": getattr(stage, "record_sha256", None),
        "approval_id": getattr(stage, "approval_id", None),
        "policy_version": getattr(stage, "policy_version", None),
    }


def budget_component(budget: Mapping[str, Any] | None) -> dict[str, Any]:
    budget = budget if isinstance(budget, Mapping) else {}
    return {
        "valid": bool(budget.get("valid")),
        "budget_id": budget.get("budget_id"),
        "record_sha256": budget.get("record_sha256"),
    }


def risk_limits_component(limits: Mapping[str, Any] | None) -> dict[str, Any]:
    """The risk limits a verdict was judged on (``RiskLimits.as_record()``)."""
    limits = limits if isinstance(limits, Mapping) else {}
    return {
        "source": limits.get("source"),
        "limits_id": limits.get("limits_id"),
        "record_sha256": limits.get("record_sha256"),
    }


def approved_profile(
    *, purpose: str, stage: Any, authority: Mapping[str, Any],
    budget: Mapping[str, Any] | None = None, risk_limits: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The records that together authorize this order. Nothing new is registered: every part is
    already an approved or registered record, and the composite is what the order is bound to."""
    return {
        "purpose": purpose,
        "stage": stage_component(stage),
        "budget": budget_component(budget) if budget is not None else None,
        "risk_limits": risk_limits_component(risk_limits) if risk_limits is not None else None,
        "authority": _clean(dict(authority)),
    }


def profile_problems(profile: Mapping[str, Any] | None) -> list[str]:
    """Why a profile does not authorize an order — empty when it does. Pure."""
    if not isinstance(profile, Mapping):
        return ["no approved profile"]
    purpose = profile.get("purpose")
    if purpose not in _AUTHORITY_FOR:
        return [f"unknown purpose {purpose!r}"]
    problems: list[str] = []
    stage = profile.get("stage") or {}
    if not (stage.get("valid") and not _missing(stage.get("stage_id"))
            and not _missing(stage.get("record_sha256")) and not _missing(stage.get("approval_id"))):
        problems.append("the execution stage is not a binding record with its approval")
    if purpose in (PURPOSE_AUTONOMOUS, PURPOSE_PROBE):
        budget = profile.get("budget") or {}
        if not (budget.get("valid") and not _missing(budget.get("budget_id"))
                and not _missing(budget.get("record_sha256"))):
            problems.append("no valid registered budget backs the order")
        limits = profile.get("risk_limits") or {}
        # The code-pinned defaults need no record to prove them (guards.RiskLimits); a registered
        # set must name the record it came from.
        if limits.get("source") != RISK_LIMITS_DEFAULT_SOURCE and (
                _missing(limits.get("limits_id")) or _missing(limits.get("record_sha256"))):
            problems.append("the risk limits in force name no record")
    authority = profile.get("authority") or {}
    kind = _AUTHORITY_FOR[purpose]
    if authority.get("kind") != kind:
        problems.append(f"the order is not authorized by a {kind} record")
    elif kind == AUTHORITY_LIVE_ARM:
        if _missing(authority.get("approval_id")) or _missing(authority.get("strategy_id")):
            problems.append("the strategy was not armed LIVE under a recorded approval")
    elif kind == AUTHORITY_PROBE_PLAN:
        if _missing(authority.get("approval_id")) or _missing(authority.get("batch_id")):
            problems.append("the probe plan names no approval")
    elif kind == AUTHORITY_TESTNET_CAPS:
        for field in ("max_order_notional_usdt", "max_daily_orders"):
            value = authority.get(field)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
                problems.append(f"the testnet caps do not state {field}")
    return problems


# --- the gate ----------------------------------------------------------------------------------

def _identity_problem(intent: Mapping[str, Any]) -> str | None:
    """Whether the intent's identifiers are the ones its own fields produce."""
    try:
        rebuilt = enrich_order_identity(dict(intent))
    except Exception as exc:  # noqa: BLE001 — an intent that cannot be re-identified fails the check
        return f"the intent cannot be re-identified ({type(exc).__name__})"
    wrong = [field for field in ("idempotency_key", "client_order_id", "order_intent_id")
             if intent.get(field) != rebuilt.get(field)]
    return f"{', '.join(wrong)} do not follow from the intent" if wrong else None


def evaluate_pre_order_gate(
    intent: Mapping[str, Any],
    *,
    purpose: str,
    venue: str,
    checks: Iterable[Mapping[str, Any]],
    profile: Mapping[str, Any],
    lineage: Mapping[str, Any],
    facts: Mapping[str, Any],
    now: str,
) -> dict[str, Any]:
    """Seal one pre-order decision. Pure: it reads no file and opens no socket.

    ``checks`` is everything the door re-derived from the facts it read; ``facts`` is the numbers
    those checks judged, recorded so the snapshot can be re-read without the process that made it.
    The result is approved only when every check — the door's and the gate's — passed."""
    door_checks = [
        check(str(c.get("check")), bool(c.get("ok")), _clean(c.get("detail")))
        for c in checks if isinstance(c, Mapping)
    ]
    wanted = LINEAGE_FIELDS.get(purpose, ())
    missing = [field for field in wanted if _missing((lineage or {}).get(field))]
    problems = profile_problems(profile)
    if isinstance(profile, Mapping) and profile.get("purpose") != purpose:
        # A profile authorizes the kind of order it was built for: a probe plan's approval is not
        # an arming approval, and neither is a testnet cap.
        problems.append(f"the profile authorizes a {profile.get('purpose')} order, not a {purpose} one")
    identity = _identity_problem(intent)
    reduce_only = bool(intent.get("reduce_only"))
    gate_checks = [
        # A door that re-derived nothing has verified nothing.
        check(CHECK_DOOR_CHECKS, bool(door_checks),
              None if door_checks else "the door re-derived no checks"),
        check(CHECK_OPENS_EXPOSURE, not reduce_only,
              "a reduce-only order is judged by the close guard, not this gate" if reduce_only else None),
        check(CHECK_INTENT_IDENTITY, identity is None, identity),
        check(CHECK_LINEAGE, purpose in LINEAGE_FIELDS and not missing,
              (f"missing {', '.join(missing)}" if missing else None)
              if purpose in LINEAGE_FIELDS else f"unknown purpose {purpose!r}"),
        check(CHECK_PROFILE, not problems, "; ".join(problems) or None),
    ]
    all_checks = door_checks + gate_checks
    approved = all(c["ok"] for c in all_checks)
    profile_clean = _clean(dict(profile)) if isinstance(profile, Mapping) else None
    body: dict[str, Any] = {
        "snapshot_version": SNAPSHOT_VERSION,
        "risk_gate_id": GATE_ID,
        "purpose": purpose,
        "venue": venue,
        "created_at": now,
        "symbol": intent.get("symbol"),
        "side": intent.get("side"),
        "client_order_id": intent.get("client_order_id"),
        "idempotency_key": intent.get("idempotency_key"),
        "order_intent_id": intent.get("order_intent_id"),
        "intent_fingerprint": intent_fingerprint(intent),
        "approved": approved,
        "failed_checks": [c["check"] for c in all_checks if not c["ok"]],
        "checks": all_checks,
        "approved_profile": profile_clean,
        "approved_profile_sha256": integrity.sha256_record(profile_clean) if profile_clean else None,
        "lineage": _clean(dict(lineage or {})),
        "facts": _clean(dict(facts or {})),
    }
    body["pre_order_risk_snapshot_id"] = integrity.short_id(
        "pre_order_risk_snapshot",
        {"intent": body["intent_fingerprint"], "at": str(now), "venue": str(venue)},
    )
    body["risk_snapshot_sha256"] = integrity.sha256_record(body)
    return body


def bind_intent(intent: Mapping[str, Any], snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """The intent as it may be sent: naming the snapshot that approved it. The two added fields are
    outside :data:`INTENT_BOUND_FIELDS`, so binding changes neither the fingerprint nor the order's
    identity at the venue."""
    return {
        **dict(intent),
        "pre_order_risk_snapshot_id": snapshot.get("pre_order_risk_snapshot_id"),
        "risk_snapshot_sha256": snapshot.get("risk_snapshot_sha256"),
    }


def _schema_problem(record: Mapping[str, Any]) -> str | None:
    """Why ``record`` is not a recordable snapshot (the closed schema), or None. The schema is
    the image's own, as for the execution stage record."""
    try:
        validate_against_schema(dict(record), _repo_root() / "schemas" / SNAPSHOT_SCHEMA_FILE,
                                "pre-order risk snapshot")
    except RuntimeSchemaError as exc:
        return str(exc)
    return None


def _seal_matches(snapshot: Mapping[str, Any]) -> bool:
    stored = snapshot.get("risk_snapshot_sha256")
    body = {k: v for k, v in snapshot.items() if k != "risk_snapshot_sha256"}
    try:
        return isinstance(stored, str) and integrity.sha256_record(body) == stored
    except (ValueError, TypeError, RecursionError):
        return False


def verify_snapshot(intent: Mapping[str, Any], snapshot: Any) -> str:
    """Refuse unless ``snapshot`` approved exactly this intent and is intact. Returns its hash."""
    if not isinstance(snapshot, Mapping):
        raise ToolError(RISK_SNAPSHOT_MISSING, "an order that opens exposure needs a pre-order risk snapshot")
    if snapshot.get("snapshot_version") != SNAPSHOT_VERSION or snapshot.get("risk_gate_id") != GATE_ID:
        raise ToolError(RISK_SNAPSHOT_TAMPERED, "the pre-order risk snapshot is not one this gate writes")
    if not _seal_matches(snapshot):
        raise ToolError(RISK_SNAPSHOT_TAMPERED, "the pre-order risk snapshot fails its self-hash")
    if snapshot.get("approved") is not True or snapshot.get("failed_checks"):
        raise ToolError(RISK_SNAPSHOT_NOT_APPROVED,
                        f"the pre-order gate refused this order ({', '.join(snapshot.get('failed_checks') or [])})")
    problem = _schema_problem(snapshot)
    if problem is not None:
        # Only a snapshot this schema describes may be recorded — and so authorize an order.
        raise ToolError(RISK_SNAPSHOT_INVALID, f"the pre-order risk snapshot is not recordable: {problem}")
    if snapshot.get("intent_fingerprint") != intent_fingerprint(intent):
        raise ToolError(RISK_SNAPSHOT_INTENT_MISMATCH, "the pre-order risk snapshot approved a different order")
    if intent.get("risk_snapshot_sha256") != snapshot.get("risk_snapshot_sha256"):
        raise ToolError(RISK_SNAPSHOT_INTENT_MISMATCH, "the order does not name the snapshot that approved it")
    return str(snapshot["risk_snapshot_sha256"])


def verify_and_persist(intent: Mapping[str, Any], snapshot: Any, *, store: Any) -> str:
    """The binding: verify, then write the snapshot to its venue's store — before anything is sent."""
    sha = verify_snapshot(intent, snapshot)
    if store is None:
        raise ToolError(RISK_SNAPSHOT_NO_STORE, "no pre-order snapshot store: the order would leave no record")
    if getattr(store, "venue", None) != snapshot.get("venue"):
        raise ToolError(RISK_SNAPSHOT_VENUE_MISMATCH,
                        f"a {snapshot.get('venue')} snapshot cannot authorize an order through the "
                        f"{getattr(store, 'venue', None)} store")
    written = store.append(snapshot)
    if written != sha:
        raise ToolError(RISK_SNAPSHOT_TAMPERED, "the store recorded a different snapshot than the one verified")
    return sha


# --- the store (decision 19) --------------------------------------------------------------------

def snapshot_path(root: Path | None = None, *, venue: str = VENUE_MAINNET) -> Path:
    return venue_state_dir(root, venue=venue) / SNAPSHOT_FILENAME


def _recorded_hashes(path: Path) -> tuple[dict[str, str], bool]:
    """``snapshot id -> hash`` for every complete row, read raw, and whether the file ends mid-line.

    An unparseable line is skipped, and that is exact rather than lenient: a row is appended and
    synced before its order is sent, so a line the writer never finished is one no order followed.
    The second value tells the writer to end that torn line first — appending straight after it
    would fuse the next row into it and lose that row too."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}, False
    except (OSError, UnicodeDecodeError) as exc:
        raise ToolError(RISK_SNAPSHOT_STORE_UNREADABLE, f"pre-order snapshots unreadable: {type(exc).__name__}") from exc
    recorded: dict[str, str] = {}
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and isinstance(row.get("pre_order_risk_snapshot_id"), str):
            recorded[row["pre_order_risk_snapshot_id"]] = str(row.get("risk_snapshot_sha256"))
    return recorded, bool(text) and not text.endswith("\n")


class PreOrderSnapshotStore:
    """One venue's append-only record of the snapshots its orders left under.

    Behind that venue's switch, like its counter: an inert store beside a capable adapter is an
    order with no record of why it was allowed. The provider and flags are the venue's own, so the
    live authorization cannot write the testnet store and the reverse."""

    filesystem_write = True

    def __init__(self, *, root: Path | None = None, authorization: Authorization | None = None,
                 venue: str = VENUE_MAINNET, provider_id: str, flags: Sequence[str]):
        self._root = root
        self._authorization = authorization
        self.venue = venue
        self._provider_id = provider_id
        self._flags = tuple(flags)

    def _assert(self) -> None:
        safety_gate.assert_authorization(
            self._authorization,
            required_flags=self._flags,
            provider_id=self._provider_id,
            now=timeutil.utc_now_iso(),
        )

    def append(self, snapshot: Mapping[str, Any]) -> str:
        """Write ``snapshot`` and sync it. Idempotent on its id; a different snapshot under a
        recorded id is refused. Returns the recorded hash."""
        self._assert()
        snapshot_id = snapshot.get("pre_order_risk_snapshot_id")
        sha = snapshot.get("risk_snapshot_sha256")
        if not (isinstance(snapshot_id, str) and snapshot_id and isinstance(sha, str) and sha):
            raise ToolError(RISK_SNAPSHOT_MISSING, "a snapshot without an id and a hash cannot be recorded")
        path = snapshot_path(self._root, venue=self.venue)
        path.parent.mkdir(parents=True, exist_ok=True)
        with locked(path.with_suffix(".lock"), code="PRE_ORDER_SNAPSHOTS_LOCKED",
                    label="pre-order risk snapshots"):
            recorded, torn_tail = _recorded_hashes(path)
            if snapshot_id in recorded:
                if recorded[snapshot_id] == sha:
                    return sha
                raise ToolError(RISK_SNAPSHOT_ID_CONFLICT,
                                f"snapshot {snapshot_id} is already recorded with different content")
            with open(path, "a", encoding="utf-8", newline="\n") as handle:
                handle.write(("\n" if torn_tail else "")
                             + json.dumps(dict(snapshot), ensure_ascii=False, sort_keys=True) + "\n")
                handle.flush()
                # The row is the order's reason for existing; it reaches the disk before the order
                # reaches the venue. The directory entry is not synced, as for the live book.
                os.fsync(handle.fileno())
        return sha


class DryRunPreOrderSnapshotStore:
    """Inert: with the venue's switch off nothing can be sent, so nothing needs recording."""

    filesystem_write = False

    def __init__(self, *, venue: str = VENUE_MAINNET):
        self.venue = venue

    def append(self, snapshot: Mapping[str, Any]) -> str:
        return str(snapshot.get("risk_snapshot_sha256"))


def read_snapshots(root: Path | None = None, *, venue: str = VENUE_MAINNET) -> list[dict[str, Any]]:
    """Every recorded snapshot at ``venue``, oldest first — a VERIFIED read for audit and the board.

    Raises on a row that fails its seal or an id recorded twice with different content. A line the
    writer never finished is not a row (see :func:`_recorded_hashes`) and is ignored — tampering
    shows as a row that parses and fails its seal, which is refused."""
    path = snapshot_path(root, venue=venue)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except (OSError, UnicodeDecodeError) as exc:
        raise ToolError(RISK_SNAPSHOT_STORE_UNREADABLE, f"pre-order snapshots unreadable: {type(exc).__name__}") from exc
    lines = text.split("\n")
    rows: list[dict[str, Any]] = []
    seen: dict[str, str] = {}
    for index, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            # A line the writer never finished: an append that never returned, so no order followed
            # it. At the end of the file it is the tail of the last attempt; anywhere else it is one
            # the next append ended with a newline before writing its own row.
            continue
        if not isinstance(row, dict) or not _seal_matches(row):
            raise ToolError(RISK_SNAPSHOT_STORE_TAMPERED, f"pre-order snapshot line {index} fails its seal")
        if _schema_problem(row) is not None:
            raise ToolError(RISK_SNAPSHOT_STORE_TAMPERED,
                            f"pre-order snapshot line {index} is not a recordable snapshot")
        snapshot_id = str(row.get("pre_order_risk_snapshot_id"))
        if snapshot_id in seen and seen[snapshot_id] != row.get("risk_snapshot_sha256"):
            raise ToolError(RISK_SNAPSHOT_STORE_TAMPERED, f"snapshot {snapshot_id} is recorded twice")
        if snapshot_id not in seen:
            seen[snapshot_id] = str(row.get("risk_snapshot_sha256"))
            rows.append(row)
    return rows


def find_snapshot(sha: str, root: Path | None = None, *, venue: str = VENUE_MAINNET) -> dict[str, Any] | None:
    """The recorded snapshot with this hash, verified — or None."""
    for row in read_snapshots(root, venue=venue):
        if row.get("risk_snapshot_sha256") == sha:
            return row
    return None


def snapshots_status(root: Path | None = None, *, venue: str = VENUE_MAINNET) -> dict[str, Any]:
    """The board's view: how many orders left under a recorded snapshot, and whether the record
    still proves itself. Never raises."""
    try:
        rows = read_snapshots(root, venue=venue)
    except ToolError as exc:
        return {"readable": False, "error": exc.reason_code, "count": None, "last_created_at": None}
    return {
        "readable": True,
        "error": None,
        "count": len(rows),
        "last_created_at": rows[-1].get("created_at") if rows else None,
    }


__all__ = [
    "AUTHORITY_LIVE_ARM",
    "AUTHORITY_PROBE_PLAN",
    "AUTHORITY_TESTNET_CAPS",
    "DryRunPreOrderSnapshotStore",
    "GATE_ID",
    "INTENT_BOUND_FIELDS",
    "LINEAGE_FIELDS",
    "PreOrderSnapshotStore",
    "SNAPSHOT_FILENAME",
    "SNAPSHOT_SCHEMA_FILE",
    "SNAPSHOT_VERSION",
    "approved_profile",
    "bind_intent",
    "check",
    "evaluate_pre_order_gate",
    "find_snapshot",
    "intent_fingerprint",
    "profile_problems",
    "read_snapshots",
    "snapshot_path",
    "snapshots_status",
    "verify_and_persist",
    "verify_snapshot",
]

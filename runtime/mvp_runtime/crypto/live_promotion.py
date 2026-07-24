"""LP6 live-canary promotion evidence (source L5).

The evidence side of the promotion gate: how many real mainnet canary orders have been
placed and reconciled cleanly. LP3's final guard already refuses an autonomous live entry
until that count reaches the configured minimum — but it takes the number as an argument and
until now **nobody supplied it**. This module is the supplier.

A canary is one small real order placed by the operator on purpose, to prove the signing,
submission, and reconciliation path works against the live venue before anything autonomous
uses it. It is evidence, not a capability: recording one grants nothing, and the record
cannot be produced except by having actually placed the order.

Fail-closed toward NOT ready, in two independent ways, both carried over from the source:

* An unverifiable registry counts **zero**, never "unknown" and never the last good number.
  Damaged evidence is no evidence.
* A minimum of zero or less is refused outright — that would be promotion with no evidence
  at all, which is the one configuration that must never read as satisfied.

Writes ride the same single ``live_trading`` grant as the P&L ledger and the order counter:
one switch for the whole live capability, revoked together.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping, Protocol

from runtime.read_only_kernel import integrity

from .. import safety_gate, timeutil
from ..errors import ToolError
from ..filelock import locked
from ..safety_gate import Authorization
from .live_pnl import (
    LIVE_TRADING_ENV,
    LIVE_TRADING_FLAGS,
    LIVE_TRADING_PROVIDER_ID,
    REAL_LIVE_TRADING,
    state_dir,
)

CANARY_TOOL_ID = "crypto.live.canary_registry"
CANARY_TOOL_VERSION = "0.1.0"

CANARY_ORDERS_FILENAME = "live_canary_orders.jsonl"
CANARY_PROVENANCE = "mvp_live_canary"

RECONCILED = "RECONCILED"

CANARY_HISTORY_UNREADABLE = "CANARY_HISTORY_UNREADABLE"
CANARY_HISTORY_TAMPERED = "CANARY_HISTORY_TAMPERED"
CANARY_HISTORY_DUPLICATE = "CANARY_HISTORY_DUPLICATE"

# The source's default: three clean canary orders before an autonomous live entry.
DEFAULT_MIN_CLEAN_CANARY_ORDERS = 3


def build_canary_order_record(
    *,
    reconcile_status: str,
    symbol: str,
    exchange_order_id: Any = None,
    client_order_id: str | None = None,
    mismatches: list[str] | None = None,
    notional_usdt: float | None = None,
    now: str,
) -> dict[str, Any]:
    """One placed-and-reconciled canary order, self-hashed.

    ``clean`` is derived here rather than accepted from the caller: an order counts only if
    the venue reconciled it AND nothing mismatched. A caller cannot assert cleanliness.
    """
    problems = list(mismatches or [])
    body: dict[str, Any] = {
        "reconcile_status": reconcile_status,
        "clean": reconcile_status == RECONCILED and not problems,
        "symbol": symbol,
        "exchange_order_id": exchange_order_id,
        "client_order_id": client_order_id,
        "mismatches": problems,
        "notional_usdt": notional_usdt,
        "recorded_at_utc": now,
        "stage": "live_canary",
        "provenance": CANARY_PROVENANCE,
    }
    body["canary_order_id"] = integrity.short_id(
        "canary", {"client_order_id": client_order_id, "exchange_order_id": exchange_order_id,
                   "recorded_at": now}
    )
    body["record_sha256"] = integrity.sha256_record(body)
    return body


def read_canary_orders(root: Path | None = None) -> list[dict[str, Any]]:
    """All canary records, oldest first — a VERIFIED read.

    Missing store = honestly empty (no canary has been placed). Anything unreadable,
    tampered, or duplicated raises: this history is the sole evidence gating autonomous live
    trading, so a record that cannot prove itself must not be allowed to count.
    """
    path = state_dir(root) / CANARY_ORDERS_FILENAME
    if not path.is_file():
        return []
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ToolError(CANARY_HISTORY_UNREADABLE, f"canary registry unreadable: {exc.strerror}") from exc
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError as exc:
            raise ToolError(CANARY_HISTORY_UNREADABLE, f"canary registry line {i + 1} is not valid JSON") from exc
        if not isinstance(record, dict):
            continue
        stored = record.get("record_sha256")
        body = {k: v for k, v in record.items() if k != "record_sha256"}
        if not isinstance(stored, str) or integrity.sha256_record(body) != stored:
            raise ToolError(CANARY_HISTORY_TAMPERED, f"canary registry line {i + 1} fails its self-hash")
        order_id = record.get("canary_order_id")
        if isinstance(order_id, str) and order_id:
            if order_id in seen:
                raise ToolError(CANARY_HISTORY_DUPLICATE, f"duplicate canary_order_id: {order_id}")
            seen.add(order_id)
        records.append(record)
    return records


def clean_canary_order_count(root: Path | None = None) -> tuple[int, str | None]:
    """``(clean_count, history_error_reason_code)``.

    A registry that cannot be verified counts **zero** and names why. Returning the number
    and the error together is deliberate: the caller gets a usable count without the error
    being silently swallowed, and "0 because damaged" is never mistaken for "0 because new".
    """
    try:
        records = read_canary_orders(root)
    except ToolError as exc:
        return 0, exc.reason_code
    return sum(1 for r in records if r.get("clean") is True), None


def promotion_status(
    *, min_orders: int | None = None, root: Path | None = None
) -> dict[str, Any]:
    """Is there enough canary evidence for an autonomous live entry?

    A ``min_orders`` of zero or less is **refused**, not satisfied: requiring no evidence is
    the one setting that must never read as ready.
    """
    required = DEFAULT_MIN_CLEAN_CANARY_ORDERS if min_orders is None else int(min_orders)
    clean_count, history_error = clean_canary_order_count(root)
    reasons: list[str] = []
    if required <= 0:
        reasons.append("promotion minimum is not configured (would be promotion with no evidence)")
    elif clean_count < required:
        reasons.append(f"need >= {required} clean canary orders, have {clean_count}")
    if history_error is not None:
        reasons.append(f"canary registry could not be verified ({history_error}); counted as zero")
    return {
        "ready": not reasons,
        "clean_count": clean_count,
        "required": required,
        "history_error": history_error,
        "reasons": reasons,
    }


class CanaryRegistry(Protocol):
    """Append-only canary evidence. Reads are ungated module functions."""

    tool_id: str
    tool_version: str

    def append_canary_order(self, record: Mapping[str, Any]) -> None: ...


class DryRunCanaryRegistry:
    """Inert registry: accepts and discards.

    A canary record should be impossible to produce without the grant, since producing one
    means an order was actually placed. If one arrives here anyway it is dropped rather than
    persisted — unbacked evidence in this registry would unlock autonomous trading.
    """

    tool_id = CANARY_TOOL_ID
    tool_version = f"{CANARY_TOOL_VERSION}-dryrun"
    filesystem_write = False

    def append_canary_order(self, record: Mapping[str, Any]) -> None:
        return None


class RealCanaryRegistry:
    """Durable canary evidence, behind the one live-trading grant."""

    tool_id = CANARY_TOOL_ID
    tool_version = CANARY_TOOL_VERSION
    provider_id = LIVE_TRADING_PROVIDER_ID
    filesystem_write = True

    def __init__(self, *, root: Path | None = None, authorization: Authorization | None = None):
        self._root = root
        self._authorization = authorization

    def _assert(self) -> None:
        safety_gate.assert_authorization(
            self._authorization,
            required_flags=LIVE_TRADING_FLAGS,
            provider_id=self.provider_id,
            now=timeutil.utc_now_iso(),
        )

    def append_canary_order(self, record: Mapping[str, Any]) -> None:
        self._assert()
        target = state_dir(self._root)
        target.mkdir(parents=True, exist_ok=True)
        path = target / CANARY_ORDERS_FILENAME
        with locked(path.with_suffix(".lock"), code="LIVE_STATE_LOCKED", label="canary registry"):
            with open(path, "a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(dict(record), ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())


def select_canary_registry(*, now: str | None = None, root: Path | None = None) -> CanaryRegistry:
    """Return the durable canary registry if the live-trading grant is open, else the inert one."""
    return safety_gate.select_gated(
        env_var=LIVE_TRADING_ENV,
        opt_in_value=REAL_LIVE_TRADING,
        flags=LIVE_TRADING_FLAGS,
        provider_id=LIVE_TRADING_PROVIDER_ID,
        default_factory=DryRunCanaryRegistry,
        gated_factory=lambda authorization: RealCanaryRegistry(root=root, authorization=authorization),
        now=now,
        root=root,
    )

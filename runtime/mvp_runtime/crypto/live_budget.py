"""Step 6 — the registered live-trading budget (``live_trading_budget.v0.1``).

The LP3 order guard currently reads its risk caps from environment variables. That is
enough to *refuse* correctly, which is all the guard does today, but env vars are not a
record: nothing is hashed, nothing is auditable, and a limit can change between two orders
with no trace. `LIVE_EXECUTION_GOVERNANCE_V0.1.md` item 6, and the policy line
``autonomous_spend_without_registered_budget: '0'``, call for a registered budget instead.

This module is the record type + the verified read + a loader; the operator registers one
with ``scripts/register_live_trading_budget.py``. It is deliberately the *record only*:

- **Registering a budget grants nothing and enables no trading.** It is a self-hashed
  operator record, not a permission and not a grant. The `live_trading` safety-flag grant,
  the confirmation phrase, the ≥3 clean canary orders, the P5 role, and LP4/LP5 all still
  stand between here and a live order.
- **The guard is not rewired here.** Making the registered budget the *authoritative* source
  the guard reads (replacing the env caps) is a separate increment, so the well-tested guard
  logic stays untouched while the record type and registration path land first.
- **Changing a limit is a new record, never a silent edit** — the id and the self-hash both
  derive from the caps, so an edited cap is a different budget with a different hash.

A read is *verified*: a tampered or unparseable budget raises rather than resolving, because
every reader of a risk limit is a risk decision (the ``live_pnl`` verified-read posture).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from runtime.read_only_kernel import integrity
from runtime.read_only_kernel.schema_validation import RuntimeSchemaError

from ..errors import ToolError
from ..paths import repo_root as _repo_root
from ..schema_cache import validate_against_schema
from .live_pnl import state_dir

LIVE_BUDGET_SCHEMA_VERSION = "live_trading_budget.v0.1"
LIVE_BUDGET_SCHEMA_FILE = "live_trading_budget.v0.1.schema.json"
LIVE_BUDGET_FILENAME = "live_trading_budget.json"

# The absolute ceiling a per-order cap can never exceed (Thomas, 2026-07-23). The schema
# pins the same 200 on ``absolute_max_notional_usdt``; the builder checks it too so the
# refusal names the offending number rather than emitting a raw schema error.
HARD_CEILING_USDT = 200.0
SUPPORTED_VENUE = "binance_futures"

BUDGET_INVALID = "LIVE_BUDGET_INVALID"
BUDGET_UNREADABLE = "LIVE_BUDGET_UNREADABLE"
BUDGET_TAMPERED = "LIVE_BUDGET_TAMPERED"

_CAP_KEYS = (
    "max_order_notional_usdt",
    "absolute_max_notional_usdt",
    "max_daily_order_count",
    "max_open_notional_usdt",
    "daily_loss_limit_usdt",
    "min_clean_canary_orders",
)


def _schema_path(repo_root: Path | None = None) -> Path:
    return (repo_root if repo_root is not None else _repo_root()) / "schemas" / LIVE_BUDGET_SCHEMA_FILE


def budget_path(root: Path | None = None) -> Path:
    """The per-machine registered-budget file (gitignored, beside the live outcomes)."""
    return state_dir(root) / LIVE_BUDGET_FILENAME


def build_live_trading_budget_record(
    *,
    caps: Mapping[str, Any],
    symbol_allowlist: Sequence[str],
    valid_from: str,
    valid_until: str,
    registered_by: str,
    registered_at: str,
    venue: str = SUPPORTED_VENUE,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Build a self-hashed, schema-valid live-trading budget record. Fail-closed.

    Raises ``ToolError(BUDGET_INVALID)`` on any violation — an unsupported venue, a missing or
    non-positive cap, a per-order cap above its absolute ceiling, an absolute ceiling above the
    200 USDT hard ceiling, an empty symbol allowlist, or a validity window that does not open
    before it closes. Every refusal names what is wrong; a budget that cannot be built is never
    written."""
    if venue != SUPPORTED_VENUE:
        raise ToolError(BUDGET_INVALID, f"unsupported venue {venue!r}; only {SUPPORTED_VENUE!r} is supported")

    missing = [k for k in _CAP_KEYS if k not in caps]
    if missing:
        raise ToolError(BUDGET_INVALID, f"caps is missing required keys: {sorted(missing)}")

    numeric: dict[str, float] = {}
    for key in _CAP_KEYS:
        try:
            numeric[key] = float(caps[key])
        except (TypeError, ValueError):
            raise ToolError(BUDGET_INVALID, f"cap {key} is not a number: {caps[key]!r}") from None
        if numeric[key] <= 0:
            # Zero or negative is the halted/unconfigured state; a registered budget must be a
            # real, positive limit or it would register the very "not configured" state the
            # guard reads as blocked.
            raise ToolError(BUDGET_INVALID, f"cap {key} must be > 0, got {numeric[key]}")

    for count_key in ("max_daily_order_count", "min_clean_canary_orders"):
        if float(caps[count_key]) != int(caps[count_key]):
            raise ToolError(BUDGET_INVALID, f"cap {count_key} must be a whole number, got {caps[count_key]!r}")

    if numeric["absolute_max_notional_usdt"] > HARD_CEILING_USDT:
        raise ToolError(
            BUDGET_INVALID,
            f"absolute_max_notional_usdt {numeric['absolute_max_notional_usdt']} exceeds the hard "
            f"ceiling {HARD_CEILING_USDT}",
        )
    if numeric["max_order_notional_usdt"] > numeric["absolute_max_notional_usdt"]:
        raise ToolError(
            BUDGET_INVALID,
            f"max_order_notional_usdt {numeric['max_order_notional_usdt']} exceeds "
            f"absolute_max_notional_usdt {numeric['absolute_max_notional_usdt']} (a cap above the "
            "ceiling is refused, never clamped)",
        )

    symbols = [str(s).strip().upper() for s in symbol_allowlist if str(s).strip()]
    if not symbols:
        raise ToolError(BUDGET_INVALID, "symbol_allowlist must name at least one symbol")

    if not (isinstance(valid_from, str) and isinstance(valid_until, str) and valid_from < valid_until):
        raise ToolError(BUDGET_INVALID, f"validity window must open before it closes: {valid_from} .. {valid_until}")
    if not (isinstance(registered_by, str) and registered_by.strip()):
        raise ToolError(BUDGET_INVALID, "registered_by (operator identity) is required")

    body: dict[str, Any] = {
        "schema_version": LIVE_BUDGET_SCHEMA_VERSION,
        # short_id forbids floats in its seed (they are non-deterministic across platforms),
        # so the caps are stringified here. The record body below keeps them numeric — its
        # self-hash uses sha256_record, which does accept floats.
        "budget_id": integrity.short_id(
            "budget",
            {"venue": venue, "symbols": symbols, "caps": {k: str(numeric[k]) for k in _CAP_KEYS},
             "valid_from": valid_from, "valid_until": valid_until, "registered_by": registered_by.strip()},
        ),
        "venue": venue,
        "symbol_allowlist": symbols,
        "caps": {
            "max_order_notional_usdt": numeric["max_order_notional_usdt"],
            "absolute_max_notional_usdt": numeric["absolute_max_notional_usdt"],
            "max_daily_order_count": int(caps["max_daily_order_count"]),
            "max_open_notional_usdt": numeric["max_open_notional_usdt"],
            "daily_loss_limit_usdt": numeric["daily_loss_limit_usdt"],
            "min_clean_canary_orders": int(caps["min_clean_canary_orders"]),
        },
        "valid_from": valid_from,
        "valid_until": valid_until,
        "registered_by": registered_by.strip(),
        "registered_at": registered_at,
    }
    body["record_sha256"] = integrity.sha256_record(body)
    _validate(body, repo_root=repo_root)
    return body


def _validate(record: Mapping[str, Any], *, repo_root: Path | None = None) -> None:
    try:
        validate_against_schema(dict(record), _schema_path(repo_root), "live trading budget")
    except RuntimeSchemaError as exc:
        raise ToolError(BUDGET_INVALID, f"budget record is not schema-valid: {exc}") from exc


def read_registered_budget(root: Path | None = None) -> dict[str, Any] | None:
    """The registered budget for this machine, VERIFIED — or None when none is registered.

    Missing file = honestly None (no budget registered yet). Anything unparseable, failing its
    self-hash, or not schema-valid raises, because every caller of a risk limit is a risk
    decision: a budget that cannot prove itself must not be allowed to authorize a cap."""
    path = budget_path(root)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ToolError(BUDGET_UNREADABLE, f"registered budget is unreadable: {exc}") from None
    if not isinstance(data, dict):
        raise ToolError(BUDGET_UNREADABLE, "registered budget is not a JSON object")
    stored = data.get("record_sha256")
    body = {k: v for k, v in data.items() if k != "record_sha256"}
    if not isinstance(stored, str) or integrity.sha256_record(body) != stored:
        raise ToolError(BUDGET_TAMPERED, "registered budget fails its self-hash")
    _validate(data)
    return data


def budget_status(root: Path | None = None, *, now: str) -> dict[str, Any]:
    """Whether a valid budget is registered right now — for the readiness board and operator.

    Fail-closed: an unverifiable budget reports ``registered`` with ``valid=False`` and names
    the error rather than reporting a comfortable "no budget"."""
    try:
        record = read_registered_budget(root)
    except ToolError as exc:
        return {"registered": True, "valid": False, "error": exc.reason_code, "budget_id": None}
    if record is None:
        return {"registered": False, "valid": False, "error": None, "budget_id": None}
    within_window = record["valid_from"] <= now <= record["valid_until"]
    return {
        "registered": True,
        "valid": within_window,
        "error": None if within_window else "OUTSIDE_VALIDITY_WINDOW",
        "budget_id": record["budget_id"],
        "venue": record["venue"],
        "symbol_allowlist": record["symbol_allowlist"],
        "caps": record["caps"],
        "valid_from": record["valid_from"],
        "valid_until": record["valid_until"],
        "record_sha256": record["record_sha256"],
    }


def limits_from_budget(record: Mapping[str, Any]) -> Any:
    """A ``LiveOrderLimits`` carrying the registered caps (for a later guard-rewiring increment).

    Maps the six registered caps; ``confirmation`` and ``manual_kill_switch`` are deliberately
    left at their defaults — they are operator env state (a phrase and a halt), not
    budget-registered caps, so a budget can never carry the confirmation that proves intent."""
    from .live_order import LiveOrderLimits

    caps = record["caps"]
    return LiveOrderLimits(
        max_order_notional_usdt=float(caps["max_order_notional_usdt"]),
        absolute_max_notional_usdt=float(caps["absolute_max_notional_usdt"]),
        max_daily_order_count=int(caps["max_daily_order_count"]),
        max_open_notional_usdt=float(caps["max_open_notional_usdt"]),
        daily_loss_limit_usdt=float(caps["daily_loss_limit_usdt"]),
        min_clean_canary_orders=int(caps["min_clean_canary_orders"]),
    )


def write_registered_budget(record: Mapping[str, Any], *, root: Path | None = None) -> Path:
    """Persist a built budget record to the per-machine state dir (operator registration path).

    Re-validates before writing (a caller cannot register an unverified record), then writes it
    as the single active budget file. Returns the path written. ``root`` is the per-machine
    STATE root (where the file lands); the schema always resolves against the real repo root,
    so the two are never conflated (they differ under a tmp-dir test)."""
    _validate(record)
    stored = record.get("record_sha256")
    body = {k: v for k, v in record.items() if k != "record_sha256"}
    if not isinstance(stored, str) or integrity.sha256_record(body) != stored:
        raise ToolError(BUDGET_TAMPERED, "refusing to register a budget that fails its self-hash")
    target = state_dir(root)
    target.mkdir(parents=True, exist_ok=True)
    path = target / LIVE_BUDGET_FILENAME
    path.write_text(json.dumps(dict(record), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return path

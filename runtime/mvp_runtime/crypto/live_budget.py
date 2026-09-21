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
  the confirmation phrase, the P5 role, and LP4/LP5 all still stand between here and a live
  order. (A ≥3 clean-canary minimum stood here too until 2026-09-15, when Thomas removed it
  with the canary door — PR1r. Records registered before still carry
  ``caps.min_clean_canary_orders``; the schema accepts it and nothing reads it.)
- **The guard is not rewired here.** Making the registered budget the *authoritative* source
  the guard reads (replacing the env caps) is a separate increment, so the well-tested guard
  logic stays untouched while the record type and registration path land first.
- **Changing a limit is a new record, never a silent edit** — the id and the self-hash both
  derive from the caps, so an edited cap is a different budget with a different hash.
- **A budget no longer lapses by itself.** Until 2026-09-15 every record carried a validity
  window (30 days by default) and fell out of force at its end; Thomas retired the window with
  the canary door (PR1r). A record registered since carries none and stands until it is
  re-registered or its file is deleted. A record registered before still carries its window
  inside its self-hash and is **still held to it** — so restoring an old archive, or a rollback
  that re-registers with the old script, cannot bring a lapsed budget back into force.

A read is *verified*: a tampered or unparseable budget raises rather than resolving, because
every reader of a risk limit is a risk decision (the ``live_ledger`` verified-read posture).
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
from .state import VENUE_MAINNET, venue_state_dir

LIVE_BUDGET_SCHEMA_VERSION = "live_trading_budget.v0.1"
LIVE_BUDGET_SCHEMA_FILE = "live_trading_budget.v0.1.schema.json"
LIVE_BUDGET_FILENAME = "live_trading_budget.json"

# The absolute ceiling a per-order cap can never exceed. 200.0 at first live bring-up
# (Thomas, 2026-07-23); raised to 500.0 on Thomas's instruction 2026-08-08, after the live
# path had earned its 4 clean canary orders and 2 completed round trips. The schema pins the
# same number on ``absolute_max_notional_usdt``; the builder checks it too so the refusal
# names the offending number rather than emitting a raw schema error, and
# ``test_schema_and_code_agree_on_the_hard_ceiling`` fails if the two ever drift.
#
# Raising this widens nothing on its own. It is the ceiling a *registered budget* may declare,
# not a cap any order is judged against: the guard reads
# ``min(max_order_notional_usdt, absolute_max_notional_usdt)`` off the registered record, so
# until an operator registers a budget carrying a larger number, every order is still judged
# against the caps already registered.
HARD_CEILING_USDT = 500.0
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
)


def _schema_path(repo_root: Path | None = None) -> Path:
    return (repo_root if repo_root is not None else _repo_root()) / "schemas" / LIVE_BUDGET_SCHEMA_FILE


def budget_path(root: Path | None = None, *, venue: str = VENUE_MAINNET) -> Path:
    """The per-machine registered-budget file for ``venue`` (gitignored, beside that venue's
    outcomes). One budget file per venue (PR1d-0): caps that bound real money must not bound, or
    be spent by, a venue that trades none.

    The record's own ``venue`` field is still the single-valued schema enum, so only a mainnet
    budget can be BUILT today; whether another venue registers one at all is PR1d-1's decision.
    The path axis lands here so that when it does, its file cannot be the live one."""
    return venue_state_dir(root, venue=venue) / LIVE_BUDGET_FILENAME


def build_live_trading_budget_record(
    *,
    caps: Mapping[str, Any],
    symbol_allowlist: Sequence[str],
    registered_by: str,
    registered_at: str,
    venue: str = SUPPORTED_VENUE,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Build a self-hashed, schema-valid live-trading budget record. Fail-closed.

    Raises ``ToolError(BUDGET_INVALID)`` on any violation — an unsupported venue, a missing or
    non-positive cap, a per-order cap above its absolute ceiling, an absolute ceiling above
    ``HARD_CEILING_USDT``, an empty symbol allowlist, or no operator identity. Every refusal
    names what is wrong; a budget that cannot be built is never written.

    The record carries no validity window (retired 2026-09-15, PR1r): it stands until it is
    re-registered or deleted. ``budget_id`` seeds on ``registered_at`` where it used to seed on
    the window, so two registrations of the same caps still get two ids."""
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

    daily_count = caps["max_daily_order_count"]
    if float(daily_count) != int(daily_count):
        raise ToolError(BUDGET_INVALID, f"cap max_daily_order_count must be a whole number, got {daily_count!r}")

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
             "registered_by": registered_by.strip(), "registered_at": registered_at},
        ),
        "venue": venue,
        "symbol_allowlist": symbols,
        "caps": {
            "max_order_notional_usdt": numeric["max_order_notional_usdt"],
            "absolute_max_notional_usdt": numeric["absolute_max_notional_usdt"],
            "max_daily_order_count": int(caps["max_daily_order_count"]),
            "max_open_notional_usdt": numeric["max_open_notional_usdt"],
            "daily_loss_limit_usdt": numeric["daily_loss_limit_usdt"],
        },
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


def read_registered_budget(root: Path | None = None, *, venue: str = VENUE_MAINNET) -> dict[str, Any] | None:
    """The registered budget for this machine, VERIFIED — or None when none is registered.

    Missing file = honestly None (no budget registered yet). Anything unparseable, failing its
    self-hash, not schema-valid, or carrying an unusable validity window raises, because every
    caller of a risk limit is a risk decision: a budget that cannot prove itself must not be
    allowed to authorize a cap."""
    path = budget_path(root, venue=venue)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError) as exc:
        raise ToolError(BUDGET_UNREADABLE, f"registered budget is unreadable: {exc}") from None
    if not isinstance(data, dict):
        raise ToolError(BUDGET_UNREADABLE, "registered budget is not a JSON object")
    stored = data.get("record_sha256")
    body = {k: v for k, v in data.items() if k != "record_sha256"}
    try:
        recomputed = integrity.sha256_record(body)
    except (ValueError, TypeError, RecursionError) as exc:
        # A NaN, a secret-shaped key or a pathological nesting cannot be canonicalised. It must
        # still surface as this module's typed refusal: the live leg reads this before it settles
        # and protects open positions, and anything but a ToolError there is an INCIDENT halt.
        raise ToolError(BUDGET_TAMPERED, f"registered budget cannot be hashed: {exc}") from None
    if not isinstance(stored, str) or recomputed != stored:
        raise ToolError(BUDGET_TAMPERED, "registered budget fails its self-hash")
    _validate(data)
    # The window has two legal shapes: none (every record built since PR1r) or both ends, opening
    # before it closes (a record registered before). The schema declares each end optional and
    # cannot pair them or order them, so the rest is refused here, on every read — half a window
    # must not read as "no window" and stand forever.
    window = (data.get("valid_from"), data.get("valid_until"))
    if window != (None, None) and not (
        isinstance(window[0], str) and isinstance(window[1], str) and window[0] < window[1]
    ):
        raise ToolError(
            BUDGET_INVALID,
            f"registered budget carries an unusable validity window: {window[0]} .. {window[1]} "
            "(a budget carries both ends, opening before it closes, or none)",
        )
    return data


def budget_status(root: Path | None = None, *, now: str, venue: str = VENUE_MAINNET) -> dict[str, Any]:
    """Whether a valid budget is registered right now — for the readiness board and operator.

    Fail-closed: an unverifiable budget reports ``registered`` with ``valid=False`` and names
    the error rather than reporting a comfortable "no budget".

    A windowless budget (every one registered since 2026-09-15, PR1r) is valid at any ``now``. A
    budget registered before is still held to the window it carries: outside it, ``valid`` is
    ``False`` with ``OUTSIDE_VALIDITY_WINDOW``. ``valid_from`` / ``valid_until`` come back as
    stored, ``None`` on a windowless record.

    Total: it never raises. The live leg resolves the budget before it settles or protects
    anything, so an exception escaping here would leave every open position unmanaged — which
    is why the optional window fields are read with ``.get``, never subscripted."""
    try:
        record = read_registered_budget(root, venue=venue)
    except ToolError as exc:
        return {"registered": True, "valid": False, "error": exc.reason_code, "budget_id": None}
    if record is None:
        return {"registered": False, "valid": False, "error": None, "budget_id": None}
    valid_from, valid_until = record.get("valid_from"), record.get("valid_until")
    # The read above refused half a window; the isinstance checks keep this closed without it.
    within_window = (valid_from is None and valid_until is None) or (
        isinstance(valid_from, str) and isinstance(valid_until, str) and valid_from <= now <= valid_until
    )
    return {
        "registered": True,
        "valid": within_window,
        "error": None if within_window else "OUTSIDE_VALIDITY_WINDOW",
        "budget_id": record["budget_id"],
        "venue": record["venue"],
        "symbol_allowlist": record["symbol_allowlist"],
        "caps": record["caps"],
        "valid_from": valid_from,
        "valid_until": valid_until,
        "record_sha256": record["record_sha256"],
    }


def write_registered_budget(record: Mapping[str, Any], *, root: Path | None = None,
                            venue: str = VENUE_MAINNET) -> Path:
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
    target = venue_state_dir(root, venue=venue)
    target.mkdir(parents=True, exist_ok=True)
    path = target / LIVE_BUDGET_FILENAME
    path.write_text(json.dumps(dict(record), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return path

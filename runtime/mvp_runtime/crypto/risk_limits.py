"""The registered C4 risk-breaker limits (``crypto_risk_limits.v0.1``).

The five breaker limits in ``guards.py`` were hardcoded constants: correct as a starting
posture, but not adjustable without a code change and a deploy, and not *recorded* — nothing
hashed, nothing auditable, no statement of who authorized which number or for how long. That
is the same gap `live_budget` closed for the LP3 order caps, so this module closes it the same
way, deliberately: same record shape, same verified read, same fail-closed posture.

Division of ownership, kept strict so there is one authority per question:

- **``guards.py`` owns the numbers** — the defaults AND the relaxation bounds. This module
  never decides what is safe; it reads a record and asks ``RiskLimits.problems()``.
- **This module owns the record** — its schema, its self-hash, its validity window, and the
  resolution rule below.

Three properties worth stating, because each is a decision rather than an implementation:

1. **Nothing registered = the defaults, silently and correctly.** A machine that never
   registers anything behaves exactly as it did before this module existed. That is what makes
   the whole thing safe to land: the configurable path is opt-in.
2. **A record that cannot prove itself is a REFUSAL, not a fallback to the defaults.** Falling
   back would silently *loosen* a breaker the operator had tightened — the fail-open direction,
   from a path whose whole job is to fail closed. Unparseable, hash-mismatched, out-of-bounds
   and expired all raise; the cycle turns that into no-new-position.
3. **Relaxations expire; defaults do not.** The validity window is what makes a loosened
   breaker a temporary, re-authorized decision instead of a permanent edit nobody revisits.
   Past ``valid_until`` the runtime does not know which limits are authorized, and uncertain
   → BLOCK.

Registering one **grants nothing and enables no trading.** It cannot widen a breaker past the
code bounds, and it is not a permission: the `live_trading` grant, the confirmation phrase, the
clean canary orders, the registered budget and both kill switches all still stand where they
stood. It can only move a breaker *within* limits this repo's code already accepts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from runtime.read_only_kernel import integrity
from runtime.read_only_kernel.schema_validation import RuntimeSchemaError

from ..errors import ToolError
from ..paths import repo_root as _repo_root
from ..schema_cache import validate_against_schema
from . import guards
from .guards import RiskLimits
from .live_pnl import state_dir

RISK_LIMITS_SCHEMA_VERSION = "crypto_risk_limits.v0.1"
RISK_LIMITS_SCHEMA_FILE = "crypto_risk_limits.v0.1.schema.json"
RISK_LIMITS_FILENAME = "crypto_risk_limits.json"

LIMITS_INVALID = "CRYPTO_RISK_LIMITS_INVALID"
LIMITS_UNREADABLE = "CRYPTO_RISK_LIMITS_UNREADABLE"
LIMITS_TAMPERED = "CRYPTO_RISK_LIMITS_TAMPERED"
LIMITS_EXPIRED = "CRYPTO_RISK_LIMITS_EXPIRED"

# The record's limit keys, in the order a refusal should read them.
_LIMIT_KEYS = (
    "risk_per_trade",
    "daily_max_loss_r",
    "weekly_max_loss_r",
    "max_consecutive_losses",
    "max_drawdown_pct",
)
_INTEGER_KEYS = ("max_consecutive_losses",)


def _schema_path(repo_root: Path | None = None) -> Path:
    return (repo_root if repo_root is not None else _repo_root()) / "schemas" / RISK_LIMITS_SCHEMA_FILE


def limits_path(root: Path | None = None) -> Path:
    """The per-machine registered-limits file (gitignored, beside the live outcomes)."""
    return state_dir(root) / RISK_LIMITS_FILENAME


def build_risk_limits_record(
    *,
    limits: Mapping[str, Any],
    valid_from: str,
    valid_until: str,
    registered_by: str,
    registered_at: str,
    drawdown_baseline_rebase: Mapping[str, Any] | None = None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Build a self-hashed, schema-valid risk-limits record. Fail-closed.

    Raises ``ToolError(LIMITS_INVALID)`` on a missing or non-numeric limit, a limit outside
    the ``guards`` relaxation bounds, a non-whole consecutive-loss count, a missing operator
    identity, or a validity window that does not open before it closes. Every refusal names the
    offending number — the bounds check runs through ``RiskLimits.problems()``, so the message
    is the same one the guard itself would produce, and there is no second copy of the numbers.
    """
    missing = [k for k in _LIMIT_KEYS if k not in limits]
    if missing:
        raise ToolError(LIMITS_INVALID, f"limits is missing required keys: {sorted(missing)}")

    numeric: dict[str, float] = {}
    for key in _LIMIT_KEYS:
        try:
            numeric[key] = float(limits[key])
        except (TypeError, ValueError):
            raise ToolError(LIMITS_INVALID, f"limit {key} is not a number: {limits[key]!r}") from None

    for key in _INTEGER_KEYS:
        if numeric[key] != int(numeric[key]):
            raise ToolError(LIMITS_INVALID, f"limit {key} must be a whole number, got {limits[key]!r}")

    candidate = RiskLimits(
        risk_per_trade=numeric["risk_per_trade"],
        daily_max_loss_r=numeric["daily_max_loss_r"],
        weekly_max_loss_r=numeric["weekly_max_loss_r"],
        max_consecutive_losses=int(numeric["max_consecutive_losses"]),
        max_drawdown_pct=numeric["max_drawdown_pct"],
    )
    problems = candidate.problems()
    if problems:
        raise ToolError(
            LIMITS_INVALID,
            "limits are outside the relaxation bounds (refused, never clamped): " + "; ".join(problems),
        )

    if not (isinstance(valid_from, str) and isinstance(valid_until, str) and valid_from < valid_until):
        raise ToolError(LIMITS_INVALID, f"validity window must open before it closes: {valid_from} .. {valid_until}")
    if not (isinstance(registered_by, str) and registered_by.strip()):
        raise ToolError(LIMITS_INVALID, "registered_by (operator identity) is required")

    body: dict[str, Any] = {
        "schema_version": RISK_LIMITS_SCHEMA_VERSION,
        # short_id forbids floats in its seed (non-deterministic across platforms), so the
        # limits are stringified here. The record body keeps them numeric — its self-hash uses
        # sha256_record, which does accept floats. Same split as live_budget.
        "limits_id": integrity.short_id(
            "risklimits",
            {"limits": {k: str(numeric[k]) for k in _LIMIT_KEYS}, "valid_from": valid_from,
             "valid_until": valid_until, "registered_by": registered_by.strip()},
        ),
        "limits": {
            "risk_per_trade": candidate.risk_per_trade,
            "daily_max_loss_r": candidate.daily_max_loss_r,
            "weekly_max_loss_r": candidate.weekly_max_loss_r,
            "max_consecutive_losses": candidate.max_consecutive_losses,
            "max_drawdown_pct": candidate.max_drawdown_pct,
        },
        "valid_from": valid_from,
        "valid_until": valid_until,
        "registered_by": registered_by.strip(),
        "registered_at": registered_at,
    }
    # OPTIONAL, and absent from the id seed on purpose: `limits_id` identifies the NUMBERS this
    # record sets, and two records with the same breakers but different retirement sets are the
    # same limits applied to different populations. The self-hash below covers it either way, so
    # the block is still tamper-evident — it just does not rename the record.
    if drawdown_baseline_rebase is not None:
        ids = drawdown_baseline_rebase.get("excluded_strategy_ids")
        reason = drawdown_baseline_rebase.get("reason")
        if not isinstance(ids, (list, tuple)) or not ids or not all(
            isinstance(i, str) and i.strip() for i in ids
        ):
            raise ToolError(
                LIMITS_INVALID,
                "drawdown_baseline_rebase.excluded_strategy_ids must be a non-empty list of ids",
            )
        if not (isinstance(reason, str) and reason.strip()):
            raise ToolError(
                LIMITS_INVALID,
                "drawdown_baseline_rebase.reason is required — a rebase forgets losses, and the "
                "record has to say whose and why",
            )
        deduped = sorted(dict.fromkeys(i.strip() for i in ids))
        if len(deduped) != len(ids):
            raise ToolError(
                LIMITS_INVALID,
                "drawdown_baseline_rebase.excluded_strategy_ids contains duplicates",
            )
        body["drawdown_baseline_rebase"] = {
            "excluded_strategy_ids": deduped,
            "reason": reason.strip(),
        }

    body["record_sha256"] = integrity.sha256_record(body)
    _validate(body, repo_root=repo_root)
    return body


def _validate(record: Mapping[str, Any], *, repo_root: Path | None = None) -> None:
    try:
        validate_against_schema(dict(record), _schema_path(repo_root), "crypto risk limits")
    except RuntimeSchemaError as exc:
        raise ToolError(LIMITS_INVALID, f"risk-limits record is not schema-valid: {exc}") from exc


def read_registered_limits(root: Path | None = None) -> dict[str, Any] | None:
    """The registered limits for this machine, VERIFIED — or None when none is registered.

    Missing file = honestly None (nothing registered, the guard uses its defaults). Anything
    unparseable, failing its self-hash, or not schema-valid raises: a record that cannot prove
    itself must not be allowed to authorize a breaker limit."""
    path = limits_path(root)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ToolError(LIMITS_UNREADABLE, f"registered risk limits are unreadable: {exc}") from None
    if not isinstance(data, dict):
        raise ToolError(LIMITS_UNREADABLE, "registered risk limits are not a JSON object")
    stored = data.get("record_sha256")
    body = {k: v for k, v in data.items() if k != "record_sha256"}
    if not isinstance(stored, str) or integrity.sha256_record(body) != stored:
        raise ToolError(LIMITS_TAMPERED, "registered risk limits fail their self-hash")
    _validate(data)
    return data


def limits_from_record(record: Mapping[str, Any]) -> RiskLimits:
    """A ``RiskLimits`` carrying the registered numbers, tagged with the record it came from.

    Re-checks the bounds. The record was checked when it was built and again by its schema on
    read, but this is the last step before the numbers judge a trade, and the bounds live in
    code precisely so that no record can be the only thing vouching for them."""
    values = record["limits"]
    resolved = RiskLimits(
        risk_per_trade=float(values["risk_per_trade"]),
        daily_max_loss_r=float(values["daily_max_loss_r"]),
        weekly_max_loss_r=float(values["weekly_max_loss_r"]),
        max_consecutive_losses=int(values["max_consecutive_losses"]),
        max_drawdown_pct=float(values["max_drawdown_pct"]),
        source=guards.SOURCE_REGISTERED,
        limits_id=record.get("limits_id"),
        record_sha256=record.get("record_sha256"),
        # The exclusion list rides with the numbers, because the drawdown limit and the
        # population it is measured over are one judgement — a verdict that carried the limit
        # without the baseline could not be re-checked against the history it ruled on.
        drawdown_excluded_strategy_ids=tuple(
            (record.get("drawdown_baseline_rebase") or {}).get("excluded_strategy_ids") or ()
        ),
    )
    problems = resolved.problems()
    if problems:
        raise ToolError(
            LIMITS_INVALID,
            f"registered limits {record.get('limits_id')} are outside the relaxation bounds: "
            + "; ".join(problems),
        )
    return resolved


def resolve_risk_limits(root: Path | None = None, *, now: str) -> RiskLimits:
    """The limits the C4 guard must judge on, right now. Raises rather than guessing.

    Returns the ``guards`` defaults when nothing is registered, and the registered set when a
    verified record covers ``now``. Raises ``ToolError`` when a record exists but cannot be
    used — unreadable, tampered, out of bounds, or past its window — because every one of those
    means "the authorized limits are unknown", and the caller's job is then to refuse new
    positions, not to pick a number.
    """
    record = read_registered_limits(root)
    if record is None:
        return guards.DEFAULT_RISK_LIMITS
    if not (record["valid_from"] <= now <= record["valid_until"]):
        raise ToolError(
            LIMITS_EXPIRED,
            f"registered risk limits {record['limits_id']} are outside their validity window "
            f"({record['valid_from']} .. {record['valid_until']}) at {now}; re-register to "
            "re-authorize, or delete the record to return to the defaults",
        )
    return limits_from_record(record)


def limits_status(root: Path | None = None, *, now: str) -> dict[str, Any]:
    """Whether a usable limits record is registered right now — for the readiness board.

    Fail-closed reporting: an unusable record reports ``registered`` with ``valid=False`` and
    names the error rather than reporting a comfortable "using defaults"."""
    try:
        record = read_registered_limits(root)
    except ToolError as exc:
        return {"registered": True, "valid": False, "error": exc.reason_code, "limits_id": None,
                "effective": None}
    if record is None:
        return {"registered": False, "valid": True, "error": None, "limits_id": None,
                "effective": guards.DEFAULT_RISK_LIMITS.as_record()}
    within_window = record["valid_from"] <= now <= record["valid_until"]
    try:
        effective = limits_from_record(record).as_record() if within_window else None
        error = None if within_window else LIMITS_EXPIRED
    except ToolError as exc:
        effective, error = None, exc.reason_code
    return {
        "registered": True,
        "valid": effective is not None,
        "error": error,
        "limits_id": record["limits_id"],
        "effective": effective,
        "valid_from": record["valid_from"],
        "valid_until": record["valid_until"],
        "registered_by": record["registered_by"],
        "record_sha256": record["record_sha256"],
    }


def write_registered_limits(record: Mapping[str, Any], *, root: Path | None = None) -> Path:
    """Persist a built limits record to the per-machine state dir (operator registration path).

    Re-validates and re-checks the self-hash before writing, then writes it as the single active
    limits file. ``root`` is the per-machine STATE root (where the file lands); the schema always
    resolves against the real repo root, so the two are never conflated (they differ under a
    tmp-dir test)."""
    _validate(record)
    stored = record.get("record_sha256")
    body = {k: v for k, v in record.items() if k != "record_sha256"}
    if not isinstance(stored, str) or integrity.sha256_record(body) != stored:
        raise ToolError(LIMITS_TAMPERED, "refusing to register limits that fail their self-hash")
    target = state_dir(root)
    target.mkdir(parents=True, exist_ok=True)
    path = target / RISK_LIMITS_FILENAME
    path.write_text(json.dumps(dict(record), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return path

"""Stop-slippage probe — buys the measurement sample without arming a strategy.

Thomas approved ``docs/proposals/STOP_SLIPPAGE_PROBE_V0.1.md`` §5 as proposed on
2026-08-11. The stop-slippage series (`live_pnl.stop_slippage_observations`) grows only
on live stop closes, live resumption sits behind the forward-confirmation gate, and the
confirmation clock is 34-69 weeks — so the cost model's one unmeasured constant
(`cost.DEFAULT_STOP_SLIPPAGE_BPS`) could not be measured without arming an unconfirmed
strategy. A probe breaks that cycle strategy-free: a venue-minimum market entry, a resting
stop 40 bps below it, and the EXISTING #683 settle path records `stop_slippage_bps` at
source. No new measurement code; this module only plans and judges.

**Approved parameters** (each rides in the approval content hash):

- N = 12 per batch: 3 symbols (BTCUSDT/ETHUSDT/SOLUSDT) x 2 volatility regimes
  (``atr_percentile`` above/below 0.5) x 2 repeats. Slippage is regime-dependent, so a
  sample pooled in one regime would re-distort the constant it exists to fix.
- Stop width 40 bps below entry, LONG probes only.
- Unfilled-stop timeout 120 minutes, then market close. A timeout row is **not** a
  slippage sample (`STOP_EXIT_REASONS` already excludes a time exit). 120 is an
  implementation default under the approval hash, as are the regime timeframe (4h — the
  primary live tier) and the per-probe notional ceiling below.
- Batch budget cap: worst case per probe = stop 40 bps + measured-worst slippage 23.5 bps
  + fees ~10 bps = 0.75% of notional; a batch whose N x worst case exceeds 10 USDT
  refuses at build time, and ``--fire`` refuses any single order above the per-probe
  notional the approval priced.

**Lineage-free by construction.** A probe outcome carries ``strategy_id``
``PROBE-<batch_id>`` and no candidate_id / generation / rule hash, so
`lifecycle.outcome_attribution_key` yields the ``sid:`` tier and
`forward_confirmation.lineage_keys` is empty — not one row can reach forward
confirmation or the lifecycle ladder. It stays fully visible to the daily-loss breaker
and the risk guard, which is §4-4's requirement, not a leak.

**All venue I/O lives in the operator door** (``scripts/run_slippage_probe.py``), through
the existing gated selectors the live leg uses. This module deliberately calls no
selector and reads no env var: the probe is a new consumer of the existing
``live_trading`` capability, not a new capability, so the safety-gate enumeration is
unchanged.
"""

from __future__ import annotations

import json
import os
import statistics
from pathlib import Path
from typing import Any, Iterable, Mapping

from runtime.read_only_kernel import integrity

from .. import approval as approval_mod
from .. import timeutil
from ..binding import bind_task_to_core
from ..errors import ApprovalBlocked, ToolError
from ..filelock import locked
from ..intake import build_task
from ..paths import repo_root as _repo_root
from ..permission import build_slippage_probe_permission_decision
from .live_pnl import state_dir, stop_slippage_observations
from .live_sizing import SymbolFilters, round_price_to_tick

PROBE_ACTION_TYPE = "crypto.probe.stop_slippage_batch"
PROBE_HASH_VERSION = "stop_slippage_probe.v1"
PLAN_VERSION = "stop_slippage_probe_plan.v1"
PLAN_FILENAME = "stop_slippage_probe_plan.json"

# The outcome-row marker. `lifecycle.outcome_attribution_key` resolves a probe row to
# `sid:PROBE-<batch>` (no lineage fields), which matches no pool entry; the `--status`
# board and the plan resolver select probe rows by this prefix.
PROBE_STRATEGY_PREFIX = "PROBE-"

# --- the approved batch (Thomas 2026-08-11, proposal §5) -------------------------------
PROBE_N = 12
PROBE_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
PROBE_STOP_BPS = 40.0
PROBE_DIRECTION = "LONG"          # §5-2: LONG probes only; SHORT is out of scope
PROBE_REPEATS = 2

REGIME_FEATURE = "atr_percentile"
REGIME_SPLIT = 0.5
REGIME_HIGH = "high_vol"          # atr_percentile >= 0.5
REGIME_LOW = "low_vol"            # atr_percentile <  0.5
REGIMES = (REGIME_LOW, REGIME_HIGH)
# Implementation default under the approval hash: the timeframe the regime is measured
# on. 4h is the primary live rotation tier; the split is meaningless without naming it.
REGIME_TIMEFRAME = "4h"

# Implementation default under the approval hash (§5-3 left T open; 120 minutes is the
# default this increment ships). A timeout close is a market close and NOT a sample.
PROBE_TIMEOUT_MINUTES = 120

# §3's arithmetic, kept as the approval priced it: stop 40 bps + measured-worst slippage
# 23.5 bps (REMAINING_WORK §C, 2026-08-06) + round-trip fees ~10 bps = 73.5 bps, carried
# as 0.75% so the cap errs upward. The per-probe notional ceiling is an implementation
# default under the approval hash — `--fire` refuses an order the venue minimum prices
# above it, so the approved worst case cannot be exceeded by a venue filter change.
WORST_CASE_LOSS_FRACTION = 0.0075
PER_PROBE_NOTIONAL_CAP_USDT = 100.0
PROBE_BUDGET_CAP_USDT = 10.0

# §5-5: at sample N >= 10 the interim `DEFAULT_STOP_SLIPPAGE_BPS` re-decision opens.
DECISION_SAMPLE_FLOOR = 10

# Cell statuses (closed set). EMPTY = not yet attempted (or an attempt that bought no
# sample and was returned); OPEN = a probe is at the venue right now; FILLED = the stop
# filled and the slippage row exists; TIMEOUT = closed at market after the timeout.
CELL_EMPTY = "EMPTY"
CELL_OPEN = "OPEN"
CELL_FILLED = "FILLED"
CELL_TIMEOUT = "TIMEOUT"
CELL_STATUSES = frozenset({CELL_EMPTY, CELL_OPEN, CELL_FILLED, CELL_TIMEOUT})
_TERMINAL_CELL_STATUSES = frozenset({CELL_FILLED, CELL_TIMEOUT})

PLAN_ACTIVE = "ACTIVE"
PLAN_COMPLETE = "COMPLETE"
PLAN_STATUSES = frozenset({PLAN_ACTIVE, PLAN_COMPLETE})

# Refusal codes. Every refusal on the probe path is one of these, stable by name.
PROBE_PARAMS_INVALID = "PROBE_PARAMS_INVALID"
PROBE_BUDGET_EXCEEDED = "PROBE_BUDGET_EXCEEDED"
PROBE_PLAN_MISSING = "PROBE_PLAN_MISSING"
PROBE_PLAN_UNREADABLE = "PROBE_PLAN_UNREADABLE"
PROBE_PLAN_TAMPERED = "PROBE_PLAN_TAMPERED"
PROBE_PLAN_INVALID = "PROBE_PLAN_INVALID"
PROBE_PLAN_NOT_ACTIVE = "PROBE_PLAN_NOT_ACTIVE"
PROBE_PLAN_EXISTS = "PROBE_PLAN_EXISTS"
PROBE_CELL_OPEN = "PROBE_CELL_OPEN"
PROBE_BATCH_EXHAUSTED = "PROBE_BATCH_EXHAUSTED"
PROBE_REGIME_EXHAUSTED = "PROBE_REGIME_EXHAUSTED"
PROBE_REGIME_UNREADABLE = "PROBE_REGIME_UNREADABLE"
PROBE_LIVE_TRADING_OFF = "PROBE_LIVE_TRADING_OFF"
PROBE_ACCOUNT_UNREADABLE = "PROBE_ACCOUNT_UNREADABLE"
PROBE_DAILY_LOSS_BREAKER = "PROBE_DAILY_LOSS_BREAKER"
PROBE_BRACKET_BREAKER = "PROBE_BRACKET_BREAKER"
PROBE_RISK_GUARD_BLOCKED = "PROBE_RISK_GUARD_BLOCKED"
PROBE_GUARD_REFUSED = "PROBE_GUARD_REFUSED"
PROBE_FILTERS_UNAVAILABLE = "PROBE_FILTERS_UNAVAILABLE"
PROBE_PRICE_UNREADABLE = "PROBE_PRICE_UNREADABLE"
PROBE_NOTIONAL_ABOVE_PLAN = "PROBE_NOTIONAL_ABOVE_PLAN"
PROBE_ENTRY_NOT_CONFIRMED = "PROBE_ENTRY_NOT_CONFIRMED"
PROBE_STOP_NOT_PLACED = "PROBE_STOP_NOT_PLACED"
PROBE_UNSETTLED = "PROBE_UNSETTLED"

# The closed key sets `validate_plan` holds records to. Additive fields are a plan
# version bump, never a silent widening — an unknown key is indistinguishable from
# tampering and reads as such.
_PARAM_KEYS = frozenset({
    "n", "symbols", "direction", "stop_bps", "regime_feature", "regime_split",
    "regime_timeframe", "repeats", "timeout_minutes", "worst_case_loss_fraction",
    "per_probe_notional_cap_usdt", "budget_cap_usdt",
})
_CELL_KEYS = frozenset({
    "symbol", "regime", "repeat", "status", "opened_at", "updated_at",
    "position_id", "entry_client_order_id", "outcome_id", "close_reason",
    "stop_slippage_bps", "note",
})
_PLAN_KEYS = frozenset({
    "plan_version", "batch_id", "params", "cells", "approval_id", "status",
    "created_at", "updated_at", "record_sha256",
})


# --- params + identity -----------------------------------------------------------------

def build_batch_params(
    *,
    n: int = PROBE_N,
    symbols: Iterable[str] = PROBE_SYMBOLS,
    stop_bps: float = PROBE_STOP_BPS,
    repeats: int = PROBE_REPEATS,
    timeout_minutes: int = PROBE_TIMEOUT_MINUTES,
    per_probe_notional_cap_usdt: float = PER_PROBE_NOTIONAL_CAP_USDT,
    budget_cap_usdt: float = PROBE_BUDGET_CAP_USDT,
) -> dict[str, Any]:
    """The one approved batch shape, validated and budget-checked. Pure.

    ``n`` must equal the grid product — the approval is for the 3x2x2 grid, and an ``n``
    that disagrees with its own grid would make the hash describe a batch the cells do
    not. The regime rule constants ride in the params so the approval hash pins them:
    moving the split or the timeframe later mints a different batch.
    """
    names = sorted({str(s).strip().upper() for s in symbols if str(s).strip()})
    if not names:
        raise ToolError(PROBE_PARAMS_INVALID, "a probe batch needs at least one symbol")
    if int(repeats) < 1 or int(timeout_minutes) < 1:
        raise ToolError(PROBE_PARAMS_INVALID, "repeats and timeout_minutes must be positive")
    if not (float(stop_bps) > 0):
        raise ToolError(PROBE_PARAMS_INVALID, "stop_bps must be positive")
    grid = len(names) * len(REGIMES) * int(repeats)
    if int(n) != grid:
        raise ToolError(
            PROBE_PARAMS_INVALID,
            f"n={n} disagrees with the {len(names)} symbols x {len(REGIMES)} regimes x "
            f"{repeats} repeats grid ({grid})",
        )
    if not (float(per_probe_notional_cap_usdt) > 0 and float(budget_cap_usdt) > 0):
        raise ToolError(PROBE_PARAMS_INVALID, "the notional and budget caps must be positive")
    params = {
        "n": int(n),
        "symbols": names,
        "direction": PROBE_DIRECTION,
        "stop_bps": float(stop_bps),
        "regime_feature": REGIME_FEATURE,
        "regime_split": REGIME_SPLIT,
        "regime_timeframe": REGIME_TIMEFRAME,
        "repeats": int(repeats),
        "timeout_minutes": int(timeout_minutes),
        "worst_case_loss_fraction": WORST_CASE_LOSS_FRACTION,
        "per_probe_notional_cap_usdt": float(per_probe_notional_cap_usdt),
        "budget_cap_usdt": float(budget_cap_usdt),
    }
    assert_batch_budget(params)
    return params


def worst_case_loss_usdt(params: Mapping[str, Any]) -> float:
    """N x per-probe ceiling x §3's worst-case fraction — what the batch can lose."""
    return round(
        int(params["n"])
        * float(params["per_probe_notional_cap_usdt"])
        * float(params["worst_case_loss_fraction"]),
        8,
    )


def assert_batch_budget(params: Mapping[str, Any]) -> None:
    """Refuse a batch whose priced worst case exceeds its own cap."""
    worst = worst_case_loss_usdt(params)
    cap = float(params["budget_cap_usdt"])
    if worst > cap:
        raise ToolError(
            PROBE_BUDGET_EXCEEDED,
            f"batch worst case {worst} USDT exceeds the {cap} USDT cap "
            f"({params['n']} probes x {params['per_probe_notional_cap_usdt']} USDT x "
            f"{params['worst_case_loss_fraction']})",
        )


def probe_content_sha256(params: Mapping[str, Any]) -> str:
    """The material identity of one batch. Any parameter change mints a different hash —
    and therefore a different approval. ``sha256_record`` (not ``sha256_value``) because
    the params legitimately carry JSON numbers; the record hash canonicalizes with sorted
    keys, so insertion order cannot mint a second identity for the same batch."""
    return integrity.sha256_record({
        "hash_version": PROBE_HASH_VERSION,
        "params": {key: params[key] for key in sorted(_PARAM_KEYS)},
    })


def batch_id_of(params: Mapping[str, Any]) -> str:
    # Seeded from the content hash string rather than the raw params: `short_id`'s
    # canonical form rejects floats, and the hash already IS the canonical identity.
    return integrity.short_id("probe_batch", {"content_sha256": probe_content_sha256(params)})


def probe_strategy_id(batch_id: str) -> str:
    return f"{PROBE_STRATEGY_PREFIX}{batch_id}"


def regime_of(atr_percentile: Any) -> str:
    """Which half of the split a measured ``atr_percentile`` falls in. Fail-closed:
    an absent or non-numeric reading refuses rather than defaulting to either regime —
    a probe filed under a guessed regime distorts exactly the balance the grid buys."""
    if isinstance(atr_percentile, bool) or not isinstance(atr_percentile, (int, float)):
        raise ToolError(
            PROBE_REGIME_UNREADABLE,
            f"{REGIME_FEATURE} is not a number ({atr_percentile!r}); the regime cannot be judged",
        )
    return REGIME_HIGH if float(atr_percentile) >= REGIME_SPLIT else REGIME_LOW


# --- the plan store --------------------------------------------------------------------

def _empty_cell(symbol: str, regime: str, repeat: int) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "regime": regime,
        "repeat": repeat,
        "status": CELL_EMPTY,
        "opened_at": None,
        "updated_at": None,
        "position_id": None,
        "entry_client_order_id": None,
        "outcome_id": None,
        "close_reason": None,
        "stop_slippage_bps": None,
        "note": None,
    }


def build_cells(params: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        _empty_cell(symbol, regime, repeat)
        for symbol in params["symbols"]
        for regime in REGIMES
        for repeat in range(1, int(params["repeats"]) + 1)
    ]


def build_plan(params: Mapping[str, Any], *, approval_id: str, now: str) -> dict[str, Any]:
    plan = {
        "plan_version": PLAN_VERSION,
        "batch_id": batch_id_of(params),
        "params": {key: params[key] for key in sorted(_PARAM_KEYS)},
        "cells": build_cells(params),
        "approval_id": approval_id,
        "status": PLAN_ACTIVE,
        "created_at": now,
        "updated_at": now,
    }
    plan["record_sha256"] = _plan_sha256(plan)
    return plan


def _plan_sha256(plan: Mapping[str, Any]) -> str:
    return integrity.sha256_record({k: v for k, v in plan.items() if k != "record_sha256"})


def plan_path(root: Path | None = None) -> Path:
    return state_dir(root) / PLAN_FILENAME


def validate_plan(plan: Any) -> dict[str, Any]:
    """Hold a plan to its closed shape, or fail closed. Returns the plan.

    Beyond key/enum checks it re-derives ``batch_id`` from the params: a plan whose id
    and params disagree is a plan whose approval binding cannot be trusted, whatever its
    self-hash says.
    """
    if not isinstance(plan, dict):
        raise ToolError(PROBE_PLAN_INVALID, "probe plan is not an object")
    unknown = set(plan) - _PLAN_KEYS
    missing = _PLAN_KEYS - set(plan)
    if unknown or missing:
        raise ToolError(
            PROBE_PLAN_INVALID,
            f"probe plan keys are not the closed set (unknown={sorted(unknown)}, "
            f"missing={sorted(missing)})",
        )
    if plan["plan_version"] != PLAN_VERSION:
        raise ToolError(PROBE_PLAN_INVALID, f"unknown plan_version {plan['plan_version']!r}")
    if plan["status"] not in PLAN_STATUSES:
        raise ToolError(PROBE_PLAN_INVALID, f"unknown plan status {plan['status']!r}")
    params = plan["params"]
    if not isinstance(params, dict) or set(params) != _PARAM_KEYS:
        raise ToolError(PROBE_PLAN_INVALID, "probe plan params are not the closed set")
    if plan["batch_id"] != batch_id_of(params):
        raise ToolError(PROBE_PLAN_INVALID, "probe plan batch_id does not derive from its params")
    cells = plan["cells"]
    if not isinstance(cells, list) or len(cells) != int(params["n"]):
        raise ToolError(PROBE_PLAN_INVALID, "probe plan cell count disagrees with params.n")
    for index, cell in enumerate(cells):
        if not isinstance(cell, dict) or set(cell) != _CELL_KEYS:
            raise ToolError(PROBE_PLAN_INVALID, f"cell {index} keys are not the closed set")
        if cell["status"] not in CELL_STATUSES:
            raise ToolError(PROBE_PLAN_INVALID, f"cell {index} status {cell['status']!r} is unknown")
        if cell["symbol"] not in params["symbols"] or cell["regime"] not in REGIMES:
            raise ToolError(PROBE_PLAN_INVALID, f"cell {index} names a symbol/regime outside the grid")
    return plan


def read_plan(root: Path | None = None) -> dict[str, Any] | None:
    """The stored plan, verified, or None when none has been confirmed yet.

    Fail-closed on everything else: an unreadable or tampered plan must not answer
    "which cell may fire" — every caller of this store is about to touch real money.
    """
    path = plan_path(root)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ToolError(PROBE_PLAN_UNREADABLE, f"probe plan unreadable: {type(exc).__name__}") from exc
    if not isinstance(raw, dict):
        raise ToolError(PROBE_PLAN_UNREADABLE, "probe plan is not an object")
    stored = raw.get("record_sha256")
    if not isinstance(stored, str) or _plan_sha256(raw) != stored:
        raise ToolError(PROBE_PLAN_TAMPERED, "probe plan fails its self-hash")
    return validate_plan(raw)


def write_plan(plan: Mapping[str, Any], root: Path | None = None) -> dict[str, Any]:
    """Persist the plan atomically (lock + tmp + fsync + replace), re-stamping its hash.

    Validates before writing — a plan this module cannot read back must never land."""
    body = dict(plan)
    body["record_sha256"] = _plan_sha256(body)
    validate_plan(body)
    target = state_dir(root)
    target.mkdir(parents=True, exist_ok=True)
    path = plan_path(root)
    with locked(path.with_suffix(".lock"), code="PROBE_PLAN_LOCKED", label="probe plan"):
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(body, ensure_ascii=False, indent=1))
            handle.flush()
            os.fsync(handle.fileno())
        tmp.replace(path)
    return body


# --- cell selection + progress ---------------------------------------------------------

def open_cell_index(plan: Mapping[str, Any]) -> int | None:
    for index, cell in enumerate(plan["cells"]):
        if cell["status"] == CELL_OPEN:
            return index
    return None


def select_cell(plan: Mapping[str, Any], *, symbol: str, regime: str) -> int:
    """The index of the EMPTY cell this probe would fill, or a typed refusal.

    One probe at a time (an OPEN cell refuses), the batch must not be exhausted, and the
    measured regime must match a cell that still needs it — firing into a full
    (symbol, regime) pair would over-weight it and starve its sibling.
    """
    if plan["status"] != PLAN_ACTIVE:
        raise ToolError(PROBE_PLAN_NOT_ACTIVE, f"probe plan is {plan['status']}, not {PLAN_ACTIVE}")
    opened = open_cell_index(plan)
    if opened is not None:
        cell = plan["cells"][opened]
        raise ToolError(
            PROBE_CELL_OPEN,
            f"cell {opened} ({cell['symbol']} {cell['regime']} #{cell['repeat']}) is OPEN "
            f"(position {cell['position_id']}); one probe at a time",
        )
    empty = [i for i, c in enumerate(plan["cells"]) if c["status"] == CELL_EMPTY]
    if not empty:
        raise ToolError(PROBE_BATCH_EXHAUSTED, "every cell in this batch is FILLED or TIMEOUT")
    symbol = str(symbol).strip().upper()
    for index in empty:
        cell = plan["cells"][index]
        if cell["symbol"] == symbol and cell["regime"] == regime:
            return index
    raise ToolError(
        PROBE_REGIME_EXHAUSTED,
        f"no EMPTY cell for {symbol} in the {regime} regime; wait for the other regime "
        "or probe another symbol",
    )


def mark_cell(
    plan: Mapping[str, Any], index: int, *, status: str, now: str, **fields: Any
) -> dict[str, Any]:
    """A copy of the plan with one cell moved. Legal moves only: EMPTY->OPEN, and
    OPEN->{FILLED, TIMEOUT, EMPTY} (EMPTY is the return path for an attempt that bought
    no sample). Completing the last cell completes the plan."""
    cells = [dict(c) for c in plan["cells"]]
    if not (0 <= index < len(cells)):
        raise ToolError(PROBE_PLAN_INVALID, f"cell index {index} is outside the plan")
    current = cells[index]["status"]
    legal = {CELL_EMPTY: {CELL_OPEN}, CELL_OPEN: {CELL_FILLED, CELL_TIMEOUT, CELL_EMPTY}}
    if status not in legal.get(current, set()):
        raise ToolError(PROBE_PLAN_INVALID, f"cell {index} cannot move {current} -> {status}")
    unknown = set(fields) - _CELL_KEYS
    if unknown:
        raise ToolError(PROBE_PLAN_INVALID, f"unknown cell fields {sorted(unknown)}")
    cells[index].update(fields)
    cells[index]["status"] = status
    cells[index]["updated_at"] = now
    if status == CELL_EMPTY:
        # The slot is free again: identity fields from the failed attempt must not leak
        # into the next one. The note stays — it is why the slot came back.
        for key in ("opened_at", "position_id", "entry_client_order_id",
                    "outcome_id", "close_reason", "stop_slippage_bps"):
            cells[index][key] = None
    updated = {**plan, "cells": cells, "updated_at": now}
    if all(c["status"] in _TERMINAL_CELL_STATUSES for c in cells):
        updated["status"] = PLAN_COMPLETE
    updated["record_sha256"] = _plan_sha256({k: v for k, v in updated.items() if k != "record_sha256"})
    return updated


def resolve_open_cell(
    plan: Mapping[str, Any],
    *,
    outcomes: Iterable[Mapping[str, Any]],
    position_open: bool,
    now: str,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Reconcile an OPEN cell against the ledger, so a crashed ``--fire`` cannot wedge
    the batch. Returns ``(plan, resolution | None)``; the plan is unchanged when there is
    nothing to resolve.

    While the position is still on the book the cell is honestly OPEN — the scheduler's
    live leg or a resumed ``--fire`` will finish it — and this resolves nothing. Once the
    book is clear, the ledger says how it ended: a ``stop_loss`` close with a measured
    ``stop_slippage_bps`` is the sample (FILLED); a ``time_exit`` is the timeout path
    (TIMEOUT); anything else — a naked close, an external close, or no row at all — bought
    no sample and returns the slot to EMPTY with the reason on the cell.
    """
    index = open_cell_index(plan)
    if index is None or position_open:
        return dict(plan), None
    cell = plan["cells"][index]
    row = next(
        (o for o in outcomes
         if isinstance(o, Mapping) and o.get("position_id")
         and o.get("position_id") == cell.get("position_id")),
        None,
    )
    if row is None:
        updated = mark_cell(plan, index, status=CELL_EMPTY, now=now,
                            note="no position on the book and no outcome in the ledger")
        return updated, {"index": index, "status": CELL_EMPTY, "outcome_id": None}
    bps = row.get("stop_slippage_bps")
    close_reason = row.get("close_reason")
    if close_reason == "stop_loss" and isinstance(bps, (int, float)) and not isinstance(bps, bool):
        status = CELL_FILLED
    elif close_reason == "time_exit":
        status = CELL_TIMEOUT
    else:
        updated = mark_cell(plan, index, status=CELL_EMPTY, now=now,
                            note=f"closed as {close_reason!r}; not a slippage sample")
        return updated, {"index": index, "status": CELL_EMPTY, "outcome_id": row.get("outcome_id")}
    updated = mark_cell(
        plan, index, status=status, now=now,
        outcome_id=row.get("outcome_id"), close_reason=close_reason,
        stop_slippage_bps=float(bps) if status == CELL_FILLED else None,
    )
    return updated, {"index": index, "status": status, "outcome_id": row.get("outcome_id")}


# --- sizing + prices (pure venue arithmetic) -------------------------------------------

def probe_quantity(filters: SymbolFilters, price: float) -> tuple[float, float]:
    """The smallest venue-legal quantity at this price: the venue minimum, exactly.

    Ceil to the lot step — a probe rounds UP where a strategy order rounds down, because
    its notional target IS the venue floor and flooring would refuse every probe. Returns
    ``(quantity, notional)``.
    """
    if filters is None or not filters.valid():
        raise ToolError(PROBE_FILTERS_UNAVAILABLE, "no usable venue filters; cannot size a probe")
    if not (isinstance(price, (int, float)) and not isinstance(price, bool) and price > 0):
        raise ToolError(PROBE_PRICE_UNREADABLE, "no usable reference price; cannot size a probe")
    step = filters.step_size
    needed = max(filters.min_qty, filters.min_notional / float(price))
    steps = int(needed / step)
    quantity = round(steps * step, 12)
    # Float division can land exactly on a step or a hair under; top up until both venue
    # minimums genuinely hold on the rounded figure.
    while quantity < filters.min_qty or round(quantity * float(price), 8) < filters.min_notional:
        steps += 1
        quantity = round(steps * step, 12)
    return quantity, round(quantity * float(price), 8)


def probe_stop_price(fill_price: float, tick_size: float, *, stop_bps: float = PROBE_STOP_BPS) -> float:
    """The resting stop trigger: ``stop_bps`` below the ACTUAL fill, rounded down to the
    venue tick. Down, so the width can only widen by under one tick — never narrow, which
    would understate the stop distance the budget was priced on."""
    if not (isinstance(fill_price, (int, float)) and not isinstance(fill_price, bool) and fill_price > 0):
        raise ToolError(PROBE_PRICE_UNREADABLE, "no fill price to hang a stop on")
    trigger = round_price_to_tick(float(fill_price) * (1.0 - float(stop_bps) / 10000.0), tick_size, mode="down")
    if trigger <= 0:
        raise ToolError(PROBE_FILTERS_UNAVAILABLE, "no usable price tick; cannot round the stop trigger")
    return trigger


# --- the R9 ask / verify / confirm (mirrors crypto/promotion.py) -----------------------

def request_probe_batch(
    *,
    params: Mapping[str, Any] | None = None,
    now: str | None = None,
    ttl_minutes: int | None = None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Build the records that ASK Thomas for one probe batch. Performs nothing.

    Returns ``{"params", "batch_id", "task", "binding", "bound_task",
    "permission_decision", "approval_request", "content_sha256"}``; the operator door
    persists the decision and request to the approval store and audits the ask, exactly
    as the promotion script does."""
    now = now or timeutil.utc_now_iso()
    root = repo_root if repo_root is not None else _repo_root()
    batch = dict(params) if params is not None else build_batch_params()
    assert_batch_budget(batch)
    content = probe_content_sha256(batch)
    batch_id = batch_id_of(batch)
    task = build_task(
        f"스톱 슬리피지 프로브 배치 승인 요청: {batch['n']}회 "
        f"({', '.join(batch['symbols'])} x {'/'.join(REGIMES)} x {batch['repeats']}), "
        f"손절 {batch['stop_bps']}bps, 최악 손실 상한 {worst_case_loss_usdt(batch)} USDT",
        now=now, channel="manual", requester_type="real_thomas", requester_id="Thomas",
        authenticated=True, repo_root=root,
    )
    binding, bound = bind_task_to_core(task, repo_root=root, now=now)
    permission_decision = build_slippage_probe_permission_decision(
        bound, batch_id=batch_id, params=batch, content_sha256=content,
        now=now, repo_root=root,
    )
    approval_request = approval_mod.build_approval_request(
        permission_decision, now=now, ttl_minutes=ttl_minutes, repo_root=root,
    )
    return {
        "params": batch,
        "batch_id": batch_id,
        "task": task,
        "binding": binding,
        "bound_task": bound,
        "permission_decision": permission_decision,
        "approval_request": approval_request,
        "content_sha256": content,
    }


def verify_probe_approval(
    approval: Mapping[str, Any] | None,
    *,
    params: Mapping[str, Any],
    now: str | None = None,
) -> dict[str, Any]:
    """Verify an approval authorizes EXACTLY this batch, or fail closed.

    The promotion door's checks, reason code for reason code: the record exists, is
    APPROVED, is inside its validity window, snapshots this action type, and its content
    hash matches the batch re-derived from the params being confirmed. Verified, never
    consumed — the pre-R10 posture RUNTIME_GOVERNANCE keeps."""
    now = now or timeutil.utc_now_iso()
    if approval is None:
        raise ApprovalBlocked("APPROVAL_MISSING", "no approval record with that id")
    status = approval.get("status")
    if status != "APPROVED":
        raise ApprovalBlocked("APPROVAL_NOT_APPROVED", f"approval status is {status}, not APPROVED")
    expires_at = (approval.get("validity") or {}).get("expires_at")
    if not isinstance(expires_at, str) or timeutil.parse_iso(expires_at) <= timeutil.parse_iso(now):
        raise ApprovalBlocked("APPROVAL_EXPIRED", "the approval's validity window has passed")
    snapshot = approval.get("approved_action_snapshot") or {}
    if snapshot.get("action_type") != PROBE_ACTION_TYPE:
        raise ApprovalBlocked(
            "APPROVAL_WRONG_ACTION",
            f"approval snapshots {snapshot.get('action_type')!r}, not a slippage probe batch",
        )
    if snapshot.get("content_sha256") != probe_content_sha256(params):
        raise ApprovalBlocked(
            "APPROVAL_CONTENT_MISMATCH",
            "the approval binds a different batch (N, symbols, stop width, regime rule, "
            "timeout, or budget cap changed)",
        )
    return dict(approval)


def confirm_probe_batch(
    approval: Mapping[str, Any] | None,
    *,
    params: Mapping[str, Any] | None = None,
    root: Path | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    """Write the approved plan store. Places NO orders.

    Refuses while an ACTIVE plan exists: one batch at a time is what the budget cap and
    the one-open-cell rule are stated over. A COMPLETE plan is replaced — its evidence
    lives in the ledger, not in this file."""
    now = now or timeutil.utc_now_iso()
    batch = dict(params) if params is not None else build_batch_params()
    assert_batch_budget(batch)
    verified = verify_probe_approval(approval, params=batch, now=now)
    existing = read_plan(root)
    if existing is not None and existing["status"] == PLAN_ACTIVE:
        raise ToolError(
            PROBE_PLAN_EXISTS,
            f"plan {existing['batch_id']} is still ACTIVE; finish or resolve it before "
            "confirming another batch",
        )
    plan = build_plan(batch, approval_id=str(verified.get("approval_id")), now=now)
    return write_plan(plan, root)


# --- the sample + §5-5 readiness -------------------------------------------------------

def probe_outcome_rows(outcomes: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The ledger rows this probe wrote, by the strategy-id marker."""
    return [
        dict(o) for o in outcomes
        if isinstance(o, Mapping)
        and str(o.get("strategy_id") or "").startswith(PROBE_STRATEGY_PREFIX)
    ]


def probe_slippage_sample(outcomes: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The probe's contribution to the stop-slippage series, through the SAME reader the
    re-decision uses — no second definition of what counts as a sample."""
    return stop_slippage_observations(probe_outcome_rows(outcomes))


def decision_readiness(observations: list[Mapping[str, Any]]) -> dict[str, Any]:
    """§5-5, as a report: at N >= 10 the interim constant's re-decision opens, and these
    are the figures it would read. ``observations`` is the FULL recorded sample
    (`stop_slippage_observations` over the whole ledger) — the probe buys rows for that
    series, it does not own a private one. Reports only; nothing here rewrites
    `cost.DEFAULT_STOP_SLIPPAGE_BPS` (a held PR owns that constant)."""
    from . import cost  # local: cost imports live_pnl labels; keep this module light to import

    values = sorted(
        float(o["stop_slippage_bps"]) for o in observations
        if isinstance(o.get("stop_slippage_bps"), (int, float))
        and not isinstance(o.get("stop_slippage_bps"), bool)
    )
    ready = len(values) >= DECISION_SAMPLE_FLOOR
    quartile: float | None = None
    if len(values) >= 4:
        quartile = round(statistics.quantiles(values, n=4)[2], 4)
    return {
        "sample_n": len(values),
        "decision_floor": DECISION_SAMPLE_FLOOR,
        "ready": ready,
        "median_bps": round(statistics.median(values), 4) if values else None,
        "upper_quartile_bps": quartile,
        "interim_default_bps": cost.DEFAULT_STOP_SLIPPAGE_BPS,
    }

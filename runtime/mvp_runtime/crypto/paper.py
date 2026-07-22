"""C5 paper position kernel — entry routing, simulated settlement, gated state.

Ports the source system's runtime entry router (``entry_strategy_router_agent.py``,
single-symbol form) and canonical paper position kernel
(``execution/paper_position_kernel.py``) onto this runtime's R8 pattern. The pure
parts (routing, entry plan, settlement math, outcome records) run at ALLOW tier; the
**only** effectful step — persisting paper state — is EXECUTE_AND_REPORT behind a new
``paper_trading`` safety-flag provider on the existing ``filesystem_write`` flag:
:class:`DryRunPaperStore` is the default (computes everything, persists nothing), the
real store is constructed solely through ``safety_gate.select_gated`` and re-asserts
its authorization at every mutating call, and the chokepoint :func:`run_paper_update`
is kill-switch bound (``kill_blocks: tool_write``) exactly like R8's ``run_write``.

Source rules kept verbatim: one order per cycle no matter how many strategies agree
(supporting ids ride along for attribution); same-symbol direction conflicts fail
closed (``BLOCK_STRATEGY_DIRECTION_CONFLICT``) rather than guessing; suspended and
archived strategies cannot open positions; settlement precedence is manual exit →
intrabar SL/TP (**pessimistic SL-first**) → time exit; ``holding_candles`` advances
once per distinct candle; the outcome's ``result_R`` is the entry-to-exit move over
the entry risk. Accounting is R-based only (no quantity/pnl fields) — R is what the
C4 risk guard and C6 feedback consume, and paper sizing added nothing but noise.

Paper state lives under the runtime's own gitignored governance-state directory —
private runtime state like the ledger, deliberately NOT the R8 ``workspace/`` (whose
create-only rule fits deliverables, not a position file that updates every cycle).
Reads (open position, outcome history) are ungated ALLOW-tier module functions; an
unreadable outcome history raises so the caller routes it into
``guards.risk_guard_unreadable`` — never silently an empty history.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Mapping, Protocol

from runtime.read_only_kernel import integrity

from .. import safety_gate, timeutil
from ..control import ControlStore
from ..errors import ToolBlocked, ToolError
from ..filelock import locked
from ..paths import repo_root as _repo_root
from ..safety_gate import FILESYSTEM_WRITE, Authorization
from .strategy import StrategySpec, evaluate_spec

PAPER_TOOL_ID = "crypto.paper.kernel"
PAPER_TOOL_VERSION = "0.1.0"
PAPER_TOOL_CLASS = "write"

PAPER_ENV = "MVP_PAPER_TRADING"
REAL_PAPER = "real"
PAPER_PROVIDER_ID = "paper_trading"
_WRITE_FLAGS = (FILESYSTEM_WRITE,)

STATE_REL = ".runtime_governance_state/crypto"
POSITION_FILENAME = "paper_position.json"
OUTCOMES_FILENAME = "paper_outcomes.jsonl"

# Router statuses/rules (source S7).
STATUS_ENTRY_CANDIDATE = "ENTRY_CANDIDATE"
STATUS_NO_ENTRY = "NO_ENTRY"
STATUS_BLOCKED = "BLOCKED"
BLOCK_DIRECTION_CONFLICT = "BLOCK_STRATEGY_DIRECTION_CONFLICT"
POSITION_CONTEXT_MISMATCH = "POSITION_CONTEXT_MISMATCH"
SETTLEMENT_ALREADY_RECORDED = "SETTLEMENT_ALREADY_RECORDED"
SETTLEMENT_UNVERIFIABLE = "SETTLEMENT_UNVERIFIABLE"
OCCUPYING_STATUSES = frozenset({"PAPER_ACTIVE", "WARNING", "PROBATION"})

# Kernel settlement limits (source paper_position_kernel; timeframes outside the
# table — e.g. 1d — use the default, the source runtime's own behavior).
MAX_HOLD_BARS = {"15m": 96, "1h": 48, "4h": 30}
DEFAULT_MAX_HOLD_BARS = 48

PAPER_KERNEL_VERSION = "paper_position_kernel.v1"  # source-compatible marker


# --- entry routing (pure) -----------------------------------------------------

def route_entries(
    pool: Mapping[str, Any], feature_row: Mapping[str, Any], *, symbol: str, timeframe: str, now: str
) -> dict[str, Any]:
    """Evaluate every routable pool strategy against this cycle's feature row.

    A spec scoped to a different (symbol, timeframe) is recorded as unevaluable and
    cannot match — an ETH daily strategy is never judged on a BTC hourly row (the
    source's ``feature_rows`` rule, single-snapshot form). The router only proposes.
    """
    entries = [
        e for e in (pool.get("active_strategies") or [])
        if e.get("status") in OCCUPYING_STATUSES and e.get("strategy_spec")
    ]
    evaluations: list[dict[str, Any]] = []
    matches: list[dict[str, Any]] = []

    for entry in entries:
        spec = StrategySpec.from_dict(entry["strategy_spec"])
        spec_symbol = str(spec.symbol_scope[0]) if spec.symbol_scope else ""
        if spec_symbol != symbol or spec.timeframe != timeframe or not feature_row:
            evaluations.append({
                "strategy_id": entry.get("strategy_id"),
                "matched": False,
                "direction": None,
                "unevaluable": f"no feature row for {spec_symbol} {spec.timeframe}",
            })
            continue
        result = evaluate_spec(spec, feature_row)
        evaluations.append({
            "strategy_id": entry.get("strategy_id"),
            "matched": result.matched,
            "direction": result.direction,
        })
        if result.matched:
            matches.append({
                "strategy_id": entry.get("strategy_id"),
                "strategy_rule_hash": entry.get("strategy_rule_hash"),
                "strategy_generation_id": entry.get("generation_id") or entry.get("strategy_spec", {}).get("generation_id"),
                "direction": result.direction,
                "champion_score": entry.get("champion_score"),
                "spec": spec,
            })

    base = {
        "strategies_evaluated": len(entries),
        "evaluations": evaluations,
        "matched_strategy_ids": sorted(m["strategy_id"] for m in matches if m["strategy_id"] is not None),
        "created_at_utc": now,
        "direction": None,
    }
    if not matches:
        return {**base, "status": STATUS_NO_ENTRY}

    directions = {m["direction"] for m in matches}
    if len(directions) > 1:
        # Single-symbol cycle: a LONG and a SHORT on the same row fail closed.
        return {
            **base,
            "status": STATUS_BLOCKED,
            "block_reason": BLOCK_DIRECTION_CONFLICT,
            "conflicting_directions": sorted(d for d in directions if d),
        }

    ranked = sorted(
        matches,
        key=lambda m: (
            m["champion_score"] if m["champion_score"] is not None else -math.inf,
            m["strategy_id"] or "",
        ),
        reverse=True,
    )
    primary = ranked[0]
    return {
        **base,
        "status": STATUS_ENTRY_CANDIDATE,
        "direction": primary["direction"],
        "primary_strategy_id": primary["strategy_id"],
        "primary_strategy_rule_hash": primary["strategy_rule_hash"],
        "primary_strategy_generation_id": primary["strategy_generation_id"],
        "primary_spec": primary["spec"],
        "supporting_strategy_ids": [m["strategy_id"] for m in ranked[1:]],
    }


def build_entry_plan(route: Mapping[str, Any], feature_row: Mapping[str, Any], *, now: str) -> dict[str, Any] | None:
    """Turn an ENTRY_CANDIDATE route into a concrete trade plan — pure, no I/O.

    Entry at the row's close; ATR stop/target from the winning spec's exit rules;
    risk = |entry - stop| = stop_atr * ATR. Returns None (no plan, honestly) when the
    route is not a candidate or the row's close/ATR is indeterminate — a plan is
    never built on data the features could not price."""
    if route.get("status") != STATUS_ENTRY_CANDIDATE:
        return None
    spec: StrategySpec = route["primary_spec"]
    entry = feature_row.get("close")
    atr = feature_row.get("atr")
    if not isinstance(entry, (int, float)) or not isinstance(atr, (int, float)) or entry <= 0 or atr <= 0:
        return None
    direction = route["direction"]
    stop_distance = spec.exit_rules.stop_atr * atr
    target_distance = spec.exit_rules.target_atr * atr
    if direction == "LONG":
        stop, target = entry - stop_distance, entry + target_distance
    else:
        stop, target = entry + stop_distance, entry - target_distance
    return {
        "symbol": str(spec.symbol_scope[0]) if spec.symbol_scope else "",
        "timeframe": spec.timeframe,
        "direction": direction,
        "entry_price": float(entry),
        "stop_loss": float(stop),
        "take_profit": float(target),
        "risk": abs(float(entry) - float(stop)),
        "strategy_id": route.get("primary_strategy_id"),
        "strategy_rule_hash": route.get("primary_strategy_rule_hash"),
        "strategy_generation_id": route.get("primary_strategy_generation_id"),
        "supporting_strategy_ids": list(route.get("supporting_strategy_ids") or []),
        "created_at_utc": now,
    }


def open_position(plan: Mapping[str, Any], *, now: str) -> dict[str, Any]:
    """The position dict an entry plan opens — pure (source ``build_position`` shape)."""
    position = {
        "position_kernel_version": PAPER_KERNEL_VERSION,
        "status": "OPEN",
        "symbol": plan["symbol"],
        "timeframe": plan["timeframe"],
        "direction": plan["direction"],
        "entry_price": plan["entry_price"],
        "stop_loss": plan["stop_loss"],
        "take_profit": plan["take_profit"],
        "risk": plan["risk"],
        "holding_candles": 0,
        "intrabar_policy": "pessimistic_sl_first",
        "opened_at_utc": now,
        "strategy_id": plan.get("strategy_id"),
        "strategy_rule_hash": plan.get("strategy_rule_hash"),
        "strategy_generation_id": plan.get("strategy_generation_id"),
        "supporting_strategy_ids": list(plan.get("supporting_strategy_ids") or []),
    }
    # short_id seeds forbid floats (fingerprint rule) — the price rides as a string.
    position["position_id"] = integrity.short_id(
        "paper_position",
        {"strategy_id": position["strategy_id"], "entry": str(position["entry_price"]), "opened_at": now},
    )
    return position


# --- settlement (pure; source math verbatim) ----------------------------------

def _result_r(direction: str, entry: float, exit_price: float, risk: float) -> float:
    if risk <= 0:
        return 0.0
    signed = (exit_price - entry) if direction == "LONG" else (entry - exit_price)
    return signed / risk


def _advance_holding(position: dict[str, Any], candle: Mapping[str, Any] | None) -> None:
    """Advance holding_candles once per DISTINCT candle (dedup on close_time), so a
    re-run within one interval cannot accelerate time_exit (the source's fix)."""
    ts = candle.get("close_time") if isinstance(candle, Mapping) else None
    if ts is not None and str(ts) == str(position.get("last_counted_candle_ts") or ""):
        return
    position["holding_candles"] = int(position.get("holding_candles", 0)) + 1
    if ts is not None:
        position["last_counted_candle_ts"] = str(ts)


def settle_trade_plan(
    position: dict[str, Any],
    candle: Mapping[str, Any] | None,
    last_close: float | None,
    max_hold: int,
    manual_exit: bool,
) -> tuple[str | None, float | None, float | None]:
    """(close_reason, exit_price, result_R), or (None, None, None) while still open.

    Precedence: manual exit → intrabar SL/TP (pessimistic SL-first) → time exit."""
    direction = position["direction"]
    entry = float(position["entry_price"])
    sl = float(position["stop_loss"])
    tp = float(position["take_profit"])
    risk = float(position["risk"])

    if manual_exit and last_close is not None:
        return "manual_exit", last_close, _result_r(direction, entry, last_close, risk)

    _advance_holding(position, candle)
    if candle is not None and risk > 0:
        high = float(candle.get("high") or 0.0)
        low = float(candle.get("low") or 0.0)
        if direction == "LONG":
            if low <= sl:
                return "stop_loss", sl, -1.0
            if high >= tp:
                return "take_profit", tp, (tp - entry) / risk
        else:
            if high >= sl:
                return "stop_loss", sl, -1.0
            if low <= tp:
                return "take_profit", tp, (entry - tp) / risk

    if last_close is not None and int(position.get("holding_candles", 0)) >= int(max_hold):
        return "time_exit", last_close, _result_r(direction, entry, last_close, risk)

    return None, None, None


def build_outcome_record(
    position: Mapping[str, Any], close_reason: str, exit_price: float, result_r: float, *, now: str
) -> dict[str, Any]:
    """The CLOSED outcome — source-registry field names (``result_R``,
    ``outcome_closed``, ``created_at_utc``), so the C4 risk guard and C6 feedback read
    native and C7-imported outcomes identically. Self-hashed for tamper evidence."""
    record = {
        "outcome_id": integrity.short_id(
            "out", {"position_id": position.get("position_id"), "reason": close_reason, "closed_at": now}
        ),
        "outcome_closed": True,
        "result_R": round(float(result_r), 8),
        "win_loss": "WIN" if result_r > 0 else ("LOSS" if result_r < 0 else "FLAT"),
        "close_reason": close_reason,
        "created_at_utc": now,
        "symbol": position.get("symbol"),
        "timeframe": position.get("timeframe"),
        "direction": position.get("direction"),
        "entry_price": position.get("entry_price"),
        "exit_price": float(exit_price),
        "holding_candles": position.get("holding_candles"),
        "position_id": position.get("position_id"),
        "opened_at_utc": position.get("opened_at_utc"),
        "strategy_id": position.get("strategy_id"),
        "strategy_rule_hash": position.get("strategy_rule_hash"),
        "strategy_generation_id": position.get("strategy_generation_id"),
        "supporting_strategy_ids": list(position.get("supporting_strategy_ids") or []),
        "provenance": "mvp_paper_kernel",
    }
    # Idempotency key: one position settles exactly once, so the settlement's
    # identity derives from the position alone — a retried settlement of the same
    # position mints the SAME settlement_id (unlike outcome_id, which varies with
    # close reason and clock), making duplicates detectable.
    record["settlement_id"] = integrity.short_id("settle", {"position_id": position.get("position_id")})
    record["record_sha256"] = integrity.sha256_record(record)
    return record


# --- state: ungated reads, gated writes ---------------------------------------

def state_dir(root: Path | None = None) -> Path:
    return (root if root is not None else _repo_root()) / STATE_REL


def load_open_position(root: Path | None = None) -> dict[str, Any] | None:
    """The current OPEN position, or None. ALLOW-tier read of private state."""
    path = state_dir(root) / POSITION_FILENAME
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        # An unreadable position file cannot honestly mean "no position": refuse the
        # cycle's paper step rather than double-opening over a live position.
        raise ToolError("POSITION_STATE_UNREADABLE", f"paper position file unreadable: {type(exc).__name__}") from exc
    if isinstance(data, dict) and data.get("status") == "OPEN":
        return data
    return None


def read_outcomes(root: Path | None = None) -> list[dict[str, Any]]:
    """All persisted outcomes, oldest first. Missing store = honestly empty; an
    unreadable line raises so the caller fails the risk guard closed
    (``guards.risk_guard_unreadable``), never trades on a silently-truncated history."""
    path = state_dir(root) / OUTCOMES_FILENAME
    if not path.is_file():
        return []
    outcomes: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ToolError("OUTCOME_HISTORY_UNREADABLE", f"paper outcomes unreadable: {exc.strerror}") from exc
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError as exc:
            raise ToolError("OUTCOME_HISTORY_UNREADABLE", f"paper outcomes line {i + 1} is not valid JSON") from exc
        if isinstance(record, dict):
            outcomes.append(record)
    return outcomes


def already_settled(position_id: str, root: Path | None = None) -> bool:
    """Whether an outcome for this position is already durably recorded.

    The settlement dup check: a crash between outcome-append and position-clear
    leaves an OPEN position whose outcome exists — re-settling it would double the
    trade in the history the risk guard and feedback read. Raises (via
    :func:`read_outcomes`) when the history is unreadable: unverifiable is never
    treated as not-settled."""
    return any(o.get("position_id") == position_id for o in read_outcomes(root))


class PaperStore(Protocol):
    tool_id: str
    tool_version: str

    def save_position(self, position: Mapping[str, Any]) -> None: ...
    def clear_position(self) -> None: ...
    def append_outcome(self, record: Mapping[str, Any]) -> None: ...
    def settle_position(self, outcome: Mapping[str, Any]) -> None: ...


class DryRunPaperStore:
    """Default store: accepts every mutation and persists nothing.

    The R8 ``DryRunWriter`` analog — the full paper path (routing, plan, settlement,
    outcome, audit) runs on the default path without the runtime gaining durable
    paper state. ``filesystem_write=False`` rides into every record."""

    tool_id = PAPER_TOOL_ID
    tool_version = f"{PAPER_TOOL_VERSION}-dryrun"
    filesystem_write = False

    def save_position(self, position: Mapping[str, Any]) -> None:
        return None

    def clear_position(self) -> None:
        return None

    def append_outcome(self, record: Mapping[str, Any]) -> None:
        return None

    def settle_position(self, outcome: Mapping[str, Any]) -> None:
        return None


class RealPaperStore:
    """Durable paper state under ``.runtime_governance_state/crypto/``.

    Constructed only behind the Safety-Flag Gate (``paper_trading`` provider on the
    ``filesystem_write`` flag); re-asserts its authorization at every mutating call so
    a directly-constructed store cannot bypass the gate. File-locked like the ledger:
    the CLI and the scheduler may both settle in principle, and a lost update here is
    a lost trade outcome."""

    tool_id = PAPER_TOOL_ID
    tool_version = PAPER_TOOL_VERSION
    provider_id = PAPER_PROVIDER_ID
    filesystem_write = True

    def __init__(self, *, root: Path | None = None, authorization: Authorization | None = None):
        self._root = root
        self._authorization = authorization

    def _dir(self) -> Path:
        target = state_dir(self._root)
        target.mkdir(parents=True, exist_ok=True)
        return target

    def _assert(self) -> None:
        safety_gate.assert_authorization(
            self._authorization,
            required_flags=_WRITE_FLAGS,
            provider_id=self.provider_id,
            now=timeutil.utc_now_iso(),
        )

    def save_position(self, position: Mapping[str, Any]) -> None:
        self._assert()
        path = self._dir() / POSITION_FILENAME
        with locked(path.with_suffix(".lock"), code="PAPER_STATE_LOCKED", label="paper position"):
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(dict(position), ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(path)

    def clear_position(self) -> None:
        self._assert()
        path = self._dir() / POSITION_FILENAME
        with locked(path.with_suffix(".lock"), code="PAPER_STATE_LOCKED", label="paper position"):
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"status": "CLOSED"}), encoding="utf-8")
            tmp.replace(path)

    def append_outcome(self, record: Mapping[str, Any]) -> None:
        self._assert()
        path = self._dir() / OUTCOMES_FILENAME
        with locked(path.with_suffix(".lock"), code="PAPER_STATE_LOCKED", label="paper outcomes"):
            with open(path, "a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(dict(record), ensure_ascii=False) + "\n")

    def settle_position(self, outcome: Mapping[str, Any]) -> None:
        """Outcome-append + position-clear as ONE serialized step.

        Holding the position lock across both writes serializes concurrent settlers
        (CLI manual exit vs. scheduler cycle) and shrinks the crash window between
        the two files to the minimum JSONL allows; a crash inside the window leaves
        outcome-written/position-OPEN, which the chokepoint's ``already_settled``
        check recovers instead of re-settling. Lock order is position → outcomes,
        the only nesting in this module."""
        self._assert()
        position_path = self._dir() / POSITION_FILENAME
        with locked(position_path.with_suffix(".lock"), code="PAPER_STATE_LOCKED", label="paper position"):
            self.append_outcome(outcome)
            tmp = position_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"status": "CLOSED"}), encoding="utf-8")
            tmp.replace(position_path)


def select_paper_store(*, now: str | None = None, root: Path | None = None) -> PaperStore:
    """Choose the paper store — the enforced Safety-Flag Gate chokepoint.

    Defaults to :class:`DryRunPaperStore`. The durable store is returned ONLY when
    both (a) the caller opts in via ``MVP_PAPER_TRADING=real`` AND (b) the gate
    authorizes ``filesystem_write`` for the ``paper_trading`` provider against a
    local, integrity-checked activation record. The env var alone fails closed."""
    return safety_gate.select_gated(
        env_var=PAPER_ENV,
        opt_in_value=REAL_PAPER,
        flags=_WRITE_FLAGS,
        provider_id=PAPER_PROVIDER_ID,
        default_factory=DryRunPaperStore,
        gated_factory=lambda authorization: RealPaperStore(root=root, authorization=authorization),
        now=now,
        root=root,
    )


# --- the cycle chokepoint -----------------------------------------------------

def run_paper_update(
    snapshot: Mapping[str, Any],
    feature_row: Mapping[str, Any],
    pool: Mapping[str, Any],
    verdict: Mapping[str, Any],
    *,
    store: PaperStore,
    now: str,
    root: Path | None = None,
    control_store: ControlStore | None = None,
    manual_exit: bool = False,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """One cycle's paper step: settle the open position, then maybe open one.

    Returns ``(summary, records)`` — records are the audit-ready events (settle /
    open), each carrying the store's ``filesystem_write`` capability flag. Fails
    closed (``ToolBlocked``, mode-aware reason) when the runtime is PAUSED/KILLED
    (``kill_blocks: tool_write``); a no-trade verdict skips the open, never the
    settlement — an already-open position must always be able to close. A position
    whose (symbol, timeframe) does not match the snapshot's is left untouched with a
    ``POSITION_CONTEXT_MISMATCH`` refusal recorded: only the position's own context
    may settle it, and the occupied slot blocks any open. Settlement is idempotent:
    a position whose outcome is already durable (a crash between append and clear)
    is recovered — cleared without a second outcome (``SETTLEMENT_ALREADY_RECORDED``)
    — and an unreadable history refuses the settlement (``SETTLEMENT_UNVERIFIABLE``).
    """
    control = control_store if control_store is not None else ControlStore(root or _repo_root())
    state = control.load()
    if not state.execution_allowed:
        raise ToolBlocked(
            state.refusal_reason_code(),
            f"runtime is {state.mode}; kill_blocks tool_write forbids the paper update",
        )

    candles = snapshot.get("candles") or []
    last_candle = candles[-1] if candles else None
    last_close = last_candle.get("close") if isinstance(last_candle, Mapping) else None
    timeframe = str(snapshot.get("timeframe") or "")
    symbol = str(snapshot.get("symbol") or "")

    records: list[dict[str, Any]] = []
    summary: dict[str, Any] = {
        "settled": None, "opened": None, "route_status": None,
        "settle_refused": None, "settle_recovered": None,
    }

    def _event(operation: str, detail: dict[str, Any]) -> dict[str, Any]:
        return {
            "tool_id": store.tool_id,
            "tool_version": store.tool_version,
            "tool_class": PAPER_TOOL_CLASS,
            "operation": operation,
            "read_only": False,
            "external_action": False,  # paper only — no exchange is ever touched
            "reversible": True,
            "filesystem_write": bool(getattr(store, "filesystem_write", False)),
            "created_at": now,
            **detail,
        }

    # 1) Settle. Runs regardless of the verdict: closing is risk-reducing — but only
    #    in the position's own context. A cycle for a different (symbol, timeframe)
    #    must not judge this position on candles it was never opened against: no
    #    settlement, no holding advance, and no opening over the occupied
    #    single-position slot. A position missing its context fields is refused too.
    position = load_open_position(root)
    if position is not None and (
        str(position.get("symbol") or "") != symbol
        or str(position.get("timeframe") or "") != timeframe
    ):
        refusal = {
            "reason_code": POSITION_CONTEXT_MISMATCH,
            "position_id": position.get("position_id"),
            "position_symbol": position.get("symbol"),
            "position_timeframe": position.get("timeframe"),
            "snapshot_symbol": symbol,
            "snapshot_timeframe": timeframe,
        }
        summary["settle_refused"] = refusal
        records.append(_event("settle_refused", {**refusal, "read_only": True}))
    elif position is not None:
        position_id = position.get("position_id")
        # Idempotency first: a crash between outcome-append and position-clear left
        # this position OPEN with its outcome already durable. Finish the
        # interrupted settlement (clear only, never a second outcome) before any
        # settlement math — a settled corpse must not advance holding or re-settle.
        # An unreadable history refuses instead: unverifiable is never not-settled.
        recovered = refused = False
        if isinstance(position_id, str) and position_id and getattr(store, "filesystem_write", False):
            try:
                recovered = already_settled(position_id, root)
            except ToolError as exc:
                refusal = {
                    "reason_code": SETTLEMENT_UNVERIFIABLE,
                    "cause_reason_code": exc.reason_code,
                    "position_id": position_id,
                }
                summary["settle_refused"] = refusal
                records.append(_event("settle_refused", {**refusal, "read_only": True}))
                refused = True
        if recovered:
            store.clear_position()
            recovery = {"reason_code": SETTLEMENT_ALREADY_RECORDED, "position_id": position_id}
            summary["settle_recovered"] = recovery
            records.append(_event("settle_recovered", recovery))
            position = None  # the slot is honestly free again
        elif not refused:
            max_hold = MAX_HOLD_BARS.get(timeframe, DEFAULT_MAX_HOLD_BARS)
            reason, exit_price, result_r = settle_trade_plan(position, last_candle, last_close, max_hold, manual_exit)
            if reason is not None:
                outcome = build_outcome_record(position, reason, exit_price, result_r, now=now)
                store.settle_position(outcome)
                summary["settled"] = {
                    "position_id": position_id,
                    "close_reason": reason,
                    "result_R": outcome["result_R"],
                    "outcome_id": outcome["outcome_id"],
                }
                records.append(_event("settle", {
                    "position_id": position_id,
                    "close_reason": reason,
                    "result_R": outcome["result_R"],
                    "outcome_id": outcome["outcome_id"],
                    "settlement_id": outcome["settlement_id"],
                    "outcome_sha256": outcome["record_sha256"],
                }))
                position = None
            else:
                store.save_position(position)  # persist advanced holding_candles

    # 2) Maybe open — only with no open position AND an allowing verdict.
    route = route_entries(pool, feature_row, symbol=symbol, timeframe=timeframe, now=now)
    summary["route_status"] = route["status"]
    if position is None and bool(verdict.get("allow_new_position")):
        plan = build_entry_plan(route, feature_row, now=now)
        if plan is not None:
            opened = open_position(plan, now=now)
            store.save_position(opened)
            summary["opened"] = {
                "position_id": opened["position_id"],
                "direction": opened["direction"],
                "strategy_id": opened.get("strategy_id"),
            }
            records.append(_event("open", {
                "position_id": opened["position_id"],
                "direction": opened["direction"],
                "entry_price": opened["entry_price"],
                "stop_loss": opened["stop_loss"],
                "take_profit": opened["take_profit"],
                "strategy_id": opened.get("strategy_id"),
                "strategy_rule_hash": opened.get("strategy_rule_hash"),
            }))
    return summary, records

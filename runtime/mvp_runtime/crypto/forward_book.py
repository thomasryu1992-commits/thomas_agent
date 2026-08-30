"""Per-strategy forward book — every OBSERVATION strategy accrues its own forward record.

**Why this exists (Thomas 2026-08-29).** The routed paper book holds ONE position per
``(symbol, timeframe)``, so N strategies sharing a context split a fixed evidence stream N
ways — and the 5-1 forward requirement (25 trades, 10 at 1d) was being fed from that stream.
Measured on the pool the day this landed: the best lineage held 10 forward trades after a
month and one held zero; verification of a per-strategy question was serialized behind a
portfolio constraint. This book runs each strategy's OWN virtual position stream — same
entry rules, same doors, same exit math and cost charging as the paper kernel — so forward
evidence accrues per lineage at the lineage's own signal rate, in parallel.

**What it deliberately is not.** Not a second paper book: nothing here feeds the risk
guards, the loss breakers, the lifecycle ladder or ``split_by_provenance``'s "own" bucket —
rows live in their OWN file with their own provenance, the separation the counterfactual
book drew. Not a fill upgrade: fills are modelled exactly as the paper kernel models them
(entry at row close, ATR exits, pessimistic SL-first, no fine-candle refinement), so a
forward row is comparable to a paper row, never to a live fill. Not the portfolio
rehearsal — the routed book keeps that job untouched.

**Row parity is the load-bearing property.** A settled row is built by the paper kernel's
own ``build_outcome_record`` — result_R net of fees+slippage on the intent basis, funding
charged at read time from ``holding_candles x timeframe`` — then re-stamped with this
book's provenance and re-hashed. ``forward_confirmation.judge_forward`` prices these rows
through the same ``net_result_r`` path as paper rows, so the 5-1 arithmetic is unchanged;
only the SOURCE of forward evidence moved (``promotion._gate_live_confirmation``).

**This file has exactly one legitimate writer**, which is why :func:`read_forward_outcomes`
is stricter than its paper counterpart: the paper store legitimately holds imported rows
whose integrity is the audited import batch, so its reader verifies only its own
provenance. Nothing ever legitimately writes a foreign row HERE — this store feeds the
door that arms real money — so a row that is not this book's, or cannot prove it is,
fails the read rather than passing around the hash check.

**Seeding.** A spec is frozen at mint, so every bar that closed after it is genuine
out-of-sample data for the lineage. :func:`walk_seed_span` replays those bars through the
SAME transition the live cycle runs, stamping HISTORICAL times into the rows — slices
spread across real calendar, ids are deterministic, and re-seeding is idempotent through
the settlement-id dedup every append runs. The walk stops at the live stream's
``first_seen_candle`` so the two streams can never settle the same bar twice, and a
position still open at that boundary is dropped un-minted — the live stream owns the
present.

The no-signal marker is DISPLAY ONLY (Thomas 2026-08-29): a lineage tracked for
``FORWARD_NO_SIGNAL_DAYS`` without a single virtual open is named in its own context's
cycle summary. Nothing here refuses, demotes or retires it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from runtime.read_only_kernel import integrity

from .. import jsonl, timeutil
from ..errors import ToolError
from ..filelock import locked
from .market_data import TIMEFRAMES
from .paper import (
    COOLDOWN_BARS_AFTER_STOPLOSS,
    OCCUPYING_STATUSES,
    STATUS_ENTRY_CANDIDATE,
    build_entry_plan,
    build_outcome_record,
    entry_cost_refusal,
    open_position,
    position_max_hold,
    regime_admits,
    settle_trade_plan,
    state_dir,
    stop_beyond_liquidation_refusal,
)
from .distribution_gate import distribution_admits
from .strategy import StrategySpec, evaluate_spec
from .lifecycle import outcome_attribution_key

FORWARD_BOOK_VERSION = "forward_book.v1"
BOOK_FILENAME = "forward_positions.json"
OUTCOMES_FILENAME = "forward_outcomes.jsonl"
FORWARD_PROVENANCE = "mvp_forward_book"

FORWARD_BOOK_UNVERIFIABLE = "FORWARD_BOOK_UNVERIFIABLE"
FORWARD_HISTORY_UNREADABLE = "FORWARD_HISTORY_UNREADABLE"
FORWARD_HISTORY_TAMPERED = "FORWARD_HISTORY_TAMPERED"
FORWARD_HISTORY_DUPLICATE = "FORWARD_HISTORY_DUPLICATE"
FORWARD_STORE_LOCKED = "FORWARD_STORE_LOCKED"

# Display-only staleness horizon: a lineage tracked this long with zero virtual opens is
# named in its own context's cycle summary (and nowhere else). 14 days is two 1h lifecycle
# windows' worth of calendar at the pool's measured per-spec signal rates (0-10
# opens/month) — long enough that "no signal yet" stops being noise, short enough to
# surface a dead slot while its promotion is still fresh in the operator's memory.
FORWARD_NO_SIGNAL_DAYS = 14


def _book_path(root: Path | None) -> Path:
    return state_dir(root) / BOOK_FILENAME


def _outcomes_path(root: Path | None) -> Path:
    return state_dir(root) / OUTCOMES_FILENAME


def lineage_key(entry: Mapping[str, Any]) -> str:
    """The entry's attribution key — the same identity the forward judge matches on.

    ``outcome_attribution_key`` yields ``cand:{candidate_id}`` when the entry carries one,
    else the generation+rule-hash form. A ``sid:`` display-name fallback (or nothing) is
    returned as '' here: the judge deliberately refuses sid: attribution, so evidence
    minted under it could never be spent — the cycle AND the seeder both refuse via this
    one function so they cannot disagree about who is trackable."""
    key = outcome_attribution_key(entry)
    if not key or key.startswith("sid:"):
        return ""
    return key


def book_key(lineage: str, symbol: str, timeframe: str) -> str:
    return f"{lineage}|{symbol}|{timeframe}"


def _fresh_state(lineage: str, symbol: str, timeframe: str, strategy_id: Any, now: str) -> dict[str, Any]:
    return {
        "lineage": lineage, "symbol": symbol, "timeframe": timeframe,
        "strategy_id": strategy_id, "first_tracked_at": now,
        # The live stream's own start: bars strictly before it belong to the seeder, bars
        # at or after it to the cycle — the boundary that keeps the two streams disjoint.
        "first_seen_candle": None,
        "last_seen_candle": None, "last_signal_at": None,
        "opens_count": 0, "position": None, "cooldown_remaining": 0,
    }


def _parse_book(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping) or not isinstance(raw.get("entries"), Mapping):
        raise ToolError(FORWARD_BOOK_UNVERIFIABLE, "forward book is not a book-shaped mapping")
    return {"forward_book_version": FORWARD_BOOK_VERSION,
            "entries": {str(k): dict(v) for k, v in raw["entries"].items() if isinstance(v, Mapping)}}


def load_book(root: Path | None = None) -> dict[str, Any]:
    """The open virtual positions and per-lineage tracking state. Fail-closed.

    Missing file is an empty book; an unreadable one raises ``FORWARD_BOOK_UNVERIFIABLE``
    so the caller degrades and writes NOTHING — the damaged file survives for
    inspection."""
    path = _book_path(root)
    if not path.is_file():
        return {"forward_book_version": FORWARD_BOOK_VERSION, "entries": {}}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ToolError(FORWARD_BOOK_UNVERIFIABLE, f"forward book unreadable: {exc}") from exc
    return _parse_book(raw)


def _write_book(book: Mapping[str, Any], *, root: Path | None, now: str) -> None:
    path = _book_path(root)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({
        "forward_book_version": FORWARD_BOOK_VERSION,
        "updated_at_utc": now,
        "entries": book["entries"],
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def mutate_book(root: Path | None, now: str, fn: Callable[[dict[str, Any]], Any]) -> Any:
    """Load -> mutate -> save with the LOCK HELD ACROSS ALL THREE.

    Both writers (the cycle and the seeder) go through here, because a
    read-modify-write whose lock covers only the final swap is last-writer-wins: the
    seeder's minutes-long walk would silently erase every cycle save in between, and the
    rolled-back ``last_seen_candle`` would then re-open bars the settlement dedup cannot
    catch (a re-opened position gets a fresh ``opened_at`` and therefore a fresh
    settlement id). The mutation callbacks here are pure in-memory work over an
    already-collected context — milliseconds — so holding the lock across them costs
    nothing that matters."""
    path = _book_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with locked(path.with_suffix(".lock"), code=FORWARD_STORE_LOCKED, label="forward book"):
        book = load_book(root)
        result = fn(book)
        _write_book(book, root=root, now=now)
    return result


def read_forward_outcomes(root: Path | None = None) -> list[dict[str, Any]]:
    """Every settled virtual outcome — a VERIFIED read, stricter than the paper store's.

    Every row must carry this book's provenance and recompute its ``record_sha256``
    exactly, and settlement ids must be present and unique. The paper reader verifies
    only its own provenance because imported rows legitimately exist there under an
    audited batch; nothing legitimately writes a foreign row HERE, and this store feeds
    the LIVE arming door — so an unrecognized row is treated as tampering, not as a
    vintage to wave through."""
    path = _outcomes_path(root)
    rows: list[dict[str, Any]] = []
    seen_settlements: set[str] = set()
    for lineno, record in jsonl.iter_numbered(
        path, read_code=FORWARD_HISTORY_UNREADABLE, label="forward outcomes", exc_type=ToolError,
    ):
        if not isinstance(record, dict):
            continue
        if record.get("provenance") != FORWARD_PROVENANCE:
            raise ToolError(FORWARD_HISTORY_TAMPERED,
                            f"forward outcomes line {lineno} carries a provenance this store "
                            "never writes")
        stored = record.get("record_sha256")
        body = {k: v for k, v in record.items() if k != "record_sha256"}
        if not isinstance(stored, str) or integrity.sha256_record(body) != stored:
            raise ToolError(FORWARD_HISTORY_TAMPERED,
                            f"forward outcomes line {lineno} fails its self-hash")
        settlement_id = record.get("settlement_id")
        if not (isinstance(settlement_id, str) and settlement_id):
            raise ToolError(FORWARD_HISTORY_TAMPERED,
                            f"forward outcomes line {lineno} carries no settlement id")
        if settlement_id in seen_settlements:
            raise ToolError(FORWARD_HISTORY_DUPLICATE,
                            f"duplicate settlement_id: {settlement_id}")
        seen_settlements.add(settlement_id)
        rows.append(record)
    return rows


def _append_outcomes(records: list[dict[str, Any]], *, root: Path | None) -> None:
    """Append settled rows, skipping any settlement already recorded.

    Dup-check and append share one lock, so a retry after a crash between the append and
    the book rewrite COMPLETES the settlement instead of doubling it — and the same
    property is what makes re-seeding idempotent: historical timestamps make seeded ids
    deterministic, so a second seed of the same span re-mints the same settlement ids and
    they are all skipped here."""
    if not records:
        return
    path = _outcomes_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with locked(path.with_suffix(".lock"), code=FORWARD_STORE_LOCKED, label="forward outcomes"):
        recorded = {r.get("settlement_id") for r in read_forward_outcomes(root)
                    if r.get("settlement_id")}
        fresh = [r for r in records if r.get("settlement_id") not in recorded]
        if not fresh:
            return
        with open(path, "a", encoding="utf-8", newline="\n") as handle:
            for record in fresh:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())


def _finalize_row(row: dict[str, Any], *, seeded: bool) -> dict[str, Any]:
    """Re-stamp a paper-kernel outcome row as this book's, and make the hash true again.

    ``build_outcome_record`` hard-codes the paper provenance and hashes the whole record;
    editing ANY field after it invalidates ``record_sha256``, so the stamp and the rehash
    travel together or not at all. ``r_basis`` stays ``intent_net_of_costs`` — a claim
    about the ACCOUNTING (fees+slippage inside, carry at read time), identical here by
    construction, not a claim about who settled the trade."""
    out = dict(row)
    out["provenance"] = FORWARD_PROVENANCE
    if seeded:
        out["seeded"] = True
    out.pop("record_sha256", None)
    out["record_sha256"] = integrity.sha256_record(out)
    return out


def _holding_budget_elapsed(position: Mapping[str, Any], *, now: str) -> bool:
    """Wall-clock budget for a position whose context left the pool — the frozen-clock
    rule the counterfactual book learned on 2026-08-04: a virtual position only advances
    on its own context's cycles, so one stranded outside the pool would otherwise hold
    its book entry forever."""
    opened = position.get("opened_at_utc")
    bars = position.get("max_holding_bars")
    minutes = TIMEFRAMES.get(str(position.get("timeframe")))
    if not (isinstance(opened, str) and opened and isinstance(bars, (int, float))
            and not isinstance(bars, bool) and minutes):
        return False
    try:
        elapsed = (timeutil.parse_iso(now) - timeutil.parse_iso(opened)).total_seconds() / 60.0
    except (TypeError, ValueError):
        return False
    return elapsed > float(bars) * minutes


def pool_context_set(pool: Mapping[str, Any]) -> set[tuple[str, str]]:
    """Every ``(symbol, timeframe)`` an occupying entry is scoped to — raw dict reads,
    because membership is a question about the pool file's own fields, not about whether
    the spec parses (a spec that does not parse is not routing anywhere either way)."""
    contexts: set[tuple[str, str]] = set()
    for entry in (pool.get("active_strategies") or []):
        if entry.get("status") not in OCCUPYING_STATUSES:
            continue
        spec = entry.get("strategy_spec")
        if not isinstance(spec, Mapping):
            continue
        tf = str(spec.get("timeframe") or "")
        for sym in (spec.get("symbol_scope") or []):
            if isinstance(sym, str) and sym and tf:
                contexts.add((sym, tf))
    return contexts


def replay_entry_bar(
    state: dict[str, Any],
    pool_entry: Mapping[str, Any],
    spec: StrategySpec,
    feature_row: Mapping[str, Any],
    candle: Mapping[str, Any] | None,
    last_close: float | None,
    *,
    symbol: str,
    timeframe: str,
    now: str,
) -> dict[str, Any] | None:
    """One bar of one lineage's virtual life — the transition the cycle and the seeder share.

    Order matches the paper kernel: settle the open position FIRST (a position must
    always be able to close), then consider a new entry on this bar's signal. Entries
    pass the same doors the real path applies — the economics door, the liquidation
    guard, the stop-loss cooldown, and the entry's regime/distribution gates read off the
    POOL ENTRY (both fail open on absent evidence, exactly as the router does). ``state``
    is this lineage-context's book slot and is mutated in place; the return value is the
    settled outcome row (un-finalized) or None.

    The cooldown decrements on the stop bar itself — the same accounting as
    ``factory._replay`` and the paper cooldown marks (whose expiry is stop close +
    N x timeframe with a STRICT compare, so the bar closing exactly at the expiry
    trades). With N=2 that is: stop at T, blocked at T+tf, entering again at T+2tf.

    At-most-once per closed candle: the bar is processed only when the candle's
    ``close_time`` is strictly newer than ``state['last_seen_candle']`` — the
    routing-marks comparison, kept inside the book because the paper store's marks are
    context-keyed and already consumed by the routed book before this step runs."""
    candle_time = candle.get("close_time") if isinstance(candle, Mapping) else None
    if not isinstance(candle_time, str) or not candle_time:
        return None
    last_seen = state.get("last_seen_candle")
    if isinstance(last_seen, str) and candle_time <= last_seen:
        return None
    state["last_seen_candle"] = candle_time
    if not state.get("first_seen_candle"):
        state["first_seen_candle"] = candle_time

    settled_row: dict[str, Any] | None = None
    position = state.get("position")
    if isinstance(position, dict) and position:
        max_hold, _legacy = position_max_hold(position, timeframe)
        reason, exit_price, gross_r = settle_trade_plan(position, candle, last_close, max_hold, False)
        if reason is not None:
            settled_row = build_outcome_record(position, reason, float(exit_price), float(gross_r), now=now)
            state["position"] = None
            if reason == "stop_loss":
                state["cooldown_remaining"] = COOLDOWN_BARS_AFTER_STOPLOSS

    cooldown = state.get("cooldown_remaining")
    if isinstance(cooldown, int) and cooldown > 0:
        state["cooldown_remaining"] = cooldown - 1
        return settled_row

    if state.get("position") or not feature_row:
        return settled_row

    result = evaluate_spec(spec, feature_row)
    if not result.matched:
        return settled_row
    state["last_signal_at"] = now
    admitted, _reason = regime_admits(pool_entry, feature_row.get("market_regime"))
    if not admitted:
        return settled_row
    di_admitted, _di_reason, _di = distribution_admits(pool_entry, feature_row)
    if not di_admitted:
        return settled_row

    plan = build_entry_plan({
        "status": STATUS_ENTRY_CANDIDATE,
        "direction": result.direction,
        "symbol": symbol,
        "timeframe": timeframe,
        "primary_spec": spec,
        "primary_strategy_id": pool_entry.get("strategy_id"),
        "primary_candidate_id": pool_entry.get("candidate_id"),
        "primary_strategy_rule_hash": pool_entry.get("strategy_rule_hash"),
        "primary_strategy_generation_id": pool_entry.get("generation_id")
        or (pool_entry.get("strategy_spec") or {}).get("generation_id"),
    }, feature_row, now=now)
    if plan is None:
        return settled_row
    if entry_cost_refusal(plan) is not None or stop_beyond_liquidation_refusal(plan) is not None:
        return settled_row

    state["position"] = open_position(plan, now=now)
    state["opens_count"] = int(state.get("opens_count") or 0) + 1
    return settled_row


def walk_seed_span(
    pool_entry: Mapping[str, Any],
    spec: StrategySpec,
    rows: Sequence[Mapping[str, Any]],
    candles: Sequence[Mapping[str, Any]],
    *,
    symbol: str,
    timeframe: str,
    mint: str,
    boundary: str | None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Replay one lineage over historical bars: [mint, boundary). Pure.

    ``boundary`` is the live stream's ``first_seen_candle`` — the seeder never crosses it,
    so the seeded span and the live span partition the calendar and no bar can settle in
    both. Rows are stamped with each bar's OWN close_time, which is what makes the ids
    deterministic (idempotent re-seed) and the confirmation slices honest. A position
    still open when the walk ends is DROPPED un-minted: the live stream owns the present,
    and a half-lived seeded position merged into it would be settled against bars its
    entry never saw.

    Returns ``(seed_state, finalized_rows)`` — the state is for the CALLER to merge under
    the book lock, and only its counters/markers, never its position."""
    state = _fresh_state(lineage_key(pool_entry), symbol, timeframe,
                         pool_entry.get("strategy_id"), mint)
    out: list[dict[str, Any]] = []
    for row, candle in zip(rows, candles):
        bar_now = str((candle or {}).get("close_time") or "")
        if not bar_now or bar_now < mint:
            continue  # pre-mint bars exist only to warm the indicators up
        if boundary and bar_now >= boundary:
            break
        settled = replay_entry_bar(state, pool_entry, spec, row, candle, row.get("close"),
                                   symbol=symbol, timeframe=timeframe, now=bar_now)
        if settled is not None:
            out.append(_finalize_row(settled, seeded=True))
    state["position"] = None  # the boundary-open position is dropped, never merged
    return state, out


def run_forward_book_update(
    *,
    pool: Mapping[str, Any],
    feature_row: Mapping[str, Any],
    last_candle: Mapping[str, Any] | None,
    last_close: float | None,
    symbol: str,
    timeframe: str,
    now: str,
    root: Path | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    """One cycle's forward-book step for one context. Observational — NEVER raises.

    Every OCCUPYING pool entry scoped to this ``(symbol, timeframe)`` advances one bar of
    its own virtual stream via :func:`replay_entry_bar`. Entries whose context has left
    the POOL (not merely "is not this cycle's context" — every other context in the same
    fan-out is foreign to this one, and sweeping those would race their own settlements)
    are expired on their wall-clock holding budget with no outcome row, and their book
    entries pruned once nothing remains to hold. The no-signal marker names only THIS
    context's lineages, so a cycle record's list is bounded by the per-context cap.

    ``persist=False`` computes the full summary against a read-only snapshot and writes
    nothing. A persist-side failure (torn line, lock contention with the seeder) degrades
    into the summary exactly like an unreadable book — this store is observational, and
    observational steps do not get to abort the cycle that feeds the live leg."""
    def _advance(book: dict[str, Any]) -> dict[str, Any]:
        entries = book["entries"]
        settled_rows: list[dict[str, Any]] = []
        opened_ids: list[str] = []
        for pool_entry in (pool.get("active_strategies") or []):
            if pool_entry.get("status") not in OCCUPYING_STATUSES or not pool_entry.get("strategy_spec"):
                continue
            try:
                spec = StrategySpec.from_dict(pool_entry["strategy_spec"])
            except Exception:
                continue
            if symbol not in spec.symbol_scope or spec.timeframe != timeframe:
                continue
            lineage = lineage_key(pool_entry)
            if not lineage:
                continue
            key = book_key(lineage, symbol, timeframe)
            state = entries.setdefault(
                key, _fresh_state(lineage, symbol, timeframe, pool_entry.get("strategy_id"), now))
            opens_before = int(state.get("opens_count") or 0)
            row = replay_entry_bar(state, pool_entry, spec, feature_row, last_candle, last_close,
                                   symbol=symbol, timeframe=timeframe, now=now)
            if row is not None:
                settled_rows.append(_finalize_row(row, seeded=False))
            if int(state.get("opens_count") or 0) > opens_before:
                opened_ids.append(str(state["position"]["position_id"]))

        # Expiry and pruning are judged against POOL membership, never against "not this
        # cycle's context": in one fan-out every other context is foreign to this one,
        # and a sweep keyed on that raced the owning context's own time_exit — losing the
        # very rows this store exists to mint. A context still in the pool keeps its
        # entries untouched here, however stale; only a lineage-context the pool no
        # longer routes is wound down.
        in_pool = pool_context_set(pool)
        expired: list[str] = []
        for key in list(entries):
            state = entries[key]
            if (str(state.get("symbol")), str(state.get("timeframe"))) in in_pool:
                continue
            position = state.get("position")
            if isinstance(position, dict) and position:
                if _holding_budget_elapsed(position, now=now):
                    expired.append(str(position.get("position_id") or key))
                    state["position"] = None
            if not state.get("position"):
                del entries[key]

        no_signal: list[str] = []
        horizon = FORWARD_NO_SIGNAL_DAYS * 1440.0
        for state in entries.values():
            if state.get("symbol") != symbol or state.get("timeframe") != timeframe:
                continue
            if int(state.get("opens_count") or 0) > 0:
                continue
            first = state.get("first_tracked_at")
            if not (isinstance(first, str) and first):
                continue
            try:
                tracked = (timeutil.parse_iso(now) - timeutil.parse_iso(first)).total_seconds() / 60.0
            except (TypeError, ValueError):
                continue
            if tracked > horizon:
                no_signal.append("%s %s %s" % (state.get("strategy_id"), symbol, timeframe))

        if persist:
            _append_outcomes(settled_rows, root=root)
        open_count = sum(1 for s in entries.values() if s.get("position"))
        return {
            "settled": [r.get("outcome_id") for r in settled_rows],
            "opened": opened_ids,
            "open_count": open_count,
            "no_signal": sorted(no_signal),
            **({"expired": expired} if expired else {}),
        }

    try:
        if persist:
            return mutate_book(root, now, _advance)
        return _advance(load_book(root))
    except ToolError as exc:
        return {"settled": [], "opened": [], "open_count": None, "no_signal": [],
                "degraded": exc.reason_code}

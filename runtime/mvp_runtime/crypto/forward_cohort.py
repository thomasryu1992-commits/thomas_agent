"""The forward cohort — forward evidence for lineages the pool does not hold (Phase 1, option A).

Designed in ``docs/proposals/FORWARD_COHORT_OFF_POOL_V0.1.md``; Thomas decided 2026-09-23 to build
Phase 1 under option A. **Why it exists.** ``forward_book`` advances only occupying pool entries,
so the number of forward clocks is the number of occupied slots — 15 on the day this landed,
against 120 lineages that clear the OBSERVATION entry bar. This module gives those lineages a
clock without a slot.

**What it is, in four parts.**

- **A frozen cohort record** (``forward_cohort.v1``, :func:`freeze_cohort`): the members, each
  with the moment its selecting row was made, and the attempt counts in total and per context,
  sealed with ``record_sha256`` and never edited. A lineage that becomes eligible later waits
  for the next cohort, so K is a number fixed before any outcome is seen.
- **Mechanical membership** (:func:`eligible_members`): the promotion door's own sets and bar —
  ``PROMOTABLE_DERIVATION_TYPES``, the promotable cost-basis and depth ranks,
  ``assert_observation_entry_bar`` — one member per ``(family, symbol scope, timeframe)`` in
  ``rank_candidates`` order; a rule the pool already routes is excluded (it has a clock), and so
  is a lineage an earlier cohort holds or one the pool's forward book has ever settled a row for
  (its pool clock and a cohort clock would cover the same bars, and option A counts only one). Nothing is chosen by hand and nothing reads an outcome.
- **A walker with its own store** (:func:`run_cohort_walk`): each member-context advances bar
  by bar through ``forward_book.replay_entry_bar`` — the transition the live forward book and
  its seeder run — from its selecting row's ``created_at_utc``
  (``forward_confirmation.selection_cutoff``). Unlike the seed walk it KEEPS a position open
  across runs: a daily walk that dropped the boundary position would discard every trade held
  overnight. Rows go to ``forward_cohort_outcomes.jsonl`` under :data:`COHORT_PROVENANCE`,
  which ``forward_book.read_forward_outcomes`` refuses as tampering — the arming door never
  reads a cohort row.
- **A report** (:func:`cohort_report`): each member's ``judge_forward`` numbers over its cohort
  rows, its trade-level mean and lower bound (:func:`trade_bounds`), and the cohort's K. Display
  only; :func:`board_summary` never lists a FORWARD_CONTRADICTED member among its leaders.

**What option A decides, and where that lives in code.** Cohort evidence never reaches the LIVE
door; it may inform which lineage an operator promotes into the pool, and then the pool's
forward clock starts at the promotion, because the cohort period was the selection data. That
last clause is enforced where the pool's forward rows are seeded (``scripts/seed_forward_book``
asks :func:`member_candidate_ids`). Nothing here promotes, refuses, ranks the pool or writes
the pool's files.

**Two parity traps, closed here rather than rediscovered.**

- *Admission evidence.* A candidate row does not carry ``regime_evidence`` or
  ``distribution_reference``; the entry doors read them off the entry and fail OPEN without
  them. :func:`synthesize_entry` projects them with ``strategy_artifact.admission_evidence``,
  the function the promotion door uses (the 2026-09-02 lesson: S004-GEN-690 probed at 36 opens
  and installed at 11).
- *Position identity.* ``position_id`` hashes ``(strategy_id, entry price, opened_at)``, and
  candidate rows share template ids (``S007`` names many lineages). Two members firing on one
  bar at one price would mint one ``settlement_id`` and the append-side dedup would silently drop
  the second. The synthesized entry's ``strategy_id`` is the member's ``candidate_id``; its rows
  therefore carry a candidate id in that field, which is a display oddity of this store alone.
"""

from __future__ import annotations

import json
import math
import os
import statistics
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from runtime.read_only_kernel import integrity

from .. import jsonl, timeutil
from ..errors import MvpRuntimeError, ToolError
from ..filelock import locked
from . import forward_book
from .candidate_identity import candidate_id
from .candidate_ranking import candidate_quality, rank_candidates
from .forward_confirmation import (
    FORWARD_CONFIRMED, FORWARD_CONTRADICTED, forward_outcomes_for, judge_forward, min_forward_trades,
    selection_cutoff,
)
from .market_data import TIMEFRAMES
from .outcome_math import net_result_r
from .pool_admission import (
    OBSERVATION_MIN_BACKTEST_CLOSED,
    PROMOTABLE_COST_BASIS_RANKS,
    PROMOTABLE_DERIVATION_TYPES,
    PROMOTABLE_EVIDENCE_DEPTH_RANKS,
    assert_observation_entry_bar,
)
from .pool_state import load_active_pool, read_candidates
from .promotion_backlog import _lineage_key
from .robustness import CONFIDENCE_Z
from .state import state_dir
from .strategy import StrategySpec
from .strategy_artifact import admission_evidence
from .vocabulary import OCCUPYING_STATUSES

FORWARD_COHORT_VERSION = "forward_cohort.v1"
# The membership rule's name, stamped into every record with the thresholds it read, so a
# cohort frozen under one bar is never read as frozen under a later one.
ELIGIBILITY_VERSION = "observation_entry_bar.v1"
COHORTS_FILENAME = "forward_cohorts.jsonl"
POSITIONS_FILENAME = "forward_cohort_positions.json"
OUTCOMES_FILENAME = "forward_cohort_outcomes.jsonl"
COHORT_PROVENANCE = "mvp_forward_cohort"
POSITIONS_VERSION = "forward_cohort_positions.v1"

FORWARD_COHORT_UNREADABLE = "FORWARD_COHORT_UNREADABLE"
FORWARD_COHORT_TAMPERED = "FORWARD_COHORT_TAMPERED"
FORWARD_COHORT_EMPTY = "FORWARD_COHORT_EMPTY"
FORWARD_COHORT_LOCKED = "FORWARD_COHORT_LOCKED"
FORWARD_COHORT_POSITIONS_INVALID = "FORWARD_COHORT_POSITIONS_INVALID"

# How far a member's forward record has got, for the reader (2026-09-23; item PR-LIVE-8 of an external
# follow-up plan). The judge's INSUFFICIENT covers both a member still short of its timeframe's trade
# floor and one past it whose record still cannot be judged; the board read both as "판정 전", and a
# three-trade leader looked like a lineage waiting on a verdict. Display only: nothing reads it to decide.
MATURITY_EXPLORATORY = "EXPLORATORY"      # below its trade floor: the numbers are a first look
MATURITY_MATURE = "MATURE"                # at or past the floor, neither confirmed nor refuted —
                                          # FORWARD_INSUFFICIENT there, or FORWARD_UNDERPOWERED (2026-09-24)
MATURITY_CONFIRMED = "CONFIRMED"          # FORWARD_CONFIRMED
MATURITY_CONTRADICTED = "CONTRADICTED"    # FORWARD_CONTRADICTED
MATURITY_UNRESOLVED = "UNRESOLVED"        # the member's candidate row cannot be found
MATURITIES = (MATURITY_EXPLORATORY, MATURITY_MATURE, MATURITY_CONFIRMED, MATURITY_CONTRADICTED,
              MATURITY_UNRESOLVED)

# Pre-cutoff bars fetched so every indicator is warm by the first counted bar — the seeder's
# figure, for the seeder's reason (the deepest consumer is a 100-bar percentile window).
WARMUP_BARS = 120
# The venue serves the newest bars, so when this binds it is the OLDEST part of a member's span
# that is lost. 5,000 is the venue's own page ceiling; one bar short of it leaves the forming
# bar the collector drops.
MAX_WALK_BARS = 4_999


# --- paths ------------------------------------------------------------------------------------

def _cohorts_path(root: Path | None) -> Path:
    return state_dir(root) / COHORTS_FILENAME


def _positions_path(root: Path | None) -> Path:
    return state_dir(root) / POSITIONS_FILENAME


def _outcomes_path(root: Path | None) -> Path:
    return state_dir(root) / OUTCOMES_FILENAME


# --- the frozen record ------------------------------------------------------------------------

def _context_key(spec: Mapping[str, Any]) -> str:
    """``robustness.SELECTION_CONTEXT`` — symbol scope + timeframe — as one string."""
    return "%s|%s" % (",".join(str(s) for s in (spec.get("symbol_scope") or [])),
                      spec.get("timeframe"))


def read_cohorts(root: Path | None = None) -> list[dict[str, Any]]:
    """Every frozen cohort, verified: each line must be a sealed ``forward_cohort.v1`` record.

    A missing file is no cohorts. A line that does not parse raises
    ``FORWARD_COHORT_UNREADABLE``; a record whose self-hash does not recompute, or that is not
    this version, raises ``FORWARD_COHORT_TAMPERED`` — membership decides which lineages the
    seeder starts at their promotion, so an edited member list is refused, not skipped."""
    cohorts: list[dict[str, Any]] = []
    for lineno, record in jsonl.iter_numbered(
        _cohorts_path(root), read_code=FORWARD_COHORT_UNREADABLE, label="forward cohorts",
        exc_type=ToolError,
    ):
        if not isinstance(record, dict) or record.get("forward_cohort_version") != FORWARD_COHORT_VERSION:
            raise ToolError(FORWARD_COHORT_TAMPERED,
                            f"forward cohorts line {lineno} is not a {FORWARD_COHORT_VERSION} record")
        stored = record.get("record_sha256")
        body = {k: v for k, v in record.items() if k != "record_sha256"}
        if not isinstance(stored, str) or integrity.sha256_record(body) != stored:
            raise ToolError(FORWARD_COHORT_TAMPERED, f"forward cohorts line {lineno} fails its self-hash")
        cohorts.append(record)
    return cohorts


def member_candidate_ids(root: Path | None = None) -> frozenset[str]:
    """Every candidate id any frozen cohort holds — the set option A starts at promotion."""
    return frozenset(
        str(member["candidate_id"])
        for cohort in read_cohorts(root) for member in cohort.get("members") or []
        if member.get("candidate_id")
    )


def eligible_members(
    candidates: Sequence[Mapping[str, Any]],
    pool: Mapping[str, Any],
    *,
    exclude_candidate_ids: frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    """The lineages a cohort frozen now would hold, in ``rank_candidates`` order. Pure.

    The chain is the promotion door's, in the backlog's order, minus everything that is about a
    SLOT rather than about the evidence (the family and size caps, the lifecycle horizon): the
    derivation allowlist, the cost-basis and depth ranks, then the OBSERVATION entry bar. A rule
    an occupying pool entry routes is out — it already has a clock — and so is a candidate id an
    earlier cohort holds. A row that cannot say when it was made cannot start a clock and is out.
    """
    occupying_hashes = {
        entry.get("strategy_rule_hash") for entry in (pool.get("active_strategies") or [])
        if entry.get("status") in OCCUPYING_STATUSES and entry.get("strategy_rule_hash")
    }
    seen_lineages: set[tuple[Any, ...]] = set()
    members: list[dict[str, Any]] = []
    for record in rank_candidates(list(candidates)):
        spec = record.get("strategy_spec")
        if not isinstance(spec, Mapping):
            continue
        cid = candidate_id(record)
        if cid in exclude_candidate_ids or record.get("strategy_rule_hash") in occupying_hashes:
            continue
        if "derivation_type" in record and record.get("derivation_type") not in PROMOTABLE_DERIVATION_TYPES:
            continue
        quality = candidate_quality(record)
        if quality["cost_basis_rank"] not in PROMOTABLE_COST_BASIS_RANKS:
            continue
        if quality["evidence_depth_rank"] not in PROMOTABLE_EVIDENCE_DEPTH_RANKS:
            continue
        try:
            assert_observation_entry_bar([record])
        except ToolError:
            continue
        if selection_cutoff(record) is None:
            continue
        lineage = _lineage_key(spec)
        if lineage in seen_lineages:
            continue
        seen_lineages.add(lineage)
        members.append({
            "candidate_id": cid,
            "strategy_rule_hash": record.get("strategy_rule_hash"),
            "generation_id": record.get("generation_id"),
            "strategy_family": spec.get("strategy_family"),
            "direction": spec.get("direction"),
            "timeframe": spec.get("timeframe"),
            "symbol_scope": list(spec.get("symbol_scope") or []),
            "selected_at_utc": str(record.get("created_at_utc")),
            "holdout_status": quality["holdout_status"],
        })
    return members


def build_cohort_record(members: Sequence[Mapping[str, Any]], *, now: str) -> dict[str, Any]:
    """The sealed ``forward_cohort.v1`` record for these members. Pure."""
    context_sizes: dict[str, int] = {}
    for member in members:
        key = _context_key(member)
        context_sizes[key] = context_sizes.get(key, 0) + 1
    body = {
        "forward_cohort_version": FORWARD_COHORT_VERSION,
        "cohort_id": integrity.short_id("fwd_cohort", {
            "frozen_at": now, "members": [m["candidate_id"] for m in members]}),
        "frozen_at_utc": now,
        "eligibility": {
            "version": ELIGIBILITY_VERSION,
            "observation_min_backtest_closed": OBSERVATION_MIN_BACKTEST_CLOSED,
            "promotable_derivation_types": sorted(PROMOTABLE_DERIVATION_TYPES),
            "promotable_cost_basis_ranks": sorted(PROMOTABLE_COST_BASIS_RANKS),
            "promotable_evidence_depth_ranks": sorted(PROMOTABLE_EVIDENCE_DEPTH_RANKS),
            "one_member_per": "strategy_family + symbol_scope + timeframe",
        },
        "cohort_size": len(members),
        "context_sizes": dict(sorted(context_sizes.items())),
        "members": [dict(m) for m in members],
    }
    return {**body, "record_sha256": integrity.sha256_record(body)}


def freeze_cohort(root: Path | None = None, *, now: str, apply: bool = False) -> dict[str, Any]:
    """Freeze the next cohort from the store as it reads now; append it only with ``apply``.

    Reads the candidates, the pool, the pool's forward rows and the earlier cohorts through their
    verified readers, so a damaged store of any of them refuses the freeze. A lineage the forward
    book ever settled a row for is left out: were it re-promoted off its cohort record, its old
    pool rows would cover bars the cohort period also covered, and the arming door reads them. An
    empty cohort is refused on apply (``FORWARD_COHORT_EMPTY``): a record with no members would
    be a K of zero that nothing can be judged against."""
    pool_clocked = frozenset(
        str(row["candidate_id"]) for row in forward_book.read_forward_outcomes(root)
        if row.get("candidate_id"))
    members = eligible_members(
        read_candidates(root), load_active_pool(root),
        exclude_candidate_ids=member_candidate_ids(root) | pool_clocked,
    )
    record = build_cohort_record(members, now=now)
    if apply:
        if not members:
            raise ToolError(FORWARD_COHORT_EMPTY, "no lineage is eligible for a new forward cohort")
        path = _cohorts_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        with locked(path.with_suffix(".lock"), code=FORWARD_COHORT_LOCKED, label="forward cohorts"):
            read_cohorts(root)  # a damaged store refuses the append rather than growing
            with open(path, "a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
    return record


# --- the walker -------------------------------------------------------------------------------

def synthesize_entry(record: Mapping[str, Any]) -> dict[str, Any]:
    """The stand-in for a pool entry that ``replay_entry_bar`` reads. Pure.

    The admission evidence a promotion would lift off the row (both doors fail open without it),
    and the member's ``candidate_id`` as ``strategy_id`` so sibling members never share a
    ``position_id``. No artifact hash: nothing here is installed, and no artifact was approved."""
    cid = candidate_id(record)
    return {
        "strategy_id": cid,
        "candidate_id": cid,
        "generation_id": record.get("generation_id"),
        "strategy_rule_hash": record.get("strategy_rule_hash"),
        "strategy_spec": dict(record.get("strategy_spec") or {}),
        "status": "COHORT",
        **admission_evidence(record),
    }


def _fresh_state(lineage: str, symbol: str, timeframe: str, strategy_id: Any, now: str) -> dict[str, Any]:
    return {
        "lineage": lineage, "symbol": symbol, "timeframe": timeframe, "strategy_id": strategy_id,
        "first_tracked_at": now, "first_seen_candle": None, "last_seen_candle": None,
        "last_signal_at": None, "opens_count": 0, "position": None, "cooldown_remaining": 0,
    }


def _finalize_row(row: Mapping[str, Any], provenance: str = COHORT_PROVENANCE) -> dict[str, Any]:
    """Re-stamp a paper-kernel outcome row as this store's, and make the hash true again."""
    out = dict(row)
    out["provenance"] = provenance
    out.pop("record_sha256", None)
    out["record_sha256"] = integrity.sha256_record(out)
    return out


def advance_member(
    state: dict[str, Any],
    entry: Mapping[str, Any],
    spec: StrategySpec,
    rows: Sequence[Mapping[str, Any]],
    candles: Sequence[Mapping[str, Any]],
    *,
    symbol: str,
    timeframe: str,
    start: str,
    provenance: str = COHORT_PROVENANCE,
) -> list[dict[str, Any]]:
    """Advance one member-context over the bars it has not seen, from ``start`` on.

    ``walk_seed_span`` minus its one drop: the position still open after the last bar STAYS in
    ``state`` for the next run. Bars before ``start`` only warm the indicators; bars at or before
    ``state['last_seen_candle']`` are skipped by ``replay_entry_bar`` itself, which is what makes a
    re-run over an overlapping frame a no-op. Each bar is stamped with its own close time, so
    settlement ids are deterministic. Mutates ``state``; returns the finalized settled rows."""
    settled: list[dict[str, Any]] = []
    for row, candle in zip(rows, candles):
        bar_now = str((candle or {}).get("close_time") or "")
        if not bar_now or bar_now < start:
            continue
        outcome = forward_book.replay_entry_bar(
            state, entry, spec, row, candle, row.get("close"),
            symbol=symbol, timeframe=timeframe, now=bar_now)
        if outcome is not None:
            settled.append(_finalize_row(outcome, provenance))
    return settled


def read_cohort_outcomes(root: Path | None = None) -> list[dict[str, Any]]:
    """Every settled cohort row, verified exactly as the forward book's rows are."""
    return forward_book.read_sealed_rows(
        _outcomes_path(root), provenance=COHORT_PROVENANCE, label="forward cohort outcomes")


def load_positions(root: Path | None = None) -> dict[str, Any]:
    """The walker's per-member-context state. Missing is empty; unreadable refuses; so does a book
    this version did not write or an entry that does not say which member it walks.

    The outcomes the walker settles are sealed rows, but this file is mutable state, and what it
    holds decides where each member's replay resumes. So an entry is checked, not trusted
    (2026-09-23): its key is its own ``(lineage, symbol, timeframe)``, the lineage names a
    candidate a frozen cohort holds, the timeframe is one the market data knows, and its candle
    marks parse and run forward. A malformed entry is refused rather than dropped, since dropping
    it would restart that member from its selection and re-open trades already settled."""
    return load_book_at(_positions_path(root), members=lambda: member_candidate_ids(root),
                        label="forward cohort positions")


def load_book_at(path: Path, *, members: Callable[[], frozenset[str]], label: str) -> dict[str, Any]:
    """A walker's positions book at ``path``, checked as :func:`load_positions` describes; ``members``
    names the walk ids its lineages may hold. Shared with the null arm (`forward_cohort_null`)."""
    if not path.is_file():
        return {"entries": {}}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ToolError(FORWARD_COHORT_UNREADABLE, f"{label} unreadable: {exc}") from exc
    if not isinstance(raw, Mapping) or not isinstance(raw.get("entries"), Mapping):
        raise ToolError(FORWARD_COHORT_UNREADABLE, f"{label} are not a book-shaped mapping")
    if raw.get("forward_cohort_positions_version") != POSITIONS_VERSION:
        raise ToolError(FORWARD_COHORT_POSITIONS_INVALID,
                        f"{label} carry version "
                        f"{raw.get('forward_cohort_positions_version')!r}, not {POSITIONS_VERSION!r}")
    entries = {str(key): _checked_entry(str(key), value) for key, value in raw["entries"].items()}
    held = members() if entries else frozenset()
    strangers = sorted(key for key, entry in entries.items()
                       if entry["lineage"].removeprefix("cand:") not in held)
    if strangers:
        raise ToolError(FORWARD_COHORT_POSITIONS_INVALID,
                        f"{label} walk lineages no frozen cohort holds: {', '.join(strangers[:5])}"
                        + (f" and {len(strangers) - 5} more" if len(strangers) > 5 else ""))
    return {"entries": entries}


def _checked_entry(key: str, value: Any) -> dict[str, Any]:
    """One entry of the positions book, or FORWARD_COHORT_POSITIONS_INVALID naming what is wrong."""
    def invalid(why: str) -> ToolError:
        return ToolError(FORWARD_COHORT_POSITIONS_INVALID, f"forward cohort position {key!r}: {why}")

    if not isinstance(value, Mapping):
        raise invalid("is not a mapping")
    entry = dict(value)
    lineage, symbol, timeframe = entry.get("lineage"), entry.get("symbol"), entry.get("timeframe")
    if not (isinstance(lineage, str) and lineage.startswith("cand:") and len(lineage) > len("cand:")):
        raise invalid(f"lineage {lineage!r} is not a cohort candidate lineage")
    if timeframe not in TIMEFRAMES:
        raise invalid(f"timeframe {timeframe!r} is not one the market data knows")
    if not (isinstance(symbol, str) and symbol) or key != forward_book.book_key(lineage, symbol, timeframe):
        raise invalid("its key is not its own (lineage, symbol, timeframe)")
    marks = {}
    for field in ("first_seen_candle", "last_seen_candle"):
        mark = entry.get(field)
        if mark is None:
            continue
        try:
            marks[field] = timeutil.parse_iso(str(mark))
        except (ValueError, TypeError):
            raise invalid(f"{field} {mark!r} does not parse") from None
    if len(marks) == 2 and marks["first_seen_candle"] > marks["last_seen_candle"]:
        raise invalid("first_seen_candle is after last_seen_candle")
    if "last_seen_candle" in marks and "first_seen_candle" not in marks:
        raise invalid("has a last_seen_candle but no first_seen_candle")
    return entry


def _assert_marks_run_forward(before: Mapping[str, Any], after: Mapping[str, Any]) -> None:
    """Refuse to write a book in which a member's last seen candle moved back or disappeared: the
    replay skips bars at or before that mark, so moving it back would re-open settled trades."""
    for key, old in before.items():
        old_mark = old.get("last_seen_candle")
        if old_mark is None:
            continue
        new = after.get(key)
        new_mark = None if new is None else new.get("last_seen_candle")
        if new_mark is None or timeutil.parse_iso(str(new_mark)) < timeutil.parse_iso(str(old_mark)):
            raise ToolError(FORWARD_COHORT_POSITIONS_INVALID,
                            f"forward cohort position {key!r}: last_seen_candle would move from "
                            f"{old_mark} to {new_mark}; nothing was written")


def _write_positions(book: Mapping[str, Any], *, root: Path | None, now: str) -> None:
    _write_book_at(_positions_path(root), book, now=now)


def _write_book_at(path: Path, book: Mapping[str, Any], *, now: str) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"forward_cohort_positions_version": POSITIONS_VERSION,
                               "updated_at_utc": now, "entries": book["entries"]},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def walk_plan(root: Path | None = None) -> dict[tuple[str, str], list[tuple[dict[str, Any], dict[str, Any]]]]:
    """Every member-context to advance, grouped by ``(symbol, timeframe)`` so each context is
    fetched once: ``{context: [(member, record), ...]}``. A member whose row the store no longer
    resolves, or whose row's rule hash is not the one frozen, is left out — the clock belongs to
    the frozen evidence, and a row that changed underneath it is not that evidence."""
    latest: dict[str, dict[str, Any]] = {}
    for record in read_candidates(root):
        latest[candidate_id(record)] = record
    plan: dict[tuple[str, str], list[tuple[dict[str, Any], dict[str, Any]]]] = {}
    seen: set[str] = set()
    for cohort in read_cohorts(root):
        for member in cohort.get("members") or []:
            cid = str(member.get("candidate_id") or "")
            record = latest.get(cid)
            if not cid or cid in seen or record is None:
                continue
            if record.get("strategy_rule_hash") != member.get("strategy_rule_hash"):
                continue
            seen.add(cid)
            record = {**record, "created_at_utc": member.get("selected_at_utc")}
            for symbol in member.get("symbol_scope") or []:
                plan.setdefault((str(symbol), str(member.get("timeframe"))), []).append((member, record))
    return plan


def bars_to_fetch(starts: Iterable[str], *, timeframe: str, now: str) -> int | None:
    """How many bars reach back to the oldest ``start`` plus the warm-up, capped. None when no
    start or the timeframe is unknown."""
    minutes = TIMEFRAMES.get(timeframe)
    moments = [timeutil.parse_iso(s) for s in starts if s]
    if not minutes or not moments:
        return None
    span = (timeutil.parse_iso(now) - min(moments)) / timedelta(minutes=minutes)
    return max(1, min(MAX_WALK_BARS, int(span) + 2 + WARMUP_BARS))


@dataclass(frozen=True)
class WalkTrack:
    """What one walker walks: the real cohort's members, or the null arm's twins
    (`forward_cohort_null`). Same frames, same per-bar transition, same sealed-row append; each
    track has its own plan, entries, positions book and outcomes store.

    ``plan(root)`` is ``{(symbol, timeframe): [(member, record), ...]}``, where ``member`` carries
    the walk id as ``candidate_id`` and the clock start as ``selected_at_utc``, and ``record`` is
    what ``spec_of`` / ``entry_of`` / ``rows_of`` read."""

    plan: Callable[[Path | None], dict[tuple[str, str], list[tuple[dict[str, Any], dict[str, Any]]]]]
    spec_of: Callable[[Mapping[str, Any], Mapping[str, Any]], StrategySpec]
    entry_of: Callable[[Mapping[str, Any], Mapping[str, Any]], dict[str, Any]]
    rows_of: Callable[[Mapping[str, Any], Sequence[Any], Sequence[Any]], Sequence[Any]]
    book_path: Callable[[Path | None], Path]
    load_book: Callable[[Path | None], dict[str, Any]]
    outcomes_path: Callable[[Path | None], Path]
    read_outcomes: Callable[[Path | None], list[dict[str, Any]]]
    provenance: str
    label: str


def run_cohort_walk(
    root: Path | None = None,
    *,
    now: str,
    frame_for: Any,
    persist: bool = True,
) -> dict[str, Any]:
    """Advance every member-context of every frozen cohort to the newest closed bar.

    ``frame_for(symbol, timeframe, bars)`` returns ``(rows, candles)`` — the replay frame the
    factory mined on, fetched once per context (the operator script and the scheduled fire pass
    the collector-backed one; tests pass their own). A context that fails to fetch costs that
    context alone and is named in ``failed``. The frames are fetched OUTSIDE the positions lock
    and the state is re-read under it, so a concurrent run can only have moved
    ``last_seen_candle`` forward, which the replay then skips."""
    return walk_track(COHORT_TRACK, root, now=now, frame_for=frame_for, persist=persist)


def walk_track(
    track: WalkTrack,
    root: Path | None = None,
    *,
    now: str,
    frame_for: Any,
    persist: bool = True,
) -> dict[str, Any]:
    """The walk :func:`run_cohort_walk` describes, over one track."""
    plan = track.plan(root)
    try:
        before = track.load_book(root)["entries"]
    except ToolError:
        before = {}
    frames: dict[tuple[str, str], tuple[Sequence[Any], Sequence[Any]]] = {}
    failed: list[str] = []
    for (symbol, timeframe), members in sorted(plan.items()):
        starts = []
        for member, record in members:
            key = forward_book.book_key(f"cand:{member['candidate_id']}", symbol, timeframe)
            starts.append(str((before.get(key) or {}).get("last_seen_candle") or member["selected_at_utc"]))
        bars = bars_to_fetch(starts, timeframe=timeframe, now=now)
        if bars is None:
            failed.append(f"{symbol} {timeframe}: unknown timeframe")
            continue
        try:
            frames[(symbol, timeframe)] = frame_for(symbol, timeframe, bars)
        except MvpRuntimeError as exc:
            failed.append(f"{symbol} {timeframe}: {getattr(exc, 'reason_code', type(exc).__name__)}")

    settled_rows: list[dict[str, Any]] = []
    opened = 0

    def _advance(book: dict[str, Any]) -> None:
        nonlocal opened
        entries = book["entries"]
        for (symbol, timeframe), (rows, candles) in frames.items():
            for member, record in plan[(symbol, timeframe)]:
                try:
                    spec = track.spec_of(member, record)
                except Exception:
                    continue
                entry = track.entry_of(member, record)
                lineage = f"cand:{member['candidate_id']}"
                key = forward_book.book_key(lineage, symbol, timeframe)
                state = entries.setdefault(
                    key, _fresh_state(lineage, symbol, timeframe, entry["strategy_id"], now))
                opens_before = int(state.get("opens_count") or 0)
                settled_rows.extend(advance_member(
                    state, entry, spec, track.rows_of(member, rows, candles), candles,
                    symbol=symbol, timeframe=timeframe, start=member["selected_at_utc"],
                    provenance=track.provenance))
                opened += int(state.get("opens_count") or 0) - opens_before

    if persist:
        path = track.book_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        with locked(path.with_suffix(".lock"), code=FORWARD_COHORT_LOCKED, label=track.label):
            book = track.load_book(root)
            marks_before = {key: {"last_seen_candle": entry.get("last_seen_candle")}
                            for key, entry in book["entries"].items()}
            _advance(book)
            _assert_marks_run_forward(marks_before, book["entries"])
            forward_book.append_sealed_rows(
                settled_rows, path=track.outcomes_path(root), read=lambda: track.read_outcomes(root))
            _write_book_at(path, book, now=now)
    else:
        _advance(track.load_book(root))
    return {
        "contexts": len(plan), "walked": len(frames), "failed": failed,
        "members": len({m["candidate_id"] for ms in plan.values() for m, _ in ms}),
        "opened": opened, "settled": len(settled_rows),
    }



# The real cohort's track: members resolved through the candidate store, walked as they are.
COHORT_TRACK = WalkTrack(
    plan=lambda root: walk_plan(root),
    spec_of=lambda member, record: StrategySpec.from_dict(record["strategy_spec"]),
    entry_of=lambda member, record: synthesize_entry(record),
    rows_of=lambda member, rows, candles: rows,
    book_path=lambda root: _positions_path(root),
    load_book=lambda root: load_positions(root),
    outcomes_path=lambda root: _outcomes_path(root),
    read_outcomes=lambda root: read_cohort_outcomes(root),
    provenance=COHORT_PROVENANCE,
    label="forward cohort positions",
)

# --- the report -------------------------------------------------------------------------------

def priced_nets(record: Mapping[str, Any], outcomes: Iterable[Mapping[str, Any]]) -> list[float]:
    """The per-trade net R of the rows ``judge_forward`` prices: ``forward_outcomes_for`` at the
    record's cutoff, a row that cannot price itself or place itself in time dropped."""
    nets: list[float] = []
    for row in forward_outcomes_for(record, outcomes):
        net = net_result_r(row)
        if net is None:
            continue
        try:
            timeutil.parse_iso(str(row.get("created_at_utc") or ""))
        except (ValueError, TypeError):
            continue
        nets.append(float(net))
    return nets


def pooled_spread(nets_by_member: Iterable[Sequence[float]]) -> float | None:
    """The standard deviation of every priced trade across the given members, pooled; None
    below two trades. The scale one trade of this cohort actually moves by."""
    pooled = [n for nets in nets_by_member for n in nets]
    return round(statistics.stdev(pooled), 6) if len(pooled) >= 2 else None


def trade_bounds(nets: Sequence[float], *, spread_floor: float | None) -> dict[str, Any]:
    """The lineage's per-trade net mean and its lower bound
    ``mean - CONFIDENCE_Z * max(sd, spread_floor) / sqrt(n)``, for display. The judge never
    sees these.

    **Why the spread has a floor.** A lineage with a fixed take-profit that has won every trade
    so far has a spread of cost noise: on 2026-09-23 the board's top three were n=4 at
    ``[1.73, 1.74, 1.76, 1.73]`` and two n=2 pairs, sd 0.016 each, so the bound sat 0.02R under
    the mean and charged a four-trade record almost nothing. A t critical value instead of z did
    not help — measured on the live cohort it left the top six unchanged, because it multiplies
    the same near-zero spread. The sample spread of a handful of trades is the number not to
    trust, so it is floored at the cohort's pooled spread (:func:`pooled_spread`, 1.39R that
    day): a short record is charged at least the noise the cohort's trades actually show. With
    the floor, n=4 at +1.74R bounds at +0.38R and n=2 at +1.54R goes negative.

    Given below the trade floor too, where the verdict has no mean. The bound is None at one
    trade — the floor would otherwise let a single +5R trade lead — and where no spread exists
    at all."""
    mean = statistics.mean(nets) if nets else None
    bound = None
    if len(nets) >= 2:
        spread = max(statistics.stdev(nets), spread_floor or 0.0)
        if spread > 0:
            bound = round(mean - CONFIDENCE_Z * spread / math.sqrt(len(nets)), 6)
    return {"trade_mean_r": None if mean is None else round(mean, 6), "trade_lower_bound_r": bound}


def maturity_of(member: Mapping[str, Any]) -> str:
    """One member line's maturity: the judge's verdict when it has one, else where its priced rows
    stand against its timeframe's trade floor (:func:`forward_confirmation.min_forward_trades`).

    ``FORWARD_UNDERPOWERED`` is not a verdict here: a record at its floor that leaned WITH the edge
    and could not resolve it reads MATURE, so 반박 counts only records that leaned against it, and
    such a member stays eligible to lead (ranked on its own lower bound like any other)."""
    status = member.get("status")
    if status == FORWARD_CONFIRMED:
        return MATURITY_CONFIRMED
    if status == FORWARD_CONTRADICTED:
        return MATURITY_CONTRADICTED
    if "priceable_count" not in member:
        return MATURITY_UNRESOLVED
    floor = min_forward_trades(member.get("timeframe"))
    return MATURITY_MATURE if int(member.get("priceable_count") or 0) >= floor else MATURITY_EXPLORATORY


def cohort_report(root: Path | None = None) -> list[dict[str, Any]]:
    """Per cohort, each member's forward numbers over the cohort's own rows. Reads only.

    ``judge_forward`` is handed the member's row with ``created_at_utc`` set to the frozen
    selection time, so its cutoff is the cohort's, whatever was appended to the store since.
    The status is the judge's arithmetic and nothing more: under option A it opens no door.
    The display bounds (:func:`trade_bounds`) floor every member's spread at the one spread
    pooled over every member of every frozen cohort, carried on each line as
    ``trade_spread_floor_r``."""
    latest: dict[str, dict[str, Any]] = {}
    for record in read_candidates(root):
        latest[candidate_id(record)] = record
    rows = read_cohort_outcomes(root)
    # Two passes: the floor pools every member's trades, so it exists only once all are priced.
    priced: list[tuple[Mapping[str, Any], list[tuple[Mapping[str, Any], Any, list[float]]]]] = []
    for cohort in read_cohorts(root):
        members = []
        for member in cohort.get("members") or []:
            record = latest.get(str(member.get("candidate_id")))
            judged = None if record is None else {**record, "created_at_utc": member.get("selected_at_utc")}
            members.append((member, judged, [] if judged is None else priced_nets(judged, rows)))
        priced.append((cohort, members))
    floor = pooled_spread(nets for _, members in priced for _, _, nets in members)
    report: list[dict[str, Any]] = []
    for cohort, members in priced:
        lines = []
        for member, judged, nets in members:
            if judged is None:
                lines.append({"candidate_id": member.get("candidate_id"), "status": "UNRESOLVED",
                              "maturity": MATURITY_UNRESOLVED})
                continue
            line = {
                "candidate_id": member.get("candidate_id"),
                "strategy_family": member.get("strategy_family"),
                "timeframe": member.get("timeframe"),
                "context": _context_key(member),
                "context_size": (cohort.get("context_sizes") or {}).get(_context_key(member)),
                **judge_forward(judged, rows),
                **trade_bounds(nets, spread_floor=floor),
                "trade_spread_floor_r": floor,
                "trade_floor": min_forward_trades(member.get("timeframe")),
            }
            line["maturity"] = maturity_of(line)
            lines.append(line)
        report.append({
            "cohort_id": cohort.get("cohort_id"), "frozen_at_utc": cohort.get("frozen_at_utc"),
            "cohort_size": cohort.get("cohort_size"), "members": lines,
        })
    return report


def board_summary(root: Path | None = None) -> dict[str, Any] | None:
    """What the daily board shows of the cohort; None before any cohort is frozen. Reads only.

    Counts over every frozen cohort's members — with any settled row, at their timeframe's
    trade floor, and per judge status — and the three members an operator would read first.
    Under option A none of this opens a door, but the leaders ARE the list an operator may
    choose a pool promotion from, so:

    - **A FORWARD_CONTRADICTED member is never a leader.** Its own record refuted it. Until
      2026-09-23 the key was CONFIRMED, then most rows — and with nothing CONFIRMED, most rows
      meant the oldest trading lineages, whose record was long enough to be judged and fail:
      that day's line was three CONTRADICTED 4h shorts at -0.23R, -0.55R and -0.44R.
    - **Then CONFIRMED first, then the trade-level lower bound, highest first**
      (:func:`trade_bounds`), not the mean. The mean has the mirror failure of row count: one
      +2R trade at n=1 outranks +0.3R over twenty. The bound charges a short record for its
      noise. It is the judge's interval with one departure the board owns and says so: the
      spread is floored at the cohort's pooled spread (``spread_floor_r``), because a few
      same-sized wins measure almost no spread (see :func:`trade_bounds`). A member whose
      bound cannot be computed yet (one trade, or no spread) ranks after every member with one."""
    cohorts = read_cohorts(root)
    if not cohorts:
        return None
    from .forward_confirmation import FORWARD_CONFIRMED, FORWARD_CONTRADICTED, min_forward_trades

    members = [m for cohort in cohort_report(root) for m in cohort["members"]]
    status_counts: dict[str, int] = {}
    for m in members:
        status_counts[str(m.get("status"))] = status_counts.get(str(m.get("status")), 0) + 1
    ranked = sorted(
        (m for m in members
         if (m.get("priceable_count") or 0) > 0 and m.get("status") != FORWARD_CONTRADICTED),
        key=lambda m: (m.get("status") != FORWARD_CONFIRMED, m.get("trade_lower_bound_r") is None,
                       -(m.get("trade_lower_bound_r") or 0.0), str(m.get("candidate_id"))),
    )
    last_walk = None
    try:
        last_walk = json.loads(_positions_path(root).read_text(encoding="utf-8")).get("updated_at_utc")
    except (OSError, ValueError, AttributeError):
        pass
    return {
        "cohorts": len(cohorts),
        "members": len(members),
        "with_rows": sum(1 for m in members if (m.get("priceable_count") or 0) > 0),
        "at_floor": sum(1 for m in members
                        if (m.get("priceable_count") or 0) >= min_forward_trades(m.get("timeframe"))),
        "status_counts": dict(sorted(status_counts.items())),
        "maturity_counts": {maturity: n for maturity in MATURITIES
                            if (n := sum(1 for m in members if (m.get("maturity") or maturity_of(m)) == maturity))},
        "leaders": [{**{k: m.get(k) for k in ("candidate_id", "timeframe", "priceable_count",
                                              "trade_mean_r", "trade_lower_bound_r", "status")},
                     "maturity": m.get("maturity") or maturity_of(m),
                     "trade_floor": min_forward_trades(m.get("timeframe"))}
                    for m in ranked[:3]],
        "spread_floor_r": next((m.get("trade_spread_floor_r") for m in members
                                if "trade_spread_floor_r" in m), None),
        "last_walk_utc": last_walk,
    }


def memoized_frames(frame_for: Any) -> Any:
    """``frame_for`` that fetches each context once per fire: a later call for the same context and
    no more bars reuses the frame (the null arm walks the members' contexts after them)."""
    cache: dict[tuple[str, str], tuple[int, Any]] = {}

    def fetch(symbol: str, timeframe: str, bars: int) -> Any:
        held = cache.get((symbol, timeframe))
        if held is not None and held[0] >= bars:
            return held[1]
        frame = frame_for(symbol, timeframe, bars)
        cache[(symbol, timeframe)] = (bars, frame)
        return frame

    return fetch


def status_line(summary: Mapping[str, Any]) -> str:
    """One walk, as the scheduler's status column carries it."""
    line = ("forward_cohort members=%s contexts=%s walked=%s opened=%s settled=%s" % (
        summary.get("members"), summary.get("contexts"), summary.get("walked"),
        summary.get("opened"), summary.get("settled")))
    failed = summary.get("failed") or []
    if failed:
        line += " failed=" + ";".join(failed)
    return line


# --- the collector-backed frame ---------------------------------------------------------------

def collector_frames(root: Path | None = None, *, now: str) -> Any:
    """``frame_for`` for :func:`run_cohort_walk`, backed by the venue: the frame the factory
    mined on — every mining leg, the liquidation feed included (``null_control``'s wiring) —
    fetched ``bars`` deep. The collector is selected once per walk, through its env gate."""
    from .factory import build_replay_frame
    from .feed_assembly import attach_mining_legs
    from .market_data import collect_market_data, select_liquidation_feed, select_market_data_collector

    collector = select_market_data_collector(now=now, root=root)
    liquidation_feed = select_liquidation_feed(now=now, root=root)

    def frame_for(symbol: str, timeframe: str, bars: int) -> tuple[Sequence[Any], Sequence[Any]]:
        snapshot, _ = collect_market_data(symbol, timeframe, collector=collector, now=now, limit=bars)
        attach_mining_legs(snapshot, collector=collector, timeframe=timeframe, now=now, root=root,
                           liquidation_feed=liquidation_feed, candle_target=lambda _tf: bars)
        frame = build_replay_frame(snapshot)
        return frame.rows, frame.candles

    return frame_for

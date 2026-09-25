"""The forward cohort's null arm: one coin-flip twin per member, walked beside it (2026-09-24; item 7
of `docs/proposals/FORWARD_COHORT_OFF_POOL_V0.1.md`, built as a companion record, Thomas's choice B).

**What it measures.** The forward judge confirms a lineage when its forward record clears its own
noise. How often does a lineage with NO information in its entry get confirmed by the same judge,
through the same exits, on the same bars? The design record's option A was chosen partly because
nobody had measured that rate. A twin that keeps its parent's exits, risk, direction and admission
evidence, but enters on a seeded coin flip, is walked by the same walker, so its judge verdicts are
that rate, per timeframe.

**A companion record, not a change to the cohort.** A cohort's membership never changes after freeze,
so the null arm is its own sealed record per cohort (`forward_cohort_nulls.v1`), frozen once and never
edited. Each twin carries everything the walker needs: the null spec, the seed, the signal rate and
the parent's admission evidence. A null spec cannot live in the candidate store (`null_control`
keeps its feature out of `factory.NUMERIC_FEATURES`, so nothing can mint or promote it), so the walk
resolves nothing through that store.

**Its clock is its parent's.** A twin starts at its parent's selection time. It has no selection to
be biased by, so starting where the parent starts gives the two arms the same bars. Its coin is
deterministic per bar, a SHA-256 of the twin's seed and the bar's open time, so a re-walk over the
same bars re-draws the same coins and settles nothing new.

**The rate is a recorded judgement:** the parent's backtest trade rate PER LEG, ``closed_count /
(bars_replayed x symbols_replayed)``. That paces the twin's trades like its parent's on each leg it
walks, so it reaches the trade floor at about the same time. `null_control` notes that a coin flip at
a matched SIGNAL rate completes about twice the trades, because a real entry's signals cluster;
matching the trade rate avoids that.

**Why there is a v2 (2026-09-25).** v1 used ``closed_count / bars_replayed``. A pooled parent's
``closed_count`` sums every leg while ``bars_replayed`` is one leg's (the shallowest), so its twin
traded each leg at about five times the parent's pace — 51 of the first cohort's 115 twins (pooled
1d 23 and 4h 18, and ten F9 rows whose single-symbol spec carries five-leg evidence). They reached the
trade floor sooner and got more judge looks, so the board's "real vs null" comparison was not at the
members' pace at 4h and 1d (1h had no pooled parent). v1 records are sealed and stay readable;
``freeze_nulls`` adds a v2 record that names the v1 it supersedes, with the SAME seeds, so a v2 twin
is its v1 twin at the corrected threshold. v2 twins have their own walk ids (``null_v2_<parent>``):
the v1 rows and book marks stay under the v1 ids and are never priced into a v2 twin. Only the
latest version of each cohort's arm is walked and reported (:func:`active_null_records`).

**Isolation.** The null arm has its own positions book and its own outcomes store (provenance
``mvp_forward_cohort_null``). Nothing that reads the cohort reads it: not `member_candidate_ids`
(the seeder's option-A set), not `cohort_report`'s pooled spread, not the board's leaders. It opens
no door and asks for nothing.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from runtime.read_only_kernel import integrity

from .. import jsonl
from ..errors import ToolError
from ..filelock import locked
from . import forward_book
from .candidate_identity import candidate_id
from .forward_cohort import (
    FIRST_VERDICT_FIELDS, FORWARD_COHORT_LOCKED, MATURITY_CONFIRMED, MATURITY_CONTRADICTED, WalkTrack,
    cohort_report, first_verdict_suffix, load_book_at, load_positions, maturity_of, read_cohorts,
    unparseable_suffix, walk_track,
)
from .forward_confirmation import FORWARD_UNDERPOWERED, judge_forward, min_forward_trades
from .null_control import NULL_FEATURE, _null_spec
from .pool_state import read_candidates
from .state import state_dir
from .strategy import StrategySpec
from .strategy_artifact import admission_evidence

NULLS_FILENAME = "forward_cohort_nulls.jsonl"
NULLS_VERSION_V1 = "forward_cohort_nulls.v1"
NULLS_VERSION = "forward_cohort_nulls.v2"
# Oldest first: a cohort's ACTIVE arm is its record of the latest version here.
NULLS_VERSIONS = (NULLS_VERSION_V1, NULLS_VERSION)
NULL_POSITIONS_FILENAME = "forward_cohort_null_positions.json"
NULL_OUTCOMES_FILENAME = "forward_cohort_null_outcomes.jsonl"
NULL_PROVENANCE = "mvp_forward_cohort_null"
NULL_ID_PREFIX = "null_"
NULL_ID_PREFIX_V2 = "null_v2_"
RATE_RULE_V1 = "parent backtest closed_count / bars_replayed"
RATE_RULE = "parent backtest closed_count / (bars_replayed x symbols_replayed)"

# The null records' own codes, so an operator can tell a damaged null arm from a damaged cohort. The
# null positions book shares the cohort's loader and its codes (`forward_cohort.load_book_at`).
FORWARD_COHORT_NULLS_UNREADABLE = "FORWARD_COHORT_NULLS_UNREADABLE"
FORWARD_COHORT_NULLS_TAMPERED = "FORWARD_COHORT_NULLS_TAMPERED"


def _nulls_path(root: Path | None) -> Path:
    return state_dir(root) / NULLS_FILENAME


def _null_positions_path(root: Path | None) -> Path:
    return state_dir(root) / NULL_POSITIONS_FILENAME


def _null_outcomes_path(root: Path | None) -> Path:
    return state_dir(root) / NULL_OUTCOMES_FILENAME


def null_id(parent_candidate_id: str, *, version: str = NULLS_VERSION_V1) -> str:
    """A twin's walk id: never a candidate id, so its rows can never be attributed to a lineage.

    Per version, because a re-frozen arm must not inherit the rows its predecessor settled. The
    default is the v1 shape, which the hypothesis trials' twins also use in stores of their own."""
    prefix = NULL_ID_PREFIX_V2 if version == NULLS_VERSION else NULL_ID_PREFIX
    return f"{prefix}{parent_candidate_id}"


def trade_rate(record: Mapping[str, Any]) -> float | None:
    """The parent's backtest trades per bar on EACH LEG, or None when the row cannot say.

    ``closed_count`` is pooled over ``symbols_replayed`` legs and ``bars_replayed`` is one leg's, so
    the per-leg pace divides by both (see the module docstring's v2 note). Absent
    ``symbols_replayed`` is one leg — every row minted before pooling."""
    evidence = record.get("backtest_evidence") or {}
    closed, bars = evidence.get("closed_count"), evidence.get("bars_replayed")
    legs = evidence.get("symbols_replayed") or 1
    for value in (closed, bars, legs):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            return None
    return min(1.0, float(closed) / (float(bars) * float(legs)))


def coin(seed: str, open_time: Any) -> float:
    """The twin's draw on one bar, uniform in [0, 1): a SHA-256 of the seed and the bar's open time.
    Deterministic per bar, never per walk, so re-walking a bar re-draws the same coin."""
    digest = hashlib.sha256(f"{seed}|{open_time}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / 2.0 ** 64


def build_null_record(cohort: Mapping[str, Any], latest: Mapping[str, Mapping[str, Any]], *,
                      now: str, supersedes: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """One twin per member of ``cohort``, from each member's frozen row as the store holds it now.
    A member whose row is gone, changed its rule hash, or cannot give a rate is listed in ``skipped``
    with the reason, never silently dropped. Pure."""
    cohort_id = str(cohort.get("cohort_id") or "")
    twins: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for member in cohort.get("members") or []:
        parent = str(member.get("candidate_id") or "")
        record = latest.get(parent)
        if record is None:
            skipped.append({"parent_candidate_id": parent, "reason": "row_gone"})
            continue
        if record.get("strategy_rule_hash") != member.get("strategy_rule_hash"):
            skipped.append({"parent_candidate_id": parent, "reason": "rule_hash_changed"})
            continue
        rate = trade_rate(record)
        if rate is None:
            skipped.append({"parent_candidate_id": parent, "reason": "no_trade_rate"})
            continue
        try:
            spec = _null_spec(StrategySpec.from_dict(record["strategy_spec"]), rate)
        except Exception as exc:  # noqa: BLE001 — a spec that cannot be twinned is recorded, not fatal
            skipped.append({"parent_candidate_id": parent, "reason": f"null_unbuildable: {type(exc).__name__}"})
            continue
        twins.append({
            "null_id": null_id(parent, version=NULLS_VERSION),
            "parent_candidate_id": parent,
            "parent_rule_hash": member.get("strategy_rule_hash"),
            "selected_at_utc": member.get("selected_at_utc"),
            "symbol_scope": list(member.get("symbol_scope") or []),
            "timeframe": member.get("timeframe"),
            "strategy_family": member.get("strategy_family"),
            "signal_rate": round(rate, 6),
            "seed": f"{cohort_id}|{parent}",
            "null_spec": spec.to_dict(),
            "admission_evidence": admission_evidence(record),
        })
    body = {
        "forward_cohort_nulls_version": NULLS_VERSION,
        "cohort_id": cohort_id,
        "frozen_at_utc": now,
        "rate_rule": RATE_RULE,
        "null_size": len(twins),
        "members": twins,
        "skipped": skipped,
    }
    if supersedes is not None:
        # The chain, so the record itself says which arm it replaces and why the ids moved.
        body["supersedes"] = {"version": supersedes.get("forward_cohort_nulls_version"),
                              "record_sha256": supersedes.get("record_sha256"),
                              "rate_rule": supersedes.get("rate_rule")}
    return {**body, "record_sha256": integrity.sha256_record(body)}


def read_null_records(root: Path | None = None) -> list[dict[str, Any]]:
    """Every frozen null arm, verified like the cohorts: sealed ``forward_cohort_nulls.v1`` records."""
    records: list[dict[str, Any]] = []
    for lineno, record in jsonl.iter_numbered(
        _nulls_path(root), read_code=FORWARD_COHORT_NULLS_UNREADABLE, label="forward cohort nulls",
        exc_type=ToolError,
    ):
        if not isinstance(record, dict) or record.get("forward_cohort_nulls_version") not in NULLS_VERSIONS:
            raise ToolError(FORWARD_COHORT_NULLS_TAMPERED,
                            f"forward cohort nulls line {lineno} is not one of {NULLS_VERSIONS}")
        stored = record.get("record_sha256")
        body = {k: v for k, v in record.items() if k != "record_sha256"}
        if not isinstance(stored, str) or integrity.sha256_record(body) != stored:
            raise ToolError(FORWARD_COHORT_NULLS_TAMPERED, f"forward cohort nulls line {lineno} fails its self-hash")
        records.append(record)
    return records


def active_null_records(root: Path | None = None) -> list[dict[str, Any]]:
    """Each cohort's arm at its latest version — the one walked, reported and compared."""
    active: dict[str, dict[str, Any]] = {}
    for record in read_null_records(root):
        cohort = str(record.get("cohort_id"))
        held = active.get(cohort)
        rank = NULLS_VERSIONS.index(record["forward_cohort_nulls_version"])
        if held is None or rank >= NULLS_VERSIONS.index(held["forward_cohort_nulls_version"]):
            active[cohort] = record
    return list(active.values())


def active_null_ids(root: Path | None = None) -> frozenset[str]:
    return frozenset(str(t["null_id"]) for r in active_null_records(root) for t in r.get("members") or [])


def null_ids(root: Path | None = None) -> frozenset[str]:
    """Every twin id of every version — the positions book's members. A superseded twin's entries
    stay in the book (its marks are where its walk stopped), and `load_book_at` refuses ids it
    is not given."""
    return frozenset(str(t["null_id"]) for r in read_null_records(root) for t in r.get("members") or [])


def freeze_nulls(root: Path | None = None, *, now: str, apply: bool = False) -> list[dict[str, Any]]:
    """A current-version null arm for every frozen cohort that has none; appended only with
    ``apply``. Frozen once per version: a cohort whose arm is v1 gets a v2 that names it
    (``supersedes``), and a cohort that already has a v2 gets nothing."""
    records = read_null_records(root)
    have = {str(r.get("cohort_id")) for r in records if r.get("forward_cohort_nulls_version") == NULLS_VERSION}
    older = {str(r.get("cohort_id")): r for r in active_null_records(root)}
    latest = {candidate_id(record): record for record in read_candidates(root)}
    built = [build_null_record(cohort, latest, now=now, supersedes=older.get(str(cohort.get("cohort_id"))))
             for cohort in read_cohorts(root)
             if str(cohort.get("cohort_id")) not in have]
    if apply and built:
        path = _nulls_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        with locked(path.with_suffix(".lock"), code=FORWARD_COHORT_LOCKED, label="forward cohort nulls"):
            present = {str(r.get("cohort_id")) for r in read_null_records(root)
                       if r.get("forward_cohort_nulls_version") == NULLS_VERSION}
            with open(path, "a", encoding="utf-8", newline="\n") as handle:
                for record in built:
                    if record["cohort_id"] in present:
                        continue
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
    return built


def null_walk_plan(root: Path | None = None) -> dict[tuple[str, str], list[tuple[dict[str, Any], dict[str, Any]]]]:
    """Every twin-context, grouped by ``(symbol, timeframe)`` like the cohort's plan."""
    plan: dict[tuple[str, str], list[tuple[dict[str, Any], dict[str, Any]]]] = {}
    seen: set[str] = set()
    for record in active_null_records(root):
        for twin in record.get("members") or []:
            nid = str(twin.get("null_id") or "")
            if not nid or nid in seen:
                continue
            seen.add(nid)
            walk = {"candidate_id": nid, "selected_at_utc": twin.get("selected_at_utc"), "seed": twin.get("seed")}
            for symbol in twin.get("symbol_scope") or []:
                plan.setdefault((str(symbol), str(twin.get("timeframe"))), []).append((walk, twin))
    return plan


def null_entry(twin: Mapping[str, Any]) -> dict[str, Any]:
    """The stand-in pool entry for a twin: its own id, its null spec, its parent's admission evidence."""
    nid = str(twin["null_id"])
    return {
        "strategy_id": nid,
        "candidate_id": nid,
        "generation_id": None,
        "strategy_rule_hash": (twin.get("null_spec") or {}).get("strategy_rule_hash"),
        "strategy_spec": dict(twin.get("null_spec") or {}),
        "status": "COHORT_NULL",
        **dict(twin.get("admission_evidence") or {}),
    }


def null_rows(walk: Mapping[str, Any], rows: Sequence[Any], candles: Sequence[Any]) -> list[Any]:
    """The context's rows with the twin's coin on each: copies, so the real members' rows stay clean."""
    seed = str(walk["seed"])
    return [None if row is None else {**row, NULL_FEATURE: coin(seed, (candle or {}).get("open_time"))}
            for row, candle in zip(rows, candles)]


def load_null_positions(root: Path | None = None) -> dict[str, Any]:
    return load_book_at(_null_positions_path(root), members=lambda: null_ids(root),
                        label="forward cohort null positions")


def read_null_outcomes(root: Path | None = None) -> list[dict[str, Any]]:
    return forward_book.read_sealed_rows(
        _null_outcomes_path(root), provenance=NULL_PROVENANCE, label="forward cohort null outcomes")


NULL_TRACK = WalkTrack(
    plan=lambda root: null_walk_plan(root),
    spec_of=lambda walk, twin: StrategySpec.from_dict(twin["null_spec"]),
    entry_of=lambda walk, twin: null_entry(twin),
    rows_of=lambda walk, rows, candles: null_rows(walk, rows, candles),
    book_path=lambda root: _null_positions_path(root),
    load_book=lambda root: load_null_positions(root),
    outcomes_path=lambda root: _null_outcomes_path(root),
    read_outcomes=lambda root: read_null_outcomes(root),
    provenance=NULL_PROVENANCE,
    label="forward cohort null positions",
    verdicts=lambda root: ((line.get("null_id"), line.get("status")) for line in null_report(root)),
)


def run_null_walk(root: Path | None = None, *, now: str, frame_for: Any, persist: bool = True) -> dict[str, Any]:
    """Advance every twin to the newest closed bar, through the cohort's own walker."""
    return walk_track(NULL_TRACK, root, now=now, frame_for=frame_for, persist=persist)


def status_line(summary: Mapping[str, Any]) -> str:
    line = ("nulls members=%s walked=%s opened=%s settled=%s" % (
        summary.get("members"), summary.get("walked"), summary.get("opened"), summary.get("settled")))
    failed = summary.get("failed") or []
    return (line + (f" failed={len(failed)}" if failed else "") + unparseable_suffix(summary)
            + first_verdict_suffix(summary))


# --- the report: the judge's rate over the twins, beside the members' (2026-09-24, PR 2) -----------

def null_report(root: Path | None = None) -> list[dict[str, Any]]:
    """Every twin's forward numbers over its own rows: the judge's verdict and the maturity, exactly
    as `forward_cohort.cohort_report` gives a member's. The twin is judged as a record whose
    ``candidate_id`` is its null id, whose ``created_at_utc`` is its parent's selection time, and
    whose spec is its null spec (for the timeframe's floor and slice width). Reads only."""
    rows = read_null_outcomes(root)
    lines: list[dict[str, Any]] = []
    for record in active_null_records(root):
        for twin in record.get("members") or []:
            judged = {"candidate_id": twin.get("null_id"), "created_at_utc": twin.get("selected_at_utc"),
                      "strategy_spec": twin.get("null_spec") or {}}
            line = {
                "null_id": twin.get("null_id"), "parent_candidate_id": twin.get("parent_candidate_id"),
                "cohort_id": record.get("cohort_id"), "timeframe": twin.get("timeframe"),
                "direction": (twin.get("null_spec") or {}).get("direction"),
                **judge_forward(judged, rows),
                "trade_floor": min_forward_trades(twin.get("timeframe")),
            }
            line["maturity"] = maturity_of(line)
            lines.append(line)
    return lines


def arm_counts(
    lines: Sequence[Mapping[str, Any]],
    history: Mapping[str, Mapping[str, str]] | None = None,
    *,
    id_key: str = "candidate_id",
) -> dict[str, dict[str, int]]:
    """Per timeframe: lineages, with priced rows, at the trade floor, confirmed, contradicted, and
    underpowered (at the floor, leaning with the edge, unresolved — MATURE in the maturity, counted
    apart so the comparison shows where the records the 2026-09-24 split moved went).

    With ``history`` (the walk's first-verdict map, keyed by ``line[id_key]``), also how many were
    EVER confirmed or contradicted at a walk: today's counts are one look, and a lineage the judge
    confirmed last week may read otherwise today (`SEQUENTIAL_FORWARD_TEST_V0.1.md`)."""
    out: dict[str, dict[str, int]] = {}
    ever = {"ever_confirmed": FIRST_VERDICT_FIELDS["FORWARD_CONFIRMED"],
            "ever_contradicted": FIRST_VERDICT_FIELDS["FORWARD_CONTRADICTED"]}
    for line in lines:
        cell = out.setdefault(str(line.get("timeframe") or "?"), {
            "members": 0, "with_rows": 0, "at_floor": 0, "confirmed": 0, "contradicted": 0,
            "underpowered": 0, **({key: 0 for key in ever} if history is not None else {})})
        if history is not None:
            stamps = history.get(str(line.get(id_key))) or {}
            for key, field in ever.items():
                cell[key] += field in stamps
        priced = int(line.get("priceable_count") or 0)
        cell["members"] += 1
        cell["with_rows"] += priced > 0
        cell["at_floor"] += priced >= min_forward_trades(line.get("timeframe"))
        maturity = line.get("maturity") or maturity_of(line)
        cell["confirmed"] += maturity == MATURITY_CONFIRMED
        cell["contradicted"] += maturity == MATURITY_CONTRADICTED
        cell["underpowered"] += line.get("status") == FORWARD_UNDERPOWERED
    return out


def arm_comparison(root: Path | None = None) -> dict[str, Any] | None:
    """The members' and the twins' judge outcomes side by side, per timeframe; None before any null
    arm is frozen. Per timeframe, never pooled: `null_control` measured the null's own baseline at
    -0.127R at 1h against -0.035R at 1d, so one pooled rate would mix three different questions."""
    if not read_null_records(root):
        return None
    members = [m for cohort in cohort_report(root) for m in cohort["members"]]
    return {"real": arm_counts(members, load_positions(root)["verdicts"]),
            "null": arm_counts(null_report(root), load_null_positions(root)["verdicts"], id_key="null_id")}

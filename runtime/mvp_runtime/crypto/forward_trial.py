"""Hypothesis trials, walked forward beside a coin-flip twin each (2026-09-25;
`docs/proposals/HYPOTHESIS_TRIAL_V0.1.md` option C, PR3).

**What it answers.** A trial row is one proposer hypothesis the factory scored at depth
(`factory._screen_trials`). Its backtest says how the rule did on bars it was selected on; the
question the option exists for is whether it does better than chance on bars it has never seen.
So each trial is walked from its mint by the cohort's own walker (`forward_cohort.walk_track`), and
so is a twin that keeps the trial's exits, risk, direction and admission evidence but enters on a
seeded coin flip — the null arm's construction (`forward_cohort_null`), one per trial.

**A track of its own** (Thomas 2026-09-24, Q3 (a)). The frozen cohort and its null arm are not
touched: a trial is never a cohort member (`forward_cohort.eligible_members` refuses its
derivation), and its rows go to their own stores — positions books and outcomes for the trials
(provenance ``mvp_forward_trial``) and for their twins (``mvp_forward_trial_null``). Nothing that
reads the cohort, the pool or the forward book reads them. It opens no door: graduation is a
Thomas PR into ``factory.TEMPLATES`` on the existing holdout gate (Q5), and this record is what he
reads beside it.

**The membership is the candidate store.** A trial row is appended once and never edited (the
store is append-only and self-hashed), so the trial set is read from it every walk rather than
frozen separately; the clock starts at the row's ``created_at_utc``, the mint. A twin is a pure
function of that sealed row — its null spec, its seed (``trial|<candidate_id>``) and its rate —
so it needs no record of its own either.

**The twin's rate is per leg.** A trial is scored across every cohort leg, so its backtest
``closed_count`` is the pooled total while ``bars_replayed`` is one leg's (the shallowest). The
twin walks each leg separately, so its per-bar rate is ``closed_count / (bars_replayed x
symbols_replayed)`` — the parent's pace on each leg. Dividing by one leg's bars alone would have
the twin trade once per leg for every trade the parent made across all of them.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

from runtime.read_only_kernel import integrity

from .. import jsonl
from ..errors import ToolError
from ..filelock import locked
from . import forward_book
from .candidate_identity import candidate_id
from .candidate_ranking import candidate_quality
from .factory import is_trial
from .factory import MAX_OPEN_TRIALS
from .forward_cohort import (
    FORWARD_COHORT_LOCKED, WalkTrack, first_verdict_suffix, load_book_at, maturity_of,
    synthesize_entry, walk_track,
)
from .forward_cohort_null import arm_counts, null_entry, null_id, null_rows, trade_rate
from .forward_confirmation import judge_forward, min_forward_trades
from .null_control import _null_spec
from .pool_state import read_candidates
from .state import state_dir
from .strategy import StrategySpec
from .strategy_artifact import admission_evidence

TRIAL_POSITIONS_FILENAME = "forward_trial_positions.json"
TRIAL_OUTCOMES_FILENAME = "forward_trial_outcomes.jsonl"
TRIAL_NULL_POSITIONS_FILENAME = "forward_trial_null_positions.json"
TRIAL_NULL_OUTCOMES_FILENAME = "forward_trial_null_outcomes.jsonl"
TRIAL_PROVENANCE = "mvp_forward_trial"
TRIAL_NULL_PROVENANCE = "mvp_forward_trial_null"
TRIAL_SEED_PREFIX = "trial|"

# Thomas's close of a trial (PR4). The candidate store is append-only and self-hashed, so a trial
# row cannot be marked closed; this is its own sealed store, read like the null arm's records.
# Not a ledger kind: the ledger rotates, and the factory's cap reads these forever.
TRIAL_CLOSES_FILENAME = "hypothesis_trial_closes.jsonl"
TRIAL_CLOSE_VERSION = "hypothesis_trial_close.v1"
CLOSE_GRADUATE = "graduate"
CLOSE_RETIRE = "retire"
CLOSE_DECISIONS = (CLOSE_GRADUATE, CLOSE_RETIRE)
# The holdout status a graduation needs (HYPOTHESIS_TRIAL_V0.1 Q5: no new threshold; installing a
# family into `factory.TEMPLATES` is a Thomas PR on the existing holdout gate).
GRADUATION_HOLDOUT_STATUS = "CONFIRMED"

HYPOTHESIS_TRIAL_CLOSES_UNREADABLE = "HYPOTHESIS_TRIAL_CLOSES_UNREADABLE"
HYPOTHESIS_TRIAL_CLOSES_TAMPERED = "HYPOTHESIS_TRIAL_CLOSES_TAMPERED"
HYPOTHESIS_TRIAL_UNKNOWN = "HYPOTHESIS_TRIAL_UNKNOWN"
HYPOTHESIS_TRIAL_ALREADY_CLOSED = "HYPOTHESIS_TRIAL_ALREADY_CLOSED"
HYPOTHESIS_TRIAL_NOT_GRADUABLE = "HYPOTHESIS_TRIAL_NOT_GRADUABLE"
HYPOTHESIS_TRIAL_CLOSE_INVALID = "HYPOTHESIS_TRIAL_CLOSE_INVALID"


def _positions_path(root: Path | None) -> Path:
    return state_dir(root) / TRIAL_POSITIONS_FILENAME


def _outcomes_path(root: Path | None) -> Path:
    return state_dir(root) / TRIAL_OUTCOMES_FILENAME


def _null_positions_path(root: Path | None) -> Path:
    return state_dir(root) / TRIAL_NULL_POSITIONS_FILENAME


def _null_outcomes_path(root: Path | None) -> Path:
    return state_dir(root) / TRIAL_NULL_OUTCOMES_FILENAME


def _closes_path(root: Path | None) -> Path:
    return state_dir(root) / TRIAL_CLOSES_FILENAME


def trial_rows(root: Path | None = None) -> list[dict[str, Any]]:
    """Every trial row in the store, once each by candidate id, in store order."""
    seen: dict[str, dict[str, Any]] = {}
    for record in read_candidates(root):
        if is_trial(record):
            seen.setdefault(candidate_id(record), record)
    return list(seen.values())


# One authority for the per-leg pace: the null arm's own, since its v2 (2026-09-25).
per_leg_trade_rate = trade_rate


def trial_twin(record: Mapping[str, Any]) -> dict[str, Any] | None:
    """The trial's coin-flip twin, in the null arm's twin shape; None when no rate can be read or
    the spec cannot be twinned. Pure: a function of the sealed row alone."""
    rate = per_leg_trade_rate(record)
    if rate is None:
        return None
    try:
        spec = _null_spec(StrategySpec.from_dict(record["strategy_spec"]), rate)
    except Exception:  # noqa: BLE001 — a spec that cannot be twinned walks without a twin
        return None
    cid = candidate_id(record)
    return {
        "null_id": null_id(cid),
        "parent_candidate_id": cid,
        "selected_at_utc": record.get("created_at_utc"),
        "symbol_scope": list(spec.symbol_scope),
        "timeframe": spec.timeframe,
        "strategy_family": spec.strategy_family,
        "signal_rate": round(rate, 6),
        "seed": f"{TRIAL_SEED_PREFIX}{cid}",
        "null_spec": spec.to_dict(),
        "admission_evidence": admission_evidence(record),
    }


# --- closing a trial (Thomas) --------------------------------------------------------------------

def read_trial_closes(root: Path | None = None) -> list[dict[str, Any]]:
    """Every close, verified: a ``hypothesis_trial_close.v1`` record whose self-hash holds."""
    records: list[dict[str, Any]] = []
    for lineno, record in jsonl.iter_numbered(
        _closes_path(root), read_code=HYPOTHESIS_TRIAL_CLOSES_UNREADABLE,
        label="hypothesis trial closes", exc_type=ToolError,
    ):
        if not isinstance(record, dict) or record.get("hypothesis_trial_close_version") != TRIAL_CLOSE_VERSION:
            raise ToolError(HYPOTHESIS_TRIAL_CLOSES_TAMPERED,
                            f"hypothesis trial closes line {lineno} is not a {TRIAL_CLOSE_VERSION} record")
        stored = record.get("record_sha256")
        body = {k: v for k, v in record.items() if k != "record_sha256"}
        if not isinstance(stored, str) or integrity.sha256_record(body) != stored:
            raise ToolError(HYPOTHESIS_TRIAL_CLOSES_TAMPERED,
                            f"hypothesis trial closes line {lineno} fails its self-hash")
        records.append(record)
    return records


def closed_trial_ids(root: Path | None = None) -> frozenset[str]:
    return frozenset(str(r.get("candidate_id")) for r in read_trial_closes(root))


def close_trial(
    root: Path | None, candidate: str, *, decision: str, reason: str, now: str, apply: bool = False,
) -> dict[str, Any]:
    """Thomas's close of one trial: graduated (he will install it into ``factory.TEMPLATES`` by PR)
    or retired. Frees its slot under :data:`factory.MAX_OPEN_TRIALS`, stops its walk and its twin's,
    and seals the forward record as it stood — the evidence the decision was taken on. Appended
    only with ``apply``.

    Refuses: an id that is not a trial, one already closed, an empty reason, and a graduation of a
    trial whose own holdout is not CONFIRMED (Q5 — the install gate is the existing one; a close
    cannot promise an install that gate would refuse). The proposal it came from stays screened:
    closing frees a slot, it never re-queues the proposal."""
    if decision not in CLOSE_DECISIONS:
        raise ToolError(HYPOTHESIS_TRIAL_CLOSE_INVALID, f"decision must be one of {CLOSE_DECISIONS}")
    if not reason.strip():
        raise ToolError(HYPOTHESIS_TRIAL_CLOSE_INVALID, "a close needs a reason")
    lines = {line["candidate_id"]: line for line in trial_report(root)}
    line = lines.get(candidate)
    if line is None:
        raise ToolError(HYPOTHESIS_TRIAL_UNKNOWN, f"{candidate} is not a hypothesis trial in the store")
    if line.get("close") is not None:
        raise ToolError(HYPOTHESIS_TRIAL_ALREADY_CLOSED,
                        f"{candidate} was closed ({line['close']['decision']}) at {line['close']['closed_at_utc']}")
    if decision == CLOSE_GRADUATE and line.get("holdout_status") != GRADUATION_HOLDOUT_STATUS:
        raise ToolError(HYPOTHESIS_TRIAL_NOT_GRADUABLE,
                        f"{candidate}'s holdout is {line.get('holdout_status')}; installing a family "
                        f"needs {GRADUATION_HOLDOUT_STATUS} (Q5). Retire it, or leave it open.")
    body = {
        "hypothesis_trial_close_version": TRIAL_CLOSE_VERSION,
        "candidate_id": candidate,
        "decision": decision,
        "reason": reason.strip(),
        "closed_at_utc": now,
        "forward_at_close": {k: line.get(k) for k in (
            "status", "maturity", "priceable_count", "mean_net_r", "holdout_status")},
        "twin_at_close": None if not line.get("twin") else {
            k: line["twin"].get(k) for k in ("status", "maturity", "priceable_count", "mean_net_r")},
    }
    record = {**body, "record_sha256": integrity.sha256_record(body)}
    if apply:
        path = _closes_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        with locked(path.with_suffix(".lock"), code=FORWARD_COHORT_LOCKED, label="hypothesis trial closes"):
            if candidate in closed_trial_ids(root):
                raise ToolError(HYPOTHESIS_TRIAL_ALREADY_CLOSED, f"{candidate} was closed concurrently")
            with open(path, "a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
    return record


# --- the two tracks ------------------------------------------------------------------------------

def trial_walk_plan(root: Path | None = None) -> dict[tuple[str, str], list[tuple[dict[str, Any], dict[str, Any]]]]:
    """Every trial-context, grouped by ``(symbol, timeframe)``, clocked from the mint."""
    plan: dict[tuple[str, str], list[tuple[dict[str, Any], dict[str, Any]]]] = {}
    closed = closed_trial_ids(root)
    for record in trial_rows(root):
        if candidate_id(record) in closed:
            continue  # closed by Thomas: its record stops where he closed it
        spec = record.get("strategy_spec") or {}
        walk = {"candidate_id": candidate_id(record), "selected_at_utc": record.get("created_at_utc")}
        for symbol in spec.get("symbol_scope") or []:
            plan.setdefault((str(symbol), str(spec.get("timeframe"))), []).append((walk, record))
    return plan


def trial_null_walk_plan(root: Path | None = None) -> dict[tuple[str, str], list[tuple[dict[str, Any], dict[str, Any]]]]:
    """Every twin-context, the null arm's plan shape over the trials' twins."""
    plan: dict[tuple[str, str], list[tuple[dict[str, Any], dict[str, Any]]]] = {}
    closed = closed_trial_ids(root)
    for record in trial_rows(root):
        if candidate_id(record) in closed:
            continue
        twin = trial_twin(record)
        if twin is None:
            continue
        walk = {"candidate_id": twin["null_id"], "selected_at_utc": twin["selected_at_utc"],
                "seed": twin["seed"]}
        for symbol in twin["symbol_scope"]:
            plan.setdefault((str(symbol), str(twin["timeframe"])), []).append((walk, twin))
    return plan


def load_trial_positions(root: Path | None = None) -> dict[str, Any]:
    # Every trial ever minted, closed ones included: a closed trial's book entries stay (its marks
    # are where its record stopped), and `load_book_at` refuses entries for ids it is not given.
    return load_book_at(_positions_path(root),
                        members=lambda: frozenset(candidate_id(r) for r in trial_rows(root)),
                        label="forward trial positions")


def load_trial_null_positions(root: Path | None = None) -> dict[str, Any]:
    return load_book_at(_null_positions_path(root),
                        members=lambda: frozenset(null_id(candidate_id(r)) for r in trial_rows(root)),
                        label="forward trial null positions")


def read_trial_outcomes(root: Path | None = None) -> list[dict[str, Any]]:
    return forward_book.read_sealed_rows(
        _outcomes_path(root), provenance=TRIAL_PROVENANCE, label="forward trial outcomes")


def read_trial_null_outcomes(root: Path | None = None) -> list[dict[str, Any]]:
    return forward_book.read_sealed_rows(
        _null_outcomes_path(root), provenance=TRIAL_NULL_PROVENANCE,
        label="forward trial null outcomes")


TRIAL_TRACK = WalkTrack(
    plan=lambda root: trial_walk_plan(root),
    spec_of=lambda walk, record: StrategySpec.from_dict(record["strategy_spec"]),
    entry_of=lambda walk, record: {**synthesize_entry(record), "status": "TRIAL"},
    rows_of=lambda walk, rows, candles: rows,
    book_path=lambda root: _positions_path(root),
    load_book=lambda root: load_trial_positions(root),
    outcomes_path=lambda root: _outcomes_path(root),
    read_outcomes=lambda root: read_trial_outcomes(root),
    provenance=TRIAL_PROVENANCE,
    label="forward trial positions",
    verdicts=lambda root: ((line["candidate_id"], line["status"]) for line in trial_report(root)),
)

TRIAL_NULL_TRACK = WalkTrack(
    plan=lambda root: trial_null_walk_plan(root),
    spec_of=lambda walk, twin: StrategySpec.from_dict(twin["null_spec"]),
    entry_of=lambda walk, twin: {**null_entry(twin), "status": "TRIAL_NULL"},
    rows_of=lambda walk, rows, candles: null_rows(walk, rows, candles),
    book_path=lambda root: _null_positions_path(root),
    load_book=lambda root: load_trial_null_positions(root),
    outcomes_path=lambda root: _null_outcomes_path(root),
    read_outcomes=lambda root: read_trial_null_outcomes(root),
    provenance=TRIAL_NULL_PROVENANCE,
    label="forward trial null positions",
    verdicts=lambda root: ((line["twin"]["null_id"], line["twin"]["status"])
                           for line in trial_report(root) if line.get("twin")),
)


def walk_trials(root: Path | None = None, *, now: str, frame_for: Any,
                persist: bool = True) -> dict[str, Any]:
    """Advance every trial to the newest closed bar, through the cohort's walker."""
    return walk_track(TRIAL_TRACK, root, now=now, frame_for=frame_for, persist=persist)


def walk_trial_nulls(root: Path | None = None, *, now: str, frame_for: Any,
                     persist: bool = True) -> dict[str, Any]:
    """Advance every twin, on the frames the trials fetched (``frame_for`` is the fire's memoized
    one). Separate from :func:`walk_trials` so the scheduler can name which arm failed."""
    return walk_track(TRIAL_NULL_TRACK, root, now=now, frame_for=frame_for, persist=persist)


def run_trial_walk(root: Path | None = None, *, now: str, frame_for: Any,
                   persist: bool = True) -> dict[str, Any]:
    """Both walks, trials first. Returns ``{"trials": summary, "nulls": summary}``."""
    return {"trials": walk_trials(root, now=now, frame_for=frame_for, persist=persist),
            "nulls": walk_trial_nulls(root, now=now, frame_for=frame_for, persist=persist)}


def status_line(summary: Mapping[str, Any]) -> str:
    """Both walks, as the scheduler's status column carries them."""
    trials, nulls = summary["trials"], summary["nulls"]
    line = "trials members=%s walked=%s opened=%s settled=%s" % (
        trials.get("members"), trials.get("walked"), trials.get("opened"), trials.get("settled"))
    if trials.get("failed"):
        line += f" failed={len(trials['failed'])}"
    line += first_verdict_suffix(trials)
    line += " nulls opened=%s settled=%s" % (nulls.get("opened"), nulls.get("settled"))
    if nulls.get("failed"):
        line += f" failed={len(nulls['failed'])}"
    return line


# --- the report ----------------------------------------------------------------------------------

def _judged(walk_id: str, started: Any, spec: Mapping[str, Any]) -> dict[str, Any]:
    return {"candidate_id": walk_id, "created_at_utc": started, "strategy_spec": dict(spec)}


def trial_report(root: Path | None = None) -> list[dict[str, Any]]:
    """Each trial's forward numbers and its twin's, side by side. Reads only.

    Judged exactly as a cohort member is (`forward_confirmation.judge_forward`, cutoff at the mint)
    and its twin exactly as a null twin is. The pair is the comparison the option exists for; the
    judge's status on the trial alone opens nothing."""
    rows = read_trial_outcomes(root)
    null_rows_ = read_trial_null_outcomes(root)
    closes = {str(c["candidate_id"]): c for c in read_trial_closes(root)}
    lines: list[dict[str, Any]] = []
    for record in trial_rows(root):
        spec = record.get("strategy_spec") or {}
        cid = candidate_id(record)
        line = {
            "candidate_id": cid,
            "strategy_family": spec.get("strategy_family"),
            "timeframe": spec.get("timeframe"),
            "direction": spec.get("direction"),
            "minted_at_utc": record.get("created_at_utc"),
            "trial_source": dict(record.get("trial_source") or {}),
            **judge_forward(_judged(cid, record.get("created_at_utc"), spec), rows),
            "trade_floor": min_forward_trades(spec.get("timeframe")),
            # Recomputed from the holdout block by the one reader allowed to (`candidate_quality`),
            # never lifted off the stored robustness label.
            "holdout_status": candidate_quality(record)["holdout_status"],
        }
        line["maturity"] = maturity_of(line)
        close = closes.get(cid)
        line["close"] = None if close is None else {
            k: close.get(k) for k in ("decision", "reason", "closed_at_utc")}
        twin = trial_twin(record)
        if twin is not None:
            twin_line = {
                "null_id": twin["null_id"], "signal_rate": twin["signal_rate"],
                "timeframe": twin["timeframe"],
                **judge_forward(_judged(twin["null_id"], twin["selected_at_utc"], twin["null_spec"]),
                                null_rows_),
            }
            twin_line["maturity"] = maturity_of(twin_line)
            line["twin"] = twin_line
        else:
            line["twin"] = None
        lines.append(line)
    return lines


def board_summary(root: Path | None = None) -> dict[str, Any] | None:
    """What the daily board shows of the trials; None before any trial is minted. Reads only.

    Open against the cap and closed; then per timeframe the null line's cells — confirmed ·
    contradicted / lineages, today's look and every look since the walk began stamping — for the
    trials and for their twins (`forward_cohort_null.arm_counts`, the same counter)."""
    lines = trial_report(root)
    if not lines:
        return None
    twins = [line["twin"] for line in lines if line.get("twin")]
    return {
        "trials": len(lines),
        "open": sum(1 for line in lines if line.get("close") is None),
        "closed": sum(1 for line in lines if line.get("close") is not None),
        "cap": MAX_OPEN_TRIALS,
        "with_rows": sum(1 for line in lines if (line.get("priceable_count") or 0) > 0),
        "real": arm_counts(lines, load_trial_positions(root)["verdicts"]),
        "null": arm_counts(twins, load_trial_null_positions(root)["verdicts"], id_key="null_id"),
    }


__all__ = [
    "board_summary",
    "close_trial",
    "closed_trial_ids",
    "read_trial_closes",
    "TRIAL_NULL_TRACK",
    "TRIAL_TRACK",
    "per_leg_trade_rate",
    "run_trial_walk",
    "walk_trial_nulls",
    "walk_trials",
    "status_line",
    "trial_report",
    "trial_twin",
]


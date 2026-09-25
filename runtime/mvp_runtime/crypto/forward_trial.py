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

from pathlib import Path
from typing import Any, Mapping

from . import forward_book
from .candidate_identity import candidate_id
from .factory import is_trial
from .forward_cohort import (
    WalkTrack, first_verdict_suffix, load_book_at, maturity_of, synthesize_entry, unparseable_suffix,
    walk_track,
)
from .forward_cohort_null import null_entry, null_id, null_rows
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


def _positions_path(root: Path | None) -> Path:
    return state_dir(root) / TRIAL_POSITIONS_FILENAME


def _outcomes_path(root: Path | None) -> Path:
    return state_dir(root) / TRIAL_OUTCOMES_FILENAME


def _null_positions_path(root: Path | None) -> Path:
    return state_dir(root) / TRIAL_NULL_POSITIONS_FILENAME


def _null_outcomes_path(root: Path | None) -> Path:
    return state_dir(root) / TRIAL_NULL_OUTCOMES_FILENAME


def trial_rows(root: Path | None = None) -> list[dict[str, Any]]:
    """Every trial row in the store, once each by candidate id, in store order."""
    seen: dict[str, dict[str, Any]] = {}
    for record in read_candidates(root):
        if is_trial(record):
            seen.setdefault(candidate_id(record), record)
    return list(seen.values())


def per_leg_trade_rate(record: Mapping[str, Any]) -> float | None:
    """The trial's backtest trades per bar ON EACH LEG, or None when the row cannot say. See the
    module docstring for why the pooled count is divided by the legs."""
    evidence = record.get("backtest_evidence") or {}
    closed, bars = evidence.get("closed_count"), evidence.get("bars_replayed")
    legs = evidence.get("symbols_replayed") or 1
    for value in (closed, bars, legs):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            return None
    return min(1.0, float(closed) / (float(bars) * float(legs)))


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


# --- the two tracks ------------------------------------------------------------------------------

def trial_walk_plan(root: Path | None = None) -> dict[tuple[str, str], list[tuple[dict[str, Any], dict[str, Any]]]]:
    """Every trial-context, grouped by ``(symbol, timeframe)``, clocked from the mint."""
    plan: dict[tuple[str, str], list[tuple[dict[str, Any], dict[str, Any]]]] = {}
    for record in trial_rows(root):
        spec = record.get("strategy_spec") or {}
        walk = {"candidate_id": candidate_id(record), "selected_at_utc": record.get("created_at_utc")}
        for symbol in spec.get("symbol_scope") or []:
            plan.setdefault((str(symbol), str(spec.get("timeframe"))), []).append((walk, record))
    return plan


def trial_null_walk_plan(root: Path | None = None) -> dict[tuple[str, str], list[tuple[dict[str, Any], dict[str, Any]]]]:
    """Every twin-context, the null arm's plan shape over the trials' twins."""
    plan: dict[tuple[str, str], list[tuple[dict[str, Any], dict[str, Any]]]] = {}
    for record in trial_rows(root):
        twin = trial_twin(record)
        if twin is None:
            continue
        walk = {"candidate_id": twin["null_id"], "selected_at_utc": twin["selected_at_utc"],
                "seed": twin["seed"]}
        for symbol in twin["symbol_scope"]:
            plan.setdefault((str(symbol), str(twin["timeframe"])), []).append((walk, twin))
    return plan


def load_trial_positions(root: Path | None = None) -> dict[str, Any]:
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
    line += unparseable_suffix(trials) + first_verdict_suffix(trials)
    line += " nulls opened=%s settled=%s" % (nulls.get("opened"), nulls.get("settled"))
    if nulls.get("failed"):
        line += f" failed={len(nulls['failed'])}"
    return line + unparseable_suffix(nulls)


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
        }
        line["maturity"] = maturity_of(line)
        twin = trial_twin(record)
        if twin is not None:
            twin_line = {
                "null_id": twin["null_id"], "signal_rate": twin["signal_rate"],
                **judge_forward(_judged(twin["null_id"], twin["selected_at_utc"], twin["null_spec"]),
                                null_rows_),
            }
            twin_line["maturity"] = maturity_of({**twin_line, "timeframe": twin["timeframe"]})
            line["twin"] = twin_line
        else:
            line["twin"] = None
        lines.append(line)
    return lines


__all__ = [
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


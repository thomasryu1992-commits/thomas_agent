"""Which rules a verdict came from — the judgement-rule fingerprint (Thomas 2026-09-24).

`docs/proposals/RESEARCH_EPOCH_V0.1.md`, decided as recommended (option A, scope per Q2). A rule
change here re-grades the whole store at once — `candidate_ranking.candidate_quality` recomputes
the holdout verdict rather than reading a stale label, deliberately — so a verdict read today and
the same verdict read before a threshold moved can come from different rules with nothing on
either saying so. The plan's alternative, freezing the rules for an epoch, would also hold back
defect fixes (#945); this records instead.

**One hash over the constants that turn evidence into a verdict**: holdout, forward, scoring,
cost, selection correction and the promotion door's thresholds. Not the family list or the
feature vocabulary — those are the search space, and already move only through reviewed code.
Every value is read from its owner at call time, and every name is one ``tunables`` indexes
(``NOT_INDEXED`` names the four that are not scalars and so sit outside that index) — so this is
a view of existing authorities, never a second copy of a number.

Stamped where a verdict is read: the daily board, the strategy funnel and a forward cohort's
frozen record (which carries the values too, so a later reader can judge it under the rules it
was frozen with). Checked at operator start the way `policy_fingerprint` checks the policy file:
the rules are baked into the image, so they move only on a deploy, and a change is announced
once.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from runtime.read_only_kernel import integrity

from .. import timeutil
from ..policy_fingerprint import CHANGED, FIRST_SEEN, UNCHANGED, fingerprints_dir
from . import cost, factory, forward_confirmation, market_data, pool_admission, robustness

JUDGEMENT_RULES_VERSION = "judgement_rules.v1"
# The record's file name under the policy fingerprints directory, beside the per-service policy ones.
FINGERPRINT_NAME = "judgement_rules"

# In the scope but not in `tunables.TUNABLES`, which indexes scalars: the scorer's component
# weights and the promotion door's three admitted sets.
NOT_INDEXED = frozenset({
    "WEIGHTS", "PROMOTABLE_COST_BASIS_RANKS", "PROMOTABLE_EVIDENCE_DEPTH_RANKS",
    "PROMOTABLE_DERIVATION_TYPES",
})


def judgement_rules() -> dict[str, Any]:
    """The scoped constants by name, JSON-shaped (sets sorted), read from their owners now."""
    return {
        # holdout and scoring
        "MIN_HOLDOUT_TRADES": robustness.MIN_HOLDOUT_TRADES,
        "MIN_HOLDOUT_PERIODS": robustness.MIN_HOLDOUT_PERIODS,
        "CONFIDENCE_Z": robustness.CONFIDENCE_Z,
        "ROBUST_SCORE_THRESHOLD": robustness.ROBUST_SCORE_THRESHOLD,
        "FRAGILE_SCORE_THRESHOLD": robustness.FRAGILE_SCORE_THRESHOLD,
        "HEALTHY_TRADES_PER_PARAMETER": robustness.HEALTHY_TRADES_PER_PARAMETER,
        "CRITICAL_TRADES_PER_PARAMETER": robustness.CRITICAL_TRADES_PER_PARAMETER,
        "MAX_FREE_PARAMETERS": robustness.MAX_FREE_PARAMETERS,
        "WEIGHTS": dict(sorted(robustness.WEIGHTS.items())),
        "HOLDOUT_FRACTION": factory.HOLDOUT_FRACTION,
        "MIN_BARS_FOR_HOLDOUT": factory.MIN_BARS_FOR_HOLDOUT,
        "WALK_FORWARD_PERIODS": factory.WALK_FORWARD_PERIODS,
        "WALK_FORWARD_MIN_PERIODS": factory.WALK_FORWARD_MIN_PERIODS,
        "MIN_TRADES_PER_WINDOW": factory.MIN_TRADES_PER_WINDOW,
        "FACTORY_DEPTH_DAYS": market_data.FACTORY_DEPTH_DAYS,
        "MIN_FACTORY_BARS": market_data.MIN_FACTORY_BARS,
        # selection correction
        "SELECTION_ALPHA": robustness.SELECTION_ALPHA,
        # forward
        "FORWARD_SLICE_WIDTH_DAYS": forward_confirmation.FORWARD_SLICE_WIDTH_DAYS,
        "MIN_FORWARD_TRADES_1D": forward_confirmation.MIN_FORWARD_TRADES_1D,
        # cost
        "DEFAULT_TAKER_FEE_BPS": cost.DEFAULT_TAKER_FEE_BPS,
        "DEFAULT_MAKER_FEE_BPS": cost.DEFAULT_MAKER_FEE_BPS,
        "DEFAULT_SLIPPAGE_BPS": cost.DEFAULT_SLIPPAGE_BPS,
        "DEFAULT_STOP_SLIPPAGE_BPS": cost.DEFAULT_STOP_SLIPPAGE_BPS,
        "DEFAULT_FUNDING_BPS_PER_INTERVAL": cost.DEFAULT_FUNDING_BPS_PER_INTERVAL,
        "MAX_ENTRY_COST_R": cost.MAX_ENTRY_COST_R,
        # the promotion door
        "OBSERVATION_MIN_BACKTEST_CLOSED": pool_admission.OBSERVATION_MIN_BACKTEST_CLOSED,
        "OBSERVATION_FAMILY_CAP": pool_admission.OBSERVATION_FAMILY_CAP,
        "PROMOTABLE_COST_BASIS_RANKS": sorted(pool_admission.PROMOTABLE_COST_BASIS_RANKS),
        "PROMOTABLE_EVIDENCE_DEPTH_RANKS": sorted(pool_admission.PROMOTABLE_EVIDENCE_DEPTH_RANKS),
        "PROMOTABLE_DERIVATION_TYPES": sorted(pool_admission.PROMOTABLE_DERIVATION_TYPES),
    }


def judgement_fingerprint() -> dict[str, Any]:
    """``{"version", "sha256", "short"}`` over :func:`judgement_rules`. Pure."""
    digest = integrity.sha256_record({"version": JUDGEMENT_RULES_VERSION, "rules": judgement_rules()})
    return {"version": JUDGEMENT_RULES_VERSION, "sha256": digest, "short": digest.split(":", 1)[-1][:12]}


def fingerprint_path(root: Path | None = None) -> Path:
    return fingerprints_dir(root) / f"{FINGERPRINT_NAME}.json"


def check_and_record(*, now: str | None = None, root: Path | None = None) -> dict[str, Any]:
    """Compare the rules in this image with what this machine last recorded, and record them.

    `policy_fingerprint.check_and_record`'s contract: never raises, records even when CHANGED (a
    change is noticed once, not re-announced), and an unwritable directory degrades to
    ``recorded: False`` rather than hiding the comparison."""
    stamp = now or timeutil.utc_now_iso()
    current = judgement_fingerprint()
    path = fingerprint_path(root)
    previous = None
    try:
        previous = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(previous, dict):
            previous = None
    except (OSError, ValueError):
        previous = None
    if previous is None:
        status = FIRST_SEEN
    elif previous.get("sha256") == current["sha256"]:
        status = UNCHANGED
    else:
        status = CHANGED
    result = {"status": status, "checked_at": stamp, **current,
              "previous_sha256": (previous or {}).get("sha256"),
              "previous_seen_at": (previous or {}).get("seen_at")}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({**current, "seen_at": stamp, "pid": os.getpid()}, ensure_ascii=False),
                       encoding="utf-8")
        tmp.replace(path)
        result["recorded"] = True
    except OSError as exc:
        result["recorded"] = False
        result["record_error"] = exc.__class__.__name__
    return result


def _short(sha: Any) -> str:
    return str(sha).split(":", 1)[-1][:12] if sha else "?"


def banner(result: dict[str, Any]) -> str:
    """The one stderr line printed at operator start, UNCHANGED included."""
    if result.get("status") == CHANGED:
        return (f"JUDGEMENT RULES CHANGED: {_short(result.get('previous_sha256'))} -> {result.get('short')}. "
                "Verdicts read from now on come from different thresholds than before this deploy.\n")
    if result.get("status") == FIRST_SEEN:
        return f"JUDGEMENT RULES: {result.get('short')} — first recording on this machine\n"
    return f"JUDGEMENT RULES: {result.get('short')} unchanged\n"


def change_notice(result: dict[str, Any]) -> str:
    """The control-channel message for a CHANGED result. A notice, not an approval."""
    return (
        "판정 규칙(holdout·forward·점수·비용·선택 보정·승격 문 상수)이 바뀐 채로 런타임이 올라왔습니다.\n"
        f"  이전 : {_short(result.get('previous_sha256'))}\n"
        f"  지금 : {result.get('short')}\n"
        "이 배포 전후의 판정은 서로 다른 규칙에서 나옵니다. 저장소는 새 규칙으로 다시 채점됩니다.\n"
        "완화 방향의 변경이었다면 에포크 경계 결정(RESEARCH_EPOCH_V0.1 Q3)이 아직 열려 있습니다."
    )

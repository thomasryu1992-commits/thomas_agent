"""C11 dashboard — the crypto pipeline's read-only status board.

    python -m runtime.mvp_runtime.crypto.dashboard            # human-readable
    python -m runtime.mvp_runtime.crypto.dashboard --json     # machine-readable
    python -m runtime.mvp_runtime.crypto.dashboard --account  # + the live exchange account

Reads only what the runtime already persists (cycle records in the ledger, the paper
outcome store, the active pool, the counterfactual registry, the safety-flag grants)
and renders uptime, performance, digest trends, lifecycle state, and gate-calibration
summaries. Pure reads at ALLOW tier: no gate, no writes, no network — the source
``scripts/dashboard.py`` posture. Unreadable inputs degrade to an explicit warning
line, never a crash and never silence.

``--account`` is the one exception and is therefore opt-in: it adds a *live* read of the
real exchange account through the separately-gated ``binance_futures_account`` feed
(LP1). Without the flag this board still makes no network call at all.
"""

from __future__ import annotations

import argparse
import math
import json
import sys
import statistics
from collections import deque
from pathlib import Path
from typing import Any

from .. import timeutil
from ..cli_common import force_utf8_io
from ..errors import MvpRuntimeError
from ..paths import repo_root as _repo_root
from ..store import LEDGER_REL, RECORDS_FILE
from . import (
    account, counterfactual, digest, feedback, lifecycle, oi_store, paper, pool, positioning_store,
)
# "Can this sample tell the sign of its own edge" is one question with one answer in this
# runtime. `robustness` owns the multiplier because it is the module that judges whether an
# edge is real — the holdout gate draws the same interval over a candidate's unseen tail that
# this board draws over settled paper outcomes. Restating 1.96 here is how the two drift.
from .robustness import CONFIDENCE_Z


def _read_cycle_records(root: Path, limit: int) -> tuple[list[dict[str, Any]], str | None]:
    """The last ``limit`` crypto-cycle records, read without holding the ledger in memory.

    This used to ``read_text()`` the whole record ledger, ``splitlines()`` it (a second full
    copy), and ``json.loads`` **every** row into a list — then keep the last twelve. On the
    live host that ledger is 56MB / 3881 rows, and parsed JSON is several times its text
    size, so a board that needs twelve records was allocating on the order of a gigabyte.
    It OOM-killed on a host with 400MB free, which would have taken the scheduler down with
    it the next time the daily report fired.

    Now: stream the file, keep a bounded window, and skip the parse entirely for rows that
    cannot be a cycle. Memory is one line plus ``limit`` records regardless of ledger size.

    The substring pre-filter is a cheap *candidate* test, never the decision — a matching
    row is still parsed and checked on ``kind``, so a false positive costs one parse and
    changes nothing. A row that looks like a cycle and will not parse IS this reader's
    business and becomes a warning; a corrupt row elsewhere in the ledger is not, and no
    longer blinds the crypto board the way one bad line anywhere used to.
    """
    path = root / LEDGER_REL / RECORDS_FILE
    if not path.is_file():
        return [], None
    recent: deque[dict[str, Any]] = deque(maxlen=max(1, limit))
    unparsable = 0
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if "crypto_cycle" not in line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    unparsable += 1
                    continue
                if row.get("kind") == "crypto_cycle":
                    recent.append(row.get("record") or {})
    except OSError as exc:
        return [], f"cycle ledger unreadable: {type(exc).__name__}"
    if unparsable:
        return list(recent), f"cycle ledger has {unparsable} unparsable row(s)"
    return list(recent), None


# The grants section lived here until 2026-08-10, when Thomas retired per-machine grants
# and their renewal (the environment is the gate). Any activation files still on disk are
# inert leftovers, so surfacing their expiries would be the board claiming a bound that
# nothing enforces — the reason `env_only_authorization` refuses to encode a fake one.


def build_status(root: Path | None = None, *, now: str | None = None, cycles: int = 12) -> dict[str, Any]:
    """Assemble the full status document. Read-only; failures become warnings."""
    root = root if root is not None else _repo_root()
    now = now or timeutil.utc_now_iso()
    warnings: list[str] = []

    cycle_rows, cycle_warning = _read_cycle_records(root, cycles)
    if cycle_warning:
        warnings.append(cycle_warning)

    try:
        outcomes = paper.read_outcomes(root)
        # The store holds this runtime's own outcomes AND history imported from the frozen
        # crypto_AI_System. Blended, the imported set dominates by count and the headline
        # answers "how did the OLD system do", which is not the question this board is asked.
        own_outcomes, imported_outcomes = paper.split_by_provenance(outcomes)
        report = feedback.build_performance_report(own_outcomes, now=now) if own_outcomes else None
        imported_report = (
            feedback.build_performance_report(imported_outcomes, now=now)
            if imported_outcomes else None
        )
        outcome_digest = digest.build_performance_digest(own_outcomes, now=now) if own_outcomes else None
    except MvpRuntimeError as exc:
        outcomes, own_outcomes, imported_outcomes = [], [], []
        report, imported_report, outcome_digest = None, None, None
        warnings.append(f"outcome store unreadable ({exc.reason_code})")

    try:
        active = pool.load_active_pool(root)
        status_counts: dict[str, int] = {}
        # Why the non-trading members stopped trading, which the status count cannot say: an
        # operator retiring a duplicate and the metrics condemning a decaying edge both land on
        # SUSPENDED with `lifecycle_consecutive_failures: 0`, because a retirement carries that
        # count forward untouched. Entries transitioned before the reason was recorded report
        # `unrecorded` — a different answer from "no reason", and an honest one.
        demotions_by_reason: dict[str, int] = {}
        for entry in active.get("active_strategies") or []:
            status = str(entry.get("status") or "?")
            status_counts[status] = status_counts.get(status, 0) + 1
            # Only the ones that stopped trading. WARNING/PROBATION are degraded but still
            # occupy a routing slot, so counting them here would answer a question nobody asked.
            if status in pool.OCCUPYING_STATUSES:
                continue
            reasons = entry.get("lifecycle_reasons")
            if not isinstance(reasons, list):
                key = "unrecorded"
            else:
                key = ", ".join(str(r) for r in reasons) or "unrecorded"
            demotions_by_reason[key] = demotions_by_reason.get(key, 0) + 1
    except MvpRuntimeError as exc:
        active, status_counts, demotions_by_reason = {"active_strategies": []}, {}, {}
        warnings.append(f"active pool unreadable ({exc.reason_code})")

    # How large a book this pool can actually fill under the directional cap. The other half of
    # the lean below: that line says the book is one-way, this one says whether the POOL is why.
    # Since the routable set is capped at one strategy per context, a spec's direction is fixed
    # at promotion time — so a pool of twenty long strategies fills four slots and no more, and
    # neither cap says so alone. Derived here rather than refused at the promotion door: the
    # directional gate can only decline, so a lopsided pool trades less rather than unsafely,
    # and blocking a promotion over it would forbid building a pool in any order but alternating.
    try:
        pool_capacity = pool.routable_directional_capacity(active.get("active_strategies") or [])
    except MvpRuntimeError as exc:
        pool_capacity = None
        warnings.append(f"pool directional capacity unreadable ({exc.reason_code})")

    # The same question as the demotion reasons above — why is this not trading — arriving by a
    # path that is not a status. A regime-excluded entry stays PAPER_ACTIVE and keeps its routing
    # slot, so **nothing in the pool says it is inert**: its rules fire, and the router declines
    # them because the strategy's own backtest lost money in the regime the market is in. Counted
    # over the cycles this board already read, because the interesting case is not one exclusion
    # but a strategy excluded on most of them, which is a demotion candidate the status count
    # would report as healthy.
    regime_excluded_cycles: dict[str, int] = {}
    for row in cycle_rows:
        for strategy_id in row.get("regime_excluded") or []:
            key = str(strategy_id)
            regime_excluded_cycles[key] = regime_excluded_cycles.get(key, 0) + 1

    # Two things the board could always have said and did not, both about work waiting on a
    # human rather than on the runtime. They are warnings rather than rows because a queue
    # nobody is told about is a queue nobody works: promotion went untouched for three days
    # while the factory kept minting, and the daily report went out every one of those
    # mornings saying `warnings=0`.
    try:
        backlog = pool.promotable_backlog(root, active_pool=active)
    except MvpRuntimeError as exc:
        backlog = None
        warnings.append(f"candidate store unreadable ({exc.reason_code})")
    if backlog and backlog["count"] >= backlog["threshold"]:
        warnings.append(
            f"승격 대기 {backlog['count']}건 (임계 {backlog['threshold']}) — "
            f"scripts/promote_strategy_candidates.py --list"
        )

    # Imported inside the function, the way `live_route` reaches the same module: the
    # operator package pulls in the whole console/pipeline tree, and the board is imported
    # from inside it (`/crypto status`).
    from .. import operator as operator_mod

    # Hourly OI coverage — a field, deliberately not a warning. It will read "not eligible" for
    # roughly a year (84 seed days against a 500-day requirement), and a warning that is true
    # every morning for a year is how a board teaches its reader to skip the warning block.
    pool_symbols = {
        str(symbol)
        for entry in (active.get("active_strategies") or [])
        for symbol in ((entry.get("strategy_spec") or {}).get("symbol_scope") or [])
        if symbol
    }
    try:
        oi_1h = oi_store.coverage_summary(root, symbols=pool_symbols)
    except MvpRuntimeError as exc:
        oi_1h = None
        warnings.append(f"hourly OI store unreadable ({exc.reason_code})")

    # **How much of the search still has parents.** Half of every factory fire goes down the
    # fusion path, and that half is bounded by `rank_fusion_parents` — which four filters now
    # narrow, three of them added within one day. Measured 2026-08-05 over the 1,661-row store:
    # 1,661 scoreable, 1,432 after retired families, **176 after `holdout_permits_parenting`**,
    # 123 after rule-hash dedup. The dominant filter reads the holdout, so the pool shrinks
    # exactly when the promotion door finds nothing — the narrower the edge search comes up,
    # the narrower the search gets.
    #
    # It reached no surface at all. The 2026-08-05 08:09Z fire produced 6 fusion children
    # against 40 the day before, six of ten contexts producing none, and the only trace was
    # `fused=0` inside a schedule's `last_status` string. At zero the fusion path dies in
    # silence — which is the shape of failure a board exists to break.
    #
    # `rank_fusion_parents` is asked directly rather than re-deriving its filters here, so the
    # number on the board cannot drift from the number the factory draws from. `top_n` is the
    # store size because the question is how many lineages QUALIFY, not which six rank highest.
    try:
        from .factory import rank_fusion_parents

        _candidates = pool.read_candidates(root)
        fusion_parents = {
            "eligible": len(rank_fusion_parents(_candidates, top_n=len(_candidates) or 1)),
            "candidates_read": len(_candidates),
        }
    except MvpRuntimeError as exc:
        fusion_parents = None
        warnings.append(f"fusion parent pool unreadable ({exc.reason_code})")

    # The positioning store's ONLY consumer is this number: it feeds no feature, so progress
    # toward eligibility is the entire visible output of the accumulation. Reported without a
    # warning for the reason the OI line above carries — a shortfall that will be true every
    # morning for over a year is how a board teaches its reader to skip the warning block.
    try:
        positioning = positioning_store.coverage_summary(root, symbols=pool_symbols)
    except MvpRuntimeError as exc:
        positioning = None
        warnings.append(f"positioning store unreadable ({exc.reason_code})")

    inbound = operator_mod.last_inbound_at(root)
    silent_days = _days_since(inbound["at"], now) if inbound else None
    if inbound is None:
        # Only worth saying where a control channel is supposed to exist. A fresh checkout
        # and every mock-channel deployment have no registration and no cursor, and warning
        # there would park a permanent line on a board that is working as configured.
        try:
            operator_mod.load_operator_registration(root)
        except MvpRuntimeError:
            pass
        else:
            warnings.append("제어 채널 인바운드 수신 기록 없음 — /approve 도달 여부 미확인")
    elif silent_days is not None and silent_days >= operator_mod.INBOUND_SILENCE_ALERT_DAYS:
        stale = " (파일 시각 추정)" if inbound["source"] == "file_mtime" else ""
        warnings.append(
            f"제어 채널 인바운드 {silent_days}일 무응답 (마지막 {_stamp(inbound['at'])}{stale}) "
            f"— /approve 가 도달하지 못할 수 있음"
        )

    try:
        cf_records = counterfactual.read_counterfactual_outcomes(root)
        cf_summary = counterfactual.summarize_counterfactuals(cf_records)
        cf_verdicts = {
            reason: sample_verdict(values)
            for reason, values in counterfactual.r_values_by_reason(cf_records).items()
        }
    except MvpRuntimeError as exc:
        cf_records, cf_summary, cf_verdicts = [], {}, {}
        warnings.append(f"counterfactual store unreadable ({exc.reason_code})")

    # Every book, not just one: positions are keyed per (venue, symbol, timeframe),
    # so a dashboard reading a single slot would under-report open exposure.
    open_positions: list[dict[str, Any]] = []
    try:
        for context, position in paper.list_open_positions(root):
            open_positions.append({"context": context.key,
                                   "symbol": context.symbol,
                                   "timeframe": context.timeframe,
                                   "position_id": position.get("position_id"),
                                   "direction": position.get("direction"),
                                   "strategy_id": position.get("strategy_id"),
                                   "entry_price": position.get("entry_price"),
                                   "opened_at": position.get("opened_at_utc")})
    except MvpRuntimeError as exc:
        warnings.append(f"position state unreadable ({exc.reason_code})")
    open_position = open_positions[0] if open_positions else None
    # How far the book leans, and how far it is allowed to. Derived at read time from the
    # positions above — never stored — for the `pool.candidate_quality` reason: a lean written
    # once would outlive the cap that produced it, and `paper.MAX_DIRECTIONAL_SKEW` is derived
    # from a constant that has already moved once. Belongs on the BOARD and not only on the
    # per-fire status line, because the gate declines on STANDING book state: an operator who
    # missed the one fire that printed a refusal still needs to see that the book is one-way.
    longs = sum(1 for entry in open_positions if str(entry.get("direction") or "").upper() == "LONG")
    shorts = sum(1 for entry in open_positions if str(entry.get("direction") or "").upper() == "SHORT")
    directional_lean = {
        "long": longs,
        "short": shorts,
        # Positions whose direction is neither, counted rather than dropped: the gate treats
        # them as aligned with whatever is proposed, so they are not decoration.
        "unattributed": len(open_positions) - longs - shorts,
        "lean": longs - shorts,
        "limit": paper.MAX_DIRECTIONAL_SKEW,
        "at_limit": abs(longs - shorts) >= paper.MAX_DIRECTIONAL_SKEW,
    }

    last_cycle = cycle_rows[-1] if cycle_rows else None
    return {
        "created_at": now,
        "cycles_seen": len(cycle_rows),
        "last_cycle": {
            "at": last_cycle.get("created_at"),
            "verdict": last_cycle.get("verdict_status"),
            "route": last_cycle.get("route_status"),
            "feeds": last_cycle.get("feeds"),
            "degraded": last_cycle.get("degraded"),
            "reason_codes": last_cycle.get("reason_codes"),
        } if last_cycle else None,
        "open_position": open_position,
        "open_positions": open_positions,
        "directional_lean": directional_lean,
        "pool_directional_capacity": pool_capacity,
        "pool_status_counts": status_counts,
        "demotions_by_reason": demotions_by_reason,
        "regime_excluded_cycles": regime_excluded_cycles,
        "cycles_read": len(cycle_rows),
        "pool_size": len(active.get("active_strategies") or []),
        # Work waiting on a human, carried as data as well as a warning so a reader past the
        # threshold can see the queue shrink instead of only learning when it crosses back.
        "promotion_backlog": backlog,
        # Depth being accumulated toward an hourly OI feature source. Reported next to the pool
        # because the strategies it would re-base are in it.
        "open_interest_1h": oi_1h,
        "positioning": positioning,
        "fusion_parents": fusion_parents,
        "control_channel": {
            "last_inbound_at": inbound["at"] if inbound else None,
            "last_inbound_source": inbound["source"] if inbound else None,
            "silent_days": silent_days,
        },
        # THIS runtime's own paper trading — the only evidence about this codebase.
        "performance": {
            "closed_count": report.get("sample_size") if report else 0,
            "expectancy": (report.get("summary") or {}).get("expectancy") if report else None,
            # The same trades at the venue's rates. Paper R is measured on intended fills and
            # carries no costs by design (`cost.py`), which is right for the risk guard and
            # wrong for the question this board's headline asks — "is there an edge worth
            # expanding". Both are carried so the board can show the gap rather than pick.
            "expectancy_net": (report.get("net_summary") or {}).get("expectancy") if report else None,
            "uncostable_count": (report.get("net_summary") or {}).get("uncostable_count") if report else 0,
            "max_drawdown": (report.get("summary") or {}).get("max_drawdown") if report else None,
            "recommendation": report.get("recommendation") if report else None,
            # Computed here from the R values rather than added to the performance report:
            # that report is a versioned, persisted record, and this is a reading of it.
            #
            # Over the NET series, so the interval and the point estimate describe the same
            # quantity. An interval around gross R beside a net expectancy would be two
            # statistics about two different venues printed as one verdict. Rows that cannot be
            # priced are absent from the series rather than counted at gross —
            # `expectancy_gross_rows` below is what says so.
            "sample_verdict": sample_verdict([
                net for net in (feedback.net_result_r(o) for o in own_outcomes)
                if net is not None
            ]),
            # The same trades after fees and slippage — the figure `lifecycle` demotes on and
            # `guards` meters, which `expectancy` above is NOT: that comes from the persisted
            # feedback report and is cost-free by construction (`cost.py`).
            #
            # Reported alongside rather than replacing it, and this is the awkward-but-honest
            # option of three. Swapping it would silently change what a number the board has
            # printed for weeks means; changing `feedback.build_performance_report` would
            # change what a versioned persisted record means, which is its own increment; and
            # showing only the gross figure would have the board contradict the ladder it
            # reports on — an operator would watch strategies get suspended for losing money
            # next to a headline saying they make it. Two labelled numbers beat one lie.
            **_net_performance(own_outcomes),
        },
        # Imported crypto_AI_System history, reported separately and never merged above: it is
        # the predecessor's record, useful as context, not as evidence about this runtime.
        "imported_performance": {
            "closed_count": imported_report.get("sample_size") if imported_report else 0,
            "expectancy": (imported_report.get("summary") or {}).get("expectancy") if imported_report else None,
            "max_drawdown": (imported_report.get("summary") or {}).get("max_drawdown") if imported_report else None,
        },
        "digest": {
            "weekly_trend": (outcome_digest or {}).get("weekly_trend"),
            "monthly_trend": (outcome_digest or {}).get("monthly_trend"),
        } if outcome_digest else None,
        "counterfactual_by_reason": cf_summary,
        # Per gate: can its blocked trades tell the sign of what they would have returned?
        "counterfactual_verdicts": cf_verdicts,
        "counterfactual_closed": sum(1 for r in cf_records if r.get("outcome_closed") is True),
        "warnings": warnings,
    }


# A dashboard that only reports state makes the reader do the judging. These are the two
# judgements the board can make honestly from what it already has, and they belong at the
# TOP, because everything under them is evidence for them.
#
# Sample size is the first: `feedback` recommends on the numbers, but a recommendation off
# 11 closed trades is noise, and the board already knows to say INSUFFICIENT_SAMPLE about
# its trends while saying nothing about the headline it prints two lines above them.
MIN_MEANINGFUL_SAMPLE = 30

# ...but a count is the wrong test, and this board graduated on it. At 30 closed trades the
# headline stopped saying "표본 부족" and started saying "검토 가능", while the number it was
# qualifying — +0.08R over 60 trades, with an R standard deviation of 1.56 — sat 0.4 standard
# errors from zero. A fixed count answers "are there enough rows?"; the question a reader has
# is "can I tell the sign?", and those diverge exactly when the variance is large relative to
# the edge, which is always true of trade returns.
#
# So the board now computes the interval. `MIN_MEANINGFUL_SAMPLE` stays as a floor — below it
# even the interval is unstable — but it is no longer what promotes the verdict.
# Below this the spread cannot be estimated, so no interval is produced at all. A verdict
# from two observations is arithmetic, not evidence — see `sample_verdict`.
MIN_INTERVAL_SAMPLE = 5


def _net_performance(own_outcomes: list[dict[str, Any]]) -> dict[str, Any]:
    """This runtime's own paper record after costs, and how much of it could be priced.

    ``expectancy_net_rows``/``expectancy_gross_rows`` are the same disclosure
    ``lifecycle.compute_metrics`` makes: a row with no prices contributes its stored R, so a
    non-zero gross count means the mean below mixes two statistics. ``None`` for an empty
    ledger, never 0.0 — the board's own rule everywhere else.
    """
    if not own_outcomes:
        return {"expectancy_net": None, "expectancy_net_rows": 0, "expectancy_gross_rows": 0}
    judged = [lifecycle.outcome_judged_r(o) for o in own_outcomes]
    priced = sum(1 for _, costed in judged if costed)
    return {
        "expectancy_net": round(statistics.fmean(value for value, _ in judged), 4),
        "expectancy_net_rows": priced,
        "expectancy_gross_rows": len(judged) - priced,
    }


POWER_Z = 0.84               # 80% power, the usual pairing (CONFIDENCE_Z: see the import)


def sample_verdict(r_values: list[float]) -> dict[str, Any]:
    """Can this sample tell the sign of its own edge, and if not, how far off is it?

    Pure. `trades_needed` is for the OBSERVED effect: if the true edge is what has been seen
    so far, this is roughly the sample at which it would separate from zero. It is a scale
    ("weeks" vs "years"), not a promise — a smaller true edge needs quadratically more.
    """
    n = len(r_values)
    if n < MIN_INTERVAL_SAMPLE:
        # Not "no edge" — no estimate. Below a handful of observations the spread cannot be
        # estimated at all, and an interval computed anyway is arithmetic rather than
        # evidence. This floor was added after the gate section rated
        # MAX_CONSECUTIVE_LOSS_GATE_BLOCKED as costing money off TWO blocked trades that
        # happened to return the same number: a zero-width interval, read as certainty.
        return {"count": n, "mean_r": None, "stdev_r": None, "stderr_r": None,
                "ci_low": None, "ci_high": None, "distinguishable_from_zero": False,
                "trades_needed": None}
    mean = statistics.fmean(r_values)
    # Sample standard deviation (n-1), not population: these observations are a sample of what
    # the strategy or gate would do, never the whole of it, and pstdev understates the spread
    # of exactly that inference.
    stdev = statistics.stdev(r_values)
    stderr = stdev / math.sqrt(n)
    low, high = mean - CONFIDENCE_Z * stderr, mean + CONFIDENCE_Z * stderr
    needed = None
    if mean and stdev:
        needed = int(round(((CONFIDENCE_Z + POWER_Z) * stdev / abs(mean)) ** 2))
    return {
        "count": n,
        "mean_r": round(mean, 4),
        "stdev_r": round(stdev, 4),
        "stderr_r": round(stderr, 4),
        "ci_low": round(low, 4),
        "ci_high": round(high, 4),
        # The whole point: zero outside the interval, not merely a row count reached.
        # `stdev > 0` guards the degenerate case — identical observations produce a
        # zero-width interval that excludes zero by construction, which is absence of
        # observed variation rather than evidence of none.
        "distinguishable_from_zero": bool(stdev > 0 and (low > 0 or high < 0)),
        "trades_needed": needed,
    }
# The second: a gate whose BLOCKED trades would have been profitable is costing money. The
# numbers for that were always printed; the subtraction was left to the reader.
_GATE_COSTING = "손해"
_GATE_EARNING = "이익"
# Neither, and saying so: the blocked trades cannot tell the sign of what they would have
# returned. A gate here is not "fine" — it is unmeasured, which is a different instruction.
_GATE_UNDECIDED = "판단 불가"
# (The grant-expiry warning lived here until 2026-08-10 — grants retired, nothing expires.)


def _r(value: Any, digits: int = 2, *, signed: bool = True) -> str:
    """R-values to two decimals. ``-0.30682667R`` is not more truthful than ``-0.31R``, it
    is only harder to scan, and this board is read on a phone. Signed by default because
    the sign IS the reading for expectancy; ``signed=False`` for magnitudes like drawdown,
    where a ``+`` only invites reading a loss as a gain."""
    if value is None:
        return "?"
    try:
        return f"{float(value):{'+' if signed else ''}.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


def _stamp(value: Any) -> str:
    """``2026-07-26T03:55:33Z`` -> ``07-26 03:55``. The year is the same on every line."""
    text = str(value or "")
    return f"{text[5:10]} {text[11:16]}" if len(text) >= 16 else text


def _gate_rows(status: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    """Gates as ``(verdict, reason, bucket)``, costing first, each side worst-first.

    A gate blocks trades; ``expectancy_R`` is what those blocked trades would have
    returned. Positive means the gate blocked winners — it is costing money. Negative
    means it blocked losers — it is earning. Sorting by that sign is the whole point:
    the operator's question is "which gate should I change", and the answer was
    previously spread across five lines of missed/avoided arithmetic."""
    verdicts = status.get("counterfactual_verdicts") or {}
    rows: list[tuple[str, str, dict[str, Any]]] = []
    for reason, bucket in (status.get("counterfactual_by_reason") or {}).items():
        expectancy = bucket.get("expectancy_R")
        try:
            costing = float(expectancy) > 0
        except (TypeError, ValueError):
            continue        # an unusable number is not a verdict; leave it out of the ranking
        # The sign of a mean is not a verdict about a gate any more than it was about the
        # headline. `MAX_CONSECUTIVE_LOSS_GATE_BLOCKED` read "+2.00R × 2건 · 손해" — two
        # blocked trades, an interval many times wider than the estimate, and a label that
        # tells an operator to go change a risk gate. Same test as the performance headline,
        # applied to the same kind of claim.
        # Two different absences, and only one of them is a pass. A reason MISSING from the
        # map was never measured (an older status document, a caller that did not compute
        # it) and keeps the mean-sign reading it always had. A reason PRESENT but without an
        # interval was measured and found unmeasurable — too few observations to estimate a
        # spread — which is a finding, not a gap. Reading the second as the first is what
        # left a 2-trade gate rated "costing money".
        if reason in verdicts and not verdicts[reason].get("distinguishable_from_zero"):
            rows.append((_GATE_UNDECIDED, reason, bucket))
            continue
        rows.append((_GATE_COSTING if costing else _GATE_EARNING, reason, bucket))
    # Costing first (it is the only one that asks for an action), then earning, then the ones
    # that cannot say — those are sorted last because acting on them is the mistake.
    order = {_GATE_COSTING: 0, _GATE_EARNING: 1, _GATE_UNDECIDED: 2}
    rows.sort(key=lambda row: (order[row[0]], -abs(float(row[2].get("expectancy_R") or 0))))
    return rows


def _headline(status: dict[str, Any]) -> list[str]:
    """The two or three lines that answer "so what", before any evidence."""
    perf = status.get("performance") or {}
    closed = perf.get("closed_count") or 0
    # The costed figure decides the headline, because the headline is a claim about whether this
    # is worth real money and real money pays fees, spread and carry. Falls back to gross only
    # when nothing could be priced — an older status document, or a history with no fills on it —
    # so a caller that predates the net figure keeps the verdict it always got.
    expectancy = perf.get("expectancy_net")
    net_basis = expectancy is not None
    if not net_basis:
        expectancy = perf.get("expectancy")
    lines: list[str] = []

    if not closed:
        lines.append("판단: 자체 페이퍼 성과 없음 — 라이브 판단 근거 없음")
    else:
        verdict_stats = perf.get("sample_verdict") or {}
        thin = closed < MIN_MEANINGFUL_SAMPLE
        try:
            losing = float(expectancy) < 0
        except (TypeError, ValueError):
            losing = False
        # The interval decides, not the row count. "검토 가능" used to appear the moment the
        # 30th trade closed, on a figure sitting 0.4 standard errors from zero — a verdict
        # about how much data there is, printed where a verdict about what it shows belongs.
        # Absence of the interval is not evidence of indistinguishability. A caller that did
        # not compute it (a hand-built status, an older document) gets the count-based verdict
        # it always got, rather than a "판단 불가" asserted from a missing field.
        measured = verdict_stats.get("ci_low") is not None
        if thin:
            verdict = f"표본 부족 ({closed}건 < {MIN_MEANINGFUL_SAMPLE}건) — 확대 근거 없음"
        elif measured and not verdict_stats.get("distinguishable_from_zero"):
            need = verdict_stats.get("trades_needed")
            more = f", 관측 효과 검출에 ~{need:,}건" if need and need > closed else ""
            verdict = (
                f"판단 불가 — {_r(expectancy)}R × {closed}건, 95% 구간 "
                f"[{_r(verdict_stats.get('ci_low'))}, {_r(verdict_stats.get('ci_high'))}]이 "
                f"0을 포함{more}"
            )
        elif losing:
            verdict = (f"자체 성과 손실 중 ({_r(expectancy)}R"
                       + (", 0과 구별됨" if measured else "") + ") — 확대 근거 없음")
        else:
            verdict = (f"자체 성과 {_r(expectancy)}R × {closed}건"
                       + (", 0과 구별됨" if measured else "") + " — 검토 가능")
        # Which venue the number describes, stated on the line rather than inferred. The gross
        # figure is the one every earlier board printed, so a reader comparing against a
        # screenshot from last week has to be told the basis changed.
        verdict += " (비용 차감 후)" if net_basis else " (총액, 비용 미차감)"
        lines.append(f"판단: {verdict}")

    costing = [row for row in _gate_rows(status) if row[0] == _GATE_COSTING]
    if costing:
        lines.append(f"      게이트 {len(costing)}개가 손해 중 → 검토 필요")
    for warning in status.get("warnings") or []:
        lines.append(f"      ⚠ {warning}")
    return lines


def render_status_text(status: dict[str, Any]) -> str:
    """Render the board for a human deciding something, not for a log.

    Same data as before, reordered so the decisions surface: the judgement first, the
    evidence under it, and the parts that are only noise until they are not (grants)
    collapsed to one line."""
    lines = [f"=== crypto dashboard === {_stamp(status.get('created_at'))}", ""]
    lines += _headline(status)
    lines.append("")

    # --- now ---------------------------------------------------------------------
    last = status.get("last_cycle")
    position = status.get("open_position")
    positions = status.get("open_positions") or []
    where = (f"{position['direction']} {position['strategy_id']} @ {position['entry_price']}"
             + (f" (외 {len(positions) - 1}건)" if len(positions) > 1 else "")) if position else "포지션 없음"
    lines.append(f"지금   {where}")
    # The book's SHAPE, under the book itself. Printed only while something is open, because a
    # lean of 0 over an empty book is the field teaching a reader to skip the line — and this is
    # the line that matters on the day the book is one-way.
    # Gated on the FIELD, not on `positions`: this renderer is also handed status dicts built by
    # an older build (and by callers that assemble one by hand), where the key is simply absent.
    # A renderer that assumed its own newest field would crash on exactly the history an
    # operator opens the board to read.
    lean = status.get("directional_lean")
    if positions and isinstance(lean, dict) and isinstance(lean.get("lean"), int):
        mark = " ⚠ 한 방향 한도" if lean.get("at_limit") else ""
        note = f" · 방향불명 {lean['unattributed']}" if lean.get("unattributed") else ""
        lines.append(
            f"       방향 롱 {lean.get('long')} / 숏 {lean.get('short')}"
            f" · 편중 {lean['lean']:+d} (한도 ±{lean.get('limit')}){note}{mark}"
        )
    if last:
        feeds = last.get("feeds") or {}
        ok = sum(1 for state in feeds.values() if state == "ok")
        degraded = " ⚠ degraded" if last.get("degraded") else ""
        lines.append(f"       마지막 {last.get('verdict')}/{last.get('route')} · "
                     f"피드 {ok}/{len(feeds)}{degraded} · {_stamp(last.get('at'))}")
        if last.get("reason_codes"):
            lines.append(f"       사유 {', '.join(last['reason_codes'])}")
    else:
        lines.append("       마지막 사이클 기록 없음")
    counts = status.get("pool_status_counts") or {}
    breakdown = " · ".join(f"{name} {count}" for name, count in sorted(counts.items()))
    lines.append(f"       풀 {status.get('pool_size')}" + (f" ({breakdown})" if breakdown else ""))
    # Printed ONLY when the pool's own composition is what holds the book below the number of
    # contexts it routes — otherwise it is a line saying nothing happened, which is the field
    # that teaches a reader to skip the line above it. A capped book is not an error, so this
    # reads as an explanation rather than a warning.
    capacity = status.get("pool_directional_capacity")
    if isinstance(capacity, dict) and capacity.get("cap_binds"):
        lines.append(
            f"       └ 방향 편중으로 {capacity['routable_contexts']}개 컨텍스트 중"
            f" {capacity['reachable_book']}개까지만 보유 가능"
            f" (롱 {capacity['long_contexts']} / 숏 {capacity['short_contexts']},"
            f" 한도 ±{capacity['skew_cap']})"
        )
    # Why the demoted ones are demoted. A status count alone reads a duplicate an operator
    # removed on purpose and a strategy the metrics condemned as the same number.
    demotions = status.get("demotions_by_reason") or {}
    if demotions:
        lines.append("       강등 사유 " + " · ".join(
            f"{reason} {count}" for reason, count in sorted(demotions.items())))
    # Regime exclusions, beside the demotion reasons and for the same reason they are there: a
    # strategy whose rules keep firing into a regime its own backtest lost money in is not
    # trading, and its PAPER_ACTIVE status says the opposite. Rendered as a share of the cycles
    # read so "3 of 12" and "12 of 12" are different statements — the first is the filter doing
    # its job, the second is a strategy that has stopped working here.
    regime_excluded = status.get("regime_excluded_cycles") or {}
    if regime_excluded:
        read = status.get("cycles_read") or 0
        lines.append("       regime 배제 " + " · ".join(
            f"{sid} {count}/{read}" for sid, count in sorted(regime_excluded.items())))
    backlog = status.get("promotion_backlog") or {}
    if backlog.get("count"):
        lines.append(f"       승격 대기 {backlog['count']} (알림 임계 {backlog.get('threshold')})")
    elif backlog.get("candidates_read"):
        # A queue of zero over a full store is the case this line exists for. It used to render
        # as nothing at all — the count was falsy, `deferred_unjudgeable` was empty because an
        # earlier filter had already taken everything, and the board printed neither. Measured
        # 2026-08-04: 474 candidates at the current cost basis, every one of them stopped at the
        # holdout gate, and the daily report said nothing about any of it.
        #
        # EVERY nonzero axis, not a top-N. This line started as the largest axis alone, grew a
        # second because one was true-but-misleading (measured 2026-08-04: `cost_basis` 546 —
        # legacy mints that drain on their own — over `holdout_insufficient` 409, the whole of
        # the CURRENT basis failing forward; naming only the first read as "old evidence, it
        # will clear"), and the same argument does not stop at two: any truncation reintroduces
        # a silent axis, one axis later. The partition sums to `candidates_read` (pinned in the
        # pool tests), so the full breakdown is the one rendering that cannot point away from a
        # finding — the daily question this line answers is not "is the queue zero" but "WHY is
        # it zero", and Thomas asked for the whole answer (2026-08-11).
        #
        # Size order, ties on `BACKLOG_REFUSAL_AXES` order — the door's own — so the line does
        # not flip between two equal axes from one morning to the next.
        refused = backlog.get("refused") or {}
        ranked = sorted(
            (axis for axis in pool.BACKLOG_REFUSAL_AXES if refused.get(axis)),
            key=lambda axis: (-refused[axis], pool.BACKLOG_REFUSAL_AXES.index(axis)),
        )
        detail = "".join(f" · {axis} {refused[axis]}건" for axis in ranked)
        lines.append(
            f"       승격 대기 0 (판정 후보 {backlog['candidates_read']}건{detail})"
        )
    # Named on its own line: these are not waiting on an operator, they are waiting on a
    # timeframe that trades often enough for the lifecycle to reach a verdict. Folding them into
    # the backlog count is what made a pool of 89 look like 89 judged strategies.
    deferred = backlog.get("deferred_unjudgeable") or []
    if deferred:
        lines.append(
            f"       판정 불가 보류 {len(deferred)} "
            f"({backlog.get('max_days_to_lifecycle_window')}일 내 lifecycle 창 미달)"
        )
    oi_1h = status.get("open_interest_1h") or {}
    if oi_1h.get("symbols"):
        state = "적격" if oi_1h.get("eligible") else "축적 중"
        lines.append(
            f"       1h OI {oi_1h.get('min_covered_days')}/{oi_1h.get('required_days')}일 "
            f"({state}, 최소 커버 심볼 기준)"
        )
    parents = status.get("fusion_parents") or {}
    if parents.get("candidates_read"):
        n = parents.get("eligible") or 0
        # Zero is the case this line exists for, so it says so rather than printing a 0 the
        # eye slides over — the fusion half of every fire is then drawing from nothing.
        note = "fusion 경로 정지" if n == 0 else f"후보 {parents['candidates_read']}건 중"
        lines.append(f"       fusion 부모 {n}개 리니지 ({note})")
    positioning = status.get("positioning") or {}
    if positioning.get("cells"):
        state = "적격" if positioning.get("eligible") else "축적 중"
        lines.append(
            f"       포지셔닝 {positioning.get('min_covered_days')}/{positioning.get('required_days')}일 "
            f"({state}, 최소 커버 셀 기준 · 피처 미연결)"
        )
    lines.append("")

    # --- performance -------------------------------------------------------------
    perf = status.get("performance") or {}
    closed = perf.get("closed_count") or 0
    lines.append(f"성과   자체 페이퍼: {_r(perf.get('expectancy'))}R × {closed}건 (총액) · "
                 f"dd {_r(perf.get('max_drawdown'), signed=False)}R → {perf.get('recommendation')}")
    if perf.get("expectancy_net") is not None:
        # Directly under the gross line, and both labelled: the two answer the same question
        # about different venues, the gap between them IS the reading when an edge is thin, and
        # the board used to print only the first one — which is why a pool of negative-expectancy
        # strategies looked healthy for weeks. This is the number that decides anything.
        #
        # The disclosure counts rows the cost model could not price, which contribute their
        # stored R and therefore mix two statistics into the mean. It reads `expectancy_gross_rows`
        # from `_net_performance` rather than the report's `uncostable_count`: same idea, but the
        # board's own figure comes from `lifecycle.outcome_judged_r`, which is what the ladder
        # actually demotes on, and quoting the other one would let the two drift.
        mixed = perf.get("expectancy_gross_rows") or 0
        lines.append(
            f"       비용 반영: {_r(perf.get('expectancy_net'))}R — 강등·리스크 판정이 읽는 값"
            + (f" (그중 {mixed}건은 가격 없어 총액 기준)" if mixed else "")
        )
    if closed and closed < MIN_MEANINGFUL_SAMPLE:
        # Attached to the number it qualifies, not stranded in a trends section below.
        lines.append(f"       ⚠ {closed}건은 판단 불가 표본 ({MIN_MEANINGFUL_SAMPLE}건 이상 필요)")
    sv = perf.get("sample_verdict") or {}
    if sv.get("stderr_r") is not None:
        # The dispersion, once, next to the mean it qualifies. Without it "+0.08R" reads as a
        # result; with it, as one draw from something 20x wider.
        lines.append(
            f"       표준편차 {_r(sv.get('stdev_r'), signed=False)}R · 표준오차 "
            f"{_r(sv.get('stderr_r'), signed=False)}R · 95% 구간 "
            f"[{_r(sv.get('ci_low'))}, {_r(sv.get('ci_high'))}]"
        )
        if not sv.get("distinguishable_from_zero") and sv.get("trades_needed"):
            lines.append(
                f"       0과 구별되려면 관측 효과 기준 약 {sv['trades_needed']:,}건 "
                f"(현재 {closed}건)"
            )
    digest_block = status.get("digest") or {}
    trends = [f"{label.split('_')[0]} {((digest_block.get(label) or {}).get('verdict'))}"
              for label in ("weekly_trend", "monthly_trend") if digest_block.get(label)]
    if trends:
        lines.append("       추세 " + " · ".join(trends))
    imported = status.get("imported_performance") or {}
    if imported.get("closed_count"):
        # Indented and parenthesised on purpose: sitting flush under a negative own-result,
        # a positive imported number reads as "we are up", which is the opposite of true.
        lines.append(f"       (참고: imported {_r(imported.get('expectancy'))}R × "
                     f"{imported['closed_count']}건 = crypto_AI_System 이력, 이 런타임 아님)")
    lines.append("")

    # --- gates -------------------------------------------------------------------
    rows = _gate_rows(status)
    if rows:
        lines.append(f"게이트 {status.get('counterfactual_closed')} 섀도우 — 막은 거래의 기대값 기준")
        verdicts = status.get("counterfactual_verdicts") or {}
        for verdict, reason, bucket in rows:
            mark = {_GATE_COSTING: "🔴", _GATE_EARNING: "🟢"}.get(verdict, "⚪")
            line = (
                f"  {mark} {reason[:44]:<44} {_r(bucket.get('expectancy_R'))}R × "
                f"{bucket.get('closed_count')}건 · {bucket.get('missed_opportunity')} 놓침 / "
                f"{bucket.get('avoided_loss')} 회피"
            )
            # The interval, only where it changes the reading: a row whose sign is unknown
            # must carry the reason it is unknown, or ⚪ is just a third colour.
            sv = verdicts.get(reason) or {}
            if verdict == _GATE_UNDECIDED:
                line += (
                    f"  [95% {_r(sv['ci_low'])}~{_r(sv['ci_high'])}]"
                    if sv.get("ci_low") is not None else
                    f"  [{sv.get('count', 0)}건 — 폭 추정 불가]"
                )
            lines.append(line)
        lines.append("")

    return "\n".join(lines).rstrip()


def _days_since(stamp: str, now: Any) -> int | None:
    """Whole days from ``stamp`` to ``now``; None when either will not parse.

    Floored, and never negative: a state file stamped slightly ahead of the board's clock
    is zero days old, not minus one, and a warning threshold must not be crossed from
    below by a clock skew."""
    try:
        elapsed = (timeutil.parse_iso(str(now)) - timeutil.parse_iso(stamp)).total_seconds()
    except (MvpRuntimeError, TypeError, ValueError):
        return None
    return max(0, int(elapsed // 86400))


def main(argv: list[str] | None = None) -> int:
    force_utf8_io()
    parser = argparse.ArgumentParser(description="Crypto pipeline status board (read-only).")
    parser.add_argument("--json", action="store_true", help="emit the full status as JSON")
    parser.add_argument("--cycles", type=int, default=12, help="how many recent cycle records to read")
    parser.add_argument(
        "--account", action="store_true",
        help="also read the live exchange account (balance/positions/P&L) — makes a network call",
    )
    args = parser.parse_args(argv)
    status = build_status(cycles=args.cycles)

    # Opt-in only: without --account this board keeps its "no gate, no network" posture and
    # reports purely from what the runtime already persisted. The live read is a separate,
    # separately-gated capability, so asking for it has to be deliberate.
    account_snapshot = None
    if args.account:
        account_snapshot, account_record = account.read_account()
        status["account"] = account_record

    if args.json:
        sys.stdout.write(json.dumps(status, ensure_ascii=False, indent=1) + "\n")
    else:
        sys.stdout.write(render_status_text(status) + "\n")
        if args.account:
            sys.stdout.write(account.render_account_text(account_snapshot) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

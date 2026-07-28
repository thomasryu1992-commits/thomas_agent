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
from . import account, counterfactual, digest, feedback, paper, pool


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


def _grants(root: Path) -> list[dict[str, Any]]:
    grants_dir = root / ".runtime_governance_state" / "safety_flag_activations"
    rows: list[dict[str, Any]] = []
    if not grants_dir.is_dir():
        return rows
    for path in sorted(grants_dir.glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            rows.append({"provider_id": record.get("provider_id", path.stem),
                         "expires_at": record.get("expires_at")})
        except (OSError, ValueError):
            rows.append({"provider_id": path.stem, "expires_at": "UNREADABLE"})
    return rows


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
        for entry in active.get("active_strategies") or []:
            status = str(entry.get("status") or "?")
            status_counts[status] = status_counts.get(status, 0) + 1
    except MvpRuntimeError as exc:
        active, status_counts = {"active_strategies": []}, {}
        warnings.append(f"active pool unreadable ({exc.reason_code})")

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
        "pool_status_counts": status_counts,
        "pool_size": len(active.get("active_strategies") or []),
        # THIS runtime's own paper trading — the only evidence about this codebase.
        "performance": {
            "closed_count": report.get("sample_size") if report else 0,
            "expectancy": (report.get("summary") or {}).get("expectancy") if report else None,
            "max_drawdown": (report.get("summary") or {}).get("max_drawdown") if report else None,
            "recommendation": report.get("recommendation") if report else None,
            # Computed here from the R values rather than added to the performance report:
            # that report is a versioned, persisted record, and this is a reading of it.
            "sample_verdict": sample_verdict([
                float(o["result_R"]) for o in own_outcomes
                if isinstance(o.get("result_R"), (int, float))
            ]),
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
        "grants": _grants(root),
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
CONFIDENCE_Z = 1.96          # two-sided 95%
POWER_Z = 0.84               # 80% power, the usual pairing


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
# Grants are nine lines of noise until one is near expiry, and then they are the only
# lines that matter.
GRANT_EXPIRY_WARNING_DAYS = 7


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
    lines.append("")

    # --- performance -------------------------------------------------------------
    perf = status.get("performance") or {}
    closed = perf.get("closed_count") or 0
    lines.append(f"성과   자체 페이퍼: {_r(perf.get('expectancy'))}R × {closed}건 · "
                 f"dd {_r(perf.get('max_drawdown'), signed=False)}R → {perf.get('recommendation')}")
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

    # --- grants ------------------------------------------------------------------
    grants = status.get("grants") or []
    if grants:
        soonest = min(grants, key=lambda g: str(g.get("expires_at") or ""))
        expiry = str(soonest.get("expires_at") or "")
        days = _days_until(expiry, status.get("created_at"))
        urgent = days is not None and days <= GRANT_EXPIRY_WARNING_DAYS
        summary = (f"권한   {len(grants)}건 · 가장 이른 만료 {expiry[:10]} "
                   f"({soonest.get('provider_id')})")
        lines.append(summary + (f" ⚠ {days}일 남음" if urgent else
                                (f" — {days}일 남음" if days is not None else "")))
        if urgent:
            for grant in sorted(grants, key=lambda g: str(g.get("expires_at") or "")):
                lines.append(f"       {grant['provider_id']:24} {str(grant['expires_at'])[:10]}")
    return "\n".join(lines).rstrip()


def _days_until(expires_at: str, now: Any) -> int | None:
    try:
        return int((timeutil.parse_iso(expires_at) - timeutil.parse_iso(str(now))).total_seconds() // 86400)
    except (MvpRuntimeError, TypeError, ValueError):
        return None


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

#!/usr/bin/env python3
"""Operator tool: the forward cohort (``crypto/forward_cohort.py``; Phase 1, option A).

Four subcommands, each dry unless it says otherwise:

- ``freeze`` lists the lineages a cohort frozen now would hold, with the attempt counts per
  context; ``--apply`` appends the sealed record. Membership never changes after that.
- ``walk`` advances every member-context to the newest closed bar through the venue collector;
  ``--apply`` writes the rows and the walker state. Without it the walk is computed and
  reported and nothing is written.
- ``freeze-nulls`` lists the null arm each frozen cohort without a CURRENT-version one would get: a
  coin-flip twin per member (``crypto/forward_cohort_null.py``); ``--apply`` appends the sealed
  record. A cohort whose arm predates the current version (v1, paced by the pooled trade count over
  one leg's bars) gets a v2 that names it and says how many twins' rates changed; the v1 record and
  its rows stay, unwalked. ``walk`` walks the active twins after the members, into the null arm's
  own stores.
- ``report`` prints each member's forward numbers over its cohort rows. Reads only. ``--detail``
  adds a second table per cohort: win rate, profit factor, drawdown, the average win and loss, what
  fees and slippage cost per trade, and the gap to the recorded backtest and holdout expectancy.
  These columns are computed here and nowhere else; no verdict, board or ranking reads them.
  ``--arms`` adds, per timeframe, the mean net R per trade of the members and of their coin-flip
  twins (``crypto/forward_cohort_null.py``) side by side, also display only. Each cohort ends with
  one line counting the matched signals a door refused an entry, per door, since the walker began
  counting (THROUGHPUT P0-3; display only).

Nothing here reaches the pool, the arming door or an order: cohort rows live in their own
store, which the arming door's reader refuses. Writes runtime state, so it runs in the container
as uid 10001, in module form::

    docker exec thomas-scheduler python -m scripts.forward_cohort freeze
    docker exec thomas-scheduler python -m scripts.forward_cohort freeze --apply
    docker exec thomas-scheduler python -m scripts.forward_cohort freeze-nulls --apply
    docker exec thomas-scheduler python -m scripts.forward_cohort walk --apply
    docker exec thomas-scheduler python -m scripts.forward_cohort report
    docker exec thomas-scheduler python -m scripts.forward_cohort report --detail
    docker exec thomas-scheduler python -m scripts.forward_cohort report --arms

A damaged candidate, pool, forward or cohort store refuses the run (``EXIT_BLOCKED``) before
anything is fetched or written.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime import timeutil  # noqa: E402
from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK  # noqa: E402
from runtime.mvp_runtime.errors import MvpRuntimeError  # noqa: E402
from runtime.mvp_runtime.state_guard import assert_not_foreign_root_run  # noqa: E402
from runtime.mvp_runtime.crypto import forward_book, forward_cohort, forward_cohort_null  # noqa: E402
from runtime.mvp_runtime.crypto.candidate_identity import candidate_id  # noqa: E402
from runtime.mvp_runtime.crypto.forward_confirmation import forward_outcomes_for  # noqa: E402
from runtime.mvp_runtime.crypto.outcome_math import net_result_r  # noqa: E402
from runtime.mvp_runtime.crypto.pool_state import read_candidates  # noqa: E402


def _freeze(root: Path, now: str, apply: bool) -> int:
    record = forward_cohort.freeze_cohort(root, now=now, apply=apply)
    print("%-26s %-4s %-28s %-15s %s" % ("candidate", "tf", "family", "holdout", "selected"))
    for member in record["members"]:
        print("%-26s %-4s %-28s %-15s %s" % (
            member["candidate_id"], member["timeframe"], member["strategy_family"],
            member["holdout_status"], member["selected_at_utc"][:10]))
    print(f"\n{record['cohort_size']} member(s) over {len(record['context_sizes'])} context(s); "
          f"largest context K = {max(record['context_sizes'].values(), default=0)}")
    if apply:
        print(f"FROZEN {record['cohort_id']} at {record['frozen_at_utc']}")
    else:
        print("DRY RUN — nothing frozen. Re-run with --apply.")
    return EXIT_OK


def _freeze_nulls(root: Path, now: str, apply: bool) -> int:
    records = forward_cohort_null.freeze_nulls(root, now=now, apply=apply)
    if not records:
        print("every frozen cohort already has its null arm")
        return EXIT_OK
    superseded = {r["record_sha256"]: r for r in forward_cohort_null.read_null_records(root)}
    for record in records:
        print(f"{record['cohort_id']}: {record['null_size']} twin(s), rate rule: {record['rate_rule']}")
        old = superseded.get((record.get("supersedes") or {}).get("record_sha256"))
        if old is not None:
            before = {t["parent_candidate_id"]: t["signal_rate"] for t in old["members"]}
            changed = [t for t in record["members"]
                       if before.get(t["parent_candidate_id"]) != t["signal_rate"]]
            print(f"  supersedes {old['forward_cohort_nulls_version']} ({old['rate_rule']}): "
                  f"{len(changed)} twin rate(s) change, {record['null_size'] - len(changed)} unchanged")
            for twin in changed[:5]:
                print(f"    {twin['parent_candidate_id']} {twin['timeframe']} "
                      f"{before.get(twin['parent_candidate_id'])} -> {twin['signal_rate']}")
        for skip in record["skipped"]:
            print(f"  skipped {skip['parent_candidate_id']}: {skip['reason']}")
    print("FROZEN" if apply else "DRY RUN — nothing frozen. Re-run with --apply.")
    return EXIT_OK


def _walk(root: Path, now: str, apply: bool) -> int:
    frame_for = forward_cohort.memoized_frames(forward_cohort.collector_frames(root, now=now))
    summary = forward_cohort.run_cohort_walk(root, now=now, frame_for=frame_for, persist=apply)
    print(f"members={summary['members']} contexts={summary['contexts']} walked={summary['walked']} "
          f"opened={summary['opened']} settled={summary['settled']}")
    for line in summary["failed"]:
        print(f"  FAILED {line}")
    for candidate in summary["unparseable"]:
        print(f"  UNPARSEABLE {candidate} (spec did not parse; not walked)")
    nulls = forward_cohort_null.run_null_walk(root, now=now, frame_for=frame_for, persist=apply)
    if nulls["members"]:
        print(forward_cohort_null.status_line(nulls))
    if not apply:
        print("DRY RUN — nothing written. Re-run with --apply.")
    return EXIT_OK


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def priced_rows(
    record: Mapping[str, Any], outcomes: Iterable[Mapping[str, Any]],
) -> list[tuple[Mapping[str, Any], float]]:
    """The rows ``forward_cohort.priced_nets`` prices, each beside its net R, oldest settlement
    first. Same filter as that function, so every detail column stands on the rows the judge's mean
    stands on."""
    priced = []
    for row in forward_outcomes_for(record, outcomes):
        net = net_result_r(row)
        if net is None:
            continue
        try:
            settled = timeutil.parse_iso(str(row.get("created_at_utc") or ""))
        except (ValueError, TypeError):
            continue
        priced.append((settled, row, float(net)))
    priced.sort(key=lambda item: item[0])
    return [(row, net) for _, row, net in priced]


def _mean_of(rows: list[Mapping[str, Any]], field: str) -> float | None:
    """The per-trade mean of one recorded cost field, or None when any row lacks it: a mean over
    the rows that happen to carry the field would describe fewer trades than the line's ``n``."""
    values = [_number(row.get(field)) for row in rows]
    if not values or any(v is None for v in values):
        return None
    return sum(values) / len(values)


def detail_columns(record: Mapping[str, Any], outcomes: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """One member's read-only detail over its priced forward rows (gap analysis §3, Thomas
    2026-09-30 Q1). Display only: nothing here is read by the judge, the board or a ranking.

    Every figure is on the net R ``judge_forward`` prices (``outcome_math.net_result_r``: fees,
    slippage and carry inside), so the win rate and the mean beside it share one cost basis. A
    figure that cannot be computed is None, never 0: no rows, no losing trade for the profit
    factor, a row without the cost field, a record without the backtest or holdout expectancy.

    ``max_drawdown_r`` is the largest peak-to-trough fall of cumulative net R in settlement order,
    reported positive. ``vs_backtest_r`` and ``vs_holdout_r`` are the forward mean minus the
    expectancy the candidate's own evidence records, so a negative value is forward doing worse."""
    priced = priced_rows(record, outcomes)
    rows = [row for row, _ in priced]
    nets = [net for _, net in priced]
    wins = [v for v in nets if v > 0]
    losses = [v for v in nets if v < 0]
    cumulative = peak = drawdown = 0.0
    for value in nets:
        cumulative += value
        peak = max(peak, cumulative)
        drawdown = max(drawdown, peak - cumulative)
    mean = sum(nets) / len(nets) if nets else None
    evidence = record.get("backtest_evidence") or {}
    backtest = _number(evidence.get("expectancy"))
    holdout = _number((evidence.get("holdout") or {}).get("expectancy"))
    return {
        "n": len(nets),
        "win_rate": len(wins) / len(nets) if nets else None,
        "profit_factor": sum(wins) / -sum(losses) if losses else None,
        "max_drawdown_r": drawdown if nets else None,
        "avg_win_r": sum(wins) / len(wins) if wins else None,
        "avg_loss_r": -sum(losses) / len(losses) if losses else None,
        "fee_cost_r": _mean_of(rows, "fee_cost_r"),
        "slippage_cost_r": _mean_of(rows, "slippage_cost_r"),
        "vs_backtest_r": None if mean is None or backtest is None else mean - backtest,
        "vs_holdout_r": None if mean is None or holdout is None else mean - holdout,
    }


def detail_report(root: Path) -> dict[tuple[Any, str], dict[str, Any]]:
    """:func:`detail_columns` for every member of every frozen cohort, keyed by cohort id and
    candidate id. A member is judged from its frozen selection time, as ``cohort_report`` judges
    it; one whose candidate row is gone has no entry. The stores are read here and again by
    ``cohort_report``, so a walk that lands between the two reads can leave one line's ``n`` a row
    apart from the table above it."""
    latest = {candidate_id(record): record for record in read_candidates(root)}
    rows = forward_cohort.read_cohort_outcomes(root)
    detail: dict[tuple[Any, str], dict[str, Any]] = {}
    for cohort in forward_cohort.read_cohorts(root):
        for member in cohort.get("members") or []:
            record = latest.get(str(member.get("candidate_id")))
            if record is None:
                continue
            judged = {**record, "created_at_utc": member.get("selected_at_utc")}
            detail[(cohort.get("cohort_id"), str(member.get("candidate_id")))] = detail_columns(judged, rows)
    return detail


def arm_means(root: Path) -> dict[str, dict[str, Any]]:
    """Per timeframe, the members' and their twins' mean net R per trade (display only, Thomas
    2026-09-30 gap analysis Q1). The board's verdict counts (``forward_cohort_null.arm_comparison``)
    are the designed comparison; this is the size of the difference beside them.

    The rows are the ones the judge prices: a member's as :func:`detail_report` resolves it, a
    twin's as ``forward_cohort_null.null_report`` judges it, over the active null arm only (a twin
    of a superseded arm and its rows never enter). Per timeframe and never pooled across them: the
    null's own baseline differs by timeframe, so one pooled figure would mix different questions.
    Members of different sizes are pooled within a timeframe, so a mean says direction, not a
    per-lineage magnitude. An arm with no priced row has ``None`` for its mean, never 0."""
    from runtime.mvp_runtime.crypto import forward_cohort_null

    real: dict[str, list[float]] = {}
    latest = {candidate_id(record): record for record in read_candidates(root)}
    rows = forward_cohort.read_cohort_outcomes(root)
    for cohort in forward_cohort.read_cohorts(root):
        for member in cohort.get("members") or []:
            record = latest.get(str(member.get("candidate_id")))
            if record is None:
                continue
            judged = {**record, "created_at_utc": member.get("selected_at_utc")}
            real.setdefault(str(member.get("timeframe")), []).extend(
                net for _, net in priced_rows(judged, rows))
    null: dict[str, list[float]] = {}
    null_rows = forward_cohort_null.read_null_outcomes(root)
    for arm in forward_cohort_null.active_null_records(root):
        for twin in arm.get("members") or []:
            judged = {"candidate_id": twin.get("null_id"), "created_at_utc": twin.get("selected_at_utc"),
                      "strategy_spec": twin.get("null_spec") or {}}
            null.setdefault(str(twin.get("timeframe")), []).extend(
                net for _, net in priced_rows(judged, null_rows))
    means: dict[str, dict[str, Any]] = {}
    for timeframe in sorted(set(real) | set(null)):
        real_nets, null_nets = real.get(timeframe, []), null.get(timeframe, [])
        real_mean = sum(real_nets) / len(real_nets) if real_nets else None
        null_mean = sum(null_nets) / len(null_nets) if null_nets else None
        means[timeframe] = {
            "real_n": len(real_nets), "real_mean_r": real_mean,
            "null_n": len(null_nets), "null_mean_r": null_mean,
            "real_minus_null_r": None if real_mean is None or null_mean is None else real_mean - null_mean,
        }
    return means


def _print_arms(means: Mapping[str, Mapping[str, Any]]) -> None:
    print("arms (display only; the board's verdict counts are the designed comparison). Mean net R per "
          "trade over the rows the judge prices, per timeframe, never pooled across timeframes; '-' is "
          "no priced row.")
    layout = "  %-4s %7s %9s %7s %9s %10s"
    print(layout % ("tf", "real_n", "real_R", "null_n", "null_R", "real-null"))

    def signed(value: float | None) -> str:
        return "-" if value is None else f"{value:+.3f}"

    for timeframe, arm in means.items():
        print(layout % (timeframe, arm["real_n"], signed(arm["real_mean_r"]), arm["null_n"],
                        signed(arm["null_mean_r"]), signed(arm["real_minus_null_r"])))


_DETAIL_COLUMNS = (
    ("win%", "win_rate", lambda v: f"{v * 100:.0f}"),
    ("PF", "profit_factor", lambda v: f"{v:.2f}"),
    ("maxDD_R", "max_drawdown_r", lambda v: f"{v:.3f}"),
    ("avgW_R", "avg_win_r", lambda v: f"{v:.3f}"),
    ("avgL_R", "avg_loss_r", lambda v: f"{v:.3f}"),
    ("fee_R", "fee_cost_r", lambda v: f"{v:.4f}"),
    ("slip_R", "slippage_cost_r", lambda v: f"{v:.4f}"),
    ("vs_bt_R", "vs_backtest_r", lambda v: f"{v:+.3f}"),
    ("vs_ho_R", "vs_holdout_r", lambda v: f"{v:+.3f}"),
)


def _print_detail(
    cohort: Mapping[str, Any], members: list[Mapping[str, Any]],
    detail: Mapping[tuple[Any, str], Mapping[str, Any]],
) -> None:
    print("  detail (display only; no verdict, board or ranking reads it). Net R as the judge prices "
          "it; fee_R and slip_R are the mean per trade; vs_bt_R and vs_ho_R are the forward mean "
          "minus the recorded backtest and holdout expectancy; '-' is not computable.")
    layout = "  %-26s %5s" + " %8s" * len(_DETAIL_COLUMNS)
    print(layout % ("candidate", "n", *(title for title, _, _ in _DETAIL_COLUMNS)))
    for m in members:
        columns = detail.get((cohort.get("cohort_id"), str(m.get("candidate_id"))))
        if columns is None:
            print(layout % (m.get("candidate_id"), "-", *("-" for _ in _DETAIL_COLUMNS)))
            continue
        print(layout % (m.get("candidate_id"), columns["n"], *(
            "-" if columns[key] is None else render(columns[key]) for _, key, render in _DETAIL_COLUMNS)))


def _print_refusals(members: list[Mapping[str, Any]]) -> None:
    """One line: the cohort's refused entries per door, summed over its members, and the earliest bar
    any member started counting. Members walked before the counter existed lost the bars before it."""
    totals: dict[str, int] = {}
    since: str | None = None
    for m in members:
        cell = m.get("entry_refusals") or {}
        for kind, n in (cell.get("counts") or {}).items():
            totals[kind] = totals.get(kind, 0) + int(n)
        start = cell.get("from")
        if isinstance(start, str) and (since is None or start < since):
            since = start
    if since is None:
        print("  entry refusals: not counted yet (the walker counts from its first walk on this version)")
        return
    shown = " · ".join(f"{kind} {totals.get(kind, 0)}" for kind in forward_book.ENTRY_REFUSAL_KINDS)
    print(f"  entry refusals since {since} (display only): {shown}")


def _report(root: Path, detail: bool = False, arms: bool = False) -> int:
    columns = detail_report(root) if detail else {}
    for cohort in forward_cohort.cohort_report(root):
        members = sorted(cohort["members"], key=lambda m: -(m.get("priceable_count") or 0))
        print(f"{cohort['cohort_id']} frozen {cohort['frozen_at_utc']} K={cohort['cohort_size']}")
        print("  %-26s %-4s %-28s %5s %9s %6s  %s" % (
            "candidate", "tf", "family", "n", "mean_R", "slices", "status (display only)"))
        for m in members:
            mean = m.get("mean_net_r")
            print("  %-26s %-4s %-28s %5s %9s %6s  %s" % (
                m.get("candidate_id"), m.get("timeframe") or "", m.get("strategy_family") or "",
                m.get("priceable_count", ""), "" if mean is None else f"{mean:+.3f}",
                m.get("active_slices", ""),
                m.get("status") + (f" ({m['waiting_on']})" if m.get("waiting_on") else "")
                + (f"  sibling of {m['sibling_of']}" if m.get("sibling_of") else "")))
        _print_refusals(members)
        if detail:
            _print_detail(cohort, members, columns)
    if arms:
        _print_arms(arm_means(root))
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="forward_cohort", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("freeze", "freeze-nulls", "walk"):
        p = sub.add_parser(name)
        p.add_argument("--apply", action="store_true", help="write; without it nothing is written")
    report = sub.add_parser("report")
    report.add_argument("--detail", action="store_true",
                        help="add the per-member detail table (display only)")
    report.add_argument("--arms", action="store_true",
                        help="add the members' and their twins' mean net R per timeframe (display only)")
    args = parser.parse_args(argv)

    try:
        assert_not_foreign_root_run(None)
    except MvpRuntimeError as exc:
        print(f"BLOCKED {exc.reason_code}: {exc.reason}", file=sys.stderr)
        return EXIT_BLOCKED

    now = timeutil.utc_now_iso()
    try:
        if args.command == "freeze":
            return _freeze(ROOT, now, args.apply)
        if args.command == "freeze-nulls":
            return _freeze_nulls(ROOT, now, args.apply)
        if args.command == "walk":
            return _walk(ROOT, now, args.apply)
        return _report(ROOT, args.detail, args.arms)
    except MvpRuntimeError as exc:
        print(f"BLOCKED {exc.reason_code}: {exc.reason}", file=sys.stderr)
        return EXIT_BLOCKED


if __name__ == "__main__":
    sys.exit(main())

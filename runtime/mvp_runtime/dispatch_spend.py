"""Dispatch spend watch — §6-3's dormant money-guard, finally with code behind it.

Thomas decided the shape on 2026-07-30 (``docs/proposals/HERMES_AGENT_DISPATCH_V0.1.md`` §6):
**no hard cap** — when assistant-originated dispatch work accumulates more than 50 USD of cost
in one UTC day, the operator is told; nothing is stopped. The proposal's own caveat is the
first thing to know about this module: on the free tiers this deployment runs,
``budgets.recorded_usage_budget`` deliberately does not meter cost at all ("reporting a
computed 0 would claim a measurement nobody took"), so ``cost_used`` is 0 on every row and
this threshold is **dormant by design** — a guard for the day real cost appears, not a control
that does anything today. The status line says so out loud (``runs=N`` beside the cost),
because a watch that reads "0.00 USD" without that qualifier would be claiming a measurement
that was never taken.

The alert rides the scheduler's existing failure-transition notifier, the same way
``crypto_data_review`` raises its stall: crossing the threshold raises ``SchedulerBlocked``,
the fire is recorded ``failed:DISPATCH_SPEND_OVER_THRESHOLD``, and the operator gets the
transition message — once on crossing, once on recovery, no spam in between. Under the
threshold the fire is a quiet status line, which is the normal fire.

Attribution is the task record's own: ``source.requester.requester_id ==
"assistant_bridge"``, the stamp every dispatched task has carried since the door shipped, and
constitutionally true since the peer-uid check (2026-08-21). Cost joins through the trace:
``budget_usage`` rows share the task's ``trace_id``. The scan includes archives bounded to the
day (``appended_since``) — a day is a window of time, and ``iter_records`` answers windows of
time wrongly and silently.
"""

from __future__ import annotations

from typing import Any, Mapping

from .store import LedgerStore

# Thomas 2026-07-30: currency threshold only, no hard cap, no count criterion (the
# request-count storm gap is a recorded, accepted coverage hole — proposal §6-5).
THRESHOLD_USD = 50.0
# The only two kinds this measurement reads. Passed to the ledger as a parse prescreen, so the
# fire stops paying to decode the cycle rows that make up almost all of the file.
SCANNED_KINDS = frozenset({"task", "budget_usage"})
DISPATCH_SPEND_OVER = "DISPATCH_SPEND_OVER_THRESHOLD"
ASSISTANT_REQUESTER = "assistant_bridge"


def measure_day(store: LedgerStore, *, now: str) -> dict[str, Any]:
    """One UTC day of assistant-dispatched cost, measured from the ledger. Read-only.

    Returns ``runs`` (dispatched tasks received today), ``cost_usd`` (their summed
    ``cost_used`` across ``budget_usage`` rows), and ``metered_rows`` — how many of those
    rows carried a nonzero cost. ``metered_rows == 0`` with ``runs > 0`` is today's normal:
    free tiers, cost unmetered, threshold dormant.
    """
    day_start = f"{now[:10]}T00:00:00Z"
    dispatch_traces: set[str] = set()
    # Two floats per trace, never the rows themselves. The rows are the biggest objects in
    # this ledger and retaining them made peak memory a function of how much OTHER work ran
    # today — on a host already living in swap, the wrong thing to scale with.
    cost_by_trace: dict[str, float] = {}
    metered_by_trace: dict[str, int] = {}
    # KINDS is the prescreen: 97.6% of the active ledger is `crypto_cycle`, and decoding it to
    # throw it away was the whole cost of this fire (measured 2026-08-31: 0.17 s of the 0.20 s).
    for row in store.iter_records_with_archive(appended_since=day_start, kinds=SCANNED_KINDS):
        record = row.get("record")
        if not isinstance(record, Mapping):
            continue
        kind = row.get("kind")
        trace = row.get("trace_id")
        if not isinstance(trace, str):
            continue
        if kind == "task":
            requester = (record.get("source") or {}).get("requester") or {}
            received = (record.get("request") or {}).get("received_at")
            if (requester.get("requester_id") == ASSISTANT_REQUESTER
                    and isinstance(received, str) and received >= day_start):
                dispatch_traces.add(trace)
        elif kind == "budget_usage":
            usage = record.get("usage") or {}
            if str(usage.get("cost_currency", "USD")) != "USD":
                # A non-USD row would make this sum a lie; no such row exists today
                # (cost_currency is pinned "USD" everywhere) and one appearing should
                # surface as a loud number, not a silent unit mix.
                continue
            amount = float(usage.get("cost_used") or 0.0)
            cost_by_trace[trace] = cost_by_trace.get(trace, 0.0) + amount
            if amount:
                metered_by_trace[trace] = metered_by_trace.get(trace, 0) + 1

    # Summed after the scan, not during it: a usage row may be appended before or after the
    # task row it belongs to, and neither order should change the answer.
    cost_usd = sum(cost_by_trace.get(trace, 0.0) for trace in dispatch_traces)
    metered_rows = sum(metered_by_trace.get(trace, 0) for trace in dispatch_traces)

    return {
        "day": now[:10],
        "runs": len(dispatch_traces),
        "cost_usd": round(cost_usd, 6),
        "metered_rows": metered_rows,
        "threshold_usd": THRESHOLD_USD,
    }


def over_threshold(measured: Mapping[str, Any]) -> bool:
    return float(measured.get("cost_usd") or 0.0) > float(measured.get("threshold_usd") or THRESHOLD_USD)


def status_line(measured: Mapping[str, Any]) -> str:
    """The quiet fire's one line — honest about what was and was not measured."""
    qualifier = "" if measured.get("metered_rows") else " (unmetered free tier — threshold dormant)"
    return (f"dispatch_spend day={measured.get('day')} runs={measured.get('runs')} "
            f"cost_usd={measured.get('cost_usd'):.2f}/{measured.get('threshold_usd'):.0f}{qualifier}")

"""C6 feedback analytics — outcome summary, performance report, rendered digest.

Ports the review-only core of the source system's feedback stage
(``outcome_analytics_v2.summarize_outcomes``, the performance-report status/
recommendation/eligibility rules, and the R-distribution) over this port's R-based
outcome records. Like every source feedback module: **this reports, it never acts** —
a recommendation is a string for Thomas, not a state change, and acting on one stays
a human decision (in this runtime: the R9 approval door, C8).

Fields the source averaged from execution telemetry this port does not produce
(slippage, latency, rejection/stale/API-error rates, reconciliation mismatches) are
dropped rather than reported as constant zeros — a zero error rate that means "not
measured" would read as "no errors". The independent-event rule survives the port:
consecutive-cycle re-entries of one setup land minutes apart, so closed-outcome count
inflates with scheduler uptime; eligibility requires enough INDEPENDENT trade events
(cluster gap 120 minutes, the source constant), not just enough rows.

Delivery rides existing paths (the contract's C6 rule): the rendered digest is plain
text for the R4 Telegram channel and the R8 workspace write — this module produces
report + text; the pipeline wiring that sends them is C7.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from runtime.read_only_kernel import integrity

from .. import timeutil
from . import paper
from ..coerce import as_float as _f
from .cost import FUNDING_INTERVALS_PER_DAY, CostModel, funding_cost_r, outcome_net_r
from .live_pnl import R_BASES_NET_OF_COSTS
from .market_data import TIMEFRAMES

PERFORMANCE_REPORT_VERSION = "performance_report.v1-mvp"

# --- what the paper record measures, and what Gate 0 needs it to ------------------------------
#
# Paper `result_R` is measured on INTENDED fills and carries no costs. That is deliberate and
# stays that way: `paper.settle_trade_plan` is the same code the factory replays, the risk
# guard's thresholds (`guards.DAILY_MAX_LOSS_R` and friends) are calibrated on it, and the
# imported crypto_AI_System history is gross too. Re-costing the durable store would move all
# three at once and rewrite evidence that is meant to be append-only.
#
# What could not stay is the READING. `live_candidate_eligible` — the machine-readable Gate 0
# flag, and the operator checklist's first line ("paper trading by this runtime shows positive
# expectancy over a sustained window") — turned on `expectancy > 0` computed on that cost-free
# figure. So the gate that authorizes real money was answering a question about a venue that
# charges nothing: no fee, no spread, and (since the funding work) no carry. The costed number
# is strictly smaller, so every error ran toward permitting.
#
# The fix is a second figure, derived at the door rather than written into the store: the same
# `cost.apply_cost_model` the factory scores with, applied to each row's recorded fills. Gross
# stays exactly where it was and keeps its consumers; net is what eligibility reads.
NET_BASIS = "net_of_fees_slippage_and_funding"
# A row that cannot be re-costed — no risk denominator, no prices, an unparseable direction.
# Counted and named, never silently dropped and never silently treated as free: an outcome the
# cost model cannot judge is one the eligibility flag must not count as evidence.
UNCOSTABLE = "OUTCOME_NOT_COSTABLE"

# Source constant: re-entries of the same setup in consecutive scheduler cycles land
# minutes apart; genuinely new setups arrive hours later. Two hours separates them.
TRADE_EVENT_MERGE_GAP_MINUTES = 120

MIN_SAMPLE_SIZE = 3  # source default

# The sample Gate 0 needs before it may call a pool a live candidate, and it is deliberately
# NOT the source default above.
#
# `MIN_SAMPLE_SIZE = 3` was harmless while the report covered the whole own-paper record: with
# 86 rows behind it, three more could not move the verdict. Scoping the report to the routable
# pool (which is the fix that makes retired strategies stop gating live) makes it the BINDING
# constraint instead — measured, three profitable trades from a freshly promoted pool would
# take `live_candidate_eligible` from False to True and open a real-money door. Three trades on
# a book that runs about -0.5R/trade net is noise, not evidence, so scoping the population
# without moving this number would have been a relaxation wearing a defect fix's clothes.
#
# Reused, never invented: it is `lifecycle`'s own lowest rung, the window that repo already
# treats as "enough closed trades to judge a strategy's record" before it will even WARN one.
# Gate 0 asks a strictly larger question — may this pool touch real money — so borrowing the
# cheapest bar the ladder will act on is the floor, not the target. `robustness`'s
# HEALTHY_TRADES_PER_PARAMETER (10, "under ~5 it is noise") sits below it and agrees.
LIVE_CANDIDATE_MIN_SAMPLE = 20

# Statuses / recommendations (source vocabulary, review-only).
STATUS_BLOCKED_NO_OUTCOMES = "PERFORMANCE_REPORT_BLOCKED_NO_OUTCOMES"
STATUS_INSUFFICIENT_SAMPLE = "PERFORMANCE_REPORT_REVIEW_ONLY_INSUFFICIENT_SAMPLE"
STATUS_RECORDED = "PERFORMANCE_REPORT_RECORDED"

RECOMMEND_EXPAND_TEST_COVERAGE = "EXPAND_TEST_COVERAGE"
RECOMMEND_REPEAT_IN_PAPER = "REPEAT_IN_PAPER"
RECOMMEND_DROP_CANDIDATE_PROFILE = "DROP_CANDIDATE_PROFILE"
RECOMMEND_CREATE_CANDIDATE_PROFILE_DRAFT = "CREATE_CANDIDATE_PROFILE_DRAFT"


def summarize_outcomes(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Core outcome metrics over R (source math verbatim; drawdown is peak-to-trough
    of cumulative R, reported positive). ``by_strategy`` is this port's analog of the
    source's per-signal summary — the axis C8's generation work needs."""
    rows = [dict(r) for r in records if isinstance(r, Mapping)]
    closed = [r for r in rows if r.get("outcome_closed") is True]
    result_rs = [_f(r.get("result_R")) for r in closed]
    wins = [v for v in result_rs if v > 0]
    losses = [v for v in result_rs if v < 0]
    win_count = len(wins)
    loss_count = len(losses)
    expectancy = sum(result_rs) / len(result_rs) if result_rs else 0.0
    # Realized payoff legs (M4a): the average winning R and the average losing R as a
    # positive magnitude, so avg_win_R / avg_loss_R is the realized reward:risk. Kept
    # separate from expectancy — the ranking wants win-rate and payoff as two axes.
    avg_win_r = sum(wins) / win_count if win_count else 0.0
    avg_loss_r = -sum(losses) / loss_count if loss_count else 0.0
    cumulative = peak = max_dd = 0.0
    for value in result_rs:
        cumulative += value
        peak = max(peak, cumulative)
        max_dd = max(max_dd, peak - cumulative)

    by_strategy: dict[str, dict[str, Any]] = {}
    for row in closed:
        key = str(row.get("strategy_id") or "unattributed")
        bucket = by_strategy.setdefault(key, {"closed_count": 0, "win_count": 0, "loss_count": 0, "_sum": 0.0})
        value = _f(row.get("result_R"))
        bucket["closed_count"] += 1
        bucket["_sum"] += value
        bucket["win_count"] += 1 if value > 0 else 0
        bucket["loss_count"] += 1 if value < 0 else 0
    for bucket in by_strategy.values():
        bucket["expectancy"] = round(bucket.pop("_sum") / bucket["closed_count"], 8)

    return {
        "outcome_count": len(rows),
        "closed_count": len(closed),
        "win_count": win_count,
        "loss_count": loss_count,
        "breakeven_count": sum(1 for v in result_rs if v == 0),
        "expectancy": round(expectancy, 8),
        "win_loss_ratio": round(win_count / loss_count, 8) if loss_count else float(win_count),
        "average_R": round(expectancy, 8),
        "avg_win_R": round(avg_win_r, 8),
        "avg_loss_R": round(avg_loss_r, 8),
        "max_drawdown": round(max_dd, 8),
        "by_strategy": by_strategy,
    }


def r_distribution(records: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """The source's R histogram over closed outcomes."""
    values = [_f(r.get("result_R")) for r in records if isinstance(r, Mapping) and r.get("outcome_closed") is True]
    return {
        "lt_minus_1R": sum(1 for v in values if v < -1.0),
        "minus_1R_to_0R": sum(1 for v in values if -1.0 <= v < 0.0),
        "zero_R": sum(1 for v in values if v == 0.0),
        "zero_to_1R": sum(1 for v in values if 0.0 < v < 1.0),
        "one_to_2R": sum(1 for v in values if 1.0 <= v < 2.0),
        "gte_2R": sum(1 for v in values if v >= 2.0),
    }


def count_independent_trade_events(records: Iterable[Mapping[str, Any]]) -> int:
    """Cluster closed outcomes into independent events (source rule, ported to the
    strategy axis): outcomes of the same strategy closing within the merge gap are
    one event; a different strategy, or a gap beyond it, starts a new one."""
    closed = sorted(
        (r for r in records if isinstance(r, Mapping) and r.get("outcome_closed") is True),
        key=lambda r: str(r.get("created_at_utc") or ""),
    )
    events = 0
    last_strategy: str | None = None
    last_time = None
    for row in closed:
        raw = row.get("created_at_utc")
        try:
            moment = timeutil.parse_iso(raw) if isinstance(raw, str) else None
        except ValueError:
            moment = None
        strategy = str(row.get("strategy_id") or "unattributed")
        same_cluster = (
            moment is not None
            and last_time is not None
            and strategy == last_strategy
            and (moment - last_time).total_seconds() / 60.0 <= TRADE_EVENT_MERGE_GAP_MINUTES
        )
        if not same_cluster:
            events += 1
        last_strategy = strategy
        last_time = moment if moment is not None else last_time
    return events


def _funding_intervals(outcome: Mapping[str, Any]) -> float:
    """How many 8h settlements this position was open across, from its bars and timeframe.

    Paper does not record wall-clock duration, but it records `holding_candles` and the
    `timeframe` those candles are in, and that product IS the duration. A row missing either
    is charged nothing on this axis rather than a guess — the fee legs still apply, and the
    resulting figure is stated as the optimistic side of the honest one."""
    bars = _f(outcome.get("holding_candles"))
    minutes = TIMEFRAMES.get(str(outcome.get("timeframe") or ""))
    if bars <= 0 or not minutes:
        return 0.0
    return (bars * minutes / 1440.0) * FUNDING_INTERVALS_PER_DAY


def net_result_r(
    outcome: Mapping[str, Any], *, cost: CostModel | None = None
) -> float | None:
    """One paper outcome re-read NET of fees, slippage and carry — or None if it cannot be.

    **This adds carry and delegates the rest.** `cost.outcome_net_r` owns the conversion — the
    basis rule (a row whose costs are already inside it must not be charged again), the risk
    denominator, and the cost model itself. This function used to re-implement all three, and
    the two copies had already drifted: only one of them knew about `intent_net_of_costs`, so
    the ladder and the board would have disagreed about the same row from the first settlement
    after 2026-08-03. One concept, one owner; what is genuinely this module's is the carry.

    Carry belongs here rather than there because deriving it needs `holding_candles × timeframe`
    and therefore `market_data.TIMEFRAMES`, which `cost` deliberately does not import — it is a
    constants-and-arithmetic module with no I/O at import. It is charged at the model's base
    rate rather than the venue's realized settlements: unlike the backtest, a settled paper row
    has no funding series attached, and refetching one per row at report time would put a
    network read behind a board that must render offline."""
    cost = cost or CostModel()
    carry = _funding_intervals(outcome) * cost.funding_bps_per_interval / 10000.0

    # **A row that is ALREADY net is costed here, not reported as uncostable.**
    #
    # `cost.outcome_net_r` returns None for these deliberately, so a settlement that already
    # charged fees and slippage is never charged them twice. But None means two different
    # things to a caller — "this row cannot be priced" and "this row is already priced" — and
    # `summarize_net_of_costs` was reading both as the first. Every row this runtime has minted
    # since 2026-07-30 labels itself `intent_net_of_costs`, so the effect was that the whole
    # forward record counted as uncostable: measured 2026-08-01 on the first settlement of the
    # newly promoted pool, a +1.778R take-profit produced `costed_count: 0` and
    # `OUTCOME_NOT_COSTABLE`. Gate 0 requires `not failure_modes`, so that mode alone would have
    # held it shut **however well the pool traded** — the third time this exact latch has
    # appeared, and the first time on the runtime's own current rows.
    #
    # What the two bases each contain has to be exact, or this trades a latch for a wrong
    # number. `paper.build_outcome_record` charges fees and slippage at settlement and records
    # no funding term, and `outcome_net_r` says the same in its own words: `intent_net_of_costs`
    # is "fees and slippage both inside". Carry is therefore the one term still owed, which is
    # also the term this function already owns (see the docstring above). So the net figure is
    # `result_R` minus carry, charged on the ENTRY FILL exactly as `apply_cost_model` charges
    # it — the same call, not a second copy of the arithmetic.
    if outcome.get("r_basis") in R_BASES_NET_OF_COSTS:
        result_r = outcome.get("result_R")
        if isinstance(result_r, bool) or not isinstance(result_r, (int, float)):
            return None
        if not carry:
            return round(float(result_r), 8)
        entry = outcome.get("entry_price")
        risk = outcome.get("risk")
        direction = outcome.get("direction")
        if (
            not isinstance(entry, (int, float)) or isinstance(entry, bool) or entry <= 0
            or not isinstance(risk, (int, float)) or isinstance(risk, bool) or risk <= 0
            or direction not in ("LONG", "SHORT")
        ):
            # Carry is owed and cannot be priced, so the row genuinely is uncostable. Returning
            # `result_R` here would report a figure that silently omits a cost this function
            # exists to charge.
            return None
        entry_fill = cost.fill_price(entry, direction, "entry")
        return round(float(result_r) - funding_cost_r(direction, entry_fill, risk, carry), 8)

    return outcome_net_r(outcome, cost=cost, funding_rate_sum=carry)


def summarize_net_of_costs(
    records: Iterable[Mapping[str, Any]], *, cost: CostModel | None = None
) -> dict[str, Any]:
    """The costed twin of :func:`summarize_outcomes` — what this book would have earned at the
    venue it actually trades at.

    ``costed_count`` and ``uncostable_count`` are reported beside the figures on purpose: an
    expectancy over a subset of the rows is a different claim from one over all of them, and
    the gate below refuses to run on a sample it could only partly price."""
    rows = [dict(r) for r in records if isinstance(r, Mapping)]
    closed = [r for r in rows if r.get("outcome_closed") is True]
    net = [(r, net_result_r(r, cost=cost)) for r in closed]
    costed = [value for _row, value in net if value is not None]
    uncostable = [row for row, value in net if value is None]
    wins = [v for v in costed if v > 0]
    losses = [v for v in costed if v < 0]
    return {
        "basis": NET_BASIS,
        "closed_count": len(closed),
        "costed_count": len(costed),
        "uncostable_count": len(uncostable),
        "total_R": round(sum(costed), 8),
        "expectancy": round(sum(costed) / len(costed), 8) if costed else 0.0,
        "win_count": len(wins),
        "loss_count": len(losses),
        "cost_model": {
            "taker_fee_bps": (cost or CostModel()).taker_fee_bps,
            "maker_fee_bps": (cost or CostModel()).maker_fee_bps,
            "slippage_bps": (cost or CostModel()).slippage_bps,
            "funding_bps_per_interval": (cost or CostModel()).funding_bps_per_interval,
        },
    }


def _status_and_recommendation(
    summary: Mapping[str, Any], *, has_rows: bool, independent_event_count: int, min_sample_size: int
) -> tuple[str, str, list[str]]:
    if not has_rows:
        return STATUS_BLOCKED_NO_OUTCOMES, RECOMMEND_EXPAND_TEST_COVERAGE, ["NO_OUTCOME_RECORDS"]
    blockers: list[str] = []
    closed_count = int(_f(summary.get("closed_count")))
    if closed_count < min_sample_size:
        blockers.append("INSUFFICIENT_CLOSED_OUTCOME_SAMPLE")
        return STATUS_INSUFFICIENT_SAMPLE, RECOMMEND_REPEAT_IN_PAPER, blockers
    if independent_event_count < min_sample_size:
        blockers.append("INSUFFICIENT_INDEPENDENT_TRADE_EVENTS")
        return STATUS_INSUFFICIENT_SAMPLE, RECOMMEND_REPEAT_IN_PAPER, blockers
    expectancy = _f(summary.get("expectancy"))
    if expectancy < 0:
        return STATUS_RECORDED, RECOMMEND_DROP_CANDIDATE_PROFILE, blockers
    if expectancy == 0:
        return STATUS_RECORDED, RECOMMEND_REPEAT_IN_PAPER, blockers
    return STATUS_RECORDED, RECOMMEND_CREATE_CANDIDATE_PROFILE_DRAFT, blockers


def _failure_modes(summary: Mapping[str, Any], *, has_rows: bool) -> list[str]:
    if not has_rows:
        return ["NO_OUTCOME_RECORDS"]
    modes: list[str] = []
    if _f(summary.get("expectancy")) < 0:
        modes.append("NEGATIVE_EXPECTANCY")
    if int(_f(summary.get("closed_count"))) == 0:
        modes.append("NO_CLOSED_OUTCOMES")
    return modes


def _net_failure_modes(net: Mapping[str, Any]) -> list[str]:
    """What the costed reading adds to the list of reasons not to go live.

    Named separately from the gross ones because the interesting case is the DISAGREEMENT: a
    book that is positive before costs and negative after has an edge smaller than its own
    frictions, and that is a different diagnosis from "no edge" — it says the entry rule works
    and the holding period does not."""
    modes: list[str] = []
    if int(_f(net.get("closed_count"))) == 0:
        return modes  # the gross side already says NO_CLOSED_OUTCOMES
    if _f(net.get("expectancy")) <= 0:
        modes.append("NEGATIVE_EXPECTANCY_NET_OF_COSTS")
    if int(_f(net.get("uncostable_count"))) > 0:
        # Not fatal on its own — it names how much of the sample the figure covers, so a reader
        # is never shown an expectancy over a subset without being told it is one.
        modes.append(UNCOSTABLE)
    return modes


def build_performance_report(
    outcomes: Iterable[Mapping[str, Any]], *, now: str, min_sample_size: int = MIN_SAMPLE_SIZE
) -> dict[str, Any]:
    """The review-only performance report over all outcomes. Deterministic for a
    given (outcomes, now); the id is seeded from the source outcome ids."""
    rows = [dict(r) for r in outcomes if isinstance(r, Mapping)]
    summary = summarize_outcomes(rows)
    net = summarize_net_of_costs(rows)
    independent_event_count = count_independent_trade_events(rows)
    status, recommendation, blockers = _status_and_recommendation(
        summary, has_rows=bool(rows), independent_event_count=independent_event_count,
        min_sample_size=min_sample_size,
    )
    failure_modes = sorted(dict.fromkeys([
        *blockers, *_failure_modes(summary, has_rows=bool(rows)), *_net_failure_modes(net),
    ]))
    live_candidate_eligible = (
        status == STATUS_RECORDED
        and recommendation == RECOMMEND_CREATE_CANDIDATE_PROFILE_DRAFT
        and not failure_modes
        and int(_f(summary.get("closed_count"))) >= min_sample_size
        and independent_event_count >= min_sample_size
        # The costed figure, and this is the whole point of it. This flag is the machine-readable
        # half of the operator checklist's Gate 0, and it used to turn on gross expectancy — a
        # venue with no fee, no spread and no carry. Every disagreement between the two runs one
        # way, because costs only ever subtract.
        and _f(net.get("expectancy")) > 0
        and int(_f(net.get("costed_count"))) >= min_sample_size
    )
    source_ids = sorted(str(r.get("outcome_id")) for r in rows if r.get("outcome_id"))
    report = {
        "performance_report_version": PERFORMANCE_REPORT_VERSION,
        "status": status,
        "recommendation": recommendation,
        "sample_size": int(_f(summary.get("closed_count"))),
        "independent_event_count": independent_event_count,
        "summary": summary,
        # The same rows at the venue's rates. Beside `summary` rather than replacing it: gross
        # is what `paper.settle_trade_plan` measured and what the risk guard is calibrated on,
        # net is what a decision about real money has to read.
        "net_summary": net,
        "r_distribution": r_distribution(rows),
        "failure_modes": failure_modes,
        "live_candidate_eligible": live_candidate_eligible,
        "source_outcome_ids": source_ids,
        "created_at_utc": now,
        # Review-only, verbatim from every source feedback module:
        "live_trading_allowed_by_this_module": False,
        "runtime_settings_mutated_by_this_module": False,
    }
    report["performance_report_id"] = integrity.short_id(
        "performance_report", {"version": PERFORMANCE_REPORT_VERSION, "sources": source_ids, "created_at": now}
    )
    return report


def render_report_text(report: Mapping[str, Any]) -> str:
    """Plain-text digest for the R4 Telegram channel / R8 workspace write."""
    summary = report.get("summary") or {}
    lines = [
        "=== paper performance report ===",
        f"status          : {report.get('status')}",
        f"recommendation  : {report.get('recommendation')}",
        f"sample (closed) : {report.get('sample_size')} ({report.get('independent_event_count')} independent events)",
        f"expectancy      : {summary.get('expectancy')} R/trade  (GROSS — intended fills, no costs)",
    ]
    # The costed line goes directly under the gross one, never at the bottom: these two answer
    # the same question about different venues, and the one that matters for real money is the
    # second. A reader who stops after the first must at least have seen them adjacent.
    net = report.get("net_summary") or {}
    net_line = f"expectancy (net): {net.get('expectancy')} R/trade  (fees + slippage + funding)"
    if int(_f(net.get("uncostable_count"))) > 0:
        net_line += f"  [over {net.get('costed_count')}/{net.get('closed_count')} costable rows]"
    lines.append(net_line)
    lines += [
        f"win/loss        : {summary.get('win_count')}W {summary.get('loss_count')}L (ratio {summary.get('win_loss_ratio')})",
        f"max drawdown    : {summary.get('max_drawdown')}R",
        f"live candidate  : {'eligible' if report.get('live_candidate_eligible') else 'not eligible'}"
        f"  (judged on the NET figure)",
    ]
    modes = report.get("failure_modes") or []
    if modes:
        lines.append(f"failure modes   : {', '.join(modes)}")
    by_strategy = summary.get("by_strategy") or {}
    if by_strategy:
        lines.append("-- by strategy --")
        for strategy_id in sorted(by_strategy):
            b = by_strategy[strategy_id]
            lines.append(
                f"{strategy_id:16}: {b['closed_count']} closed, expectancy {b['expectancy']}, "
                f"{b['win_count']}W {b['loss_count']}L"
            )
    lines.append(f"report id       : {report.get('performance_report_id')}")
    return "\n".join(lines)


def _routable_rows(
    rows: list[Mapping[str, Any]], routable: set[str] | None
) -> list[Mapping[str, Any]]:
    """The rows Gate 0 may judge: those produced by a lineage that can still route.

    **Why the scoping exists.** Gate 0 asks whether the paper record shows an edge, and the
    answer decides whether real money may start. Measured 2026-08-01 on this machine: all **86**
    rows holding it shut came from lineages the `lifecycle` ladder had already SUSPENDED, and
    **zero** came from one that can still route. The ladder had done exactly its job — judge a
    strategy on its paper losses and retire it — and the losses of strategies that no longer
    exist were still gating the live door.

    That is the same defect #405 names for the drawdown, by the same mechanism: on a system built
    to rotate strategies, an all-time figure eventually measures elapsed time rather than
    performance. Every retirement leaves permanent dead weight in the mean, so a pool that
    improved by exactly the route the design intends could never clear the gate it has to clear.

    **The direction this fails in.** ``routable=None`` means the caller could not read the pool,
    and it scopes to NOTHING — the report then has no rows, and Gate 0 refuses on
    ``BLOCKED_NO_OUTCOMES`` rather than on a population nobody could establish. That is the
    opposite of `guards.drawdown_baseline`'s `None`, and deliberately: there, unknown-routability
    must KEEP losses in a brake; here it must WITHHOLD an eligibility claim. Both refuse.

    **What this does not do.** It does not change how large a sample Gate 0 needs —
    ``MIN_SAMPLE_SIZE`` is untouched and raising it is #400 §7's own separate decision. Scoping
    the population and moving the threshold are two changes, and only the first is a defect fix.
    Retiring a loser can now raise the surviving mean, which is the intended behaviour of a
    filter rather than a loophole: the question Gate 0 asks is whether the pool that would trade
    shows an edge, and a retired strategy is not that pool.
    """
    if routable is None:
        return []
    return [r for r in rows if str(r.get("strategy_id") or "") in routable]


def run_paper_performance_report(
    *,
    now: str,
    root=None,
    outcomes: Iterable[Mapping[str, Any]] | None = None,
    # No default, for the reason `plan_live_entry`'s `verdict` has none: an optional scope is a
    # scope the one caller that forgets it never applies, and the branch nobody tests is then
    # the unscoped one. `None` is a legitimate value here — "the pool could not be read" — and
    # it refuses; omitting the argument is a TypeError.
    routable_strategy_ids: set[str] | None,
) -> tuple[dict[str, Any], str]:
    """Read the paper outcome store and produce (report, rendered_text).

    An unreadable store propagates as the typed ``OUTCOME_HISTORY_UNREADABLE`` —
    a report over a silently-truncated history would be a lie with a status field.

    ``outcomes`` lets a caller that has **already** performed that verified read hand it over
    instead of paying for a second one. `read_outcomes` re-parses every row and recomputes every
    native record's sha256: 98 ms over 3,000 rows, twice per cycle, times twenty contexts in a
    pool fan-out — 3.9 s a fire, growing with the history for as long as the runtime trades.

    Deliberately an argument rather than a cache keyed on the file's mtime and size. This store
    is the one the risk guard reads to decide whether to keep trading, and its tamper evidence is
    the per-record hash; a freshness heuristic that decides when to skip re-checking those hashes
    is a weakening of a fail-closed path in exchange for milliseconds. An explicit hand-off from
    the one caller that already read is worth the same and gives up nothing. Passing ``None``
    reads, exactly as before — including the raise, which is why `cycle` passes ``None`` when its
    own read failed.

    **The report covers this runtime's OWN paper rows only** — the same `split_by_provenance`
    line the risk guard draws, and the one `dashboard` already drew by calling
    :func:`build_performance_report` directly. It used to cover the whole store, which was wrong
    in two ways at once:

    - **The question.** ``live_candidate_eligible`` is Gate 0 — *does THIS runtime's paper record
      show an edge* — and the history imported from the frozen crypto_AI_System was produced by
      different code. It is real, and it cannot answer a question about this runtime. `cycle`
      already makes exactly this argument where it splits the same rows before the breakers see
      them; the report was the one consumer that had not.
    - **The arithmetic, which is worse.** Imported rows carry no cost basis, so
      `summarize_net_of_costs` cannot cost them and `_net_failure_modes` raises
      ``OUTCOME_NOT_COSTABLE`` — permanently, because those rows never become costable. Gate 0
      requires ``not failure_modes``, so over the whole store it reads **False however well this
      runtime trades**: measured 2026-07-31, 114 imported rows against 86 own ones held that mode
      up on every cycle. A gate that cannot open is not a gate, and wiring one would have been
      indistinguishable from wiring a working gate right up until the moment it should have
      opened.

    Net expectancy is unchanged by the split, because uncostable rows never entered it
    (-0.50498579 R either way on that measurement). What changes is that the failure mode — and
    therefore the verdict — now describes the runtime being judged."""
    if outcomes is None:
        outcomes = paper.read_outcomes(root)  # raises ToolError when unreadable
    own, _imported = paper.split_by_provenance(outcomes)
    own = _routable_rows(own, routable_strategy_ids)
    report = build_performance_report(own, now=now, min_sample_size=LIVE_CANDIDATE_MIN_SAMPLE)
    return report, render_report_text(report)


__all__ = [
    "NET_BASIS",
    "UNCOSTABLE",
    "build_performance_report",
    "count_independent_trade_events",
    "net_result_r",
    "r_distribution",
    "render_report_text",
    "run_paper_performance_report",
    "summarize_net_of_costs",
    "summarize_outcomes",
]

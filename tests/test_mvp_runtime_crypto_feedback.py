"""C6 feedback analytics tests — summary math, independence rule, report semantics.

The source rules under test: expectancy/drawdown math, the independent-event gate
(closed-count inflates with scheduler uptime; eligibility needs independent events),
review-only recommendations (negative expectancy → drop, positive → candidate draft),
and the report's honesty rules (unreadable history raises; review-only flags false)."""

from __future__ import annotations

import json

import pytest

from runtime.read_only_kernel import integrity

from runtime.mvp_runtime.crypto import paper
from runtime.mvp_runtime.crypto import feedback
from runtime.mvp_runtime.crypto.feedback import (
    LIVE_CANDIDATE_MIN_SAMPLE,
    RECOMMEND_CREATE_CANDIDATE_PROFILE_DRAFT,
    RECOMMEND_DROP_CANDIDATE_PROFILE,
    RECOMMEND_EXPAND_TEST_COVERAGE,
    RECOMMEND_REPEAT_IN_PAPER,
    STATUS_BLOCKED_NO_OUTCOMES,
    STATUS_INSUFFICIENT_SAMPLE,
    STATUS_RECORDED,
    build_performance_report,
    count_independent_trade_events,
    net_result_r,
    r_distribution,
    render_report_text,
    run_paper_performance_report,
    summarize_net_of_costs,
    summarize_outcomes,
)
from runtime.mvp_runtime.errors import ToolError

NOW = "2026-07-22T12:00:00Z"


def _outcome(result_r, closed_at, *, strategy_id="S1", closed=True, outcome_id=None,
             priced=True, holding_candles=4, timeframe="1d",
             provenance=paper.PAPER_PROVENANCE):
    """One settled paper outcome.

    ``provenance`` defaults to this runtime's own marker because that is what a real row
    carries, and `run_paper_performance_report` now reports over own rows only — a fixture
    without it would be silently reporting on the imported population.

    ``priced`` controls whether the row carries the fills a cost model needs. Real records have
    carried them since C5 and the risk denominator since 2026-07-29; ``priced=False`` stands in
    for the imported crypto_AI_System history and for pre-`risk` rows, which the net figure
    reports as uncostable rather than pricing on a guess."""
    record = {
        "outcome_id": outcome_id or f"out_{strategy_id}_{closed_at}",
        "result_R": result_r,
        "outcome_closed": closed,
        "created_at_utc": closed_at,
        "strategy_id": strategy_id,
        "provenance": provenance,
    }
    if priced:
        risk = 4.0
        record.update({
            "direction": "LONG", "entry_price": 100.0, "risk": risk,
            "exit_price": 100.0 + result_r * risk,
            "close_reason": "take_profit" if result_r > 0 else "stop_loss",
            "holding_candles": holding_candles, "timeframe": timeframe,
        })
    return record


# Hours apart -> each is an independent event.
SPREAD = [
    _outcome(2.0, "2026-07-18T00:00:00Z"),
    _outcome(-1.0, "2026-07-19T00:00:00Z"),
    _outcome(1.5, "2026-07-20T00:00:00Z"),
    _outcome(-1.0, "2026-07-21T00:00:00Z"),
]


# --- summary math -------------------------------------------------------------

def test_summary_math():
    summary = summarize_outcomes(SPREAD)
    assert summary["closed_count"] == 4
    assert summary["win_count"] == 2 and summary["loss_count"] == 2
    assert summary["expectancy"] == round((2.0 - 1.0 + 1.5 - 1.0) / 4, 8)
    assert summary["win_loss_ratio"] == 1.0
    # M4a realized payoff legs: avg win (2.0, 1.5)=1.75, avg loss magnitude (1.0,1.0)=1.0.
    assert summary["avg_win_R"] == 1.75 and summary["avg_loss_R"] == 1.0
    # Equity path: 2, 1, 2.5, 1.5 -> deepest fall from a peak is 1.0R.
    assert summary["max_drawdown"] == 1.0


def test_avg_win_loss_R_degenerate_cases():
    # No losses: avg_loss_R is 0.0 (the ranking reads this as an undefined ratio).
    all_wins = summarize_outcomes([_outcome(1.0, NOW), _outcome(2.0, NOW)])
    assert all_wins["avg_win_R"] == 1.5 and all_wins["avg_loss_R"] == 0.0
    # No wins: avg_win_R is 0.0, avg_loss_R the positive mean magnitude.
    all_losses = summarize_outcomes([_outcome(-1.0, NOW), _outcome(-2.0, NOW)])
    assert all_losses["avg_win_R"] == 0.0 and all_losses["avg_loss_R"] == 1.5


def test_summary_empty_and_open_rows():
    assert summarize_outcomes([])["closed_count"] == 0
    summary = summarize_outcomes([_outcome(-5.0, NOW, closed=False)])
    assert summary["outcome_count"] == 1 and summary["closed_count"] == 0


def test_win_loss_ratio_with_zero_losses_is_win_count():
    summary = summarize_outcomes([_outcome(1.0, NOW), _outcome(2.0, NOW)])
    assert summary["win_loss_ratio"] == 2.0


def test_by_strategy_breakdown():
    rows = SPREAD + [_outcome(3.0, "2026-07-21T12:00:00Z", strategy_id="S2")]
    by_strategy = summarize_outcomes(rows)["by_strategy"]
    assert by_strategy["S1"]["closed_count"] == 4
    assert by_strategy["S2"] == {"closed_count": 1, "win_count": 1, "loss_count": 0, "expectancy": 3.0}


def test_r_distribution_buckets():
    rows = [_outcome(v, NOW) for v in (-1.5, -0.5, 0.0, 0.5, 1.5, 2.5)]
    assert r_distribution(rows) == {
        "lt_minus_1R": 1, "minus_1R_to_0R": 1, "zero_R": 1,
        "zero_to_1R": 1, "one_to_2R": 1, "gte_2R": 1,
    }


# --- independent events -------------------------------------------------------

def test_consecutive_cycle_reentries_are_one_event():
    rows = [_outcome(1.0, f"2026-07-22T10:{m:02d}:00Z") for m in (0, 15, 30)]
    assert count_independent_trade_events(rows) == 1


def test_gap_beyond_merge_window_splits_events():
    rows = [_outcome(1.0, "2026-07-22T10:00:00Z"), _outcome(1.0, "2026-07-22T13:00:00Z")]
    assert count_independent_trade_events(rows) == 2


def test_different_strategies_are_different_events():
    rows = [
        _outcome(1.0, "2026-07-22T10:00:00Z", strategy_id="S1"),
        _outcome(1.0, "2026-07-22T10:05:00Z", strategy_id="S2"),
    ]
    assert count_independent_trade_events(rows) == 2


# --- report semantics ---------------------------------------------------------

def test_no_outcomes_blocks_report():
    report = build_performance_report([], now=NOW)
    assert report["status"] == STATUS_BLOCKED_NO_OUTCOMES
    assert report["recommendation"] == RECOMMEND_EXPAND_TEST_COVERAGE
    assert report["live_candidate_eligible"] is False


def test_insufficient_closed_sample():
    report = build_performance_report(SPREAD[:2], now=NOW)
    assert report["status"] == STATUS_INSUFFICIENT_SAMPLE
    assert report["recommendation"] == RECOMMEND_REPEAT_IN_PAPER


def test_uptime_inflated_sample_is_still_insufficient():
    # 5 closed outcomes minutes apart = 1 independent event: the closed count alone
    # must not unlock eligibility (the source's scheduler-uptime rule).
    rows = [_outcome(1.0, f"2026-07-22T10:{m:02d}:00Z") for m in (0, 10, 20, 30, 40)]
    report = build_performance_report(rows, now=NOW)
    assert report["sample_size"] == 5 and report["independent_event_count"] == 1
    assert report["status"] == STATUS_INSUFFICIENT_SAMPLE
    assert "INSUFFICIENT_INDEPENDENT_TRADE_EVENTS" in report["failure_modes"]


def test_negative_expectancy_recommends_drop():
    rows = [_outcome(-1.0, f"2026-07-{d:02d}T00:00:00Z") for d in (18, 19, 20, 21)]
    report = build_performance_report(rows, now=NOW)
    assert report["status"] == STATUS_RECORDED
    assert report["recommendation"] == RECOMMEND_DROP_CANDIDATE_PROFILE
    assert "NEGATIVE_EXPECTANCY" in report["failure_modes"]
    assert report["live_candidate_eligible"] is False


def test_positive_expectancy_is_candidate_eligible():
    report = build_performance_report(SPREAD, now=NOW)
    assert report["status"] == STATUS_RECORDED
    assert report["recommendation"] == RECOMMEND_CREATE_CANDIDATE_PROFILE_DRAFT
    assert report["live_candidate_eligible"] is True


def test_report_is_deterministic_and_review_only():
    a = build_performance_report(SPREAD, now=NOW)
    b = build_performance_report(SPREAD, now=NOW)
    assert a == b and a["performance_report_id"].startswith("performance_report")
    # Review-only, structurally: the report can never claim an execution right.
    assert a["live_trading_allowed_by_this_module"] is False
    assert a["runtime_settings_mutated_by_this_module"] is False


def test_render_contains_the_decision_inputs():
    report = build_performance_report(SPREAD, now=NOW)
    text = render_report_text(report)
    assert "expectancy" in text and str(report["summary"]["expectancy"]) in text
    assert report["recommendation"] in text
    assert report["performance_report_id"] in text


# --- store integration --------------------------------------------------------

def test_run_paper_performance_report_reads_store(tmp_path):
    state = paper.state_dir(tmp_path)
    state.mkdir(parents=True)
    # Self-hashed: these rows carry this runtime's own provenance now, and `read_outcomes`
    # verifies the per-record hash on exactly those — the tamper evidence that makes the
    # report's history trustworthy is the same rule that makes this fixture write one.
    lines = "".join(json.dumps({**o, "record_sha256": integrity.sha256_record(o)}) + "\n"
                    for o in SPREAD)
    (state / "paper_outcomes.jsonl").write_text(lines, encoding="utf-8")
    report, text = run_paper_performance_report(now=NOW, root=tmp_path, routable_strategy_ids={"S1"})
    assert report["sample_size"] == 4
    assert "paper performance report" in text


def test_run_paper_performance_report_fails_closed_on_corrupt_store(tmp_path):
    state = paper.state_dir(tmp_path)
    state.mkdir(parents=True)
    (state / "paper_outcomes.jsonl").write_text("{broken\n", encoding="utf-8")
    with pytest.raises(ToolError) as exc:
        run_paper_performance_report(now=NOW, root=tmp_path, routable_strategy_ids={"S1"})
    assert exc.value.reason_code == "OUTCOME_HISTORY_UNREADABLE"


# --- Gate 0 reads the costed figure (2026-07-29) -----------------------------------------------
#
# `live_candidate_eligible` is the machine-readable half of the operator checklist's first line
# — "paper trading by this runtime shows positive expectancy over a sustained window" — and it
# turned on GROSS expectancy. Paper R is measured on intended fills and carries no costs by
# design; that design is intact, and it was never the problem. The problem was the gate that
# authorized real money reading a venue with no fee, no spread and no carry.

def _priced(result_r, closed_at, **kw):
    return _outcome(result_r, closed_at, **kw)


def test_the_net_figure_is_strictly_below_the_gross_one():
    """Costs only ever subtract, so every disagreement between the two runs one way."""
    gross = summarize_outcomes(SPREAD)
    net = feedback.summarize_net_of_costs(SPREAD)
    assert net["costed_count"] == gross["closed_count"]
    assert net["expectancy"] < gross["expectancy"]
    assert net["basis"] == feedback.NET_BASIS


def test_a_book_positive_before_costs_and_negative_after_is_not_eligible():
    """The case the flag existed to catch and could not: an edge smaller than its own frictions.
    Diagnosed distinctly from "no edge" — the entry rule works, the holding period does not."""
    thin = [
        _priced(0.02, "2026-07-18T00:00:00Z", holding_candles=30),
        _priced(0.02, "2026-07-19T00:00:00Z", holding_candles=30),
        _priced(0.03, "2026-07-20T00:00:00Z", holding_candles=30),
        _priced(0.02, "2026-07-21T00:00:00Z", holding_candles=30),
    ]
    report = build_performance_report(thin, now=NOW)
    assert summarize_outcomes(thin)["expectancy"] > 0, "gross is positive — that is the premise"
    assert report["net_summary"]["expectancy"] < 0
    assert report["live_candidate_eligible"] is False
    assert "NEGATIVE_EXPECTANCY_NET_OF_COSTS" in report["failure_modes"]
    assert "NEGATIVE_EXPECTANCY" not in report["failure_modes"], "the gross book was positive"


def test_a_long_hold_costs_more_than_a_short_one():
    """Carry scales with time held, which is what the fee legs alone could never express."""
    brief = feedback.net_result_r(_priced(1.0, NOW, holding_candles=1))
    long_hold = feedback.net_result_r(_priced(1.0, NOW, holding_candles=40))
    assert long_hold < brief


def test_rows_that_cannot_be_priced_are_named_never_assumed_free():
    """The imported crypto_AI_System history and every pre-`risk` row. An outcome the cost model
    cannot judge must not be counted as evidence for a live decision."""
    mixed = [*SPREAD, _priced(1.0, "2026-07-22T00:00:00Z", priced=False)]
    net = feedback.summarize_net_of_costs(mixed)
    assert net["uncostable_count"] == 1
    assert net["costed_count"] == 4
    report = build_performance_report(mixed, now=NOW)
    assert feedback.UNCOSTABLE in report["failure_modes"]
    assert report["live_candidate_eligible"] is False


def test_an_entirely_unpriceable_history_certifies_nothing():
    """Fail-closed: a report that cannot price a single row must not certify eligibility on the
    gross figure alone, however good that figure looks."""
    unpriced = [_priced(r, at, priced=False) for r, at in (
        (2.0, "2026-07-18T00:00:00Z"), (1.5, "2026-07-19T00:00:00Z"),
        (2.0, "2026-07-20T00:00:00Z"), (1.5, "2026-07-21T00:00:00Z"))]
    report = build_performance_report(unpriced, now=NOW)
    assert report["summary"]["expectancy"] > 1.0
    assert report["net_summary"]["costed_count"] == 0
    assert report["live_candidate_eligible"] is False


def test_the_break_even_row_is_uncostable_rather_than_derived_wrong():
    """`risk` is recorded now, but an older row is recovered from `result_R = move / risk` —
    which divides by zero exactly on a flat trade, the row a cost model would push negative."""
    flat = _priced(0.0, NOW, priced=False)
    flat.update({"direction": "LONG", "entry_price": 100.0, "exit_price": 100.0})
    assert feedback.net_result_r(flat) is None


def test_a_recorded_risk_prices_a_break_even_row():
    """Which is why the field is recorded rather than always derived."""
    flat = _priced(0.0, NOW)
    assert flat["risk"] == 4.0
    net = feedback.net_result_r(flat)
    assert net is not None and net < 0.0, "a flat trade still pays its costs"


def test_the_report_text_puts_the_two_figures_next_to_each_other():
    """They answer the same question about different venues. A reader who stops after the first
    line must at least have seen them adjacent, and must know which one the flag was judged on."""
    text = render_report_text(build_performance_report(SPREAD, now=NOW))
    gross_at, net_at = text.index("expectancy      :"), text.index("expectancy (net):")
    assert net_at - gross_at < 120, "the costed line is not directly under the gross one"
    assert "GROSS" in text and "fees + slippage + funding" in text
    assert "judged on the NET figure" in text


def test_the_gross_summary_keeps_its_meaning_and_its_consumers():
    """`paper.settle_trade_plan` is the same code the factory replays, `guards` thresholds are
    calibrated on gross R, and the imported history is gross. The net figure is a derivation at
    the door, never a rewrite of an append-only store."""
    report = build_performance_report(SPREAD, now=NOW)
    assert report["summary"] == summarize_outcomes(SPREAD)
    assert all("net" not in key for key in report["summary"])


# --- the history is read once per cycle, not twice (2026-07-29) --------------------------------
#
# `read_outcomes` re-parses every row and recomputes every native record's sha256 — 98 ms over
# 3,000 rows, and it ran twice per cycle (the risk guard's read, then the report's) times twenty
# contexts in a pool fan-out. The verification is not the waste; doing it twice on an unchanged
# file is.

def test_the_report_uses_a_history_the_caller_already_verified(tmp_path, monkeypatch):
    reads = []
    real = paper.read_outcomes
    monkeypatch.setattr(paper, "read_outcomes", lambda root=None: (reads.append(1), real(root))[1])

    report, _text = run_paper_performance_report(now=NOW, root=tmp_path, outcomes=SPREAD, routable_strategy_ids={"S1"})
    assert not reads, "the store was read despite being handed the rows"
    assert report["sample_size"] == 4


def test_a_handed_history_produces_the_identical_report(tmp_path):
    """Reuse must change nothing about the answer — the same property the replay frame owes."""
    a, text_a = run_paper_performance_report(now=NOW, root=tmp_path, outcomes=SPREAD, routable_strategy_ids={"S1"})
    b, text_b = run_paper_performance_report(now=NOW, root=tmp_path, outcomes=list(SPREAD), routable_strategy_ids={"S1"})
    assert a == b and text_a == text_b


def test_passing_none_still_reads_and_still_raises(tmp_path):
    """The cycle passes None precisely when its own read failed, and the report must then fail
    the same way — a report over a history nobody could verify is what must not be produced."""
    state = paper.state_dir(tmp_path)
    state.mkdir(parents=True, exist_ok=True)
    (state / paper.OUTCOMES_FILENAME).write_text("{not json}\n", encoding="utf-8")
    with pytest.raises(ToolError) as excinfo:
        run_paper_performance_report(now=NOW, root=tmp_path, outcomes=None, routable_strategy_ids={"S1"})
    assert excinfo.value.reason_code == "OUTCOME_HISTORY_UNREADABLE"


# --- Gate 0's population: own rows only ---------------------------------------

def test_the_report_covers_own_paper_rows_and_not_the_imported_history():
    """`live_candidate_eligible` asks whether THIS runtime has an edge, so history produced by
    the frozen crypto_AI_System cannot be in the answer — the same split the risk guard draws."""
    imported = [_outcome(5.0, "2026-07-18T01:00:00Z", outcome_id="imp1",
                         priced=False, provenance="crypto_ai_system_import")]
    report, _text = run_paper_performance_report(now=NOW, outcomes=SPREAD + imported, routable_strategy_ids={"S1"})
    assert report["sample_size"] == len(SPREAD)
    assert all(oid.startswith("out_") for oid in report["source_outcome_ids"])


def test_uncostable_imported_rows_can_no_longer_latch_gate_0_shut():
    """The reason the split is load-bearing rather than tidy. Imported rows are never costable,
    `_net_failure_modes` raises OUTCOME_NOT_COSTABLE on them permanently, and Gate 0 requires an
    empty `failure_modes` — so over the blended store the gate reads False however well the
    runtime trades. A gate that cannot open is indistinguishable from one that works right up
    until the moment it should have opened."""
    winners = [_outcome(2.0, f"2026-06-{i + 1:02d}T00:00:00Z", outcome_id=f"w{i}")
               for i in range(LIVE_CANDIDATE_MIN_SAMPLE)]
    imported = [_outcome(9.0, "2026-05-17T00:00:00Z", outcome_id="imp1",
                         priced=False, provenance="crypto_ai_system_import")]

    blended = build_performance_report(winners + imported, now=NOW)
    assert "OUTCOME_NOT_COSTABLE" in blended["failure_modes"]
    assert blended["live_candidate_eligible"] is False

    own_only, _text = run_paper_performance_report(
        now=NOW, outcomes=winners + imported, routable_strategy_ids={"S1"})
    assert own_only["failure_modes"] == []
    assert own_only["live_candidate_eligible"] is True


# --- Gate 0 judges the pool that would trade, not the ones already retired -----

def test_a_retired_lineages_losses_no_longer_hold_gate_0_shut():
    """Measured 2026-08-01 on the live machine: **all 86** rows holding Gate 0 shut came from
    lineages `lifecycle` had already SUSPENDED, and **zero** from one that could still route.
    The ladder had done its job — judge a strategy on its paper losses and retire it — and the
    losses of strategies that no longer exist were still gating a real-money door."""
    # Days apart: `count_independent_trade_events` collapses trades that close together, so a
    # cluster inside one hour is one event and would fail the sample gate for a reason this test
    # is not about. Sized to LIVE_CANDIDATE_MIN_SAMPLE so the eligible case is reachable.
    losers = [_outcome(-10.0, f"2026-05-{i + 1:02d}T00:00:00Z", strategy_id="RETIRED",
                       outcome_id=f"ret{i}") for i in range(4)]
    winners = [_outcome(1.5, f"2026-06-{i + 1:02d}T00:00:00Z", strategy_id="ROUTING",
                        outcome_id=f"rou{i}") for i in range(LIVE_CANDIDATE_MIN_SAMPLE)]

    blended = build_performance_report(losers + winners, now=NOW)
    assert blended["live_candidate_eligible"] is False        # the retired lineage drags it down

    scoped, _text = run_paper_performance_report(
        now=NOW, outcomes=losers + winners, routable_strategy_ids={"ROUTING"},
    )
    assert scoped["sample_size"] == LIVE_CANDIDATE_MIN_SAMPLE
    assert scoped["live_candidate_eligible"] is True


def test_an_unreadable_pool_withholds_the_eligibility_claim():
    """The opposite fail direction to `guards.drawdown_baseline`, and deliberately: there,
    unknown routability must KEEP losses inside a brake; here it must WITHHOLD a claim that real
    money may start. Both refuse — over a population nobody could establish, so does this."""
    winners = [_outcome(1.5, f"2026-06-{i + 1:02d}T00:00:00Z", strategy_id="ROUTING",
                        outcome_id=f"rou{i}") for i in range(LIVE_CANDIDATE_MIN_SAMPLE)]
    report, _text = run_paper_performance_report(
        now=NOW, outcomes=winners, routable_strategy_ids=None,
    )
    assert report["sample_size"] == 0
    assert report["live_candidate_eligible"] is False


def test_the_scope_has_no_default():
    """Structural: an optional scope is one the caller that forgets it never applies, and the
    untested branch is then the unscoped one."""
    import inspect

    parameter = inspect.signature(run_paper_performance_report).parameters["routable_strategy_ids"]
    assert parameter.default is inspect.Parameter.empty


def test_gate_0_needs_more_than_a_handful_of_trades_from_a_fresh_pool():
    """The hazard scoping the population created, and the reason the threshold moved with it.

    While the report covered the whole 86-row record, `MIN_SAMPLE_SIZE = 3` could not move the
    verdict — three more rows against 86 change nothing. Scoping to the routable pool makes it
    the BINDING constraint, and three profitable trades from a freshly promoted pool would have
    taken `live_candidate_eligible` from False to True. On a book running about -0.5R/trade net
    that is noise, not evidence: scoping without raising this would have been a relaxation
    wearing a defect fix's clothes."""
    def _winners(n):
        return [_outcome(0.4, f"2026-06-{i + 1:02d}T00:00:00Z", strategy_id="ROUTING",
                         outcome_id=f"w{i}") for i in range(n)]

    below, _ = run_paper_performance_report(
        now=NOW, outcomes=_winners(LIVE_CANDIDATE_MIN_SAMPLE - 1), routable_strategy_ids={"ROUTING"})
    assert below["live_candidate_eligible"] is False
    assert "INSUFFICIENT_CLOSED_OUTCOME_SAMPLE" in below["failure_modes"]

    at, _ = run_paper_performance_report(
        now=NOW, outcomes=_winners(LIVE_CANDIDATE_MIN_SAMPLE), routable_strategy_ids={"ROUTING"})
    assert at["live_candidate_eligible"] is True


def test_the_floor_is_reused_from_the_lifecycle_ladder_not_invented():
    """Pinned so the number cannot drift into an unsourced constant: it IS the window the ladder
    needs before it will even WARN a strategy, and Gate 0 asks a strictly larger question."""
    from runtime.mvp_runtime.crypto.lifecycle import DEFAULT_WINDOWS

    assert LIVE_CANDIDATE_MIN_SAMPLE == DEFAULT_WINDOWS[0]


# --- a row that is already net is COSTED, not called uncostable ----------------

def _settled_net_row(result_r=1.778, *, holding=8, timeframe="4h", direction="SHORT"):
    """The shape `paper.build_outcome_record` writes since 2026-07-30: costs already charged,
    labelled `intent_net_of_costs`, and carrying no funding term."""
    return {
        "outcome_closed": True, "result_R": result_r, "r_basis": "intent_net_of_costs",
        "created_at_utc": "2026-08-01T10:00:00Z", "outcome_id": "net1", "strategy_id": "S1",
        "symbol": "SOLUSDT", "timeframe": timeframe, "direction": direction,
        "entry_price": 73.06, "exit_price": 71.501, "risk": 0.8358,
        "holding_candles": holding, "close_reason": "take_profit",
        "provenance": paper.PAPER_PROVENANCE,
    }


def test_an_already_net_row_is_costed_rather_than_reported_uncostable():
    """The third instance of the same latch, and the first on the runtime's OWN current rows.

    `cost.outcome_net_r` returns None for an already-net row deliberately, so settlement costs
    are never charged twice — but None also means "cannot be priced", and
    `summarize_net_of_costs` read both as the second. Every row minted since 2026-07-30 is
    `intent_net_of_costs`, so the whole forward record counted as uncostable: measured on the
    first settlement of the newly promoted pool, a +1.778R take-profit produced
    `costed_count: 0` and `OUTCOME_NOT_COSTABLE`. Gate 0 requires `not failure_modes`, so that
    mode alone would have held it shut however well the pool traded."""
    summary = summarize_net_of_costs([_settled_net_row()])
    assert (summary["costed_count"], summary["uncostable_count"]) == (1, 0)
    assert summary["expectancy"] != 0.0


def test_the_already_net_row_is_still_charged_the_carry_it_does_not_contain():
    """Not a latch traded for a wrong number. Settlement charges fees and slippage and records
    no funding term, so carry is the one cost still owed — and it is SIGNED: a short earns it,
    which is why the net figure here sits ABOVE the stored `result_R` rather than below."""
    short_net = net_result_r(_settled_net_row(direction="SHORT"))
    long_net = net_result_r(_settled_net_row(direction="LONG"))
    assert short_net > 1.778 > long_net
    # A zero holding window owes no carry at all, so the stored figure passes through exactly.
    assert net_result_r(_settled_net_row(holding=0)) == 1.778


def test_a_row_that_owes_carry_it_cannot_price_stays_uncostable():
    """Returning `result_R` there would report a figure that silently omits a cost this
    function exists to charge."""
    row = _settled_net_row()
    row.pop("risk")
    row["entry_price"] = 0
    assert net_result_r(row) is None

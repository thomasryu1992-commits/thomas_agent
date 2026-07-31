"""The cost floor on entries, and the cost basis the lifecycle judges on.

Two halves of one defect, which is why they are one file. The runtime could open a trade whose
fees exceeded any edge it had ever measured, and it could not afterwards notice, because the
number the demotion ladder reads had never had a cost subtracted from it. Neither half fixes
anything alone: closing only the entry door leaves the ladder blind to what is already routing,
and netting only the ladder waits twenty trades per lineage to act on a trade that should not
have been taken.

What the tests pin, in the order the trade meets them:

- friction in R is a function of the STOP DISTANCE, not of the strategy — the arithmetic behind
  ``cost.MAX_ENTRY_COST_R``;
- an unpriceable plan costs ``inf``, never zero (a gate that reads unknown as free is not a gate);
- both doors refuse on the same number, the paper book and ``live_entry``;
- a settled outcome keeps its gross ``result_R`` byte-identical and gains the net one;
- the ladder demotes a strategy that is profitable gross and not net — the case that could not
  previously happen;
- the daily breaker meters net, so a day that lost money cannot read as flat.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from runtime.mvp_runtime import timeutil
from runtime.mvp_runtime.control import ControlStore
from runtime.mvp_runtime.crypto import cost, counterfactual, dashboard, live_entry, paper, pool
from runtime.mvp_runtime.crypto.cost import MAX_ENTRY_COST_R, outcome_net_r, round_trip_cost_r
from runtime.mvp_runtime.crypto.cycle import run_crypto_cycle
from runtime.mvp_runtime.crypto.guards import run_risk_guard
from runtime.mvp_runtime.crypto.lifecycle import (
    LifecycleThresholds,
    compute_metrics,
    compute_strategy_performance,
    evaluate_lifecycle,
)
from runtime.mvp_runtime.crypto.market_data import Candle, MarketSnapshot
from runtime.mvp_runtime.crypto.paper import (
    ENTRY_COST_UNECONOMIC,
    DryRunPaperStore,
    RealPaperStore,
    build_outcome_record,
    entry_cost_refusal,
    run_paper_update,
)
from runtime.mvp_runtime.safety_gate import FILESYSTEM_WRITE, Authorization

NOW = "2026-07-22T12:00:00Z"

_AUTH = Authorization(
    flags=(FILESYSTEM_WRITE,), provider_id="paper_trading", activation_sha256="sha256:test",
    expires_at="2999-01-01T00:00:00Z", evidence_ref=".runtime_governance_state/evidence.md",
)


# --- half one: the friction arithmetic ----------------------------------------

def test_friction_is_a_function_of_the_stop_distance():
    """The finding this whole change rests on. Same rates, same price, same direction — only
    the stop moves, and the cost in R moves inversely with it. This is why the 15m timeframe
    was structurally unprofitable while 1d was not: 1R there is a tenth as wide."""
    entry = 60000.0
    tight = round_trip_cost_r("LONG", entry, entry * 0.0022)   # a 15m-shaped stop: 0.22% of price
    wide = round_trip_cost_r("LONG", entry, entry * 0.0333)    # a 1d-shaped stop: 3.3% of price

    assert tight > MAX_ENTRY_COST_R > wide
    # Inverse in the stop distance, so the ratio of costs is the inverse ratio of the stops.
    assert tight / wide == pytest.approx(0.0333 / 0.0022, rel=1e-6)


def test_friction_is_direction_symmetric():
    """A short pays the same round trip as a long: it sells low on the way in and buys high on
    the way out, which is the same two adverse crossings."""
    assert round_trip_cost_r("LONG", 100.0, 1.0) == pytest.approx(round_trip_cost_r("SHORT", 100.0, 1.0))


@pytest.mark.parametrize(
    "direction,entry,risk",
    [("LONG", 100.0, 0.0), ("LONG", 100.0, -1.0), ("LONG", 0.0, 1.0), ("SIDEWAYS", 100.0, 1.0)],
)
def test_unpriceable_friction_is_infinite_not_free(direction, entry, risk):
    """``apply_cost_model`` returns zeros for a non-positive risk — a defensive guard that is
    correct for accounting and catastrophic for a gate, because zero cost admits the trade.
    The gate's own helper diverges here deliberately."""
    assert round_trip_cost_r(direction, entry, risk) == math.inf
    assert entry_cost_refusal(
        {"direction": direction, "entry_price": entry, "risk": risk}
    )["round_trip_cost_r"] == "inf"


def test_friction_prices_the_taker_exit_even_though_a_target_is_maker():
    """A winning trade exits maker at its target, and pricing THAT exit would let a plan clear
    the floor on the assumption it wins. The gate charges what a losing trade pays."""
    priced = round_trip_cost_r("LONG", 100.0, 1.0)
    maker_exit = -cost.apply_cost_model("LONG", 100.0, 100.0, 1.0, close_reason="take_profit").net_r

    assert priced > maker_exit


def test_refusal_names_the_numbers_that_produced_it():
    plan = {"direction": "LONG", "entry_price": 60000.0, "risk": 100.0,
            "symbol": "BTCUSDT", "timeframe": "15m", "strategy_id": "S1"}
    refusal = entry_cost_refusal(plan)

    assert refusal["reason_code"] == ENTRY_COST_UNECONOMIC
    assert refusal["limit_r"] == MAX_ENTRY_COST_R
    assert refusal["round_trip_cost_r"] > MAX_ENTRY_COST_R
    assert refusal["timeframe"] == "15m" and refusal["strategy_id"] == "S1"


def test_a_wide_enough_stop_is_admitted():
    assert entry_cost_refusal(
        {"direction": "LONG", "entry_price": 60000.0, "risk": 2000.0}
    ) is None


# --- half one, wired: the paper book refuses ----------------------------------

def _spec_dict(**overrides):
    base = {
        "schema_version": "strategy_spec.v1",
        "strategy_id": "S1",
        "strategy_version": "1.0",
        "strategy_family": "breakout",
        "symbol_scope": ["BTCUSDT"],
        "timeframe": "1d",
        "direction": "long",
        "entry_rules": {"operator": "AND", "conditions": [
            {"feature": "close", "comparison": ">", "value_from": "ma20"},
        ]},
        "exit_rules": {"stop_model": "atr", "stop_atr": 1.5, "target_atr": 2.0, "max_holding_bars": 10},
        "risk_constraints": {"max_risk_per_trade_R": 1.0},
    }
    base.update(overrides)
    return base


def _pool():
    return {"pool_version": "active_strategy_pool.v1", "active_strategies": [{
        "strategy_id": "S1",
        "status": "PAPER_ACTIVE",
        "champion_score": 0.5,
        "strategy_rule_hash": "deadbeef",
        "generation_id": "GEN-001",
        "strategy_spec": _spec_dict(),
    }]}


# 1.5 * 0.06 = 0.09 against a 60.0 close: a stop 0.15% of the price, which is what a 15m ATR
# looks like. The wide row keeps the same match conditions and only widens the stop.
ROW_TIGHT = {"timestamp": "2026-07-22T00:00:00Z", "close": 60.0, "ma20": 50.0, "atr": 0.06}
ROW_WIDE = {**ROW_TIGHT, "atr": 2.0}


def _snapshot():
    return {"symbol": "BTCUSDT", "timeframe": "1d", "candles": [{
        "open_time": "2026-07-21T00:00:00Z", "open": 59.0, "high": 61.0, "low": 58.0,
        "close": 60.0, "volume": 10.0, "close_time": "2026-07-22T00:00:00Z",
    }]}


def _paper_update(row, *, root):
    return run_paper_update(
        _snapshot(), row, _pool(), {"allow_new_position": True},
        store=DryRunPaperStore(), now=NOW, root=root, control_store=ControlStore(root),
    )


def test_the_paper_book_refuses_an_uneconomic_entry_and_opens_nothing(tmp_path):
    """The route still matches — this is not the router changing its mind. The plan is built,
    priced, and refused, and the refusal is a RECORD rather than a silently absent position."""
    summary, records = _paper_update(ROW_TIGHT, root=tmp_path)

    assert summary["route_status"] == paper.STATUS_ENTRY_CANDIDATE
    assert summary["opened"] is None
    assert summary["open_refused"]["reason_code"] == ENTRY_COST_UNECONOMIC
    assert [r["operation"] for r in records] == ["open_refused"]


def test_the_same_signal_with_a_wider_stop_opens(tmp_path):
    """The gate is about the stop, not about the strategy or the symbol: one field changes and
    the identical signal becomes tradable. Without this the refusal test would also pass if the
    gate simply refused everything."""
    summary, _ = _paper_update(ROW_WIDE, root=tmp_path)

    assert summary["opened"] is not None
    assert summary.get("open_refused") is None


def test_both_doors_refuse_on_one_threshold():
    """The live leg is handed the shared ROUTE, not the paper step's refusal, so a paper entry
    declined for cost would otherwise still reach a real order. Same constant, one authority."""
    assert live_entry.MAX_ENTRY_COST_R is MAX_ENTRY_COST_R


# --- half two: what a settled outcome records ---------------------------------

def _position(**overrides):
    base = {
        "position_id": "pos_1", "venue": "binance_futures", "symbol": "BTCUSDT",
        "timeframe": "1d", "direction": "LONG", "entry_price": 100.0,
        "stop_loss": 96.0, "take_profit": 108.0, "risk": 4.0, "holding_candles": 3,
        "opened_at_utc": "2026-07-20T00:00:00Z", "strategy_id": "S1", "candidate_id": "cand_1",
    }
    base.update(overrides)
    return base


def test_the_settled_row_is_net_and_carries_what_it_would_take_to_undo():
    """Settlement charges the costs (2026-07-30), so ``result_R`` IS the net figure and the
    label says so. What this increment still adds is the rest of the arithmetic: the gross it
    came from, the per-unit risk it is denominated in, and the RATES it was charged at —
    without which none of the three can be re-derived at a later schedule.

    The previous shape of this test asserted the opposite (``result_R`` gross, a
    ``result_R_net`` beside it). Both increments were solving the same defect from opposite
    ends and this one landed second; keeping both names would be one fact stored twice."""
    record = build_outcome_record(_position(), "take_profit", 108.0, 2.0, now=NOW)

    assert record["result_R"] < record["gross_result_R"] == 2.0
    assert record["r_basis"] == "intent_net_of_costs"
    assert "result_R_net" not in record
    assert record["risk"] == 4.0
    assert record["cost_model"] == {"taker_fee_bps": 5.0, "maker_fee_bps": 2.0, "slippage_bps": 3.0}


def test_a_settled_row_is_not_costed_a_second_time_on_read():
    """The two halves meet at the basis label, and this is the join. A row settled after the
    change carries its costs inside ``result_R``; ``outcome_net_r`` must decline it rather than
    subtract them again, or the ladder sees one loss twice and demotes an average strategy."""
    settled = build_outcome_record(_position(), "stop_loss", 96.0, -1.0, now=NOW)

    assert outcome_net_r(settled) is None
    # ...while a row from before the change is exactly what it does exist to re-price.
    assert outcome_net_r({**settled, "r_basis": "intent"}) is not None


def test_the_record_hash_covers_the_new_fields():
    """Self-hashing is what makes a tampered outcome detectable, so a field added outside the
    hash would be a field the store cannot defend."""
    record = build_outcome_record(_position(), "stop_loss", 96.0, -1.0, now=NOW)
    tampered = {**record, "cost_model": {"taker_fee_bps": 0.0, "maker_fee_bps": 0.0, "slippage_bps": 0.0}}
    tampered.pop("record_sha256")

    from runtime.read_only_kernel import integrity
    assert integrity.sha256_record(tampered) != record["record_sha256"]


def test_a_target_exit_is_charged_maker_and_a_stop_exit_taker():
    """The one asymmetry the cost model already knew about, now reaching paper outcomes: a
    target rests as a maker LIMIT and fills at its own price, a stop leaves at market."""
    target = build_outcome_record(_position(), "take_profit", 108.0, 2.0, now=NOW)
    stop = build_outcome_record(_position(), "stop_loss", 96.0, -1.0, now=NOW)

    charged = lambda r: r["gross_result_R"] - r["result_R"]
    assert charged(target) < charged(stop)


# --- half two: which rows may be netted --------------------------------------

def test_a_live_row_keeps_its_own_basis():
    """Live R is measured on ACTUAL fills, so slippage is already inside it and charging the
    full model would double-count. Its missing fees are a separate, still-open gap."""
    assert outcome_net_r({
        "r_basis": "filled", "result_R": -1.0, "entry_price": 100.0,
        "exit_price": 96.0, "direction": "LONG", "risk": 4.0,
    }) is None


def test_a_row_with_no_prices_is_not_guessed():
    """Imported history carries R and nothing to price it with. ``None`` means the caller keeps
    the gross figure and says so, rather than a net number nobody could derive."""
    assert outcome_net_r({"result_R": 1.88, "provenance": "crypto_ai_system_import"}) is None


def test_a_row_written_before_risk_was_recorded_reconstructs_it():
    """Paper settlement guarantees ``result_R = ±(exit - entry) / risk``, so the rows already
    on disk can be priced from the identity rather than waiting for new ones."""
    with_risk = {"result_R": 2.0, "entry_price": 100.0, "exit_price": 108.0,
                 "direction": "LONG", "risk": 4.0, "close_reason": "take_profit"}
    without = {k: v for k, v in with_risk.items() if k != "risk"}

    assert outcome_net_r(without) == pytest.approx(outcome_net_r(with_risk))


def test_an_exact_breakeven_without_risk_is_unpriceable():
    """The one case the reconstruction cannot do — dividing by a zero R — and it declines
    rather than dividing."""
    assert outcome_net_r({
        "result_R": 0.0, "entry_price": 100.0, "exit_price": 100.0, "direction": "LONG",
    }) is None


def test_an_unrecognised_basis_gets_the_costs_charged():
    """The default has to be the pessimistic branch. If an absent or unknown ``r_basis`` opted
    out, a row could buy itself the cheaper treatment by omitting a field."""
    assert outcome_net_r({
        "r_basis": "something_new", "result_R": 2.0, "entry_price": 100.0,
        "exit_price": 108.0, "direction": "LONG", "risk": 4.0,
    }) is not None


# --- half two, wired: the ladder and the breaker ------------------------------

def _outcome(result_r, *, risk, entry=100.0, close_reason="take_profit", at=NOW):
    """A settled paper outcome at a chosen R and stop width, priced consistently."""
    exit_price = entry + result_r * risk
    return {
        "outcome_closed": True, "result_R": result_r, "risk": risk, "entry_price": entry,
        "exit_price": exit_price, "direction": "LONG", "close_reason": close_reason,
        "r_basis": "intent", "created_at_utc": at, "candidate_id": "cand_1",
    }


def test_metrics_expectancy_is_net_and_says_how_much_of_it_was_priced():
    """A window that mixes priced and unpriced rows is still judged — dropping the unpriced
    ones would shrink the window, and a window that never fills escalates nothing — but the
    mix is on the metrics rather than known by whoever remembers it."""
    priced = [_outcome(0.1, risk=0.2) for _ in range(4)]
    unpriced = [{"outcome_closed": True, "result_R": 0.1, "created_at_utc": NOW}]
    metrics = compute_metrics(priced + unpriced)

    assert metrics["r_basis_net_rows"] == 4 and metrics["r_basis_gross_rows"] == 1
    assert metrics["expectancy_r"] < 0.1  # the four priced rows dragged it below gross


def test_the_ladder_demotes_a_strategy_that_is_profitable_gross_and_not_net():
    """The case that could not previously happen, and the reason this change exists.

    Twenty trades at +0.05R gross on a stop 0.2% of the price: the ladder used to compare
    +0.05 against ``warn_expectancy_r = 0.0``, see a winner, and leave it PAPER_ACTIVE for as
    long as it kept trading. The friction on that stop is ~0.8R, so the strategy is deeply
    negative, and the thresholds are unchanged — it is their input that was wrong.
    """
    outcomes = [_outcome(0.05, risk=0.2) for _ in range(20)]
    performance = compute_strategy_performance("S1", outcomes, now=NOW)

    assert performance["lifetime"]["expectancy_r"] < 0
    decision = evaluate_lifecycle(
        "PAPER_ACTIVE", performance, consecutive_failures=0,
        thresholds=LifecycleThresholds(), now=NOW,
    )
    assert decision["new_status"] != "PAPER_ACTIVE"


def test_the_gross_view_of_the_same_trades_would_have_kept_it_active():
    """The counterfactual that makes the test above mean something: strip the prices and the
    identical R history is a strategy the ladder has no reason to touch."""
    outcomes = [
        {"outcome_closed": True, "result_R": 0.05, "created_at_utc": NOW, "candidate_id": "cand_1"}
        for _ in range(20)
    ]
    performance = compute_strategy_performance("S1", outcomes, now=NOW)

    assert performance["lifetime"]["expectancy_r"] == pytest.approx(0.05)
    decision = evaluate_lifecycle(
        "PAPER_ACTIVE", performance, consecutive_failures=0,
        thresholds=LifecycleThresholds(), now=NOW,
    )
    assert decision["new_status"] == "PAPER_ACTIVE"


def test_the_daily_breaker_meters_net_so_a_losing_day_cannot_read_as_flat():
    """``DAILY_MAX_LOSS_R = -2.0`` is a claim about equity, and paper R never paid for the
    round trip. Six small gross winners on a tight stop are a real loss past the breaker."""
    day = [_outcome(0.1, risk=0.2, at="2026-07-22T0%d:00:00Z" % i) for i in range(6)]
    verdict = run_risk_guard(day, now=NOW)

    assert verdict["allow_new_position"] is False
    assert verdict["daily_pnl_r"] < -2.0


def test_the_breaker_is_unmoved_by_rows_it_cannot_price():
    """Netting must not become a way to invent losses either: a row with no prices contributes
    exactly its stored R, as it always did."""
    day = [{"outcome_closed": True, "result_R": 0.1, "created_at_utc": NOW} for _ in range(6)]
    verdict = run_risk_guard(day, now=NOW)

    assert verdict["daily_pnl_r"] == pytest.approx(0.6)
    assert verdict["allow_new_position"] is True


# --- the board says which basis it is showing ---------------------------------

def test_the_board_prints_both_bases_so_it_cannot_contradict_the_ladder():
    """The headline expectancy comes from the persisted feedback report and is gross. Netting
    the ladder without saying so on the board would have an operator watch strategies get
    suspended for losing money beside a number saying they make it."""
    own = [_outcome(0.1, risk=0.2) for _ in range(6)]
    perf = dashboard._net_performance(own)

    assert perf["expectancy_net"] < 0 < 0.1
    assert perf["expectancy_net_rows"] == 6 and perf["expectancy_gross_rows"] == 0

    text = dashboard.render_status_text({"performance": {
        "closed_count": 6, "expectancy": 0.1, "max_drawdown": 1.0,
        "recommendation": "CONTINUE", **perf,
    }})
    # "총액" is the gross line's label after the merge; it was "비용 제외" on this branch and the
    # two say the same thing. The property under test is that BOTH bases are printed, so the
    # board cannot show a healthy headline above a ladder that is suspending strategies.
    assert "총액" in text and "비용 반영" in text


def test_the_board_names_how_many_rows_it_could_not_price():
    own = [_outcome(0.1, risk=0.2)] + [{"outcome_closed": True, "result_R": 0.1}]
    perf = dashboard._net_performance(own)

    assert perf["expectancy_gross_rows"] == 1
    text = dashboard.render_status_text({"performance": {
        "closed_count": 2, "expectancy": 0.1, "recommendation": "CONTINUE", **perf,
    }})
    assert "1건은 가격 없어 총액 기준" in text


def test_an_empty_ledger_has_no_net_expectancy_rather_than_zero():
    assert dashboard._net_performance([])["expectancy_net"] is None


# --- the refusal is measurable, not silent ------------------------------------

class _NarrowRangeCollector:
    """Flat candles with a 0.06 true range on a 100 close — a 15m-shaped ATR on a 1d fixture,
    which is the only property this test needs from the market."""

    tool_id = "crypto.market_data.readonly"
    tool_version = "0.1.0-fake"
    network_egress = False
    source = "fake_exchange"

    def collect(self, symbol, timeframe, *, limit, timeout_seconds):
        step = timedelta(days=1)
        last_close = datetime(2026, 7, 22, 11, tzinfo=timezone.utc)
        n = 60
        candles = [
            Candle(
                open_time=timeutil.format_iso(last_close - (n - i) * step),
                open=100.0, high=100.03, low=99.97, close=100.0, volume=10.0,
                close_time=timeutil.format_iso(last_close - (n - 1 - i) * step),
            )
            for i in range(n)
        ]
        return MarketSnapshot(symbol=symbol, timeframe=timeframe, candles=candles,
                              source=self.source, is_synthetic=False)


def test_a_cost_refusal_is_shadowed_so_a_too_tight_gate_can_be_caught(tmp_path):
    """A wrong refusal leaves no trace — no record shows the trades that did not happen — and
    ``counterfactual.py`` exists precisely so a gate that saves money can be told from one that
    is merely too tight. The verdict ALLOWED this entry, so before this change the shadow
    condition (guards refused) was false and this refusal would have been the one nobody could
    calibrate.
    """
    pool.install_active_pool(
        {"active_strategies": [{
            "strategy_id": "S1", "status": "PAPER_ACTIVE", "champion_score": 0.5,
            "strategy_spec": _spec_dict(entry_rules={
                "operator": "AND",
                "conditions": [{"feature": "close", "comparison": ">", "value": 0.0}],
            }),
        }]},
        root=tmp_path,
    )
    record = run_crypto_cycle(
        collector=_NarrowRangeCollector(),
        store=RealPaperStore(root=tmp_path, authorization=_AUTH),
        now=NOW, root=tmp_path, control_store=ControlStore(tmp_path),
    )

    assert record["verdict_status"] == "ALLOW"          # the guards did not refuse
    assert record["opened"] is None                     # the economics door did
    shadows = counterfactual.load_open_counterfactuals(tmp_path)
    assert [s["block_reasons"] for s in shadows] == [[ENTRY_COST_UNECONOMIC]]

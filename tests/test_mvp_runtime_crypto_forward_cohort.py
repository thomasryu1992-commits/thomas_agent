"""The forward cohort (Phase 1, option A — Thomas 2026-09-23).

Pinned in the order it matters: (1) cohort rows can never reach the arming door — the money
store's reader refuses them and the walker never writes the money store; (2) the walker is the
forward book's own transition, and it keeps a position open across runs, starts each member at
its selecting row, never counts a bar twice and keeps sibling members' rows apart; (3) the
synthesized entry carries the admission evidence a promotion would, so the doors do not fail
open; (4) membership is mechanical and frozen: the door's bar, one member per lineage, no rule
the pool routes, no lineage with a pool clock, a sealed record whose edit is refused; (5) a
cohort member promoted into the pool seeds from its promotion (option A).
"""

from __future__ import annotations

import json

import pytest

from runtime.mvp_runtime.crypto import forward_book as fb
from runtime.mvp_runtime.crypto import forward_cohort as fco
from runtime.mvp_runtime.crypto import pool_state
from runtime.mvp_runtime.crypto.candidate_ranking import expected_replayed_bars
from runtime.mvp_runtime.crypto.cost import (
    DEFAULT_FUNDING_BPS_PER_INTERVAL,
    DEFAULT_MAKER_FEE_BPS,
    DEFAULT_SLIPPAGE_BPS,
    DEFAULT_TAKER_FEE_BPS,
    FUNDING_SOURCE_VENUE,
)
from runtime.mvp_runtime.crypto.outcome_math import net_result_r
from runtime.mvp_runtime.crypto.strategy import StrategySpec
from runtime.mvp_runtime.errors import ToolError
from runtime.read_only_kernel import integrity

SELECTED = "2026-07-03T00:00:00Z"
NOW = "2026-07-20T00:00:00Z"


def _spec_dict(*, family="breakout", symbols=("BTCUSDT",), timeframe="1d", adx=20.0, hold=10):
    return {
        "schema_version": "strategy_spec.v1", "strategy_id": "S007", "strategy_version": "1.0",
        "strategy_family": family, "symbol_scope": list(symbols), "timeframe": timeframe,
        "direction": "long",
        "entry_rules": {"operator": "AND", "conditions": [
            {"feature": "close", "comparison": ">", "value_from": "ma20"},
            {"feature": "adx", "comparison": ">=", "value": adx},
        ]},
        "exit_rules": {"stop_model": "atr", "stop_atr": 1.5, "target_atr": 2.0,
                       "max_holding_bars": hold},
        "risk_constraints": {"max_risk_per_trade_R": 1.0},
    }


def _record(cid, *, family="breakout", adx=20.0, created=SELECTED, closed=60, net_r=12.0,
            regime=None, **spec_kw):
    """A candidate row that clears the OBSERVATION entry bar (the entry-bar suite's shape: a
    THIN holdout, 60 closes, positive at today's rates) with a spec that parses and replays."""
    spec = _spec_dict(family=family, adx=adx, **spec_kw)
    evidence = {
        "robustness": {"verdict": "PROVISIONAL", "holdout_status": "INSUFFICIENT"},
        "closed_count": closed, "win_count": int(closed * 0.45),
        "avg_win_R": 2.0, "avg_loss_R": 1.0, "expectancy": round(net_r / closed, 8),
        "cost_summary": {
            "cost_model": {
                "taker_fee_bps": DEFAULT_TAKER_FEE_BPS, "maker_fee_bps": DEFAULT_MAKER_FEE_BPS,
                "slippage_bps": DEFAULT_SLIPPAGE_BPS,
                "funding_bps_per_interval": DEFAULT_FUNDING_BPS_PER_INTERVAL,
                "funding_source": FUNDING_SOURCE_VENUE,
            },
            "total_net_r": net_r, "total_fee_cost_r": 2.0, "total_maker_fee_cost_r": 0.5,
        },
        "bars_replayed": expected_replayed_bars(spec["timeframe"]),
        "holdout": {"closed_count": 10},
    }
    if regime is not None:
        evidence["regime_breakdown"] = {"per_regime": regime}
    record = {
        "candidate_id": cid, "strategy_id": "S007", "generation_id": "GEN-900",
        "strategy_rule_hash": StrategySpec.from_dict(spec).strategy_rule_hash,
        "strategy_spec": spec, "champion_score": 0.8, "backtest_evidence": evidence,
    }
    if created is not None:
        record["created_at_utc"] = created
    return record


def _pool(*entries):
    return {"pool_version": "active_strategy_pool.v1", "active_strategies": list(entries)}


def _day(n):
    return f"2026-07-{n:02d}T00:00:00Z"


def _candle(close_time, *, low=103.0, high=106.0, close=105.0):
    return {"open_time": close_time, "open": 104.0, "high": high, "low": low,
            "close": close, "volume": 10.0, "close_time": close_time}


def _row(close_time, *, match=True, regime=None):
    row = {"timestamp": close_time, "close": 105.0 if match else 95.0, "ma20": 100.0,
           "adx": 25.0, "atr": 2.0}
    if regime is not None:
        row["market_regime"] = regime
    return row


def _frame(days, *, stop_on=(), match=lambda d: True, regime=None):
    """A daily frame: the entry matches on every day ``match`` allows; a day in ``stop_on``
    trades through the stop (entry 105, stop 102)."""
    rows, candles = [], []
    for d in days:
        rows.append(_row(_day(d), match=match(d), regime=regime))
        candles.append(_candle(_day(d), low=101.0 if d in stop_on else 103.0))
    return rows, candles


def _member(record):
    return {"candidate_id": record["candidate_id"], "strategy_rule_hash": record["strategy_rule_hash"],
            "timeframe": record["strategy_spec"]["timeframe"],
            "symbol_scope": record["strategy_spec"]["symbol_scope"],
            "strategy_family": record["strategy_spec"]["strategy_family"],
            "selected_at_utc": record["created_at_utc"]}


def _install_cohort(tmp_path, *records):
    """Records into the candidate store, and one frozen cohort holding them."""
    pool_state.append_candidates([dict(r) for r in records], root=tmp_path)
    cohort = fco.build_cohort_record([_member(r) for r in records], now=NOW)
    path = fco._cohorts_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(cohort) + "\n")
    return cohort


def _walk(tmp_path, frame, *, now=NOW, persist=True):
    return fco.run_cohort_walk(tmp_path, now=now, frame_for=lambda s, t, b: frame, persist=persist)


# --- (1) the arming door never reads a cohort row -------------------------------------------

def test_the_money_store_reader_refuses_a_cohort_row(tmp_path):
    """Option A as code: a cohort row in the forward book's file is tampering, not evidence."""
    row = fco._finalize_row({"outcome_closed": True, "candidate_id": "c1", "settlement_id": "settle_x",
                             "result_R": 1.0, "provenance": fb.FORWARD_PROVENANCE})
    assert row["provenance"] == fco.COHORT_PROVENANCE
    path = fb._outcomes_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    with pytest.raises(ToolError) as exc:
        fb.read_forward_outcomes(tmp_path)
    assert exc.value.reason_code == fb.FORWARD_HISTORY_TAMPERED


def test_a_walk_writes_its_own_store_and_never_the_forward_books(tmp_path):
    _install_cohort(tmp_path, _record("cand_a"))
    summary = _walk(tmp_path, _frame(range(1, 8), stop_on={5}))
    assert summary["settled"] == 1
    assert len(fco.read_cohort_outcomes(tmp_path)) == 1
    assert not fb._outcomes_path(tmp_path).exists()
    assert not fb._book_path(tmp_path).exists()


# --- (2) the walker -------------------------------------------------------------------------

def test_a_position_open_at_the_end_of_a_run_is_carried_into_the_next(tmp_path):
    """The seed walk drops a boundary position because the live stream owns the present; the
    cohort has no live stream, so a daily walk that dropped it would lose every trade held
    across a run. Run one opens on the 3rd and ends open; run two sees the stop on the 5th."""
    _install_cohort(tmp_path, _record("cand_a"))
    first = _walk(tmp_path, _frame(range(1, 5)), now=_day(4))
    assert first["opened"] == 1 and first["settled"] == 0
    key = fb.book_key("cand:cand_a", "BTCUSDT", "1d")
    assert fco.load_positions(tmp_path)["entries"][key]["position"]

    second = _walk(tmp_path, _frame(range(1, 7), stop_on={5}), now=_day(6))
    assert second["settled"] == 1
    (row,) = fco.read_cohort_outcomes(tmp_path)
    assert row["opened_at_utc"] == _day(3) and row["close_reason"] == "stop_loss"


def test_the_walk_starts_at_the_selecting_row(tmp_path):
    """Bars before the selection only warm the indicators: a signal on them opens nothing."""
    _install_cohort(tmp_path, _record("cand_a"))
    _walk(tmp_path, _frame(range(1, 8), stop_on={2, 5}))
    (row,) = fco.read_cohort_outcomes(tmp_path)
    assert row["opened_at_utc"] >= SELECTED


def test_re_walking_the_same_bars_mints_nothing_new(tmp_path):
    _install_cohort(tmp_path, _record("cand_a"))
    frame = _frame(range(1, 12), stop_on={5, 9})
    first = _walk(tmp_path, frame)
    again = _walk(tmp_path, frame)
    assert first["settled"] >= 1 and again["settled"] == 0 and again["opened"] == 0
    assert len(fco.read_cohort_outcomes(tmp_path)) == first["settled"]


def test_sibling_members_on_one_bar_keep_their_own_rows(tmp_path):
    """Both members share the template id ``S007`` and fire on the same bar at the same price.
    Keyed on it, one ``position_id`` — and the dedup would have silently dropped a lineage."""
    a, b = _record("cand_a", family="breakout"), _record("cand_b", family="breakout_twin", adx=21.0)
    _install_cohort(tmp_path, a, b)
    _walk(tmp_path, _frame(range(1, 8), stop_on={5}))
    rows = fco.read_cohort_outcomes(tmp_path)
    assert sorted(r["candidate_id"] for r in rows) == ["cand_a", "cand_b"]
    assert len({r["settlement_id"] for r in rows}) == 2


def test_a_context_that_fails_to_fetch_costs_that_context_alone(tmp_path):
    from runtime.mvp_runtime.errors import ToolBlocked
    _install_cohort(tmp_path, _record("cand_a"), _record("cand_e", family="other", symbols=("ETHUSDT",)))

    def frame_for(symbol, timeframe, bars):
        if symbol == "ETHUSDT":
            raise ToolBlocked("TOOL_ERROR", "venue down")
        return _frame(range(1, 8), stop_on={5})

    summary = fco.run_cohort_walk(tmp_path, now=NOW, frame_for=frame_for)
    assert summary["walked"] == 1 and summary["failed"] == ["ETHUSDT 1d: TOOL_ERROR"]
    assert [r["candidate_id"] for r in fco.read_cohort_outcomes(tmp_path)] == ["cand_a"]


def test_a_dry_walk_writes_nothing(tmp_path):
    _install_cohort(tmp_path, _record("cand_a"))
    summary = _walk(tmp_path, _frame(range(1, 8), stop_on={5}), persist=False)
    assert summary["settled"] == 1
    assert not fco._outcomes_path(tmp_path).exists()
    assert not fco._positions_path(tmp_path).exists()


def test_the_fetch_reaches_back_to_the_oldest_start_plus_the_warm_up():
    assert fco.bars_to_fetch([_day(3), _day(10)], timeframe="1d", now=_day(20)) == 17 + 2 + fco.WARMUP_BARS
    assert fco.bars_to_fetch(["2020-01-01T00:00:00Z"], timeframe="1h", now=_day(20)) == fco.MAX_WALK_BARS
    assert fco.bars_to_fetch([], timeframe="1d", now=_day(20)) is None


# --- (3) the synthesized entry --------------------------------------------------------------

def test_the_entry_carries_the_admission_evidence_a_promotion_would():
    regime = {"trending_down": {"trades": 30, "total_r": -6.0}}
    entry = fco.synthesize_entry(_record("cand_a", regime=regime))
    assert entry["regime_evidence"] == regime
    assert "distribution_reference" in entry
    assert entry["strategy_id"] == entry["candidate_id"] == "cand_a"


def test_a_regime_the_row_measured_against_opens_nothing(tmp_path):
    """Without the projection the regime door fails open and the walk would trade the regime
    the lineage's own evidence excludes — the 2026-09-02 S004-GEN-690 shape."""
    _install_cohort(tmp_path, _record("cand_a", regime={"trending_down": {"trades": 30, "total_r": -6.0}}))
    summary = _walk(tmp_path, _frame(range(1, 8), stop_on={5}, regime="trending_down"))
    assert summary["opened"] == 0 and summary["settled"] == 0


# --- (4) membership -------------------------------------------------------------------------

def test_membership_is_the_bar_one_per_lineage_and_no_routed_rule():
    ok = _record("cand_ok")
    sibling = _record("cand_sib", adx=22.0)             # same family and context as cand_ok
    below = _record("cand_below", family="thin", closed=20)
    routed = _record("cand_routed", family="routed")
    timeless = _record("cand_timeless", family="timeless", created=None)
    trial = {**_record("cand_trial", family="trial"), "derivation_type": "trial_family"}
    pool = _pool({"status": "PAPER_ACTIVE", "strategy_rule_hash": routed["strategy_rule_hash"]})
    members = fco.eligible_members([ok, sibling, below, routed, timeless, trial], pool)
    ids = [m["candidate_id"] for m in members]
    assert len([i for i in ids if i in ("cand_ok", "cand_sib")]) == 1
    assert not {"cand_below", "cand_routed", "cand_timeless", "cand_trial"} & set(ids)
    assert fco.eligible_members([ok], _pool(), exclude_candidate_ids=frozenset({"cand_ok"})) == []


def test_a_frozen_record_is_sealed_and_an_edit_is_refused(tmp_path):
    cohort = _install_cohort(tmp_path, _record("cand_a"), _record("cand_b", family="other"))
    assert cohort["cohort_size"] == 2 and cohort["context_sizes"] == {"BTCUSDT|1d": 2}
    assert fco.member_candidate_ids(tmp_path) == {"cand_a", "cand_b"}
    path = fco._cohorts_path(tmp_path)
    edited = json.loads(path.read_text(encoding="utf-8"))
    edited["members"] = edited["members"][:1]
    path.write_text(json.dumps(edited) + "\n", encoding="utf-8")
    with pytest.raises(ToolError) as exc:
        fco.read_cohorts(tmp_path)
    assert exc.value.reason_code == fco.FORWARD_COHORT_TAMPERED


def test_freeze_appends_once_and_the_next_cohort_holds_only_new_lineages(tmp_path):
    pool_state.append_candidates([_record("cand_a")], root=tmp_path)
    dry = fco.freeze_cohort(tmp_path, now=NOW)
    assert [m["candidate_id"] for m in dry["members"]] == ["cand_a"]
    assert not fco._cohorts_path(tmp_path).exists()
    fco.freeze_cohort(tmp_path, now=NOW, apply=True)
    assert len(fco.read_cohorts(tmp_path)) == 1
    with pytest.raises(ToolError) as exc:
        fco.freeze_cohort(tmp_path, now=_day(21), apply=True)
    assert exc.value.reason_code == fco.FORWARD_COHORT_EMPTY


def test_a_lineage_the_pool_ever_clocked_is_not_frozen(tmp_path):
    pool_state.append_candidates([_record("cand_a"), _record("cand_b", family="other")], root=tmp_path)
    clocked = {"outcome_closed": True, "candidate_id": "cand_b", "settlement_id": "settle_b",
               "result_R": 1.0, "provenance": fb.FORWARD_PROVENANCE}
    clocked["record_sha256"] = integrity.sha256_record(clocked)
    fb._append_outcomes([clocked], root=tmp_path)
    assert [m["candidate_id"] for m in fco.freeze_cohort(tmp_path, now=NOW)["members"]] == ["cand_a"]


def test_the_report_judges_at_the_frozen_selection_time(tmp_path):
    _install_cohort(tmp_path, _record("cand_a"))
    _walk(tmp_path, _frame(range(1, 8), stop_on={5}))
    (cohort,) = fco.cohort_report(tmp_path)
    (line,) = cohort["members"]
    assert line["candidate_id"] == "cand_a" and line["closed_count"] == 1
    assert line["context_size"] == 1 and line["status"] == "FORWARD_INSUFFICIENT"
    # below the floor the verdict has no mean; the display bounds still read the one row
    (row,) = fco.read_cohort_outcomes(tmp_path)
    assert line["trade_mean_r"] == pytest.approx(net_result_r(row), abs=1e-6)
    assert line["trade_lower_bound_r"] is None  # one trade is never bounded
    assert line["trade_spread_floor_r"] is None  # nor pooled: one trade in the whole cohort


def test_the_trade_lower_bound_floors_the_spread_at_the_pooled_one():
    nets = (1.0, -0.5, 2.0, 0.5)  # stdev 1.0408
    bounds = fco.trade_bounds(nets, spread_floor=None)
    assert bounds["trade_mean_r"] == pytest.approx(0.75)
    assert bounds["trade_lower_bound_r"] == pytest.approx(0.75 - 1.96 * 1.040833 / 2, abs=1e-5)
    # a floor under the sample spread changes nothing; one over it is what is charged
    assert fco.trade_bounds(nets, spread_floor=0.5) == bounds
    assert fco.trade_bounds(nets, spread_floor=2.0)["trade_lower_bound_r"] == pytest.approx(0.75 - 1.96)
    # one trade is never bounded, floor or not; no spread and no floor is no bound
    assert fco.trade_bounds([5.2], spread_floor=1.4) == {"trade_mean_r": 5.2, "trade_lower_bound_r": None}
    assert fco.trade_bounds([0.5, 0.5], spread_floor=None)["trade_lower_bound_r"] is None
    assert fco.pooled_spread([[1.0], []]) is None
    assert fco.pooled_spread([[1.0], [3.0]]) == pytest.approx(1.414214)


def test_a_few_same_sized_wins_do_not_lead_a_longer_noisier_record(tmp_path, monkeypatch):
    # the 2026-09-23 board after #950: fixed-take-profit winners with a spread of cost noise
    nets = {"win4": [1.73, 1.74, 1.76, 1.73], "pair": [1.55, 1.53],
            "steady": [1.98, -1.05, 1.93, 1.9, 1.96, 1.94], "loser": [-1.0, 1.8, -1.0]}
    _install_cohort(tmp_path, *(_record(cid, family=cid) for cid in nets))
    monkeypatch.setattr(fco, "priced_nets", lambda judged, rows: nets[judged["candidate_id"]])
    monkeypatch.setattr(fco, "judge_forward", lambda judged, rows: {
        "status": "FORWARD_INSUFFICIENT", "priceable_count": len(nets[judged["candidate_id"]])})
    (cohort,) = fco.cohort_report(tmp_path)
    lines = {m["candidate_id"]: m for m in cohort["members"]}
    floor = lines["pair"]["trade_spread_floor_r"]
    assert floor == pytest.approx(1.172322, abs=1e-5)  # every member's trades, pooled
    assert lines["pair"]["trade_lower_bound_r"] < 0 < lines["steady"]["trade_lower_bound_r"]
    board = fco.board_summary(tmp_path)
    assert [m["candidate_id"] for m in board["leaders"]] == ["win4", "steady", "pair"]
    assert board["spread_floor_r"] == floor


# --- (5) option A at the pool's seeder --------------------------------------------------------

def _seeder_on(tmp_path, monkeypatch, entry):
    import scripts.seed_forward_book as seeder
    pool_state.install_active_pool(_pool(entry), root=tmp_path)
    seeded = []

    def seed(entry, spec, symbol, *, mint, now, root, collector, apply):
        seeded.append((entry.get("candidate_id"), mint))
        return {"strategy_id": entry.get("strategy_id"), "symbol": symbol, "timeframe": spec.timeframe,
                "mint": mint[:10], "bars": 0, "opens": 0, "settled": 0, "skipped": None}

    monkeypatch.setattr(seeder, "ROOT", tmp_path)
    monkeypatch.setattr(seeder, "assert_not_foreign_root_run", lambda *_a, **_k: None)
    monkeypatch.setattr(seeder.market_data, "select_market_data_collector", lambda **_k: None)
    monkeypatch.setattr(seeder, "seed_lineage", seed)
    return seeder, seeded


def _pool_entry(record, **overrides):
    entry = {"strategy_id": "S007-GEN-900", "status": "PAPER_ACTIVE",
             "candidate_id": record["candidate_id"], "generation_id": record["generation_id"],
             "strategy_rule_hash": record["strategy_rule_hash"], "champion_score": 0.8,
             "strategy_spec": record["strategy_spec"]}
    entry.update(overrides)
    return entry


def test_a_cohort_member_promoted_into_the_pool_seeds_from_its_promotion(tmp_path, monkeypatch):
    """The cohort period was the evidence it was promoted on: its pool clock starts after."""
    record = _record("cand_a")
    _install_cohort(tmp_path, record)
    seeder, seeded = _seeder_on(tmp_path, monkeypatch,
                                _pool_entry(record, promoted_at="2026-08-15T00:00:00Z"))
    assert seeder.main(["--list"]) == seeder.EXIT_OK
    assert seeded == [("cand_a", "2026-08-15T00:00:00Z")]


def test_a_cohort_member_without_a_promotion_time_is_not_seeded(tmp_path, monkeypatch, capsys):
    record = _record("cand_a")
    _install_cohort(tmp_path, record)
    seeder, seeded = _seeder_on(tmp_path, monkeypatch, _pool_entry(record))
    assert seeder.main(["--list"]) == seeder.EXIT_OK
    assert seeded == []
    assert "cohort member without promoted_at" in capsys.readouterr().out


def test_a_lineage_no_cohort_held_still_seeds_from_its_selecting_row(tmp_path, monkeypatch):
    record = _record("cand_a")
    pool_state.append_candidates([record], root=tmp_path)
    seeder, seeded = _seeder_on(tmp_path, monkeypatch,
                                _pool_entry(record, promoted_at="2026-08-15T00:00:00Z"))
    assert seeder.main(["--list"]) == seeder.EXIT_OK
    assert seeded == [("cand_a", SELECTED)]


def test_a_damaged_cohort_store_refuses_the_seed(tmp_path, monkeypatch, capsys):
    record = _record("cand_a")
    _install_cohort(tmp_path, record)
    with fco._cohorts_path(tmp_path).open("a", encoding="utf-8") as handle:
        handle.write("{not json\n")
    seeder, seeded = _seeder_on(tmp_path, monkeypatch, _pool_entry(record, promoted_at=NOW))
    assert seeder.main(["--list"]) == seeder.EXIT_BLOCKED
    assert "BLOCKED FORWARD_COHORT_UNREADABLE" in capsys.readouterr().err
    assert seeded == []


# --- (6) the scheduled walk and the board (the second PR) -----------------------------------

def _cohort_schedule():
    from runtime.mvp_runtime import scheduler
    return scheduler.Schedule(
        schedule_id="schedule_forward_cohort_test", kind=scheduler.KIND_FORWARD_COHORT,
        request="", interval_seconds=86400, enabled=True, created_by="test",
        created_at=NOW, next_run_at=NOW,
    )


def _fire(tmp_path, monkeypatch, frame_for):
    from runtime.mvp_runtime import scheduler
    monkeypatch.setattr(fco, "collector_frames", lambda root, *, now: frame_for)
    return scheduler._execute(
        _cohort_schedule(), now=NOW, ledger=None, working_memory=None,
        programization=None, repo_root=tmp_path, executor=lambda **_: {},
    )


def test_the_kind_is_maintenance_and_beyond_any_delegation():
    from runtime.mvp_runtime import schedule_delegation, scheduler
    kind = scheduler.KIND_FORWARD_COHORT
    assert kind in scheduler.KINDS and kind in scheduler.MAINTENANCE_KINDS
    assert kind not in scheduler.RISK_KINDS
    assert kind in schedule_delegation.FINANCIAL_KINDS


def test_a_fire_walks_every_frozen_cohort_and_reports_one_line(tmp_path, monkeypatch):
    _install_cohort(tmp_path, _record("cand_a"))
    status = _fire(tmp_path, monkeypatch, lambda s, t, b: _frame(range(1, 8), stop_on={5}))
    assert status.startswith("forward_cohort members=1 contexts=1 walked=1")
    assert "settled=1" in status
    assert len(fco.read_cohort_outcomes(tmp_path)) == 1


def test_a_fire_before_any_freeze_is_a_quiet_line(tmp_path, monkeypatch):
    status = _fire(tmp_path, monkeypatch, lambda s, t, b: pytest.fail("nothing to fetch"))
    assert status.startswith("forward_cohort members=0 contexts=0 walked=0")


def test_a_fire_in_which_every_context_failed_fails(tmp_path, monkeypatch):
    from runtime.mvp_runtime import scheduler
    from runtime.mvp_runtime.errors import ToolBlocked
    _install_cohort(tmp_path, _record("cand_a"))

    def down(symbol, timeframe, bars):
        raise ToolBlocked("TOOL_ERROR", "venue down")

    with pytest.raises(scheduler.SchedulerBlocked) as exc:
        _fire(tmp_path, monkeypatch, down)
    assert exc.value.reason_code == "FORWARD_COHORT_WALK_FAILED"
    assert "BTCUSDT 1d: TOOL_ERROR" in str(exc.value)


def test_the_board_shows_the_cohort_and_says_it_opens_no_door(tmp_path):
    from runtime.mvp_runtime.crypto.dashboard import build_status, render_status_text
    assert build_status(tmp_path, now=NOW)["forward_cohort"] is None
    assert "forward 코호트" not in render_status_text(build_status(tmp_path, now=NOW))

    _install_cohort(tmp_path, _record("cand_a"), _record("cand_b", family="other"))
    _walk(tmp_path, _frame(range(1, 8), stop_on={5}))
    status = build_status(tmp_path, now=NOW)
    board = status["forward_cohort"]
    assert board["members"] == 2 and board["with_rows"] == 2 and board["at_floor"] == 0
    assert board["status_counts"] == {"FORWARD_INSUFFICIENT": 2}
    assert board["last_walk_utc"] == NOW
    text = render_status_text(status)
    assert "forward 코호트 2계보 · 기록 2 · 탐색 2 · 성숙 0 · 확정 0 · 반박 0 (선별 전용" in text
    assert board["maturity_counts"] == {"EXPLORATORY": 2}
    assert "상위(σ≥" in text and "R) cand_a 1d n=1 평균 " in text
    assert "하한 ?R [탐색 1/10] · cand_b 1d n=1" in text  # n=1 each: no bound, so by id


def _board_of(monkeypatch, *members):
    monkeypatch.setattr(fco, "read_cohorts", lambda root: [{"cohort_id": "c1"}])
    monkeypatch.setattr(fco, "cohort_report", lambda root: [{"members": list(members)}])
    return fco.board_summary(None)


def _line(cid, status, n, *, mean=None, bound=None, timeframe="4h"):
    return {"candidate_id": cid, "timeframe": timeframe, "status": status, "priceable_count": n,
            "trade_mean_r": mean, "trade_lower_bound_r": bound, "trade_spread_floor_r": 1.2}


def test_a_contradicted_member_with_the_most_rows_is_never_a_leader(monkeypatch):
    from runtime.mvp_runtime.crypto.dashboard import render_status_text
    board = _board_of(
        monkeypatch,
        # the 2026-09-23 board: CONTRADICTED 4h shorts held the most rows and led it
        _line("short_a", "FORWARD_CONTRADICTED", 40, mean=-0.23, bound=-0.6),
        _line("short_b", "FORWARD_CONTRADICTED", 38, mean=-0.55, bound=-0.9),
        _line("young", "FORWARD_INSUFFICIENT", 3, mean=0.4, bound=0.1),
    )
    assert [m["candidate_id"] for m in board["leaders"]] == ["young"]
    assert board["status_counts"] == {"FORWARD_CONTRADICTED": 2, "FORWARD_INSUFFICIENT": 1}
    text = render_status_text({"forward_cohort": board})
    assert "상위(σ≥1.20R) young 4h n=3 평균 +0.40R 하한 +0.10R [탐색 3/25]" in text
    assert "short_a" not in text and "short_b" not in text


def test_leaders_rank_confirmed_then_the_lower_bound_not_the_rows_or_the_mean(monkeypatch):
    board = _board_of(
        monkeypatch,
        _line("many_rows", "FORWARD_INSUFFICIENT", 20, mean=0.3, bound=-0.1),
        _line("one_lucky", "FORWARD_INSUFFICIENT", 1, mean=2.0),
        _line("tight", "FORWARD_INSUFFICIENT", 8, mean=0.2, bound=0.05),
        _line("confirmed", "FORWARD_CONFIRMED", 30, mean=0.1, bound=0.02, timeframe="1d"),
        _line("no_rows", "FORWARD_INSUFFICIENT", 0),
    )
    assert [m["candidate_id"] for m in board["leaders"]] == ["confirmed", "tight", "many_rows"]


def test_an_unreadable_cohort_store_is_a_board_warning(tmp_path):
    from runtime.mvp_runtime.crypto.dashboard import build_status
    path = fco._cohorts_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json\n", encoding="utf-8")
    status = build_status(tmp_path, now=NOW)
    assert status["forward_cohort"] is None
    assert "forward cohort store unreadable (FORWARD_COHORT_UNREADABLE)" in status["warnings"]


# --- (3) the positions book is checked, not trusted (2026-09-23) -------------------------------

def _walked_book(tmp_path):
    """A positions book the walker wrote for one member, and the path it sits at."""
    _install_cohort(tmp_path, _record("cand_a"))
    _walk(tmp_path, _frame(range(1, 5)), now=_day(4))
    path = fco._positions_path(tmp_path)
    return json.loads(path.read_text(encoding="utf-8")), path


def _refused_after(tmp_path, edit):
    raw, path = _walked_book(tmp_path)
    edit(raw)
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ToolError) as exc:
        fco.load_positions(tmp_path)
    assert exc.value.reason_code == fco.FORWARD_COHORT_POSITIONS_INVALID
    return exc.value


def _only_entry(raw):
    (key,) = raw["entries"]
    return key, raw["entries"][key]


def test_a_book_the_walker_wrote_loads(tmp_path):
    raw, _ = _walked_book(tmp_path)
    assert set(fco.load_positions(tmp_path)["entries"]) == set(raw["entries"])


def test_a_book_of_another_version_is_refused(tmp_path):
    _refused_after(tmp_path, lambda raw: raw.update(forward_cohort_positions_version="forward_cohort_positions.v0"))


def test_an_entry_that_is_not_a_mapping_is_refused_not_dropped(tmp_path):
    """Dropped, the member would restart from its selection and re-open trades already settled."""
    def edit(raw):
        key, _ = _only_entry(raw)
        raw["entries"][key] = "garbled"
    _refused_after(tmp_path, edit)


def test_an_entry_filed_under_another_key_is_refused(tmp_path):
    def edit(raw):
        key, entry = _only_entry(raw)
        raw["entries"] = {fb.book_key("cand:cand_a", "ETHUSDT", "1d"): entry}
    _refused_after(tmp_path, edit)


def test_an_unknown_timeframe_is_refused(tmp_path):
    def edit(raw):
        key, entry = _only_entry(raw)
        entry["timeframe"] = "7m"
        raw["entries"] = {fb.book_key(entry["lineage"], entry["symbol"], "7m"): entry}
    _refused_after(tmp_path, edit)


def test_a_lineage_no_frozen_cohort_holds_is_refused(tmp_path):
    def edit(raw):
        key, entry = _only_entry(raw)
        entry["lineage"] = "cand:cand_stranger"
        raw["entries"] = {fb.book_key("cand:cand_stranger", entry["symbol"], entry["timeframe"]): entry}
    assert "cand_stranger" in _refused_after(tmp_path, edit).reason


@pytest.mark.parametrize("marks", [
    {"first_seen_candle": _day(4), "last_seen_candle": _day(2)},
    {"last_seen_candle": "not a time"},
    {"first_seen_candle": None, "last_seen_candle": _day(3)},
])
def test_candle_marks_that_do_not_parse_or_run_backward_are_refused(tmp_path, marks):
    _refused_after(tmp_path, lambda raw: _only_entry(raw)[1].update(marks))


def test_a_walk_that_would_move_a_mark_back_writes_nothing(tmp_path, monkeypatch):
    """The replay skips bars at or before `last_seen_candle`; a mark moved back re-opens settled
    trades. The walk refuses before it writes, so the book and the outcomes stay as they were."""
    _walked_book(tmp_path)
    path = fco._positions_path(tmp_path)
    before = path.read_text(encoding="utf-8")
    real = fco.advance_member

    def advance_then_rewind(state, *args, **kwargs):
        rows = real(state, *args, **kwargs)
        state["last_seen_candle"] = _day(1)
        return rows

    monkeypatch.setattr(fco, "advance_member", advance_then_rewind)
    with pytest.raises(ToolError) as exc:
        _walk(tmp_path, _frame(range(1, 7), stop_on={5}), now=_day(6))
    assert exc.value.reason_code == fco.FORWARD_COHORT_POSITIONS_INVALID
    assert path.read_text(encoding="utf-8") == before
    assert fco.read_cohort_outcomes(tmp_path) == []


# --- maturity: how far a member's record has got (2026-09-23) -------------------------------------

@pytest.mark.parametrize("status,n,timeframe,maturity", [
    ("FORWARD_INSUFFICIENT", 3, "4h", fco.MATURITY_EXPLORATORY),
    ("FORWARD_INSUFFICIENT", 25, "4h", fco.MATURITY_MATURE),   # at its floor, still not judgeable
    ("FORWARD_INSUFFICIENT", 10, "1d", fco.MATURITY_MATURE),   # 1d has its own, lower floor
    ("FORWARD_INSUFFICIENT", 9, "1d", fco.MATURITY_EXPLORATORY),
    ("FORWARD_CONFIRMED", 30, "4h", fco.MATURITY_CONFIRMED),
    ("FORWARD_CONTRADICTED", 40, "4h", fco.MATURITY_CONTRADICTED),
])
def test_maturity_is_the_verdict_when_there_is_one_else_the_trade_floor(status, n, timeframe, maturity):
    assert fco.maturity_of(_line("m", status, n, timeframe=timeframe)) == maturity


def test_a_member_whose_row_is_gone_is_unresolved_not_exploratory():
    assert fco.maturity_of({"candidate_id": "gone", "status": "UNRESOLVED"}) == fco.MATURITY_UNRESOLVED


def test_the_board_counts_maturity_and_marks_a_mature_leader_apart_from_an_exploratory_one(monkeypatch):
    """Both are FORWARD_INSUFFICIENT; the board used to print both as "판정 전"."""
    from runtime.mvp_runtime.crypto.dashboard import render_status_text
    board = _board_of(
        monkeypatch,
        _line("seasoned", "FORWARD_INSUFFICIENT", 30, mean=0.2, bound=0.05),
        _line("young", "FORWARD_INSUFFICIENT", 3, mean=0.4, bound=0.01),
        _line("refuted", "FORWARD_CONTRADICTED", 40, mean=-0.3, bound=-0.6),
    )
    assert board["maturity_counts"] == {"EXPLORATORY": 1, "MATURE": 1, "CONTRADICTED": 1}
    assert [(m["candidate_id"], m["maturity"]) for m in board["leaders"]] == [
        ("seasoned", "MATURE"), ("young", "EXPLORATORY")]
    text = render_status_text({"forward_cohort": {**board, "members": 3, "with_rows": 3}})
    assert "탐색 1 · 성숙 1 · 확정 0 · 반박 1 (선별 전용" in text
    assert "seasoned 4h n=30 평균 +0.20R 하한 +0.05R [성숙·판정 전]" in text
    assert "young 4h n=3 평균 +0.40R 하한 +0.01R [탐색 3/25]" in text


def test_the_report_carries_each_members_maturity(tmp_path):
    _install_cohort(tmp_path, _record("cand_a"))
    _walk(tmp_path, _frame(range(1, 8), stop_on={5}))
    (cohort,) = fco.cohort_report(tmp_path)
    (member,) = cohort["members"]
    assert member["maturity"] == fco.MATURITY_EXPLORATORY and member["trade_floor"] == 10


def test_an_underpowered_member_reads_mature_and_may_lead():
    """At its floor, leaning with the edge, unresolved: MATURE, not 반박 — and the leader filter
    drops only CONTRADICTED, so it ranks on its own lower bound."""
    from runtime.mvp_runtime.crypto.forward_confirmation import (
        FORWARD_CONTRADICTED, FORWARD_UNDERPOWERED,
    )
    line = {"status": FORWARD_UNDERPOWERED, "priceable_count": 10, "timeframe": "1d"}
    assert fco.maturity_of(line) == fco.MATURITY_MATURE
    assert fco.maturity_of({**line, "status": FORWARD_CONTRADICTED}) == fco.MATURITY_CONTRADICTED


# --- first-verdict stamps (2026-09-24, SEQUENTIAL_FORWARD_TEST_V0.1 decision Q2) ------------------

def _report_saying(status):
    return lambda root=None: [{"members": [{"candidate_id": "cand_a", "status": status}]}]


def test_a_verdict_is_stamped_the_first_walk_it_is_seen_and_kept_when_it_changes(tmp_path, monkeypatch):
    _install_cohort(tmp_path, _record("cand_a"))
    monkeypatch.setattr(fco, "cohort_report", _report_saying("FORWARD_CONFIRMED"))
    first = _walk(tmp_path, _frame(range(1, 5)), now=_day(4))
    assert first["first_confirmed"] == 1 and "first_confirmed=1" in fco.status_line(first)
    # the judge reads otherwise a day later: the stamp is a look that already happened
    monkeypatch.setattr(fco, "cohort_report", _report_saying("FORWARD_CONTRADICTED"))
    second = _walk(tmp_path, _frame(range(1, 6)), now=_day(5))
    assert second["first_confirmed"] == 0 and second["first_contradicted"] == 1
    assert fco.load_positions(tmp_path)["verdicts"] == {
        "cand_a": {"first_confirmed_at_utc": _day(4), "first_contradicted_at_utc": _day(5)}}
    # and a third look at the same verdict stamps nothing new
    third = _walk(tmp_path, _frame(range(1, 7)), now=_day(6))
    assert third["first_contradicted"] == 0
    assert fco.load_positions(tmp_path)["verdicts"]["cand_a"]["first_contradicted_at_utc"] == _day(5)


def test_a_judge_that_cannot_read_its_stores_costs_the_stamps_never_the_walk(tmp_path, monkeypatch):
    _install_cohort(tmp_path, _record("cand_a"))

    def refuse(root=None):
        raise ToolError("CANDIDATE_STORE_UNREADABLE", "gone")

    monkeypatch.setattr(fco, "cohort_report", refuse)
    summary = _walk(tmp_path, _frame(range(1, 8), stop_on={5}), now=_day(7))
    assert summary["settled"] == 1 and summary["verdicts_failed"] == "CANDIDATE_STORE_UNREADABLE"
    assert "verdicts_failed=CANDIDATE_STORE_UNREADABLE" in fco.status_line(summary)
    book = fco.load_positions(tmp_path)
    (entry,) = book["entries"].values()
    assert entry["last_seen_candle"] is not None and book["verdicts"] == {}


def test_nothing_is_stamped_on_a_dry_walk(tmp_path, monkeypatch):
    _install_cohort(tmp_path, _record("cand_a"))
    monkeypatch.setattr(fco, "cohort_report", _report_saying("FORWARD_CONFIRMED"))
    _walk(tmp_path, _frame(range(1, 5)), now=_day(4), persist=False)
    assert not fco._positions_path(tmp_path).exists()


@pytest.mark.parametrize("verdicts, why", [
    (["cand_a"], "not a mapping"),
    ({"cand_a": {"first_confirmed_at_utc": "yesterday"}}, "does not parse"),
    ({"cand_a": {"first_seen_at_utc": "2026-07-04T00:00:00Z"}}, "not only"),
    ({"cand_nobody": {"first_confirmed_at_utc": "2026-07-04T00:00:00Z"}}, "no frozen cohort holds"),
])
def test_a_first_verdict_map_that_is_not_the_walks_own_is_refused(tmp_path, verdicts, why):
    exc = _refused_after(tmp_path, lambda raw: raw.__setitem__("verdicts", verdicts))
    assert why in str(exc)


def test_a_book_that_would_move_or_drop_a_stamp_is_refused():
    before = {"cand_a": {"first_confirmed_at_utc": "2026-07-04T00:00:00Z"}}
    for after in ({}, {"cand_a": {"first_confirmed_at_utc": "2026-07-05T00:00:00Z"}}):
        with pytest.raises(ToolError) as exc:
            fco._assert_verdicts_kept(before, after)
        assert exc.value.reason_code == fco.FORWARD_COHORT_POSITIONS_INVALID
    fco._assert_verdicts_kept(before, {"cand_a": {**before["cand_a"], "first_contradicted_at_utc": "x"}})


def test_only_confirmed_and_contradicted_are_stamped():
    history, newly = fco.record_first_verdicts(
        {}, [("a", "FORWARD_UNDERPOWERED"), ("b", "FORWARD_INSUFFICIENT"), ("c", "UNRESOLVED"),
             (None, "FORWARD_CONFIRMED"), ("d", "FORWARD_CONFIRMED")], now="t")
    assert history == {"d": {"first_confirmed_at_utc": "t"}}
    assert newly == {"first_confirmed_at_utc": 1, "first_contradicted_at_utc": 0}

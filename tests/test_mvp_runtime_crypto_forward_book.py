"""The per-strategy forward book (Thomas 2026-08-29).

Pinned in the order it matters: (1) a virtual row is judged EXACTLY like a paper row —
same accounting basis, priced by the same reader, attributed by the same key — because
the 5-1 gate's arithmetic did not change, only its source; (2) the book paces itself
(once per closed candle, one position per lineage-context, the paper cooldown's own
off-by-nothing) so parallelism cannot become over-counting; (3) expiry is judged against
POOL membership, never against "not this cycle's context"; (4) the store is
tamper-evident end to end and a persist-side failure degrades instead of aborting the
cycle; (5) seeding partitions the calendar against the live stream and is idempotent;
(6) the no-signal marker names its own context's lineages and nothing more.
"""

from __future__ import annotations

import json

import pytest

from runtime.mvp_runtime.crypto import forward_book as fb
from runtime.mvp_runtime.crypto import forward_confirmation as fc
from runtime.mvp_runtime.crypto import promotion
from runtime.mvp_runtime.crypto.feedback import net_result_r
from runtime.mvp_runtime.crypto.paper import COOLDOWN_BARS_AFTER_STOPLOSS
from runtime.mvp_runtime.crypto.strategy import StrategySpec
from runtime.mvp_runtime.errors import ToolError
from runtime.read_only_kernel import integrity

NOW = "2026-08-29T12:00:00Z"


def _spec_dict(**overrides):
    base = {
        "schema_version": "strategy_spec.v1",
        "strategy_id": "S1",
        "strategy_version": "1.0",
        "strategy_family": "breakout",
        "symbol_scope": ["BTCUSDT"],
        "timeframe": "1d",
        "direction": "long",
        "entry_rules": {
            "operator": "AND",
            "conditions": [
                {"feature": "close", "comparison": ">", "value_from": "ma20"},
                {"feature": "adx", "comparison": ">=", "value": 20.0},
            ],
        },
        "exit_rules": {"stop_model": "atr", "stop_atr": 1.5, "target_atr": 2.0,
                       "max_holding_bars": 10},
        "risk_constraints": {"max_risk_per_trade_R": 1.0},
    }
    base.update(overrides)
    return base


def _pool_entry(**overrides):
    entry = {
        "strategy_id": "S1-GEN-9",
        "status": "PAPER_ACTIVE",
        "champion_score": 0.8,
        "candidate_id": "cand_forwardbook0001",
        "generation_id": "GEN-9",
        "strategy_rule_hash": "",
        "strategy_spec": _spec_dict(),
    }
    entry.update(overrides)
    return entry


def _pool(*entries):
    return {"pool_version": "active_strategy_pool.v1", "active_strategies": list(entries)}


ROW = {"timestamp": "2026-08-29T00:00:00Z", "close": 105.0, "ma20": 100.0, "adx": 25.0, "atr": 2.0}
ROW_NO_MATCH = {**ROW, "close": 95.0}


def _candle(close_time, *, low=103.0, high=106.0, close=105.0):
    return {"open_time": close_time, "open": 104.0, "high": high, "low": low,
            "close": close, "volume": 10.0, "close_time": close_time}


def _update(pool, row, candle, tmp_path, *, now=NOW, persist=True, symbol="BTCUSDT", timeframe="1d"):
    return fb.run_forward_book_update(
        pool=pool, feature_row=row, last_candle=candle,
        last_close=candle.get("close") if candle else None,
        symbol=symbol, timeframe=timeframe, now=now, root=tmp_path, persist=persist)


# --- a virtual row is a paper row in everything the judge reads -----------------------

def test_open_then_stop_settles_a_row_the_forward_judge_can_spend(tmp_path):
    pool = _pool(_pool_entry())
    s1 = _update(pool, ROW, _candle("2026-08-29T00:00:00Z"), tmp_path)
    assert s1["opened"] and s1["open_count"] == 1 and s1["settled"] == []

    s2 = _update(pool, ROW_NO_MATCH, _candle("2026-08-30T00:00:00Z", low=101.0),
                 tmp_path, now="2026-08-30T12:00:00Z")
    assert s2["settled"] and s2["open_count"] == 0

    rows = fb.read_forward_outcomes(tmp_path)
    assert len(rows) == 1
    row = rows[0]
    assert row["provenance"] == fb.FORWARD_PROVENANCE
    assert row["r_basis"] == "intent_net_of_costs"
    assert row["close_reason"] == "stop_loss"
    assert row["candidate_id"] == "cand_forwardbook0001"
    assert row["gross_result_R"] == pytest.approx(-1.0)  # the unmoved-stop convention survives
    assert row["result_R"] == pytest.approx(
        row["gross_result_R"] - row["fee_cost_r"] - row["slippage_cost_r"])
    assert net_result_r(row) is not None

    record = {"candidate_id": "cand_forwardbook0001", "strategy_spec": {"timeframe": "1d"}}
    assert len(fc.forward_outcomes_for(record, rows)) == 1
    verdict = fc.judge_forward(record, rows)
    assert verdict["closed_count"] == 1 and verdict["priceable_count"] == 1


def test_take_profit_prices_the_maker_leg_like_paper(tmp_path):
    pool = _pool(_pool_entry())
    _update(pool, ROW, _candle("2026-08-29T00:00:00Z"), tmp_path)
    _update(pool, ROW_NO_MATCH, _candle("2026-08-30T00:00:00Z", high=110.0),
            tmp_path, now="2026-08-30T12:00:00Z")
    row = fb.read_forward_outcomes(tmp_path)[0]
    assert row["close_reason"] == "take_profit"
    assert row["maker_fee_cost_r"] > 0


def test_time_exit_settles_at_max_hold_and_needs_last_close(tmp_path):
    """The one exit that reads last_close; a stream without closes would hold forever and
    then lose the row to nothing at all — so the path gets its own pin."""
    entry = _pool_entry(strategy_spec=_spec_dict(
        exit_rules={"stop_model": "atr", "stop_atr": 1.5, "target_atr": 2.0,
                    "max_holding_bars": 2}))
    pool = _pool(entry)
    _update(pool, ROW, _candle("2026-08-29T00:00:00Z"), tmp_path)
    _update(pool, ROW_NO_MATCH, _candle("2026-08-30T00:00:00Z"), tmp_path,
            now="2026-08-30T12:00:00Z")
    s = _update(pool, ROW_NO_MATCH, _candle("2026-08-31T00:00:00Z"), tmp_path,
                now="2026-08-31T12:00:00Z")
    assert s["settled"]
    assert fb.read_forward_outcomes(tmp_path)[0]["close_reason"] == "time_exit"


# --- pacing: parallel must not mean over-counted --------------------------------------

def test_the_same_candle_is_processed_at_most_once(tmp_path):
    pool = _pool(_pool_entry())
    candle = _candle("2026-08-29T00:00:00Z")
    s1 = _update(pool, ROW, candle, tmp_path)
    s2 = _update(pool, ROW, candle, tmp_path)
    assert s1["opened"] and not s2["opened"]
    assert s2["open_count"] == 1


def test_one_virtual_position_per_lineage_and_context(tmp_path):
    pool = _pool(_pool_entry())
    _update(pool, ROW, _candle("2026-08-29T00:00:00Z"), tmp_path)
    s2 = _update(pool, ROW, _candle("2026-08-30T00:00:00Z"), tmp_path,
                 now="2026-08-30T12:00:00Z")
    assert not s2["opened"] and s2["open_count"] == 1


def test_the_cooldown_matches_the_paper_marks_and_the_backtest(tmp_path):
    """Parity, not a private rule: the paper marks' expiry is stop close + N x timeframe
    with a STRICT compare (the bar closing exactly at the expiry trades — pinned in the
    paper tests), and `factory._replay` spends one count on the stop bar. Both land on:
    stop at T, blocked at T+tf, entering again at T+2tf."""
    pool = _pool(_pool_entry())
    _update(pool, ROW, _candle("2026-08-29T00:00:00Z"), tmp_path)
    s_stop = _update(pool, ROW, _candle("2026-08-30T00:00:00Z", low=101.0), tmp_path,
                     now="2026-08-30T12:00:00Z")
    assert s_stop["settled"] and not s_stop["opened"]
    s_blocked = _update(pool, ROW, _candle("2026-08-31T00:00:00Z"), tmp_path,
                        now="2026-08-31T12:00:00Z")
    assert not s_blocked["opened"]
    s_again = _update(pool, ROW, _candle("2026-09-01T00:00:00Z"), tmp_path,
                      now="2026-09-01T12:00:00Z")
    assert s_again["opened"], "the bar at stop close + 2 bars trades on the paper path too"


# --- expiry is a POOL-membership question ----------------------------------------------

def test_a_position_whose_context_is_still_pooled_is_never_swept_by_another_cycle(tmp_path):
    """The rule the first review round caught inverted: in one fan-out every OTHER context
    is foreign to this one, and sweeping on that raced the owning context's own time_exit
    — deterministically for seeded positions. Membership in the POOL is the only honest
    'this context is gone' signal."""
    pool = _pool(_pool_entry())
    _update(pool, ROW, _candle("2026-08-29T00:00:00Z"), tmp_path)
    s = fb.run_forward_book_update(
        pool=pool, feature_row={}, last_candle=_candle("2026-09-20T00:00:00Z"),
        last_close=105.0, symbol="ETHUSDT", timeframe="4h",
        now="2026-09-20T12:00:00Z", root=tmp_path, persist=True)
    assert not s.get("expired")
    assert any(st.get("position") for st in fb.load_book(tmp_path)["entries"].values())


def test_a_departed_context_expires_on_its_wall_budget_and_is_pruned(tmp_path):
    pool = _pool(_pool_entry())
    _update(pool, ROW, _candle("2026-08-29T00:00:00Z"), tmp_path)
    s = fb.run_forward_book_update(
        pool=_pool(), feature_row={}, last_candle=_candle("2026-09-20T00:00:00Z"),
        last_close=105.0, symbol="ETHUSDT", timeframe="4h",
        now="2026-09-20T12:00:00Z", root=tmp_path, persist=True)
    assert s.get("expired")
    assert fb.read_forward_outcomes(tmp_path) == []      # nobody priced that exit
    assert fb.load_book(tmp_path)["entries"] == {}       # nothing left to hold


# --- store discipline ------------------------------------------------------------------

def test_dry_run_computes_and_writes_nothing(tmp_path):
    s = _update(_pool(_pool_entry()), ROW, _candle("2026-08-29T00:00:00Z"), tmp_path,
                persist=False)
    assert s["opened"]
    assert not fb._book_path(tmp_path).exists()
    assert fb.read_forward_outcomes(tmp_path) == []


def test_settlement_append_is_idempotent(tmp_path):
    pool = _pool(_pool_entry())
    _update(pool, ROW, _candle("2026-08-29T00:00:00Z"), tmp_path)
    _update(pool, ROW_NO_MATCH, _candle("2026-08-30T00:00:00Z", low=101.0),
            tmp_path, now="2026-08-30T12:00:00Z")
    row = fb.read_forward_outcomes(tmp_path)[0]
    fb._append_outcomes([dict(row)], root=tmp_path)
    assert len(fb.read_forward_outcomes(tmp_path)) == 1


def test_a_tampered_row_fails_its_self_hash(tmp_path):
    pool = _pool(_pool_entry())
    _update(pool, ROW, _candle("2026-08-29T00:00:00Z"), tmp_path)
    _update(pool, ROW_NO_MATCH, _candle("2026-08-30T00:00:00Z", low=101.0),
            tmp_path, now="2026-08-30T12:00:00Z")
    path = fb._outcomes_path(tmp_path)
    row = json.loads(path.read_text().strip())
    row["result_R"] = 99.0
    path.write_text(json.dumps(row) + "\n")
    with pytest.raises(ToolError) as exc:
        fb.read_forward_outcomes(tmp_path)
    assert exc.value.reason_code == fb.FORWARD_HISTORY_TAMPERED


def test_a_foreign_provenance_row_is_tampering_not_a_vintage(tmp_path):
    """This file has ONE legitimate writer and feeds the door that arms real money. A
    paper row copied in — hash-valid under ITS provenance — must fail the read, not slide
    past the check the way the paper store waves its audited imports through."""
    smuggled = {"outcome_closed": True, "candidate_id": "cand_forwardbook0001",
                "result_R": 1.0, "r_basis": "intent_net_of_costs",
                "created_at_utc": NOW, "provenance": "mvp_paper_kernel",
                "settlement_id": "settle_smuggled0001"}
    smuggled["record_sha256"] = integrity.sha256_record(
        {k: v for k, v in smuggled.items() if k != "record_sha256"})
    path = fb._outcomes_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(smuggled) + "\n")
    with pytest.raises(ToolError) as exc:
        fb.read_forward_outcomes(tmp_path)
    assert exc.value.reason_code == fb.FORWARD_HISTORY_TAMPERED


def test_an_unreadable_book_degrades_and_writes_nothing(tmp_path):
    fb._book_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    fb._book_path(tmp_path).write_text("{not json")
    s = _update(_pool(_pool_entry()), ROW, _candle("2026-08-29T00:00:00Z"), tmp_path)
    assert s["degraded"] == fb.FORWARD_BOOK_UNVERIFIABLE
    assert fb._book_path(tmp_path).read_text() == "{not json"


def test_a_torn_outcomes_file_degrades_the_step_instead_of_aborting_the_cycle(tmp_path):
    """The never-raises contract holds past load: settlement must append, the append runs
    the verified read, and a line torn by a crash mid-write would otherwise abort every
    later step of the context cycle — including the live leg — from an OBSERVATIONAL
    store."""
    pool = _pool(_pool_entry())
    _update(pool, ROW, _candle("2026-08-29T00:00:00Z"), tmp_path)
    path = fb._outcomes_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"torn')
    s = _update(pool, ROW_NO_MATCH, _candle("2026-08-30T00:00:00Z", low=101.0),
                tmp_path, now="2026-08-30T12:00:00Z")
    assert s["degraded"] == fb.FORWARD_HISTORY_UNREADABLE
    assert path.read_text() == '{"torn'  # preserved for inspection


# --- attribution and the marker --------------------------------------------------------

def test_an_unattributable_entry_is_not_tracked(tmp_path):
    entry = _pool_entry(candidate_id=None, generation_id=None, strategy_rule_hash=None)
    s = _update(_pool(entry), ROW, _candle("2026-08-29T00:00:00Z"), tmp_path)
    assert not s["opened"] and s["open_count"] == 0
    assert fb.lineage_key(entry) == ""  # the cycle and the seeder share this refusal


def test_the_no_signal_marker_names_its_own_context_only(tmp_path):
    entry = _pool_entry(strategy_spec=_spec_dict(entry_rules={
        "operator": "AND",
        "conditions": [{"feature": "adx", "comparison": ">=", "value": 99.0}],
    }))
    pool = _pool(entry)
    _update(pool, ROW, _candle("2026-08-29T00:00:00Z"), tmp_path)
    inside = _update(pool, ROW, _candle("2026-09-05T00:00:00Z"), tmp_path,
                     now="2026-09-05T12:00:00Z")
    assert inside["no_signal"] == []
    past = _update(pool, ROW, _candle("2026-09-14T00:00:00Z"), tmp_path,
                   now="2026-09-14T12:00:01Z")
    assert any("S1-GEN-9" in item for item in past["no_signal"])
    # Another context's cycle does not repeat this context's marker — the list on a cycle
    # record is bounded by that context's own per-context cap.
    other = fb.run_forward_book_update(
        pool=pool, feature_row={}, last_candle=_candle("2026-09-14T04:00:00Z"),
        last_close=105.0, symbol="ETHUSDT", timeframe="4h",
        now="2026-09-14T12:00:02Z", root=tmp_path, persist=True)
    assert other["no_signal"] == []


# --- seeding ---------------------------------------------------------------------------

def _seed_frame(days, *, start_day=1):
    rows, candles = [], []
    for i in range(days):
        ct = "2026-07-%02dT00:00:00Z" % (start_day + i,)
        rows.append(dict(ROW, timestamp=ct))
        candles.append(_candle(ct))
    return rows, candles


def test_seeding_stamps_historical_times_and_is_idempotent(tmp_path):
    entry = _pool_entry(strategy_spec=_spec_dict(
        exit_rules={"stop_model": "atr", "stop_atr": 1.5, "target_atr": 2.0,
                    "max_holding_bars": 2}))
    spec = StrategySpec.from_dict(entry["strategy_spec"])
    rows, candles = _seed_frame(10)
    state, out = fb.walk_seed_span(entry, spec, rows, candles, symbol="BTCUSDT",
                                   timeframe="1d", mint="2026-07-03T00:00:00Z", boundary=None)
    assert out, "the span produced settled rows"
    assert all(r["seeded"] is True for r in out)
    assert all(r["created_at_utc"].startswith("2026-07-") for r in out)
    assert min(r["created_at_utc"] for r in out) >= "2026-07-03T00:00:00Z"  # pre-mint bars warm only
    fb._append_outcomes(out, root=tmp_path)
    fb._append_outcomes(out, root=tmp_path)  # the re-seed
    assert len(fb.read_forward_outcomes(tmp_path)) == len(out)

    state2, out2 = fb.walk_seed_span(entry, spec, rows, candles, symbol="BTCUSDT",
                                     timeframe="1d", mint="2026-07-03T00:00:00Z", boundary=None)
    assert [r["settlement_id"] for r in out2] == [r["settlement_id"] for r in out]


def test_seeding_stops_at_the_live_boundary_and_drops_the_open_position(tmp_path):
    """The two streams partition the calendar: nothing before `first_seen_candle` belongs
    to the live stream, nothing at or after it to the seeder — so no bar can ever settle
    in both, whatever order the operator runs things in."""
    entry = _pool_entry()
    spec = StrategySpec.from_dict(entry["strategy_spec"])
    rows, candles = _seed_frame(10)
    state, out = fb.walk_seed_span(entry, spec, rows, candles, symbol="BTCUSDT",
                                   timeframe="1d", mint="2026-07-01T00:00:00Z",
                                   boundary="2026-07-05T00:00:00Z")
    assert state["position"] is None
    assert all(r["created_at_utc"] < "2026-07-05T00:00:00Z" for r in out)
    assert state["last_seen_candle"] < "2026-07-05T00:00:00Z"


def test_seeding_refuses_a_sid_only_identity(tmp_path):
    entry = _pool_entry(candidate_id=None, generation_id=None, strategy_rule_hash=None)
    assert fb.lineage_key(entry) == ""  # the seeder checks exactly this and skips


# --- the arming door reads THIS store (the 5-1 source revision) ------------------------

def _confirming_rows(candidate_id, n=10):
    rows = []
    for i in range(n):
        day = 1 + i * 35  # ~10 distinct 30-day slices across ten months
        row = {"outcome_closed": True, "candidate_id": candidate_id,
               "result_R": 1.0 + 0.01 * (i % 3), "r_basis": "intent_net_of_costs",
               "created_at_utc": (timeutil_day(day)),
               "outcome_id": f"out_seedgate{i:04d}", "settlement_id": f"settle_seedgate{i:04d}"}
        rows.append(fb._finalize_row(row, seeded=True))
    return rows


def timeutil_day(day_offset):
    from datetime import datetime, timedelta, timezone
    return (datetime(2026, 1, 1, tzinfo=timezone.utc)
            + timedelta(days=day_offset)).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_forward_store_rows_confirm_a_live_promotion_through_the_gate(tmp_path):
    record = {"candidate_id": "cand_gate0001", "strategy_id": "S9",
              "strategy_spec": {"timeframe": "1d"}}
    fb._append_outcomes(_confirming_rows("cand_gate0001"), root=tmp_path)
    g = promotion._GateInput(candidates=[record], keep_active=True, live_tier="LIVE",
                             entries=[], store_root=tmp_path, occupying=[])
    promotion._gate_live_confirmation(g)  # no raise: the virtual stream armed it


def test_paper_store_rows_no_longer_count_as_forward_evidence(tmp_path):
    """The other direction of the source switch, pinned so a revert cannot pass: the same
    confirming rows sitting in the PAPER ledger arm nothing — the door reads only the
    forward book now."""
    from runtime.mvp_runtime.crypto.paper import OUTCOMES_FILENAME as PAPER_OUTCOMES
    from runtime.mvp_runtime.crypto.state import state_dir
    rows = []
    for r in _confirming_rows("cand_gate0002"):
        row = {k: v for k, v in r.items() if k not in ("record_sha256", "seeded", "provenance")}
        row["provenance"] = "crypto_ai_system_import"  # a shape paper's reader admits unverified
        rows.append(row)
    p = state_dir(tmp_path) / PAPER_OUTCOMES
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    record = {"candidate_id": "cand_gate0002", "strategy_id": "S9",
              "strategy_spec": {"timeframe": "1d"}}
    g = promotion._GateInput(candidates=[record], keep_active=True, live_tier="LIVE",
                             entries=[], store_root=tmp_path, occupying=[])
    with pytest.raises(ToolError) as exc:
        promotion._gate_live_confirmation(g)
    assert exc.value.reason_code == "CANDIDATE_UNCONFIRMED_FOR_LIVE"


# --- the seed merge: idempotent counters, not just idempotent rows -----------------------

def _seed_merge_setup(tmp_path, *, live_opens=2, seed_opens=5):
    """A book whose live stream has settled ``live_opens`` rows, plus a seed result."""
    import scripts.seed_forward_book as seeder
    lineage = "cand:cand_forwardbook0001"
    key = fb.book_key(lineage, "BTCUSDT", "1d")
    book = {"forward_book_version": fb.FORWARD_BOOK_VERSION, "entries": {key: {
        "lineage": lineage, "symbol": "BTCUSDT", "timeframe": "1d",
        "strategy_id": "S1-GEN-9", "first_tracked_at": "2026-08-20T00:00:00Z",
        "first_seen_candle": "2026-08-25T00:00:00Z",
        "last_seen_candle": "2026-08-29T00:00:00Z", "last_signal_at": "2026-08-29T00:00:00Z",
        "opens_count": live_opens, "position": None, "cooldown_remaining": 0,
    }}}
    seed_state = {"first_tracked_at": "2026-07-01T00:00:00Z", "opens_count": seed_opens,
                  "last_signal_at": "2026-08-20T00:00:00Z"}
    return seeder, book, key, seed_state


def test_re_seeding_does_not_inflate_the_open_counter(tmp_path):
    """The rows a re-seed writes are deduped; before #820 the counter was not, so every
    extra ``--apply`` added the whole seed span again (three applies left GEN-706 at 29
    opens against 12 real ones). The seeder's half is now ASSIGNED, not accumulated."""
    seeder, book, key, seed_state = _seed_merge_setup(tmp_path)
    for _ in range(3):
        seeder._merge_seed_state(book, key, seed_state, live_settled=2)
    state = book["entries"][key]
    assert state["seed_opens_count"] == 5
    assert state["opens_count"] == 2      # the live half, untouched by any number of runs
    assert fb.opens_total(state) == 7
    # what seeding IS allowed to move: the mint reaches back, the signal reaches forward
    assert state["first_tracked_at"] == "2026-07-01T00:00:00Z"
    assert state["last_signal_at"] == "2026-08-29T00:00:00Z"


def test_a_book_written_before_the_split_has_its_live_half_reconstructed(tmp_path):
    """A pre-#820 entry carries an unknowable number of seed folds inside ``opens_count``,
    so the first merge rebuilds it from the store: the live rows this context minted, plus
    an open position if it holds one. Trusting the inflated number would freeze it."""
    seeder, book, key, seed_state = _seed_merge_setup(tmp_path, live_opens=29)
    seeder._merge_seed_state(book, key, seed_state, live_settled=2)
    assert book["entries"][key]["opens_count"] == 2
    assert fb.opens_total(book["entries"][key]) == 7
    # and the reconstruction happens once — a later live open is the cycle's to keep
    book["entries"][key]["opens_count"] = 3
    seeder._merge_seed_state(book, key, seed_state, live_settled=2)
    assert book["entries"][key]["opens_count"] == 3


def test_a_position_open_at_reconstruction_counts_as_its_own_open(tmp_path):
    seeder, book, key, seed_state = _seed_merge_setup(tmp_path, live_opens=29)
    book["entries"][key]["position"] = {"position_id": "pos_x"}
    seeder._merge_seed_state(book, key, seed_state, live_settled=2)
    assert book["entries"][key]["opens_count"] == 3  # 2 settled + the one still open


def test_seeding_a_context_the_live_stream_never_reached_starts_it_live_at_zero(tmp_path):
    import scripts.seed_forward_book as seeder
    lineage = "cand:cand_forwardbook0001"
    key = fb.book_key(lineage, "BTCUSDT", "1d")
    book = {"forward_book_version": fb.FORWARD_BOOK_VERSION, "entries": {}}
    seed_state = {"lineage": lineage, "symbol": "BTCUSDT", "timeframe": "1d",
                  "first_tracked_at": "2026-07-01T00:00:00Z", "opens_count": 4,
                  "last_signal_at": "2026-08-20T00:00:00Z", "position": {"position_id": "p"},
                  "last_seen_candle": "2026-08-24T00:00:00Z",
                  "first_seen_candle": "2026-08-01T00:00:00Z", "cooldown_remaining": 3}
    seeder._merge_seed_state(book, key, seed_state, live_settled=0)
    state = book["entries"][key]
    assert (state["seed_opens_count"], state["opens_count"]) == (4, 0)
    # the live stream still owns its own start, its position and its cooldown
    assert state["position"] is None and state["cooldown_remaining"] == 0
    assert state["first_seen_candle"] is None and state["last_seen_candle"] is None


def test_a_lineage_that_only_ever_fired_in_the_seed_span_is_not_called_signal_less(tmp_path):
    """The marker asks "has this lineage EVER fired", and seeded opens are firings — so it
    reads the sum. Splitting the counter without this would have flagged every lineage
    whose only evidence is its backfill."""
    entry = _pool_entry(strategy_spec=_spec_dict(entry_rules={
        "operator": "AND",
        "conditions": [{"feature": "adx", "comparison": ">=", "value": 99.0}],
    }))
    pool = _pool(entry)
    _update(pool, ROW, _candle("2026-08-29T00:00:00Z"), tmp_path)
    key = next(iter(fb.load_book(tmp_path)["entries"]))
    fb.mutate_book(tmp_path, NOW, lambda b: b["entries"][key].update(  # backfill found three
        {"seed_opens_count": 3}))
    past = _update(pool, ROW, _candle("2026-09-14T00:00:00Z"), tmp_path,
                   now="2026-09-14T12:00:01Z")
    assert past["no_signal"] == []

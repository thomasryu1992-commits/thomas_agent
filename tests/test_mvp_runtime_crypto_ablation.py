"""Ablation lattice tests — a conjunction must beat its own parts to mint.

`docs/proposals/FACTORY_ABLATION_V0.1.md` §3, approved as proposed (Thomas 2026-08-12):
k <= 3 (lattice <= 7 members), selection on TRAIN net expectancy only, the full
conjunction mints only if it strictly beats every proper subset, ties go to the simpler
side, no new derivation type, and the whole lattice is charged to the context's attempt
count.

The selection tests plant their edge in raw candle columns (``volume``, ``open``) so which
lattice member enters where is controlled bar by bar, while every replay still runs the
real evaluator, the real settlement and the real cost model — the repo's measured lesson
being that measuring through factory internals skips its guards, these drive the factory's
own entry points: :func:`factory.ablate_hypothesis` over :func:`factory.build_replay_frame`
frames, and :func:`factory.run_factory` for the mint-path wiring."""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone

from runtime.mvp_runtime import timeutil
from runtime.mvp_runtime.crypto import pool, robustness
from runtime.mvp_runtime.crypto.factory import (
    ABLATION_MAX_CONDITIONS,
    ablate_hypothesis,
    build_replay_frame,
    holdout_split_index,
    run_factory,
)
from runtime.mvp_runtime.crypto.strategy import StrategySpec

NOW = "2026-07-22T12:00:00Z"
NOW_DT = datetime(2026, 7, 22, 12, tzinfo=timezone.utc)

# The three condition knobs. A and B read the same column and nest (B implies A), which is
# what manufactures EXACT expectancy ties: implied-equal members enter on identical bars and
# settle to identical floats. W reads ``open``, the one raw column settlement never touches,
# so it moves independently of A/B and of the outcome path.
_A = {"feature": "volume", "comparison": ">=", "value": 10.0}
_B = {"feature": "volume", "comparison": ">=", "value": 30.0}
_W = {"feature": "open", "comparison": ">=", "value": 50.0}


def _hypothesis(conditions, **overrides):
    base = {
        "schema_version": "strategy_spec.v1",
        "strategy_id": "H1", "strategy_version": "1.0", "strategy_family": "breakout",
        "symbol_scope": ["BTCUSDT"], "timeframe": "1d", "direction": "long",
        "entry_rules": {"operator": "AND", "conditions": conditions},
        "exit_rules": {"stop_model": "atr", "stop_atr": 1.5, "target_atr": 2.0,
                       "max_holding_bars": 10},
        "risk_constraints": {"max_risk_per_trade_R": 1.0},
    }
    base.update(overrides)
    return StrategySpec.from_dict(base)


def _planted_snapshot(events, *, n=300):
    """Flat 1d candles with planted, spaced outcome events.

    ``events`` maps a bar index to ``(volume, open_price, kind)``. The event bar carries
    the condition knobs; a ``"win"`` rallies the next two bars far past any ATR target this
    series produces, a ``"loss"`` dumps the next bar through any stop. Quiet bars carry
    volume 1 / open 1, so a condition on either knob fires exactly where it is planted.
    Events are spaced (>= 20 bars in these tests) so every position settles flat before the
    next event, and the ATR spike an event leaves decays before the next entry prices its
    exits."""
    step = timedelta(days=1)
    last_close = NOW_DT - timedelta(hours=1)
    closes = [100.0] * n
    for index, (_volume, _open, kind) in events.items():
        if kind == "win":
            closes[index + 1], closes[index + 2] = 106.0, 112.0
        else:
            closes[index + 1] = 85.0
    candles = []
    for i in range(n):
        volume, open_price, _ = events.get(i, (1.0, 1.0, None))
        close_time = last_close - (n - 1 - i) * step
        candles.append({
            "open_time": timeutil.format_iso(close_time - step),
            "open": open_price,
            "high": closes[i] + 2.0, "low": closes[i] - 2.0, "close": closes[i],
            "volume": volume,
            "close_time": timeutil.format_iso(close_time),
        })
    return {"symbol": "BTCUSDT", "timeframe": "1d", "candles": candles,
            "is_synthetic": False}


def _train_events_edge_in_one_condition():
    """Wins only where B (volume >= 30) holds; A-only bars (volume 15) lose.

    {B} and {A,B} enter on identical bars — B implies A — so the full conjunction can at
    best TIE its own part, and {A} is diluted by the planted losses."""
    events = {}
    for i, bar in enumerate(range(40, 201, 20)):        # 40..200: nine train events
        events[bar] = (35.0, 1.0, "win") if i % 2 == 0 else (15.0, 1.0, "loss")
    return events


def _train_events_interaction():
    """Wins only where A AND W hold together; each condition alone is diluted by losses."""
    events = {}
    kinds = [("win", 15.0, 100.0), ("loss", 15.0, 1.0), ("loss", 1.0, 100.0)]
    for i, bar in enumerate(range(40, 201, 20)):
        kind, volume, open_price = kinds[i % 3]
        events[bar] = (volume, open_price, kind)
    return events


# --- (a) the edge lives in one condition: the single-condition spec wins ---------------

def test_a_conjunction_whose_edge_is_one_condition_loses_to_that_condition():
    snapshot = _planted_snapshot(_train_events_edge_in_one_condition())
    frame = build_replay_frame(snapshot)
    result = ablate_hypothesis(_hypothesis([_A, _B]), [frame])
    assert result is not None
    winner, block = result
    assert block["lattice_size"] == 3
    assert block["full_beat_subsets"] is False
    assert block["hypothesis_conditions"] == [_A, _B]
    assert block["winner_conditions"] == [_B]
    assert [c.to_dict() for c in winner.entry_rules.conditions] == [_B]
    scores = block["train_net_expectancy_by_subset"]
    assert set(scores) == {"0", "1", "0+1"}
    # B implies A, so the full conjunction ties {B} exactly and is diluted nowhere — the
    # planted structure the selection had to see.
    assert scores["1"] == scores["0+1"]
    assert scores["1"] > scores["0"]
    # The winner is a different strategy than the hypothesis: its identity re-derives.
    assert winner.strategy_rule_hash != _hypothesis([_A, _B]).strategy_rule_hash


# --- (b) the conditions genuinely interact: the full conjunction wins ------------------

def test_a_conjunction_that_beats_every_part_mints_whole():
    snapshot = _planted_snapshot(_train_events_interaction())
    frame = build_replay_frame(snapshot)
    hypothesis = _hypothesis([_A, _W])
    result = ablate_hypothesis(hypothesis, [frame])
    assert result is not None
    winner, block = result
    assert block["full_beat_subsets"] is True
    assert winner is hypothesis                       # untouched, hash and all
    assert block["winner_conditions"] == block["hypothesis_conditions"] == [_A, _W]
    scores = block["train_net_expectancy_by_subset"]
    assert scores["0+1"] > scores["0"] and scores["0+1"] > scores["1"]


# --- (c) ties go to the simpler subset -------------------------------------------------

def test_a_tie_hands_the_win_to_the_fewest_conditions():
    """k = 3 with one working condition: every member containing B ties at B's expectancy
    (implication makes the entry bars identical), so the full set cannot STRICTLY beat its
    parts and the tie at the top of the proper subsets resolves to the 1-condition {B}."""
    snapshot = _planted_snapshot(_train_events_edge_in_one_condition())
    frame = build_replay_frame(snapshot)
    always_true = {"feature": "open", "comparison": ">=", "value": 0.5}
    result = ablate_hypothesis(_hypothesis([_A, _B, always_true]), [frame])
    assert result is not None
    winner, block = result
    assert block["lattice_size"] == 7
    assert block["full_beat_subsets"] is False
    assert block["winner_conditions"] == [_B]
    scores = block["train_net_expectancy_by_subset"]
    # The planted ties: {B} == {A,B} == {B,C} == {A,B,C}, and the simpler side won.
    assert scores["1"] == scores["0+1"] == scores["1+2"] == scores["0+1+2"]
    assert len(winner.entry_rules.conditions) == 1


# --- (d) nothing to ablate skips the lattice -------------------------------------------

def test_k1_or_wide_or_non_and_hypotheses_skip_the_lattice():
    snapshot = _planted_snapshot(_train_events_edge_in_one_condition())
    frame = build_replay_frame(snapshot)
    assert ablate_hypothesis(_hypothesis([_B]), [frame]) is None
    four = [_A, _B, _W, {"feature": "close", "comparison": ">", "value": 1.0}]
    assert len(four) == ABLATION_MAX_CONDITIONS + 1
    assert ablate_hypothesis(_hypothesis(four), [frame]) is None
    or_spec = _hypothesis([_A, _B], entry_rules={"operator": "OR", "conditions": [_A, _B]})
    assert ablate_hypothesis(or_spec, [frame]) is None


# --- (e) the selection cannot see a holdout bar, structurally --------------------------

def test_selection_is_identical_whatever_the_holdout_contains():
    """Two snapshots share every train bar and disagree about every holdout event — the
    second's tail rewards the full conjunction and punishes {B}. If any lattice replay
    could read past ``holdout_split_index``, the two selections would diverge; they are
    byte-identical because the train frames the lattice replays simply do not contain the
    tail (`factory._train_frame`)."""
    train = _train_events_edge_in_one_condition()
    n = 300
    split = holdout_split_index(n)
    tail_a = {bar: (35.0, 1.0, "win") for bar in range(split + 10, n - 5, 20)}
    tail_b = {bar: (35.0, 1.0, "loss") for bar in range(split + 10, n - 5, 20)}
    frame_a = build_replay_frame(_planted_snapshot({**train, **tail_a}, n=n))
    frame_b = build_replay_frame(_planted_snapshot({**train, **tail_b}, n=n))
    hypothesis = _hypothesis([_A, _B])
    winner_a, block_a = ablate_hypothesis(hypothesis, [frame_a])
    winner_b, block_b = ablate_hypothesis(hypothesis, [frame_b])
    assert block_a == block_b
    assert winner_a.strategy_rule_hash == winner_b.strategy_rule_hash
    # And the two tails really do differ where the FULL backtest would see them — the
    # control that proves the equality above is about the lattice, not about the fixture.
    assert frame_a.rows[split:] != frame_b.rows[split:]
    assert frame_a.rows[:split] == frame_b.rows[:split]


def test_the_lattice_is_deterministic():
    snapshot = _planted_snapshot(_train_events_edge_in_one_condition())
    frame = build_replay_frame(snapshot)
    first = ablate_hypothesis(_hypothesis([_A, _B, _W]), [frame])
    second = ablate_hypothesis(_hypothesis([_A, _B, _W]), [frame])
    assert first is not None and second is not None
    assert first[0].to_dict() == second[0].to_dict()
    assert first[1] == second[1]
    # Key order is the enumeration order — sizes ascending, index-lexicographic — so a
    # stored block re-serializes identically on every read.
    assert list(first[1]["train_net_expectancy_by_subset"]) == [
        "0", "1", "2", "0+1", "0+2", "1+2", "0+1+2",
    ]


# --- the mint path wires the winner, the block, and the counters -----------------------

def _trending_snapshot(n=200):
    """The factory-test fixture's shape, locally: rising closes with a mid-series dip and
    the taker legs a production snapshot always carries, so the rotation's flow families
    stay mintable and the fire exercises the evidence path."""
    step = timedelta(days=1)
    last_close = NOW_DT - timedelta(hours=1)
    candles = []
    price = 100.0
    for i in range(n):
        drift = 1.0 if (i // 20) % 3 != 2 else -1.5
        price = max(10.0, price + drift)
        close_time = last_close - (n - 1 - i) * step
        volume = 10.0 + (i % 7)
        buy_share = 0.62 if drift > 0 else 0.38
        candles.append({
            "open_time": timeutil.format_iso(close_time - step),
            "open": price - drift, "high": price + 1.5, "low": price - 1.5,
            "close": price, "volume": volume,
            "close_time": timeutil.format_iso(close_time),
            "taker_buy_base": volume * buy_share,
            "taker_buy_quote": volume * buy_share * price,
            "quote_volume": volume * price,
            "trade_count": 40.0 + (i % 11),
        })
    moment = timeutil.parse_iso(candles[0]["open_time"])
    end = timeutil.parse_iso(candles[-1]["close_time"])
    funding = []
    while moment <= end:
        funding.append({"timestamp": timeutil.format_iso(moment),
                        "funding_rate": 0.0001 * ((len(funding) % 7) - 3)})
        moment += timedelta(hours=8)
    return {"symbol": "BTCUSDT", "timeframe": "1d", "candles": candles,
            "funding": funding, "is_synthetic": False}


def test_run_factory_ablates_exactly_the_hypotheses_with_a_lattice_to_run():
    """Wiring, through the factory's own front door: every registered candidate whose
    condition count sits in [2, ABLATION_MAX_CONDITIONS] carries the ablation block, every
    one outside does not, and the fire's counters reconcile with the blocks it stored."""
    result = run_factory(_trending_snapshot(), active_pool={"active_strategies": []},
                         existing_candidates=[], now=NOW)
    assert result["candidates"], "the fixture must mint, or this asserts nothing"
    blocks = 0
    for candidate in result["candidates"]:
        k = len(candidate["strategy_spec"]["entry_rules"]["conditions"])
        ablation = candidate["backtest_evidence"].get("ablation")
        if 2 <= k <= ABLATION_MAX_CONDITIONS or ablation is not None:
            # Registered k is the WINNER's count and may be 1 after a swap; the block
            # remembers the hypothesis. Presence must line up with the hypothesis size.
            assert ablation is not None
            hypothesis_k = len(ablation["hypothesis_conditions"])
            assert 2 <= hypothesis_k <= ABLATION_MAX_CONDITIONS
            assert ablation["lattice_size"] == 2 ** hypothesis_k - 1
            assert ablation["winner_conditions"] == (
                candidate["strategy_spec"]["entry_rules"]["conditions"])
            # §3-3: no new derivation type — the winner registers through the seeded path.
            assert candidate["derivation_type"] == "seeded_template"
            blocks += 1
    ablation_refusals = [r for r in result["rejected"]
                         if str(r.get("reason", "")).startswith("ablation_winner_")]
    assert result["ablated_count"] == blocks + len(ablation_refusals)
    assert 0 <= result["luck_filtered_count"] <= result["ablated_count"]
    luck_filtered_registered = sum(
        1 for c in result["candidates"]
        if c["backtest_evidence"].get("ablation", {}).get("full_beat_subsets") is False)
    assert result["luck_filtered_count"] >= luck_filtered_registered


# --- (f) the whole lattice is charged to the context -----------------------------------

def _context_record(cid, *, lattice_size=None):
    evidence = {"expectancy": 0.1, "stdev_r": 0.2, "closed_count": 30}
    if lattice_size is not None:
        evidence["ablation"] = {"lattice_size": lattice_size}
    return {"candidate_id": cid,
            "strategy_spec": {"symbol_scope": ["BTCUSDT"], "timeframe": "1h"},
            "backtest_evidence": evidence}


def test_a_lattice_winner_charges_its_whole_lattice_to_its_context():
    key = (("BTCUSDT",), "1h")
    assert pool.attempts_by_context([_context_record("c-plain")]) == {key: 1}
    assert pool.attempts_by_context(
        [_context_record("c-plain"), _context_record("c-winner", lattice_size=7)]
    ) == {key: 8}
    # The bar the best-of-N correction sets moves with the charge, which is the point:
    # seven tried and one kept is not the same evidence as one tried and one kept.
    assert robustness.selection_adjusted_z(8) > robustness.selection_adjusted_z(2)
    quality = pool.candidate_quality(_context_record("c-winner", lattice_size=7), attempts=8)
    assert quality["attempts_in_context"] == 8
    assert quality["selection_adjusted_z"] == round(robustness.selection_adjusted_z(8), 6)


def test_an_unreadable_lattice_size_charges_one_never_zero():
    key = (("BTCUSDT",), "1h")
    for bad in ("7", True, 0, -3, None, 2.5):
        record = _context_record("c-bad", lattice_size=bad)
        assert pool.attempts_by_context([record]) == {key: 1}, bad


def test_re_appends_of_a_lattice_winner_charge_once():
    """The distinct-candidate rule survives the extension: a re-appended winner is one
    lattice, not two."""
    winner = _context_record("c-winner", lattice_size=7)
    assert pool.attempts_by_context([winner, dict(winner)]) == {(("BTCUSDT",), "1h"): 7}


def test_the_mint_output_charges_by_its_own_blocks():
    """End to end: the attempt count over a real fire's rows equals the sum of each row's
    lattice size (or 1), which is what makes `selection_adjusted_z` honest about the grid."""
    result = run_factory(_trending_snapshot(), active_pool={"active_strategies": []},
                         existing_candidates=[], now=NOW)
    expected: dict[tuple, int] = {}
    for candidate in result["candidates"]:
        key = pool.search_context_key(candidate["strategy_spec"])
        size = candidate["backtest_evidence"].get("ablation", {}).get("lattice_size", 1)
        expected[key] = expected.get(key, 0) + size
    assert pool.attempts_by_context(result["candidates"]) == expected


# --- (g) the extra evidence key changes no ranking and no door -------------------------

def test_the_ablation_block_is_inert_to_quality_backlog_and_the_store(tmp_path):
    result = run_factory(_trending_snapshot(), active_pool={"active_strategies": []},
                         existing_candidates=[], now=NOW)
    carrying = [c for c in result["candidates"] if "ablation" in c["backtest_evidence"]]
    assert carrying, "the fixture must mint through the lattice, or this asserts nothing"
    for candidate in carrying:
        stripped = copy.deepcopy(candidate)
        del stripped["backtest_evidence"]["ablation"]
        # Attempts held equal, the quality view is identical: no ranking axis reads the
        # block — it is evidence about the SEARCH, and its only ranking effect is the
        # attempt charge, which is the designed one.
        assert pool.candidate_quality(candidate, attempts=5) == \
               pool.candidate_quality(stripped, attempts=5)
    # The store takes the rows, hands them back intact, and the backlog partition and the
    # promotion ranking still hold over rows carrying the key.
    assert pool.append_candidates(result["candidates"], root=tmp_path) == len(result["candidates"])
    stored = pool.read_candidates(tmp_path)
    assert [c["backtest_evidence"].get("ablation") for c in stored] == \
           [c["backtest_evidence"].get("ablation") for c in result["candidates"]]
    ranked = pool.rank_candidates(stored)
    assert len(ranked) == len({c["candidate_id"] for c in stored})
    backlog = pool.promotable_backlog(tmp_path)
    assert backlog["candidates_read"] == len(ranked)
    # The backlog's own invariant — each judged row counted exactly once — holds over rows
    # carrying the key, which is the whole of what "unaffected" means for a partition.
    assert backlog["count"] + sum(backlog["refused"].values()) == backlog["candidates_read"]

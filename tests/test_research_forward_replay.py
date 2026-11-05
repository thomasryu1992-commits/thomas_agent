"""HISTORICAL_FORWARD_REPLAY_V0.1: contexts, generation ids, shards, freeze exclusions, scoring window, verdict."""

from __future__ import annotations

import json
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from runtime.mvp_runtime import scheduler
from runtime.mvp_runtime.crypto import factory, template_space
from runtime.mvp_runtime.errors import ToolError
from scripts.research import forward_replay as fr


# --- contexts and constants ------------------------------------------------------------------------------

def test_the_contexts_are_todays_seven_schedules():
    assert [c.name for c in fr.CONTEXTS] == ["BTCUSDT-1h", "ETHUSDT-1h", "SOLUSDT-1h", "BNBUSDT-1h", "DOGEUSDT-1h",
                                            "pooled-4h", "pooled-1d"]
    pooled = [c for c in fr.CONTEXTS if len(c.symbols) > 1]
    assert {c.timeframe for c in pooled} == set(factory.POOLED_TIMEFRAMES)
    assert all(c.symbols == ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "DOGEUSDT") for c in pooled)
    assert all(len(c.symbols) == 1 for c in fr.CONTEXTS if c.timeframe not in factory.POOLED_TIMEFRAMES)


def test_the_fire_uses_the_schedulers_fusion_allocation():
    assert fr.FUSION_PAIRS == scheduler.FACTORY_FUSION_PAIRS


def test_the_hold_allowance_covers_the_longest_holding_a_template_may_draw():
    longest = max(t.param_space["max_holding_bars"].hi for t in template_space.TEMPLATES
                  if "max_holding_bars" in t.param_space)
    assert fr.HOLD_ALLOWANCE_BARS >= longest


# --- generation ids and the fire ------------------------------------------------------------------------

def test_generation_ids_are_day_major_unique_and_independent_of_the_shard():
    days = [date(2022, 1, 1) + timedelta(days=i) for i in range(3)]
    ids = [fr.generation_id(d, c) for d in days for c in fr.CONTEXTS]
    assert len(set(ids)) == len(ids) and ids == sorted(ids)
    assert all(i.startswith("GEN-") for i in ids)
    assert fr.generation_id(days[1], fr.CONTEXTS[0]) > fr.generation_id(days[0], fr.CONTEXTS[-1])


def test_the_fixed_generation_reaches_run_factory_and_is_restored():
    saved = factory.next_generation_id
    with fr.fixed_generation("GEN-000042"):
        assert factory.next_generation_id([{"generation_id": "GEN-999"}]) == "GEN-000042"
    assert factory.next_generation_id is saved


def test_the_replay_feed_has_no_vendor_rows():
    with pytest.raises(ToolError):
        fr._NoFeed().open_interest_history("BTCUSDT", days=10, timeout_seconds=1)


def test_a_fire_hands_run_factory_the_scheduler_shape(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(fr, "mining_snapshot", lambda symbol, tf, **k: {"symbol": symbol, "timeframe": tf})

    class _Cache:
        def __init__(self, collector):
            pass
    monkeypatch.setattr(fr.market_data, "PeerCandleCache", _Cache)
    monkeypatch.setattr(fr.market_data, "select_market_data_collector", lambda **k: object())

    def run(snapshot, **kwargs):
        seen.update(kwargs, snapshot=snapshot, gid=factory.next_generation_id([]))
        return {"candidates": [{"x": 1}]}
    row = fr.fire(fr.BY_NAME["pooled-4h"], date(2024, 6, 3), [{"old": 1}], state=tmp_path, run=run)
    assert row["status"] == "fired" and row["candidates"] == [{"x": 1}]
    assert seen["snapshot"]["symbol"] == "BTCUSDT" and len(seen["cohort_snapshots"]) == 4
    assert seen["active_pool"] == {} and seen["positioning_eligible"] is False and seen["trial_proposals"] is None
    assert seen["fusion_pairs"] == fr.FUSION_PAIRS and seen["now"] == "2024-06-03T08:10:00Z"
    assert seen["existing_candidates"] == [{"old": 1}]
    assert seen["gid"] == fr.generation_id(date(2024, 6, 3), fr.BY_NAME["pooled-4h"])
    single = fr.fire(fr.BY_NAME["ETHUSDT-1h"], date(2024, 6, 3), [], state=tmp_path, run=run)
    assert single["status"] == "fired" and seen["cohort_snapshots"] is None


def test_a_leg_the_cache_cannot_serve_cancels_the_fire(monkeypatch, tmp_path):
    monkeypatch.setattr(fr, "mining_snapshot", lambda symbol, tf, **k: None if symbol == "SOLUSDT" else {})
    monkeypatch.setattr(fr.market_data, "PeerCandleCache", lambda c: None)
    monkeypatch.setattr(fr.market_data, "select_market_data_collector", lambda **k: object())
    row = fr.fire(fr.BY_NAME["pooled-1d"], date(2021, 1, 4), [], state=tmp_path,
                  run=lambda *a, **k: pytest.fail("ran without a leg"))
    assert row["status"] == "no_data:SOLUSDT" and row["candidates"] == []


# --- shards ------------------------------------------------------------------------------------------------

def _cache(tmp_path):
    base = tmp_path / "cache" / "binance" / "klines"
    base.mkdir(parents=True)
    (base / "BTCUSDT.1h.jsonl").write_text("[1,2]\n")
    return tmp_path / "cache"


def test_a_shard_resumes_after_its_last_fire_and_grows_its_own_store(monkeypatch, tmp_path):
    calls = []

    def fake_fire(context, day, store, **k):
        calls.append((day, len(store)))
        return {"day": day.isoformat(), "status": "fired", "candidates": [{"d": day.isoformat()}], "seconds": 0.0}
    monkeypatch.setattr(fr, "fire", fake_fire)
    out, cache = tmp_path / "s.jsonl", _cache(tmp_path)
    ctx = fr.BY_NAME["BTCUSDT-1h"]
    assert fr.simulate(ctx, date(2023, 1, 1), date(2023, 1, 3), out, arm="A", cache_root=cache, state=tmp_path) == 0
    assert [c[1] for c in calls] == [0, 1, 2]
    calls.clear()
    assert fr.simulate(ctx, date(2023, 1, 1), date(2023, 1, 5), out, arm="A", cache_root=cache, state=tmp_path) == 0
    assert calls == [(date(2023, 1, 4), 3), (date(2023, 1, 5), 4)]
    head, fires = fr.read_shard(out)
    assert head["context"] == "BTCUSDT-1h" and [f["day"] for f in fires][-1] == "2023-01-05"


def test_a_shard_written_with_other_data_is_refused(monkeypatch, tmp_path):
    monkeypatch.setattr(fr, "fire", lambda c, d, s, **k: {"day": d.isoformat(), "status": "fired",
                                                          "candidates": [], "seconds": 0.0})
    out, cache = tmp_path / "s.jsonl", _cache(tmp_path)
    ctx = fr.BY_NAME["BTCUSDT-1h"]
    fr.simulate(ctx, date(2023, 1, 1), date(2023, 1, 1), out, arm="A", cache_root=cache, state=tmp_path)
    (cache / "binance" / "klines" / "BTCUSDT.1h.jsonl").write_text("[1,3]\n")
    assert fr.simulate(ctx, date(2023, 1, 1), date(2023, 1, 2), out, arm="A", cache_root=cache,
                       state=tmp_path) == fr.EXIT_BLOCKED


def _shard(path, manifest, days):
    path.write_text("\n".join([json.dumps({"manifest": manifest}),
                               *[json.dumps({"day": d, "candidates": c}) for d, c in days]]) + "\n")
    return path


def test_compare_shards_finds_the_first_day_that_differs(tmp_path):
    m = {"spec": fr.SPEC, "arm": "A", "context": "pooled-4h", "code_digest": "c", "cache_digest": "d",
         "machine": "x86_64"}
    row = {"strategy_rule_hash": "h", "backtest_evidence": {"expectancy": 0.1}, "mint_params": {}}
    a = _shard(tmp_path / "a", m, [("2023-01-01", [row]), ("2023-01-02", [row])])
    b = _shard(tmp_path / "b", {**m, "machine": "arm64"}, [("2023-01-01", [row]), ("2023-01-02", [row])])
    assert fr.compare_shards(a, b) == {"identical": True, "compared_days": 2}
    c = _shard(tmp_path / "c", m, [("2023-01-01", [row]),
                                   ("2023-01-02", [{**row, "backtest_evidence": {"expectancy": 0.1000001}}])])
    assert fr.compare_shards(a, c)["day"] == "2023-01-02"
    d = _shard(tmp_path / "d", {**m, "code_digest": "other"}, [])
    assert fr.compare_shards(a, d)["reason"] == "manifest:code_digest"


# --- freeze and the scoring window -------------------------------------------------------------------------

def _member(cid, family="breakout", tf="4h"):
    return {"candidate_id": cid, "strategy_family": family, "timeframe": tf, "symbol_scope": ["BTCUSDT"],
            "strategy_rule_hash": cid, "direction": "long", "selected_at_utc": "x"}


def test_the_freeze_reads_only_rows_made_before_the_anchor_and_keeps_the_doors_exclusions():
    store = [{"created_at_utc": "2023-01-10T08:10:00Z", "id": "a"}, {"created_at_utc": "2023-02-20T08:10:00Z", "id": "b"}]
    asked = []

    def eligible(rows, pool, *, exclude_candidate_ids, exclude_lineages):
        asked.append(([r["id"] for r in rows], exclude_candidate_ids, exclude_lineages, pool))
        return [_member("m1")] if len(asked) == 1 else []
    first = date(2023, 2, 1)
    at = [first, first + timedelta(days=28), first + timedelta(days=28 * 7)]
    fr.freeze_all(store, at, eligible=eligible)
    assert asked[0][0] == ["a"] and asked[1][0] == ["a", "b"] and asked[0][3] == {}
    assert asked[1][1] == {"m1"} and ("breakout", ("BTCUSDT",), "4h") in asked[1][2]
    # 196 days later the cohort is closed: its lineage is free again, its member id is still held
    assert asked[2][2] == frozenset() and asked[2][1] == {"m1"}


def test_only_trades_opened_inside_the_window_count(monkeypatch):
    candles = [{"open_time": f"2024-01-{d:02d}T00:00:00Z"} for d in range(1, 31)]
    frame = SimpleNamespace(candles=candles, rows=[{}] * 30, cost=None, funding=None)
    monkeypatch.setattr(fr, "_slice", lambda f, i: SimpleNamespace(candles=f.candles[i:], rows=f.rows[i:],
                                                                   cost=None, funding=None))
    outcomes = [{"created_at_utc": "2024-01-04T00:00:00Z", "result_R": 9.0},
                {"created_at_utc": "2024-01-05T00:00:00Z", "result_R": 1.0},
                {"created_at_utc": "2024-01-12T00:00:00Z", "result_R": -0.5},
                {"created_at_utc": "2024-01-20T00:00:00Z", "result_R": 7.0}]
    monkeypatch.setattr(fr, "_replay", lambda spec, rows, c, **k: (outcomes, 0, 0, 0, 0, 0, 0, 0))
    got = fr.window_trades(None, [frame, frame], "2024-01-05T00:00:00Z", "2024-01-20T00:00:00Z")
    assert got == [1.0, -0.5, 1.0, -0.5]


# --- the verdict --------------------------------------------------------------------------------------------

def _reading(mean, p, anchors=30, years=None):
    return {"mean_R": mean, "p_one_sided": p, "anchors": anchors, "years_R": years or {"2023": 1.0, "2024": 1.0}}


def test_the_verdict_is_holm_across_the_arms_at_five_percent():
    assert fr.verdicts({"A": _reading(0.1, 0.03), "B": _reading(0.1, 0.5)}, leak_ok=True, sanity_ok=True) \
        == {"A": "REPLAY_NOT_CONFIRMED", "B": "REPLAY_NOT_CONFIRMED"}
    assert fr.verdicts({"A": _reading(0.1, 0.024), "B": _reading(0.1, 0.5)}, leak_ok=True, sanity_ok=True)["A"] \
        == "REPLAY_CONFIRMED"


def test_the_verdict_needs_the_floor_the_years_and_both_checks():
    ok = {"A": _reading(0.1, 0.001)}
    assert fr.verdicts(ok, leak_ok=True, sanity_ok=True)["A"] == "REPLAY_CONFIRMED"
    assert fr.verdicts({"A": _reading(0.1, 0.001, anchors=19)}, leak_ok=True, sanity_ok=True)["A"] == "NOT_JUDGEABLE"
    assert fr.verdicts({"A": _reading(0.1, 0.001, years={"2023": 1, "2024": -1})},
                       leak_ok=True, sanity_ok=True)["A"] == "REPLAY_NOT_CONFIRMED"
    assert fr.verdicts(ok, leak_ok=False, sanity_ok=True)["A"] == "BLOCKED"
    assert fr.verdicts(ok, leak_ok=True, sanity_ok=False)["A"] == "BLOCKED"


def test_the_anchor_t_is_one_sided_and_the_bootstrap_is_seeded():
    up = fr.t_stats([0.1, 0.2, 0.15, 0.12])
    assert up["p_one_sided"] < 0.01 and fr.t_stats([-0.1, -0.2, -0.15, -0.12])["p_one_sided"] > 0.99
    values = [0.1, -0.05, 0.2, 0.0, 0.15, -0.1, 0.3, 0.05]
    assert fr.block_bootstrap_p(values) == fr.block_bootstrap_p(values)
    assert fr.block_bootstrap_p(values[:5]) is None


# --- where it may run -------------------------------------------------------------------------------------

def test_outside_the_sandbox_it_needs_a_cache_and_no_gate_variable(monkeypatch, tmp_path):
    monkeypatch.setattr(fr, "_in_sandbox", lambda: False)
    installed = []
    monkeypatch.setattr(fr.lake, "install", lambda **k: installed.append(k))
    assert fr._prepare(None)[1].startswith("outside the research sandbox")
    monkeypatch.setenv("MVP_MARKET_DATA", "binance_futures")
    assert "MVP_MARKET_DATA" in fr._prepare(tmp_path)[1] and installed == []
    monkeypatch.delenv("MVP_MARKET_DATA")
    assert fr._prepare(tmp_path) == (tmp_path, None) and installed == [{"cache": tmp_path, "lake": None}]


# --- the leak check --------------------------------------------------------------------------------------

def test_the_cut_cache_holds_only_what_opened_or_settled_before_the_instant(tmp_path):
    src = tmp_path / "src"
    (src / "binance" / "klines").mkdir(parents=True)
    (src / "binance" / "funding").mkdir(parents=True)
    t = fr._ms("2024-06-03T08:10:00Z")
    (src / "binance" / "klines" / "BTCUSDT.1h.jsonl").write_text(
        "\n".join(json.dumps([t + d, "1"]) for d in (-7_200_000, -3_600_000, 0, 3_600_000)) + "\n")
    (src / "binance" / "funding" / "BTCUSDT.jsonl").write_text(
        "\n".join(json.dumps({"fundingTime": t + d}) for d in (-1, 0, 1)) + "\n")
    dropped = fr.truncate_cache(src, tmp_path / "cut", "2024-06-03T08:10:00Z")
    kl = [json.loads(x)[0] for x in (tmp_path / "cut" / "binance" / "klines" / "BTCUSDT.1h.jsonl").read_text().splitlines()]
    fu = [json.loads(x)["fundingTime"] for x in (tmp_path / "cut" / "binance" / "funding" / "BTCUSDT.jsonl").read_text().splitlines()]
    assert kl == [t - 7_200_000, t - 3_600_000] and fu == [t - 1] and dropped == 4


def test_the_leak_check_compares_the_fire_on_both_caches_and_restores_the_cache(monkeypatch, tmp_path):
    src = tmp_path / "src"
    (src / "binance" / "klines").mkdir(parents=True)
    installed = []
    monkeypatch.setattr(fr.lake, "install", lambda **k: installed.append(k["cache"]))
    rows = iter([[{"strategy_rule_hash": "h"}], [{"strategy_rule_hash": "h"}],
                 [{"strategy_rule_hash": "h"}], [{"strategy_rule_hash": "LEAK"}]])
    monkeypatch.setattr(fr, "fire", lambda c, d, s, **k: {"status": "fired", "candidates": next(rows)})
    ctx, day = fr.BY_NAME["pooled-1d"], date(2024, 6, 3)
    assert fr.leak_check(ctx, day, cache_root=src, work=tmp_path, state=tmp_path)["identical"] is True
    assert fr.leak_check(ctx, day, cache_root=src, work=tmp_path, state=tmp_path)["identical"] is False
    assert installed[1] == src and installed[3] == src and installed[0] != src
    assert not (tmp_path / "cut-pooled-1d-2024-06-03").exists()


def test_the_anchors_leave_every_forward_trade_room_to_close_before_the_data_ends():
    at = fr.replay_anchors()
    assert at[0] == date(2022, 6, 6) and len(at) == fr.ANCHOR_COUNT
    assert all((b - a).days == fr.ANCHOR_STEP_DAYS for a, b in zip(at, at[1:]))
    longest_1d_hold = timedelta(days=fr.HOLD_ALLOWANCE_BARS)
    assert at[-1] + timedelta(days=fr.FORWARD_DAYS) + longest_1d_hold <= fr.DATA_END
    first, last = fr.fire_span()
    assert first == at[0] - timedelta(days=fr.WARM_START_DAYS) and last == at[-1] - timedelta(days=1)


# --- arm B ---------------------------------------------------------------------------------------------

from scripts.research import cross_section_basket as xs  # noqa: E402
from scripts.research import regime_strategies as rs  # noqa: E402
from scripts.research import regime_strategies_v02 as v2  # noqa: E402


def _btc_daily(n=420):
    import math
    start = date(2021, 1, 1)
    closes = [100.0 * (1.006 ** k if k < 250 else 1.006 ** 250 * 0.993 ** (k - 250)) * (1 + 0.03 * math.sin(k / 9))
              for k in range(n)]
    candles = [{"open_time": f"{(start + timedelta(days=k)).isoformat()}T00:00:00Z",
                "close_time": f"{(start + timedelta(days=k)).isoformat()}T23:59:59.999Z",
                "close": c, "high": c * 1.01} for k, c in enumerate(closes)]
    return start, closes, candles


def test_the_daily_regime_is_the_research_btc_only_label():
    start, closes, candles = _btc_daily()
    market = xs.Market()
    prev = closes[0]
    for k, c in enumerate(closes):
        market.bars.setdefault(rs.BTC, {})[start + timedelta(days=k)] = xs.Bar(open=prev, close=c, quote_volume=1.0)
        prev = c
    research = v2.labels_from(v2.raw_btc_only, rs.Data(market, {}), start, start + timedelta(days=len(closes) - 1))
    ours = {date.fromisoformat(t[:10]): label for t, label in fr.daily_regimes(candles)}
    assert ours == research and len(set(ours.values())) >= 2


def test_a_bar_sees_the_last_closed_daily_label_never_its_own_days():
    start, closes, candles = _btc_daily()
    series = fr.daily_regimes(candles)
    labels = dict(series)
    # a day whose own close changed the label: a bar inside it must still see the day before's
    day = next(date.fromisoformat(t[:10]) for (t0, a), (t, b) in zip(series, series[1:]) if a != b)
    bars = [{"open_time": f"{day.isoformat()}T{h:02d}:00:00Z", "close": 1.0, "high": 1.0} for h in range(24)]
    cols = fr.arm_b_columns({"candles": bars, fr.DAILY_KEY: candles})
    before = labels[f"{(day - timedelta(days=1)).isoformat()}T23:59:59.999Z"]
    assert cols[fr.MKT_REGIME] == [before] * 24


def test_near_high_is_the_close_over_the_ninety_bar_high():
    bars = [{"open_time": str(i), "close": float(i % 50 + 1), "high": float(i % 50 + 2)} for i in range(120)]
    near = fr.arm_b_columns({"candles": bars})[fr.NEAR_HIGH]
    assert near[:89] == [None] * 89
    assert near[100] == pytest.approx((100 % 50 + 1) / max(float(i % 50 + 2) for i in range(11, 101)) - 1)


def test_exactly_the_odd_numbered_specs_carry_a_gate():
    gates = [fr.gate_for({"strategy_id": f"S{n:03d}", "generation_id": "GEN-000123"}) for n in range(1, 9)]
    assert [g is None for g in gates] == [False, True] * 4
    assert set(g for g in gates if g) <= set(fr.REGIMES)
    assert fr.gate_for({"strategy_id": "F-cross", "generation_id": "G"}) is None


def test_arm_b_admits_its_columns_gates_the_draw_adds_the_template_and_restores_everything():
    before = (template_space.NUMERIC_FEATURES, dict(template_space.CATEGORICAL_FEATURES),
              dict(template_space._FEATURE_FEED), fr.backtest.build_feature_rows, fr.generator.build_spec_dict,
              fr.generator.templates_for_timeframe, factory.templates_for_timeframe)
    with fr.arm_b():
        assert fr.NEAR_HIGH in template_space.NUMERIC_FEATURES and fr.MKT_REGIME in template_space.CATEGORICAL_FEATURES
        assert any(t.family == fr.NEAR_HIGH_FAMILY for t in factory.templates_for_timeframe("1d"))
        assert not any(t.family == fr.NEAR_HIGH_FAMILY for t in factory.templates_for_timeframe("1h"))
        template = template_space.templates_for_timeframe("1d")[0]
        params = dict(template.base_params)
        odd = fr.generator.build_spec_dict(template, params, strategy_id="S001", generation_id="GEN-000001")
        even = fr.generator.build_spec_dict(template, params, strategy_id="S002", generation_id="GEN-000001")
        assert odd["entry_rules"]["conditions"][-1]["feature"] == fr.MKT_REGIME
        assert all(c["feature"] != fr.MKT_REGIME for c in even["entry_rules"]["conditions"])
        near = fr.generator.build_spec_dict(fr.NEAR_HIGH_TEMPLATE, dict(fr.NEAR_HIGH_TEMPLATE.base_params),
                                            strategy_id="S001", generation_id="GEN-000001")
        assert [c["feature"] for c in near["entry_rules"]["conditions"]] == [fr.NEAR_HIGH, fr.MKT_REGIME]
        from runtime.mvp_runtime.crypto.strategy import StrategySpec
        assert template_space.validate_strategy(StrategySpec.from_dict(near))["approved_for_backtest"] is True
        assert template_space.validate_strategy(StrategySpec.from_dict(odd))["approved_for_backtest"] is True
    from runtime.mvp_runtime.crypto.strategy import StrategySpec as _Spec
    assert template_space.validate_strategy(_Spec.from_dict(near))["approved_for_backtest"] is False
    after = (template_space.NUMERIC_FEATURES, dict(template_space.CATEGORICAL_FEATURES),
             dict(template_space._FEATURE_FEED), fr.backtest.build_feature_rows, fr.generator.build_spec_dict,
             fr.generator.templates_for_timeframe, factory.templates_for_timeframe)
    assert after == before


def test_arm_b_rows_gain_the_columns_only_when_the_snapshot_carries_btc_daily(monkeypatch):
    start, closes, candles = _btc_daily()
    monkeypatch.setattr(fr.backtest, "build_feature_rows", lambda snap: [{"close": 1.0}])
    monkeypatch.setattr(fr, "arm_b_columns", lambda snap: {fr.MKT_REGIME: ["BULL"], fr.NEAR_HIGH: [-0.02]})
    with fr.arm_b():
        with_daily = fr.backtest.build_feature_rows({"candles": [{}], fr.DAILY_KEY: candles})
        without = fr.backtest.build_feature_rows({"candles": [{}]})
    assert with_daily == [{"close": 1.0, fr.MKT_REGIME: "BULL", fr.NEAR_HIGH: -0.02}]
    assert without == [{"close": 1.0}]

"""C8 factory tests — seeded generation, validation, backtest evidence, promotion door.

The source rules under test: same seed → same batch; the validator fails closed on
unknown features and bad risk:reward; the backtest uses the shared evaluator + exit
math; the factory only ever creates candidates; and the active pool changes only
through the explicit operator door (kill-switch bound, audited)."""

from __future__ import annotations

import json
import random
from itertools import combinations
from datetime import datetime, timedelta, timezone

import pytest

from runtime.mvp_runtime import control, timeutil
from runtime.mvp_runtime.control import ControlState, ControlStore
from runtime.mvp_runtime.crypto import factory, market_data, pool
from runtime.mvp_runtime.crypto.factory import (
    generate_batch,
    backtest_spec,
    fuse_specs,
    mutate_params,
    next_generation_id,
    rank_fusion_parents,
    run_factory,
    templates_for_timeframe,
    validate_strategy,
    FusionRefused,
    ParamSpec,
)
from runtime.mvp_runtime.crypto.strategy import StrategySpec, evaluate_spec
from runtime.mvp_runtime.errors import ToolBlocked
from runtime.mvp_runtime.scheduler import (
    FACTORY_FUSION_PAIRS,
    KIND_FACTORY,
    ScheduleStore,
    build_schedule,
    run_due,
)
from runtime.mvp_runtime.store import LEDGER_REL, RECORDS_FILE, LedgerStore

from scripts.promote_strategy_candidates import run_promotion

NOW = "2026-07-22T12:00:00Z"
NOW_DT = datetime(2026, 7, 22, 12, tzinfo=timezone.utc)


def _spec_dict(**overrides):
    base = {
        "schema_version": "strategy_spec.v1",
        "strategy_id": "S1", "strategy_version": "1.0", "strategy_family": "breakout",
        "symbol_scope": ["BTCUSDT"], "timeframe": "1d", "direction": "long",
        "entry_rules": {"operator": "AND",
                        "conditions": [{"feature": "close", "comparison": ">", "value_from": "ma20"}]},
        "exit_rules": {"stop_model": "atr", "stop_atr": 1.5, "target_atr": 2.0, "max_holding_bars": 10},
        "risk_constraints": {"max_risk_per_trade_R": 1.0},
    }
    base.update(overrides)
    return base


def _trending_snapshot(n=200):
    """Rising closes with a mid-series dip: entries and both exit kinds occur."""
    step = timedelta(days=1)
    last_close = NOW_DT - timedelta(hours=1)
    candles = []
    price = 100.0
    for i in range(n):
        drift = 1.0 if (i // 20) % 3 != 2 else -1.5  # two up-blocks, one down-block
        price = max(10.0, price + drift)
        close_time = last_close - (n - 1 - i) * step
        candles.append({
            "open_time": timeutil.format_iso(close_time - step),
            "open": price - drift, "high": price + 1.5, "low": price - 1.5,
            "close": price, "volume": 10.0 + (i % 7),
            "close_time": timeutil.format_iso(close_time),
        })
    return {"symbol": "BTCUSDT", "timeframe": "1d", "candles": candles, "is_synthetic": False}


# --- generator ----------------------------------------------------------------

def test_same_seed_same_batch():
    a = generate_batch("GEN-001", seed=42, timeframe="1d")
    b = generate_batch("GEN-001", seed=42, timeframe="1d")
    assert a == b  # the source's reproducibility rule
    assert a["accepted_count"] == a["requested_count"] == 4
    assert all(s["created_by"] == "mvp_factory" for s in a["specs"])


def test_different_seed_different_params():
    a = generate_batch("GEN-001", seed=1, timeframe="1d")
    b = generate_batch("GEN-001", seed=2, timeframe="1d")
    assert a["specs"] != b["specs"]


def test_known_hashes_are_never_reminted():
    """Same generation id AND seed, so the second call would re-mint exactly the first
    batch: that is the collision the dedup exists for. (Using two DIFFERENT generation
    ids no longer collides — they now rotate onto different families — so it would
    assert nothing about deduplication.)"""
    a = generate_batch("GEN-001", seed=7, timeframe="1d")
    hashes = frozenset(s["strategy_rule_hash"] for s in a["specs"])
    b = generate_batch("GEN-001", seed=7, timeframe="1d", known_rule_hashes=hashes)
    assert not hashes & {s["strategy_rule_hash"] for s in b["specs"]}
    assert any(r.get("reason") == "duplicate_rule_hash" for r in b["rejected"])


def test_mutation_respects_bounds():
    rng = random.Random(0)
    space = {"x": ParamSpec(1.0, 2.0), "n": ParamSpec(5, 10, integer=True)}
    for _ in range(50):
        out = mutate_params({"x": 1.5, "n": 7}, space, rng)
        assert 1.0 <= out["x"] <= 2.0
        assert isinstance(out["n"], int) and 5 <= out["n"] <= 10


def test_every_family_in_the_library_is_reachable_across_generations():
    """The defect this pins. The picker was ``templates[len(accepted) % len(templates)]``
    and a batch is four specs, so it selected templates[0..3] on EVERY run — 228 of 228
    factory candidates came from those four families while the other sixteen (htf_*,
    oi_*, funding_fade_*, mean_reversion*, macd_momentum*, bollinger_*) sat in the
    library unreachable. Porting a family was invisible work.

    Consecutive generations must walk the whole list, and do it without overlap so the
    walk is a rotation rather than a random sample that might miss one for weeks.

    Mined on a NON-reference symbol, and the choice is load-bearing: the rel_strength_*
    families are deliberately unmintable for the market proxy (their columns are None there),
    so covering the whole library requires a symbol that can express every family. Asking
    ``templates_for_timeframe`` and ``generate_batch`` about the same symbol is what keeps
    this test measuring the rotation rather than the gate — see
    ``test_reference_families_are_not_minted_for_the_reference_symbol`` for the gate."""
    symbol = "ETHUSDT"
    templates = templates_for_timeframe("1h", symbol=symbol)
    families = {t.family for t in templates}
    runs = -(-len(templates) // 4)  # ceil: batches needed to cover the library once
    seen, minted = set(), []
    for n in range(runs):
        batch = generate_batch(f"GEN-{n:03d}", seed=n, symbol=symbol, timeframe="1h")
        got = [spec["strategy_family"] for spec in batch["specs"]]
        minted.extend(got)
        seen.update(got)
    assert seen == families, f"unreachable families: {sorted(families - seen)}"
    # No family repeats until every one has been minted once. Stated over the first
    # ``len(templates)`` mints rather than over all of them, because the final batch
    # overshoots whenever the library size is not a multiple of the batch size — with 26
    # families and batches of 4, seven runs mint 28 specs and two families are necessarily
    # seen twice. That overshoot is arithmetic, not a rotation defect; what the rotation
    # actually owes is that the overshoot is the ONLY source of repeats.
    pass_one = minted[: len(templates)]
    assert len(set(pass_one)) == len(templates), (
        "a family repeated before the rotation had covered the library: "
        f"{sorted({f for f in pass_one if pass_one.count(f) > 1})}"
    )


def test_a_context_covers_its_library_when_generation_ids_stride():
    """The defect the test above cannot see, because it walks GEN-000, GEN-001, GEN-002.

    Production never does. Generation ids are global — `next_generation_id` takes the
    next one across the whole store — and there are 15 scheduled factory contexts, so
    ONE context's own generations stride by 15. `_rotation_offset` multiplied that by
    `count`, and a strided walk over a modulus visits only the multiples of
    `gcd(stride * count, total)`: with stride 15, count 4 and total 36 that is 12, so
    three blocks of four out of nine and **24 of 36 families were permanently
    unreachable for a given context**. Measured on the store 2026-07-31: 440 seeded
    candidates over ~110 generations, median context had minted 16 distinct families.

    So this walks the way the scheduler does. Asserted on the FAMILIES a context can
    reach, not on the offsets, because the offset is an implementation detail and the
    reachable set is the thing that was broken.

    **Both halves are bounded to ONE covering pass, and that bound is the test.** The
    starvation half used to run four passes and assert the generation number still fell
    short, which held at 36 families and stopped holding at 34: whether a strided walk
    *eventually* reaches everything is `gcd(stride * count, total)`, and that number jumps
    around whenever a family joins or leaves the library. Retiring the `volatility_squeeze`
    pair took the gcd from 12 to 2 and the four-pass contrast collapsed — the test said in its
    own failure message that this meant rewrite, so this is that rewrite. "Covers its library
    within one pass" is what the cursor actually promises and what an operator depends on;
    "covers it eventually" is a coincidence of the library's size."""
    symbol, timeframe, contexts = "ETHUSDT", "1h", 15
    templates = templates_for_timeframe(timeframe, symbol=symbol)
    runs = -(-len(templates) // 4)  # ceil: fires needed to cover the library once

    def families_over(fires, *, use_rotation_index):
        seen = set()
        for fire in range(fires):
            generation = 695 + fire * contexts  # the global counter, strided
            batch = generate_batch(
                f"GEN-{generation:03d}", seed=generation, symbol=symbol, timeframe=timeframe,
                rotation_index=fire if use_rotation_index else None,
            )
            seen.update(spec["strategy_family"] for spec in batch["specs"])
        return seen

    # The cursor: one context covers its whole library in ceil(total/count) of its own fires.
    covered = families_over(runs, use_rotation_index=True)
    assert covered == {t.family for t in templates}, (
        f"unreachable with the per-context cursor: {sorted({t.family for t in templates} - covered)}"
    )

    # The generation number, over the SAME fires: short of the library. Pinned as an inequality
    # rather than an exact count — how far short depends on the stride arithmetic and moves
    # whenever a family joins or leaves.
    starved = families_over(runs, use_rotation_index=False)
    assert len(starved) < len(templates), (
        "the global generation number covered the whole library in one pass — if the rotation "
        "arithmetic changed so this is now true, this test is measuring nothing and should be "
        "rewritten (do NOT relax it to more passes: 'covers eventually' is a property of the "
        "library's size, not of the cursor this exists to protect)"
    )


def test_contexts_firing_together_do_not_mint_the_same_block():
    """The half the cursor did not fix: contexts marching in step explore `count` families a day.

    `context_rotation_index` decides how far a context has walked. Every context fires on the
    same schedule, so they all acquire generations at the same rate and the cursor converges —
    measured on the store 2026-08-04, **13 of 20 contexts sat on rotation_index 11**, every
    non-BTC symbol identical across its timeframes. A fire mints `count` CONSECUTIVE families,
    so the whole factory drew one block of four: 2026-08-03's seeded mints were
    `bollinger_breakout` 14, `bollinger_breakdown_short` 14, `macd_momentum` 13,
    `macd_momentum_short` 13 — four families, drawn fourteen times over.

    Grouped by the template LIBRARY rather than by timeframe, which is the honest unit: the
    offset is taken modulo the library's size, so two contexts collapse onto one block only when
    they draw from the same list. BTCUSDT is the standing example — it is the reference symbol,
    so `rel_strength_*` cannot be minted on it and its 1h library is 36 against the other four
    symbols' 38. Grouping by timeframe read that difference as the phase working, and the first
    version of this test asserted a collapse to `count` that BTC's odd library already broke.

    The phased bound is `> count` rather than an exact number — how many distinct families the
    phases reach depends on the library's size and on the hash, and pinning it would make this
    fail on a family being added rather than on the property being broken."""
    symbols = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "DOGEUSDT")
    count, fire = 4, 11  # one fire number, the way the scheduler fires them
    groups: dict[tuple[str, tuple[str, ...]], list[str]] = {}

    for timeframe in ("1h", "4h", "1d"):
        for symbol in symbols:
            library = tuple(t.family for t in templates_for_timeframe(timeframe, symbol=symbol))
            groups.setdefault((timeframe, library), []).append(symbol)

    shared = {key: members for key, members in groups.items() if len(members) > 1}
    assert shared, "no two contexts share a library — this test would be measuring nothing"

    for (timeframe, library), members in shared.items():
        total = len(library)
        families, unphased = set(), set()
        for symbol in members:
            phase = factory.context_rotation_phase(symbol, timeframe, count=count, total=total)
            for offset, sink in (
                (factory._rotation_offset("GEN-999", 0, count, total,
                                          rotation_index=fire, phase=phase), families),
                (factory._rotation_offset("GEN-999", 0, count, total,
                                          rotation_index=fire, phase=0), unphased),
            ):
                sink.update(library[(offset + i) % total] for i in range(count))

        # The defect, pinned so it cannot come back unnoticed: with no phase, every symbol
        # sharing a library mints the identical block.
        assert len(unphased) == count, (
            f"{timeframe}/{members}: expected the unphased rotation to collapse onto one block "
            f"of {count}, got {len(unphased)} — if this no longer holds the phase may be "
            f"redundant, but check WHY before deleting it"
        )
        assert len(families) > count, (
            f"{timeframe}/{members}: still one block of {count} between them "
            f"({sorted(families)}) — the phase is not separating them"
        )


def test_the_rotation_phase_preserves_library_coverage():
    """The property the phase must not buy its breadth with.

    A phase shifts WHERE a context starts; `ceil(total / count)` of its own fires must still
    tile the whole library, because that is what `context_rotation_index` exists to guarantee
    and what an operator depends on when they add a family. It holds for any phase — the walk
    still steps by `count` — and this pins it against an edit that made the phase multiply or
    stride instead of offset."""
    count = 4
    for symbol, timeframe in (("BTCUSDT", "1h"), ("SOLUSDT", "4h"), ("ETHUSDT", "1d")):
        templates = templates_for_timeframe(timeframe, symbol=symbol)
        total = len(templates)
        phase = factory.context_rotation_phase(symbol, timeframe, count=count, total=total)
        seen = set()
        for fire in range(-(-total // count)):
            offset = factory._rotation_offset(
                "GEN-999", 0, count, total, rotation_index=fire, phase=phase
            )
            seen.update(templates[(offset + i) % total].family for i in range(count))
        assert seen == {t.family for t in templates}, (
            f"{symbol} {timeframe}: the phase cost this context coverage — unreachable in one "
            f"pass: {sorted({t.family for t in templates} - seen)}"
        )


def test_the_rotation_phase_is_a_stable_property_of_the_context():
    """It must not move when the store does.

    The phase separates contexts; one derived from anything that changes — a candidate count,
    a timestamp, a machine — would re-synchronise them on the next prune or re-import, which is
    the failure it exists to prevent. So: a pure function of (symbol, timeframe), and bounded by
    the number of offsets the walk actually visits, since a larger phase only names an offset
    the rotation already reaches."""
    import math

    assert factory.context_rotation_phase("BTCUSDT", "1h", count=4, total=38) == \
        factory.context_rotation_phase("BTCUSDT", "1h", count=4, total=38)
    assert factory.context_rotation_phase("BTCUSDT", "1h", count=4, total=38) != \
        factory.context_rotation_phase("BTCUSDT", "4h", count=4, total=38)

    for total in (30, 32, 36, 38):
        cycle = total // math.gcd(4, total)
        for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "DOGEUSDT"):
            phase = factory.context_rotation_phase(symbol, "1h", count=4, total=total)
            assert 0 <= phase < cycle, f"{symbol}/{total}: phase {phase} outside [0, {cycle})"

    # An empty library is the degenerate case the offset helper already guards; the phase must
    # not raise on it either (a factory with no mintable family is a refusal, not a crash).
    assert factory.context_rotation_phase("BTCUSDT", "1h", count=4, total=0) == 0


def test_the_rotation_cursor_counts_fires_not_candidates():
    """One fire mints a whole batch under a single generation id, so a batch is one step
    of the rotation and not `count` steps. Fused children ride the same id and must not
    advance it either — they are the same fire."""
    def record(generation_id, symbol="ETHUSDT", timeframe="1h"):
        return {"generation_id": generation_id,
                "strategy_spec": {"symbol_scope": [symbol], "timeframe": timeframe,
                                  "generation_id": generation_id}}

    store = [record("GEN-001"), record("GEN-001"), record("GEN-001"), record("GEN-001"),
             record("GEN-016"), record("GEN-016")]  # two fires: four seeded + two fused
    assert factory.context_rotation_index(store, symbol="ETHUSDT", timeframe="1h") == 2

    # Another context's rows are not this context's fires — the bug in miniature.
    store += [record("GEN-002", timeframe="4h"), record("GEN-003", symbol="SOLUSDT")]
    assert factory.context_rotation_index(store, symbol="ETHUSDT", timeframe="1h") == 2
    assert factory.context_rotation_index(store, symbol="ETHUSDT", timeframe="4h") == 1
    assert factory.context_rotation_index(store, symbol="SOLUSDT", timeframe="1h") == 1
    # A context that has never fired starts at zero rather than raising.
    assert factory.context_rotation_index(store, symbol="BNBUSDT", timeframe="1d") == 0
    # Malformed rows are skipped, never fatal: a factory that refuses to mine because one
    # legacy row lost its spec is a worse failure than a cursor one short.
    assert factory.context_rotation_index(
        [*store, {"generation_id": "GEN-999"}, {"strategy_spec": None}],
        symbol="ETHUSDT", timeframe="1h") == 2


def test_run_factory_steps_the_rotation_on_its_own_context():
    """The seam: `run_factory` must COUNT the store it is already given and pass the
    cursor down. Both stages were tested and the join was not — which is how #201 shipped.

    Two runs of the same context over stores differing only in how many times that context
    has already fired must mint different families; a run whose store belongs to a
    DIFFERENT context must not be advanced by it."""
    snapshot = _trending_snapshot()

    def families(existing):
        result = run_factory(snapshot, active_pool={}, existing_candidates=existing,
                             now=NOW, count=4)
        return [c["strategy_spec"]["strategy_family"] for c in result["candidates"]]

    def rows(generation_id, symbol="BTCUSDT", timeframe="1d"):
        return {"generation_id": generation_id,
                "strategy_spec": {"symbol_scope": [symbol], "timeframe": timeframe,
                                  "generation_id": generation_id}}

    first = families([])
    second = families([rows("GEN-001")])
    assert first != second, "a second fire of the same context re-minted the same families"

    # Rows from other contexts leave this context's cursor where it was.
    assert families([rows("GEN-001"), rows("GEN-002", timeframe="4h"),
                     rows("GEN-003", symbol="SOLUSDT")]) == second


def test_the_rotation_is_deterministic():
    a = generate_batch("GEN-042", seed=7, timeframe="1h")
    b = generate_batch("GEN-042", seed=7, timeframe="1h")
    assert a == b
    assert ([s["strategy_family"] for s in a["specs"]]
            != [s["strategy_family"] for s in generate_batch("GEN-043", seed=7, timeframe="1h")["specs"]])


def test_generated_specs_carry_generation_lineage():
    batch = generate_batch("GEN-042", seed=9, timeframe="1d")
    assert all(s["generation_id"] == "GEN-042" for s in batch["specs"])


# --- validator ----------------------------------------------------------------

def test_unknown_feature_blocks():
    # open_interest is a real source column whose feed was never ported — it must
    # stay unmintable (funding_zscore graduated INTO the registry with C9).
    spec = StrategySpec.from_dict(_spec_dict(entry_rules={
        "operator": "AND", "conditions": [{"feature": "open_interest", "comparison": ">", "value": 1.0}],
    }))
    verdict = validate_strategy(spec)
    assert verdict["approved_for_backtest"] is False
    assert "BLOCK_UNKNOWN_FEATURE" in verdict["block_reasons"]


@pytest.mark.parametrize("feature", [
    "liquidation_total", "long_liquidation", "short_liquidation", "liquidation_spike_ratio",
])
def test_liquidation_features_are_mintable(feature):
    # Held out while the Coinalyze feed was unconfigured; admitted 2026-07-24.
    spec = StrategySpec.from_dict(_spec_dict(entry_rules={
        "operator": "AND", "conditions": [{"feature": feature, "comparison": ">", "value": 1.0}],
    }))
    assert validate_strategy(spec)["approved_for_backtest"] is True


def test_the_three_added_liquidation_features_fail_closed_without_a_feed():
    """Why admitting them is safe: no feed means None, and None never matches.

    This is the whole argument for the widening. ``liquidation_spike_ratio`` does NOT
    share it — with no feed it falls back to a constant 0.0, so a ``< x`` condition on
    it matches fabricated data. That hazard predates the widening; this test pins the
    distinction so a later change to the fallbacks cannot erase it silently.
    """
    from runtime.mvp_runtime.crypto.features import latest_feature_row
    from runtime.mvp_runtime.crypto.market_data import MockMarketDataCollector, collect_market_data
    from runtime.mvp_runtime.crypto.strategy import evaluate_spec

    snapshot, _ = collect_market_data(
        "BTCUSDT", "1h", collector=MockMarketDataCollector(), now=NOW, limit=120
    )
    row = latest_feature_row(snapshot)  # no "liquidations" key — the default posture

    for feature in ("liquidation_total", "long_liquidation", "short_liquidation"):
        assert row[feature] is None
        for comparison, value in ((">", -1e12), ("<", 1e12)):  # both directions
            spec = StrategySpec.from_dict(_spec_dict(entry_rules={
                "operator": "AND",
                "conditions": [{"feature": feature, "comparison": comparison, "value": value}],
            }))
            assert evaluate_spec(spec, row).matched is False, f"{feature} {comparison} matched"

    # The pre-existing exception, asserted rather than left as folklore.
    assert row["liquidation_spike_ratio"] == 0.0
    permissive = StrategySpec.from_dict(_spec_dict(entry_rules={
        "operator": "AND",
        "conditions": [{"feature": "liquidation_spike_ratio", "comparison": "<", "value": 1.0}],
    }))
    assert evaluate_spec(permissive, row).matched is True  # matches the constant, not data


def test_bad_reward_risk_blocks():
    spec = StrategySpec.from_dict(_spec_dict(
        exit_rules={"stop_model": "atr", "stop_atr": 2.0, "target_atr": 1.0, "max_holding_bars": 10}))
    assert "BLOCK_INVALID_RISK_REWARD" in validate_strategy(spec)["block_reasons"]


def test_categorical_ordering_comparison_blocks():
    spec = StrategySpec.from_dict(_spec_dict(entry_rules={
        "operator": "AND", "conditions": [{"feature": "market_regime", "comparison": ">", "value": "RANGE"}],
    }))
    assert "BLOCK_INVALID_COMPARISON" in validate_strategy(spec)["block_reasons"]


def test_valid_spec_approved():
    spec = StrategySpec.from_dict(_spec_dict())
    verdict = validate_strategy(spec)
    assert verdict["approved_for_backtest"] is True and verdict["block_reasons"] == []


def test_all_ported_templates_validate():
    for template in templates_for_timeframe("1d"):
        batch = generate_batch("GEN-001", seed=3, count=1, timeframe="1d")
        assert batch["accepted_count"] == 1  # every family passes its own validator


# --- volatility-regime families -----------------------------------------------

# The squeeze pair is deliberately absent — retired from the rotation 2026-08-02
# (`factory.RETIRED_FAMILIES`). The tests below iterate `TEMPLATES` and would silently cover
# nothing if this set named families that are not minted, so it names only what is.
VOLATILITY_FAMILIES = frozenset({"volatility_expansion_long", "volatility_expansion_short"})

# The columns these families exist to avoid. Each is a real, mintable member of
# NUMERIC_FEATURES and each is a LEVEL: `atr` is in price units, the other two are
# fractions of price that run ~0.2% at 15m and ~3% at 1d. A threshold mined on any of
# them means a different thing at every rung of the ladder the template is retimed onto.
_VOLATILITY_LEVEL_COLUMNS = frozenset({"atr", "atr_pct_of_price", "bb_width_pct"})


def _volatility_regime_snapshot(n=600):
    """Alternating calm and violent blocks, one of the violent ones falling.

    ``_trending_snapshot`` cannot serve here: its highs and lows are a constant ±1.5
    around the close, so ATR is flat, every rolling rank ties at ~0.5, and a family
    gated on ``atr_percentile >= 0.7`` would take zero trades — which would look
    exactly like a broken family rather than a fixture with no volatility in it."""
    step = timedelta(days=1)
    last_close = NOW_DT - timedelta(hours=1)
    candles = []
    price = 100.0
    for i in range(n):
        violent = (i // 25) % 2 == 1
        drift = 2.0 if violent else 0.25
        span = 4.0 if violent else 0.4
        if (i // 25) % 4 == 3:  # one falling violent block, so the short legs can enter
            drift = -drift
        price = max(10.0, price + drift)
        close_time = last_close - (n - 1 - i) * step
        candles.append({
            "open_time": timeutil.format_iso(close_time - step),
            "open": price - drift, "high": price + span, "low": price - span,
            "close": price, "volume": 10.0 + (i % 7),
            "close_time": timeutil.format_iso(close_time),
        })
    return {"symbol": "BTCUSDT", "timeframe": "1d", "candles": candles, "is_synthetic": False}


def _template_spec(template, timeframe="1d"):
    return StrategySpec.from_dict(_spec_dict(
        strategy_family=template.family, direction=template.direction, timeframe=timeframe,
        entry_rules={"operator": "AND",
                     "conditions": template.entry_builder(template.base_params)},
        exit_rules={"stop_model": "atr", "stop_atr": 1.2, "target_atr": 3.0,
                    "max_holding_bars": 24},
    ))


def test_volatility_families_close_trades_on_a_market_that_has_regimes():
    """The hazard these families are most exposed to is the one `POSITIONING_FAMILIES`
    documents: a family whose conditions are never *determined* mints, backtests, takes
    zero trades and is retired as FRAGILE — blamed for a window it could not read.

    A percentile column is the sharpest version of that risk, because it is None until
    `rolling_percentile` has its `min_periods` and then ranks against a window that may
    have no spread in it at all. So each family has to be shown closing real trades on a
    market that actually changes volatility, not merely parsing."""
    snapshot = _volatility_regime_snapshot()
    for template in factory.TEMPLATES:
        if template.family not in VOLATILITY_FAMILIES:
            continue
        evidence = backtest_spec(_template_spec(template), snapshot)
        assert evidence["closed_count"] >= factory.MIN_TRADES_PER_WINDOW, (
            f"{template.family} closed {evidence['closed_count']} trades — a family that "
            "cannot trade its own premise is noise in the rotation, not diversity"
        )


def test_volatility_families_mine_percentiles_and_never_levels():
    """The rule that makes these families retimeable at all, pinned structurally.

    `atr`, `atr_pct_of_price` and `bb_width_pct` are all mintable — nothing in the
    validator refuses them — so the only thing standing between this family and a
    threshold that silently means something different at every timeframe is a decision.
    This test is that decision, written down where breaking it fails."""
    for template in factory.TEMPLATES:
        if template.family not in VOLATILITY_FAMILIES:
            continue
        read = set()
        for condition in template.entry_builder(template.base_params):
            read.add(condition["feature"])
            if condition.get("value_from"):
                read.add(condition["value_from"])
        assert not read & _VOLATILITY_LEVEL_COLUMNS, (
            f"{template.family} mines a volatility LEVEL ({sorted(read & _VOLATILITY_LEVEL_COLUMNS)}); "
            "a level is not comparable across the ladder this template is retimed onto"
        )
        assert read & {"atr_percentile", "bb_width_percentile"}, (
            f"{template.family} is a volatility family that reads no volatility rank"
        )


def test_the_expansion_filter_actually_excludes_the_quiet_bars():
    """The families trade (above) and read the right column (above); this pins that the
    column is doing the work. A row identical but for a calm `atr_percentile` must not
    enter — otherwise the filter is decoration and the family is `breakout` renamed."""
    template = next(t for t in factory.TEMPLATES if t.family == "volatility_expansion_long")
    spec = _template_spec(template)
    trending = {"close": 110.0, "ma20": 105.0, "ma50": 100.0}
    assert evaluate_spec(spec, {**trending, "atr_percentile": 0.95}).matched is True
    assert evaluate_spec(spec, {**trending, "atr_percentile": 0.10}).matched is False
    # Absent, not merely low: the fail-closed evaluator must leave it indeterminate rather
    # than treat a missing rank as a passing one.
    assert evaluate_spec(spec, trending).matched is False


def test_scheduled_fusion_never_outgrows_the_seeded_rotation():
    """`FACTORY_FUSION_PAIRS` was raised to 4 on the store's own evidence (crossover
    reaches ROBUST at 13.6% against the seeded rotation's 5.5%), and the bound on raising
    it further is not arithmetic — it is that the seeded rotation is the ONLY path by
    which a newly added family enters the store at all. Fusion draws exclusively on
    lineages that are already there. Let it exceed the seeded half of a fire and every
    family added after today competes for a shrinking remainder."""
    assert 0 < FACTORY_FUSION_PAIRS <= factory.DEFAULT_BATCH_SIZE


# --- backtest -----------------------------------------------------------------

def test_backtest_is_deterministic_and_produces_outcomes():
    spec = StrategySpec.from_dict(_spec_dict())
    snapshot = _trending_snapshot()
    a = backtest_spec(spec, snapshot)
    b = backtest_spec(spec, snapshot)
    assert a == b
    assert a["closed_count"] > 0  # the trending fixture must actually trade
    assert a["score_basis"] == "robustness_score_v1"  # C8b: anti-overfit score
    assert a["champion_score"] == a["robustness"]["robustness_score"]
    assert a["robustness"]["verdict"] in {"ROBUST", "PROVISIONAL", "FRAGILE"}
    # bars_replayed is what the SCORE saw; the rest is the untouched holdout tail.
    assert a["bars_replayed"] == factory.holdout_split_index(200) == 140
    assert a["holdout"]["bars"] == 200 - 140
    # M4a: realized payoff legs ride in the evidence for the promotion ranking.
    assert "avg_win_R" in a and "avg_loss_R" in a
    assert a["avg_win_R"] >= 0.0 and a["avg_loss_R"] >= 0.0


def test_backtest_never_enters_on_indeterminate_features():
    # ma50 needs 50 bars; a 30-bar window leaves it indeterminate on every row,
    # so a spec referencing it can never enter (the evaluator's fail-closed rule).
    spec = StrategySpec.from_dict(_spec_dict(entry_rules={
        "operator": "AND", "conditions": [{"feature": "close", "comparison": ">", "value_from": "ma50"}],
    }))
    result = backtest_spec(spec, _trending_snapshot(30))
    assert result["closed_count"] == 0


# --- run_factory --------------------------------------------------------------

def test_run_factory_produces_evidence_backed_candidates():
    result = run_factory(_trending_snapshot(), active_pool={"active_strategies": []},
                         existing_candidates=[], now=NOW)
    assert result["generation_id"] == "GEN-001"
    # `accepted_count` is a MINT-time count (generated + validated) and `candidates` is what
    # survived the supply check that runs after it, so the two are equal only when this fixture
    # happens to supply every column the rotation's block reads. It is a bare OHLCV snapshot, so
    # a block containing an `xs_*` family yields fewer candidates than specs — correctly, and
    # `unsuppliable_features` records each one in `rejected`. That was a coincidence of where the
    # cursor landed rather than a promise, and it stopped holding on 2026-08-04 when retiring two
    # families shifted every later index by two.
    #
    # So the invariant is asserted instead of the coincidence: the two counts must RECONCILE
    # through `rejected`, which is the property that would actually be broken by a candidate
    # going missing silently — the failure the equality was standing in for.
    assert result["accepted_count"] == 4
    starved = [r for r in result["rejected"] if "never supplies" in r.get("reason", "")]
    seeded = len(result["candidates"]) - result["fused_count"]
    assert seeded + len(starved) == result["accepted_count"]
    assert seeded >= 1, "the fixture must still exercise the evidence path"
    for c in result["candidates"]:
        assert c["provenance"] == "mvp_factory" and c["status"] == "BACKTESTED"
        assert c["backtest_evidence"]["strategy_rule_hash"] == c["strategy_rule_hash"]
        assert c["evidence_input_sha256"].startswith("sha256:")
        # Stored candidate_id equals the lineage-derived one (the promotion key).
        assert c["candidate_id"] == pool.derive_candidate_id(c)
    assert len({c["candidate_id"] for c in result["candidates"]}) == len(result["candidates"])


def test_run_factory_is_reproducible_from_inputs():
    snapshot = _trending_snapshot()
    a = run_factory(snapshot, active_pool={"active_strategies": []}, existing_candidates=[], now=NOW)
    b = run_factory(snapshot, active_pool={"active_strategies": []}, existing_candidates=[], now=NOW)
    assert a == b  # seed derives from the candle window, not the clock


def test_generation_number_advances_past_pool_and_candidates():
    existing = [{"generation_id": "GEN-068"}, {"strategy_spec": {"generation_id": "GEN-070"}}]
    assert next_generation_id(existing) == "GEN-071"
    result = run_factory(_trending_snapshot(), active_pool={"active_strategies": []},
                         existing_candidates=existing, now=NOW)
    assert result["generation_id"] == "GEN-071"


# --- scheduler template -------------------------------------------------------

def test_scheduler_factory_fire_appends_candidates_and_ledgers(tmp_path, monkeypatch):
    monkeypatch.delenv("MVP_MARKET_DATA", raising=False)
    schedule = build_schedule(kind=KIND_FACTORY, request="", interval_seconds=86400,
                              created_by="op", now="2026-07-22T10:00:00Z")
    store = ScheduleStore(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.add(schedule)
    ledger = LedgerStore(tmp_path / LEDGER_REL)
    summary = run_due(store, now="2026-07-23T11:00:00Z", control_store=ControlStore(tmp_path),
                      ledger=ledger, repo_root=tmp_path)
    assert summary["fired"] == 1
    assert summary["results"][0]["status"].startswith("generated=")
    candidates = pool.read_candidates(tmp_path)
    assert len(candidates) == 4  # mock collector data is fine for MINING (not trading)
    rows = [json.loads(line) for line in
            (tmp_path / LEDGER_REL / RECORDS_FILE).read_text(encoding="utf-8").splitlines()]
    assert any(r["kind"] == "crypto_factory" for r in rows)


def test_scheduled_factory_fire_attempts_fusion_over_durable_parents(tmp_path, monkeypatch):
    """The scheduler passes a non-zero ``fusion_pairs``, so the SECOND fire — with the
    first fire's candidates durable as parents — must actually attempt crossover.
    The machinery shipped dormant: every scheduled call left the default 0, so no
    fused child was ever minted. Deterministic either way it lands: the recorded
    factory result carries fused children (parent lineage attached) or explicit
    ``fusion_rejected`` reasons — dormant fusion produced neither."""
    monkeypatch.delenv("MVP_MARKET_DATA", raising=False)
    schedule = build_schedule(kind=KIND_FACTORY, request="", interval_seconds=86400,
                              created_by="op", now="2026-07-22T10:00:00Z")
    store = ScheduleStore(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.add(schedule)
    ledger = LedgerStore(tmp_path / LEDGER_REL)
    run_due(store, now="2026-07-23T11:00:00Z", control_store=ControlStore(tmp_path),
            ledger=ledger, repo_root=tmp_path)          # fire 1: parents become durable
    summary = run_due(store, now="2026-07-24T12:00:00Z", control_store=ControlStore(tmp_path),
                      ledger=ledger, repo_root=tmp_path)  # fire 2: fusion draws on them
    assert summary["fired"] == 1
    assert "fused=" in summary["results"][0]["status"]
    rows = [json.loads(line) for line in
            (tmp_path / LEDGER_REL / RECORDS_FILE).read_text(encoding="utf-8").splitlines()]
    second = [r["record"] for r in rows if r["kind"] == "crypto_factory"][-1]
    fused = [c for c in second["candidates"] if c.get("parent_candidate_ids")]
    assert fused or second["fusion_rejected"], "fusion was never attempted"
    for child in fused:
        assert child["provenance"] == "mvp_factory_fusion"
        assert len(child["parent_candidate_ids"]) == 2


# --- the promotion door -------------------------------------------------------

def _seed_candidates(tmp_path):
    result = run_factory(_trending_snapshot(), active_pool={"active_strategies": []},
                         existing_candidates=[], now=NOW)
    pool.append_candidates(result["candidates"], root=tmp_path)
    return [c["strategy_id"] for c in result["candidates"]]


def test_promotion_installs_selected_candidates(tmp_path):
    # `allow_oversized_pool` because one factory run mines every candidate for the SAME
    # context, so promoting two of them is exactly what the per-context cap refuses. That
    # refusal has its own tests (test_mvp_runtime_crypto_promotion.py); the subject here is
    # that the door installs what was selected, and the escape keeps it that.
    ids = _seed_candidates(tmp_path)
    summary = run_promotion(selectors=ids[:2], promoted_by="Thomas", reason="reviewed",
                            keep_active=False, root=tmp_path, now=NOW, without_approval=True,
                            allow_oversized_pool=True)
    assert summary["pool_size"] == 2
    active = pool.load_active_pool(tmp_path)
    assert [e["strategy_id"] for e in active["active_strategies"]] == ids[:2]
    assert all(e["status"] == "PAPER_ACTIVE" for e in active["active_strategies"])
    # Audited on the control ledger.
    control_text = (tmp_path / LEDGER_REL / "control_events.jsonl").read_text(encoding="utf-8")
    assert "crypto_strategy_promotion_event.v0" in control_text


def test_promotion_keep_active_adds(tmp_path):
    ids = _seed_candidates(tmp_path)
    run_promotion(selectors=ids[:1], promoted_by="Thomas", reason="r",
                  keep_active=False, root=tmp_path, now=NOW, without_approval=True)
    run_promotion(selectors=ids[1:2], promoted_by="Thomas", reason="r",
                  keep_active=True, root=tmp_path, now=NOW, without_approval=True,
                  allow_oversized_pool=True)   # same context as the incumbent; see above
    active = pool.load_active_pool(tmp_path)
    assert len(active["active_strategies"]) == 2


def test_promotion_refused_while_killed(tmp_path):
    ids = _seed_candidates(tmp_path)
    store = ControlStore(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(
        json.dumps(ControlState(mode=control.KILLED, updated_by="op", updated_at=NOW, reason="t").as_record()),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit) as exc:
        run_promotion(selectors=ids[:1], promoted_by="Thomas", reason="r",
                      keep_active=False, root=tmp_path, now=NOW)
    assert "BLOCKED" in str(exc.value)
    assert pool.load_active_pool(tmp_path) == {"active_strategies": []}


def test_promotion_refuses_unknown_candidate(tmp_path):
    _seed_candidates(tmp_path)
    with pytest.raises(SystemExit):
        run_promotion(selectors=["S_NOPE"], promoted_by="Thomas", reason="r",
                      keep_active=False, root=tmp_path, now=NOW)


# --- candidate lineage (fusion groundwork) ------------------------------------

def _lineage_row(parent_ids, derivation="mutation", seed_tag="x"):
    """A minimal candidate row claiming a derivation from ``parent_ids``."""
    return {
        "strategy_id": "S900",
        "strategy_rule_hash": f"sha256:{seed_tag * 8}",
        "generation_id": "GEN-900",
        "evidence_input_sha256": "sha256:" + "e" * 8,
        "derivation_type": derivation,
        "parent_candidate_ids": parent_ids,
    }


def test_factory_records_are_seeded_with_empty_parents():
    result = run_factory(_trending_snapshot(), active_pool={"active_strategies": []},
                         existing_candidates=[], now=NOW)
    assert result["candidates"]
    for c in result["candidates"]:
        assert c["derivation_type"] == "seeded_template"
        assert c["parent_candidate_ids"] == []


def test_parented_row_roundtrips_when_parents_are_durable(tmp_path):
    _seed_candidates(tmp_path)
    parents = [pool.candidate_id(r) for r in pool.read_candidates(tmp_path)[:2]]
    pool.append_candidates([_lineage_row(parents, derivation="crossover")], root=tmp_path)
    rows = pool.read_candidates(tmp_path)
    assert rows[-1]["parent_candidate_ids"] == parents
    assert rows[-1]["derivation_type"] == "crossover"


def test_legacy_rows_without_lineage_fields_still_append(tmp_path):
    row = _lineage_row([], seed_tag="l")
    del row["derivation_type"], row["parent_candidate_ids"]
    assert pool.append_candidates([row], root=tmp_path) == 1


def test_unknown_parent_refused_and_nothing_written(tmp_path):
    _seed_candidates(tmp_path)
    before = len(pool.read_candidates(tmp_path))
    with pytest.raises(Exception) as exc:
        pool.append_candidates([_lineage_row(["cand-nope"])], root=tmp_path)
    assert "UNKNOWN_PARENT_CANDIDATE" in str(exc.value)
    assert len(pool.read_candidates(tmp_path)) == before  # all-or-nothing


@pytest.mark.parametrize("mutant, reason", [
    ({"derivation_type": "alien"}, "unknown derivation_type"),
    ({"derivation_type": "seeded_template"}, "admits 0..0 parents"),          # parents ride along
    ({"parent_candidate_ids": None}, "list of non-empty ids"),
    ({"parent_candidate_ids": ["p", "p"]}, "duplicate"),
    ({"parent_candidate_ids": ["only-one"], "derivation_type": "crossover"}, "admits 2+"),
])
def test_incoherent_lineage_refused(tmp_path, mutant, reason):
    _seed_candidates(tmp_path)
    known = pool.candidate_id(pool.read_candidates(tmp_path)[0])
    row = {**_lineage_row([known]), **mutant}
    with pytest.raises(Exception) as exc:
        pool.append_candidates([row], root=tmp_path)
    assert "CANDIDATE_LINEAGE_INVALID" in str(exc.value)
    assert reason.split()[0].lower() in str(exc.value).lower()


def test_parents_without_derivation_type_refused(tmp_path):
    _seed_candidates(tmp_path)
    known = pool.candidate_id(pool.read_candidates(tmp_path)[0])
    row = _lineage_row([known])
    del row["derivation_type"]
    with pytest.raises(Exception) as exc:
        pool.append_candidates([row], root=tmp_path)
    assert "CANDIDATE_LINEAGE_INVALID" in str(exc.value)


# --- fusion: crossover of two proven lineages ---------------------------------

def _parent_spec(strategy_id, conditions, **overrides):
    return StrategySpec.from_dict(_spec_dict(
        strategy_id=strategy_id,
        entry_rules={"operator": "AND", "conditions": conditions},
        **overrides,
    ))


_CLOSE_OVER_MA20 = {"feature": "close", "comparison": ">", "value_from": "ma20"}
_MA20_OVER_MA50 = {"feature": "ma20", "comparison": ">", "value_from": "ma50"}


def test_fusion_is_order_independent():
    a = _parent_spec("S1", [_CLOSE_OVER_MA20])
    b = _parent_spec("S2", [{"feature": "adx", "comparison": ">=", "value": 25.0}])
    left = fuse_specs(a, b, strategy_id="S9", generation_id="GEN-9")
    right = fuse_specs(b, a, strategy_id="S9", generation_id="GEN-9")
    assert left.to_dict() == right.to_dict()
    assert left.strategy_rule_hash == right.strategy_rule_hash


def test_fused_entry_is_the_deduplicated_union_under_and():
    a = _parent_spec("S1", [_CLOSE_OVER_MA20, {"feature": "adx", "comparison": ">=", "value": 25.0}])
    b = _parent_spec("S2", [_CLOSE_OVER_MA20, {"feature": "rsi", "comparison": "<=", "value": 55.0}])
    child = fuse_specs(a, b, strategy_id="S9", generation_id="GEN-9")
    assert child.entry_rules.operator == "AND"
    # The shared condition appears once; the child is strictly more selective.
    assert sorted(c.feature for c in child.entry_rules.conditions) == ["adx", "close", "rsi"]
    assert len(child.entry_rules.conditions) == 3
    assert child.strategy_family == "breakout"  # shared family collapses


@pytest.mark.parametrize("overrides, reason", [
    ({"direction": "short"}, "direction_mismatch"),
    ({"timeframe": "4h"}, "timeframe_mismatch"),
    ({"symbol_scope": ["ETHUSDT"]}, "symbol_scope_mismatch"),
    ({"venue": market_data.HYPERLIQUID}, "venue_mismatch"),
])
def test_fusion_refuses_parents_that_disagree_on_context(overrides, reason):
    a = _parent_spec("S1", [_CLOSE_OVER_MA20])
    b = _parent_spec("S2", [_MA20_OVER_MA50], **overrides)
    with pytest.raises(FusionRefused) as exc:
        fuse_specs(a, b, strategy_id="S9", generation_id="GEN-9")
    assert exc.value.reason == reason


def test_a_fused_child_carries_the_venue_it_was_mined_from():
    """Fusion is the second minting path, and it was silent about the venue.

    `build_spec_dict` records it; this one built a spec dict without it, so the child took
    `StrategySpec`'s migration default and every fused child read as binance_futures
    whatever its parents were — the separation the field exists to prevent, reintroduced
    by the one caller that did not set it.
    """
    a = _parent_spec("S1", [_CLOSE_OVER_MA20], venue=market_data.HYPERLIQUID)
    b = _parent_spec("S2", [_MA20_OVER_MA50], venue=market_data.HYPERLIQUID)
    child = fuse_specs(a, b, strategy_id="S9", generation_id="GEN-9")
    assert child.venue == market_data.HYPERLIQUID
    # And the child is judged against that venue, not against the default one.
    assert factory.validate_strategy(child)["approved_for_backtest"]


def test_a_cross_venue_fusion_cannot_smuggle_a_feature_into_a_venue_lacking_it():
    """Why this is a refusal and not a resolution.

    The union of two parents' conditions is judged against ONE vocabulary, and there is no
    correct answer to which. It passed until now only because hyperliquid's vocabulary is a
    subset of binance's — an accident of today's venue list. This pins the shape rather than
    the accident: the conditions never get merged at all.
    """
    binance = _parent_spec(
        "S1", [{"feature": "taker_flow_zscore", "comparison": ">", "value": 1.0}]
    )
    hyperliquid = _parent_spec("S2", [_MA20_OVER_MA50], venue=market_data.HYPERLIQUID)
    with pytest.raises(FusionRefused) as exc:
        fuse_specs(hyperliquid, binance, strategy_id="S9", generation_id="GEN-9")
    assert exc.value.reason == "venue_mismatch"


def test_stored_parents_without_a_venue_still_fuse():
    # Both read as binance_futures, so the new precondition is satisfied rather than
    # tripped: 1020 stored candidates predate the field and must keep fusing.
    stored_a, stored_b = _spec_dict(strategy_id="S1"), _spec_dict(strategy_id="S2")
    assert "venue" not in stored_a and "venue" not in stored_b
    child = fuse_specs(
        StrategySpec.from_dict({**stored_a, "entry_rules": {
            "operator": "AND", "conditions": [_CLOSE_OVER_MA20]}}),
        StrategySpec.from_dict({**stored_b, "entry_rules": {
            "operator": "AND", "conditions": [_MA20_OVER_MA50]}}),
        strategy_id="S9", generation_id="GEN-9",
    )
    assert child.venue == market_data.BINANCE_FUTURES


def test_fusion_refuses_an_or_parent():
    a = _parent_spec("S1", [_CLOSE_OVER_MA20])
    b = StrategySpec.from_dict(_spec_dict(
        strategy_id="S2",
        entry_rules={"operator": "OR", "conditions": [_MA20_OVER_MA50, _CLOSE_OVER_MA20]},
    ))
    with pytest.raises(FusionRefused) as exc:
        fuse_specs(a, b, strategy_id="S9", generation_id="GEN-9")
    assert exc.value.reason == "non_and_parent"  # AND-union would change what OR meant


def test_fusion_refuses_when_the_union_exceeds_the_condition_cap():
    feats_a = ["close", "ma20", "ma50", "adx", "rsi"]
    feats_b = ["macd", "atr", "volume", "roc_4"]
    a = _parent_spec("S1", [{"feature": f, "comparison": ">=", "value": 1.0} for f in feats_a])
    b = _parent_spec("S2", [{"feature": f, "comparison": ">=", "value": 1.0} for f in feats_b])
    with pytest.raises(FusionRefused) as exc:
        fuse_specs(a, b, strategy_id="S9", generation_id="GEN-9")
    assert exc.value.reason == "too_many_conditions"


def _conditions(*features):
    return [{"feature": f, "comparison": ">=", "value": 1.0} for f in features]


def test_fusion_refuses_the_band_that_has_never_produced_a_judgeable_holdout():
    """Exactly 8 conditions is legal and worthless: 0 of 22 such mints on the current cost
    basis, and 0 of 25 across the whole store, closed enough holdout trades to be judged.

    A union under AND only narrows, so this is the end of a monotone slide the store measures
    at 4 conditions 81% judgeable, 5 64%, 6 30%, 7 16%, 8 zero.
    """
    a = _parent_spec("S1", _conditions("close", "ma20", "ma50", "adx"))
    b = _parent_spec("S2", _conditions("macd", "atr", "volume", "roc_4"))
    with pytest.raises(FusionRefused) as exc:
        fuse_specs(a, b, strategy_id="S9", generation_id="GEN-9")
    assert exc.value.reason == "holdout_unjudgeable"


def test_the_band_below_it_is_still_minted():
    """Only the zero-yield band is cut. 7 conditions yields 16% and 16% is not none — cutting
    there would be the wider decision F1 states and does not take."""
    a = _parent_spec("S1", _conditions("close", "ma20", "ma50", "adx"))
    b = _parent_spec("S2", _conditions("macd", "atr", "volume"))
    child = fuse_specs(a, b, strategy_id="S9", generation_id="GEN-9")
    assert len(child.entry_rules.conditions) == factory.MAX_FUSION_ENTRY_CONDITIONS == 7


def test_the_fusion_bound_can_never_be_looser_than_the_validator_bound():
    """The two answer different questions — rule legality (source S3) and whether the child can
    produce judgeable evidence — and only one ordering of them means anything. Raised above the
    validator's, the fusion bound would refuse nothing and read as though it did."""
    assert factory.MAX_FUSION_ENTRY_CONDITIONS <= factory.MAX_ENTRY_CONDITIONS


def test_fusion_validates_the_child_even_when_a_parent_never_was():
    """An imported parent may sit outside the validator's bounds; the blend is
    checked, never clamped."""
    a = _parent_spec("S1", [_CLOSE_OVER_MA20])
    b = _parent_spec("S2", [_MA20_OVER_MA50],
                     exit_rules={"stop_model": "atr", "stop_atr": 12.0,
                                 "target_atr": 12.0, "max_holding_bars": 10})
    with pytest.raises(FusionRefused) as exc:
        fuse_specs(a, b, strategy_id="S9", generation_id="GEN-9")
    assert "BLOCK_INVALID_PARAMETER_RANGE" in exc.value.reason


def test_rank_fusion_parents_orders_by_score_and_skips_the_unscorable():
    spec = _parent_spec("S1", [_CLOSE_OVER_MA20]).to_dict()
    records = [
        {"candidate_id": "cand-low", "champion_score": 0.1, "strategy_spec": spec},
        {"candidate_id": "cand-high", "champion_score": 0.9, "strategy_spec": spec},
        {"candidate_id": "cand-unscored", "strategy_spec": spec},          # no evidence to pass on
        {"candidate_id": "cand-specless", "champion_score": 0.5},          # nothing to fuse
    ]
    ranked = rank_fusion_parents(records)
    assert [r["candidate_id"] for r in ranked] == ["cand-high", "cand-low"]


# --- fusion parent bucketing --------------------------------------------------

def _bucket_record(candidate_id, score, spec):
    return {"candidate_id": candidate_id, "champion_score": score, "strategy_spec": spec.to_dict()}


def test_buckets_group_only_structurally_fusable_lineages():
    """The defect this closes: parents were ranked GLOBALLY and paired top-N, but a
    crossover is only defined inside one (direction, timeframe, symbol) context — so
    the leaders were spread across contexts and nearly every pair was refused on a
    mismatch. The first live day attempted 240 pairs and refused all 240."""
    long_a = _parent_spec("S1", [_CLOSE_OVER_MA20])
    long_b = _parent_spec("S2", [_MA20_OVER_MA50])
    short = _parent_spec("S3", [_CLOSE_OVER_MA20], direction="short")
    other_tf = _parent_spec("S4", [_CLOSE_OVER_MA20], timeframe="4h")
    other_symbol = _parent_spec("S5", [_CLOSE_OVER_MA20], symbol_scope=["ETHUSDT"])
    records = [
        _bucket_record("cand-long-a", 0.9, long_a),
        _bucket_record("cand-long-b", 0.8, long_b),
        _bucket_record("cand-short", 0.95, short),      # top score, wrong direction to pair with
        _bucket_record("cand-other-tf", 0.7, other_tf),
        _bucket_record("cand-other-symbol", 0.99, other_symbol),
    ]
    buckets = factory.fusion_parent_buckets(records, symbol="BTCUSDT", timeframe="1d")
    # Only the two same-context longs can pair: the short is alone in its bucket, and
    # the other timeframe/symbol are not this run's context at all.
    assert [[r["candidate_id"] for r in b] for b in buckets] == [["cand-long-a", "cand-long-b"]]
    for left, right in combinations(buckets[0], 2):
        fuse_specs(StrategySpec.from_dict(left["strategy_spec"]),
                   StrategySpec.from_dict(right["strategy_spec"]),
                   strategy_id="S9", generation_id="GEN-9")  # every offered pair is fusable


def test_buckets_exclude_contexts_the_run_cannot_score_honestly():
    """A fused child is backtested on the CALLER's snapshot, so a parent pair from
    another symbol/timeframe would be stored with evidence that never described it.
    Global ranking hid this (compatible pairs were vanishingly rare); making pairs
    common has to close it."""
    eth = _parent_spec("S1", [_CLOSE_OVER_MA20], symbol_scope=["ETHUSDT"])
    eth2 = _parent_spec("S2", [_MA20_OVER_MA50], symbol_scope=["ETHUSDT"])
    records = [_bucket_record("cand-eth-a", 0.9, eth), _bucket_record("cand-eth-b", 0.8, eth2)]
    assert factory.fusion_parent_buckets(records, symbol="ETHUSDT", timeframe="1d")
    assert factory.fusion_parent_buckets(records, symbol="BTCUSDT", timeframe="1d") == []
    assert factory.fusion_parent_buckets(records, symbol="ETHUSDT", timeframe="4h") == []


# --- fusion through the factory door ------------------------------------------

def _durable_parent(tmp_path, candidate_id, spec, score):
    record = {
        "candidate_id": candidate_id, "strategy_id": spec.strategy_id,
        "strategy_rule_hash": spec.strategy_rule_hash, "generation_id": "GEN-000",
        "status": "BACKTESTED", "champion_score": score, "strategy_spec": spec.to_dict(),
        "backtest_evidence": {"closed_count": 9}, "evidence_input_sha256": "sha256:parentwindow",
        "provenance": "test", "created_at_utc": NOW,
    }
    pool.append_candidates([record], root=tmp_path)
    return record


def _two_durable_parents(tmp_path):
    _durable_parent(tmp_path, "cand-aaa", _parent_spec("S1", [_CLOSE_OVER_MA20]), 99.0)
    _durable_parent(tmp_path, "cand-bbb", _parent_spec("S2", [_MA20_OVER_MA50]), 98.0)
    return pool.read_candidates(tmp_path)


def test_factory_default_mints_nothing_fused():
    result = run_factory(_trending_snapshot(), active_pool={"active_strategies": []},
                         existing_candidates=[], now=NOW)
    assert result["fused_count"] == 0
    assert result["fusion_rejected"] == []
    assert all(c["derivation_type"] == "seeded_template" for c in result["candidates"])


def test_fused_child_carries_lineage_own_evidence_and_appends(tmp_path):
    parents = _two_durable_parents(tmp_path)
    result = run_factory(_trending_snapshot(), active_pool={"active_strategies": []},
                         existing_candidates=parents, now=NOW, count=1, fusion_pairs=1)
    fused = [c for c in result["candidates"] if c["derivation_type"] == "crossover"]
    assert result["fused_count"] == 1 and len(fused) == 1
    child = fused[0]
    assert child["parent_candidate_ids"] == ["cand-aaa", "cand-bbb"]  # sorted, order-independent
    assert child["provenance"] == "mvp_factory_fusion"
    # Its own evidence window and its own score — a parent's 99.0 is not inherited.
    assert child["evidence_input_sha256"] == result["evidence_input_sha256"] != "sha256:parentwindow"
    assert child["champion_score"] != 99.0
    assert child["backtest_evidence"]["closed_count"] > 0
    # The store's lineage guard accepts it: both parents were durable beforehand.
    assert pool.append_candidates(result["candidates"], root=tmp_path) == len(result["candidates"])


def test_fusion_is_deterministic(tmp_path):
    parents = _two_durable_parents(tmp_path)
    kwargs = dict(active_pool={"active_strategies": []}, existing_candidates=parents,
                  now=NOW, count=1, fusion_pairs=1)
    assert run_factory(_trending_snapshot(), **kwargs) == run_factory(_trending_snapshot(), **kwargs)


def test_unsatisfiable_union_is_refused_rather_than_stored(tmp_path):
    """rsi <= 5 AND rsi >= 95 parses, validates, and can never trade."""
    _durable_parent(tmp_path, "cand-lo",
                    _parent_spec("S1", [{"feature": "rsi", "comparison": "<=", "value": 5.0}]), 9.0)
    _durable_parent(tmp_path, "cand-hi",
                    _parent_spec("S2", [{"feature": "rsi", "comparison": ">=", "value": 95.0}]), 8.0)
    result = run_factory(_trending_snapshot(), active_pool={"active_strategies": []},
                         existing_candidates=pool.read_candidates(tmp_path), now=NOW,
                         count=1, fusion_pairs=1)
    assert result["fused_count"] == 0
    assert result["fusion_rejected"] == [
        {"parent_candidate_ids": ["cand-hi", "cand-lo"], "reason": "no_trades"}
    ]


# --- M4a: promotion ranking (robustness first-pass, win-rate + reward:risk) ----

def _cand(cid, *, verdict, score, closed, wins, avg_win=None, avg_loss=None,
          expectancy=0.0, spec_rr=None):
    evidence = {"closed_count": closed, "win_count": wins, "expectancy": expectancy,
                "robustness": {"verdict": verdict}}
    if avg_win is not None or avg_loss is not None:
        evidence["avg_win_R"] = avg_win or 0.0
        evidence["avg_loss_R"] = avg_loss or 0.0
    record = {"candidate_id": cid, "champion_score": score, "backtest_evidence": evidence}
    if spec_rr is not None:
        record["strategy_spec"] = {"exit_rules": {"stop_atr": 1.0, "target_atr": spec_rr}}
    return record


def test_candidate_quality_realized_designed_and_all_wins():
    realized = pool.candidate_quality(
        _cand("c", verdict="ROBUST", score=0.8, closed=10, wins=6, avg_win=2.0, avg_loss=1.0))
    assert realized["win_rate"] == 0.6 and realized["reward_risk"] == 2.0
    assert realized["reward_risk_basis"] == "realized" and realized["edge_quality"] == 1.2

    legacy = pool.candidate_quality(  # no avg_* fields -> designed target/stop fallback
        _cand("c", verdict="ROBUST", score=0.8, closed=10, wins=7, spec_rr=2.0))
    assert legacy["reward_risk"] == 2.0 and legacy["reward_risk_basis"] == "designed"

    all_wins = pool.candidate_quality(
        _cand("c", verdict="ROBUST", score=0.7, closed=5, wins=5, avg_win=1.2, avg_loss=0.0))
    assert all_wins["all_wins"] is True and all_wins["reward_risk"] is None
    assert all_wins["edge_quality"] == float("inf")


def test_rank_candidates_robustness_tier_before_performance():
    # A PROVISIONAL lineage with a huge edge still sorts below every ROBUST one.
    strong_provisional = _cand("p", verdict="PROVISIONAL", score=0.6, closed=10, wins=8,
                               avg_win=3.0, avg_loss=0.5, expectancy=1.0)
    robust_mid = _cand("r1", verdict="ROBUST", score=0.8, closed=10, wins=6, avg_win=2.0, avg_loss=1.0)
    robust_low = _cand("r2", verdict="ROBUST", score=0.75, closed=10, wins=5, avg_win=1.0, avg_loss=1.0)
    ranked = [r["candidate_id"] for r in pool.rank_candidates([strong_provisional, robust_low, robust_mid])]
    assert ranked == ["r1", "r2", "p"]  # tier first, then edge_quality within tier


def test_rank_candidates_is_deterministic_and_latest_wins():
    a1 = _cand("dup", verdict="ROBUST", score=0.7, closed=10, wins=5, avg_win=1.0, avg_loss=1.0)
    a2 = _cand("dup", verdict="ROBUST", score=0.9, closed=10, wins=9, avg_win=2.0, avg_loss=1.0)  # newer
    tie_a = _cand("aaa", verdict="ROBUST", score=0.7, closed=10, wins=5, avg_win=1.0, avg_loss=1.0)
    tie_b = _cand("bbb", verdict="ROBUST", score=0.7, closed=10, wins=5, avg_win=1.0, avg_loss=1.0)
    ranked = pool.rank_candidates([a1, tie_b, a2, tie_a])
    ids = [r["candidate_id"] for r in ranked]
    assert ids.count("dup") == 1  # latest-wins collapse
    # dup now carries the newer (stronger) evidence, so it leads; equal-quality ties
    # fall back to candidate_id ascending (aaa before bbb).
    assert ids == ["dup", "aaa", "bbb"]


# --- one frame per fire, not one per spec (2026-07-29) -----------------------------------------
#
# Features, candles and the carry series are properties of the market and the calendar; the spec
# decides only which bars it enters on. `backtest_spec` rebuilt all three per spec anyway, and
# `build_feature_rows` is 6.0 seconds at the 48,000-bar 15m window — so a batch of four plus
# fusion children spent ~30s a fire recomputing an identical frame, on a scheduler that runs
# schedules sequentially and shares that tick with the live leg.

def test_a_shared_frame_scores_a_spec_identically_to_a_rebuilt_one():
    """The property the whole optimisation rests on: reuse changes nothing about the answer."""
    from runtime.mvp_runtime.crypto.factory import backtest_spec, build_replay_frame

    snapshot = _trending_snapshot()
    spec = StrategySpec.from_dict(_spec_dict())
    assert backtest_spec(spec, snapshot) == backtest_spec(
        spec, snapshot, frame=build_replay_frame(snapshot)
    )


def test_the_factory_builds_the_frame_once_however_many_specs_it_scores():
    from runtime.mvp_runtime.crypto import factory as factory_mod
    from runtime.mvp_runtime.crypto.factory import run_factory

    built = []
    real = factory_mod.build_feature_rows

    def counting(snapshot):
        built.append(1)
        return real(snapshot)

    original = factory_mod.build_feature_rows
    factory_mod.build_feature_rows = counting
    try:
        result = run_factory(_trending_snapshot(), active_pool={"active_strategies": []},
                             existing_candidates=[], now=NOW, count=4)
    finally:
        factory_mod.build_feature_rows = original

    assert result["accepted_count"] >= 2, "the run has to actually score several specs"
    assert len(built) == 1, f"the feature frame was rebuilt {len(built)} times"


def test_a_frame_from_a_different_cost_model_is_refused():
    """With no venue funding history the carry series spreads `funding_bps_per_interval` over
    each bar, so a frame built under one model and replayed under another would price trades at
    rates they never faced — the failure `pool.cost_basis_rank` catches only after the fact."""
    from runtime.mvp_runtime.crypto.cost import CostModel
    from runtime.mvp_runtime.crypto.factory import backtest_spec, build_replay_frame

    snapshot = _trending_snapshot()
    frame = build_replay_frame(snapshot, cost=CostModel(funding_bps_per_interval=0.0))
    with pytest.raises(ValueError) as excinfo:
        backtest_spec(StrategySpec.from_dict(_spec_dict()), snapshot, frame=frame)
    assert "cost model" in str(excinfo.value)


def test_a_frame_built_at_a_matching_cost_model_is_accepted():
    from runtime.mvp_runtime.crypto.cost import CostModel
    from runtime.mvp_runtime.crypto.factory import backtest_spec, build_replay_frame

    snapshot, cost = _trending_snapshot(), CostModel(taker_fee_bps=7.5)
    frame = build_replay_frame(snapshot, cost=cost)
    evidence = backtest_spec(StrategySpec.from_dict(_spec_dict()), snapshot, frame=frame, cost=cost)
    assert evidence["cost_summary"]["cost_model"]["taker_fee_bps"] == 7.5


# --- the cost structure's two mint-time decisions ------------------------------

def test_a_retired_family_is_not_minted_and_still_has_its_builder():
    """Retired, not deleted. `volatility_squeeze_*` produced 28 candidates across 14
    generations with zero ROBUST and a median of -0.2241R/trade — a compressed band is a
    narrow ATR, a narrow ATR is a narrow 1R, and 1R is what every fixed-bps cost is divided
    by. Stopping the mint is the decision; keeping the builder is what makes re-listing it a
    one-line change if the entry ever gains a compensating widener."""
    minted = {t.family for t in factory.TEMPLATES}
    assert factory.RETIRED_FAMILIES, "a retirement set that empties itself records nothing"
    assert not (minted & factory.RETIRED_FAMILIES), (
        f"{sorted(minted & factory.RETIRED_FAMILIES)} is both retired and in the rotation"
    )
    for family in factory.RETIRED_FAMILIES:
        assert hasattr(factory, f"_{family}_entry"), (
            f"{family} is retired but its builder is gone — that is a deletion, and a "
            "deletion cannot be reversed by re-listing one line"
        )


def test_the_stop_floor_holds_and_the_base_sits_inside_it():
    """1R is `stop_atr` x ATR, so the multiple decides what fraction of the risk unit a fixed
    ~10-16 bps round trip eats. Measured 2026-08-02: the 0.8-1.2 band runs -0.3746R/trade at
    15m against +0.0054R for 1.2-1.6.

    The second assertion is the one that is easy to get wrong. `mutate_params` draws
    `base +/- (hi - lo) * scale` and CLAMPS, so a base sitting on the floor sends half of every
    draw to exactly that value — one number, one rule hash, and the parameter stops varying
    instead of shifting. The base has to clear the floor by more than nothing."""
    stop = factory._EXIT_PARAMS["stop_atr"]
    assert stop.lo >= 1.2, "the tight band is negative at 15m and worse at 4h than the alternative"

    base = factory._EXIT_BASE["stop_atr"]
    assert stop.lo < base < stop.hi, "the base must sit strictly inside its own range"

    rng = random.Random(11)
    draws = [factory.mutate_params(factory._EXIT_BASE, factory._EXIT_PARAMS, rng)["stop_atr"]
             for _ in range(2000)]
    pinned = sum(1 for d in draws if d <= stop.lo + 1e-9) / len(draws)
    assert pinned < 0.10, (
        f"{pinned:.1%} of draws pinned to the floor — the clamp is eating the distribution "
        "rather than bounding it"
    )
    assert min(draws) >= stop.lo and max(draws) <= stop.hi


def test_fusion_cannot_carry_a_child_outside_the_space_it_mints_from():
    """The hole #420's `stop_atr` floor left open, found the day after it shipped.

    Averaging two parents that are both inside an interval lands inside it — a midpoint of
    a convex set is in the set — so the only escape is a parent minted under an OLDER space.
    Measured 2026-08-02: 43% of the candidate store predates the floor moving 0.8 -> 1.2, and
    20 of that day's 60 fused children landed below it, one (GEN-738 at 1.1700) reaching the
    promotable board while the direct mint path was clean 60/60."""
    spec = factory._EXIT_PARAMS["stop_atr"]
    stale = _parent_spec("S1", [_CLOSE_OVER_MA20],
                         exit_rules={"stop_model": "atr", "stop_atr": 0.9,
                                     "target_atr": 3.0, "max_holding_bars": 24})
    fresh = _parent_spec("S2", [_MA20_OVER_MA50],
                         exit_rules={"stop_model": "atr", "stop_atr": 1.44,
                                     "target_atr": 3.0, "max_holding_bars": 24})

    child = fuse_specs(stale, fresh, strategy_id="S9", generation_id="GEN-9")
    assert (0.9 + 1.44) / 2 < spec.lo, "fixture no longer straddles the floor"
    assert child.exit_rules.stop_atr == spec.lo


def test_fusion_leaves_an_in_space_blend_exactly_where_the_midpoint_falls():
    """The clamp bounds; it does not round or re-centre. Two in-space parents must fuse to
    their own midpoint, or the crossover has stopped meaning what its docstring says."""
    a = _parent_spec("S1", [_CLOSE_OVER_MA20],
                     exit_rules={"stop_model": "atr", "stop_atr": 1.3,
                                 "target_atr": 3.0, "max_holding_bars": 24})
    b = _parent_spec("S2", [_MA20_OVER_MA50],
                     exit_rules={"stop_model": "atr", "stop_atr": 1.5,
                                 "target_atr": 4.0, "max_holding_bars": 30})
    child = fuse_specs(a, b, strategy_id="S9", generation_id="GEN-9")
    assert child.exit_rules.stop_atr == 1.4
    assert child.exit_rules.target_atr == 3.5
    assert child.exit_rules.max_holding_bars == 27


def test_a_fused_parameter_and_a_mutated_one_obey_the_same_bounds():
    """One answer to "where may a minted parameter land". The clamp is not a second policy
    beside `mutate_params`; it is the same constants applied at the other mint path — but only
    to inputs the validator already accepts, so it cannot launder an illegal parent.

    "The same constants" is the UNION of the generation spaces since 2026-08-04, when the fade
    families got their own (`_FADE_EXIT_PARAMS`, a 4-16 bar hold against the trend space's
    12-48). A mutated fade parameter may legally be 6, so a fused one may be 6 — clamping to
    `_EXIT_PARAMS` alone would make the two mint paths disagree, which is the exact property
    this test exists to deny. Only the `max_holding_bars` floor actually moves; the fade space
    is strictly inside the trend space on the other two."""
    rng = random.Random(4)
    for name, spec in factory._EXIT_PARAMS.items():
        low, high = factory._EXIT_LEGAL_RANGE[name]
        union_lo = min(space[name].lo for space in factory._GENERATION_SPACES)
        union_hi = max(space[name].hi for space in factory._GENERATION_SPACES)
        # Legal but below every generation space: clamped up to the union floor.
        assert factory._fused_exit_param(name, low, low) == (
            int(round(union_lo)) if spec.integer else round(union_lo, 4))
        # Legal but above it: clamped down to the union ceiling.
        assert factory._fused_exit_param(name, high, high) == (
            int(round(union_hi)) if spec.integer else round(union_hi, 4))
        # ILLEGAL: passed through untouched so `validate_strategy` gets to refuse the child.
        illegal = high * 10
        assert factory._fused_exit_param(name, illegal, illegal) == (
            int(round(illegal)) if spec.integer else round(illegal, 4))
    mutated = mutate_params(factory._EXIT_BASE, factory._EXIT_PARAMS, rng)
    for name, spec in factory._EXIT_PARAMS.items():
        assert spec.lo <= mutated[name] <= spec.hi


def test_the_backtest_refuses_the_entries_the_runtime_would_refuse(monkeypatch):
    """Until 2026-08-02 only the two RUNTIME doors enforced `MAX_ENTRY_COST_R`
    (`paper.entry_cost_refusal`, `live_entry`), so a candidate's expectancy described a
    population the runtime would not trade.

    Pinned as a DIFFERENCE on one spec and one snapshot — door on against door off — because
    an absolute count depends on the fixture's ATR, and the property is that the two agree
    about which trades exist, not that any particular number of them do."""
    snapshot = _trending_snapshot(300)
    tight = StrategySpec.from_dict(_spec_dict(
        exit_rules={"stop_model": "atr", "stop_atr": 0.3, "target_atr": 3.0,
                    "max_holding_bars": 10}))

    with_door = backtest_spec(tight, snapshot)
    monkeypatch.setattr(factory, "MAX_ENTRY_COST_R", 1e9)
    without = backtest_spec(tight, snapshot)

    assert with_door["entry_cost_door"]["applied"] is True
    assert with_door["entry_cost_door"]["refused_entries"] > 0
    assert with_door["closed_count"] < without["closed_count"], (
        "the door removed nothing — the backtest is still scoring trades the runtime refuses"
    )
    # And the door is off in the second run, so its own record says so rather than lying.
    assert without["entry_cost_door"]["refused_entries"] == 0


def test_a_stop_wide_enough_to_pay_its_costs_is_not_refused():
    """The other side of the same door, so the test above cannot pass by refusing everything."""
    snapshot = _trending_snapshot(300)
    wide = StrategySpec.from_dict(_spec_dict(
        exit_rules={"stop_model": "atr", "stop_atr": 2.0, "target_atr": 4.0,
                    "max_holding_bars": 10}))
    evidence = backtest_spec(wide, snapshot)
    assert evidence["entry_cost_door"]["refused_entries"] == 0
    assert evidence["closed_count"] > 0


def test_the_holdout_runs_the_same_door_as_the_scored_window(monkeypatch):
    """A confirmation measured over a wider population than the score would not be confirming
    the same thing — `holdout_status` gates ROBUST, so the two have to see the same trades."""
    snapshot = _trending_snapshot(400)
    tight = StrategySpec.from_dict(_spec_dict(
        exit_rules={"stop_model": "atr", "stop_atr": 0.3, "target_atr": 3.0,
                    "max_holding_bars": 10}))
    with_door = backtest_spec(tight, snapshot)["holdout"]
    monkeypatch.setattr(factory, "MAX_ENTRY_COST_R", 1e9)
    without = backtest_spec(tight, snapshot)["holdout"]

    assert with_door["refused_entries"] > 0
    assert with_door["closed_count"] < without["closed_count"]


# --- S1 increment 2: the vocabulary is scoped to the venue a spec was mined on ----
#
# A feature whose feed the venue lacks is not merely useless there. Most such specs never
# fire, never accrue a sample, and so can never be demoted off a routing slot — the latch
# BUILD_HISTORY records for the loss breaker. One is worse: `liquidation_spike_ratio` falls
# back to a CONSTANT 0.0 with no feed rather than to None, so a mined `< x` condition on it
# matches every bar forever, on a number nobody measured.

def test_every_mintable_feature_is_classified():
    """`_FEATURE_FEED` must be TOTAL over the vocabulary — every feature names its feed.

    This is now the CI half of a two-layer guard rather than the whole of it: the runtime
    half is `known_features`, which admits an unclassified feature nowhere. What this adds
    is that the mistake is reported where it was made, instead of surfacing later as specs
    blocking on a feature the author believed they had added.
    """
    vocabulary = set(factory.NUMERIC_FEATURES) | set(factory.CATEGORICAL_FEATURES)
    classified = set(factory._FEATURE_FEED)

    unclassified = vocabulary - classified
    assert not unclassified, (
        f"mintable but unclassified: {sorted(unclassified)} — name each in `_FEATURE_FEED` "
        f"with the feed it needs (`FEED_CANDLES` if OHLCV alone produces it). Until then "
        f"they are mintable on no venue at all."
    )
    stale = classified - vocabulary
    assert not stale, f"classified but not mintable: {sorted(stale)}"
    for feed in factory._FEATURE_FEED.values():
        assert feed in market_data.KNOWN_FEEDS, f"unknown feed named: {feed}"


def test_an_unclassified_feature_is_mintable_nowhere(monkeypatch):
    """The runtime half: the code refuses it, not just the test above.

    A feature added to the vocabulary and to no table is the realistic mistake — nothing
    about writing one is venue-aware. It used to resolve to "available on every venue",
    which is how an unclassified feature would have reached a venue with no feed for it.
    It now resolves to a feed nobody declares, so every venue rejects it and a spec naming
    it takes BLOCK_UNKNOWN_FEATURE.
    """
    monkeypatch.setattr(
        factory, "NUMERIC_FEATURES", factory.NUMERIC_FEATURES | {"liquidation_burst_ratio"}
    )
    for venue in market_data.VENUE_FEEDS:
        numeric, _ = factory.known_features(venue)
        assert "liquidation_burst_ratio" not in numeric, f"admitted on {venue}"
    # And the CI half still names it, so the mistake is reported rather than only absorbed.
    with pytest.raises(AssertionError, match="liquidation_burst_ratio"):
        test_every_mintable_feature_is_classified()


def test_no_venue_can_declare_the_unclassified_feed():
    """The sentinel only fails closed while no venue answers to it — including by typo.

    `KNOWN_FEEDS` is what a venue may declare and `UNCLASSIFIED_FEED` is deliberately not in
    it, so a venue naming it is a mistake this catches rather than a venue that quietly
    admits every unclassified feature.
    """
    assert market_data.UNCLASSIFIED_FEED not in market_data.KNOWN_FEEDS
    for venue, feeds in market_data.VENUE_FEEDS.items():
        assert market_data.UNCLASSIFIED_FEED not in feeds, f"{venue} declares the sentinel"
        assert feeds <= market_data.KNOWN_FEEDS, (
            f"{venue} declares feeds nobody defined: {sorted(feeds - market_data.KNOWN_FEEDS)}"
        )


def test_binance_keeps_the_whole_vocabulary():
    # The vocabulary was built for this venue, so scoping must subtract nothing from it —
    # this is the "crypto verdicts are unchanged" guarantee in its narrowest form.
    numeric, categorical = factory.known_features(market_data.BINANCE_FUTURES)
    assert numeric == factory.NUMERIC_FEATURES
    assert categorical == factory.CATEGORICAL_FEATURES


def test_hyperliquid_loses_exactly_the_features_its_feeds_cannot_produce():
    numeric, categorical = factory.known_features(market_data.HYPERLIQUID)
    removed = factory.NUMERIC_FEATURES - numeric
    assert removed == {
        "liquidation_spike_ratio", "liquidation_total", "long_liquidation", "short_liquidation",
        "open_interest_change_pct", "open_interest_zscore",
        "positioning_divergence_change", "positioning_divergence_zscore",
        "mark_price", "index_price", "mark_index_basis_bps",
        "premium_index", "premium_index_zscore",
        "taker_buy_ratio", "taker_flow_imbalance", "taker_flow_zscore", "taker_flow_ma",
    }
    # Kept, and easy to lose by accident: these live in the same feature builder as the
    # taker split but need the trade COUNT, which this venue reports.
    assert {"avg_trade_size_zscore", "trade_count_zscore"} <= numeric
    # Funding is a real series here, and the categorical columns are all candle-derived.
    assert {"funding_rate", "funding_zscore"} <= numeric
    assert categorical == factory.CATEGORICAL_FEATURES


def test_a_fabricated_constant_feature_is_refused_where_its_feed_can_never_exist():
    # `liquidation_spike_ratio` is the reason this increment is not merely tidy: with no
    # feed it is 0.0, not None, so this spec would match on every bar of an equity venue.
    spec_dict = _spec_dict(entry_rules={
        "operator": "AND",
        "conditions": [{"feature": "liquidation_spike_ratio", "comparison": "<", "value": 0.5}],
    })
    on_binance = factory.validate_strategy(StrategySpec.from_dict(spec_dict))
    assert on_binance["approved_for_backtest"] is True

    on_hyperliquid = factory.validate_strategy(
        StrategySpec.from_dict({**spec_dict, "venue": market_data.HYPERLIQUID})
    )
    assert on_hyperliquid["approved_for_backtest"] is False
    assert on_hyperliquid["block_reasons"] == ["BLOCK_UNKNOWN_FEATURE"]


def test_an_undeclared_venue_blocks_on_the_venue_not_on_its_features():
    # Reporting BLOCK_UNKNOWN_FEATURE here would blame every condition for something that
    # has nothing to do with them, so the verdict names the venue and stops.
    verdict = factory.validate_strategy(StrategySpec.from_dict(_spec_dict(venue="not_a_venue")))
    assert verdict["approved_for_backtest"] is False
    assert verdict["block_reasons"] == ["BLOCK_UNKNOWN_VENUE"]


def test_venue_feeds_refuses_an_undeclared_venue():
    with pytest.raises(ToolBlocked) as exc:
        market_data.venue_feeds("not_a_venue")
    assert exc.value.reason_code == "UNKNOWN_VENUE"


def test_a_stored_spec_without_a_venue_reads_as_binance():
    # Absent PROVES binance_futures: no other venue existed when the record was written.
    # That is a migration default backed by a fact, not a guess about a forgetful caller.
    stored = _spec_dict()
    assert "venue" not in stored
    spec = StrategySpec.from_dict(stored)
    assert spec.venue == market_data.BINANCE_FUTURES
    assert factory.validate_strategy(spec)["approved_for_backtest"] is True


def test_the_venue_field_does_not_move_the_rule_hash():
    # The guard for 1020 stored candidates and the active pool. `from_dict` VERIFIES a
    # stored `strategy_rule_hash` rather than re-minting it, so a fingerprint that gained a
    # key would make every stored spec fail to load. Identity is already venue-discriminated
    # through `symbol_scope`, whose symbols are namespaced (`xyz:XLE` vs `BTCUSDT`).
    base = StrategySpec.from_dict(_spec_dict())
    other = StrategySpec.from_dict(_spec_dict(venue=market_data.HYPERLIQUID))
    assert base.strategy_rule_hash == other.strategy_rule_hash
    # And a spec carrying its ORIGINAL hash still parses — the real failure mode.
    round_trip = StrategySpec.from_dict(base.to_dict())
    assert round_trip.strategy_rule_hash == base.strategy_rule_hash
    assert round_trip.venue == market_data.BINANCE_FUTURES


def test_to_dict_carries_the_venue():
    assert StrategySpec.from_dict(_spec_dict()).to_dict()["venue"] == market_data.BINANCE_FUTURES


# --- S1 increment 3: generation is scoped to the venue too ------------------------
#
# The validator refusing a spec is the right OUTCOME and the wrong PLACE to spend a rotation
# slot: the family is picked, params drawn, spec built and hashed, all to be refused for a
# feature the venue was never going to serve. Worse for `liquidation_spike_ratio`, whose
# constant-0.0 fallback means the condition is not refused at evaluation, it is TRUE.

def test_binance_mints_every_template():
    # The "nothing changes today" guarantee, in its narrowest form: the vocabulary was built
    # for this venue, so the venue gate must subtract no family from it.
    for timeframe in ("15m", "1h", "4h"):
        scoped = factory.templates_for_timeframe(timeframe, venue=market_data.BINANCE_FUTURES)
        unscoped_families = {
            t.family for t in factory.TEMPLATES
            if t.family not in factory.POSITIONING_FAMILIES
        }
        assert {t.family for t in scoped} >= unscoped_families - factory.HTF_FAMILIES


def test_hyperliquid_drops_the_families_whose_feeds_it_lacks():
    scoped = {t.family for t in factory.templates_for_timeframe("1h", venue=market_data.HYPERLIQUID)}
    binance = {t.family for t in factory.templates_for_timeframe("1h", venue=market_data.BINANCE_FUTURES)}
    assert binance - scoped == {
        "oi_squeeze_long", "oi_squeeze_short", "oi_unwind_long", "oi_unwind_short",
        "premium_fade_long", "premium_fade_short",
        "taker_absorption_long", "taker_absorption_short",
        "taker_flow_long", "taker_flow_short",
    }
    # Funding rides a real series here, so the funding families survive — the gate is about
    # the feed each family needs, not about which venue is the familiar one.
    assert {f for f in scoped if f.startswith("funding_")}


def test_a_templates_feature_set_does_not_move_with_its_params():
    """The assumption `template_features` rests on, pinned rather than trusted.

    It reads the conditions built from `base_params`, which is only the template's whole
    feature set if the builders use params for thresholds and never to CHOOSE a feature. A
    builder that branched on a param would make the venue gate read one arm and mint the
    other.
    """
    for template in factory.TEMPLATES:
        moved = {k: (v * 1.5 if isinstance(v, (int, float)) else v)
                 for k, v in template.base_params.items()}
        shifted = frozenset(
            name
            for cond in template.entry_builder(moved)
            for key in ("feature", "value_from")
            if (name := cond.get(key))
        )
        assert shifted == factory.template_features(template), template.family


def test_every_template_names_only_mintable_features():
    # A template naming something outside the vocabulary would be dropped on EVERY venue by
    # the new gate — silently, since a dropped family looks exactly like an ungated one.
    vocabulary = frozenset(factory.NUMERIC_FEATURES) | frozenset(factory.CATEGORICAL_FEATURES)
    for template in factory.TEMPLATES:
        unknown = factory.template_features(template) - vocabulary
        assert not unknown, f"{template.family} names {sorted(unknown)}"


def test_templates_for_an_undeclared_venue_refuse():
    # Not "no templates" — that reads as a timeframe that mints nothing, which is a thing
    # that legitimately happens and would hide the typo.
    with pytest.raises(ToolBlocked) as exc:
        factory.templates_for_timeframe("1h", venue="not_a_venue")
    assert exc.value.reason_code == "UNKNOWN_VENUE"


def test_a_generated_spec_records_the_venue_it_was_mined_on():
    batch = generate_batch("gen-venue", seed=11, count=2, timeframe="1h",
                           venue=market_data.HYPERLIQUID)
    assert batch["specs"], "expected the venue to still mint something"
    for spec in batch["specs"]:
        assert spec["venue"] == market_data.HYPERLIQUID
        # And it survives the round trip the store puts it through.
        assert StrategySpec.from_dict(spec).venue == market_data.HYPERLIQUID


def test_generation_mints_nothing_its_own_venue_would_refuse():
    """The loop this increment closes: what generation produces, validation accepts.

    Before the gate, a run on a venue lacking the taker/oi/premium feeds still picked those
    families off the rotation and built specs the validator then refused — the slot spent,
    the candidate never scored.
    """
    for venue in sorted(market_data.VENUE_FEEDS):
        batch = generate_batch("gen-loop", seed=5, count=4, timeframe="1h", venue=venue)
        assert batch["specs"], venue
        for spec in batch["specs"]:
            verdict = factory.validate_strategy(StrategySpec.from_dict(spec))
            assert verdict["approved_for_backtest"], (
                venue, spec["strategy_family"], verdict["block_reasons"]
            )


# --- the fade families get their own exit geometry (2026-08-04) --------------------------------
#
# Until now every family shared `_EXIT_PARAMS`, which is a TREND geometry: a target reaching 8x
# ATR over a 48-bar hold is "ride it while it runs". For a fade — whose thesis is that price
# returns to a mean and is COMPLETE when it gets there — that lets the search draw a spec whose
# entry says "this is stretched" and whose exit says "hold for a trend".

_FADE_FAMILIES = frozenset({
    "mean_reversion", "mean_reversion_short", "funding_fade_long", "funding_fade_short",
    "premium_fade_long", "premium_fade_short", "oi_unwind_long", "oi_unwind_short",
    "taker_absorption_long", "taker_absorption_short",
})


def test_every_fade_family_mints_from_the_fade_space_and_no_other_family_does():
    """The set is asserted in BOTH directions on purpose. A family added to the library later
    with a fade premise and the trend space is the silent half of this — it would mint the very
    geometry mismatch this space exists to remove, and nothing else in the suite would notice."""
    got = {t.family for t in factory.TEMPLATES
           if t.param_space["max_holding_bars"] is factory._FADE_EXIT_PARAMS["max_holding_bars"]}
    assert got == _FADE_FAMILIES, (
        f"fade space on the wrong set: unexpected {sorted(got - _FADE_FAMILIES)}, "
        f"missing {sorted(_FADE_FAMILIES - got)}"
    )


def test_the_fade_space_can_never_draw_a_reward_risk_the_validator_refuses():
    """`validate_strategy` enforces `target_atr / stop_atr >= MIN_REWARD_RISK`, and
    `mutate_params` draws the two INDEPENDENTLY. So a space with `target.lo < stop.hi` mints a
    fraction of specs the validator then refuses — burning attempts against
    `_MAX_ATTEMPTS_PER_SPEC` and biasing whatever survives toward the high-target corner, which
    is the opposite of what a fade geometry is for.

    This is why the fade target floor is 2.0: it equals the stop ceiling, and the property is
    arithmetic rather than lucky. It is asserted on the BOUNDS first, because a draw-based test
    alone would pass on a space that is merely unlikely to violate it."""
    stop = factory._FADE_EXIT_PARAMS["stop_atr"]
    target = factory._FADE_EXIT_PARAMS["target_atr"]
    assert target.lo >= stop.hi * factory.MIN_REWARD_RISK, (
        f"target floor {target.lo} is below stop ceiling {stop.hi} — some draws are unmintable"
    )

    rng = random.Random(5)
    for _ in range(2000):
        drawn = factory.mutate_params(factory._FADE_EXIT_BASE, factory._FADE_EXIT_PARAMS, rng)
        assert drawn["target_atr"] / drawn["stop_atr"] >= factory.MIN_REWARD_RISK


def test_the_fade_bases_sit_strictly_inside_their_bounds():
    """The pinning trap `_EXIT_BASE` already documents, restated for the second space: a base ON
    a bound sends ~half of every draw to exactly that value, collapsing the parameter instead of
    shifting it. Checked for all three, not just the stop, because the fade space moved all
    three and a mid-range base is cheap insurance on each."""
    rng = random.Random(17)
    draws = [factory.mutate_params(factory._FADE_EXIT_BASE, factory._FADE_EXIT_PARAMS, rng)
             for _ in range(2000)]
    for name, spec in factory._FADE_EXIT_PARAMS.items():
        base = factory._FADE_EXIT_BASE[name]
        assert spec.lo < base < spec.hi, f"{name}: base {base} is on a bound of its own range"
        values = [d[name] for d in draws]
        assert min(values) >= spec.lo and max(values) <= spec.hi
        pinned = sum(1 for v in values if v <= spec.lo + 1e-9 or v >= spec.hi - 1e-9) / len(values)
        assert pinned < 0.10, f"{name}: {pinned:.1%} of draws pinned to a bound"


def test_a_fade_hold_survives_fusion_instead_of_being_clamped_back_to_the_trend_floor():
    """`_fused_exit_param` holds a child inside "the space the factory currently explores", and
    that is now a SET of spaces. Clamping to `_EXIT_PARAMS` alone would take the midpoint of two
    fade parents holding 6 and 8 bars and push it to 12 — silently restoring the geometry the
    fade space exists to remove, on exactly the children of the families it applies to."""
    a = _parent_spec("S1", [_CLOSE_OVER_MA20],
                     exit_rules={"stop_model": "atr", "stop_atr": 1.6, "target_atr": 2.4,
                                 "max_holding_bars": 6})
    b = _parent_spec("S2", [_MA20_OVER_MA50],
                     exit_rules={"stop_model": "atr", "stop_atr": 1.8, "target_atr": 2.6,
                                 "max_holding_bars": 8})
    child = fuse_specs(a, b, strategy_id="S9", generation_id="GEN-9")
    assert child.exit_rules.max_holding_bars == 7
    assert factory.validate_strategy(child)["approved_for_backtest"]


def test_two_trend_parents_still_clamp_exactly_as_they_did():
    """The union widened only the `max_holding_bars` floor, 12 -> 4, and it cannot bind on two
    trend parents: the midpoint of two values at or above 12 is at or above 12. Pinned so the
    widening cannot be read as a licence to loosen the trend space."""
    stale = _parent_spec("S1", [_CLOSE_OVER_MA20],
                         exit_rules={"stop_model": "atr", "stop_atr": 0.9, "target_atr": 3.0,
                                     "max_holding_bars": 20})
    fresh = _parent_spec("S2", [_MA20_OVER_MA50],
                         exit_rules={"stop_model": "atr", "stop_atr": 1.3, "target_atr": 3.0,
                                     "max_holding_bars": 30})
    child = fuse_specs(stale, fresh, strategy_id="S9", generation_id="GEN-9")
    assert child.exit_rules.stop_atr == factory._EXIT_PARAMS["stop_atr"].lo  # 1.1 -> clamped to 1.2
    assert child.exit_rules.max_holding_bars == 25


# --- xs_momentum -> xs_reversion is a swap, not an addition (2026-08-04) -----------------------

def test_the_momentum_pair_is_retired_rather_than_deleted_and_cannot_return_alongside_reversion():
    """Retired, so the builders stay and re-listing one line is the whole of re-enabling. But the
    pair is mutually exclusive with the reversion families rather than merely superseded:
    `xs_momentum_short` and `xs_reversion_long` fire on the IDENTICAL condition, so a library
    holding both makes every qualifying bar a direction conflict."""
    minted = {t.family for t in factory.TEMPLATES}
    for family in ("xs_momentum_long", "xs_momentum_short"):
        assert family in factory.RETIRED_FAMILIES
        assert hasattr(factory, f"_{family}_entry"), "retirement kept no builder — that is a deletion"
        assert family not in minted
    assert {"xs_reversion_long", "xs_reversion_short"} <= minted

    params = {"xs_rank_edge": 0.3, "xs_dispersion_min": 1.0}
    assert factory._xs_momentum_short_entry(params) == factory._xs_reversion_long_entry(params), (
        "the two fire on different conditions now — the exclusivity argument for the swap is "
        "gone and both could be minted"
    )

    # The cohort gate names only what is minted (see CROSS_SECTION_FAMILIES), so the swap has to
    # move that set too or the minted pair escapes the gate. Disjointness itself is pinned in
    # tests/test_mvp_runtime_crypto_cross_section.py, which owns that property.
    assert factory.CROSS_SECTION_FAMILIES == {"xs_reversion_long", "xs_reversion_short"}


def test_macd_momentum_is_retired_and_its_event_form_is_still_in_the_library():
    """The retirement is a REDUNDANCY finding: `macd_momentum` gates on a STATE (`macd > signal`
    plus `hist > 0`), true for the whole of a trend, which is the defect the `lag` vocabulary was
    added to fix — and the EVENT form of the same hypothesis is already minted as `macd_cross_*`.
    Retiring the state form is only defensible while the event form remains, so that is pinned
    here rather than left to the comment."""
    minted = {t.family for t in factory.TEMPLATES}
    for family in ("macd_momentum", "macd_momentum_short"):
        assert family in factory.RETIRED_FAMILIES and family not in minted
        assert hasattr(factory, f"_{family}_entry")
    assert {"macd_cross_up", "macd_cross_down"} <= minted, (
        "the state form was retired as redundant against an event form that is no longer minted"
    )

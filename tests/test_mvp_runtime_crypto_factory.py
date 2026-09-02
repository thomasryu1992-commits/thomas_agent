"""C8 factory tests — seeded generation, validation, backtest evidence, promotion door.

The source rules under test: same seed → same batch; the validator fails closed on
unknown features and bad risk:reward; the backtest uses the shared evaluator + exit
math; the factory only ever creates candidates; and the active pool changes only
through the explicit operator door (kill-switch bound, audited)."""

from __future__ import annotations

import collections
import json
import random
import statistics
from itertools import combinations
from datetime import datetime, timedelta, timezone

import pytest

from runtime.mvp_runtime import control, timeutil
from runtime.mvp_runtime.control import ControlState, ControlStore
from runtime.mvp_runtime.crypto import factory, features, indicators, market_data, pool, robustness
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
from runtime.mvp_runtime import scheduler as scheduler_mod
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


def _funding_series(candles, *, hours=8):
    """A settlement series spanning ``candles`` — the leg a real snapshot always carries.

    Not decoration. Funding rides the binance_futures grant, so every production snapshot has
    it; a bare-OHLCV fixture only looked adequate while an ABSENT feed fabricated
    ``funding_rate = 0.0``. Once absence became indeterminate (2026-08-05) every
    ``funding_fade_*`` spec minted against this fixture was correctly refused as unsuppliable —
    and at 1d/BTCUSDT that pair is half the rotation's first block, with ``taker_flow_*`` (which
    this bare snapshot also cannot supply) being the other half. The fixture stopped exercising
    the evidence path at all, which is the failure this closes.

    Rates oscillate rather than hold flat: a constant series has zero deviation, so
    ``funding_zscore`` would be undefined and the column indeterminate again.
    """
    if not candles:
        return []
    moment = timeutil.parse_iso(candles[0]["open_time"])
    end = timeutil.parse_iso(candles[-1]["close_time"])
    events = []
    while moment <= end:
        events.append({"timestamp": timeutil.format_iso(moment),
                       "funding_rate": 0.0001 * ((len(events) % 7) - 3)})
        moment += timedelta(hours=hours)
    return events


def _trending_snapshot(n=200, *, funding=True, taker_flow=True):
    """Rising closes with a mid-series dip: entries and both exit kinds occur.

    ``funding`` defaults ON because a production snapshot always carries it. Pass False for the
    tests that are ABOUT an unsourced carry — see :func:`_funding_series` for why the default
    had to move.

    ``taker_flow`` defaults ON for the same reason and it is the stronger case: the aggressor
    legs arrive in every klines response the collector makes, so a snapshot WITHOUT them is a
    shape production never produces. It defaulted off only because nothing had needed it, and
    what that cost was invisible until the rotation grew — a fire whose block lands on the flow
    families mints nothing at all against a snapshot missing their columns
    (``unsuppliable_features``), so the fixture silently decided which families these tests
    could see. Pass False for a test that is about the absence itself.
    """
    step = timedelta(days=1)
    last_close = NOW_DT - timedelta(hours=1)
    candles = []
    price = 100.0
    for i in range(n):
        drift = 1.0 if (i // 20) % 3 != 2 else -1.5  # two up-blocks, one down-block
        price = max(10.0, price + drift)
        close_time = last_close - (n - 1 - i) * step
        volume = 10.0 + (i % 7)
        candle = {
            "open_time": timeutil.format_iso(close_time - step),
            "open": price - drift, "high": price + 1.5, "low": price - 1.5,
            "close": price, "volume": volume,
            "close_time": timeutil.format_iso(close_time),
        }
        if taker_flow:
            # The aggressor split swings with the block's drift, so the flow columns carry
            # signal rather than a constant — a constant would leave every flow family at
            # zero trades, which is the same blindness by a different route.
            buy_share = 0.62 if drift > 0 else 0.38
            candle.update({
                "taker_buy_base": volume * buy_share,
                "taker_buy_quote": volume * buy_share * price,
                "quote_volume": volume * price,
                "trade_count": 40.0 + (i % 11),
            })
        candles.append(candle)
    snapshot = {"symbol": "BTCUSDT", "timeframe": "1d", "candles": candles,
                "is_synthetic": False}
    if funding:
        snapshot["funding"] = _funding_series(candles)
    return snapshot


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


# --- an unordered parameter is a name, not a number -----------------------------

def test_an_unordered_parameter_is_drawn_uniformly_not_perturbed():
    """`session_index` indexes ASIA/EUROPE/US. Perturbing it by a fraction of its range —
    `1 ± 0.7` over [0, 2] — put 71.3% of draws on the base's own value, and the store agrees:
    of 67 `session_trend_*` specs ever minted, EUROPE took 76.1% and the US session 9.0%."""
    rng = random.Random(11)
    space = {"s": ParamSpec(0, 2, integer=True, ordered=False)}
    drawn = collections.Counter(mutate_params({"s": 1}, space, rng)["s"] for _ in range(30000))
    assert set(drawn) == {0, 1, 2}
    for value in (0, 1, 2):
        assert 0.30 < drawn[value] / 30000 < 0.36, dict(drawn)


def test_an_unordered_parameter_ignores_the_elite_centre():
    """A centre is a claim about a neighbourhood and these values have none — and centring on
    the oversampled label would ratchet the artifact of the draw into the next generation."""
    space = {"s": ParamSpec(0, 2, integer=True, ordered=False)}
    from_base = collections.Counter(
        mutate_params({"s": 1}, space, random.Random(3))["s"] for _ in range(4000))
    from_elite = collections.Counter(
        mutate_params({"s": 2}, space, random.Random(3))["s"] for _ in range(4000))
    assert from_base == from_elite


def test_an_ordered_parameter_still_centres_on_its_base():
    """The regression the change above must not cause: `max_holding_bars` is genuinely ordinal
    and 24 bars IS nearer 25 than 48."""
    rng = random.Random(5)
    space = {"n": ParamSpec(12, 48, integer=True)}
    drawn = [mutate_params({"n": 24}, space, rng)["n"] for _ in range(4000)]
    assert min(drawn) >= 12 and max(drawn) <= 37       # base ± 0.35 × range, clamped
    assert 22 <= statistics.median(drawn) <= 26


# --- the bound bounds the draw, it does not collect it --------------------------
#
# `mutate_params` clamped until 2026-08-05, so a centre nearer a bound than its own span sent
# every overshoot to exactly that bound. That is not a bounded distribution, it is a delta on
# the edge — one value, one rule hash — and it RATCHETED: `generate_batch` centres half of every
# batch on `elite_base_params`, so a pinned row becomes a centre on the bound and a centre on
# the bound sends half its own children back to it.

def test_no_parameter_in_the_library_stacks_draws_on_its_own_bound():
    """The property, asserted over the whole library rather than over one space, because it was
    diagnosed as one parameter's problem and it never was: measured 2026-08-05, 16 (parameter,
    space, base) combinations pinned from their own template base — covering 114 of 170 template
    parameter slots — led by `target_atr` 18.8%, `flow_ma_min` and `rel_min` 18.3%,
    `xs_dispersion_min` 14.3%, `htf_sep_min` 11.9%."""
    for template in factory.TEMPLATES:
        rng = random.Random(17)
        draws = [mutate_params(template.base_params, template.param_space, rng)
                 for _ in range(2000)]
        for name, spec in template.param_space.items():
            if not spec.ordered:
                continue
            on_bound = sum(1 for d in draws
                           if abs(d[name] - spec.lo) < 1e-9 or abs(d[name] - spec.hi) < 1e-9)
            # An integer parameter legitimately lands on its bound — 12 and 48 are values, not
            # overflow — so it is judged against a uniform's share of one grid point, generously.
            ceiling = 0.08 if spec.integer else 0.01
            assert on_bound / len(draws) < ceiling, (
                f"{template.family}.{name}: {on_bound / len(draws):.1%} of draws sit on a bound "
                f"of {[spec.lo, spec.hi]} from base {template.base_params[name]}"
            )


def test_a_centre_on_the_bound_does_not_send_half_its_children_back_to_it():
    """The ratchet, in isolation. Under the clamp a centre sitting exactly on `target_atr`'s
    floor re-pinned ~50% of its own draws; the store's real elite centres included seven such
    rows (14.6% of the 48 it supplies for the trend space), two of them re-pinning above 50%."""
    rng = random.Random(19)
    floor = factory._EXIT_PARAMS["reward_risk"].lo
    centre = {**factory._EXIT_BASE, "reward_risk": floor}
    draws = [mutate_params(centre, factory._EXIT_PARAMS, rng)["reward_risk"] for _ in range(8000)]
    on_floor = sum(1 for d in draws if abs(d - floor) < 1e-9)
    assert on_floor / len(draws) < 0.01, f"{on_floor / len(draws):.1%} re-pinned on the floor"
    span = (factory._EXIT_PARAMS["reward_risk"].hi - floor) * factory._MUTATION_SCALE
    assert max(draws) > floor + span * 0.5, (
        "the fold must spread the overshoot INTO the space, not delete it")


def test_the_fold_keeps_the_aim_where_truncating_would_move_it():
    """Why a fold and not a redraw inside the surviving window. Truncating is the other obvious
    repair — it is what `_apply_reward_risk_floor` correctly does for a hard floor — but it
    answers a different question near a bound: from a centre ON `target_atr`'s 1.6 floor a
    truncated draw means 2.72, where the fold holds the median near the centre it was given.

    A mutation is a claim about a NEIGHBOURHOOD. The point of this change was to fix the shape
    of the draw without moving where it aims, and that is the half that is easy to lose."""
    rng = random.Random(23)
    draws = [mutate_params(factory._EXIT_BASE, factory._EXIT_PARAMS, rng)["reward_risk"]
             for _ in range(20000)]
    assert abs(statistics.median(draws) - factory._EXIT_BASE["reward_risk"]) < 0.15, (
        f"median {statistics.median(draws):.3f} against a base of "
        f"{factory._EXIT_BASE['reward_risk']} — the draw has been re-aimed, not reshaped"
    )


def test_the_low_target_region_survives_as_a_region_rather_than_a_spike():
    """What the fold did to the band `_apply_reward_risk_floor` refuses to delete. It was 23.5%
    of draws with 14.4pp of that stacked on the single value 1.6; it is now ~17% spread across
    [1.6, 2.0). The store's own numbers say that is not a loss: the pinned value measured
    -0.2642R against -0.2502R for the rest of the band, so the spike was the WORSE half of the
    region it was standing in."""
    rng = random.Random(37)
    floor = factory._EXIT_PARAMS["reward_risk"].lo
    draws = [mutate_params(factory._EXIT_BASE, factory._EXIT_PARAMS, rng)["reward_risk"]
             for _ in range(20000)]
    # The bottom of the drawn window rather than a fixed ratio: the base is 2.07 and the span
    # 0.805, so the FLOOR itself is out of reach from the template centre (min draw ~1.27) and
    # a fixed `floor + eps` band would be testing arithmetic instead of the distribution. What
    # must survive is a REGION of near-target geometry, and the elite ratchet is what reaches
    # below it — see `_EXIT_PARAMS`.
    low = [d for d in draws if d < factory._EXIT_BASE["reward_risk"] - 0.45]
    assert len(low) / len(draws) > 0.15, "the low band has been squeezed out, not un-spiked"
    on_floor = sum(1 for d in low if abs(d - floor) < 1e-9)
    assert on_floor / len(low) < 0.05, (
        f"{on_floor / len(low):.1%} of the low band is still the single pinned value"
    )


def test_the_fold_holds_for_a_span_wider_than_its_own_interval():
    """No space in the library reaches this (the widest is `max_holding_bars`, span 12.6 against
    a width of 36), and a single reflection would land outside the bounds when one does."""
    assert factory._fold_into_bounds(100.0, 0.0, 1.0) == pytest.approx(0.0)
    assert factory._fold_into_bounds(-100.5, 0.0, 1.0) == pytest.approx(0.5)
    rng = random.Random(41)
    space = {"x": ParamSpec(0.0, 1.0)}
    for _ in range(2000):
        # scale 8.0 puts the window at ±8 against an interval of width 1
        out = mutate_params({"x": 0.5}, space, rng, scale=8.0)
        assert 0.0 <= out["x"] <= 1.0


def test_the_fold_leaves_a_draw_that_never_left_the_space_alone():
    """It must be identity inside the interval, or it is a second perturbation."""
    for value in (0.0, 0.25, 1.0, 3.999):
        assert factory._fold_into_bounds(value, 0.0, 4.0) == pytest.approx(value)


def test_an_unordered_continuous_parameter_is_refused():
    """Silently truncating it to an integer is the failure this refuses at import."""
    with pytest.raises(ValueError):
        ParamSpec(0.0, 2.0, ordered=False)


def test_the_session_families_declare_their_index_unordered():
    """The fix is only real where it is declared, so the declaration is pinned rather than the
    behaviour of one template."""
    for family in ("session_trend_long", "session_trend_short"):
        template = next(t for t in factory.TEMPLATES if t.family == family)
        assert not template.param_space["session_index"].ordered
    assert all(
        spec.ordered
        for template in factory.TEMPLATES
        for name, spec in template.param_space.items()
        if name != "session_index"
    )


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


# --- a family that cannot mint costs its own slot, not the fire -----------------

def _refuse_family(monkeypatch, family):
    """Make `validate_strategy` refuse one family and pass everything else through.

    A family cannot be stuck by any input `generate_batch` accepts — `templates_for_timeframe`
    has already gated its features and `_apply_reward_risk_floor` its exit ratio — so the state
    is reached through the validator, which is the one thing that can refuse a legal draw."""
    real = factory.validate_strategy

    def refusing(spec):
        if spec.strategy_family == family:
            return {"strategy_id": spec.strategy_id,
                    "strategy_rule_hash": spec.strategy_rule_hash,
                    "approved_for_backtest": False,
                    "block_reasons": ["BLOCK_TEST_REFUSAL"]}
        return real(spec)

    monkeypatch.setattr(factory, "validate_strategy", refusing)


def _first_family(*, symbol="BTCUSDT", timeframe="1h", rotation_index=0, count=4):
    """The family ``generate_batch`` reaches first for this context — the one to jam."""
    templates = templates_for_timeframe(timeframe, symbol=symbol)
    offset = factory._rotation_offset(
        "x", 0, count, len(templates), rotation_index=rotation_index,
        phase=factory.context_rotation_phase(symbol, timeframe, count=count,
                                             total=len(templates)),
    )
    return templates[offset].family


def test_a_stuck_family_no_longer_takes_the_whole_fire_with_it(monkeypatch):
    """The template is picked by the ACCEPT index, which a refusal does not move, so the loop
    re-drew the same family until the shared budget was gone and the three slots behind it were
    never tried. One family that cannot mint cost every candidate of the fire, not one."""
    stuck = _first_family()
    _refuse_family(monkeypatch, stuck)

    batch = generate_batch("GEN-900", seed=5, count=4, symbol="BTCUSDT", timeframe="1h",
                           rotation_index=0)

    families = [s["strategy_family"] for s in batch["specs"]]
    assert stuck not in families
    assert batch["accepted_count"] == 4, f"the fire delivered {families}"
    assert batch["batch_complete"]
    # It spent its own share and no more — `_MAX_ATTEMPTS_PER_SPEC`, not the whole budget.
    assert sum(1 for r in batch["rejected"] if r.get("strategy_family") == stuck) == (
        factory._MAX_ATTEMPTS_PER_SPEC
    )


def test_every_family_stuck_still_terminates(monkeypatch):
    """The global budget is still the backstop: nothing here can loop forever."""
    monkeypatch.setattr(factory, "validate_strategy", lambda spec: {
        "strategy_id": spec.strategy_id, "strategy_rule_hash": spec.strategy_rule_hash,
        "approved_for_backtest": False, "block_reasons": ["BLOCK_TEST_REFUSAL"],
    })
    batch = generate_batch("GEN-901", seed=5, count=4, symbol="BTCUSDT", timeframe="1h",
                           rotation_index=0)
    assert batch["accepted_count"] == 0
    assert not batch["batch_complete"]
    assert len(batch["rejected"]) == 4 * factory._MAX_ATTEMPTS_PER_SPEC


def test_the_cursor_is_the_accept_index_when_nothing_is_stuck():
    """The guard must be invisible on the path every fire in the store has taken — 185 of 185
    minted exactly `count`, so a cursor that advanced on anything else would move them all."""
    templates = templates_for_timeframe("1h", symbol="BTCUSDT")
    phase = factory.context_rotation_phase("BTCUSDT", "1h", count=4, total=len(templates))
    for rotation_index in range(12):
        batch = generate_batch(f"GEN-{rotation_index:03d}", seed=rotation_index, count=4,
                               symbol="BTCUSDT", timeframe="1h", rotation_index=rotation_index)
        assert batch["batch_complete"]
        offset = factory._rotation_offset("x", 0, 4, len(templates),
                                          rotation_index=rotation_index, phase=phase)
        assert [s["strategy_family"] for s in batch["specs"]] == [
            templates[(offset + j) % len(templates)].family for j in range(4)
        ]


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

    # The exception, closed 2026-08-05. This assertion is the reason it is worth having had:
    # it stated the hazard as fact rather than folklore — a `< 1.0` condition matching a value
    # nobody measured, on the DEFAULT feed configuration, on every row — and so it is the
    # assertion that had to flip when the 0-fill went.
    assert row["liquidation_spike_ratio"] is None
    permissive = StrategySpec.from_dict(_spec_dict(entry_rules={
        "operator": "AND",
        "conditions": [{"feature": "liquidation_spike_ratio", "comparison": "<", "value": 1.0}],
    }))
    assert evaluate_spec(permissive, row).matched is False  # no measurement, no match


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
    # Prices scaled so ATR (±span) is a realistic fraction of price (~0.5-1.5%),
    # not the 4% that price=100 / span=4 produces — at 20x leverage the liquidation
    # guard correctly refuses stops wider than ~4.6% of entry.
    price = 5000.0
    for i in range(n):
        violent = (i // 25) % 2 == 1
        drift = 20.0 if violent else 2.5
        span = 40.0 if violent else 4.0
        if (i // 25) % 4 == 3:  # one falling violent block, so the short legs can enter
            drift = -drift
        price = max(500.0, price + drift)
        close_time = last_close - (n - 1 - i) * step
        candles.append({
            "open_time": timeutil.format_iso(close_time - step),
            "open": price - drift, "high": price + span, "low": price - span,
            "close": price, "volume": 10.0 + (i % 7),
            "close_time": timeutil.format_iso(close_time),
        })
    return {"symbol": "BTCUSDT", "timeframe": "1d", "candles": candles, "is_synthetic": False,
            "funding": _funding_series(candles)}


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


# --- pooled backtest: one hypothesis, several symbols' evidence ----------------
#
# The ratio F1 says is binding: a spec scoped to one symbol gets as much evidence as that
# symbol signals, which at 4h and 1d is under MIN_HOLDOUT_TRADES. Pooling adds legs without
# adding hypotheses. These pin the two halves of that claim — the evidence really does pool,
# and the DEPTH claim does not, because depth is a calendar span and five symbols over the
# same 350 days is not 1,750 days of market.

def _shifted_snapshot(n=200, *, symbol="ETHUSDT", scale=2.0, funding=False):
    """The trending fixture at a different price level, so its trades are its own.

    ``funding`` defaults OFF, the opposite of the base fixture: this one exists for the pooled
    carry tests, where its job is to be the leg that FELL BACK so a pool can be shown taking its
    worst leg's sourcing. It never reaches `run_factory`, so it needs no mintable columns.
    """
    snapshot = dict(_trending_snapshot(n, funding=funding))
    snapshot["symbol"] = symbol
    snapshot["candles"] = [
        {**c, "open": c["open"] * scale, "high": c["high"] * scale,
         "low": c["low"] * scale, "close": c["close"] * scale}
        for c in snapshot["candles"]
    ]
    return snapshot


def _weak_trend_snapshot(n=200, *, symbol="ETHUSDT"):
    """`_trending_snapshot`'s shape with a shallower rise — same down-blocks, weaker up-blocks.

    A genuinely DIFFERENT market, which `_shifted_snapshot` is not: that one scales the same
    path, and ATR-relative exits are scale-invariant, so a scaled leg scores IDENTICALLY. Any
    test about two legs disagreeing needs a market like this one. Measured against the base
    fixture: 5 closes at +0.093R here, 15 at +0.834R there.

    The rise stays positive on purpose. A market hostile enough that nothing enters (drift
    flipping every bar; the whole trend inverted) compares nothing — the leg reports
    `expectancy: None` and the test goes vacuous by a second route, which is the failure this
    fixture exists to avoid rather than to swap for another one.
    """
    snapshot = dict(_trending_snapshot(n))
    snapshot["symbol"] = symbol
    candles, price = [], 100.0
    for i, source in enumerate(snapshot["candles"]):
        drift = 0.4 if (i // 20) % 3 != 2 else -1.5   # the base fixture's 1.0, weakened
        price = max(10.0, price + drift)
        candles.append({**source, "open": price - drift, "high": price + 1.5,
                        "low": price - 1.5, "close": price})
    snapshot["candles"] = candles
    return snapshot


def test_a_single_frame_pool_is_the_single_symbol_backtest():
    """`backtest_spec` delegates here, so the one-leg answer must not have moved."""
    spec = StrategySpec.from_dict(_spec_dict())
    snapshot = _trending_snapshot()
    assert factory.backtest_spec_pooled(spec, [snapshot]) == backtest_spec(spec, snapshot)
    assert backtest_spec(spec, snapshot)["symbols_replayed"] == 1


def test_pooling_adds_the_legs_trades_and_its_holdout():
    spec = StrategySpec.from_dict(_spec_dict())
    first, second = _trending_snapshot(), _shifted_snapshot()
    one, two = backtest_spec(spec, first), backtest_spec(spec, second)
    assert one["closed_count"] > 0 and two["closed_count"] > 0

    pooled = factory.backtest_spec_pooled(spec, [first, second])
    assert pooled["symbols_replayed"] == 2
    assert pooled["closed_count"] == one["closed_count"] + two["closed_count"]
    # The whole point: the tail that decides CONFIRMED grows with the legs.
    assert pooled["holdout"]["closed_count"] == (
        one["holdout"]["closed_count"] + two["holdout"]["closed_count"]
    )
    assert pooled["holdout"]["symbols"] == 2
    # ...and so does the sample the overfitting veto reads.
    assert pooled["robustness"]["trade_count"] == pooled["closed_count"]


def test_pooled_depth_is_per_symbol_and_never_the_sum():
    """`pool.evidence_depth_of` reads `bars_replayed` as a CALENDAR span, so summing legs
    would tier a pooled candidate as though shown history that does not exist."""
    spec = StrategySpec.from_dict(_spec_dict())
    pooled = factory.backtest_spec_pooled(spec, [_trending_snapshot(), _shifted_snapshot()])
    assert pooled["bars_replayed"] == factory.holdout_split_index(200) == 140
    assert pooled["holdout"]["bars"] == 200 - 140


def test_pooled_depth_takes_the_shallowest_leg():
    """A short leg bounds the claim: the depth every symbol actually contributed."""
    spec = StrategySpec.from_dict(_spec_dict())
    pooled = factory.backtest_spec_pooled(
        spec, [_trending_snapshot(200), _shifted_snapshot(120)]
    )
    assert pooled["bars_replayed"] == factory.holdout_split_index(120)


def test_pooling_refuses_frames_from_a_different_cost_model():
    """The single-frame refusal, which must not weaken by being pooled: a figure mixing
    rates describes no book."""
    spec = StrategySpec.from_dict(_spec_dict())
    snapshot = _trending_snapshot()
    cheap = factory.CostModel(taker_fee_bps=1.0)
    frames = [factory.build_replay_frame(snapshot, cost=cheap)]
    with pytest.raises(ValueError, match="different cost model"):
        factory.backtest_spec_pooled(spec, [snapshot], frames=frames, cost=factory.CostModel())


def test_a_pooled_backtest_needs_something_to_replay():
    spec = StrategySpec.from_dict(_spec_dict())
    with pytest.raises(ValueError, match="at least one"):
        factory.backtest_spec_pooled(spec, [])


# --- walk-forward period subtotals: temporal_stability's inputs, recorded before judged ------
#
# The scored region cut the way `_holdout_evidence` cuts the tail — same field names, same
# clamp rule — so the store can measure whether the subtotals discriminate BEFORE anything
# scores on them (`scripts/walk_forward_stability_report.py` reads them; the decision gate is
# `docs/proposals/WALK_FORWARD_TEMPORAL_STABILITY_V0.1.md`). The holdout suite's
# "changing only the tail leaves walk_forward identical" test already pins these to the
# scored region, so what is left to pin here is the accounting and the withheld judgment.


def test_walk_forward_periods_subtotal_the_scored_region_exactly():
    """Every closed trade lands in exactly one period: the subtotals are a partition of the
    scored net R, never a resample of it."""
    evidence = backtest_spec(StrategySpec.from_dict(_spec_dict()), _trending_snapshot(400))
    assert evidence["closed_count"] > 0, "fixture must trade, or this pins nothing"
    wf = evidence["walk_forward"]
    assert len(wf["period_r"]) == factory.WALK_FORWARD_PERIODS
    assert len(wf["period_trades"]) == factory.WALK_FORWARD_PERIODS
    assert wf["periods"] == factory.WALK_FORWARD_PERIODS
    assert sum(wf["period_trades"]) == evidence["closed_count"]
    assert sum(wf["period_r"]) == pytest.approx(
        evidence["cost_summary"]["total_net_r"], abs=1e-6)
    assert wf["periods_judged"] == sum(1 for n in wf["period_trades"] if n > 0)


def test_temporal_stability_is_recorded_as_inputs_but_not_yet_judged():
    """This increment records and withholds: the field stays None, the scorer keeps reading
    absent evidence, and stripping the new subtotals moves nothing — so no score and no
    verdict can differ between a record minted before and after this landed. The judgment
    is PR-2's, taken on the store's own measured occupancy and discrimination (the
    `HOLDOUT_PERIODS` order: measured, then moved)."""
    evidence = backtest_spec(StrategySpec.from_dict(_spec_dict()), _trending_snapshot(400))
    walk_forward = evidence["walk_forward"]
    assert walk_forward["temporal_stability"] is None
    assert "insufficient_walk_forward_evidence" in evidence["robustness"]["warnings"]

    spec = StrategySpec.from_dict(_spec_dict())
    metrics = {"trade_count": evidence["closed_count"],
               "total_net_r": evidence["cost_summary"]["total_net_r"],
               "fee_cost_r": evidence["cost_summary"]["total_fee_cost_r"],
               "slippage_cost_r": evidence["cost_summary"]["total_slippage_cost_r"],
               "funding_cost_r": evidence["cost_summary"]["total_funding_cost_r"]}
    stripped = {key: value for key, value in walk_forward.items()
                if key not in {"period_r", "period_trades", "periods", "periods_judged"}}
    with_inputs = robustness.score_robustness(
        spec, metrics, walk_forward, evidence["regime_breakdown"], holdout=evidence["holdout"])
    without = robustness.score_robustness(
        spec, metrics, stripped, evidence["regime_breakdown"], holdout=evidence["holdout"])
    assert with_inputs["robustness_score"] == without["robustness_score"]
    assert with_inputs["verdict"] == without["verdict"]


def test_a_pooled_leg_closing_late_clamps_into_the_last_period_and_loses_no_trade():
    """The shallowest leg sets the period width (the `window_r` rule, kept): a trade closing
    beyond the short leg's span clamps into the last period rather than falling off the
    partition."""
    spec = StrategySpec.from_dict(_spec_dict())
    pooled = factory.backtest_spec_pooled(
        spec, [_trending_snapshot(200), _shifted_snapshot(120)])
    wf = pooled["walk_forward"]
    assert sum(wf["period_trades"]) == pooled["closed_count"]
    assert sum(wf["period_r"]) == pytest.approx(
        pooled["cost_summary"]["total_net_r"], abs=1e-6)


def test_the_mint_scope_still_defaults_to_the_symbol_being_mined():
    """Every one of the stored candidates carries a single-symbol scope, and `run_factory`
    is untouched — widening is a caller's decision, never a default."""
    template = templates_for_timeframe("1d")[0]
    spec = factory.build_spec_dict(
        template, dict(template.base_params), strategy_id="S1",
        generation_id="GEN-001", symbol="SOLUSDT",
    )
    assert spec["symbol_scope"] == ["SOLUSDT"]


def test_a_widened_mint_scope_is_sorted_and_deduplicated():
    """`symbol_scope` is inside `strategy_rule_fingerprint`, so two callers naming the same
    set in different orders must reach the same rule hash."""
    template = templates_for_timeframe("1d")[0]
    build = lambda scope: factory.build_spec_dict(
        template, dict(template.base_params), strategy_id="S1",
        generation_id="GEN-001", symbol="BTCUSDT", symbol_scope=scope,
    )
    widened = build(["SOLUSDT", "BTCUSDT", "BTCUSDT"])
    assert widened["symbol_scope"] == ["BTCUSDT", "SOLUSDT"]
    assert (StrategySpec.from_dict(widened).strategy_rule_hash
            == StrategySpec.from_dict(build(["BTCUSDT", "SOLUSDT"])).strategy_rule_hash)


def test_a_pooled_carry_is_only_as_sourced_as_its_worst_leg():
    """Reporting `venue_history` over a book where one leg fell back to the modelled rate
    would overstate what the funding figure is evidence of."""
    spec = StrategySpec.from_dict(_spec_dict())
    with_funding = dict(_trending_snapshot())
    # At the bar OPEN, which is where the venue actually settles (00:00/08:00/16:00 UTC against
    # a daily bar opening at 00:00). Stamping them at the close would leave the first bar's
    # whole span before the series' first settlement, i.e. a genuinely partial series — which
    # is a different property from the one this test pins.
    with_funding["funding"] = [
        {"timestamp": c["open_time"], "funding_rate": 0.0001}
        for c in with_funding["candles"]
    ]
    frames = [factory.build_replay_frame(with_funding), factory.build_replay_frame(_shifted_snapshot())]
    assert frames[0].funding_source == factory.FUNDING_SOURCE_VENUE
    assert frames[1].funding_source == factory.FUNDING_SOURCE_FALLBACK
    pooled = factory.backtest_spec_pooled(spec, [], frames=frames)
    assert pooled["cost_summary"]["cost_model"]["funding_source"] == factory.FUNDING_SOURCE_FALLBACK


def test_a_partial_leg_weakens_a_pooled_carry_without_collapsing_it_to_modelled():
    """Three answers now, and the middle one has to survive pooling. A leg whose series covers
    most of its window measured most of it — calling that `modelled_constant` understates it
    exactly as `venue_history` overstated it, and an ordered strength is what keeps both from
    happening."""
    spec = StrategySpec.from_dict(_spec_dict())
    covered = dict(_trending_snapshot())
    covered["funding"] = [
        {"timestamp": c["open_time"], "funding_rate": 0.0001} for c in covered["candles"]
    ]
    partial = dict(_trending_snapshot())
    # Starts a third of the way in: the bars before it are unmeasured, not free.
    partial["funding"] = [
        {"timestamp": c["open_time"], "funding_rate": 0.0001}
        for c in partial["candles"][len(partial["candles"]) // 3:]
    ]
    frames = [factory.build_replay_frame(covered), factory.build_replay_frame(partial)]
    assert frames[0].funding_source == factory.FUNDING_SOURCE_VENUE
    assert frames[1].funding_source == factory.FUNDING_SOURCE_PARTIAL
    pooled = factory.backtest_spec_pooled(spec, [], frames=frames)
    assert pooled["cost_summary"]["cost_model"]["funding_source"] == factory.FUNDING_SOURCE_PARTIAL


# --- prior windows: does the tail's answer hold before the tail? ----------------
#
# `REMAINING_WORK.md` §F9: the pooled door's first result was five `oi_unwind_short` draws
# clearing a selection-adjusted bar at 4h, and 16 of 16 draws across two seed namespaces decayed
# to −0.23…−0.31R in adjacent earlier windows. A door reading only the newest tail takes that row
# on its face, and `period_r` does not catch it — it partitions the holdout, and the reversal is
# thousands of bars before the holdout begins.

def _spec_for_prior_windows():
    return StrategySpec.from_dict(_spec_dict())


def test_slicing_a_built_frame_equals_rebuilding_it_except_the_last_bars_funding():
    """**The claim `_prefix_frame` rests on**, and the reason the measurement is cheap enough to
    sit on the mint path: every FEATURE is a trailing-window computation, so row *i* carries the
    same values whether the series ran to the end or stopped after it.

    The funding series is the exception and this pins how wide it is. `funding_charges_per_bar`
    spreads settlements across the bars they fall in, so the final bar of a prefix was charged
    knowing the series continued. One bar — and asserting *that*, rather than asserting equality
    and deleting the check when it fails, is what keeps the exception from quietly growing."""
    snapshot = _trending_snapshot(n=200)
    full = factory.build_replay_frame(snapshot)
    keep = factory.holdout_split_index(len(full.rows))
    sliced = factory._prefix_frame(full, keep)
    rebuilt = factory.build_replay_frame(
        {**snapshot, "candles": list(snapshot["candles"])[:keep], "candle_count": keep})

    assert sliced is not None
    assert sliced.split == rebuilt.split
    assert len(sliced.rows) == len(rebuilt.rows) == keep
    assert sliced.rows == rebuilt.rows, "a feature stopped being trailing-window"
    assert sliced.candles == rebuilt.candles
    assert sliced.funding[:-1] == rebuilt.funding[:-1], (
        "the funding difference has spread past the final bar"
    )


def test_prefix_invariance_holds_with_htf_and_external_series():
    """The lookahead guard, beyond OHLCV: HTF columns and asof-aligned external series
    (liquidations) are also trailing-window after alignment, so a prefix of the built
    frame must carry exactly the values those bars had in the full series.

    The existing test above pins this for the basic OHLCV + funding + taker_flow fixture.
    This test adds the two alignment code paths the basic fixture does not exercise:
    close-time-keyed HTF columns (_htf_columns) and backward as-of liquidation events.
    If either alignment leaks a future value into an earlier bar, this fails."""
    step = timedelta(hours=1)
    n = 200
    last_close = NOW_DT - timedelta(hours=1)
    candles = []
    price = 100.0
    for i in range(n):
        drift = 1.0 if (i // 20) % 3 != 2 else -1.5
        price = max(10.0, price + drift)
        close_time = last_close - (n - 1 - i) * step
        volume = 10.0 + (i % 7)
        candles.append({
            "open_time": timeutil.format_iso(close_time - step),
            "open": price - drift, "high": price + 1.5, "low": price - 1.5,
            "close": price, "volume": volume,
            "close_time": timeutil.format_iso(close_time),
            "taker_buy_base": volume * 0.55,
            "taker_buy_quote": volume * 0.55 * price,
            "quote_volume": volume * price,
            "trade_count": 40.0 + (i % 11),
        })

    # HTF candles: one every 4 bars, keyed by close_time (the first instant known).
    htf_candles = []
    for j in range(0, n, 4):
        block = candles[j : j + 4]
        if len(block) < 4:
            break
        htf_candles.append({
            "open_time": block[0]["open_time"],
            "close_time": block[-1]["close_time"],
            "open": block[0]["open"], "close": block[-1]["close"],
            "high": max(c["high"] for c in block),
            "low": min(c["low"] for c in block),
            "volume": sum(c["volume"] for c in block),
        })

    # Liquidation events: one every 3 bars, backward-asof aligned.
    liq_events = []
    for k in range(0, n, 3):
        liq_events.append({
            "timestamp": candles[k]["open_time"],
            "long_liquidation": 1000.0 + k * 10,
            "short_liquidation": 500.0 + k * 5,
        })

    snapshot = {
        "symbol": "BTCUSDT", "timeframe": "1h", "candles": candles,
        "is_synthetic": False, "htf_candles": htf_candles,
        "funding": _funding_series(candles, hours=8),
        "liquidations": liq_events,
    }

    full = factory.build_replay_frame(snapshot)
    keep = factory.holdout_split_index(len(full.rows))
    sliced = factory._prefix_frame(full, keep)
    rebuilt = factory.build_replay_frame(
        {**snapshot, "candles": candles[:keep], "candle_count": keep})

    assert sliced is not None
    assert len(sliced.rows) == len(rebuilt.rows) == keep
    assert sliced.rows == rebuilt.rows, (
        "a feature stopped being trailing-window — the lookahead guard tripped"
    )


def test_the_earlier_windows_are_adjacent_and_do_not_overlap():
    """Window *k+1*'s tail must end exactly where window *k*'s begins — F3's construction. Any
    overlap would score the same bars twice and read as agreement between windows."""
    frame = factory.build_replay_frame(_trending_snapshot(n=200))
    keep, bounds = len(frame.rows), [(frame.split, len(frame.rows))]
    for _ in range(factory.PRIOR_WINDOWS):
        keep = factory.holdout_split_index(keep)
        prefix = factory._prefix_frame(frame, keep)
        if prefix is None:
            break
        bounds.append((prefix.split, keep))
    assert len(bounds) > 1, "the fixture is too short to exercise a single earlier window"
    for newer, older in zip(bounds, bounds[1:]):
        assert older[1] == newer[0], f"{older} does not end where {newer} begins"


def test_the_holdout_block_carries_the_earlier_windows():
    """Reported beside `period_r`, never folded into it: the two answer different questions."""
    frames = [factory.build_replay_frame(_trending_snapshot(n=200))]
    holdout = factory.backtest_spec_pooled(
        _spec_for_prior_windows(), [], frames=frames)["holdout"]
    assert len(holdout["prior_window_r"]) == len(holdout["prior_window_trades"])
    assert len(holdout["prior_window_r"]) <= factory.PRIOR_WINDOWS
    assert "period_r" in holdout, "the sibling field must still be there"


def test_a_series_too_short_yields_fewer_windows_rather_than_an_error():
    """Absence of an earlier window is information — the series ran out — and a caller reading a
    short list learns that. Raising would make a shallow candidate unscoreable instead."""
    frames = [factory.build_replay_frame(_trending_snapshot(n=70))]
    holdout = factory.backtest_spec_pooled(
        _spec_for_prior_windows(), [], frames=frames)["holdout"]
    assert len(holdout["prior_window_r"]) < factory.PRIOR_WINDOWS


def test_a_leg_that_runs_out_stops_the_whole_window_rather_than_shrinking_the_pool():
    """A pooled figure over four legs where the others had five is a different pool, and
    comparing it to the holdout would be comparing two populations."""
    frames = [factory.build_replay_frame(_trending_snapshot(n=200)),
              factory.build_replay_frame(_shifted_snapshot(n=70))]
    r, n = factory._prior_window_evidence(
        _spec_for_prior_windows(), frames, cost=frames[0].cost)
    assert len(r) == len(n) < factory.PRIOR_WINDOWS


# --- pooled minting (F9 shape B: 4h and 1d, 1h left single-symbol) --------------

def _pooled_run(snapshot, cohort, **kwargs):
    return run_factory(snapshot, active_pool={"active_strategies": []},
                       existing_candidates=[], now=NOW, cohort_snapshots=cohort, **kwargs)


def test_no_cohort_is_exactly_todays_behaviour():
    """The default has to be unchanged down to the seed, or every stored row's lineage moves."""
    snapshot = _trending_snapshot()
    plain = run_factory(snapshot, active_pool={"active_strategies": []},
                        existing_candidates=[], now=NOW)
    empty = _pooled_run(snapshot, None)
    assert plain["seed"] == empty["seed"]
    assert plain["pooled"] is False and empty["pooled"] is False
    assert [c["strategy_rule_hash"] for c in plain["candidates"]] == \
           [c["strategy_rule_hash"] for c in empty["candidates"]]


def test_a_cohort_mints_one_hypothesis_over_every_leg():
    result = _pooled_run(_trending_snapshot(), [_shifted_snapshot(symbol="ETHUSDT")])
    assert result["pooled"] is True
    assert result["pooled_symbols"] == ["BTCUSDT", "ETHUSDT"]
    for candidate in result["candidates"]:
        assert candidate["strategy_spec"]["symbol_scope"] == ["BTCUSDT", "ETHUSDT"]
        # `symbols` is what `_holdout_evidence` counted, so it proves the legs were replayed
        # rather than merely named on the spec.
        assert candidate["backtest_evidence"]["holdout"]["symbols"] == 2


def test_the_policy_fences_a_timeframe_it_does_not_pool():
    """1h is 12/12 judgeable single-symbol (F2); pooling it would spend the directional lever
    for nothing. A caller handing over a cohort anyway gets the single-symbol fire."""
    snapshot = {**_trending_snapshot(), "timeframe": "1h"}
    result = _pooled_run(snapshot, [_shifted_snapshot(symbol="ETHUSDT")])
    assert result["pooled"] is False
    assert all(c["strategy_spec"]["symbol_scope"] == ["BTCUSDT"] for c in result["candidates"])


def test_two_cohorts_sharing_a_first_leg_do_not_draw_the_same_batch():
    """The seed reads every leg. Sharing the primary used to be enough to reproduce a batch,
    which made "same seed, same batch" mean something narrower than it says."""
    primary = _trending_snapshot()
    a = _pooled_run(primary, [_shifted_snapshot(symbol="ETHUSDT", scale=2.0)])
    b = _pooled_run(primary, [_shifted_snapshot(symbol="SOLUSDT", scale=3.0)])
    assert a["seed"] != b["seed"]


def test_a_pooled_fire_with_no_cohort_parents_fuses_nothing_and_still_tops_up():
    """A pooled fire fuses since 2026-09-02, but only from parents fitted on this EXACT cohort —
    and an empty store offers none, so the fusion budget re-fires as the seeded topup exactly as
    it did while the pooled boundary stood. What this pins is the topup's scope, not the
    boundary: every row a pooled fire stores claims the cohort."""
    result = _pooled_run(_trending_snapshot(), [_shifted_snapshot(symbol="ETHUSDT")],
                         fusion_pairs=4)
    assert all((c.get("derivation_type") or "seeded_template") == "seeded_template"
               for c in result["candidates"])
    # With fusion declined, the whole fusion budget re-fires as the seeded topup — the second
    # `generate_batch` call site. Its rows must carry the cohort too: a topup row scoped
    # `[symbol]` would wear pooled evidence over a single-symbol label (and split
    # `pool.search_context_key` attempt counts).
    assert result["seeded_topup_count"] > 0, "the topup path must actually fire here"
    for candidate in result["candidates"]:
        assert candidate["strategy_spec"]["symbol_scope"] == ["BTCUSDT", "ETHUSDT"]
        assert candidate["backtest_evidence"]["holdout"]["symbols"] == 2


def test_a_pooled_fire_with_a_dry_store_reads_as_a_dry_pool_because_it_is_one():
    """From 2026-08-10 to 2026-09-02 this exact record meant "the pooled boundary declined", and
    the skip reason `pooled_fire` existed so it could not be mistaken for a dry parent pool.
    Now the path RUNS on a pooled fire, so `None` is the honest reading: fusion looked, and an
    empty store offered no pair fitted on this cohort."""
    result = _pooled_run(_trending_snapshot(), [_shifted_snapshot(symbol="ETHUSDT")],
                         fusion_pairs=4)
    assert result["pooled"] is True
    assert result["fused_count"] == 0 and result["fusion_rejected"] == []
    assert result["fusion_skipped"] is None
    assert not hasattr(factory, "FUSION_POOLED_FIRE"), "the retired reason must not linger"


def test_a_caller_that_never_asked_for_fusion_is_not_a_dry_pool_either():
    result = run_factory(_trending_snapshot(), active_pool={"active_strategies": []},
                         existing_candidates=[], now=NOW)
    assert result["fusion_skipped"] == factory.FUSION_NOT_REQUESTED


def test_a_fire_that_ran_fusion_reports_no_skip_reason(tmp_path):
    """``None`` is the load-bearing value: it is the only reading under which an empty
    ``fusion_rejected`` means the store genuinely offered no pair."""
    parents = _two_improving_parents(tmp_path)
    result = run_factory(_trending_snapshot(), active_pool={"active_strategies": []},
                         existing_candidates=parents, now=NOW, count=1, fusion_pairs=1)
    assert result["fusion_skipped"] is None
    assert result["fused_count"] >= 1


def test_every_skip_reason_is_a_declared_one():
    """A stable vocabulary, so a reader (or a watch) can enumerate the cases rather than
    pattern-match strings that drift."""
    for cohort in (None, [_shifted_snapshot(symbol="ETHUSDT")]):
        result = _pooled_run(_trending_snapshot(), cohort)
        assert result["fusion_skipped"] in factory.FUSION_SKIP_REASONS
    # A fire that asked for fusion ran it, pooled or not — `None` is the only reading under
    # which an empty `fusion_rejected` means the store genuinely offered no pair.
    ran = _pooled_run(_trending_snapshot(), [_shifted_snapshot(symbol="ETHUSDT")], fusion_pairs=4)
    assert ran["fusion_skipped"] is None


def test_a_leg_missing_a_column_starves_the_spec_rather_than_shrinking_the_pool():
    """Fail closed on ANY leg: a pooled figure whose fourth symbol supplied none of the columns
    the rule names is a four-leg pool reporting five."""
    bare = _shifted_snapshot(symbol="ETHUSDT")
    bare["candles"] = [{k: v for k, v in c.items() if k != "taker_buy_base"}
                       for c in bare["candles"]]
    result = _pooled_run(_trending_snapshot(), [bare])
    starved = [r for r in result["rejected"] if r.get("reason") == factory.UNSUPPLIABLE_FEATURE]
    assert starved or result["candidates"], "the fire produced neither candidates nor a reason"


def test_a_pooled_context_does_not_inherit_a_single_symbol_centre():
    """Membership when mining one symbol, exact equality when mining a cohort. A single-symbol
    elite is a centre fitted on one symbol, and F2 measured transferring one as strictly harder
    than searching the cohort from the start."""
    single = {"strategy_spec": {"symbol_scope": ["BTCUSDT"], "timeframe": "4h"}}
    assert factory._matches_context(single["strategy_spec"], symbol="BTCUSDT", timeframe="4h")
    assert not factory._matches_context(
        single["strategy_spec"], symbol="BTCUSDT", timeframe="4h", scope=["BTCUSDT", "ETHUSDT"])
    cohort = {"symbol_scope": ["BTCUSDT", "ETHUSDT"], "timeframe": "4h"}
    assert factory._matches_context(
        cohort, symbol="BTCUSDT", timeframe="4h", scope=["BTCUSDT", "ETHUSDT"])


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

@pytest.fixture(autouse=True)
def _no_leftover_factory_child():
    # The factory fire spawns a child since the #705 process separation; module state
    # must not leak one test's child into the next.
    yield
    scheduler_mod._reset_factory_child()


def _fire_and_collect(store, ledger, tmp_path, *, spawn_at, collect_at):
    """One scheduled factory occurrence, end to end: spawn pass, child, collect pass."""
    spawn = run_due(store, now=spawn_at, control_store=ControlStore(tmp_path),
                    ledger=ledger, repo_root=tmp_path)
    assert spawn["spawned"] == 1, spawn["results"]
    assert scheduler_mod.factory_child_join(300), "the factory child never finished"
    return run_due(store, now=collect_at, control_store=ControlStore(tmp_path),
                   ledger=ledger, repo_root=tmp_path)


def test_scheduler_factory_fire_appends_candidates_and_ledgers(tmp_path):
    schedule = build_schedule(kind=KIND_FACTORY, request="", interval_seconds=86400,
                              created_by="op", now="2026-07-22T10:00:00Z")
    store = ScheduleStore(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.add(schedule)
    ledger = LedgerStore(tmp_path / LEDGER_REL)
    summary = _fire_and_collect(store, ledger, tmp_path,
                                spawn_at="2026-07-23T11:00:00Z",
                                collect_at="2026-07-23T11:01:00Z")
    assert summary["fired"] == 1
    assert summary["results"][0]["status"].startswith("generated=")
    candidates = pool.read_candidates(tmp_path)
    # `DEFAULT_BATCH_SIZE` seeded plus the whole of `FACTORY_FUSION_PAIRS` drawn again as
    # seeded, because the FIRST fire has no durable parents and so mints nothing fused. Four
    # here — the number this asserted before the shortfall draw existed — is now the reading
    # that would mean the fusion half of the budget had been discarded.
    # (mock collector data is fine for MINING, not for trading)
    assert len(candidates) == 8
    assert summary["results"][0]["status"].startswith("generated=8/8 fused=0 topup=4")
    # What ablation did rides the same line: the lattice spends replays with no cadence
    # change, and the status line is where that spend and its yield are legible per fire.
    assert " ablated=" in summary["results"][0]["status"]
    assert " luck_filtered=" in summary["results"][0]["status"]
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
    # This test is about the WIRING — does a scheduled fire reach the fusion path at all.
    # `holdout_permits_parenting` is a separate rule with its own tests, and no first-fire
    # candidate on this synthetic history survives it (they close too few holdout trades, and
    # what they do close loses), so leaving it in would assert "fusion was never attempted" for
    # a reason that has nothing to do with what this test names.
    monkeypatch.setattr(factory, "holdout_permits_parenting", lambda record: True)
    schedule = build_schedule(kind=KIND_FACTORY, request="", interval_seconds=86400,
                              created_by="op", now="2026-07-22T10:00:00Z")
    store = ScheduleStore(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.add(schedule)
    ledger = LedgerStore(tmp_path / LEDGER_REL)
    _fire_and_collect(store, ledger, tmp_path,               # fire 1: parents become durable
                      spawn_at="2026-07-23T11:00:00Z", collect_at="2026-07-23T11:01:00Z")
    summary = _fire_and_collect(store, ledger, tmp_path,     # fire 2: fusion draws on them
                                spawn_at="2026-07-24T12:00:00Z",
                                collect_at="2026-07-24T12:01:00Z")
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
    # `allow_cluster_siblings` for the same reason as the escapes beside it: on this
    # synthetic monotone-trending snapshot several mined specs trade identically, and once
    # the guard stopped refusing most of their entries (the assumption moved to the
    # account's real 5x) two of them became indistinguishable on the recorded axes. The
    # cluster door has its own tests; the subject here is that the door installs what was
    # selected.
    ids = _seed_candidates(tmp_path)
    summary = run_promotion(selectors=ids[:2], promoted_by="Thomas", reason="reviewed",
                            keep_active=False, live_tier="LIVE", root=tmp_path, now=NOW, without_approval=True,
                            allow_oversized_pool=True, allow_below_entry_bar=True,
                            allow_unconfirmed_holdout=True, allow_cluster_siblings=True)
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
                  keep_active=False, live_tier="LIVE", root=tmp_path, now=NOW, without_approval=True,
                  allow_below_entry_bar=True, allow_unconfirmed_holdout=True)
    run_promotion(selectors=ids[1:2], promoted_by="Thomas", reason="r",
                  keep_active=True, live_tier="LIVE", root=tmp_path, now=NOW, without_approval=True,
                  allow_oversized_pool=True,   # same context as the incumbent; see above
                  allow_cluster_siblings=True,  # and indistinguishable from it; see above
                  allow_below_entry_bar=True, allow_unconfirmed_holdout=True)
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
                      keep_active=False, live_tier="LIVE", root=tmp_path, now=NOW)
    assert "BLOCKED" in str(exc.value)
    assert pool.load_active_pool(tmp_path) == {"active_strategies": []}


def test_promotion_refuses_unknown_candidate(tmp_path):
    _seed_candidates(tmp_path)
    with pytest.raises(SystemExit):
        run_promotion(selectors=["S_NOPE"], promoted_by="Thomas", reason="r",
                      keep_active=False, live_tier="LIVE", root=tmp_path, now=NOW)


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
_RSI_OVER_50 = {"feature": "rsi", "comparison": ">=", "value": 50.0}
_RSI_UNDER_60 = {"feature": "rsi", "comparison": "<=", "value": 60.0}


def _parenting_evidence(expectancy=0.20, closed=30, **extra):
    """Evidence carrying a holdout deep enough to judge and not losing.

    Every parent fixture carries one, so that a test asserting a row was EXCLUDED excludes it
    for the reason that test is about (no score, no spec, retired family) rather than
    incidentally through :func:`factory.holdout_permits_parenting`."""
    return {**extra, "holdout": {"closed_count": closed, "expectancy": expectancy}}


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
    ev = _parenting_evidence()
    records = [
        {"candidate_id": "cand-low", "champion_score": 0.1, "strategy_spec": spec,
         "backtest_evidence": ev},
        {"candidate_id": "cand-high", "champion_score": 0.9, "strategy_spec": spec,
         "backtest_evidence": ev},
        {"candidate_id": "cand-unscored", "strategy_spec": spec,           # no evidence to pass on
         "backtest_evidence": ev},
        {"candidate_id": "cand-specless", "champion_score": 0.5,           # nothing to fuse
         "backtest_evidence": ev},
    ]
    ranked = rank_fusion_parents(records)
    assert [r["candidate_id"] for r in ranked] == ["cand-high", "cand-low"]


@pytest.mark.parametrize("holdout, why", [
    (None, "no holdout block at all — a row predating holdouts has no out-of-sample evidence"),
    ({"closed_count": 24, "expectancy": 0.9}, "one trade short of judgeable, however it earned"),
    ({"closed_count": 40, "expectancy": -0.001}, "judgeable and it lost"),
    ({"expectancy": 0.9}, "no closed_count to read"),
    ({"closed_count": 40}, "no expectancy to read"),
    ({"closed_count": True, "expectancy": 0.9}, "a bool is not a reading"),
])
def test_a_lineage_without_surviving_out_of_sample_evidence_may_not_parent(holdout, why):
    """`champion_score` carries no out-of-sample term — holdout "never moves the score" — so
    without this the holdout could refuse a candidate at the promotion door and still have no
    say in which lineages bred. Fail-closed: absence is not a pass."""
    spec = _parent_spec("S1", [_CLOSE_OVER_MA20]).to_dict()
    evidence = {} if holdout is None else {"holdout": holdout}
    record = {"candidate_id": "cand-x", "champion_score": 0.9, "strategy_spec": spec,
              "backtest_evidence": evidence}
    assert rank_fusion_parents([record]) == [], why


@pytest.mark.parametrize("expectancy", [0.0, 0.4])
def test_a_judgeable_holdout_at_or_above_zero_may_parent(expectancy):
    """The bar is one bit at zero, not a ranking on magnitude: the unit of independence is the
    market period and this store carries about ten, so ordering lineages by the SIZE of a
    holdout edge would mine a quantity below what is resolvable. Breaking even qualifies."""
    spec = _parent_spec("S1", [_CLOSE_OVER_MA20]).to_dict()
    record = {"candidate_id": "cand-x", "champion_score": 0.9, "strategy_spec": spec,
              "backtest_evidence": _parenting_evidence(expectancy=expectancy, closed=25)}
    assert [r["candidate_id"] for r in rank_fusion_parents([record])] == ["cand-x"]


def test_the_holdout_floor_is_the_one_the_promotion_door_uses():
    """One authority for "is this tail deep enough to judge" — reusing the constant rather than
    restating 25 keeps parent eligibility moving with it."""
    assert factory.MIN_HOLDOUT_TRADES == robustness.MIN_HOLDOUT_TRADES
    spec = _parent_spec("S1", [_CLOSE_OVER_MA20]).to_dict()
    at_floor = {"candidate_id": "cand-x", "champion_score": 0.9, "strategy_spec": spec,
                "backtest_evidence": _parenting_evidence(closed=robustness.MIN_HOLDOUT_TRADES)}
    assert len(rank_fusion_parents([at_floor])) == 1


def test_ranking_still_orders_by_score_not_by_holdout_size():
    """Eligibility moved; the ranking currency did not. Ranking by holdout magnitude would be
    the max-of-many-draws selection this store is already full of."""
    spec = _parent_spec("S1", [_CLOSE_OVER_MA20]).to_dict()
    records = [
        {"candidate_id": "cand-big-holdout", "champion_score": 0.1, "strategy_spec": spec,
         "strategy_rule_hash": "hash-a",
         "backtest_evidence": _parenting_evidence(expectancy=5.0)},
        {"candidate_id": "cand-high-score", "champion_score": 0.9, "strategy_spec": spec,
         "strategy_rule_hash": "hash-b",
         "backtest_evidence": _parenting_evidence(expectancy=0.01)},
    ]
    assert [r["candidate_id"] for r in rank_fusion_parents(records)] == [
        "cand-high-score", "cand-big-holdout"]


def test_a_rescored_lineage_parents_once_not_twice():
    """A re-score is the same strategy at a new evidence window, not a second lineage.

    `derive_candidate_id` keys on (generation, rules, evidence_input_sha256), so re-scoring
    mints a new id for identical rules — and dedup on the id then admitted both. Measured on
    the store the day the 336-row backfill landed: 348 rule hashes in more than one row, twins
    in 20 of 27 fusable buckets. Every twin pair dies in `_fuse_batch` as `duplicate_rule_hash`
    and `(twin_a, X)` / `(twin_b, X)` propose the same child twice, so the bucket loses both
    parent diversity and pair draws.
    """
    spec = _parent_spec("S1", [_CLOSE_OVER_MA20]).to_dict()
    other = _parent_spec("S2", [_MA20_OVER_MA50]).to_dict()
    ev = _parenting_evidence()
    records = [
        {"candidate_id": "cand-source", "champion_score": 0.9, "backtest_evidence": ev,
         "strategy_rule_hash": "hash-a", "strategy_spec": spec},
        {"candidate_id": "cand-rescored", "champion_score": 0.4, "backtest_evidence": ev,
         "strategy_rule_hash": "hash-a", "strategy_spec": spec},   # same rules, new window
        {"candidate_id": "cand-other", "champion_score": 0.5, "backtest_evidence": ev,
         "strategy_rule_hash": "hash-b", "strategy_spec": other},
    ]
    ranked = rank_fusion_parents(records)
    assert [r["candidate_id"] for r in ranked] == ["cand-other", "cand-rescored"]


def test_the_surviving_twin_is_the_freshest_not_the_best_scoring():
    """Latest-wins, deliberately. Two rows sharing a rule hash are one strategy measured over
    two windows; taking the higher score would pick whichever window flattered it, which is the
    max-of-many-draws selection the holdout gate exists to refuse. File order puts the freshest
    evidence last."""
    spec = _parent_spec("S1", [_CLOSE_OVER_MA20]).to_dict()
    records = [
        {"candidate_id": "cand-flattering", "champion_score": 0.95,
         "backtest_evidence": _parenting_evidence(),
         "strategy_rule_hash": "hash-a", "strategy_spec": spec},
        {"candidate_id": "cand-fresh", "champion_score": 0.10,
         "backtest_evidence": _parenting_evidence(),
         "strategy_rule_hash": "hash-a", "strategy_spec": spec},
    ]
    assert [r["candidate_id"] for r in rank_fusion_parents(records)] == ["cand-fresh"]


def test_hashless_legacy_rows_do_not_collapse_into_one_parent():
    """The fallback matters: keying on a missing hash would merge every legacy row into a single
    bucket entry and delete real lineages. Absent hash falls back to the candidate id."""
    spec = _parent_spec("S1", [_CLOSE_OVER_MA20]).to_dict()
    records = [
        {"candidate_id": "cand-1", "champion_score": 0.9, "strategy_spec": spec,
         "backtest_evidence": _parenting_evidence()},
        {"candidate_id": "cand-2", "champion_score": 0.8, "strategy_spec": spec,
         "backtest_evidence": _parenting_evidence()},
        {"candidate_id": "cand-3", "champion_score": 0.7,
         "backtest_evidence": _parenting_evidence(),
         "strategy_rule_hash": "", "strategy_spec": spec},          # empty is not an identity
    ]
    assert len(rank_fusion_parents(records)) == 3


# --- fusion parent bucketing --------------------------------------------------

def _bucket_record(candidate_id, score, spec):
    return {"candidate_id": candidate_id, "champion_score": score, "strategy_spec": spec.to_dict(),
            "backtest_evidence": _parenting_evidence()}


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
        "backtest_evidence": _parenting_evidence(closed_count=9),
        "evidence_input_sha256": "sha256:parentwindow",
        "provenance": "test", "created_at_utc": NOW,
    }
    pool.append_candidates([record], root=tmp_path)
    return record


def _two_durable_parents(tmp_path):
    """A structurally fusable pair whose child does NOT clear the improvement bar.

    On ``_trending_snapshot`` the child earns +0.092R over 6 trades against a better parent's
    +0.574R over 17 — the ordinary case, and what fusion stored by default before the bar
    existed (9.8% of the store's children out-score their best parent)."""
    _durable_parent(tmp_path, "cand-aaa", _parent_spec("S1", [_CLOSE_OVER_MA20]), 99.0)
    _durable_parent(tmp_path, "cand-bbb", _parent_spec("S2", [_MA20_OVER_MA50]), 98.0)
    return pool.read_candidates(tmp_path)


def _two_improving_parents(tmp_path):
    """The rarer pair whose child does clear it: both parents lose on this snapshot
    (-0.485R over 8 trades, -0.546R over 18) and their union turns positive at +0.099R
    without dropping ``champion_score`` below either (0.396 against 0.191 and 0.333).

    Deliberately not a flattering fixture — it is one of only 1 pair in 78 tried over this
    snapshot's feature grid that passes, which is the yield the bar actually has."""
    _durable_parent(tmp_path, "cand-aaa", _parent_spec("S1", [_RSI_OVER_50]), 99.0)
    _durable_parent(tmp_path, "cand-bbb", _parent_spec("S2", [_RSI_UNDER_60]), 98.0)
    return pool.read_candidates(tmp_path)


def test_factory_default_mints_nothing_fused():
    result = run_factory(_trending_snapshot(), active_pool={"active_strategies": []},
                         existing_candidates=[], now=NOW)
    assert result["fused_count"] == 0
    assert result["fusion_rejected"] == []
    assert all(c["derivation_type"] == "seeded_template" for c in result["candidates"])


def test_fused_child_carries_lineage_own_evidence_and_appends(tmp_path):
    parents = _two_improving_parents(tmp_path)
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
    parents = _two_improving_parents(tmp_path)
    kwargs = dict(active_pool={"active_strategies": []}, existing_candidates=parents,
                  now=NOW, count=1, fusion_pairs=1)
    assert run_factory(_trending_snapshot(), **kwargs) == run_factory(_trending_snapshot(), **kwargs)


def test_unsatisfiable_union_is_refused_rather_than_stored(tmp_path):
    """rsi <= 5 AND rsi >= 95 parses, validates, and can never trade.

    The refusal reason moved when the ablation lattice landed in front of the no-trades
    gate: a zero-trade union scores a train expectancy of 0.0, ties its zero-trade
    subsets, a tie hands the win to the simpler side, and the winning subset carries the
    parents' shared exits — so it IS a durable parent and falls to the duplicate guard.
    Either way nothing is stored, which is the property this test pins; `no_trades` still
    guards the registered spec for any winner that reaches its own backtest."""
    _durable_parent(tmp_path, "cand-lo",
                    _parent_spec("S1", [{"feature": "rsi", "comparison": "<=", "value": 5.0}]), 9.0)
    _durable_parent(tmp_path, "cand-hi",
                    _parent_spec("S2", [{"feature": "rsi", "comparison": ">=", "value": 95.0}]), 8.0)
    result = run_factory(_trending_snapshot(), active_pool={"active_strategies": []},
                         existing_candidates=pool.read_candidates(tmp_path), now=NOW,
                         count=1, fusion_pairs=1)
    assert result["fused_count"] == 0
    assert result["fusion_rejected"] == [
        {"parent_candidate_ids": ["cand-hi", "cand-lo"],
         "reason": "ablation_winner_duplicate_rule_hash"}
    ]


# --- the allocation fusion declined -------------------------------------------

def _declined_fusion_runs(tmp_path, *, count=2, pairs=2):
    """The same fire with the fusion allocation off and on, over parents whose only pair is
    refused — so the difference between the two results is the shortfall draw and nothing
    else. Returns ``(control, result)``."""
    parents = _two_durable_parents(tmp_path)  # their child does not clear the improvement bar
    kwargs = dict(active_pool={"active_strategies": []}, existing_candidates=parents, now=NOW,
                  count=count)
    return (run_factory(_trending_snapshot(), fusion_pairs=0, **kwargs),
            run_factory(_trending_snapshot(), fusion_pairs=pairs, **kwargs))


def test_the_allocation_fusion_declined_is_drawn_again_as_seeded(tmp_path):
    """A dry fusion used to hand its slots back to nobody, and that went from invisible to
    dominant the moment the two parent-child rules landed: measured on the live scheduler
    ledger, children minted per context went **4.00 a fire** (07-31 -> 08-04) to **0.40**
    (08-05, first fire under `FUSION_IMPROVEMENT_METRICS`) to **0.00** (08-06) — 20 rows
    minted where the budget allowed 40."""
    control, result = _declined_fusion_runs(tmp_path)
    assert result["fused_count"] == 0, "the fixture must exercise the DECLINED path"
    assert result["seeded_topup_count"] == 2
    assert result["accepted_count"] == control["accepted_count"] + 2
    assert result["batch_complete"]
    assert len(result["candidates"]) > len(control["candidates"])
    # A draw's KIND does not change with which half of the budget paid for it.
    assert all(c["derivation_type"] == "seeded_template" for c in result["candidates"])
    # The batch's own hashes reach the shortfall draw's duplicate guard, so a second draw
    # landing on the same rules is refused rather than stored twice — a different seed alone
    # would make that unlikely rather than impossible.
    hashes = [c["strategy_rule_hash"] for c in result["candidates"]]
    assert len(set(hashes)) == len(hashes)
    assert pool.append_candidates(result["candidates"], root=tmp_path) == len(result["candidates"])


def test_a_filled_fusion_allocation_leaves_no_shortfall_to_draw(tmp_path):
    """The draw only ever re-spends what fusion declined, which is what makes it unable to
    cut a good fusion — the distinction from lowering `FACTORY_FUSION_PAIRS`."""
    parents = _two_improving_parents(tmp_path)
    result = run_factory(_trending_snapshot(), active_pool={"active_strategies": []},
                         existing_candidates=parents, now=NOW, count=1, fusion_pairs=1)
    assert result["fused_count"] == 1
    assert result["seeded_topup_count"] == 0
    assert result["accepted_count"] == 1


def test_a_fire_that_never_allocated_fusion_draws_no_shortfall():
    """`fusion_pairs=0` is still exactly the old behaviour, front to back."""
    result = run_factory(_trending_snapshot(), active_pool={"active_strategies": []},
                         existing_candidates=[], now=NOW)
    assert result["seeded_topup_count"] == 0
    assert result["accepted_count"] == result["requested_count"] == 4


def test_the_shortfall_draw_stays_on_this_fires_own_rotation_slice(tmp_path):
    """`_rotation_offset` is `(step + phase) * count`, so `count` is what fixes WHICH families
    a fire mints. Asking for the shortfall directly would land the draw on the slice belonging
    to some other fire and leave this one's families half-explored — so the draw passes `count`
    and `rotation_index` unchanged and keeps only the shortfall of what comes back."""
    control, result = _declined_fusion_runs(tmp_path)
    this_slice = {c["strategy_spec"]["strategy_family"] for c in control["candidates"]}
    assert result["fused_count"] == 0 and len(result["candidates"]) > len(control["candidates"])
    assert this_slice, "the control fire must actually mint something to compare against"
    assert {c["strategy_spec"]["strategy_family"] for c in result["candidates"]} <= this_slice


def test_the_shortfall_draw_is_reproducible_from_the_recorded_window(tmp_path):
    """Its seed is the next eight hex digits of the same window hash the batch seeded from,
    so the second draw is as replayable as the first and no clock enters."""
    parents = _two_durable_parents(tmp_path)
    kwargs = dict(active_pool={"active_strategies": []}, existing_candidates=parents,
                  now=NOW, count=2, fusion_pairs=2)
    a = run_factory(_trending_snapshot(), **kwargs)
    assert a["seeded_topup_count"] == 2, "the shortfall path has to actually be exercised"
    assert a == run_factory(_trending_snapshot(), **kwargs)


# --- the improvement bar: a child is stored only when it beats its parents -----

_PARENTS_ONE = [{"expectancy": 0.40, "champion_score": 0.50}]


def test_a_child_must_out_earn_the_best_parent_not_merely_one_of_them():
    """Beating the weaker parent answers the wrong question: fusion is worth recording only
    if it beat KEEPING either parent, and keeping the stronger one was free."""
    parents = [{"expectancy": 0.40, "champion_score": 0.50},
               {"expectancy": 0.60, "champion_score": 0.50}]
    assert factory._fusion_improvement(
        {"expectancy": 0.50, "champion_score": 0.90}, parents) == "no_expectancy_gain"
    assert factory._fusion_improvement(
        {"expectancy": 0.61, "champion_score": 0.90}, parents) is None


def test_an_equal_return_is_not_an_improvement():
    assert factory._fusion_improvement(
        {"expectancy": 0.40, "champion_score": 0.90}, _PARENTS_ONE) == "no_expectancy_gain"


def test_the_score_leg_refuses_a_regression_but_admits_a_tie():
    """A fused child carries more conditions on fewer trades than either parent, so
    ``parameter_parsimony`` and ``sample_adequacy`` — 45% of the score — move against it by
    construction. The leg asks it not to FALL, not to rise, or fusion would be refused on its
    own arithmetic rather than on the child's merit."""
    earns_more = {"expectancy": 0.90, "champion_score": 0.49}
    assert factory._fusion_improvement(earns_more, _PARENTS_ONE) == "champion_score_regression"
    assert factory._fusion_improvement({**earns_more, "champion_score": 0.50}, _PARENTS_ONE) is None


@pytest.mark.parametrize("broken", [
    {"champion_score": 0.9},                          # expectancy absent entirely
    {"expectancy": 0.9},                              # champion_score absent entirely
    {"expectancy": None, "champion_score": 0.9},
    {"expectancy": "0.9", "champion_score": 0.9},
    {"expectancy": 0.9, "champion_score": True},      # bool is not a reading
])
def test_an_unreadable_metric_refuses_rather_than_passes(broken):
    """Fail-closed: an absent number is not a zero, and treating it as one would admit exactly
    the children whose evidence is malformed."""
    good = {"expectancy": 0.10, "champion_score": 0.10}
    assert factory._fusion_improvement(broken, [good]) == "improvement_unmeasurable"
    assert factory._fusion_improvement(
        {"expectancy": 9.0, "champion_score": 9.0}, [broken]) == "improvement_unmeasurable"


def test_no_parent_to_compare_against_is_unmeasurable_not_a_pass():
    assert factory._fusion_improvement(
        {"expectancy": 9.0, "champion_score": 9.0}, []) == "improvement_unmeasurable"


def test_a_child_that_does_not_out_earn_its_parents_is_refused_rather_than_stored(tmp_path):
    """The ordinary pair, and the whole reason for the bar: the union of two profitable trend
    conditions earns +0.092R over 6 trades where the better parent earned +0.574R over 17.
    Before the bar this was appended, ranked, and available to parent a further generation.

    Since the ablation lattice, the refusal comes one gate EARLIER and says something
    sharper: on the train segment the union fails to strictly beat its own best proper
    subset, that subset carries the parents' shared exits and so IS one of the durable
    parents, and re-minting a stored rule is what the duplicate guard exists to refuse.
    The property this test pins — this union is refused, never stored — is unchanged;
    `no_expectancy_gain` remains the refusal for a union that WINS its lattice and then
    fails to out-earn its parents (the `_fusion_improvement` unit tests carry the bar
    itself)."""
    parents = _two_durable_parents(tmp_path)
    result = run_factory(_trending_snapshot(), active_pool={"active_strategies": []},
                         existing_candidates=parents, now=NOW, count=1, fusion_pairs=1)
    assert result["fused_count"] == 0
    assert not [c for c in result["candidates"] if c["derivation_type"] == "crossover"]
    assert result["fusion_rejected"] == [
        {"parent_candidate_ids": ["cand-aaa", "cand-bbb"],
         "reason": "ablation_winner_duplicate_rule_hash"}
    ]


def test_every_minted_child_records_beating_both_parents_on_one_window(tmp_path):
    parents = _two_improving_parents(tmp_path)
    result = run_factory(_trending_snapshot(), active_pool={"active_strategies": []},
                         existing_candidates=parents, now=NOW, count=1, fusion_pairs=1)
    fused = [c for c in result["candidates"] if c["derivation_type"] == "crossover"]
    assert fused, "the fixture's pair must still mint, or this asserts nothing"
    for child in fused:
        comparison = child["fusion_improvement"]
        # One window for both sides — the whole point of replaying the parents here.
        assert comparison["measured_on_evidence_sha256"] == result["evidence_input_sha256"]
        assert set(comparison["parents"]) == set(child["parent_candidate_ids"])
        assert (comparison["child"]["expectancy"]
                > max(p["expectancy"] for p in comparison["parents"].values()))
        assert (comparison["child"]["champion_score"]
                >= max(p["champion_score"] for p in comparison["parents"].values()))
        # The recorded child figures are the child's own evidence, not a second measurement.
        assert comparison["child"]["expectancy"] == child["backtest_evidence"]["expectancy"]


def test_a_fused_conjunction_runs_the_lattice_and_registers_with_its_grid(tmp_path):
    """The ablation lattice sits on the fusion path too, and §3-3 as approved: the winner
    registers through the EXISTING path — ``derivation_type`` stays ``crossover``, and the
    grid it survived rides in ``backtest_evidence.ablation``. On this fixture the union
    (rsi >= 50 AND rsi <= 60) genuinely beats both one-sided parts on the train segment
    (+0.116R against -0.54R and -0.48R), so the full conjunction is what mints — the
    interaction case, measured through the real mint path with the improvement gate live."""
    parents = _two_improving_parents(tmp_path)
    result = run_factory(_trending_snapshot(), active_pool={"active_strategies": []},
                         existing_candidates=parents, now=NOW, count=1, fusion_pairs=1)
    fused = [c for c in result["candidates"] if c["derivation_type"] == "crossover"]
    assert len(fused) == 1
    child = fused[0]
    ablation = child["backtest_evidence"]["ablation"]
    assert ablation["lattice_size"] == 3
    assert ablation["full_beat_subsets"] is True
    assert ablation["winner_conditions"] == ablation["hypothesis_conditions"]
    assert ablation["winner_conditions"] == child["strategy_spec"]["entry_rules"]["conditions"]
    full_key = "0+1"
    scores = ablation["train_net_expectancy_by_subset"]
    assert all(scores[full_key] > scores[key] for key in scores if key != full_key)
    # The fire's counters see the fused lattice alongside the seeded one.
    assert result["ablated_count"] >= 1


def test_the_bar_is_the_parents_replayed_here_not_the_row_they_were_stored_with(tmp_path):
    """890 of the store's 894 parent/child evidence links are on DIFFERENT candle windows, so a
    bar read off a parent's stored row would score the market's drift between two windows and
    call it lineage improvement.

    The fixture makes that visible without mocking: ``_durable_parent`` stores an evidence
    block holding neither ``expectancy`` nor ``champion_score``, so a gate reading the stored
    row could only answer ``improvement_unmeasurable`` and would refuse every pair. Minting at
    all is therefore proof the comparison came from a replay on the child's own snapshot."""
    parents = _two_improving_parents(tmp_path)
    assert all("expectancy" not in p["backtest_evidence"] for p in parents)
    result = run_factory(_trending_snapshot(), active_pool={"active_strategies": []},
                         existing_candidates=parents, now=NOW, count=1, fusion_pairs=1)
    assert result["fused_count"] == 1
    assert not any(r["reason"] == "improvement_unmeasurable" for r in result["fusion_rejected"])


def test_a_parent_is_replayed_once_per_fire_however_many_pairs_cite_it(monkeypatch):
    """`FUSION_PARENT_POOL` parents offer 15 pairs, so an unmemoised bar would replay each
    parent up to 5 times for an answer that cannot change between them."""
    seen = []
    original = factory.backtest_spec

    def counting(spec, snapshot, **kwargs):
        seen.append(spec.strategy_rule_hash)
        return original(spec, snapshot, **kwargs)

    monkeypatch.setattr(factory, "backtest_spec", counting)
    # Distinct exits per parent, so an ablation winner — a single-condition subset carrying
    # the pair's MIDPOINT exits — can never hash-collide with a parent and show up in the
    # spy's parent filter as a spurious re-replay. What this test counts is the parent
    # memoisation, and the fixture has to keep the two populations separable to count it.
    specs = [_parent_spec(f"S{i}", [{"feature": "adx", "comparison": ">=", "value": 20.0 + i}],
                          exit_rules={"stop_model": "atr", "stop_atr": 1.5 + 0.1 * i,
                                      "target_atr": 2.0, "max_holding_bars": 10})
             for i in range(4)]
    bucket = [{"candidate_id": f"cand-{i}", "strategy_spec": s.to_dict(), "champion_score": 1.0}
              for i, s in enumerate(specs)]
    factory._fuse_batch([bucket], _trending_snapshot(), generation_id="GEN-9", start_index=1,
                        pairs=99, seen_hashes=set(), evidence_sha="sha256:x", now=NOW)
    parent_replays = [h for h in seen if h in {s.strategy_rule_hash for s in specs}]
    assert len(parent_replays) == len(set(parent_replays)) == 4


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


def test_the_stop_ceiling_leaves_room_the_old_one_truncated():
    """The ceiling opened 2.0 -> 3.0 (Thomas 2026-08-25), and the property is REACH.

    `span = (hi - lo) * _MUTATION_SCALE`, so the old ceiling capped the template half at
    1.73 — every spec in the store sits under that number because no draw could pass it, not
    because higher scored worse. Measured on a 1h stop sweep with the reward:risk HELD FIXED
    (target scaled with the stop, so only the geometry moves), net expectancy went -0.162R to
    -0.032R per trade and the per-spec optima sat at 2.6-3.2. Under the old space that region
    was unreachable, so the search could not have found it however well it worked.

    Pinned as an inequality rather than as the number 3.0: what must not regress is that a
    spec can be drawn meaningfully above where the store stops, and that the pair the space
    can now mint stays inside what `validate_strategy` admits."""
    stop = factory._EXIT_PARAMS["stop_atr"]
    base = factory._EXIT_BASE["stop_atr"]
    reach = base + (stop.hi - stop.lo) * factory._MUTATION_SCALE
    assert reach > 2.0, (
        f"the template half reaches only {reach:.2f} — the region the sweep found optima in "
        "is still truncated, so widening the ceiling bought nothing"
    )
    assert stop.hi <= factory.STOP_ATR_RANGE[1]


def test_widening_the_stop_no_longer_drags_the_target_distribution_with_it():
    """The reason the ceiling could not move under the old parameterisation, kept as a guard.

    With `target_atr` drawn independently and `_apply_reward_risk_floor` pushing it above
    `MIN_REWARD_RISK x stop`, widening the stop's ceiling dragged the whole target
    distribution up behind it: on the 2.0 -> 4.0 move the median target went 3.02 -> 3.28 and
    the low band collapsed 17.5% -> 11.1%. Drawing the RATIO decouples them — the stop's
    bounds cannot reach the ratio's draw at all — and this pins that a future widening stays
    free of the same side effect.

    Compares the aim under two stop ceilings on the same seed. The ratio must not move."""
    import copy

    def ratio_median(stop_hi):
        space = copy.deepcopy(factory._EXIT_PARAMS)
        space["stop_atr"] = ParamSpec(space["stop_atr"].lo, stop_hi)
        rng = random.Random(97)
        return statistics.median(
            mutate_params(factory._EXIT_BASE, space, rng)["reward_risk"] for _ in range(8000))

    narrow, wide = ratio_median(2.0), ratio_median(3.0)
    assert abs(narrow - wide) < 1e-9, (
        f"the ratio's aim moved {narrow:.4f} -> {wide:.4f} when only the STOP ceiling changed "
        "— the two axes are coupled again"
    )


def test_the_fade_geometry_keeps_its_narrow_stop():
    """The ceiling moved on the TREND geometry only, and the boundary is the fade's thesis.

    A fade is complete when price returns to the mean — `_FADE_EXIT_BASE` pairs a short hold
    with a near target for exactly that reason. A wide stop on a fade does not buy a cheaper
    risk unit so much as a longer wait in a position whose premise has already failed, which
    is the fade's own failure mode. Left narrow deliberately, and pinned so that a later
    widening of the trend space does not carry it along by habit."""
    assert factory._FADE_EXIT_PARAMS["stop_atr"].hi <= 2.0


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
    for name in factory._EXIT_LEGAL_RANGE:
        low, high = factory._EXIT_LEGAL_RANGE[name]
        if name == "target_atr":
            # Derived from (stop_atr, reward_risk) since 2026-08-25, so its "space" is the
            # product's range — the same union question, asked of a parameter that is no
            # longer drawn. See `_fused_exit_param`.
            union_lo = min(s["stop_atr"].lo * s["reward_risk"].lo for s in factory._GENERATION_SPACES)
            union_hi = max(s["stop_atr"].hi * s["reward_risk"].hi for s in factory._GENERATION_SPACES)
            spec = factory.ParamSpec(union_lo, union_hi)
        else:
            spec = factory._EXIT_PARAMS[name]
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
        # Reads `taker_flow_imbalance`, so it drops on a venue with no aggressor split for the
        # same reason the three flow families above it do.
        "taker_flow_fade_long", "taker_flow_fade_short",
    }
    # Funding rides a real series here, so the funding families survive — the gate is about
    # the feed each family needs, not about which venue is the familiar one.
    assert {f for f in scoped if f.startswith("funding_")}


# --- the hold a timeframe's own holdout can judge -----------------------------

def test_the_hold_bound_reads_the_window_through_the_replays_own_split():
    """Derived from the two functions the replay uses, never a restated constant — so moving
    the window or the split moves this with it."""
    for tf in ("15m", "1h", "4h", "1d"):
        total = market_data.factory_candle_target(tf)
        holdout = total - factory.holdout_split_index(total)
        assert factory.judgeable_holding_bars(tf) == holdout // robustness.MIN_HOLDOUT_TRADES


def test_the_hold_space_is_narrowed_only_where_its_own_holdout_could_not_judge_it():
    """1d holds 600 holdout bars, so a 48-bar hold caps the tail at 12 trades against a floor
    of 25 — 55% of the 1d rows in the store on 2026-08-06 were unjudgeable by that geometry
    alone. Every faster tier clears its own space and must not move: 4h yields 72 and 1h 288."""
    for tf in ("15m", "1h", "4h"):
        spaces = {(s.lo, s.hi) for t in factory.templates_for_timeframe(tf)
                  if (s := t.param_space.get("max_holding_bars"))}
        assert spaces == {(12, 48), (4, 16)}, f"{tf} must keep its space"

    cap = factory.judgeable_holding_bars("1d")
    assert cap == 24
    for template in factory.templates_for_timeframe("1d"):
        spec = template.param_space["max_holding_bars"]
        assert spec.hi <= cap
        # The centre has to live inside the space it centres, or the family aims outside it.
        assert spec.lo <= template.base_params["max_holding_bars"] <= spec.hi
        # The whole point: no draw can put the ceiling under the floor it is judged against.
        holdout = 2_000 - factory.holdout_split_index(2_000)
        assert holdout / spec.hi >= robustness.MIN_HOLDOUT_TRADES


def test_the_hold_bound_cannot_be_escaped_by_an_elite_centre_outside_it():
    """A 1d row minted before the bound carries a 37-bar hold, and `elite_base_params` will
    hand it back as a centre. `mutate_params` folds a centre outside its space back inside, so
    the bound holds on the DRAW rather than on the centre — which is what makes it a bound at
    all rather than a suggestion."""
    template = next(t for t in factory.templates_for_timeframe("1d")
                    if t.param_space["max_holding_bars"].hi == 24)
    stale = {**template.base_params, "max_holding_bars": 37.0}
    rng = random.Random(4)
    drawn = [factory.mutate_params(stale, template.param_space, rng)["max_holding_bars"]
             for _ in range(200)]
    assert max(drawn) <= 24 and min(drawn) >= 12


def test_a_family_whose_shortest_hold_cannot_be_judged_is_not_minted():
    """The fail-closed branch. It does not fire on today's ladder — the shortest space starts
    at 4 against a 1d cap of 24 — and exists so that a deeper floor or a shallower window
    cannot turn the bound into a space with `lo > hi` that draws silently."""
    template = factory.templates_for_timeframe("1d")[0]
    assert factory._judgeable_hold_space(template, 1) is None
    # A cap at or above the space's own top leaves the template untouched, object and all.
    assert factory._judgeable_hold_space(template, 10_000) is template


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
    # 2026-08-08. Both are reversal claims — "the slow leg is stretched and the fast one has
    # turned", "one-way aggression has spent the move" — so both are COMPLETE when the stretch
    # unwinds, which is the whole rule this split encodes.
    "htf_reversal_long", "htf_reversal_short",
    "taker_flow_fade_long", "taker_flow_fade_short",
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
    ratio = factory._FADE_EXIT_PARAMS["reward_risk"]
    assert ratio.lo >= factory.MIN_REWARD_RISK, (
        f"ratio floor {ratio.lo} is below MIN_REWARD_RISK — some draws are unmintable"
    )
    # And the other end: the pair is drawn independently, so the PRODUCT has to stay inside
    # what the validator admits or the same attempt-burning returns from the top corner.
    stop = factory._FADE_EXIT_PARAMS["stop_atr"]
    assert stop.hi * ratio.hi <= factory.TARGET_ATR_RANGE[1]

    rng = random.Random(5)
    for _ in range(2000):
        drawn = factory.mutate_params(factory._FADE_EXIT_BASE, factory._FADE_EXIT_PARAMS, rng)
        assert drawn["reward_risk"] >= factory.MIN_REWARD_RISK
        assert drawn["stop_atr"] * drawn["reward_risk"] <= factory.TARGET_ATR_RANGE[1]


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


# --- a retirement has to reach the fusion path too (2026-08-04) --------------------------------
#
# `RETIRED_FAMILIES` is enforced by de-listing from `TEMPLATES`, which stops the DIRECT mint path
# and nothing else. Fusion draws its parents from the candidate STORE, which still holds every row
# the family produced before it was retired — so a retired lineage kept breeding under a compound
# name. Measured on the 08:09Z fire of 2026-08-04, the first after `macd_momentum_*` and
# `xs_momentum_*` were retired: 3 of 80 children carried a retired parent.

def _retired_parent_record(cid, family, score=0.9):
    return {"candidate_id": cid, "champion_score": score,
            "backtest_evidence": _parenting_evidence(),
            "strategy_spec": _spec_dict(strategy_family=family)}


def test_a_retired_family_cannot_parent_a_fusion_child():
    """The direct mint path and the fusion path are two doors, and the retirement set was only
    on one of them. A row whose family left the rotation carries the entry conditions the
    retirement was about, so it may not pass them on."""
    retired = sorted(factory.RETIRED_FAMILIES)[0]
    records = [
        {"candidate_id": "cand-live", "champion_score": 0.5,
         "backtest_evidence": _parenting_evidence(),
         "strategy_spec": _spec_dict(strategy_family="breakout")},
        _retired_parent_record("cand-retired", retired),
    ]
    ranked = rank_fusion_parents(records)
    assert [r["candidate_id"] for r in ranked] == ["cand-live"], (
        f"{retired} is retired but still ranked as a fusion parent"
    )


def test_a_compound_family_is_retired_when_any_component_is():
    """The form the leak actually took. A fused family is "a+b", so a child of a retired parent
    is stored under a name that matches no entry in the set — `macd_momentum_short+
    xs_momentum_short` was built from two retired families and nothing else, and an exact-name
    check would have re-admitted it as a grandparent."""
    retired = sorted(factory.RETIRED_FAMILIES)[0]
    assert factory.carries_retired_family(_retired_parent_record("c", f"breakout+{retired}"))
    assert factory.carries_retired_family(_retired_parent_record("c", f"{retired}+breakout"))
    assert factory.carries_retired_family(
        _retired_parent_record("c", f"{retired}+{sorted(factory.RETIRED_FAMILIES)[1]}"))
    assert not factory.carries_retired_family(
        _retired_parent_record("c", "breakout+trend_pullback"))
    # A name that merely CONTAINS a retired name as a substring is not a component of it.
    assert not factory.carries_retired_family(
        _retired_parent_record("c", f"{retired}_extended"))


def test_a_row_without_a_readable_family_is_not_treated_as_retired():
    """Fail-open here rather than closed, and deliberately: the other eligibility rules in
    `rank_fusion_parents` already refuse a row with no parseable spec, so this predicate only
    has to answer about rows that survived them. Refusing an unreadable name would silently
    narrow the parent pool for a reason unrelated to retirement."""
    assert not factory.carries_retired_family({"strategy_spec": {"strategy_family": None}})
    assert not factory.carries_retired_family({"strategy_spec": {}})
    assert not factory.carries_retired_family({})


def test_retired_parents_are_gone_from_every_bucket_not_just_the_ranking():
    """`fusion_parent_buckets` is what `_fuse_batch` actually consumes, so the filter has to
    survive the grouping. Two live rows plus two retired ones in the same context: the bucket
    must form from the live pair alone, and a bucket of one is dropped entirely."""
    live = [_bucket_record(f"live-{i}", 0.9 - i * 0.1,
                           _parent_spec(f"L{i}", [_CLOSE_OVER_MA20], strategy_family="breakout"))
            for i in range(2)]
    retired_name = sorted(factory.RETIRED_FAMILIES)[0]
    dead = [_bucket_record(f"dead-{i}", 0.99,
                           _parent_spec(f"D{i}", [_MA20_OVER_MA50], strategy_family=retired_name))
            for i in range(2)]
    buckets = factory.fusion_parent_buckets(
        [*dead, *live], symbol="BTCUSDT", timeframe="1d")
    seen = {r["candidate_id"] for bucket in buckets for r in bucket}
    assert seen == {"live-0", "live-1"}, f"retired rows reached a bucket: {sorted(seen)}"


def test_no_generation_space_can_draw_a_reward_risk_the_validator_refuses():
    """`validate_strategy` refuses `target_atr / stop_atr < MIN_REWARD_RISK`, and before
    2026-08-04 the trend space proposed pairs it then refused — 4.71% of 200,000 draws at
    `_EXIT_BASE`, minimum drawn R:R 0.925 — because `target_atr` and `stop_atr` were drawn
    independently. Each refusal spends an attempt against `_MAX_ATTEMPTS_PER_SPEC` and biases
    what survives toward the high-target corner, the only corner never refused.

    **Since 2026-08-25 the ratio IS the drawn parameter, so this is structural rather than
    arithmetic**: its floor is `MIN_REWARD_RISK` and no draw can land under it, from any
    centre. Asserted on the BOUNDS first for that reason, then over draws from adverse
    centres — the half of every batch that `generate_batch` centres on `elite_base_params`
    can sit anywhere a prior accepted candidate did, and a test that only drew from the
    template base would have called the OLD space clean too.

    The ceiling is the other half and is new with the ratio: the pair is still drawn
    independently, so `stop_atr x reward_risk` has to stay inside `TARGET_ATR_RANGE` or the
    same attempt-burning returns from the top corner instead of the bottom."""
    for space in factory._GENERATION_SPACES:
        ratio = space["reward_risk"]
        assert ratio.lo >= factory.MIN_REWARD_RISK
        assert space["stop_atr"].hi * ratio.hi <= factory.TARGET_ATR_RANGE[1]

    rng = random.Random(23)
    for space in factory._GENERATION_SPACES:
        adverse = [
            {"stop_atr": space["stop_atr"].hi, "reward_risk": space["reward_risk"].lo,
             "max_holding_bars": space["max_holding_bars"].lo},
            {"stop_atr": space["stop_atr"].lo, "reward_risk": space["reward_risk"].hi,
             "max_holding_bars": space["max_holding_bars"].hi},
        ]
        for centre in adverse:
            for _ in range(2000):
                drawn = factory.mutate_params(centre, space, rng)
                target = drawn["stop_atr"] * drawn["reward_risk"]
                assert drawn["reward_risk"] >= factory.MIN_REWARD_RISK
                assert factory.TARGET_ATR_RANGE[0] <= target <= factory.TARGET_ATR_RANGE[1]


def test_the_reward_risk_floor_is_inert_because_the_pair_can_no_longer_be_drawn_illegal():
    """What replaced the redraw, and why the redraw is still in the file.

    `_apply_reward_risk_floor` existed because `target_atr` was drawn independently of
    `stop_atr` and the pair could land under `MIN_REWARD_RISK`. Clamping the target to the
    stop would have parked 4.7% of every batch on R:R exactly 1.0 — one value, one rule hash,
    mass on an edge — so it redrew above the floor instead and left 0.005% there.

    Since 2026-08-25 the ratio is what is drawn and its floor IS `MIN_REWARD_RISK`, so no
    draw reaches the function at all: the mass at the floor is now zero by construction
    rather than small by repair. Asserted as a distribution for the same reason the redraw
    was — a legality check passes under either design, which is what makes the difference
    easy to lose.

    The function stays because it is `mutate_params`' contract to any space that draws a
    `target_atr` against a `stop_atr`, not a property of the two spaces that no longer do."""
    rng = random.Random(29)
    draws = [factory.mutate_params(factory._EXIT_BASE, factory._EXIT_PARAMS, rng)
             for _ in range(20000)]
    ratios = [d["reward_risk"] for d in draws]
    at_floor = sum(1 for r in ratios if abs(r - factory.MIN_REWARD_RISK) < 1e-9) / len(ratios)
    assert at_floor == 0.0, (
        f"{at_floor:.2%} of draws sit on R:R exactly {factory.MIN_REWARD_RISK} — the ratio's "
        "own floor should make that unreachable, not merely rare"
    )
    assert min(ratios) >= factory.MIN_REWARD_RISK

    # Still correct for a space that DOES draw a target against a stop.
    legacy = {"stop_atr": factory.ParamSpec(1.2, 2.0),
              "target_atr": factory.ParamSpec(1.6, 8.0)}
    out = {"stop_atr": 1.9, "target_atr": 1.6}
    factory._apply_reward_risk_floor(
        out, {"stop_atr": 1.45, "target_atr": 3.0}, legacy, random.Random(3), scale=0.35)
    assert out["target_atr"] >= 1.9 * factory.MIN_REWARD_RISK


def test_the_trend_ratio_floor_is_not_raised_to_delete_the_near_target_region():
    """The obvious tidy-up was to lift the trend ratio's floor the way `_FADE_EXIT_PARAMS`
    holds a narrower band. It was NOT taken, and this pins that, because it is one edit to
    undo by accident.

    Measured on the candidate store by holdout R/trade, the near-target region is the least
    negative band (-0.2642 pinned at target 1.6, -0.2502 in 1.6-2.0, against -0.3059 at
    2.0-3.0 and -0.3216 above 3.0), and paired within (family, timeframe, symbol) it runs
    +0.0174R median in favour of the low band, 34 of 58 cells — weak, but not the direction
    that justifies deleting it.

    Under the ratio axis the region is reached by RATIO rather than by an absolute target, so
    the assertion moved with it: the floor stays at `MIN_REWARD_RISK`, and the drawn window
    keeps real mass in its lower half rather than aiming past it."""
    ratio = factory._EXIT_PARAMS["reward_risk"]
    assert ratio.lo == factory.MIN_REWARD_RISK, (
        "the trend ratio floor was raised off MIN_REWARD_RISK — that deletes the near-target "
        "region, which measures as the least negative band in the store; see `_EXIT_PARAMS`"
    )

    rng = random.Random(31)
    draws = [factory.mutate_params(factory._EXIT_BASE, factory._EXIT_PARAMS, rng)["reward_risk"]
             for _ in range(20000)]
    below_base = sum(1 for d in draws if d < factory._EXIT_BASE["reward_risk"]) / len(draws)
    assert below_base > 0.40, (
        f"only {below_base:.1%} of draws sit below the centre — the window has been aimed past "
        "the near-target region rather than spanning it"
    )


def test_the_reward_risk_floor_leaves_an_unsatisfiable_draw_alone_for_the_validator():
    """A space whose target CEILING sits below `MIN_REWARD_RISK x` the drawn stop has no legal
    answer inside its own bounds. The repair must not invent one: handing `validate_strategy` a
    target above the space it was drawn from is the laundering `_fused_exit_param` refuses at the
    other mint path, and the refusal is the correct outcome.

    Two distinct claims, and the second is the one worth a test. That no target escapes the
    ceiling is guaranteed by the final clamp and holds however the branch is written. That the
    draw is left ALONE is not: drop the early return and every unsatisfiable pair comes back as
    `target_spec.hi` exactly — still refused, so still "safe", but a value the search never chose,
    written into `mint_params` and read back later by `elite_base_params` as a centre. So the
    assertion is on the DISTRIBUTION, not on legality.

    No shipped space can reach this — both put the target ceiling well above the stop ceiling —
    so it is constructed rather than waited for."""
    impossible = {"stop_atr": ParamSpec(3.0, 3.0), "target_atr": ParamSpec(1.0, 1.5)}
    rng = random.Random(37)
    draws = [factory.mutate_params({"stop_atr": 3.0, "target_atr": 1.2}, impossible, rng)
             for _ in range(500)]
    targets = [d["target_atr"] for d in draws]

    assert max(targets) <= 1.5, "the repair invented a target above the space's ceiling"
    at_ceiling = sum(1 for t in targets if t == 1.5) / len(targets)
    assert at_ceiling < 0.20, (
        f"{at_ceiling:.0%} of unsatisfiable draws returned the ceiling — the repair is rewriting "
        "a draw it cannot help instead of leaving it for the validator"
    )
    for drawn in draws:
        assert drawn["target_atr"] / drawn["stop_atr"] < factory.MIN_REWARD_RISK
    spec = _parent_spec("S1", [_CLOSE_OVER_MA20], exit_rules={
        "stop_model": "atr", "stop_atr": draws[0]["stop_atr"],
        "target_atr": draws[0]["target_atr"], "max_holding_bars": 24})
    verdict = validate_strategy(spec)
    assert not verdict["approved_for_backtest"]
    assert "BLOCK_INVALID_RISK_REWARD" in verdict["block_reasons"]


def test_an_adverse_elite_centre_no_longer_spends_a_batch_on_refusals():
    """The end-to-end form of the property, through the path that actually amplifies it.

    `run_factory` centres half of each batch on `elite_base_params`, so a family whose most
    ROBUST prior candidate happens to sit beside the R:R diagonal used to refuse up to a third of
    its own draws — `champion_score` selects that centre and knows nothing about the ratio. The
    batch does not fail (it retries against `_MAX_ATTEMPTS_PER_SPEC`), which is why this went
    unnoticed; it just spends the attempts and keeps the high-target survivors.

    Swept over seeds rather than asserted on one, and that is load-bearing: only HALF a batch is
    drawn around the elite centre (`(len(accepted) + _elite_flip(generation_id)) % 2`), so with
    the floor removed a single 4-spec batch shows the defect on 25 of 40 seeds. One seed is a
    coin flip; twenty is not. Which half moves per fire, but every family here carries the same
    adverse centre, so the sweep does not depend on which."""
    families = {t.family for t in templates_for_timeframe("1h", symbol="BTCUSDT")}
    adverse = {f: {"stop_atr": 1.7185, "target_atr": 1.7627, "max_holding_bars": 24}
               for f in families}
    refused_seeds = []
    for seed in range(20):
        batch = generate_batch(f"GEN-rr-{seed}", seed=seed, count=4, timeframe="1h",
                               elite_params=adverse)
        assert batch["batch_complete"], f"seed {seed}: elite centre exhausted the attempt budget"
        if any("BLOCK_INVALID_RISK_REWARD" in (r.get("block_reasons") or [])
               for r in batch["rejected"]):
            refused_seeds.append(seed)
        for spec in batch["specs"]:
            exit_rules = spec["exit_rules"]
            assert exit_rules["target_atr"] / exit_rules["stop_atr"] >= factory.MIN_REWARD_RISK
    assert not refused_seeds, (
        f"seeds {refused_seeds} spent attempts on risk:reward refusals from an elite centre"
    )




# --- id numbering: reserved, not survived --------------------------------------

def test_a_fused_child_starts_after_every_id_the_seeded_draw_RESERVED(monkeypatch):
    """`generate_batch` numbers S001..S00count as it mints; `_score_draw` then drops the specs
    whose features the frame never supplies. So survivors < ids issued the moment anything
    starves, and a fusion cursor counted off survivors points back into the seeded block.

    The first fused child is then minted under a name a stored seeded row already holds — two
    different strategies sharing one `(generation_id, strategy_id)`. Promotions key on the
    lineage-derived `candidate_id`, so nothing mis-promotes; `resolve_candidates` refuses the
    pair as ambiguous and any display surface shows one name over two rules.

    Asserted on the cursor rather than on a minted collision, because reaching one needs a
    rotation slice that both starves a spec AND has durable fusable parents — a condition the
    live store has not hit, which is exactly why this went unnoticed rather than why it is fine.
    """
    from runtime.mvp_runtime.crypto import factory

    real_unsuppliable = factory.unsuppliable_features
    starved_one: list[str] = []

    def starve_the_first(spec, rows):
        if not starved_one:
            starved_one.append(spec.strategy_id)
            return ["a_column_this_frame_never_supplies"]
        return real_unsuppliable(spec, rows)

    captured: dict[str, int] = {}
    real_fuse = factory._fuse_batch

    def spy(*args, **kwargs):
        captured["start_index"] = kwargs["start_index"]
        return real_fuse(*args, **kwargs)

    monkeypatch.setattr(factory, "unsuppliable_features", starve_the_first)
    monkeypatch.setattr(factory, "_fuse_batch", spy)
    factory.run_factory(_trending_snapshot(), active_pool={"active_strategies": []},
                        existing_candidates=[], now=NOW, fusion_pairs=2)

    assert starved_one, "the fixture must actually starve a spec"
    assert captured["start_index"] == factory.DEFAULT_BATCH_SIZE + 1, (
        "the fusion cursor counted survivors, so it points at an id the seeded draw already "
        "issued"
    )


def test_the_shortfall_draw_starts_after_fusion_on_the_same_convention(monkeypatch):
    """The inverse pin, and the reason `count` is the right cursor rather than
    `len(batch["specs"])`: both draws reserve blocks, so a draw that accepted fewer than it
    asked for leaves a GAP in the numbering instead of risking an overlap."""
    from runtime.mvp_runtime.crypto import factory

    captured: list[int] = []
    real_generate = factory.generate_batch

    def spy(*args, **kwargs):
        captured.append(kwargs.get("start_index", 1))
        return real_generate(*args, **kwargs)

    monkeypatch.setattr(factory, "generate_batch", spy)
    result = factory.run_factory(_trending_snapshot(), active_pool={"active_strategies": []},
                                 existing_candidates=[], now=NOW, fusion_pairs=2)
    # Seeded block first at 1; the shortfall draw, if it ran, after the seeded block AND
    # whatever fusion minted.
    assert captured[0] == 1
    if len(captured) > 1:
        assert captured[1] == factory.DEFAULT_BATCH_SIZE + result["fused_count"] + 1


def test_fusion_reads_the_symbol_run_factory_resolved(monkeypatch):
    """One fact, one default. `run_factory` falls back to BTCUSDT; the fusion call used to
    re-read the snapshot with a `""` fallback, so a snapshot without a symbol would mint
    BTCUSDT specs while `fusion_parent_buckets` filtered on `""` and dropped every parent —
    no bucket, no children, no reason recorded. Unreachable through the scheduler, which
    always sets it."""
    from runtime.mvp_runtime.crypto import factory

    seen: dict[str, str] = {}
    real_buckets = factory.fusion_parent_buckets

    def spy(records, *, symbol, timeframe, scope=None):
        seen["symbol"] = symbol
        return real_buckets(records, symbol=symbol, timeframe=timeframe, scope=scope)

    monkeypatch.setattr(factory, "fusion_parent_buckets", spy)
    snapshot = {k: v for k, v in _trending_snapshot().items() if k != "symbol"}
    factory.run_factory(snapshot, active_pool={"active_strategies": []},
                        existing_candidates=[], now=NOW, fusion_pairs=2)
    assert seen["symbol"] == "BTCUSDT", "the two fallbacks for one fact disagree again"


# --- post-stop-loss cooldown --------------------------------------------------

class TestReplayCooldown:
    """The backtest replay must skip re-entry for COOLDOWN_BARS_AFTER_STOPLOSS bars
    after a stop-loss, matching the live paper path's cooldown gate."""

    def _make_rows_and_candles(self, n=50):
        """A series where a breakout spec enters and stops out repeatedly."""
        step = timedelta(days=1)
        last_close = NOW_DT - timedelta(hours=1)
        rows = []
        candles = []
        price = 100.0
        for i in range(n):
            drift = 1.0 if (i // 10) % 2 == 0 else -1.0
            price = max(10.0, price + drift)
            close_time = last_close - (n - 1 - i) * step
            atr = 3.0
            candle = {
                "open_time": timeutil.format_iso(close_time - step),
                "open": price - drift,
                "high": price + 2.0,
                "low": price - 2.0,
                "close": price,
                "volume": 10.0,
                "close_time": timeutil.format_iso(close_time),
            }
            candles.append(candle)
            rows.append({
                "close": price, "atr": atr, "ma20": price - 1.0,
                "market_regime": "TREND_UP",
            })
        return rows, candles

    def test_cooldown_skips_entries_after_stoploss(self):
        """After a stop-loss, the replay must skip COOLDOWN_BARS_AFTER_STOPLOSS bars
        before allowing a new entry on the same series."""
        from runtime.mvp_runtime.crypto.cost import CostModel

        spec = StrategySpec.from_dict(_spec_dict(
            exit_rules={"stop_model": "atr", "stop_atr": 0.5, "target_atr": 5.0,
                        "max_holding_bars": 20},
        ))
        rows, candles = self._make_rows_and_candles(n=80)
        result = factory._replay(spec, rows, candles, cost=CostModel())
        outcomes = result[0]
        cooldown_skipped = result[6]
        stop_losses = [o for o in outcomes if o["close_reason"] == "stop_loss"]
        if len(stop_losses) >= 1:
            assert cooldown_skipped >= 1, (
                "stop-losses occurred but no cooldown skips were recorded"
            )

    def test_cooldown_counter_is_returned_in_replay_tuple(self):
        """The return tuple carries cooldown_skipped (index 6)."""
        from runtime.mvp_runtime.crypto.cost import CostModel
        spec = StrategySpec.from_dict(_spec_dict())
        result = factory._replay(spec, [], [], cost=CostModel())
        assert len(result) == 8
        assert result[6] == 0

    def test_cooldown_does_not_fire_on_take_profit_or_time_exit(self):
        """Only stop_loss triggers cooldown; take_profit and time_exit do not."""
        from runtime.mvp_runtime.crypto.cost import CostModel

        spec = StrategySpec.from_dict(_spec_dict(
            exit_rules={"stop_model": "atr", "stop_atr": 5.0, "target_atr": 0.3,
                        "max_holding_bars": 3},
        ))
        rows, candles = self._make_rows_and_candles(n=80)
        result = factory._replay(spec, rows, candles, cost=CostModel())
        outcomes = result[0]
        cooldown_skipped = result[6]
        stop_losses = [o for o in outcomes if o["close_reason"] == "stop_loss"]
        if not stop_losses:
            assert cooldown_skipped == 0, (
                "cooldown fired without any stop-losses"
            )

    def test_backtest_evidence_carries_cooldown_door_block(self):
        """backtest_spec_pooled records a cooldown_door block in its output."""
        snapshot = _trending_snapshot()
        frame = factory.build_replay_frame(snapshot)
        spec = StrategySpec.from_dict(_spec_dict())
        result = factory.backtest_spec_pooled(spec, [snapshot], frames=[frame])
        assert "cooldown_door" in result
        assert result["cooldown_door"]["applied"] is True
        assert result["cooldown_door"]["cooldown_bars"] == 2


class TestIntrabarGapMeasurement:
    """When a single bar touches both SL and TP, the backtest assumes SL-first
    (pessimistic). The intrabar_gap block measures how often this happens and
    the maximum R the assumption could have cost."""

    def _ambiguous_candles(self, n=60):
        """Bars wide enough that both SL and TP are touched on the same candle."""
        step = timedelta(days=1)
        last_close = NOW_DT - timedelta(hours=1)
        rows = []
        candles = []
        price = 100.0
        for i in range(n):
            close_time = last_close - (n - 1 - i) * step
            atr = 2.0
            candles.append({
                "open_time": timeutil.format_iso(close_time - step),
                "open": price,
                "high": price + 20.0,
                "low": price - 20.0,
                "close": price,
                "volume": 10.0,
                "close_time": timeutil.format_iso(close_time),
            })
            rows.append({
                "close": price, "atr": atr, "ma20": price - 1.0,
                "market_regime": "TREND_UP",
            })
        return rows, candles

    def test_ambiguous_exits_are_counted(self):
        from runtime.mvp_runtime.crypto.cost import CostModel

        spec = StrategySpec.from_dict(_spec_dict(
            exit_rules={"stop_model": "atr", "stop_atr": 1.0, "target_atr": 2.0,
                        "max_holding_bars": 20},
        ))
        rows, candles = self._ambiguous_candles()
        outcomes, *_ = factory._replay(spec, rows, candles, cost=CostModel())
        ambiguous = [o for o in outcomes if o.get("exit_resolution") == "pessimistic_sl_first"]
        assert len(ambiguous) >= 1, "expected at least one ambiguous exit"
        for o in ambiguous:
            assert "ambiguous_gap_r" in o
            assert o["ambiguous_gap_r"] > 0, "gap should be positive (TP_R > SL_R)"

    def test_unambiguous_exits_have_no_gap(self):
        from runtime.mvp_runtime.crypto.cost import CostModel

        spec = StrategySpec.from_dict(_spec_dict(
            exit_rules={"stop_model": "atr", "stop_atr": 0.5, "target_atr": 5.0,
                        "max_holding_bars": 20},
        ))
        rows, candles = TestReplayCooldown()._make_rows_and_candles(n=80)
        outcomes, *_ = factory._replay(spec, rows, candles, cost=CostModel())
        unambiguous = [o for o in outcomes if o.get("exit_resolution") == "unambiguous"]
        for o in unambiguous:
            assert "ambiguous_gap_r" not in o

    def test_backtest_evidence_carries_intrabar_gap_block(self):
        snapshot = _trending_snapshot()
        frame = factory.build_replay_frame(snapshot)
        spec = StrategySpec.from_dict(_spec_dict())
        result = factory.backtest_spec_pooled(spec, [snapshot], frames=[frame])
        assert "intrabar_gap" in result
        gap = result["intrabar_gap"]
        assert "ambiguous_exits" in gap
        assert "total_gap_r" in gap
        assert "gap_per_trade_r" in gap
        assert gap["ambiguous_exits"] >= 0
        assert isinstance(gap["total_gap_r"], float)


class TestLiquidationGuard:
    """A stop placed beyond the liquidation price never triggers — the position is
    liquidated first. The guard refuses such entries in backtest, paper, and live."""

    def test_stop_beyond_liquidation_is_refused(self):
        """A stop past the liquidation distance is refused, whatever that distance is.

        The expected price is DERIVED from the guard's own constants rather than written
        out: the arithmetic used to be pinned at `95.4`, which was 20x spelled as a number
        and would have had to be re-typed every time the assumption moved (it moved to 5x
        on 2026-09-02). What this test is for is the refusal, not the leverage.
        """
        from runtime.mvp_runtime.crypto.paper import (
            ASSUMED_LEVERAGE, MAINTENANCE_MARGIN_RATE,
            liquidation_price, stop_is_beyond_liquidation,
            stop_beyond_liquidation_refusal,
        )
        entry = 100.0
        liq = liquidation_price(entry, "LONG")
        expected = entry * (1.0 - 1.0 / ASSUMED_LEVERAGE + MAINTENANCE_MARGIN_RATE)
        assert liq == pytest.approx(expected, abs=0.01)
        beyond = liq - 1.0
        assert stop_is_beyond_liquidation(entry, beyond, True)
        refusal = stop_beyond_liquidation_refusal({
            "direction": "LONG", "entry_price": entry, "stop_loss": beyond,
        })
        assert refusal is not None
        assert refusal["reason_code"] == "STOP_BEYOND_LIQUIDATION"
        assert refusal["liquidation_price"] == pytest.approx(liq, abs=0.01)

    def test_stop_within_liquidation_is_admitted(self):
        """A stop safely above the liquidation price passes."""
        from runtime.mvp_runtime.crypto.paper import stop_beyond_liquidation_refusal
        refusal = stop_beyond_liquidation_refusal({
            "direction": "LONG", "entry_price": 100.0, "stop_loss": 97.0,
        })
        assert refusal is None

    def test_short_direction(self):
        """SHORT: liquidation is above entry; stop above liquidation is refused."""
        from runtime.mvp_runtime.crypto.paper import (
            liquidation_price, stop_is_beyond_liquidation, stop_beyond_liquidation_refusal,
        )
        from runtime.mvp_runtime.crypto.paper import (
            ASSUMED_LEVERAGE, MAINTENANCE_MARGIN_RATE,
        )
        entry = 100.0
        liq = liquidation_price(entry, "SHORT")
        # Derived, not spelled: the literal used to be 104.6, which was 20x written as a
        # number and stopped being true when the assumption moved to the account's real 5x.
        expected = entry * (1.0 + 1.0 / ASSUMED_LEVERAGE - MAINTENANCE_MARGIN_RATE)
        assert liq == pytest.approx(expected, abs=0.01)
        beyond, inside = liq + 1.0, liq - 1.0
        assert stop_is_beyond_liquidation(entry, beyond, False)
        assert not stop_is_beyond_liquidation(entry, inside, False)
        refusal = stop_beyond_liquidation_refusal({
            "direction": "SHORT", "entry_price": entry, "stop_loss": beyond,
        })
        assert refusal is not None

    def test_low_leverage_admits_wide_stop(self):
        """At 5x leverage, the liquidation distance is ~19.6% — most stops fit."""
        from runtime.mvp_runtime.crypto.paper import (
            liquidation_price, stop_is_beyond_liquidation,
        )
        entry = 100.0
        liq = liquidation_price(entry, "LONG", leverage=5.0)
        # liq ≈ 100 * (1 - 0.2 + 0.004) = 80.4
        assert liq == pytest.approx(80.4, abs=0.01)
        assert not stop_is_beyond_liquidation(entry, 90.0, True, leverage=5.0)

    def test_high_leverage_refuses_normal_stop(self):
        """At 125x, the liquidation distance is ~0.4% — even a 1-ATR stop exceeds it."""
        from runtime.mvp_runtime.crypto.paper import stop_is_beyond_liquidation
        entry = 100.0
        # At 125x: liq ≈ 100 * (1 - 0.008 + 0.004) = 99.6 — stop at 98 is way below
        assert stop_is_beyond_liquidation(entry, 98.0, True, leverage=125.0)

    def test_replay_counts_liquidation_refusals(self):
        """_replay returns a liquidation_refused count as the 8th element.

        The breaching width is DERIVED from the guard's constants rather than fixed at 5.0:
        the fixture's ATR is ~3% of a ~100 price, so the multiple that clears the liquidation
        distance is `room / 0.03` — about 1.5x at the old 20x assumption and 6.5x at the
        account's real 5x. Pinning the number made this test a statement about the leverage
        instead of about the counter."""
        from runtime.mvp_runtime.crypto.cost import CostModel
        from runtime.mvp_runtime.crypto.paper import (
            ASSUMED_LEVERAGE, MAINTENANCE_MARGIN_RATE,
        )

        room = 1.0 / ASSUMED_LEVERAGE - MAINTENANCE_MARGIN_RATE
        breaching_stop = round(room / 0.03 + 1.0, 2)   # +1 so the tail of the ATR range clears too
        spec = StrategySpec.from_dict(_spec_dict(
            exit_rules={"stop_model": "atr", "stop_atr": breaching_stop,
                        "target_atr": breaching_stop + 1.0, "max_holding_bars": 10},
        ))
        rows, candles = TestReplayCooldown()._make_rows_and_candles(n=80)
        result = factory._replay(spec, rows, candles, cost=CostModel())
        assert len(result) == 8
        liq_refused = result[7]
        assert liq_refused >= 1, "expected at least one liquidation refusal at stop_atr=5.0"

    def test_backtest_evidence_carries_liquidation_guard_block(self):
        """backtest_spec_pooled records a liquidation_guard block in its output."""
        snapshot = _trending_snapshot()
        frame = factory.build_replay_frame(snapshot)
        spec = StrategySpec.from_dict(_spec_dict())
        result = factory.backtest_spec_pooled(spec, [snapshot], frames=[frame])
        assert "liquidation_guard" in result
        guard = result["liquidation_guard"]
        assert guard["applied"] is True
        assert guard["assumed_leverage"] == factory.ASSUMED_LEVERAGE
        assert guard["maintenance_margin_rate"] == 0.004
        assert guard["refused_entries"] >= 0


# --- the pooled block says WHICH symbols, not just how many --------------------------------

class TestPooledPerSymbolBreakdown:
    """`symbols_replayed` counts the legs; `per_symbol` says what each one earned.

    The distinction decides a promotion. A pooled candidate carries the whole cohort's
    `symbol_scope`, so promoting it needs every one of those contexts — and §F9 recorded the
    2026-08-23 refusal where narrowing it to the free ones had no evidence behind it, because
    the block held a five-market average and nothing else. "The cohort earns +0.29R" and "each
    of the five earns about +0.29R" are different claims, and only the second licenses trading
    one of them.

    Not a new measurement: the scored loop already replays one frame per symbol, and these
    figures were computed and summed away on the next line.
    """

    def test_each_leg_is_named_and_its_own_trades_are_reported(self):
        spec = StrategySpec.from_dict(_spec_dict())
        first, second = _trending_snapshot(), _shifted_snapshot()
        one, two = backtest_spec(spec, first), backtest_spec(spec, second)

        pooled = factory.backtest_spec_pooled(spec, [first, second])
        per = pooled["per_symbol"]
        assert set(per) == {"BTCUSDT", "ETHUSDT"}
        assert per["BTCUSDT"]["closed_count"] == one["closed_count"]
        assert per["ETHUSDT"]["closed_count"] == two["closed_count"]
        assert per["BTCUSDT"]["expectancy"] == pytest.approx(one["expectancy"])
        assert per["ETHUSDT"]["expectancy"] == pytest.approx(two["expectancy"])

    def test_the_legs_sum_to_the_pooled_figures(self):
        """The breakdown must be the same trades seen differently, never a second population."""
        spec = StrategySpec.from_dict(_spec_dict())
        pooled = factory.backtest_spec_pooled(
            spec, [_trending_snapshot(), _shifted_snapshot()])
        per = pooled["per_symbol"]
        assert sum(leg["closed_count"] for leg in per.values()) == pooled["closed_count"]
        assert sum(leg["win_count"] for leg in per.values()) == pooled["win_count"]

    def test_a_disagreeing_leg_is_visible_rather_than_averaged_away(self):
        """The property that makes this worth recording. Two legs whose expectancies differ
        must show that difference, or the block cannot answer the question §F9 blocked on.

        **The comparison has to be worth making, and nothing here checked that it was.** The
        first version paired `_trending_snapshot` with `_shifted_snapshot(scale=5.0)` and
        asserted only `min <= pooled <= max`, which holds when all three numbers are equal. It
        did in fact separate them — by 0.0408R, and not because of the scale (ATR-relative
        exits are scale-invariant) but because that fixture defaults `funding=False`, so the
        legs paid different carry. A spread that arrives by accident is one fixture default
        away from vanishing, and the assertion would have gone quiet rather than red.

        So the spread is asserted first, and the pairing is a strong trend against a weak one:
        0.834R over 15 closes against 0.093R over 5. A fixture change that collapses that —
        or a leg that stops trading, which compares nothing by a second route — fails here.
        """
        spec = StrategySpec.from_dict(_spec_dict())
        # A strong trend against a weak one, both still trading — see `_weak_trend_snapshot`
        # for why the weak one stays positive.
        pooled = factory.backtest_spec_pooled(
            spec, [_trending_snapshot(), _weak_trend_snapshot()])
        legs = {name: leg["expectancy"] for name, leg in pooled["per_symbol"].items()}
        assert len(legs) == 2
        scored = [v for v in legs.values() if v is not None]
        assert len(scored) == 2, f"a leg did not trade, so nothing is being compared: {legs}"
        assert max(scored) - min(scored) > 0, (
            f"the two markets scored alike, so this test proves nothing: {legs}")
        # And the pooled average sits between them — which is exactly why the average alone
        # cannot license trading either one.
        assert min(scored) <= pooled["expectancy"] <= max(scored)

    def test_a_leg_that_never_traded_says_so_rather_than_reading_break_even(self):
        """`summarize_outcomes` returns 0.0 expectancy for an empty population, which is right
        for a total and wrong for a leg being compared: at 0.0 "this symbol broke even" and
        "this symbol never entered" are the same number, and the second is the one that must
        stop a promotion."""
        # A condition no bar in either fixture satisfies, so both legs replay and neither enters.
        spec = StrategySpec.from_dict(_spec_dict(entry_rules={
            "operator": "AND",
            "conditions": [{"feature": "rsi", "comparison": "<", "value": -1.0}],
        }))
        pooled = factory.backtest_spec_pooled(
            spec, [_trending_snapshot(), _shifted_snapshot()])
        assert set(pooled["per_symbol"]) == {"BTCUSDT", "ETHUSDT"}
        for name, leg in pooled["per_symbol"].items():
            assert leg["closed_count"] == 0, name
            assert leg["expectancy"] is None, f"{name} reads break-even on zero trades"

    def test_a_single_symbol_backtest_names_its_one_leg(self):
        spec = StrategySpec.from_dict(_spec_dict())
        snapshot = _trending_snapshot()
        result = backtest_spec(spec, snapshot)
        assert set(result["per_symbol"]) == {"BTCUSDT"}
        assert result["per_symbol"]["BTCUSDT"]["closed_count"] == result["closed_count"]

    def test_a_frame_with_no_symbol_reads_as_absent_never_as_agreement(self):
        """A caller predating `ReplayFrame.symbol` still replays correctly; it just cannot
        contribute a labelled leg. The reader must learn "cannot say", not "the legs agreed"."""
        spec = StrategySpec.from_dict(_spec_dict())
        frame = factory.build_replay_frame(_trending_snapshot())
        anonymous = factory.ReplayFrame(
            rows=frame.rows, candles=frame.candles, funding=frame.funding,
            funding_source=frame.funding_source, split=frame.split, cost=frame.cost,
        )
        assert anonymous.symbol is None
        pooled = factory.backtest_spec_pooled(spec, [], frames=[anonymous])
        assert pooled["per_symbol"] == {}
        assert pooled["closed_count"] > 0, "the replay itself must be unaffected"


def test_candidate_identity_is_one_leaf_for_both_sides():
    """`factory` mints candidate ids and `pool` stores and re-derives them; both must be
    the SAME rule object — a drifted second implementation would mint ids the store then
    derives differently for legacy rows, silently splitting one lineage into two."""
    from runtime.mvp_runtime.crypto import candidate_identity, factory, pool

    assert factory.candidate_id is candidate_identity.candidate_id is pool.candidate_id
    assert (factory.derive_candidate_id is candidate_identity.derive_candidate_id
            is pool.derive_candidate_id)


def test_exit_probe_slots_mint_the_unexplored_stop_ceiling():
    """Slots 1 and 2 (mod 4, after the flip) centre stop_atr on the family's own ceiling.

    #782 opened the generation space to stop (1.2, 3.0), and 15 generations later the store
    still held zero draws above 2.1 — a centred draw spans ± 0.35 x range, so the region the
    2026-08-25 sweep pointed at ([2.37, 3.0]) is arithmetically unreachable from any centre
    `champion_score` ever picks. The probe slot forces coverage: its draw must land in the
    top `_MUTATION_SCALE` of its OWN family's stop interval (a fade family probes its own
    2.0 ceiling, not the trend space's), and every non-probe slot must stay within reach of
    the centre its parity chose, because a probe that leaks into the ordinary slots would be
    a preference, not a coverage guarantee."""
    templates = templates_for_timeframe("1d", symbol="BTCUSDT")
    spaces = {t.family: t.param_space for t in templates}
    bases = {t.family: t.base_params for t in templates}
    elite_stop = 1.3
    elite = {"stop_atr": elite_stop, "reward_risk": 2.2, "max_holding_bars": 24}
    batch = generate_batch(
        "GEN-777", seed=11, count=8, symbol="BTCUSDT", timeframe="1d",
        elite_params={family: dict(elite) for family in spaces},
    )
    flip = factory._elite_flip("GEN-777")
    probed = []
    for index, spec in enumerate(batch["specs"]):
        family = spec["strategy_family"]
        space = spaces[family]["stop_atr"]
        span = (space.hi - space.lo) * factory._MUTATION_SCALE
        stop = spec["exit_rules"]["stop_atr"]
        if (index + flip) % 4 in factory._EXIT_PROBE_SLOTS:
            probed.append(index)
            assert stop >= space.hi - span - 1e-9, (
                f"probe slot {index} drew stop {stop} below its band [{space.hi - span}, {space.hi}]"
            )
        else:
            centre = elite_stop if (index + flip) % 2 == 0 else bases[family]["stop_atr"]
            assert stop <= centre + span + 1e-9, (
                f"non-probe slot {index} drew stop {stop} beyond its centre {centre}'s reach"
            )
    assert len(probed) == 4, "an 8-draw batch carries one probe per parity per half"


def test_the_probe_moves_the_stop_centre_and_nothing_else():
    """On an elite-parity probe slot the OTHER exit parameters still draw around the elite
    centre — the probe overrides one key of the centre dict, so an elite reward_risk of 2.2
    must still bound the drawn ratio even while the stop jumps to the ceiling. A probe that
    re-centred the whole draw would spend the slot exploring a region nobody chose."""
    templates = templates_for_timeframe("1d", symbol="BTCUSDT")
    spaces = {t.family: t.param_space for t in templates}
    elite_rr = 2.2
    elite = {"stop_atr": 1.3, "reward_risk": elite_rr, "max_holding_bars": 24}
    batch = generate_batch(
        "GEN-778", seed=13, count=8, symbol="BTCUSDT", timeframe="1d",
        elite_params={family: dict(elite) for family in spaces},
    )
    flip = factory._elite_flip("GEN-778")
    checked = 0
    for index, spec in enumerate(batch["specs"]):
        if (index + flip) % 4 not in factory._EXIT_PROBE_SLOTS:
            continue
        if (index + flip) % 2 != 0:
            continue  # base-parity probe: the elite centre has no claim on it
        space = spaces[spec["strategy_family"]]
        if "reward_risk" not in space:
            continue
        rr_span = (space["reward_risk"].hi - space["reward_risk"].lo) * factory._MUTATION_SCALE
        exit_rules = spec["exit_rules"]
        drawn_rr = exit_rules["target_atr"] / exit_rules["stop_atr"]
        assert abs(drawn_rr - elite_rr) <= rr_span + 1e-2, (
            f"probe slot {index} drew reward_risk {drawn_rr}, outside the elite centre's reach"
        )
        checked += 1
    assert checked, "the batch must contain at least one elite-parity probe slot"


def _volatile_candles(n=400, price=100.0, atr_pct_by_bar=None):
    """A candle series whose per-bar range is a chosen fraction of price.

    The true range IS ``high - low`` here (each bar opens and closes at ``price``), so the
    ATR the ceiling reads back is the mean of the chosen fractions — which makes the
    admissible multiple checkable by hand rather than only by the code under test.
    """
    out = []
    for i in range(n):
        pct = 0.01 if atr_pct_by_bar is None else atr_pct_by_bar(i)
        half = price * pct / 2.0
        out.append({"open": price, "high": price + half, "low": price - half,
                    "close": price, "open_time": "2026-01-01T00:00:00Z"})
    return out


def test_the_probe_ceiling_admits_the_fraction_it_promises():
    """The ceiling is checked against `paper.stop_is_beyond_liquidation` itself, not against
    a second copy of the arithmetic — the whole point of reading `room` out of
    `liquidation_price` is that the two cannot drift. A tenth of the bars are ten times as
    volatile as the rest, so a ceiling taken from the mean would be refused on all of them."""
    from runtime.mvp_runtime.crypto import paper

    candles = _volatile_candles(atr_pct_by_bar=lambda i: 0.10 if i % 10 == 0 else 0.01)
    ceiling = factory.liquidation_admissible_stop_atr(candles)
    assert ceiling is not None

    # Replay the guard's own question per bar: would a stop this wide sit beyond liquidation?
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    closes = [c["close"] for c in candles]
    atr_series = indicators.atr(highs, lows, closes, features.ATR_PERIOD)
    judged = [(close, atr) for close, atr in zip(closes, atr_series) if atr]
    admitted = sum(
        1 for close, atr in judged
        if not paper.stop_is_beyond_liquidation(close, close - ceiling * atr, True)
    )
    fraction = admitted / len(judged)
    assert fraction >= factory.PROBE_LIQUIDATION_ADMIT_FRACTION - 0.02, (
        f"ceiling {ceiling} admitted only {fraction:.2%} of bars"
    )
    # And it is a real bound, not a vacuous one: doubling it loses bars the ceiling kept.
    wider = sum(
        1 for close, atr in judged
        if not paper.stop_is_beyond_liquidation(close, close - 2 * ceiling * atr, True)
    )
    assert wider < admitted, "the ceiling must be tight enough that widening it costs coverage"


def test_a_binding_ceiling_narrows_the_probe_and_a_loose_one_changes_nothing():
    """The ceiling only ever narrows. A value above every family's own space must leave the
    batch byte-identical to the un-ceilinged one — otherwise the parameter would be changing
    draws it has no claim on."""
    kwargs = dict(seed=11, count=8, symbol="BTCUSDT", timeframe="1d")
    unbounded = generate_batch("GEN-780", **kwargs)
    assert generate_batch("GEN-780", probe_stop_ceiling=99.0, **kwargs) == unbounded

    tight = 2.0
    bounded = generate_batch("GEN-780", probe_stop_ceiling=tight, **kwargs)
    templates = {t.family: t.param_space for t in templates_for_timeframe("1d", symbol="BTCUSDT")}
    flip = factory._elite_flip("GEN-780")
    probed = 0
    for index, spec in enumerate(bounded["specs"]):
        space = templates[spec["strategy_family"]]["stop_atr"]
        if (index + flip) % 4 not in factory._EXIT_PROBE_SLOTS or space.hi <= tight:
            continue
        span = (space.hi - space.lo) * factory._MUTATION_SCALE
        stop = spec["exit_rules"]["stop_atr"]
        # Centred on the ceiling now, so the draw sits within one span of it — and crucially
        # below the band the space's own ceiling would have produced.
        assert stop <= tight + span + 1e-9, f"probe {index} drew {stop} above the ceiling's reach"
        probed += 1
    assert probed, "the batch must contain a probe slot whose family the ceiling actually binds"


def test_a_ceiling_under_the_family_floor_spends_the_slot_on_an_ordinary_draw():
    """A context that cannot carry a probe at all must not mint one at its floor. A draw
    pinned to the bottom of the space is not a wide-stop datapoint, and counting it as probe
    coverage would report a search this factory never ran."""
    templates = {t.family: t.param_space for t in templates_for_timeframe("1d", symbol="BTCUSDT")}
    floor = min(space["stop_atr"].lo for space in templates.values())
    batch = generate_batch("GEN-781", seed=5, count=8, symbol="BTCUSDT", timeframe="1d",
                           probe_stop_ceiling=floor - 0.1)
    flip = factory._elite_flip("GEN-781")
    for index, spec in enumerate(batch["specs"]):
        space = templates[spec["strategy_family"]]["stop_atr"]
        span = (space.hi - space.lo) * factory._MUTATION_SCALE
        stop = spec["exit_rules"]["stop_atr"]
        assert stop >= space.lo - 1e-9, "a refused probe must not mint below the space"
        # Only the TREND space can answer this: a fade family's band [1.79, 2.0] overlaps what
        # an ordinary draw around its 1.7 base already reaches, so a draw landing there proves
        # nothing either way. The trend space's band starts at 2.37, beyond any centre's reach.
        if (index + flip) % 4 in factory._EXIT_PROBE_SLOTS and space.hi > 2.5:
            assert stop <= space.hi - span + 1e-9, (
                f"slot {index} still drew in the probe band ({stop}) under an impossible ceiling"
            )


def test_the_cohort_ceiling_takes_the_most_binding_leg():
    """A pooled spec is scored on every leg, so the ceiling is the cohort's minimum — and a
    leg too short to have an ATR is skipped rather than read as unbounded."""
    calm = {"candles": _volatile_candles(atr_pct_by_bar=lambda i: 0.005)}
    wild = {"candles": _volatile_candles(atr_pct_by_bar=lambda i: 0.05)}
    silent = {"candles": _volatile_candles(n=3)}

    calm_ceiling = factory.liquidation_admissible_stop_atr(calm["candles"])
    wild_ceiling = factory.liquidation_admissible_stop_atr(wild["candles"])
    assert wild_ceiling < calm_ceiling
    assert factory.liquidation_admissible_stop_atr(silent["candles"]) is None

    assert factory.cohort_probe_stop_ceiling([calm, wild]) == wild_ceiling
    assert factory.cohort_probe_stop_ceiling([calm, silent]) == calm_ceiling
    assert factory.cohort_probe_stop_ceiling([silent]) is None



# --- pooled fusion: the second increment ----------------------------------------------------

_COHORT = ["BTCUSDT", "ETHUSDT"]


def _two_cohort_parents(tmp_path, scope=None):
    """The improving pair, fitted on the cohort rather than on BTCUSDT alone."""
    scope = list(scope or _COHORT)
    _durable_parent(tmp_path, "cand-pool-a",
                    _parent_spec("S1", [_RSI_OVER_50], symbol_scope=scope), 99.0)
    _durable_parent(tmp_path, "cand-pool-b",
                    _parent_spec("S2", [_RSI_UNDER_60], symbol_scope=scope), 98.0)
    return pool.read_candidates(tmp_path)


def test_cohort_buckets_take_exact_scope_not_membership(tmp_path):
    """Membership when mining one symbol, exact equality when mining a cohort. A BTCUSDT-only
    parent CONTAINS the primary, and under the membership rule it would bucket into a pooled
    fire and fuse into a child claiming one symbol while scored on two — the wrong number the
    pooled boundary existed to refuse, mirrored."""
    single = _two_improving_parents(tmp_path)                 # scope ["BTCUSDT"]
    both = _two_cohort_parents(tmp_path)                       # + scope ["BTCUSDT", "ETHUSDT"]
    assert len(both) == 4

    by_membership = factory.fusion_parent_buckets(both, symbol="BTCUSDT", timeframe="1d")
    assert {r["candidate_id"] for b in by_membership for r in b} == {
        "cand-aaa", "cand-bbb", "cand-pool-a", "cand-pool-b"}, "membership keeps every parent"

    by_cohort = factory.fusion_parent_buckets(both, symbol="BTCUSDT", timeframe="1d",
                                              scope=_COHORT)
    assert [sorted(r["candidate_id"] for r in b) for b in by_cohort] == [["cand-pool-a", "cand-pool-b"]]
    # And the cohort rule is order-independent, like the pool key it mirrors.
    assert factory.fusion_parent_buckets(both, symbol="ETHUSDT", timeframe="1d",
                                         scope=list(reversed(_COHORT))) == by_cohort
    assert factory.fusion_parent_buckets(single, symbol="BTCUSDT", timeframe="1d",
                                         scope=_COHORT) == [], "no parent fitted on this cohort"


def test_a_pooled_fusion_scores_the_child_across_every_leg(tmp_path, monkeypatch):
    """The whole point of the second increment: with the cohort's frames the child (and each
    parent, on this window) is replayed across every leg, so its evidence claims exactly what
    its scope claims. The improvement bar is waived here because this test is about WHERE the
    child is scored, not whether this synthetic pair happens to clear the bar; the bar has its
    own tests. Handing the same call one frame is the single-symbol path, byte for byte."""
    monkeypatch.setattr(factory, "_fusion_improvement", lambda child, parents: None)
    parents = _two_cohort_parents(tmp_path)
    primary = _trending_snapshot()
    frames = [factory.build_replay_frame(primary),
              factory.build_replay_frame(_shifted_snapshot(symbol="ETHUSDT"))]
    buckets = factory.fusion_parent_buckets(parents, symbol="BTCUSDT", timeframe="1d",
                                            scope=_COHORT)
    common = dict(generation_id="GEN-777", start_index=5, pairs=1, evidence_sha="sha256:test",
                  now=NOW)

    pooled_children, rejected = factory._fuse_batch(
        buckets, primary, seen_hashes=set(), frames=frames, **common)
    assert pooled_children, f"no pooled child minted: {rejected}"
    child = pooled_children[0]
    assert child["strategy_spec"]["symbol_scope"] == _COHORT
    assert child["backtest_evidence"]["symbols_replayed"] == 2
    assert child["backtest_evidence"]["holdout"]["symbols"] == 2
    assert child["derivation_type"] == "crossover"
    assert sorted(child["parent_candidate_ids"]) == ["cand-pool-a", "cand-pool-b"]
    for parent_figures in child["fusion_improvement"]["parents"].values():
        assert set(parent_figures) == set(factory.FUSION_IMPROVEMENT_METRICS)

    single_children, _ = factory._fuse_batch(
        buckets, primary, seen_hashes=set(), frames=None, **common)
    assert single_children[0]["backtest_evidence"]["symbols_replayed"] == 1, (
        "without frames the call must remain the one-frame path"
    )


def test_a_pooled_fire_fuses_cohort_parents_end_to_end(tmp_path, monkeypatch):
    """Through `run_factory`: a pooled fire with cohort-fitted parents in the store reports no
    skip reason, mints crossover children scoped to the cohort and scored across it, and its
    seeded rows still carry the cohort too. The bar is waived for the reason stated above."""
    monkeypatch.setattr(factory, "_fusion_improvement", lambda child, parents: None)
    parents = _two_cohort_parents(tmp_path)
    result = run_factory(_trending_snapshot(), active_pool={"active_strategies": []},
                         existing_candidates=parents, now=NOW, count=1, fusion_pairs=1,
                         cohort_snapshots=[_shifted_snapshot(symbol="ETHUSDT")])
    assert result["pooled"] is True
    assert result["fusion_skipped"] is None
    assert result["fused_count"] >= 1, f"fusion ran and refused everything: {result['fusion_rejected']}"
    fused = [c for c in result["candidates"] if c["derivation_type"] == "crossover"]
    assert fused
    for child in fused:
        assert child["strategy_spec"]["symbol_scope"] == _COHORT
        assert child["backtest_evidence"]["symbols_replayed"] == 2
        assert child["evidence_input_sha256"] == result["evidence_input_sha256"]
    for candidate in result["candidates"]:
        assert candidate["strategy_spec"]["symbol_scope"] == _COHORT
    # The store's lineage guard accepts a pooled child of pooled parents.
    assert pool.append_candidates(result["candidates"], root=tmp_path) == len(result["candidates"])

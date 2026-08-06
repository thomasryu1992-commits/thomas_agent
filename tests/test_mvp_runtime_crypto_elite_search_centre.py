"""The search sampled the same neighbourhood for a hundred generations.

`mutate_params` draws `base ± (hi − lo) × 0.35` around `template.base_params`, and that base is
a CONSTANT. Nothing a generation learned changed where the next one looked: repeated sampling,
not evolution.

Moving the centre has a hazard that has to be named or it is a worse bug than the one it closes.
Hill-climbing on a noisy fitness surface converges on the noise — which is the failure this
store already exhibits, expectancy falling toward the cost of trading as sample size grows. So
the centre follows ROBUSTNESS rather than expectancy (centring on the highest expectancy is
precisely the mechanism that produced a store of maxima), and half the draws stay on the
template's own base so the search cannot collapse onto one point.

These pin the parts that are easy to get subtly wrong: which candidate wins, when the centre
refuses to move at all, and that the split is deterministic rather than random.

And the part that WAS wrong, for the whole life of the split: "half the draws" was read off the
accept index, which is the same counter the template is picked with, so each family's centre was
fixed rather than alternating and no family ever saw both. The lock is arithmetic — `offset` is a
multiple of `count`, `total` is even because families are minted in long/short pairs — so the
tests below sweep contexts of three different sizes rather than one.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.crypto.factory import (
    ELITE_EVIDENCE_MIN_TRADES,
    elite_base_params,
    holdout_permits_centring,
    holdout_permits_parenting,
)
from runtime.mvp_runtime.crypto.robustness import MIN_HOLDOUT_TRADES

FALLBACK = {"adx_min": 22.0, "rsi_max": 55.0}


def _candidate(*, family="trend_pullback", symbol="BTCUSDT", timeframe="1h",
               params=None, closed=100, score=0.7):
    return {
        "champion_score": score,
        "mint_params": params if params is not None else {"adx_min": 30.0, "rsi_max": 40.0},
        "backtest_evidence": {"closed_count": closed},
        "strategy_spec": {"strategy_family": family, "timeframe": timeframe,
                          "symbol_scope": [symbol]},
    }


def _centre(candidates, **over):
    kw = {"family": "trend_pullback", "symbol": "BTCUSDT", "timeframe": "1h",
          "fallback": FALLBACK}
    kw.update(over)
    return elite_base_params(candidates, **kw)


# --- when the centre moves ------------------------------------------------------

def test_the_most_robust_candidate_sets_the_centre():
    weak = _candidate(score=0.4, params={"adx_min": 18.0, "rsi_max": 60.0})
    strong = _candidate(score=0.9, params={"adx_min": 28.0, "rsi_max": 45.0})
    assert _centre([weak, strong]) == {"adx_min": 28.0, "rsi_max": 45.0}


def test_expectancy_does_not_decide_it():
    """Centring on the highest expectancy is the mechanism that produced a store whose
    expectancy converges on the cost of trading. `champion_score` is the anti-overfit score."""
    lucky = _candidate(score=0.3, params={"adx_min": 15.0, "rsi_max": 65.0})
    lucky["backtest_evidence"]["expectancy"] = 9.99
    solid = _candidate(score=0.8, params={"adx_min": 25.0, "rsi_max": 50.0})
    assert _centre([lucky, solid])["adx_min"] == 25.0


def test_a_tie_keeps_the_earlier_record():
    """`>` not `>=`, so the centre does not drift with store order among equals."""
    first = _candidate(score=0.7, params={"adx_min": 20.0, "rsi_max": 50.0})
    second = _candidate(score=0.7, params={"adx_min": 29.0, "rsi_max": 41.0})
    assert _centre([first, second])["adx_min"] == 20.0


# --- when it refuses to ---------------------------------------------------------

def test_nothing_to_learn_from_keeps_the_template_base():
    assert _centre([]) == FALLBACK


def test_a_thin_record_does_not_move_the_centre():
    """A centre moved on three trades is not a lesson."""
    thin = _candidate(closed=ELITE_EVIDENCE_MIN_TRADES - 1, params={"adx_min": 30.0})
    assert _centre([thin]) == FALLBACK


def test_a_candidate_without_recorded_params_is_skipped():
    """Every candidate in the store today. The first generation after this still draws around
    the template base; the one after it has something to move toward."""
    legacy = _candidate()
    del legacy["mint_params"]
    assert _centre([legacy]) == FALLBACK


@pytest.mark.parametrize("over", [
    {"family": "breakout"}, {"timeframe": "4h"}, {"symbol": "ETHUSDT"},
])
def test_another_context_teaches_this_one_nothing(over):
    """A parameter that worked on BTC 1h is not evidence about ETH 4h, and the review's own
    finding is that cost as a share of R differs twelvefold across timeframes."""
    assert _centre([_candidate()], **over) == FALLBACK


def test_a_score_that_is_not_a_number_is_skipped():
    broken = _candidate()
    broken["champion_score"] = None
    assert _centre([broken]) == FALLBACK


# --- the shape of what comes back ----------------------------------------------

def test_only_keys_the_template_still_declares_come_through():
    """A param the family dropped must not be resurrected from an old record."""
    stale = _candidate(params={"adx_min": 30.0, "rsi_max": 40.0, "removed_param": 1.0})
    assert set(_centre([stale])) == set(FALLBACK)


def test_a_param_the_family_gained_comes_from_the_template():
    older = _candidate(params={"adx_min": 30.0})
    assert _centre([older]) == {"adx_min": 30.0, "rsi_max": FALLBACK["rsi_max"]}


def test_the_fallback_is_never_mutated():
    before = dict(FALLBACK)
    _centre([_candidate()])
    _centre([])
    assert FALLBACK == before


# --- and never toward a region the tail already refuted -------------------------

def _with_holdout(candidate, *, closed, expectancy):
    candidate["backtest_evidence"]["holdout"] = {"closed_count": closed, "expectancy": expectancy}
    return candidate


def test_a_refuted_region_does_not_set_the_centre():
    """`champion_score` is computed on the SCORED window, so it is blind to the tail — and the
    maximum over a growing store landed on a row the tail had refuted in 227 of 461 contexts."""
    refuted = _with_holdout(
        _candidate(score=0.9, params={"adx_min": 28.0, "rsi_max": 45.0}),
        closed=MIN_HOLDOUT_TRADES, expectancy=-0.25)
    survived = _with_holdout(
        _candidate(score=0.4, params={"adx_min": 18.0, "rsi_max": 60.0}),
        closed=MIN_HOLDOUT_TRADES, expectancy=0.05)
    assert _centre([refuted, survived]) == {"adx_min": 18.0, "rsi_max": 60.0}


def test_a_refuted_best_falls_back_rather_than_being_used():
    """Nothing left to learn from is the template base, not the least-bad refuted row."""
    refuted = _with_holdout(_candidate(score=0.9, params={"adx_min": 28.0}),
                            closed=MIN_HOLDOUT_TRADES, expectancy=-0.01)
    assert _centre([refuted]) == FALLBACK


def test_an_unjudged_tail_still_sets_the_centre():
    """Fail OPEN, unlike `holdout_permits_parenting`. A child cites its parents' evidence; a
    centre cites nothing — it says where to look, and what is minted there earns its own. Being
    strict here costs the mechanism: on this store 273 contexts keep a centre against 67."""
    thin = _with_holdout(_candidate(score=0.9, params={"adx_min": 28.0, "rsi_max": 45.0}),
                         closed=MIN_HOLDOUT_TRADES - 1, expectancy=-9.0)
    assert _centre([thin]) == {"adx_min": 28.0, "rsi_max": 45.0}
    absent = _candidate(score=0.9, params={"adx_min": 26.0, "rsi_max": 44.0})
    assert _centre([absent]) == {"adx_min": 26.0, "rsi_max": 44.0}


def test_the_two_holdout_doors_disagree_on_absence_by_design():
    """Pinned as a pair, because the next reader's instinct is to collapse them into one."""
    unjudged = _candidate()
    assert holdout_permits_centring(unjudged)
    assert not holdout_permits_parenting(unjudged)
    refuted = _with_holdout(_candidate(), closed=MIN_HOLDOUT_TRADES, expectancy=-0.2)
    assert not holdout_permits_centring(refuted)
    assert not holdout_permits_parenting(refuted)


# --- one store pass, same answer ------------------------------------------------

def test_one_pass_gives_every_family_the_per_family_answer():
    """`elite_centres` is the whole rotation's centres from one read of the store.

    It replaced 38 calls to `elite_base_params` per fire, each re-reading a 1,140-row store. The
    property that matters is that it did not become a SECOND definition of which candidate may
    move a centre — so it is pinned against the per-family form rather than against a fixture.
    """
    from runtime.mvp_runtime.crypto.factory import elite_centres, templates_for_timeframe

    store = [
        _candidate(family="trend_pullback", params={"adx_min": 28.0, "rsi_max": 45.0}, score=0.9),
        _candidate(family="trend_pullback", params={"adx_min": 15.0, "rsi_max": 65.0}, score=0.2),
        _candidate(family="breakout", params={"adx_min": 31.0}, score=0.8),
        _candidate(family="breakout", timeframe="4h", params={"adx_min": 19.0}, score=0.99),
        _candidate(family="mean_reversion", symbol="ETHUSDT", params={"rsi_max": 21.0}, score=0.95),
    ]
    templates = templates_for_timeframe("1h", symbol="BTCUSDT")
    batched = elite_centres(store, templates, symbol="BTCUSDT", timeframe="1h")

    assert set(batched) == {t.family for t in templates}
    for template in templates:
        assert batched[template.family] == elite_base_params(
            store, family=template.family, symbol="BTCUSDT", timeframe="1h",
            fallback=template.base_params,
        )


def test_a_malformed_stored_row_does_not_kill_the_fire():
    """A backtest fire must not die on one bad row — `.get` on a non-Mapping spec raises."""
    broken = {"champion_score": 0.9, "mint_params": {"adx_min": 30.0},
              "backtest_evidence": {"closed_count": 100}, "strategy_spec": ["not", "a", "mapping"]}
    assert _centre([broken, _candidate()]) == {"adx_min": 30.0, "rsi_max": 40.0}


# --- and half the draws stay home ----------------------------------------------

FIRES = 120


def _centre_log(monkeypatch, templates):
    """Record ``(family, which centre)`` for every draw ``generate_batch`` makes.

    Object identity is the discriminator and it is exact: the base branch passes
    ``template.base_params`` itself, the elite branch builds a fresh dict, so nothing has to be
    inferred from a drawn value.

    **The templates are captured from `templates_for_timeframe` itself rather than from the
    caller's own copy.** That used to be one and the same: the retiming was
    ``replace(t, timeframe=...)``, which carries the inner dicts through by reference, so every
    call handed back the same ``param_space`` object and a map built once matched forever. It
    stopped being true when `judgeable_holding_bars` began narrowing the hold space at 1d — a
    narrowed family gets a fresh ``param_space`` and ``base_params`` per call, so the id from
    the caller's copy was absent from the map and this raised `KeyError` on the 1d case alone.
    Keying off the objects the batch is actually holding restores the exactness rather than
    trading it for a content comparison, and it no longer depends on that sharing at all.
    """
    from runtime.mvp_runtime.crypto import factory

    by_space: dict[int, object] = {}
    real_templates = factory.templates_for_timeframe

    def capturing(*args, **kwargs):
        retimed = real_templates(*args, **kwargs)
        by_space.update({id(t.param_space): t for t in retimed})
        return retimed

    monkeypatch.setattr(factory, "templates_for_timeframe", capturing)
    by_space.update({id(t.param_space): t for t in templates})

    real = factory.mutate_params
    log: list[tuple[str, str]] = []

    def recording(base_params, param_space, rng, **kw):
        template = by_space[id(param_space)]
        log.append((template.family,
                    "base" if base_params is template.base_params else "elite"))
        return real(base_params, param_space, rng, **kw)

    monkeypatch.setattr(factory, "mutate_params", recording)
    return log


def _fire(elite, *, symbol="ETHUSDT", timeframe="1h", fires=FIRES):
    from runtime.mvp_runtime.crypto.factory import generate_batch

    for fire in range(fires):
        generate_batch(f"GEN-{fire:03d}", seed=fire, count=4, symbol=symbol,
                       timeframe=timeframe, elite_params=elite, rotation_index=fire)


@pytest.mark.parametrize("symbol,timeframe", [
    ("ETHUSDT", "1h"),   # total 36 — gcd(4, 36) == 4, the tightest walk
    ("BTCUSDT", "1h"),   # total 34 — no rel_strength_* on the market proxy
    ("BTCUSDT", "1d"),   # total 24 — where stepping the split on rotation_index would re-lock
])
def test_no_family_is_locked_to_one_centre(monkeypatch, symbol, timeframe):
    """The defect this split had for its whole life, and the reason it is a per-fire hash.

    `generate_batch` picks its template with `templates[(offset + len(accepted)) % total]` and
    used the SAME counter to choose the centre. `offset` is always a multiple of `count` and
    `total` is always even (every family is a long/short pair), so a template's index parity
    equalled its accept parity and each family's centre was fixed forever — measured over 60
    fires, BOTH = 0 in every context. The elite half then had no anchor and the base half could
    not learn, nor reach the outer third of its own parameter space.
    """
    from runtime.mvp_runtime.crypto.factory import templates_for_timeframe

    templates = templates_for_timeframe(timeframe, symbol=symbol)
    log = _centre_log(monkeypatch, templates)
    _fire({t.family: dict(t.base_params) for t in templates},
          symbol=symbol, timeframe=timeframe)

    seen: dict[str, set[str]] = {}
    for family, which in log:
        seen.setdefault(family, set()).add(which)

    assert set(seen) == {t.family for t in templates}, "the rotation did not reach every family"
    locked = sorted(f for f, centres in seen.items() if len(centres) == 1)
    assert not locked, f"{len(locked)} families never saw both centres: {locked}"


def test_the_split_still_halves_every_batch(monkeypatch):
    """Breaking the lock must not cost the property the lock was a broken form of."""
    from runtime.mvp_runtime.crypto.factory import templates_for_timeframe

    templates = templates_for_timeframe("1h", symbol="ETHUSDT")
    log = _centre_log(monkeypatch, templates)
    _fire({t.family: dict(t.base_params) for t in templates}, fires=40)

    elite = sum(1 for _, which in log if which == "elite")
    assert elite * 2 == len(log), f"{elite} elite draws of {len(log)}"


def test_the_split_is_derived_not_drawn():
    """Deterministic, so a replay reproduces the same batch — and taken from the generation id,
    which rides on every candidate, so a stored fire's split can be reconstructed from it."""
    from runtime.mvp_runtime.crypto.factory import _elite_flip, generate_batch

    kwargs = {"seed": 7, "count": 4, "symbol": "ETHUSDT", "timeframe": "1h"}
    assert generate_batch("GEN-042", **kwargs) == generate_batch("GEN-042", **kwargs)
    assert _elite_flip("GEN-042") == _elite_flip("GEN-042")
    assert {_elite_flip(f"GEN-{n:03d}") for n in range(40)} == {0, 1}

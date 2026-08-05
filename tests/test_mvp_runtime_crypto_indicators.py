"""C3 indicator/feature parity against the SOURCE implementation.

``tests/fixtures/crypto_indicator_parity.json`` holds outputs computed by the source
system's pandas implementation (crypto_AI_System ``features/indicators.py`` +
``strategy_hash.py``) over the shared 200-candle fixture. These tests assert the pure-
Python port reproduces them value-for-value: None exactly where pandas had NaN,
floats to 1e-9 relative tolerance. This is the C3 gate condition — replay parity, not
a reimplementation that merely looks similar."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from runtime.mvp_runtime.crypto import features, indicators
from runtime.mvp_runtime.crypto.strategy import StrategySpec

FIXTURES = Path(__file__).parent / "fixtures"
CANDLES = json.loads((FIXTURES / "crypto_parity_candles.json").read_text(encoding="utf-8"))
PARITY = json.loads((FIXTURES / "crypto_indicator_parity.json").read_text(encoding="utf-8"))
EXPECTED = PARITY["features"]

# C9: the fixture carries synthetic feed series (with deliberate gap bands); their
# presence engages the series semantics, so the feed columns are parity-checked too.
SNAPSHOT = {"candles": CANDLES, **PARITY.get("feeds", {})}
ROWS = features.build_feature_rows(SNAPSHOT)


def _assert_series_equal(name: str, actual: list, expected: list) -> None:
    assert len(actual) == len(expected), f"{name}: length {len(actual)} != {len(expected)}"
    for i, (a, e) in enumerate(zip(actual, expected)):
        if e is None:
            assert a is None, f"{name}[{i}]: expected None (pandas NaN), got {a}"
        elif isinstance(e, str):  # categorical parity (market_regime labels)
            assert a == e, f"{name}[{i}]: {a!r} != {e!r}"
        else:
            assert a is not None, f"{name}[{i}]: expected {e}, got None"
            assert math.isclose(a, e, rel_tol=1e-9, abs_tol=1e-12), f"{name}[{i}]: {a} != {e}"


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_feature_parity_with_source_implementation(name):
    actual = [row.get(name) for row in ROWS]
    _assert_series_equal(name, actual, EXPECTED[name])


def test_direct_indicator_calls_match_feature_rows():
    closes = [c["close"] for c in CANDLES]
    _assert_series_equal("sma", indicators.sma(closes, 20), EXPECTED["ma20"])
    _assert_series_equal("ema", indicators.ema(closes, 50), EXPECTED["ema50"])
    _assert_series_equal("roc", indicators.roc(closes, 4), EXPECTED["roc_4"])


def test_strategy_rule_hash_parity_with_source_implementation():
    # The hash must be bit-identical to the source's: the C7 import keeps original
    # hashes, and from_dict verifies a provided hash instead of re-minting it.
    for spec_id, raw in PARITY["specs"].items():
        parsed = StrategySpec.from_dict(raw)
        assert parsed.strategy_rule_hash == PARITY["strategy_rule_hashes"][spec_id]


def test_warmup_rows_marked_and_final_rows_ok():
    statuses = [row["data_quality_status"] for row in ROWS]
    assert statuses[0] == "WARMUP"  # nothing is warm on the first candle
    assert statuses[-1] == "OK"  # 200 candles fully warm every ported feature
    # WARMUP must be a prefix: once warm, a clean series never regresses.
    first_ok = statuses.index("OK")
    assert all(s == "OK" for s in statuses[first_ok:])


def test_an_unconfigured_feed_is_indeterminate_like_a_failed_one():
    """The last three fabricated constants, removed 2026-08-05.

    A key ABSENT from the snapshot used to keep the source's pre-C9 0-fill while a key present
    and EMPTY was already None — the reasoning being that an unconfigured feed is not a degraded
    one. True, and it does not survive what these columns are for: all three are in
    `factory.NUMERIC_FEATURES`, so a literal against a value that is always exactly zero does
    not select, it decides, and `unsuppliable_features` cannot see it because the column is
    populated rather than missing. `NoLiquidationFeed` is the DEFAULT, so this was every machine
    without a Coinalyze grant, on every row."""
    last = features.build_feature_rows({"candles": CANDLES})[-1]
    assert last["liquidation_spike_ratio"] is None
    assert last["funding_rate"] is None and last["funding_zscore"] is None


def test_absent_derivative_prices_are_indeterminate_not_the_old_constants():
    """The mark/index/basis fallbacks are gone, deliberately.

    This test previously asserted the source's no-feed behaviour — ``mark == index ==
    close`` and ``mark_index_basis_bps == 0.0`` — which was the right port while no mark or
    index series was collected. It was also a live hazard, because ``mark_index_basis_bps``
    is in ``factory.NUMERIC_FEATURES``: a literal condition on a value that is always
    exactly zero does not select, it decides, so every minted ``basis > x`` could only ever
    be false and every ``basis <= x`` only ever true.

    Now that the series are collected (`market_data.derivative_price_klines`), absence means
    the venue did not answer, and the honest reading of that is None — no basis, no entry.

    The ``liquidation_spike_ratio`` and funding 0-fills were the same class of hazard and
    survived this change only because they were "a separate decision about a separate feed".
    That decision was taken 2026-08-05 the same way and on the same condition — both feeds are
    collected now — so the test above asserts None rather than the constants, and this
    vocabulary no longer fabricates a value anywhere."""
    last = features.build_feature_rows({"candles": CANDLES})[-1]
    assert last["mark_price"] is None
    assert last["index_price"] is None
    assert last["mark_index_basis_bps"] is None
    assert last["premium_index"] is None and last["premium_index_zscore"] is None


def test_feed_present_but_empty_is_indeterminate_never_constant():
    # Fetch attempted and FAILED (key present, list empty): NaN-honest — a spec
    # referencing these fails closed to no-entry instead of evaluating a constant.
    last = features.build_feature_rows({"candles": CANDLES, "funding": [], "liquidations": []})[-1]
    assert last["funding_rate"] is None and last["funding_zscore"] is None
    assert last["liquidation_spike_ratio"] is None


def test_latest_feature_row_empty_snapshot_is_empty():
    assert features.latest_feature_row({"candles": []}) == {}

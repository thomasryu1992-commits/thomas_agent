"""Cross-sectional context: where a symbol RANKS in its declared cohort.

The last item on ``docs/TRADING_ALPHA_RESEARCH_RECORD.md``, and the first feature group whose
value depends on symbols the cycle is not trading. ``rel_strength_roc_4`` already measured
against one proxy; a rank is a different statement, and the one a cross-sectional strategy
needs — every altcoin can beat BTC in the same hour, which says nothing about which of them to
buy and nothing at all about which to sell.

What these tests are guarding, in rough order of how quietly each would fail:

- **the cohort floor.** ``xs_rank_pct`` has only ``members - 1`` distinct values, so a
  two-member cohort reports a coin flip under the same column name a six-member cohort uses.
  ``MIN_CROSS_SECTION_MEMBERS`` is the line, and nothing downstream could detect its absence.
- **partial degradation.** Five peers of six is a usable cohort and must stay usable; this leg
  deliberately does NOT share ``attach_reference``'s all-or-nothing posture, so both halves of
  that difference need pinning.
- **the exact open-time join.** Same rule as the reference and derivative legs, and picking
  wrongly is silent: an as-of join would rank this symbol against a peer's stale bar and every
  column would still look populated.
- **long/short disjointness.** ``xs_reversion_long`` and ``xs_reversion_short`` share one
  ``xs_rank_edge`` parameter precisely so the two can never match on the same row, which would
  be ``paper.BLOCK_DIRECTION_CONFLICT`` on exactly the bars the families exist for. That is a
  property of the param SPACE, so a test on one mined value would not establish it.
- **the fan-out cost.** Six members × four timeframes × five contexts is 120 reads for 24
  answers, which makes this the largest redundancy surface in the runtime if the cache breaks.
"""

from __future__ import annotations

from runtime.mvp_runtime.crypto import factory, features
from runtime.mvp_runtime.crypto.cycle import attach_cross_section, attach_reference
from runtime.mvp_runtime.crypto.features import build_feature_rows
from runtime.mvp_runtime.crypto.market_data import (
    CROSS_SECTION_DEGRADED,
    CROSS_SECTION_UNIVERSE,
    MockMarketDataCollector,
    PeerCandleCache,
    collect_market_data,
)
from runtime.mvp_runtime.crypto.strategy import StrategySpec, evaluate_spec

NOW = "2026-07-30T00:00:00Z"


def _candle(hour, close, *, day="2026-07-20"):
    return {
        "open_time": f"{day}T{hour:02d}:00:00Z", "close_time": f"{day}T{hour + 1:02d}:00:00Z",
        "open": close, "high": close + 1.0, "low": close - 1.0, "close": close, "volume": 10.0,
    }


def _ramp(count, *, start=100.0, step):
    """A monotone close series, so ``roc_4`` is a known constant sign at every warm bar."""
    return [_candle(h, start + step * h) for h in range(count)]


def _snapshot(own_step, peer_steps, *, bars=12, symbol="ETHUSDT"):
    """A frame whose peers are ramps of chosen slopes — so the rank is arithmetic, not luck."""
    return {
        "symbol": symbol, "timeframe": "1h", "candles": _ramp(bars, step=own_step),
        "peer_candles": {
            f"PEER{index}USDT": _ramp(bars, step=step)
            for index, step in enumerate(peer_steps)
        },
    }


# --- the rank itself ----------------------------------------------------------

def test_the_strongest_member_ranks_1_and_the_weakest_ranks_0():
    """Endpoints exactly, not approximately: the fraction is over ``members - 1``, so a spec
    mining ``>= 1.0`` is asking for the cohort leader and must be able to get it."""
    strongest = build_feature_rows(_snapshot(5.0, [1.0, 2.0, 3.0, 4.0]))[-1]
    assert strongest["xs_rank_pct"] == 1.0
    assert strongest["xs_members"] == 5.0

    weakest = build_feature_rows(_snapshot(0.5, [1.0, 2.0, 3.0, 4.0]))[-1]
    assert weakest["xs_rank_pct"] == 0.0


def test_the_middle_member_ranks_by_how_many_it_is_above():
    """Two of four below → 2/4 of the cohort's span, with five members."""
    row = build_feature_rows(_snapshot(2.5, [1.0, 2.0, 3.0, 4.0]))[-1]
    assert row["xs_rank_pct"] == 0.5


def test_ties_share_the_lower_rank_rather_than_being_broken_by_symbol_name():
    """A tiebreak on the alphabet would make a rank depend on how a peer is spelled — and it
    would be invisible, because the column stays populated and in range either way."""
    row = build_feature_rows(_snapshot(2.0, [2.0, 2.0, 3.0, 4.0]))[-1]
    # Three members share the slowest slope and two are above them. Nothing is strictly below,
    # so all three tied members read 0.0 rather than one of them being promoted to 2/4.
    assert row["xs_rank_pct"] == 0.0


def test_excess_momentum_is_measured_against_the_cohort_median_not_the_mean():
    """A mean is dragged by one outlier, which for an excess measurement is the direction that
    reports a leader as average. Median: with slopes 1/2/3/4 the cohort median is own-inclusive."""
    row = build_feature_rows(_snapshot(3.0, [1.0, 2.0, 3.0, 100.0]))[-1]
    # Cohort roc values are ordered by slope; the 100.0 peer cannot pull the median.
    assert row["xs_excess_roc_4"] is not None
    assert row["xs_excess_roc_4"] == 0.0, "own is the median of five, so its excess is zero"


def test_dispersion_ratio_is_self_calibrating_and_dispersion_is_a_level():
    """The RATIO is what a spec may mine, so it must be present and centred near 1.0 on a
    stationary window; the LEVEL is evidence only and must still be recorded."""
    collector = MockMarketDataCollector()
    snapshot, _ = collect_market_data("ETHUSDT", "1h", collector=collector, now=NOW, limit=400)
    assert attach_cross_section(snapshot, collector=collector, now=NOW) is None
    rows = build_feature_rows(snapshot)
    ratios = [r["xs_dispersion_ratio"] for r in rows if r["xs_dispersion_ratio"] is not None]
    levels = [r["xs_dispersion"] for r in rows if r["xs_dispersion"] is not None]
    assert ratios and levels
    ordered = sorted(ratios)
    median = ordered[len(ordered) // 2]
    assert 0.5 < median < 2.0, "a ratio to its own rolling median cannot sit far from 1.0"
    assert all(level >= 0.0 for level in levels), "a standard deviation is never negative"


# --- absence is indeterminate, never a middling constant -----------------------

def test_no_peers_leaves_every_cross_sectional_column_none():
    """The `open_interest` posture, not the `liquidation_spike_ratio` one. A rank of 0.5 for
    "we could not rank" is the single most dangerous value this column could take: it is
    plausible, in range, and matched by roughly half of all mineable conditions."""
    rows = build_feature_rows({"symbol": "ETHUSDT", "timeframe": "1h", "candles": _ramp(12, step=1.0)})
    for column in features.XS_COLUMNS:
        assert rows[-1][column] is None, column


def test_a_cohort_below_the_floor_reports_members_but_refuses_to_rank():
    """Two peers is a three-member cohort: ranks would be {0.0, 0.5, 1.0}, i.e. a mined
    threshold is a choice among three buckets. The count is still recorded, because "the
    cohort was too thin" and "no peers arrived at all" are different facts."""
    row = build_feature_rows(_snapshot(2.0, [1.0, 3.0]))[-1]
    assert features.MIN_CROSS_SECTION_MEMBERS == 4
    assert row["xs_rank_pct"] is None
    assert row["xs_excess_roc_4"] is None
    assert row["xs_dispersion"] is None
    assert row["xs_members"] == 3.0, "the thin cohort is reported, not hidden"


def test_the_floor_admits_exactly_min_members():
    """Off-by-one at the boundary would silently cost a whole cohort size."""
    row = build_feature_rows(_snapshot(2.0, [1.0, 3.0, 4.0]))[-1]
    assert row["xs_members"] == 4.0
    assert row["xs_rank_pct"] is not None


def test_an_unknown_own_momentum_is_not_ranked_at_the_middle():
    """Warmup bars have no ``roc_4``. The cohort's spread is still computable there, and
    reporting it with a rank would be a rank of nothing."""
    rows = build_feature_rows(_snapshot(2.0, [1.0, 3.0, 4.0, 5.0]))
    assert rows[0]["xs_rank_pct"] is None
    assert rows[0]["xs_members"] is None


# --- alignment: the exact open-time join, not as-of ---------------------------

def test_a_peer_bar_the_venue_skipped_drops_that_peer_rather_than_carrying_it_forward():
    """The third alignment rule in this module, and choosing wrongly is silent: an as-of join
    would rank this symbol against a peer's stale bar and the column would still be populated.

    ``PEERGAP`` is missing the final bar, so at that bar the cohort is one smaller — and with
    the floor at four, a four-member cohort becomes three and refuses to rank at all. That is
    the observable consequence of the join being exact."""
    snapshot = _snapshot(2.0, [1.0, 3.0, 4.0], bars=12)
    gapped = snapshot["peer_candles"]["PEER0USDT"]
    snapshot["peer_candles"]["PEER0USDT"] = gapped[:-1]
    rows = build_feature_rows(snapshot)
    assert rows[-1]["xs_members"] == 3.0, "the missing bar dropped the peer, not its last value"
    assert rows[-1]["xs_rank_pct"] is None
    assert rows[-2]["xs_members"] == 4.0, "the bar before it still had the full cohort"


def test_a_peer_on_a_different_grid_contributes_nothing():
    """Peers are requested AT this frame's interval. One that is not on the grid is not a
    coarser series to be as-of joined — it is a mismatch, and joining it would be inventing
    an alignment rule nobody chose."""
    snapshot = _snapshot(2.0, [1.0, 3.0, 4.0], bars=12)
    snapshot["peer_candles"]["PEER0USDT"] = [
        {**candle, "open_time": candle["open_time"].replace(":00:00Z", ":30:00Z")}
        for candle in snapshot["peer_candles"]["PEER0USDT"]
    ]
    rows = build_feature_rows(snapshot)
    assert rows[-1]["xs_members"] == 3.0


# --- the fetch leg ------------------------------------------------------------

def test_attach_fetches_every_universe_member_except_the_traded_symbol():
    """The traded symbol needs no fetch: its momentum is already in the row, and it is the
    thing being ranked. Fetching it would spend a request to re-derive a column we have."""
    collector = MockMarketDataCollector()
    snapshot, _ = collect_market_data("BTCUSDT", "1h", collector=collector, now=NOW, limit=50)
    assert attach_cross_section(snapshot, collector=collector, now=NOW) is None
    assert "BTCUSDT" not in snapshot["peer_candles"]
    assert set(snapshot["peer_candles"]) == set(CROSS_SECTION_UNIVERSE) - {"BTCUSDT"}


def test_a_symbol_outside_the_universe_is_still_ranked_against_it():
    """Unlike the reference leg there is no self-comparison degeneracy to guard against: a
    symbol's rank among its peers is well defined whether or not it is a declared member."""
    collector = MockMarketDataCollector()
    snapshot, _ = collect_market_data("LTCUSDT", "1h", collector=collector, now=NOW, limit=200)
    assert attach_cross_section(snapshot, collector=collector, now=NOW) is None
    assert set(snapshot["peer_candles"]) == set(CROSS_SECTION_UNIVERSE)
    assert build_feature_rows(snapshot)[-1]["xs_members"] == float(len(CROSS_SECTION_UNIVERSE) + 1)


def test_one_failing_peer_thins_the_cohort_and_reports_it_rather_than_failing_the_leg():
    """**The deliberate difference from ``attach_reference``.** There, one series either
    arrived or the leg was indeterminate. Here, refusing to rank because the sixth peer timed
    out would throw away a measurement the runtime has — so the cohort thins, the columns stay
    live, and the reason code says the cohort was not whole."""
    from runtime.mvp_runtime.errors import ToolError

    refused = CROSS_SECTION_UNIVERSE[-1]

    class _PartialCollector(MockMarketDataCollector):
        def collect(self, symbol, timeframe, *, limit, timeout_seconds):
            if symbol == refused:
                raise ToolError("TOOL_TRANSPORT", "boom")
            return super().collect(symbol, timeframe, limit=limit, timeout_seconds=timeout_seconds)

    collector = _PartialCollector()
    snapshot, _ = collect_market_data("ETHUSDT", "1h", collector=collector, now=NOW, limit=200)
    assert attach_cross_section(snapshot, collector=collector, now=NOW) == CROSS_SECTION_DEGRADED
    assert refused not in snapshot["peer_candles"]
    row = build_feature_rows(snapshot)[-1]
    assert row["xs_rank_pct"] is not None, "a thinned cohort is still a cohort"
    assert row["xs_members"] == float(len(CROSS_SECTION_UNIVERSE) - 1)


def test_every_peer_failing_leaves_the_key_absent_and_never_raises():
    """The ``attach_htf`` contract: context that cannot be read must not take down a cycle
    that would have traded without it."""
    from runtime.mvp_runtime.errors import ToolError

    class _BrokenCollector(MockMarketDataCollector):
        def collect(self, symbol, timeframe, *, limit, timeout_seconds):
            if symbol != "ETHUSDT":
                raise ToolError("TOOL_TRANSPORT", "boom")
            return super().collect(symbol, timeframe, limit=limit, timeout_seconds=timeout_seconds)

    collector = _BrokenCollector()
    snapshot, _ = collect_market_data("ETHUSDT", "1h", collector=collector, now=NOW, limit=200)
    assert attach_cross_section(snapshot, collector=collector, now=NOW) == CROSS_SECTION_DEGRADED
    assert "peer_candles" not in snapshot
    assert build_feature_rows(snapshot)[-1]["xs_rank_pct"] is None


def test_a_frame_with_no_grid_to_join_onto_is_a_precondition_not_an_error():
    """The degraded-collection path: no candles and no known interval means there is nothing
    to request peers against. Same outcome as `attach_feeds`' derivative-price guard."""
    collector = MockMarketDataCollector()
    assert attach_cross_section(
        {"symbol": "ETHUSDT", "timeframe": "", "candles": []}, collector=collector, now=NOW
    ) is None
    assert attach_cross_section(
        {"symbol": "", "timeframe": "1h", "candles": []}, collector=collector, now=NOW
    ) is None


# --- fan-out cost -------------------------------------------------------------

def test_one_cache_serves_both_context_legs_across_a_fan_out():
    """Six members × two timeframes × four contexts is 48 asks for 12 answers, and the
    reference leg's own reads are a SUBSET of those — the proxy is a cohort member. One cache
    rather than one per leg is what makes the second read free."""
    class _CountingCollector(MockMarketDataCollector):
        def __init__(self):
            self.collects = 0

        def collect(self, symbol, timeframe, *, limit, timeout_seconds):
            self.collects += 1
            return super().collect(symbol, timeframe, limit=limit, timeout_seconds=timeout_seconds)

    collector = _CountingCollector()
    cache = PeerCandleCache(collector)
    contexts = ("ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT")
    for symbol in contexts:
        for timeframe in ("1h", "4h"):
            snapshot = {"symbol": symbol, "timeframe": timeframe, "candles": [_candle(0, 100.0)]}
            assert attach_reference(
                snapshot, collector=collector, now=NOW, limit=50, cache=cache
            ) is None
            assert attach_cross_section(
                snapshot, collector=collector, now=NOW, limit=50, cache=cache
            ) is None
            assert snapshot["peer_candles"], "the cached series must still be attached"
    # Distinct reads = every universe member at each of two timeframes. The reference leg adds
    # nothing on top, because the proxy is one of those members at those same timeframes.
    assert cache.calls == len(CROSS_SECTION_UNIVERSE) * 2
    assert collector.collects == cache.calls


def test_the_cache_remembers_a_refusal_per_symbol_not_per_leg():
    """A venue that just refused ETH will refuse it again in the same second; re-asking once
    per remaining context is how a rate cap gets reached. But a refusal for ONE member must
    not poison the others — that would turn one bad peer into an empty cohort."""
    from runtime.mvp_runtime.errors import ToolError

    refused = "DOGEUSDT"

    class _PartialCollector(MockMarketDataCollector):
        def __init__(self):
            self.collects = 0

        def collect(self, symbol, timeframe, *, limit, timeout_seconds):
            self.collects += 1
            if symbol == refused:
                raise ToolError("TOOL_TRANSPORT", "boom")
            return super().collect(symbol, timeframe, limit=limit, timeout_seconds=timeout_seconds)

    collector = _PartialCollector()
    cache = PeerCandleCache(collector)
    for symbol in ("ETHUSDT", "SOLUSDT", "BNBUSDT"):
        snapshot = {"symbol": symbol, "timeframe": "1h", "candles": [_candle(0, 100.0)]}
        assert attach_cross_section(
            snapshot, collector=collector, now=NOW, limit=50, cache=cache
        ) == CROSS_SECTION_DEGRADED
        assert refused not in snapshot["peer_candles"]
        assert snapshot["peer_candles"], "one refusal must not empty the cohort"
    assert collector.collects == len(CROSS_SECTION_UNIVERSE), (
        "each member was asked exactly once across three contexts, refusals included"
    )


def test_a_frame_and_a_peer_read_of_one_series_are_one_read_either_way():
    """The third door into the same series: a caller MINING a symbol, not ranking against it.

    The factory reads five frames and ranks each against a cohort containing the other four, so
    the frame reader and the cohort reader want the same six series — and until `frame` existed
    only one of them came through here. Order-independence is the property under test: whichever
    asks first must pay, and it must not matter which, because a fire's position in a pass is
    not something the venue should be able to tell."""
    class _CountingCollector(MockMarketDataCollector):
        def __init__(self):
            self.collects = 0

        def collect(self, symbol, timeframe, *, limit, timeout_seconds):
            self.collects += 1
            return super().collect(symbol, timeframe, limit=limit, timeout_seconds=timeout_seconds)

    for frame_first in (True, False):
        collector = _CountingCollector()
        cache = PeerCandleCache(collector)
        snapshot = {"symbol": "ETHUSDT", "timeframe": "1h", "candles": [_candle(0, 100.0)]}
        legs = [
            lambda: cache.frame("SOLUSDT", "1h", limit=50, now=NOW),
            lambda: attach_cross_section(snapshot, collector=collector, now=NOW, limit=50,
                                         cache=cache),
        ]
        for leg in (legs if frame_first else list(reversed(legs))):
            leg()
        assert cache.calls == len(CROSS_SECTION_UNIVERSE) - 1, frame_first
        assert collector.collects == cache.calls, frame_first
        # SOLUSDT is a peer of ETHUSDT, so the frame read is one of the cohort's five either way.
        assert "SOLUSDT" in snapshot["peer_candles"]


def test_a_frame_is_copied_so_one_fire_cannot_write_into_another_s():
    """A snapshot is the one thing here its reader MUTATES — `attach_feeds` and all three
    context legs write keys onto it. Handing two fires the same dict would put one symbol's
    derivative series and peer cohort on another symbol's frame, silently and with every
    column still populated."""
    cache = PeerCandleCache(MockMarketDataCollector())
    first = cache.frame("ETHUSDT", "1h", limit=20, now=NOW)
    first["funding"] = ["mine"]
    first["candles"].append(_candle(99, 1.0))

    second = cache.frame("ETHUSDT", "1h", limit=20, now=NOW)
    assert cache.calls == 1, "still one read — the copy is not a second fetch"
    assert "funding" not in second
    assert len(second["candles"]) == 20
    assert second["candles"][0] is first["candles"][0], "the bars themselves are shared"


# --- the mintable vocabulary and the families ---------------------------------

def test_only_the_normalized_columns_are_mintable():
    """The raw-`open_interest` rule. A dispersion LEVEL means something different at 1h than
    at 4h, and ``xs_members`` is a count of how many peers answered — a condition on it would
    mine feed availability rather than the market."""
    for column in features.XS_NUMERIC_COLUMNS:
        assert column in factory.NUMERIC_FEATURES, column
    for column in features.XS_EVIDENCE_COLUMNS:
        assert column not in factory.NUMERIC_FEATURES, column
    assert set(features.XS_EVIDENCE_COLUMNS) == {"xs_dispersion", "xs_members"}


def test_the_long_and_short_legs_are_disjoint_across_the_whole_param_space():
    """A property of the SPACE, not of one mined value. Independent floor/ceiling params could
    be mined into an overlap (long >= 0.4 while short <= 0.6), and a long and a short matching
    on one feature row is `paper.BLOCK_DIRECTION_CONFLICT` — the pair failing closed on
    exactly the bars it was minted for. One shared ``xs_rank_edge`` capped below 0.5 is what
    makes that unreachable.

    The families became ``xs_reversion_*`` on 2026-08-04 (the momentum pair reads the same
    column with the opposite sign and was swapped out). The construction is unchanged and so is
    this property — what MIRRORED is which leg sits at which end: reversion buys the cohort's
    laggards, so the LONG leg is the one with the ceiling and the short leg the floor."""
    long_template = next(t for t in factory.TEMPLATES if t.family == "xs_reversion_long")
    short_template = next(t for t in factory.TEMPLATES if t.family == "xs_reversion_short")
    for template in (long_template, short_template):
        edge = template.param_space["xs_rank_edge"]
        assert edge.lo >= 0.0 and edge.hi < 0.5, "an edge at or above 0.5 admits an overlap"

    widest = long_template.param_space["xs_rank_edge"].hi
    long_ceiling = long_template.entry_builder(
        {**long_template.base_params, "xs_rank_edge": widest}
    )[0]["value"]
    short_floor = short_template.entry_builder(
        {**short_template.base_params, "xs_rank_edge": widest}
    )[0]["value"]
    assert long_ceiling < short_floor


def test_the_families_are_minted_and_reach_a_real_trade_count():
    """A family that mints but can never fire is noise pretending to be diversity — the exact
    thing ``templates_for_timeframe``'s other three gates exist to prevent. Measured rather
    than asserted: on a 2,000-bar mock frame both legs fire on well over a hundred bars."""
    collector = MockMarketDataCollector()
    snapshot, _ = collect_market_data("ETHUSDT", "1h", collector=collector, now=NOW, limit=2000)
    assert attach_cross_section(snapshot, collector=collector, now=NOW, limit=2000) is None
    rows = build_feature_rows(snapshot)

    for family in sorted(factory.CROSS_SECTION_FAMILIES):
        template = next(
            t for t in factory.templates_for_timeframe("1h", symbol="ETHUSDT")
            if t.family == family
        )
        spec = StrategySpec.from_dict(factory.build_spec_dict(
            template, template.base_params, strategy_id="S001",
            generation_id="GEN-001", symbol="ETHUSDT",
        ))
        assert factory.validate_strategy(spec)["approved_for_backtest"], family
        matched = sum(1 for row in rows if evaluate_spec(spec, row).matched)
        assert matched > 100, f"{family} fired on only {matched} of {len(rows)} bars"


def test_the_families_survive_every_timeframe_because_the_cohort_is_a_constant():
    """Unlike htf_* and session_*, nothing about a coarser bar makes a rank undefined — so
    these must NOT drop at 1d, where the library is otherwise at its thinnest."""
    for timeframe in ("15m", "1h", "4h", "1d"):
        families = {t.family for t in factory.templates_for_timeframe(timeframe)}
        assert factory.CROSS_SECTION_FAMILIES <= families, timeframe


def test_the_cohort_gate_drops_the_families_when_the_universe_is_too_small(monkeypatch):
    """The gate does not bind today, which is exactly why it needs a test: shrink the cohort
    below the floor and every ``xs_*`` column is permanently None, so the families would still
    mint, still backtest, take zero trades, and be retired as FRAGILE — the family blamed for
    a cohort too small to rank."""
    from runtime.mvp_runtime.crypto import market_data

    monkeypatch.setattr(market_data, "CROSS_SECTION_UNIVERSE", ("BTCUSDT", "ETHUSDT"))
    families = {t.family for t in factory.templates_for_timeframe("1h", symbol="ETHUSDT")}
    assert not (factory.CROSS_SECTION_FAMILIES & families)
    # ...and the rest of the library is untouched, so the gate is narrow.
    assert "trend_pullback" in families


def test_a_spec_reading_a_rank_does_not_trade_without_a_cohort():
    """End to end through the evaluator: the fail-closed rule for an absent measurement, on
    the columns whose absence is most plausible-looking."""
    spec = StrategySpec.from_dict({
        "schema_version": "strategy_spec.v1", "strategy_id": "S001", "strategy_version": "1.0",
        "strategy_family": "xs_momentum_long", "direction": "long", "timeframe": "1h",
        "symbol_scope": ["ETHUSDT"],
        "entry_rules": {"operator": "AND", "conditions": [
            {"feature": "xs_rank_pct", "comparison": ">=", "value": 0.8},
        ]},
        "exit_rules": {"stop_model": "atr", "stop_atr": 1.0, "target_atr": 2.0,
                       "max_holding_bars": 12},
    })
    without = build_feature_rows(
        {"symbol": "ETHUSDT", "timeframe": "1h", "candles": _ramp(12, step=1.0)}
    )[-1]
    assert evaluate_spec(spec, without).matched is False

    with_cohort = build_feature_rows(_snapshot(5.0, [1.0, 2.0, 3.0, 4.0]))[-1]
    assert evaluate_spec(spec, with_cohort).matched is True

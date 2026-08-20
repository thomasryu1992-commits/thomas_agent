"""C3 feature rows — the evaluator's input, built from a C2 market snapshot.

Ports the slice of the source system's ``feature_store.build_feature_frame`` that the
active strategy population actually references (audited against the real pool: 38
strategies, 21 distinct feature names), with the source's own defaults for every
period. Everything OHLCV-derived is computed by ``indicators``; the four names that
come from feeds not yet ported (mark/index price, basis, liquidation spike) carry the
source's **documented no-feed fallbacks** — mark/index fall back to close (making the
basis 0), liquidations legacy-fill to a 0.0 spike ratio — so a spec referencing them
evaluates exactly as the source system does when those feeds are absent. Every other
unknown feature is simply absent from the row, which the evaluator treats as
indeterminate → no entry (fail-closed, the source's own rule).

Warm-up rows carry ``data_quality_status="WARMUP"`` when any of close/atr/rsi/adx is
still None (the source's definition), so downstream health checks can refuse to trade
on an unwarmed row without re-deriving the rule.

**Beyond the source port: order flow and session.** Almost everything above is a
transformation of one series — the OHLCV history — so twenty template families built on
it recombine information the pool already holds. Two column groups here are not:

- ``taker_*`` reads the kline legs the collector already downloads and used to discard
  (see ``market_data.Candle``). Who crossed the spread is not recoverable from OHLC: two
  bars with identical open/high/low/close differ entirely depending on whether the move
  was bought into or sold into. It costs no request, no vendor and no grant.
- ``session`` / ``day_type`` come from the timestamp already on every row. The market is
  continuous but its participants are not, and the composition of who is trading is a
  property no price transformation encodes.
- ``mark_price`` / ``index_price`` / ``mark_index_basis_bps`` / ``premium_index`` are the
  venue's derivative pricing, now measured instead of assumed. The first three carried the
  source's no-feed fallbacks (close, close, a hardcoded ``0.0``) while the factory could mint
  literal conditions on them; ``premium_index`` is the basis funding is computed from, at bar
  resolution rather than the 8h event cadence ``funding_rate`` carries.
- ``ref_*`` measure this symbol against a market proxy. Every other column describes one
  symbol alone, which is why every pool strategy carries the same market beta with no feature
  able to say so; ``rel_strength_roc_4`` subtracts that shared move out and
  ``ref_correlation`` says how much of it there was.

All of them follow the open-interest absence rule rather than the
``liquidation_spike_ratio`` one: missing input yields None (indeterminate → no entry), never
a constant a mined condition could match on. The derivative and reference groups make that
rule load-bearing rather than merely tidy, because both REPLACED fabricated constants —
see ``_derivative_price_columns`` and ``_reference_columns``.

**Three alignment rules live here, and choosing wrongly is silent.** An exact open-time join
for series on this frame's own grid (:func:`_same_grid_column`: derivative prices, the
reference symbol); backward as-of for series on their own cadence (:func:`_asof_align`:
funding, liquidations, open interest); close-time keying for the coarser higher timeframe
(:func:`_htf_columns`). Each is named where it applies and each has its own test. Together
they form the **lookahead guard**: every column at bar i depends only on data at or before
bar i, so a prefix of the built frame carries exactly the values those bars had in the full
series — the property that makes replay-live parity hold on differently-sized candle windows
(pinned by ``test_prefix_invariance_holds_with_htf_and_external_series``).
"""

from __future__ import annotations

from typing import Any

from . import indicators

# Source-system defaults (config keys features.*), fixed — the MVP carries no AppConfig.
MA_FAST = 20
MA_SLOW = 50
EMA_FAST = 20
EMA_SLOW = 50
ATR_PERIOD = 14
RSI_PERIOD = 14
ADX_PERIOD = 14
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
BB_PERIOD = 20
BB_STD = 2.0
PERCENTILE_WINDOW = 100
# Observations the volatility reference needs before it will answer. The same looser floor the
# funding z-score uses, and for the same reason: a live sizing decision should not wait 100 bars
# to become possible when 20 observations already locate a typical level. Below it the reference
# is None and the multiplier is 1.0 — unscaled, never a guessed reference.
ATR_REFERENCE_MIN_PERIODS = 20
ROC_FAST = 4
VOLUME_Z_WINDOW = 20
ADX_TREND_THRESHOLD = 20.0  # entry_policy.adx_trend_threshold source default
FUNDING_Z_WINDOW = 100      # SETTLEMENTS (~33.3 days on every timeframe) since the 2026-08-17
                            # event-space decision; the source default judged 100 BARS
FUNDING_Z_MIN_PERIODS = 10  # the source's looser min_periods, now counted in settlements
OI_CHANGE_PERIOD = 1        # OI READINGS between the two reads a change compares (feed cadence, not bars)
OI_Z_WINDOW = 30            # OI readings the z-score judges the current level against
OI_Z_MIN_PERIODS = 10
LIQ_MA_WINDOW = 50          # liquidation spike baseline window
LIQ_MA_MIN_PERIODS = 10

# --- taker order flow ---------------------------------------------------------
# Windows for the flow derivatives. 20 matches VOLUME_Z_WINDOW deliberately: the two
# describe the same bars from different sides (how much traded vs who was the aggressor),
# and a threshold mined on one should mean the same span as one mined on the other.
TAKER_FLOW_Z_WINDOW = 20
TAKER_FLOW_MA_WINDOW = 20
TAKER_FLOW_MA_MIN_PERIODS = 10
TRADE_SIZE_Z_WINDOW = 20
TRADE_SIZE_Z_MIN_PERIODS = 10

# --- derivative price series --------------------------------------------------
# The premium index z-score window. Premium is a BAR-native series (collected at the frame's
# own interval), so bar space is its correct time base — unlike funding, whose z moved to its
# 8h settlement cadence on 2026-08-17. The shared 100/10 numbers survive that split but the
# SPANS no longer match across the pair: 100 premium bars is 16.7 days at 4h and 100 days at
# 1d, while 100 settlements is ~33.3 days everywhere — a threshold mined on one does not mean
# the other's span. The looser min_periods is the funding series' own rule, kept because a
# spec should not wait 100 bars to become evaluable when 10 observations already locate the
# current reading.
PREMIUM_Z_WINDOW = 100
PREMIUM_Z_MIN_PERIODS = 10

# --- reference-symbol (cross-asset) context -----------------------------------
# The market proxy every other symbol is measured against, and the window its correlation
# is measured over. 100 bars matches PERCENTILE_WINDOW: long enough that the estimate is
# not one week's accident, short enough that a regime change eventually shows in it.
REFERENCE_CORR_WINDOW = 100
REFERENCE_CORR_MIN_PERIODS = 30

# The reference columns a spec may reference. All four are NORMALIZED — a rate of change, a
# difference of two rates, a correlation, and a label — so none of them carries a price
# scale and a mined threshold means the same thing on ETH and on DOGE.
REFERENCE_NUMERIC_COLUMNS = ("ref_roc_4", "rel_strength_roc_4", "ref_correlation")
REFERENCE_CATEGORICAL_COLUMNS = ("ref_market_regime",)
REFERENCE_COLUMNS = (*REFERENCE_NUMERIC_COLUMNS, *REFERENCE_CATEGORICAL_COLUMNS)

# --- cross-sectional (universe) context ----------------------------------------
# Where this symbol RANKS among its cohort, rather than how it compares to one proxy.
# `rel_strength_roc_4` answers "is this beating BTC"; these answer "is this the strongest
# thing available", which is the question a cross-sectional strategy actually asks and the
# one a single benchmark cannot express: every altcoin can beat BTC at once, and knowing
# that says nothing about which of them to buy.
#
# The cohort is `market_data.CROSS_SECTION_UNIVERSE` — see there for why it is a declared
# constant and what changing it invalidates.

# The fewest determinate cohort members a rank will be computed from. Below it the columns
# are None, deliberately: `xs_rank_pct` has only `members - 1` distinct values, so a cohort
# of two offers {0.0, 1.0} — a rank that is really a coin flip, and a mined threshold on it
# is a mined coin flip. The floor is what stops a fan-out where four of six peers failed
# from silently reporting ranks with the same column names and a tenth of the information.
MIN_CROSS_SECTION_MEMBERS = 4

# The dispersion reference window. PERCENTILE_WINDOW, matching `atr_pct_reference`, because
# it is the same kind of quantity — "what is normal for this measurement lately" — and a
# threshold mined against one span should mean the same span as one mined against the other.
XS_DISPERSION_WINDOW = PERCENTILE_WINDOW
XS_DISPERSION_MIN_PERIODS = ATR_REFERENCE_MIN_PERIODS

# Mintable: a rank fraction in [0, 1], an excess rate of change, and a dispersion RATIO
# against its own recent normal. All three are scale-free by construction, so a mined
# threshold means the same thing on BTC and on DOGE, and — the `atr_pct_reference` argument —
# the ratio is self-calibrating, so there is no dispersion level anybody had to authorize.
XS_NUMERIC_COLUMNS = ("xs_rank_pct", "xs_excess_roc_4", "xs_dispersion_ratio")
# Evidence only, never mintable, for the raw-`open_interest` reason. `xs_dispersion` is a
# level whose typical size differs by timeframe, so a threshold mined at 1h would mean
# something else at 4h; `xs_members` is a count of how much data arrived, so a condition on
# it would mine feed availability rather than the market.
XS_EVIDENCE_COLUMNS = ("xs_dispersion", "xs_members")
XS_COLUMNS = (*XS_NUMERIC_COLUMNS, *XS_EVIDENCE_COLUMNS)

# --- positioning (who is HOLDING, not who is trading) ---------------------------
# `positioning_store` has accumulated three hourly series per symbol since it shipped:
# `top_position` (the long share of the top cohort's POSITIONS), `top_account` (the same cohort
# counted by ACCOUNTS) and `global_account` (every account). The one the research record singled
# out is the DIVERGENCE between the first and the last — large capital against the crowd — because
# it is the one measurement here that cannot be derived from OHLCV at any resolution.
#
# Windows match the funding z-score deliberately: both describe a crowding pressure in its own
# cadence, at the same 100-observation depth. Depth, not calendar span — 100 hourly readings is
# ~4.2 days while 100 funding settlements is ~33.3 — and since 2026-08-17 funding actually is
# on its own cadence (it was bar-space between the C3 port and the event-space decision).
POSITIONING_Z_WINDOW = 100
POSITIONING_Z_MIN_PERIODS = 10
# Periods a change is measured over, in the SERIES' own time base (hourly readings, not bars) —
# the `open_interest` rule, and for its reason: a bar-over-bar diff of the ALIGNED column is zero
# on every bar between two hourly readings, which on a 15m frame is three bars in four.
POSITIONING_CHANGE_PERIODS = 24

# Mintable: a standardisation and a change. Both cancel a symbol's own structural baseline — a top
# cohort that habitually runs 5 points longer than the crowd on one symbol and 15 on another reads
# the same in both.
POSITIONING_NUMERIC_COLUMNS = ("positioning_divergence_zscore", "positioning_divergence_change")
# Evidence only, never mintable. `positioning_divergence` is a LEVEL: dimensionless and bounded,
# which is not the same as comparable — whether +0.05 means the same on BTC as on DOGE is an
# empirical question nobody has answered, so minting a threshold on it would assert the answer.
# The three raw shares are levels for the same reason. Exactly the `xs_dispersion` split: keep the
# level as evidence, mint only what is standardised.
POSITIONING_EVIDENCE_COLUMNS = (
    "positioning_divergence", "positioning_top_long", "positioning_top_account_long",
    "positioning_global_long",
)
POSITIONING_COLUMNS = (*POSITIONING_NUMERIC_COLUMNS, *POSITIONING_EVIDENCE_COLUMNS)

# --- session context ----------------------------------------------------------
# UTC hour blocks. Crypto trades continuously but its PARTICIPANTS do not: venue activity
# concentrates in regional working hours, and the composition differs (retail-heavy vs
# desk-heavy), which is what a session label is actually a proxy for.
SESSION_BOUNDS: tuple[tuple[str, int, int], ...] = (
    ("ASIA", 0, 8),      # 00:00-07:59 UTC
    ("EUROPE", 8, 16),   # 08:00-15:59 UTC
    ("US", 16, 24),      # 16:00-23:59 UTC
)
SESSION_VALUES = frozenset(name for name, _lo, _hi in SESSION_BOUNDS)
DAY_TYPE_VALUES = frozenset({"WEEKDAY", "WEEKEND"})

# The longest bar a session label can describe. A bar longer than one session block spans
# several of them, so the label would be a property of the bar's OPEN hour rather than of
# the market during the bar — and at 1d every bar opens at 00:00, making it the CONSTANT
# "ASIA". A constant that a mined condition can match on is the `liquidation_spike_ratio`
# hazard, so the column goes None instead: no session, no session-conditioned entry.
MAX_SESSION_BAR_MINUTES = 4 * 60


def _asof_align(
    bar_times: list[str], events: list[dict[str, Any]], columns: tuple[str, ...],
    *, numeric: bool = True,
) -> dict[str, list]:
    """Pure-python ``merge_asof(direction='backward')`` — each bar carries the last
    event at or before its OPEN time (an event can never leak into earlier bars);
    bars before the first event, or with an unparseable key, stay None (the
    source's rule: indeterminate, never a constant).

    ``numeric=False`` passes values through uncast, for categorical columns (the
    HTF regime label); the alignment itself is identical, and that identity is the
    point — one look-ahead rule, not two."""
    from .. import timeutil as _timeutil

    parsed_events: list[tuple[Any, dict[str, Any]]] = []
    for event in events:
        raw = event.get("timestamp")
        if not isinstance(raw, str):
            continue
        try:
            parsed_events.append((_timeutil.parse_iso(raw), event))
        except (ValueError, TypeError):
            continue
    parsed_events.sort(key=lambda pair: pair[0])
    out: dict[str, list] = {col: [] for col in columns}
    cursor = 0
    last: dict[str, Any] | None = None
    for raw_bar in bar_times:
        try:
            bar_ts = _timeutil.parse_iso(raw_bar)
        except (ValueError, TypeError):
            for col in columns:
                out[col].append(None)
            continue
        while cursor < len(parsed_events) and parsed_events[cursor][0] <= bar_ts:
            last = parsed_events[cursor][1]
            cursor += 1
        for col in columns:
            value = last.get(col) if last is not None else None
            if not numeric:
                out[col].append(value)
                continue
            try:
                out[col].append(float(value) if value is not None else None)
            except (TypeError, ValueError):
                out[col].append(None)
    return out


def _same_grid_column(bar_times: list[str], events: list[Any]) -> list[float | None]:
    """A series that shares this frame's grid, joined on bar OPEN time. Exact, not as-of.

    The derivative price series (mark, index, premium index) are requested at the SAME
    interval as the candles, so their bar opening at T *is* the bar opening at T here, and
    its close is first known at exactly the moment this row's own ``close`` is. Pairing them
    is simultaneous, so an exact key match is both correct and the strongest available
    statement: a bar with no counterpart stays None rather than silently inheriting an older
    one, which is what :func:`_asof_align` would do.

    That difference is deliberate and is the reason this is a separate function. As-of
    alignment is right for a series on its OWN cadence (funding every 8h, open interest
    daily) — carrying the last known reading forward is the honest read there. It would be
    wrong here: a gap in the mark series means the venue did not report that bar, and
    substituting the previous bar's mark would fabricate a basis for a bar that has none.
    And the HTF leg needs a third rule again (key on the higher candle's CLOSE), because it
    spans a coarser grid where the simultaneous bar is still forming. Three situations,
    three rules, each named where it applies.
    """
    by_open: dict[str, float] = {}
    for event in events:
        if not isinstance(event, dict):
            continue
        stamp, value = event.get("timestamp"), event.get("value")
        if not isinstance(stamp, str) or isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        by_open[stamp] = float(value)
    return [by_open.get(bar_time) for bar_time in bar_times]


def _derivative_price_columns(bar_times: list[str], snapshot: dict[str, Any]) -> dict[str, list]:
    """``mark_price`` / ``index_price`` / ``mark_index_basis_bps`` / premium columns.

    **This replaces a fabricated constant, and that is a behaviour change worth stating.**
    Before the series existed these three were the source system's documented no-feed
    fallbacks — ``mark = index = close`` and a basis hardcoded to ``0.0`` — while
    ``factory.NUMERIC_FEATURES`` admitted all three as mintable. A literal condition on a
    basis that is always exactly zero is not selective, it is decided: ``basis > 0.5`` could
    never match and ``basis <= 0.5`` could never fail. The column was worse than absent,
    because absent columns are indeterminate and this one was confidently wrong.

    So absence now yields **None**, not the old constants: no feed, no basis, no entry for a
    spec that reads one. That is the ``open_interest`` rule and the opposite of the
    ``liquidation_spike_ratio`` one, and it means a pool spec referencing the basis stops
    trading while the series is unavailable rather than matching on a zero. Strictly safer,
    and the series rides the candles' own grant, so "unavailable" means the venue failed
    rather than the feed being unconfigured.

    ``premium_index`` is the funding basis at BAR resolution. ``funding_rate`` is an 8h
    event carried forward by :func:`_asof_align`, so ``funding_zscore`` steps three times a
    day; a 1h ``funding_fade_*`` spec has therefore been timing its entry on a value that
    last moved up to eight hours ago. This column moves every bar.
    """
    has_mark = "mark_prices" in snapshot
    has_index = "index_prices" in snapshot
    has_premium = "premium_index" in snapshot

    mark = (_same_grid_column(bar_times, snapshot.get("mark_prices") or [])
            if has_mark else [None] * len(bar_times))
    index = (_same_grid_column(bar_times, snapshot.get("index_prices") or [])
             if has_index else [None] * len(bar_times))
    premium = (_same_grid_column(bar_times, snapshot.get("premium_index") or [])
               if has_premium else [None] * len(bar_times))

    # Basis needs BOTH legs and a non-zero divisor. A zero or negative index price is not a
    # 100% basis, it is a broken reading.
    basis_bps: list[float | None] = []
    for mark_value, index_value in zip(mark, index):
        if mark_value is None or index_value is None or index_value <= 0:
            basis_bps.append(None)
        else:
            basis_bps.append((mark_value - index_value) / index_value * 10_000.0)

    return {
        "mark_price": mark,
        "index_price": index,
        "mark_index_basis_bps": basis_bps,
        "premium_index": premium,
        "premium_index_zscore": indicators.zscore(
            premium, PREMIUM_Z_WINDOW, PREMIUM_Z_MIN_PERIODS
        ),
    }


def _reference_columns(
    bar_times: list[str], roc_own: list[float | None], closes: list[Any], snapshot: dict[str, Any]
) -> dict[str, list]:
    """Cross-asset context: how this symbol is behaving *relative to the market*.

    Every other column in this row describes one symbol in isolation, which is why every
    strategy the pool holds carries the same market beta: when the benchmark drops, every
    open long is losing at once, and no feature the row provides can tell the runtime that.
    ``rel_strength_roc_4`` subtracts that shared move out, and ``ref_correlation`` measures
    how much of it there was to subtract.

    **Alignment is an exact open-time join, like the derivative prices and unlike the HTF
    leg.** The reference series is collected at this frame's own interval, so its bar opening
    at T is the same bar as this one, and its close is first known at the same instant. The
    HTF leg keys on the higher candle's CLOSE because a coarser bar is still forming at this
    bar's open; here there is nothing still forming, so there is nothing to defer.

    The reference's own derived values come from :func:`build_feature_rows` — one feature
    pipeline, not a second that could drift — over a sub-snapshot carrying only candles, so
    the recursion terminates at depth one.

    **Every column is None when this symbol IS the reference.** ``rel_strength_roc_4`` would
    be exactly 0.0 and ``ref_correlation`` exactly 1.0 for the benchmark against itself:
    fabricated constants a mined condition could match on, which is the hazard this module
    refuses everywhere else. "How does the benchmark behave relative to the benchmark" is
    undefined rather than trivially answered, so the honest column is empty and a
    relative-strength spec simply never fires on the benchmark.
    """
    empty = {col: [None] * len(bar_times) for col in REFERENCE_COLUMNS}
    reference_candles = snapshot.get("reference_candles") or []
    symbol = str(snapshot.get("symbol") or "")
    reference_symbol = str(snapshot.get("reference_symbol") or "")
    if not reference_candles or (symbol and reference_symbol and symbol == reference_symbol):
        return empty

    reference_rows = build_feature_rows({"candles": reference_candles})
    ref_by_open: dict[str, dict[str, Any]] = {}
    for candle, row in zip(reference_candles, reference_rows):
        open_time = candle.get("open_time")
        if isinstance(open_time, str):
            ref_by_open[open_time] = row

    ref_roc: list[float | None] = []
    ref_regime: list[Any] = []
    ref_close: list[float | None] = []
    for bar_time in bar_times:
        row = ref_by_open.get(bar_time)
        if row is None:
            ref_roc.append(None)
            ref_regime.append(None)
            ref_close.append(None)
            continue
        value = row.get("roc_4")
        ref_roc.append(float(value) if isinstance(value, (int, float)) else None)
        ref_regime.append(row.get("market_regime"))
        close = row.get("close")
        ref_close.append(float(close) if isinstance(close, (int, float)) else None)

    # Excess momentum: this symbol's rate of change minus the benchmark's over the same
    # bars. Both legs must be known — a difference against an unknown is not zero.
    rel_strength = [
        (own - ref) if (own is not None and ref is not None) else None
        for own, ref in zip(roc_own, ref_roc)
    ]

    # Correlation is measured on bar-over-bar RETURNS, not on prices: two rising price
    # series correlate near 1.0 whatever they do bar to bar, which would report every pair
    # as identical and make the column useless for the thing it is for.
    own_returns = _bar_returns(closes)
    ref_returns = _bar_returns(ref_close)

    return {
        "ref_roc_4": ref_roc,
        "rel_strength_roc_4": rel_strength,
        "ref_correlation": indicators.rolling_correlation(
            own_returns, ref_returns, REFERENCE_CORR_WINDOW, REFERENCE_CORR_MIN_PERIODS
        ),
        "ref_market_regime": ref_regime,
    }


def _cross_section_columns(
    bar_times: list[str], roc_own: list[float | None], snapshot: dict[str, Any]
) -> dict[str, list]:
    """Where this symbol RANKS in its cohort, per bar — the cross-sectional leg.

    ``rel_strength_roc_4`` measures against one proxy, which cannot answer the question a
    cross-sectional strategy asks. Every altcoin can outperform BTC in the same hour; that
    tells the runtime nothing about *which* of them to buy, and nothing at all about which to
    sell. A rank does, and it is the first column in this row whose value depends on symbols
    the cycle is not trading.

    **Peer momentum comes from ``indicators.roc``, not from the full pipeline** —
    deliberately, and this is the one place it differs from :func:`_reference_columns`. That
    function recurses into :func:`build_feature_rows` because it needs ``ref_market_regime``,
    a classifier over many columns, and reimplementing it would be a second authority that
    could drift. Here the only peer quantity wanted is the same rate of change
    ``build_feature_rows`` itself computes as ``roc_4`` — ``indicators.roc(closes,
    ROC_FAST)`` — so calling that function directly is not a second implementation, it is
    the same one. Which is what makes a six-member cohort affordable: six ``roc`` calls
    rather than six 25-indicator pipelines, over a window that is 12,000 bars deep in the
    factory.

    **Alignment is the exact open-time join**, via :func:`_same_grid_column`, for the reason
    the reference leg uses it: peers are collected at this frame's own interval, so a peer bar
    opening at T *is* this bar, and its close is first known at the same instant. A bar with
    no peer counterpart drops that peer from the cohort rather than carrying its previous
    value forward, which would rank this symbol against a stale reading.

    Key ABSENT (no peers fetched, or every fetch failed) → every column None, so an ``xs_*``
    spec is indeterminate and does not trade. Partial arrival is honest rather than fatal:
    the cohort is whoever answered, ``xs_members`` says how many that was, and
    :data:`MIN_CROSS_SECTION_MEMBERS` is the floor below which the rank is not reported at
    all.

    The traded symbol is always a cohort member — it is the thing being ranked — so a cohort
    of ``n`` peers ranks ``n + 1`` series. Unlike the reference leg there is no
    self-comparison degeneracy to guard against: a symbol's rank among its peers is perfectly
    well defined whether or not it is itself a declared universe member, which is why this
    leg has no equivalent of ``attach_reference``'s skip-the-proxy rule.
    """
    empty = {col: [None] * len(bar_times) for col in XS_COLUMNS}
    peers = snapshot.get("peer_candles")
    if not isinstance(peers, dict) or not peers:
        return empty

    # One aligned momentum series per peer, on THIS frame's grid.
    peer_series: list[list[float | None]] = []
    for _peer_symbol, candles in sorted(peers.items()):
        if not isinstance(candles, list) or not candles:
            continue
        closes = [c.get("close") if isinstance(c, dict) else None for c in candles]
        peer_roc = indicators.roc(closes, ROC_FAST)
        events = [
            {"timestamp": candle.get("open_time"), "value": value}
            for candle, value in zip(candles, peer_roc)
            if isinstance(candle, dict)
        ]
        peer_series.append(_same_grid_column(bar_times, events))
    if not peer_series:
        return empty

    rank_pct: list[float | None] = []
    excess: list[float | None] = []
    dispersion: list[float | None] = []
    members: list[float | None] = []
    for index in range(len(bar_times)):
        own = roc_own[index] if index < len(roc_own) else None
        # No own momentum, no rank: this symbol's own position in the cohort is the entire
        # measurement, so an unknown own value makes the cohort's spread interesting to
        # nobody. Indeterminate, never a middling 0.5.
        if own is None:
            rank_pct.append(None)
            excess.append(None)
            dispersion.append(None)
            members.append(None)
            continue
        cohort = [own] + [
            series[index] for series in peer_series
            if series[index] is not None
        ]
        if len(cohort) < MIN_CROSS_SECTION_MEMBERS:
            rank_pct.append(None)
            excess.append(None)
            dispersion.append(None)
            members.append(float(len(cohort)))
            continue
        members.append(float(len(cohort)))
        # Fraction of the cohort this symbol is strictly above, so the weakest member is
        # exactly 0.0 and the strongest exactly 1.0. Ties share the LOWER value — no
        # tiebreak by symbol name, which would make the rank depend on an alphabet.
        below = sum(1 for value in cohort if value < own)
        rank_pct.append(below / (len(cohort) - 1))
        excess.append(own - _median(cohort))
        dispersion.append(_pstdev(cohort))

    return {
        "xs_rank_pct": rank_pct,
        "xs_excess_roc_4": excess,
        "xs_dispersion": dispersion,
        # How unusual today's dispersion is against its own recent normal. The RATIO rather
        # than the level is what a spec may mine, for the `atr_pct_reference` reason: a level
        # threshold would be a number nobody authorized and would mean one thing at 1h and
        # another at 4h. Median rather than mean for the same reason as there — a mean is
        # pulled up by exactly the dispersion spikes this is measuring against.
        "xs_dispersion_ratio": [
            (value / reference) if (value is not None and reference not in (None, 0)) else None
            for value, reference in zip(
                dispersion,
                indicators.rolling_median(
                    dispersion, XS_DISPERSION_WINDOW, XS_DISPERSION_MIN_PERIODS
                ),
            )
        ],
        "xs_members": members,
    }


def _positioning_columns(bar_times: list[str], snapshot: dict[str, Any]) -> dict[str, list]:
    """Where large capital sits relative to the crowd — the positioning leg.

    **The one measurement in this module that OHLCV cannot reproduce at any resolution.** Every
    other column describes what traded; these describe what is *held*, and by whom. `top_position`
    is the long share of the top cohort's positions, `global_account` the long share of every
    account, and the gap between them separates informed capital from headcount.

    **Alignment is backward as-of** (:func:`_asof_align`), the funding and open-interest rule —
    not the exact open-time join the derivative-price and cross-section legs use. The store keeps
    an HOURLY series on its own cadence, independent of the bar grid, so a bar carries the last
    reading at or before its open. Substituting the exact join here would blank every bar that is
    not on an hour boundary; substituting as-of over THERE would fabricate a basis for a bar the
    venue skipped. Three grids, three rules, each named where it applies.

    **The derived values are computed in the SERIES' own time base and then aligned**, which is
    the `open_interest` lesson repeated rather than rediscovered: a change or a z-score computed
    over the ALIGNED column would be measuring 24 repeats of one hourly reading on a 15m frame,
    and the z-score's window would mean 100 bars rather than 100 observations.

    Key ABSENT (no store rows supplied) → every column None, so a `positioning_*` spec is
    indeterminate and does not trade. The `open_interest` posture, never the
    `liquidation_spike_ratio` one.
    """
    empty = {col: [None] * len(bar_times) for col in POSITIONING_COLUMNS}
    rows = snapshot.get("positioning") or []
    if not isinstance(rows, list) or not rows:
        return empty

    # One reading per (series, timestamp), so the three series can be paired by time.
    by_series: dict[str, dict[str, float]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        series, stamp = row.get("series"), row.get("timestamp")
        value = row.get("long_ratio")
        if not isinstance(series, str) or not isinstance(stamp, str):
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        by_series.setdefault(series, {})[stamp] = float(value)

    top_position = by_series.get("top_position") or {}
    global_account = by_series.get("global_account") or {}
    top_account = by_series.get("top_account") or {}
    # The divergence needs BOTH legs at the same reading; a gap against an unknown is not zero.
    stamps = sorted(set(top_position) & set(global_account))
    if not stamps:
        return empty

    divergence = [top_position[s] - global_account[s] for s in stamps]
    zscore = indicators.zscore(divergence, POSITIONING_Z_WINDOW, POSITIONING_Z_MIN_PERIODS)
    change: list[float | None] = []
    for index in range(len(divergence)):
        previous = index - POSITIONING_CHANGE_PERIODS
        change.append(divergence[index] - divergence[previous] if previous >= 0 else None)

    events = [
        {
            "timestamp": stamp,
            "positioning_divergence": divergence[i],
            "positioning_divergence_zscore": zscore[i],
            "positioning_divergence_change": change[i],
            "positioning_top_long": top_position[stamp],
            "positioning_global_long": global_account[stamp],
            "positioning_top_account_long": top_account.get(stamp),
        }
        for i, stamp in enumerate(stamps)
    ]
    return _asof_align(bar_times, events, POSITIONING_COLUMNS)


def _median(values: list[float]) -> float:
    """Median of a non-empty list of floats."""
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def _pstdev(values: list[float]) -> float:
    """Population standard deviation; 0.0 for a single value.

    Population rather than sample: the cohort IS the population — it is the whole declared
    universe, not a draw from a larger one — so dividing by ``n - 1`` would be correcting for
    a sampling step that never happened.
    """
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return (sum((value - mean) ** 2 for value in values) / len(values)) ** 0.5


def _bar_returns(closes: list[Any]) -> list[float | None]:
    """Simple bar-over-bar returns; None wherever either end is missing or the base is 0."""
    out: list[float | None] = [None]
    for i in range(1, len(closes)):
        previous, current = closes[i - 1], closes[i]
        if not _is_positive(previous) or not isinstance(current, (int, float)) or isinstance(current, bool):
            out.append(None)
        else:
            out.append(float(current) / float(previous) - 1.0)
    return out


def _open_interest_event_columns(levels: list[Any]) -> dict[str, list]:
    """Change and z-score of the OI series, in the series' OWN time base.

    ``levels`` are consecutive readings of the feed (daily today), so a change here is
    day against day regardless of the bar size the caller will align onto. Non-numeric
    or non-positive readings break the chain rather than being coerced: a change
    against a missing or zero base is not zero, it is unknown."""
    numeric: list[float | None] = []
    for value in levels:
        try:
            numeric.append(float(value) if value is not None else None)
        except (TypeError, ValueError):
            numeric.append(None)
    change_pct: list[float | None] = []
    for index, current in enumerate(numeric):
        previous = numeric[index - OI_CHANGE_PERIOD] if index >= OI_CHANGE_PERIOD else None
        if current is None or previous in (None, 0):
            change_pct.append(None)
        else:
            change_pct.append((current - previous) / previous)
    return {
        "change_pct": change_pct,
        "zscore": indicators.zscore(numeric, OI_Z_WINDOW, OI_Z_MIN_PERIODS),
    }


def classify_market_regime(row: dict[str, Any], adx_threshold: float = ADX_TREND_THRESHOLD) -> str:
    """Port of the source regime classifier (features/regime.py), row-wise."""
    close, ma20, ma50, adx = row.get("close"), row.get("ma20"), row.get("ma50"), row.get("adx")
    atr_pct = row.get("atr_percentile")
    if close is None or ma20 is None or ma50 is None or adx is None:
        return "UNCLEAR"
    if atr_pct is not None:
        if atr_pct >= 0.80:
            return "HIGH_VOLATILITY"
        if atr_pct <= 0.20 and adx < adx_threshold:
            return "LOW_VOLATILITY"
    if close > ma20 > ma50 and adx >= adx_threshold:
        return "TREND_UP"
    if close < ma20 < ma50 and adx >= adx_threshold:
        return "TREND_DOWN"
    if adx < adx_threshold:
        return "RANGE"
    return "UNCLEAR"

# The minimum candle count for a fully-warmed row: bb_width_percentile needs
# BB_PERIOD - 1 warm-up plus max(10, PERCENTILE_WINDOW // 5) observations.
MIN_WARM_CANDLES = BB_PERIOD - 1 + max(10, PERCENTILE_WINDOW // 5)


# --- taker order flow ---------------------------------------------------------

def _taker_flow_columns(candles: list[dict[str, Any]]) -> dict[str, list]:
    """Order-flow columns from the kline legs the venue already sent.

    ``taker_buy_base`` is the volume bought by the side that CROSSED the spread. Over the
    bar's total volume it says who was the aggressor — the one thing in this module that
    price history cannot reconstruct, since a bar's OHLC is identical whether the move was
    bought into or sold into.

    Every column is indeterminate rather than constant when its input is missing (the
    open-interest posture, not the ``liquidation_spike_ratio`` one): a snapshot collected
    before these legs existed, or a venue that stops sending them, must make a flow spec
    stop trading — never let it match on a fabricated balance of 0.5.

    A reported buy volume outside ``[0, volume]`` is impossible, so it reads as unknown
    rather than being clamped into range: clamping would convert a broken payload into a
    confident extreme reading, which is the direction that trades."""
    volumes = [c.get("volume") for c in candles]
    taker_base = [c.get("taker_buy_base") for c in candles]
    trade_counts = [c.get("trade_count") for c in candles]

    ratio: list[float | None] = []
    for volume, bought in zip(volumes, taker_base):
        if not _is_positive(volume) or not isinstance(bought, (int, float)) or isinstance(bought, bool):
            ratio.append(None)
            continue
        share = float(bought) / float(volume)
        ratio.append(share if 0.0 <= share <= 1.0 else None)

    # Signed form, so a threshold reads the same on both sides of balance and a mined
    # `>= 0.2` means "buyers 60% of volume" rather than an offset from an implicit 0.5.
    imbalance = [2.0 * r - 1.0 if r is not None else None for r in ratio]

    # Trade SIZE, not trade count: the count is venue-scale (BTC prints far more trades
    # than SOL), but volume-per-trade normalizes it into "how big is the average
    # participant right now" — which is what separates a retail tape from a desk one.
    avg_trade_size: list[float | None] = []
    for volume, count in zip(volumes, trade_counts):
        if not _is_positive(volume) or not _is_positive(count):
            avg_trade_size.append(None)
        else:
            avg_trade_size.append(float(volume) / float(count))

    return {
        "taker_buy_ratio": ratio,
        "taker_flow_imbalance": imbalance,
        "taker_flow_zscore": indicators.zscore(imbalance, TAKER_FLOW_Z_WINDOW),
        # Persistent pressure rather than one bar's print: a single bar's imbalance is
        # mostly that bar's own move, while its rolling mean is the part that survived
        # several bars — the closest this row gets to a cumulative-delta slope.
        "taker_flow_ma": indicators.rolling_mean(
            imbalance, TAKER_FLOW_MA_WINDOW, TAKER_FLOW_MA_MIN_PERIODS
        ),
        "avg_trade_size": avg_trade_size,
        "avg_trade_size_zscore": indicators.zscore(
            avg_trade_size, TRADE_SIZE_Z_WINDOW, TRADE_SIZE_Z_MIN_PERIODS
        ),
        "trade_count_zscore": indicators.zscore(
            [float(c) if _is_positive(c) else None for c in trade_counts],
            TRADE_SIZE_Z_WINDOW, TRADE_SIZE_Z_MIN_PERIODS,
        ),
    }


def _is_positive(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


# --- session context ----------------------------------------------------------

def classify_session(hour_utc: int) -> str:
    """The UTC hour's session block. Total over 0-23 by construction."""
    for name, low, high in SESSION_BOUNDS:
        if low <= hour_utc < high:
            return name
    return SESSION_BOUNDS[-1][0]


def _session_columns(candles: list[dict[str, Any]]) -> dict[str, list]:
    """``session`` and ``day_type`` per bar, or None where the label cannot mean anything.

    The bar's duration is read from the series itself (first candle open→close) rather
    than from a ``timeframe`` key, so the rule holds identically in the HTF recursion,
    which passes candles alone. Bars longer than one session block get ``session=None``
    (see :data:`MAX_SESSION_BAR_MINUTES`); ``day_type`` stays available at every duration
    up to a day, because a daily bar genuinely IS one weekday or one weekend day."""
    from .. import timeutil as _timeutil

    minutes: float | None = None
    if candles:
        try:
            opened = _timeutil.parse_iso(str(candles[0]["open_time"]))
            closed = _timeutil.parse_iso(str(candles[0]["close_time"]))
            span = (closed - opened).total_seconds() / 60.0
            minutes = span if span > 0 else None
        except (KeyError, TypeError, ValueError):
            minutes = None
    session_applies = minutes is not None and minutes <= MAX_SESSION_BAR_MINUTES

    sessions: list[str | None] = []
    day_types: list[str | None] = []
    for candle in candles:
        try:
            opened = _timeutil.parse_iso(str(candle["open_time"]))
        except (KeyError, TypeError, ValueError):
            sessions.append(None)
            day_types.append(None)
            continue
        sessions.append(classify_session(opened.hour) if session_applies else None)
        day_types.append("WEEKEND" if opened.weekday() >= 5 else "WEEKDAY")
    return {"session": sessions, "day_type": day_types}

# --- higher-timeframe (HTF) context -------------------------------------------

# The HTF columns a spec may reference. Numeric ones are NORMALIZED (ratios, not
# prices) so a threshold means the same thing on BTC and DOGE; the regime label is
# the categorical one, and the whole point of the family — "only go long while the
# timeframe above says TREND_UP".
HTF_NUMERIC_COLUMNS = ("htf_adx", "htf_rsi", "htf_price_distance_ma20", "htf_ma20_distance_ma50")
HTF_CATEGORICAL_COLUMNS = ("htf_market_regime",)
HTF_COLUMNS = (*HTF_NUMERIC_COLUMNS, *HTF_CATEGORICAL_COLUMNS)


def _htf_columns(bar_times: list[str], htf_candles: list[dict[str, Any]]) -> dict[str, list]:
    """HTF feature columns aligned onto this timeframe's bars, point-in-time.

    **The look-ahead rule, and why it holds structurally.** Each HTF row is keyed by
    its candle's ``close_time`` — the first instant that candle is *known* — and
    aligned with the same backward :func:`_asof_align` the funding and liquidation
    feeds use, which matches on the lower bar's OPEN time. So a bar opening at 04:00
    sees the HTF candle that closed at 04:00 but never the one closing at 07:59:59,
    for live routing and replay alike. The classic HTF backtest error (letting the
    still-forming higher candle decide the lower bar) is therefore not something this
    code has to remember to avoid: the key is the close, so an unclosed candle has no
    key to match on.

    The HTF rows come from :func:`build_feature_rows` itself — one feature pipeline,
    not a second one that could drift — over a sub-snapshot carrying only candles, so
    the recursion terminates at depth one (no ``htf_candles`` key, no HTF columns)."""
    htf_rows = build_feature_rows({"candles": htf_candles})
    events: list[dict[str, Any]] = []
    for candle, row in zip(htf_candles, htf_rows):
        close_time = candle.get("close_time")
        if not isinstance(close_time, str):
            continue  # unkeyable: cannot be placed in time, so it is never visible
        ma20, ma50, close = row.get("ma20"), row.get("ma50"), row.get("close")
        events.append({
            "timestamp": close_time,
            "htf_adx": row.get("adx"),
            "htf_rsi": row.get("rsi"),
            "htf_price_distance_ma20": (
                (close - ma20) / ma20 if (close is not None and ma20 not in (None, 0)) else None
            ),
            "htf_ma20_distance_ma50": (
                (ma20 - ma50) / ma50 if (ma20 is not None and ma50 not in (None, 0)) else None
            ),
            "htf_market_regime": row.get("market_regime"),
        })
    columns = _asof_align(bar_times, events, HTF_NUMERIC_COLUMNS)
    columns.update(_asof_align(bar_times, events, HTF_CATEGORICAL_COLUMNS, numeric=False))
    return columns


def build_feature_rows(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Compute one feature row per candle from a C2 snapshot dict.

    Deterministic, pure, no I/O. Row values are float or None (indeterminate).
    """
    candles = snapshot.get("candles") or []
    opens = [c["open"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    closes = [c["close"] for c in candles]
    volumes = [c["volume"] for c in candles]

    ma20 = indicators.sma(closes, MA_FAST)
    ma50 = indicators.sma(closes, MA_SLOW)
    ema20 = indicators.ema(closes, EMA_FAST)
    ema50 = indicators.ema(closes, EMA_SLOW)
    atr = indicators.atr(highs, lows, closes, ATR_PERIOD)
    rsi = indicators.rsi(closes, RSI_PERIOD)
    adx = indicators.adx(highs, lows, closes, ADX_PERIOD)
    macd_line, macd_signal, macd_hist = indicators.macd(closes, MACD_FAST, MACD_SLOW, MACD_SIGNAL)
    bb_upper, bb_lower, bb_width_pct, bb_percent_b = indicators.bollinger(closes, BB_PERIOD, BB_STD)
    bb_width_percentile = indicators.rolling_percentile(bb_width_pct, PERCENTILE_WINDOW)
    roc_4 = indicators.roc(closes, ROC_FAST)
    volume_zscore = indicators.zscore(volumes, VOLUME_Z_WINDOW)
    atr_pct_of_price = [
        (a / c if (a is not None and isinstance(c, (int, float)) and c != 0) else None)
        for a, c in zip(atr, closes)
    ]
    atr_percentile = indicators.rolling_percentile(atr_pct_of_price, PERCENTILE_WINDOW)
    # What "normal volatility" is for this symbol right now, in the same units as
    # `atr_pct_of_price` — so the live size multiplier is a RATIO of two measured quantities
    # rather than a constant somebody chose. Self-calibrating on purpose: a target volatility
    # written into the code would be a number nobody authorized (the #356 lesson) and would
    # mean something different on BTC than on DOGE. Median, not mean — see
    # `indicators.rolling_median` for why the mean's bias here is the unsafe direction.
    atr_pct_reference = indicators.rolling_median(
        atr_pct_of_price, PERCENTILE_WINDOW, ATR_REFERENCE_MIN_PERIODS
    )

    # C9 derivative feeds. Absent, present-and-empty, or fetched — every one of them yields
    # None where there is no measurement. Indeterminate, never a constant, so a spec
    # referencing them fails closed to no-entry.
    #
    # **The key-ABSENT branch used to be the exception, and it was the last fabricated constant
    # in this vocabulary.** It kept the source's pre-C9 0-fill for `funding_rate`,
    # `funding_zscore` and `liquidation_spike_ratio` on the reasoning that an unconfigured feed
    # is not a degraded one. That distinction is real and it does not survive contact with what
    # the columns are used for: all three are in `factory.NUMERIC_FEATURES`, so a mined literal
    # against a value that is always exactly zero does not SELECT, it DECIDES — every
    # `spike_ratio < x` was true on every bar of a machine that never measured it, and
    # `unsuppliable_features` cannot catch it because the column is populated, just fabricated.
    #
    # It was live rather than theoretical: `market_data.NoLiquidationFeed` is the DEFAULT, so
    # any machine without a Coinalyze grant took the branch on every replay row.
    # `test_a_missing_feed_cannot_satisfy_a_condition` asserted the hazard as folklore-made-fact
    # — a `< 1.0` condition matching the constant — and now asserts that it cannot.
    #
    # This is the `mark_index_basis_bps` decision, applied to the feeds it was deferred on. That
    # one removed `mark == index == close` and `basis == 0.0` for exactly this reason once the
    # series were collected; funding and liquidations are both collected now, so the condition
    # that justified deferring is gone. Behaviour is unchanged for every spec in the store: the
    # one that names `liquidation_spike_ratio` reads `> 0.580805` and the `funding_fade_*`
    # families gate on |z| >= 1.0, all of which were false against 0.0 and are indeterminate
    # now — no entry either way. What changes is the direction nobody had minted yet.
    bar_times = [c["open_time"] for c in candles]
    # HTF context. Key ABSENT (or empty) = no higher timeframe supplied — every HTF
    # column is None, so an htf_* spec is indeterminate and never matches. That is
    # the liquidation posture, not the spike-ratio one: no HTF, no trade, never a
    # fabricated constant that would let a filter silently pass.
    htf_candles = snapshot.get("htf_candles") or []
    htf = (_htf_columns(bar_times, htf_candles) if htf_candles
           else {col: [None] * len(candles) for col in HTF_COLUMNS})
    has_funding_series = "funding" in snapshot
    if has_funding_series:
        # The same events the alignment will see: an event without a usable timestamp must not
        # consume a z-window slot the aligned columns cannot carry.
        funding_events = [
            e for e in (snapshot.get("funding") or [])
            if isinstance(e, dict) and isinstance(e.get("timestamp"), str) and e.get("timestamp")
        ]
        funding_rate = _asof_align(bar_times, funding_events, ("funding_rate",))["funding_rate"]
        # Z in the SERIES' own time base, then aligned — the `open_interest`/positioning rule,
        # adopted for funding by decision 2026-08-17 (Thomas; evidence and options in
        # docs/proposals/FUNDING_ZSCORE_TIME_BASE_V0.1.md). The z over the ALIGNED column this
        # replaces re-counted each 8h settlement once per bar, so the 100-"observation" window
        # held ~4 real settlements at 15m and 300 at 1d — the same |z| threshold asked a
        # 16.7-day question at 4h and a 100-day question at 1d, and the two bases disagreed on
        # 15-20% of |z| >= 1.0 gate decisions (measured on live feeds, 2026-08-17). Now the
        # window is FUNDING_Z_WINDOW settlements (~33.3 days) on every timeframe. This is the
        # one deliberate deviation from C3 source parity; the parity test names it.
        ordered = sorted(funding_events, key=lambda e: str(e.get("timestamp") or ""))
        event_z = indicators.zscore(
            [e.get("funding_rate") for e in ordered], FUNDING_Z_WINDOW, FUNDING_Z_MIN_PERIODS
        )
        # None z rides along (warmup, zero-variance): a bar after such a settlement must read
        # indeterminate, never the previous settlement's stale z.
        funding_zscore = _asof_align(
            bar_times,
            [{"timestamp": e.get("timestamp"), "funding_zscore": z}
             for e, z in zip(ordered, event_z)],
            ("funding_zscore",),
        )["funding_zscore"]
    else:
        funding_rate = [None] * len(candles)
        funding_zscore = [None] * len(candles)

    has_liq_series = "liquidations" in snapshot
    if has_liq_series:
        aligned = _asof_align(bar_times, snapshot.get("liquidations") or [],
                              ("long_liquidation", "short_liquidation"))
        long_liq, short_liq = aligned["long_liquidation"], aligned["short_liquidation"]
        liq_total = [
            (l + s) if (l is not None and s is not None) else None
            for l, s in zip(long_liq, short_liq)
        ]
        liq_ma = indicators.rolling_mean(liq_total, LIQ_MA_WINDOW, LIQ_MA_MIN_PERIODS)
        liquidation_spike_ratio = [
            (t / m) if (t is not None and m is not None and m != 0) else None
            for t, m in zip(liq_total, liq_ma)
        ]
    else:
        long_liq = short_liq = liq_total = [None] * len(candles)
        # Was `[0.0] * len(candles)` — the last fabricated constant in the vocabulary, and the
        # only column here that an absent feed left MATCHABLE. See the note above.
        liquidation_spike_ratio = [None] * len(candles)

    # Open interest — how much position is OUTSTANDING, the third leg beside what it
    # costs to hold (funding) and what was forcibly closed (liquidations). Raw OI is a
    # venue-scale quantity and means nothing across symbols, so what the row carries
    # (and what a spec may reference) are the NORMALIZED derivatives: the rate of
    # change, and how unusual the current level is against its own recent history.
    #
    # Series semantics throughout: no feed (key absent) or a failed fetch (key present,
    # empty) both leave every column None — indeterminate, never a constant. That is
    # the liquidation posture deliberately, not the spike-ratio one: a missing feed
    # must not let an oi_* condition match on a fabricated zero.
    # **Derive in the series' own time base, then align.** The OI feed is daily while a
    # context may be 15m/1h/4h, so a bar-over-bar diff of the ALIGNED column is zero on
    # every bar between two daily values — 23 of 24 on an hourly frame. Measured on a
    # real 12,000-bar ETH 1h frame: the aligned series changed on 4.2% of bars and the
    # squeeze conditions intersected on 0 of 12,000, i.e. the families could not fire at
    # all. Computing the change and the z-score over the EVENTS (day against day) and
    # aligning the results instead gives every bar the last KNOWN daily reading, which
    # is what a point-in-time feature should carry, and makes the z-score's window mean
    # 30 daily observations rather than 30 repeats of one.
    if "open_interest" in snapshot:
        events = [e for e in (snapshot.get("open_interest") or []) if isinstance(e, dict)]
        events = sorted(events, key=lambda e: str(e.get("timestamp") or ""))
        levels = [e.get("open_interest") for e in events]
        derived = _open_interest_event_columns(levels)
        enriched = [
            {**event, "open_interest_change_pct": change, "open_interest_zscore": z}
            for event, change, z in zip(events, derived["change_pct"], derived["zscore"])
        ]
        aligned = _asof_align(
            bar_times, enriched,
            ("open_interest", "open_interest_change_pct", "open_interest_zscore"),
        )
        open_interest = aligned["open_interest"]
        open_interest_change_pct = aligned["open_interest_change_pct"]
        open_interest_zscore = aligned["open_interest_zscore"]
    else:
        open_interest = open_interest_change_pct = open_interest_zscore = [None] * len(candles)

    # Order flow and session context. Both are pure functions of the candle list — no feed,
    # no extra request — so they are computed unconditionally; their own absence rules
    # (missing legs → None, over-long bars → None) live inside the helpers.
    flow = _taker_flow_columns(candles)
    session = _session_columns(candles)
    derivative = _derivative_price_columns(bar_times, snapshot)
    reference = _reference_columns(bar_times, roc_4, closes, snapshot)
    # The cross-sectional leg reads `peer_candles`, which `_reference_columns`' recursive
    # sub-snapshot never carries — so the recursion cannot reach this and there is no second
    # level of peer fetching to worry about.
    cross_section = _cross_section_columns(bar_times, roc_4, snapshot)
    positioning = _positioning_columns(bar_times, snapshot)

    rows: list[dict[str, Any]] = []
    for i, candle in enumerate(candles):
        close = closes[i]
        row: dict[str, Any] = {
            "timestamp": candle["open_time"],
            "open": opens[i],
            "high": highs[i],
            "low": lows[i],
            "close": close,
            "volume": volumes[i],
            "ma20": ma20[i],
            "ma50": ma50[i],
            "ema20": ema20[i],
            "ema50": ema50[i],
            "atr": atr[i],
            "atr_pct_of_price": atr_pct_of_price[i],
            "atr_percentile": atr_percentile[i],
            # The typical volatility this bar's is measured against (live sizing).
            "atr_pct_reference": atr_pct_reference[i],
            "rsi": rsi[i],
            "adx": adx[i],
            "macd": macd_line[i],
            "macd_signal": macd_signal[i],
            "macd_hist": macd_hist[i],
            "volume_zscore": volume_zscore[i],
            "bb_upper": bb_upper[i],
            "bb_lower": bb_lower[i],
            "bb_width_pct": bb_width_pct[i],
            "bb_percent_b": bb_percent_b[i],
            "bb_width_percentile": bb_width_percentile[i],
            "roc_4": roc_4[i],
            "price_distance_ma20": (
                (close - ma20[i]) / ma20[i] if (ma20[i] not in (None, 0)) else None
            ),
            # C9 feeds (series semantics when configured; legacy constants when not).
            "funding_rate": funding_rate[i],
            "funding_zscore": funding_zscore[i],
            "long_liquidation": long_liq[i],
            "short_liquidation": short_liq[i],
            "liquidation_total": liq_total[i],
            "liquidation_spike_ratio": liquidation_spike_ratio[i],
            # Positioning: raw level (evidence only) + the normalized derivatives.
            "open_interest": open_interest[i],
            "open_interest_change_pct": open_interest_change_pct[i],
            "open_interest_zscore": open_interest_zscore[i],
            # Derivative prices, measured. These carried the source's no-feed fallbacks
            # (close / close / a hardcoded 0.0 basis) until the series were collected; the
            # fallbacks are gone because a mintable column that is always exactly zero
            # decides its own conditions rather than selecting on anything. Absent series
            # now read None — see `_derivative_price_columns` for why that is the safer
            # direction and what it changes for a spec that already reads the basis.
            "mark_price": derivative["mark_price"][i],
            "index_price": derivative["index_price"][i],
            "mark_index_basis_bps": derivative["mark_index_basis_bps"][i],
            # The funding basis at BAR resolution, beside the 8h event series above.
            "premium_index": derivative["premium_index"][i],
            "premium_index_zscore": derivative["premium_index_zscore"][i],
            # Raw flow legs as the venue reported them — evidence only, never mintable, for
            # the reason raw open_interest is not mintable: they are venue-scale quantities,
            # so a threshold on one would mean a different thing on every symbol and a
            # different thing again after the venue grows.
            "quote_volume": candle.get("quote_volume"),
            "trade_count": candle.get("trade_count"),
            "taker_buy_base": candle.get("taker_buy_base"),
            "taker_buy_quote": candle.get("taker_buy_quote"),
            "avg_trade_size": flow["avg_trade_size"][i],
            # ...and the normalized derivatives, which are the mintable ones.
            "taker_buy_ratio": flow["taker_buy_ratio"][i],
            "taker_flow_imbalance": flow["taker_flow_imbalance"][i],
            "taker_flow_zscore": flow["taker_flow_zscore"][i],
            "taker_flow_ma": flow["taker_flow_ma"][i],
            "avg_trade_size_zscore": flow["avg_trade_size_zscore"][i],
            "trade_count_zscore": flow["trade_count_zscore"][i],
            # Who is at the desk. Categorical, so the validator admits only ==/!= and the
            # factory cannot mine a threshold across a label that has no ordering.
            "session": session["session"][i],
            "day_type": session["day_type"][i],
            # HTF context: the last CLOSED higher-timeframe candle's read, or None.
            **{col: htf[col][i] for col in HTF_COLUMNS},
            # Cross-asset context: this symbol measured against the market proxy. All None
            # for the proxy itself — see `_reference_columns`.
            **{col: reference[col][i] for col in REFERENCE_COLUMNS},
            # Cross-sectional context: where this symbol RANKS in its declared cohort. The
            # first columns whose value depends on symbols this cycle is not trading — see
            # `_cross_section_columns`.
            **{col: cross_section[col][i] for col in XS_COLUMNS},
            # Positioning: who is HOLDING, from the store the runtime accumulates itself. The
            # only columns here that OHLCV cannot reproduce — see `_positioning_columns`.
            **{col: positioning[col][i] for col in POSITIONING_COLUMNS},
        }
        row["market_regime"] = classify_market_regime(row)
        row["data_quality_status"] = (
            "WARMUP"
            if any(row[k] is None for k in ("close", "atr", "rsi", "adx"))
            else "OK"
        )
        rows.append(row)
    return attach_previous_bar(rows)


# The prefix a lagged reference resolves to, and the only lag depth there is.
#
# **A crossover is the thing the spec vocabulary could not say.** `macd > macd_signal` is a
# STATE — true for the whole of a trend — so a spec built on it re-fires on every bar of that
# trend rather than on the bar the relationship changed. There was no way to write "crossed
# above": no lag operator, and `value_from` reads the same row. So the search could not see the
# hypothesis at all, whatever it did with parameters.
#
# One bar, not n. A crossover needs exactly one, and each further depth copies the whole numeric
# vocabulary into every row again — the cost here is row size, not CPU, and it is paid on every
# bar of every replay. "ADX rising for five bars" wants a different mechanism (a rolling
# derivative computed once) rather than five copies of everything, and is a later increment.
PREVIOUS_BAR_PREFIX = "prev_"


def attach_previous_bar(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Give every row the PREVIOUS row's values under ``prev_``. Pure; mutates and returns rows.

    The first row's are all ``None`` — there is no bar before it — which the fail-closed
    evaluator reads as indeterminate, so a spec that references a lag simply does not match on
    the first bar rather than matching on an invented one.

    Only numeric and label columns are copied, and `prev_` columns are never copied again: a
    row carries the bar before it, not the whole history, and `prev_prev_*` would grow the row
    without any way to reference it.
    """
    previous: dict[str, Any] | None = None
    for row in rows:
        lagged = {
            f"{PREVIOUS_BAR_PREFIX}{key}": (previous.get(key) if previous is not None else None)
            for key in row
            if not key.startswith(PREVIOUS_BAR_PREFIX)
        }
        previous = {k: v for k, v in row.items() if not k.startswith(PREVIOUS_BAR_PREFIX)}
        row.update(lagged)
    return rows


def latest_feature_row(snapshot: dict[str, Any]) -> dict[str, Any]:
    """The most recent feature row — what the entry evaluator consumes. Empty
    snapshot → empty row (every lookup indeterminate, so nothing can match)."""
    rows = build_feature_rows(snapshot)
    return rows[-1] if rows else {}

# Forward verdicts and regime episodes: how many independent market episodes the first verdicts rest on

**Status:** RECORD 2026-09-27 — 측정 기록. forward 판정 6건이 BTC 일봉 regime 구간 3–5개(하락 추세 0일)에서 나왔고, 반박·성숙 판정은 시간 분산 검사 없이 나온다. 에포크 경계에서 볼 결정 항목 2개(§5)를 함께 적는다.

**What this is:** an observation, with the method to reproduce it. It changes nothing. Changing a
judgment rule is a research-epoch boundary decision (`RESEARCH_EPOCH_V0.1.md` Q3, Thomas 2026-09-26).
New display machinery in `crypto/` is paused until the first cohort verdict (system review D3). This
record exists so the boundary decision starts from numbers.

**Question:** a strategy's evidence is counted in trades. Trades that come from one market episode
are not independent draws. How many independent regime episodes do the current verdicts actually
rest on, and does the judge account for it?

## 1. Method

- **Data:** public Binance USDⓈ-M candles for BTC, ETH, SOL, BNB and DOGE at 1h, 4h and 1d. Collected
  with the factory's own collector (`market_data.select_market_data_collector`, which returned
  `BinanceFuturesCollector`) and its own window (`factory_candle_target`): 24,000 / 6,000 / 2,000
  bars, none `depth_capped`. 1h and 4h span 2024-01-01 → 2026-09-27. 1d spans 2021-04-06 →
  2026-09-26.
- **Labels:** `features.build_feature_rows`, which attaches `classify_market_regime` per bar
  (TREND_UP, TREND_DOWN, RANGE, HIGH_VOLATILITY, LOW_VOLATILITY, UNCLEAR).
- **Episode (this record's construction, not a repository concept):** a maximal run of one label,
  allowing interruptions of ≤ 1 day, kept only if it lasts ≥ 3 days. UNCLEAR is never an episode.
  BTC on its own 1d labels is the macro proxy.
- **Forward rows:** `crypto/forward_cohort_outcomes.jsonl`, 600 rows as of 2026-09-27, opened
  2026-08-04 → 2026-09-25. Each trade is mapped to the episode of the bar it opened in.
- **Provenance:** run read-only inside `thomas-scheduler` via stdin (Appendix). Nothing was written
  to the state directory.

Sensitivity of the episode definition (BTC, whole window / holdout = last 30%):

| TF | gap ≤ 0.5d, min ≥ 1d | gap ≤ 1d, min ≥ 3d (used) | gap ≤ 2d, min ≥ 7d |
|---|---|---|---|
| 1h (holdout 299d) | UP 36 · DOWN 29 · HV 35 | UP 10 · DOWN 7 · HV 14 | UP 6 · DOWN 5 · HV 2 |
| 4h (holdout 299d) | UP 19 · DOWN 21 · HV 18 | UP 12 · DOWN 10 · HV 8 | UP 3 · DOWN 3 · HV 2 |
| 1d (holdout 599d) | UP 16 · DOWN 15 · HV 11 | UP 11 · DOWN 10 · HV 10 | UP 8 · DOWN 4 · HV 8 |

(holdout counts; UP/DOWN = TREND_UP/TREND_DOWN, HV = HIGH_VOLATILITY)

The counts move with the definition. The conclusions below hold under all three.

## 2. The six forward verdicts

| Lineage | Verdict | Trades | Calendar | Symbol × own-TF episodes | BTC-1d episodes |
|---|---|---|---|---|---|
| `cand_843ab00a138151de6cf0` 4h breakdown_short | CONTRADICTED | 43 | 5 weeks | 15 | 4 |
| `cand_009729a48daed9d52cfc` 4h macd_cross_down | CONTRADICTED | 42 | 5 weeks | 15 | 4 |
| `cand_cb0ed2f6edf2460ab18c` 4h taker_flow_short | CONTRADICTED | 37 | 5 weeks | 16 | 5 |
| `cand_618d89cf62f75e8d6a6b` 4h funding_momentum_short | CONTRADICTED | 33 | 5 weeks | 12 | 5 |
| `cand_ef09156e6b113bb285d3` 1d trend_pullback | UNDERPOWERED | 11 | 3 weeks | 11 | 4 |
| `cand_6ca57482afb44b0c9f24` 1d trend_pullback+volatility_expansion_long | UNDERPOWERED | 11 | 4 weeks | 9 | 3 |

(the BTC-1d count includes "no episode", i.e. an UNCLEAR stretch, as one of its buckets)

- **Timing.** The trades cluster between 2026-08-30 and 2026-09-23. `cand_843ab00a138151de6cf0` opened
  7 of its 43 on a single day (08-30), across symbols.
- **What BTC-1d did in that stretch:**

  | Label | Dates |
  |---|---|
  | LOW_VOLATILITY | 08-04 → 08-20 |
  | TREND_UP | 08-21 → 08-27 |
  | HIGH_VOLATILITY | 08-28 → 09-02 |
  | TREND_UP | 09-03 → 09-08 |
  | UNCLEAR | 09-09 → 09-17 |
  | TREND_UP | 09-18 → 09-26 |

  **There was no TREND_DOWN day.** The six verdicts share the same three to five episodes.
- **Across symbols.** On the last 1,800 4h bars, 3.48 of 5 symbols carry the modal label on an average
  bar. On the last 300 1d bars, 3.47 of 5 do. A trade on a second symbol in the same week is mostly the
  same draw again.

`judge_forward`'s interval (`mean − 1.96·sd/√n`) treats 33–43 trades as that many independent draws.
The independent units behind them are closer to the episode count.

**What this does not show:** that the four shorts have an edge. The null twins keep each member's
direction and walked the same period. They are the control for exactly this confound: a short in a
tape with no downtrend loses whether its timing is good or not. They read 4h CONTRADICTED **4 real vs 2
null** of 48 lineages each. That does not resolve either way. The finding is about how few
independent draws the verdicts contain (N_eff), not an acquittal.

## 3. The judge's asymmetry

`forward_confirmation.judge_forward` (`forward_confirmation.py:279-287`):
- **CONTRADICTED and UNDERPOWERED** return at the trade floor, on the trade-level interval alone,
  **before** the slice test.
- **CONFIRMED** additionally needs ≥ `MIN_HOLDOUT_PERIODS` = 8 active slices of 14 days, which is at
  least about 112 days.

The comment that places the early return (#964) justifies it for confirmation's sake: the slice
interval is wider, so neither later branch could confirm. That is true. But under option A a
CONTRADICTED has its own consequence: it drops the member from the leaders the board offers for
promotion (#950/#951). That consequence has no time-spread requirement.

The confirming side is empirically fine. Over the last 600 days, a 112-day window holds a median of
**8** BTC-1d episodes (min 6, max 12), and **76 of 86** weekly-stepped windows contain both trend
directions.

`robustness.holdout_status` has the same shape (`robustness.py:484-495`). It matters much less there:
the holdout is 300–600 days, and the last 300 days alone hold 22 BTC-1d episodes.

**The current cost is bounded.** The verdict is recomputed from all rows on every walk, so
CONTRADICTED is not latched. The four shorts are off the screen until a downtrend episode arrives, not
retired. `first_contradicted` is stamped now (#967), so any later sequential rule would read that
stamp.

## 4. Recordable gaps

- **`regime_breadth` counts labels, not episodes.** `robustness._regime_breadth` reads
  `regimes_traded` and `profitable_regime_count`. "Profitable in three regimes" can be three labels
  inside one or two episodes.
- **No per-candidate episode count is possible today.** `factory` keeps `per_regime` totals (trades,
  R) per label on the candidate record, but not trade times. Measuring a candidate's in-sample or
  holdout episodes needs a replay.

## 5. Decision items for the next research-epoch boundary

- **E1 — time spread before a negative forward verdict.** Require the slice test (or an episode floor
  on the macro proxy) before CONTRADICTED/UNDERPOWERED is returned. Until then the record reads
  INSUFFICIENT.
  - This **loosens** the screen: fewer members leave the leaders.
  - That is why it waits for the boundary.
  - The alternative is to keep the early return and read CONTRADICTED only beside the null arm's
    same-timeframe count, which is what the board already shows.
- **E2 — record episode identity with evidence.** Stamp each replayed trade's opening episode (or bar
  time) into the candidate's `per_regime` block. Then `regime_breadth` can count episodes and this
  record's §2 can be computed per candidate.
  - It is a record-schema change, so it is new measurement machinery.
  - It waits for D3 to lift.

## Appendix — reproduction

Run from the host (read-only in the container, public data only):

```
docker exec -i -w /app thomas-scheduler python - < regime_dump.py > regimes.json
```

```python
# regime_dump.py — emits the per-bar regime label for the factory window of each series.
import json, sys, dataclasses
from runtime.mvp_runtime.crypto import market_data, features
col = market_data.select_market_data_collector()
out = {"collector": type(col).__name__, "series": {}}
for tf in ("1h", "4h", "1d"):
    limit = market_data.factory_candle_target(tf)
    for sym in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "DOGEUSDT"):
        snap = col.collect(sym, tf, limit=limit, timeout_seconds=30)
        d = dataclasses.asdict(snap)
        rows = features.build_feature_rows(d)
        key = next(k for k in ("open_time", "open_time_utc", "timestamp", "time") if k in d["candles"][0])
        out["series"][f"{sym} {tf}"] = {"limit": limit, "n": len(rows), "capped": snap.depth_capped,
            "bars": [[c[key], r.get("market_regime")] for c, r in zip(d["candles"], rows)]}
json.dump(out, sys.stdout)
```

The episode merge (gap ≤ 1d, min ≥ 3d) and the trade mapping are 40 lines of standard-library Python
over `regimes.json` and `forward_cohort_outcomes.jsonl`. Each trade takes the label and episode of
the last bar at or before its `opened_at_utc` (`bisect`).

# 2026-11-13 — the proposer prompt says what a trade costs (prompt v3)

`runtime/mvp_runtime/crypto/proposer.py` (`cost_terms`, `typical_atr_bps`, `clean_atr_bps`, `build_proposal_prompt`
v3), `pipeline_worker.py` (the `atr_bps` input), `scheduler.py` (`delegate_proposal_generation`).

**What it is for.** The model proposer picks `stop_atr` for every rule it proposes. Cost in R is fixed basis points
divided by the stop distance, so that one knob decides whether a rule can pay for itself — and the backtest and both
entry doors refuse an entry whose round trip costs more than `cost.MAX_ENTRY_COST_R` (0.25R). Until this change the
prompt said none of that. The code allows this path: a rule with a tight stop on a quiet timeframe passes the
validator, its backtest refuses every entry as uneconomic, and the trial screen turns it down as "no trades". How
often real proposals took it is not known — the proposal ledger is not readable from here. PR #1462 (prompt v2) told the model
about the pace screen and left cost out as an open item, because the per-timeframe cost figures in the records are
measurements on the store, not code.

**What the prompt now carries, and where each number comes from.**

- The round trip in basis points of notional: `cost.round_trip_cost_r` evaluated at a stop distance of the whole
  price, through `cost.cost_model_for(venue)`. Today that is 16.0 bps (taker in, taker plus slippage out).
- The same cost in R: the scheduler reads one figure off the frame the proposals will be scored on — the median of
  ATR over close, in basis points (`typical_atr_bps`) — and sends it beside the family names. The prompt states the
  cost at `stop_atr` 1.0 and 2.0, and says the figure is one symbol's recent bars while the cohort re-score sees
  other symbols and a longer history.
- The stop below which the entry door refuses a typical bar: the round trip over (0.25R x that ATR share).
- Funding: the rule (charged on notional at each settlement; a long pays a positive rate, a short earns it) and its
  size per day at the venue's base rate. The backtest charges the venue's own history; the base rate is named as such.

**What is still not in the prompt.** The measured per-timeframe cost per trade and the gross figures (holdout
results), and anything a forward walk produced. Same line as v2: the generator is told what the code does, never
what the judge has read.

**One number crosses, not the frame.** `atr_bps` is optional on the worker's wire. The worker's door refuses a value
that is not a finite number from 0.5 to 5000 rather than clipping it, and an older caller that sends none gets the
rule without the timeframe's arithmetic. A venue whose costs are not declared (`COST_MODEL_UNMEASURED`) gets no cost
paragraph at all: quoting Binance's rates for another venue would be wrong in a way the model cannot detect. The
proposal record keeps the figure as `prompt_atr_bps` (the record names a symbol, a timeframe and a candle count,
not the bars, so the number could not be rebuilt from it).

**Checked.** The stop the prompt names is where `trade_plan.entry_cost_refusal` turns: a plan one percent inside the
edge is refused and one percent outside is admitted, at three ATR levels. The figures in the prompt are compared
with the cost model's own output, not with typed constants. A whole proposer fire sends the figure and no frame.

**Not verified.** No real model has answered v2 or v3. The typical ATR here was read from the mock collector's
frames only; the real figures per timeframe are not known to this change. Whether the paragraph changes what the
model proposes is unknown until proposals from a deployed image can be compared.

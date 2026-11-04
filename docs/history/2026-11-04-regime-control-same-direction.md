# 2026-11-04 — Regime strategy set: the random control keeps each leg's direction

**What.** `regime_strategies.random_twin` now redraws only each leg's symbol. The leg keeps its own direction and
weight. Before this change the direction was a coin flip. The symbol hash is unchanged, so a rerun picks the same
random symbols as the first stage-1 run; only the signs differ.

**Why.** Stage 1 ran once (`20261104T134503Z-359740`). A separate calculation on the same screening spans gives
the equal-weight top-30 long in BULL weeks +433 bps/week (t 3.14), and the BULL candidates sit 30–140 bps above that.
A coin-flip control averages near zero, so any long strategy beat it in a rising market. With the direction kept,
the control carries the same market exposure, and "beats the random control" asks about symbol choice.

**What did not change.** The screen thresholds, the floor, the Holm family and its prior p values, and the stage-2
verdict rule. t and p are still computed on the strategy's own weekly returns, and the control is still only a sign
condition.

**When.** After stage 1 was read, before stage 2 and before the holdout was read. Thomas: "바꾸고 다시".
The module's code_digest changed, so stage 2 refuses the first stage-1 record; stage 1 reruns first.

**Test.** `test_the_random_twin_keeps_each_legs_direction_so_market_drift_cancels`: in a market where every member
moves alike, the twin earns the strategy's gross return on every seed. With the coin-flip direction restored,
this test fails.

# S1 was written without reading the schedules: the factory is mostly 1h, so a 1d tilt has to come from C3's 1h or add to it

- **What was wrong:** S1 of `docs/proposals/CRYPTO_STRATEGY_EDGE_ORDER_V0.1.md` (decided 2026-10-01) says
  "cut 4h, add 1d". The proposal never read `schedules.jsonl`. Read the same day, the factory runs seven
  daily schedules: per-symbol 1h on ETH, BNB, SOL and DOGE, plus one pooled five-symbol schedule each for
  1h, 4h and 1d. Per-symbol 4h and 1d are disabled.
- **Why it matters:** five of the seven fires are 1h. That is the output of review C3/D4 (1h pooling, the
  lever D3 exempted). 4h is already a single pooled schedule, so it has almost nothing left to cut. A
  1d tilt therefore either (a) adds 1d without cutting, for example by re-enabling per-symbol 1d, which
  raises the attempt count in 1d contexts and the mint load; or (b) converts some 1h to 1d, which shrinks
  C3's output. S1's decision text did not know about that conflict.
- **What this PR changes:** a dated correction block under S1 in §4, a pointer to it from §3's "할 일"
  line, and the forward slice duration in two places (about 99 days from the first trade, not 112;
  `forward_confirmation.py` gives that figure). The decision is unchanged: the direction stays 1d, and
  the choice between (a) and (b) is Thomas's when S1 executes. The correction recommends reading C3's 1h
  results first.
- **Found while:** cleaning up session memory, whose factory-rotation note still said "4h/1d only" from
  2026-08-06.

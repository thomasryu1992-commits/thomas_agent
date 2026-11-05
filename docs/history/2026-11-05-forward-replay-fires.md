# 2026-11-05 — Historical forward replay, part 1: the fires, the shards, the timing and the leak check

**What.** Added `scripts/research/forward_replay.py` and `scripts/research/replay_mac.sh` for
`docs/proposals/HISTORICAL_FORWARD_REPLAY_V0.1.md` (Thomas 2026-11-05).
- `simulate` fires one context daily through `factory.run_factory` unchanged. Each fire's snapshot is built the
  way the scheduler builds it, cut at the fire's instant with `lake.as_of`. Fires go into a resumable shard file
  headed by a manifest: code digest, cache digest and arm.
- `time-fires` measures one fire per context.
- `leak-check` runs the same fire on the whole cache and on a copy cut at the fire's instant.
- `compare-shards` is the Mac / server identity check.
- Freeze (`freeze_all`), scoring (`score_cohort`, `window_trades`) and the §4 verdict are here as functions. The
  `judge` command that runs them over merged shards is part 2, with arm B.

**Measured 2026-11-05, sandbox at 50% CPU and an empty store, fires at 2024-06-03:**

| Context | Seconds per fire | Peak memory |
|---|---|---|
| pooled 4h | 54 | 421MB |
| BTC 1h | 40 | 435MB |
| ETH 1h | 54 | 453MB |
| pooled 1d | 13 | 136MB |

Over the fire span (2021-12-08 → 2026-07-26, about 1,690 days) that comes to:
- the server's share (pooled 4h and 1d): about 31 hours;
- the five 1h contexts: about 110 single-process hours at the sandbox's speed, which the Mac runs in parallel.

No §5 simplification is needed.

**Leak check, same date, pooled 1d and BTC 1h:** 664,266 rows after the instant were dropped. Both caches gave
identical fires (8 candidates each).

**Fidelity choices.**
- The generation id is day × context, so a shard alone mints the ids all seven interleaved would mint.
- No trial proposals and no positioning.
- No Coinalyze rows on any machine, so the Mac and the server see the same data.
- Anchors are fixed: 55 of them, 28 days apart, from 2022-06-06. The last one leaves the 28 forward days plus a
  48-day 1d hold before the cache ends.

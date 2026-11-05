# 2026-11-05 — Historical forward replay, part 2b: the judge, and the server's run

**What.** Added `scripts/research/forward_replay_judge.py`, which reads the shards and judges §4 of
`HISTORICAL_FORWARD_REPLAY_V0.1.md`. It refuses a shard set short of seven contexts per arm, one code digest per
arm, or every fire day. It freezes at the 55 anchors and scores each window on a frame read at the window's end
plus the hold allowance. Before the verdict it requires:
- the fire leak records: arm × context × the first, middle and last anchor, identical, same digest;
- the scoring leak check: the same anchors scored on a cache cut at the window's end;
- the real-cohort sanity rule.

It also reads B against A paired by anchor.

Added `scripts/research/replay_server.sh`, the server's share, one sandbox at a time:
- the three-day pooled 4h piece the Mac also runs;
- the 42 leak checks;
- the pooled 1d and 4h shards of A, then of B.

It started on 2026-11-05 at 05:32Z, from a clean checkout of `9db7127a`.

**Why a separate module.** Every shard carries `forward_replay`'s code digest, so that file cannot change while
shards are being written. The judge imports it and adds nothing to it.

**Tests.** 7 tests, which catch 6 of 7 mutants. The survivor drops the positive-mean condition from B_BETTER. That
condition is already implied, because a one-sided p ≤ 0.05 needs a positive mean.

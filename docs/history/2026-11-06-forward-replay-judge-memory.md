# 2026-11-06 — Historical forward replay: the judge fits the sandbox

**What.** `forward_replay_judge` loaded every candidate of every shard into one list. The finished shard set is
~94,000 candidates per arm. One 1h shard alone peaked at 753 MB as dicts, so one arm is ~5 GB. The research sandbox
allows 700 MB, and the host has ~2 GB free. The fixture tests never ran at that scale.

The judge now:
- streams the shards (`shard_lines`) instead of `forward_replay.read_shard`, which reads a whole file;
- builds a *selection store* per arm:
  - A candidate id none of whose rows passes the door's per-row filters (`eligible_members([row], {})`) is kept as
    a slim row: id, time, symbol scope and timeframe, lattice size, holdout symbols, and a derivation the door
    skips.
  - Every other row drops the evidence blocks selection does not read (`SELECTION_DROPS`).
- reads the members' full rows again for scoring and the null twins (`member_rows`), the last row of each id in
  shard order, which is the full store's `latest`;
- judges one arm per process (`--arm`) and combines the per-arm reports into the verdicts (`--combine`).

**Why this is exact, not an approximation.**
- A row that fails the per-row filters is never a member, whatever else is in the store.
- It cannot be dropped, though. `rank_candidates` counts attempts per context over the whole store, and the count
  sets every other row's selection rank. So it stays, and it keeps exactly what `attempts_by_context` and
  `pooled_context_keys` read. A test calls both on full and slim rows.
- Rows of an admissible id stay whole apart from the dropped blocks. A later re-append that is not admissible
  therefore still wins latest-per-id, as it does in the full store.

**Measured, not assumed.**
- The judge's own functions on real shard pairs (A: pooled 1d + SOL 1h; B: pooled 1d + DOGE 1h) give the same
  members and the same null-twin records at all 55 anchors as the full store (265 and 295 members) [checked].
- A first drop list also removed `regime_breakdown` and `distribution_reference`. Members stayed equal, but the
  null twins changed from anchor 12 on, because `build_null_record` reads both. That is why the twins take the
  members' full rows, not the selection rows.
- A whole arm now peaks at ~390 MB to load and freeze, against 5 GB before.

**Not changed.** `forward_replay.py` and `runtime/mvp_runtime/crypto/` are untouched, so the shards' code digest
(`2285b619…`) still holds. The verdict rules are not changed either.

**Where the shards came from.** Three pooled shards (4h A, 4h B, 1d B) were computed on the Mac rather than the
server. Each server pooled 4h fire took about 80 s, against about 18 s on the Mac. The cross-check behind the
move: the server's partial pooled 4h A matched the Mac's full shard on all 952 shared days. The proposal's
"실행 기록" lists the rest.

# 2026-11-07 — Held-out forward replay: the 4h and 1d schedules on five symbols the replay never read

**What.** `HISTORICAL_FORWARD_REPLAY_V0.1` came out NOT_CONFIRMED for both arms on 2026-11-06. Exploration
afterwards proposed one variant (V2): keep only the 4h and 1d schedules. Thomas ran it against the recommendation
not to ("그래도 실행"). The test is on ADA, AVAX, LINK, LTC and DOT. The pre-registration is
`docs/proposals/HISTORICAL_FORWARD_REPLAY_HELDOUT_V0.1.md`.

Added:
- `scripts/research/forward_replay_heldout.py`: the two contexts, their fire, shards and leak check.
- `forward_replay_judge.py --design heldout`: the same judge over those shards.
- `replay_heldout_server.sh` and `replay_heldout_mac.sh`: the two machines' shares.

**Why a sibling module, not an edit of `forward_replay.py`.** Three reasons:
- The original shards carry `forward_replay`'s code digest `2285b619`, and `origin/main` still hashes to it.
- Editing the file would break that link.
- PR #1391 keeps `DATA_END` unchanged for the same reason.

The sibling re-implements only what binds the context list: `fire`, `simulate`, `leak_check`, `manifest` and
`code_digest`. Everything else is imported. Its digest covers itself, `forward_replay.py` and the crypto lane.

**Why the generation id is the replaced slot's.** When the factory passes no rotation index, the generator walks its
rotation by the generation number (`generator._rotation_offset`). It also seeds the elite split with the id. A held-out context
standing in `pooled-4h`'s slot sees the stride and ids that slot saw. A new numbering would have changed the
stride, a second difference besides the symbols.

**Why the sanity rule is gone and what stands in.** The original froze its replayed store at the three real cohort
instants and compared sizes and families. No real cohort ever held these symbols, so the rule would compare against
nothing and BLOCK every arm. What stands in: the held-out fire run on the original POOLED symbols reproduces the
original `pooled-1d` shard's rows, hash for hash, before the official run. The test pins that `run_factory`
receives the same arguments apart from the legs.

**Why the fire span stops at the last anchor.** The original's fires after 2026-07-27 served only the sanity
comparison at the 09-23, 10-01 and 11-04 freezes. Without it, nothing reads them. Stopping there also keeps every
fire inside the six reference symbols' cache, which ends 10-22/10-23.

**Tests.** 11, in `tests/test_research_forward_replay_heldout.py`:
- the five symbols are disjoint from everything the original read;
- slot ids;
- fire equivalence with the original fire, both arms;
- a missing leg cancels the fire;
- digest and spec;
- resume, and refusing an original shard;
- each design reading only its own shards;
- the two-context leak floor;
- combine without the real cohorts, with the original still needing them.

# 2026-11-16 — discovery loop round 39: how large an effect the map's two doors can see

**Shipped (registration and tool; nothing run in this commit):** `scripts/research/strategy_ladder.py`
(`door_frames`, `planted`, `door_size`, `door_reading`, command `door-size`), tests,
`docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md` §56.

**Why.** Round 38 ended on "no cell meets the second rule", and the first rule has found nothing new for many
rounds. Neither says anything until we know what size of effect each rule can see.

**Choices that are not obvious.**

- *The planted variable is built from the forward return the cell reads.* That is deliberate: it is a
  calibration object with a known relation to the outcome, read by the map's own functions on the map's own
  decisions and members. It is never registered and never printed beside a real variable; a test pins that.
  Planting the effect in prices instead would move the returns the random-variable lines were computed on, and
  then the gate against the last run could not hold.
- *Two kinds of noise.* Noise redrawn every week is the cleanest case and can never keep its rank, so it can
  never meet the full lead rule. Noise fixed per coin keeps its rank, which is what real variables do and what the
  rule asks for. The decision reads the second.
- *The size is said four ways*: the planted ρ, the mean rank correlation, the tercile spread and what a rank book
  made on it before costs. The last is laid beside the entry rule plus the median cost of the tier's weekly books
  on file (15% + 9.0%). The top tier has no cost on file and gets no such number.
- *Facts written before the run*: the real cells' per-decision noise, backed out of their t values, and the mean
  rank correlation and spread each door needs at that noise. If the planted cells are cleaner than the real ones,
  the result says so, using a real anchor (30-day funding on ranks 31–100: an 88 bp spread at t 2.27).
- *A false-alarm gate*: size 0 seen in more than 3 of 100 seeds in any combination means the planting is broken.
- *What the result decides is one thing*: whether the next round reads the tier below rank 100 with the map alone
  or with the map and a sweep together. No line, cap or closed reading moves.

## Result: the map's doors are coarser than the entry rule

One sandbox run at `b1db9846` (18 minutes); `~/research/discovery/door1/door.json`.

- *Both gates passed*: the same run's random-variable lines equal round 38's, and size 0 was seen in at most 2 of
  100 seeds in any combination.
- *The decision bracket* (ranks 31–100, weekly, noise fixed per coin, rank shape, the full lead rule): 77% of seeds
  at ρ 0.05, where a weekly rank book makes 29.9% a year before costs, and 100% at ρ 0.065 (38.5%). Both exceed
  the entry rule plus the median cost (24%). An effect exactly the size of the entry rule is seen about half the
  time. On the top 30 the bracket is 40.6–50.7%, and the 28-day cells are coarser still.
- *The second rule* (the spread's t) is nowhere finer than the first.
- *The planted cells are cleaner than real ones* (per-decision rank correlation spread 0.130 against 0.160 on ranks
  31–100), so for real variables the doors are coarser again by about a fifth.
- *What it says about earlier rounds*: the map's "no lead" on all-weeks cells means no effect of roughly 30–40% a
  year before costs in that form. Closed readings are not re-judged.
- *Next, by the registered table*: the tier below rank 100 is read with the map and a sweep together.

## Added after the result

- *The yearly figures are the planted shape's translation.* The door reads a t in units of mean rank correlation
  (0.047–0.061 for 80% of seeds on ranks 31–100, weekly). Turning that into money assumes the effect is spread
  evenly across ranks. Both real anchors sit off that translation in opposite directions: the top tier's
  volatility cells have rank t of 5.5–6.9 with spreads of 31–94 bp, where the planted rows with that t carry
  164–206 bp; 30-day funding on ranks 31–100 has an 88 bp spread with a rank t of −1.25, where the planted row
  with that spread has a median t of 4.36. So "no lead means no effect over 30–40% a year" holds for evenly spread
  effects only. The decision stands, and this supports it.
- *Short-history cells are coarser*: real all-weeks cells run 25–243 decisions, the calibration 212–243.
- *The spread door* leads the rank door on the raw shape at ranks 31–100 for ρ 0.04–0.05.
- *False alarms*: the largest over all sixteen combinations is 2%.

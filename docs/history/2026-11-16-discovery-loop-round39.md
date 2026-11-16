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

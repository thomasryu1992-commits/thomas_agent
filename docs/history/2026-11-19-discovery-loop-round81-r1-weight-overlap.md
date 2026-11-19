# 2026-11-19 — discovery loop round 81: does R1's book on ranks 101+ rebuild a struck book? (registration)

**Shipped:**
- `docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md` §98.8 and §99. §98.8 records Thomas's "아니오 (기본값)": R1 is
  closed on the two upper tiers.
- The map row and the status line.
- In `strategy_ladder`: `_beside`, `r1_lower_overlap` and the command `r1-lower-overlap --check FILE`.

No value is read in this commit. The door reads no return.

**Why first.** R1's nearest kin on ranks 101+ is a struck name (range_30, median rank correlation −0.650). §97.4
already says no book is built from an R1 lead that overlaps a struck book at 0.5 or more. Measuring that before the
map and sweep spends nothing if it closes R1.

**Choices that are not obvious.**
- *The cosine loop of round 41 was moved into `_beside` unchanged.* The existing tests pin `lower_lead_overlap`.
- *Round 41's overlap must come back first.* The market now carries the tier's sealed positioning files, which round
  41's did not. If round 41's overlap does not come back on it, R1 is not read.
- *R1's book takes no side.* A cosine's size does not depend on one book's sign, and R1 has no map lead to name a
  side.
- *The two struck positioning books are compared too,* OI growth and long-account share. Round 41 could not load
  them, and round 45 sealed them.
- *Prior.* The chance of closing is 70%. L22 weights are linear in rank, so the cosine is close to the rank
  correlation.

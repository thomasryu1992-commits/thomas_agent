# 2026-11-10 — Premium convergence screen and its 1h refresh, built before any window day

**What.** `PREMIUM_CONVERGENCE_V0.1` cleared its entry floor (#1415), and §5 orders a screen over
2027-01-01..2027-04-30 plus a 1h refresh, both before the window opens.

Added:
- `scripts/research/premium_convergence_screen.py`.
- `tests/test_research_premium_convergence_screen.py`.
- A fifth step in `scripts/research/funding_basis_refresh.py`: six symbols' `fetch_public --timeframe 1h`, paced like
  step 4.

The model and the window are untouched. The verdict reuses `funding_basis_screen.summarise`.

**Why coverage is checked before any simulation.** The model skips a missing hour while flat, and a missing
premium means "no decision". A cache that was simply not refreshed therefore reads as a quiet market and would
produce a confident `SCREEN_DROP`. The screen first requires, per symbol:
- index/mark bars and premium bars through the window's last hour;
- a funding settlement on every window day, through 16:00 of the last one.

Anything short is `NOT_YET` with dates only. Interior holes still follow the registered §2 rule: skipped while
flat, `BLOCKED` while held.

**Why funding is checked day by day.** Funding comes from the monthly archive, not from the 1h bars. A month the
refresh skipped would count as zero funding with no error, and a last-timestamp check would not see it.

**Why the refresh step lives in the funding-basis refresh.** One command per finished month keeps the pacing,
quiet-window, retry and ban rules in one place. The six symbols were already funding targets; the union is now
explicit, so a later change to the funding-basis targets cannot drop them. The premium-convergence window ends a
month later, so the command runs through `--month 2027-04`.

**Why a window without a trade is dropped.** Its mean is exactly zero, and the gate's rule is strictly above zero.
That is the expected outcome given the near-zero trade count since 2024 (#1415). A test pins it, and a `>=`
mutation is caught.

**Why "reaches the last hour" means a bar at or after it.** If the venue lacks exactly 2027-04-30 23:00 but later
bars exist, the data has arrived and the hole is interior. Demanding that one bar would pin `NOT_YET` forever.
Checked against the real archive: from each symbol's first traded day to 2026-09-30, no day lacks a settlement, so
the per-day funding rule does not strand the May run.

**A stated default §5 left open.** A position open at the window's last close is marked there, with no exit
cost. Both the entry run and `funding_basis.simulate_symbol` do the same.

**The entry run's input digest no longer reproduces from the live cache.** The digest hashes every 1h bar, and the
refresh extends them; the sandbox reads the live research cache, not a snapshot of it. The digest recorded at
registration (`f532be5755ad3080`, from the load step) records the cache as it stood on 2026-11-10 and cannot be re-derived after this refresh. The
funding-basis digests behave the same way once their monthly refresh extends the 1d bars.

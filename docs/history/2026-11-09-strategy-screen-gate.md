# 2026-11-09 — The verdict gate becomes a four-month screen; funding basis is its first candidate

**What.** On 2026-11-08 Thomas set a gate: build a hypothesis only if its forward verdict takes under a month.
The funding basis, the most market-neutral structure the cache can test, failed it (`FUNDING_BASIS_V0.1`,
GATE_FAIL, monthly Sharpe 0.40–0.44 against the 2.49 a month needs). On 2026-11-09 Thomas replaced it with a
two-stage gate (`docs/proposals/STRATEGY_SCREEN_GATE_V0.1.md`):
- A four-month forward screen keeps a strategy whose mean daily return is above zero.
- The existing confirmation rules stay as the second stage.

Added:
- `scripts/research/funding_basis_screen.py`: the screen for the funding basis over 2026-12-01..2027-03-31.
- `tests/test_research_funding_basis_screen.py`.

**Why the screen is a sign test.** A verdict's length is set by the strategy's Sharpe, not by the gate. Only
error tolerance moves it. "Mean above zero" is one-sided α 0.5. It keeps 80% of true annual-Sharpe-1.5
strategies in four months and drops half of the null ones. The point is to drop dead hypotheses early. Keeping
one means only that it goes on to the second stage.

**Why it prints nothing before the window is covered.** `SEQUENTIAL_FORWARD_TEST_V0.1` (2026-09-24) rejected
early rejection and early confirmation. Its error analysis was about reading a fixed verdict repeatedly. The
screen has one reading. Until every symbol's bars and settlements cover the window, the command reports
`NOT_YET` and how far the data reaches, with no returns, so there is nothing to read early.

**Why the window starts on 2026-12-01.** The model reads monthly funding archives. Whole calendar months avoid
needing the daily archive for the rest of November. The window opens after the pre-registration merged, so no
day of it was seen.

**Why the screen reuses `funding_basis.simulate_symbol` unchanged.** The hypothesis that cleared the entry floor
(annual Sharpe ≥ 1.0 on 2020–2026) is that exact model. A screen of a different model would test something that
never cleared the floor.

**Why `funding_basis_screen.py` is not in the crypto script roster.** The roster lists scripts that import the
crypto lane. The screen reaches it only through `funding_basis`, which is already listed.

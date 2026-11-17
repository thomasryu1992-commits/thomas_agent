# 2026-11-17 — discovery loop round 52: L24, a combination read beside the same search on noise

**Shipped (registration and tool; nothing read in this commit):** `scripts/research/search_prune.py`,
`tests/test_research_search_prune.py`, `docs/proposals/SEARCH_THEN_PRUNE_V0.1.md` (DECIDED: Thomas's S1–S5
"기본값대로"), `docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md` §70.

**Why.** Thomas asked for a search that raises the Sharpe by combining patterns, with the overfitting pruned after.
Every single-variable book stayed under the entry rule; the loop had no logic that combines weak readings.

**Choices that are not obvious.**

- *The search is its own null.* The same search, every configuration, on random variables drawn to the material's
  size, 200 times. Choosing components and signs is paid for there. After fifty rounds over the same variables no
  honest count of tries exists, so the deflated Sharpe and PBO are printed and never gate.
- *Each traded year learns only from weeks that end before it*, the ladder's `TRANSFER_SPLITS` and its rule for a
  learned sign. Each tier's book is read by that tier's own rule: the top tier over five years with 2020–2021 flat,
  the others over their own span.
- *Fresh noise every repeat, of the tier's own null class.* The draft drew each repeat from the map's 114 controls:
  repeats shared variables, and the weekly half paid more cost than any real score, so the null sat low. Each repeat
  now makes its own variables, half weekly and half fixed on the top tier and fixed below it, as the tier sweeps'
  nulls are; both searches print turnover.
- *Values are tabulated group by group* and the search reads only bars, funding and those tables, because every
  group at once does not fit the sandbox. The members in every file and in the search must agree, or it stops.
- *The name.* L8 already names the risk-matched book, so the logic is L24.
- *The real search is locked in code* (`REAL_SEARCH_DECIDED`, a recorded noise run of the same tier, size and
  material).

## Result (2026-11-17 05:29Z)

The noise searches (200 per tier, fresh variables) put the best of the 18 configurations at a mean of 0.766, a 95th
percentile of 1.250 and a maximum of 1.75. The real search's best was the top tier's |t| ≥ 2 configuration at an
annual Sharpe of 1.066; 38 of the 200 noise bests reach it, so p = 0.194 and L24 is closed by its first rule. The
other two tiers were negative in every configuration. Shown and not gates: deflated Sharpe 0.033, PBO 0.7 / 0.6 / 0.3.

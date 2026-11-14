# 2026-11-14 — discovery loop round 35: the premium from the archive, read again

**Shipped (registration and tools; nothing fetched or run in this commit):** `scripts/binance_archive.py` (the
monthly kind `premiumIndexKlines-1d`), `scripts/research/signal_lab.py` (`read_premium` reads the archive;
`read_premium_api`; `premium_sources` and the `premium-sources` command),
`docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md` §51 and a correction to §50.

**Why.** The premium ranked 44.5 coins a decision on ranks 31–100 where bar variables rank 53.3. The files had come
from the venue's API for the symbols an earlier search asked for: coins that were at some month in the top by
volume. §50 first wrote "the API serves listed symbols only" as confirmed; comparing names showed that was wrong
(12 of the 267 are no longer listed and 203 of the 221 missing ones are), and the registration says so. Either way
the tier was read on coins chosen with later knowledge. The archive holds the same series for every symbol.

**Choices that are not obvious.**

- *One source, the same rule for every coin.* The map now reads the premium from the archive only. A day the
  archive lacks is not filled from the API file, which would give some coins more history than others.
- *A pilot before registering.* One month of one symbol was fetched into memory and laid beside the API file: same
  layout as klines, thirty equal closes.
- *Three checks on inputs before any return, each with its outcome written beforehand*: the two sources' closes must
  be equal wherever both exist; coverage by tier before and after; and round 26's overlap rule on the full set
  (0.72 was measured on the subset). If the overlap is 0.8 or more the premium stays registered but its cells are
  funding read again and cannot be leads.
- *What is re-read and what is carried.* The six premium variables are re-read on the map, in the sweep and in the
  per-variable axis check. The other books, the 600 null books and the other seven groups are carried by file.
  §44's lead and D10 are not re-judged.
- *A side fact for Thomas, nothing touched*: the funding-basis and premium-convergence screens read the API files;
  their symbols are their own lists.

## Input checks (23:37Z; no return read yet)

**Fetched:** 9,594 files in the first pass, 0 in the second, 0 failed; 393 symbol folders, 40M.

- *The value gate passed*: 213 symbols in both sources, 211,450 days compared, 0 differ.
- *Filled*: members with a value per weekly decision went 26.9 → 27.2 on the top 30 and 44.7 → 53.4 on ranks
  31–100 (bars: 27.2, 53.3). Counted on the host with no return condition.
- *Overlap on the full set*: 0.7286 with 30-day funding on the top 30, under 0.8. The premium stays its own variable.
- *689 days only the API files have* are holes in the archive's own files (raw files opened: BTCUSDT 2021-07 has 26
  rows), most of them five days of July 2021 missing for every symbol. They empty 0 cells in five of the six
  variables and 4 in the position variable; a window over a hole averages fewer days.
- *A side fact, recorded and not fixed*: dated quarterly contracts sit among ranks 31–100 in 18 of 3,236
  member-months, because a symbol absent from the contract-type snapshot defaults to a perpetual and the snapshot
  holds no expired name. 47 weekly cells rank one on bar variables (0.34%), none on the top 30. They have no
  premium in either source, so this round's comparison does not see them. Changing the default would move tier
  membership under every reproduce-exactly gate and re-measure closed hypotheses on another universe, so it is a
  gap on record: a tool round of its own if taken up.

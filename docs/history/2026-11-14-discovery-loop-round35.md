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

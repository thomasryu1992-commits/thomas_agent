# 2026-11-09 — One command for the funding-basis screens' monthly data

**What.** `scripts/research/funding_basis_refresh.py --month YYYY-MM` runs, for a finished month, every read the
two screens need. The screens are v0.1's eleven symbols and the v0.2.1 top-30 universe. The reads, in order:
- the symbol lists;
- every perp's monthly 1d klines;
- the spot pairs' klines;
- the targets' funding archive and derivative series.

It adds no network read: each request goes through `binance_archive.py` or `fetch_public`, which keep their own
rules. Thomas asked for it on 2026-11-09 ("둘다 순서대로 진행").

**Why these rules.** Each comes from a fault in the v0.2 fetch on 2026-11-09.
- Requests in a burst hit the IP's minute weight; production saw none of it. So: one symbol every 20 s, wait 70 s
  on a weight stop, and stop the whole run on a 429 or 418.
- A probe fetched only the index, and MATIC nearly lost 38 months. So: one `fetch_public` call per symbol with
  every derivative kind.
- A list file without a trailing newline dropped ZRX. So: symbol lists go as arguments, never through a file.

**Why the targets are a superset.** Membership needs the spot archive and is decided by the screen in the
sandbox. Here only the volume ranking is computed. The top 30 by volume in month M and in M − 1 contain next
month's and this month's members. The ranking reads public klines only, never a return.

**Why a failing symbol does not stop the run.** A delisted perp can answer 400 (SXP did). Stopping there would
leave every later symbol unfetched. The failure is listed, the exit code is BLOCKED, and a rerun skips what is
already in.

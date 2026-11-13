# 2026-11-13 — netting stage 1: a net book computed from the pool's forward record, not written by the runtime

`scripts/research/pool_netting.py`, `docs/proposals/NETTING_STAGE1_DERIVED_BOOK_V0.1.md`.

**What it is for.** Thomas (2026-11-12): do not cap the number of strategies; settle what many strategies bring. The
design (`SAME_SYMBOL_NETTING_AND_RISK_BUDGET_V0.1`) answers with one net position per symbol and a risk budget per
bet. Its stage 1 was "a paper-only netting book the live path never reads".

**Why it is a computation and not a store.** The pool's per-strategy forward book already holds every virtual
position since 2026-07-28. Giving each one a size and a bet and summing per symbol needs no new writer: it can be
recomputed on every read. Nothing is stored, so there is nothing a live path could read, and three and a half
months of record were usable on the first day instead of after months of a new store filling.

**What had to be opened to do it.** Two more fields of the forward rows: `entry_price` and `risk` (Thomas
2026-11-13, S2). Only their ratio is used — the notional per 1R — and the loader keeps the ratio, not the two
numbers. Results, exit prices, costs and close reasons are still dropped at parse time; the cohort is not read.

**The design rule that was two rules.** "A strategy joining an open bet shares its budget, and open positions are
not reduced for a new signal" cannot both hold with a bet fixed at 1R. So the tool computes both readings and picks
neither: `v1` keeps every bet at 1R by resizing the others whenever one comes or goes; `v2` fixes a strategy's share
at entry and lets the bet pass 1R. `v0` is today's 1R per strategy.

**A definition corrected before the run.** The first draft counted "netted volume" as entries and exits that met an
open position of the other direction. That is wrong: a long opened at one time and a short opened later are still
four orders. Volume is netted only where opposite-signed changes share a timestamp; overlapping opposite positions
lower the net notional and nothing else. The proposal and the design doc's own sentence were corrected in #1464
before anything was computed.

**What the one run said** (lake `20261113T021701Z`, 327 positions, 18 strategies; the figures are in the proposal).
The bet budget is what lowers exposure — the largest open risk falls from 15R to 9–9.5R — while netting itself takes
about 7% off the mean notional. Two of the four expectations written before the run were wrong: netted volume was
13–15%, not under 5% (strategies stepped in one cycle hand over at the same instant more often than assumed), and
the net was not clearly smaller than the gross. At 1% risk on 1,000 USDT nearly every slice is several times the
registered 60 USDT order limit, so today that limit binds before any risk budget would.

**Checked.** The tests use built rows only: no result reaches a report, a bet under `v1` is always 1R, `v2` never
resizes, overlapping opposite positions net no volume, and a close and an open at one instant do. A planted fault in
the net change failed three tests. After the run, turnover and netted volume under `v0` were recounted by a
different method through the tool's own loader and matched to four decimals; the peak of 15 open positions and of 9
symbol-direction groups equals what the overlap measurement counted on 2026-11-12.

**Not done.** No judgement, no P&L, no runtime change. Protective orders, reconciliation and the order of partial
offsets (the design's open problems) are untouched. Which split the design adopts is asked there, not decided here.

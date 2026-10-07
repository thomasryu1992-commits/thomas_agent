# Total asset allocation: gold is held as an ETF at Toss, because Toss Securities offers no KRX gold account

- **What this PR changes:** `docs/proposals/TOTAL_ASSET_ALLOCATION_V0.1.md`. The neutral mix's gold sleeve
  (13%) moves from KRX spot gold to a gold ETF bought at Toss Securities (Thomas 2026-10-07). The §8.1
  Toss row and the status line record why; `STATUS.md` is regenerated. No code, no schema, no constant.
- **Why:** Q3 kept Toss as the only brokerage. A web check on 2026-10-07 found no source saying Toss
  Securities offers a KRX gold account. Toss's spot-gold offers have always been partner accounts:
  Korea Investment & Securities via the Toss app (2025-06), and NH Investment & Securities behind Toss
  Bank's "금 모으기" (2026-04). The official Toss Securities FAQ could not be read, so the finding rests on
  public sources only.
- **Cost:** ETF gains are taxed at 15.4%, a drag of 0.46%p on the sleeve, or about 0.06%p/yr on the
  portfolio (roughly 60,000 KRW per 100M KRW). §8.3's Toss-only figure of 0.38%p already assumed this.
- **Placement:** pension-savings room goes to global equities first. Gold may go in the non-credited part
  of the pension account (0.16 drag) if room remains.
- **Rebalancing:** gold ETF sales are taxable, so gold is topped up with contributions first.
- **Not used:** the Toss Bank–NH spot-gold account, although it is tax-exempt. It is another brokerage's
  account and the holdings board cannot read it.

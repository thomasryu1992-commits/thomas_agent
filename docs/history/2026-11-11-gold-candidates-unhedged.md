# Gold candidates are picked unhedged

- **Why:** reviewing the rebalancing strategy on 2026-11-11 found that the automatic candidate pick (RB2'') let a
  hedged gold ETF through.
  - The gold rule matched `금선물` and never filtered `(H)`, and the pick ranks by trading value only.
  - The allocation counts gold as dollar exposure (`allocation.CURRENCY_DEFAULT`, D-C). The validation measured
    gold in won terms, dollar gold times the rate.
  - A hedged gold ETF earns dollar gold plus the Korea–US rate gap (the validation's hedged formula) and loses the
    won-weakness cover.
  - The US Treasury quarter already had `unhedged_only`; gold did not.
  - Thomas: "금 환헤지 제외 진행". Record: `REBALANCING_PROPOSAL_V0.1.md`, the RB2'' decision section.
- **Change:**
  - `candidates.auto_picks` takes gold with `unhedged_only=True`. The report still lists a hedged gold ETF, marked
    `fx hedged` with a note. An unhedged gold ETF is marked `fx USD`.
  - `candidates.RULES_VERSION` (2) is written into the automatic pick file. A file written under another version
    is screened again once at the next fire (trigger `rules`, notice label "선정 규칙 변경"), so a change of
    rules reaches the pick before the next month.
- **Not changed:**
  - Thomas's manual pick (`--approve-candidates`) still wins its class.
  - A gold holding already bought is neither sold nor reclassified. Its currency entry is Thomas's (`gold:krw` for
    a hedged one).
  - When no unhedged gold ETF trades, the class keeps its last pick (the existing rule for a class the screen finds
    nothing for).
- **Checked:**
  - The pick takes the unhedged ETF even when the hedged one trades more.
  - With only a hedged ETF, gold gets no new pick.
  - An older file is screened again exactly once, its hedged gold pick is replaced, and the notice says why.
  - Six mutations were each caught.

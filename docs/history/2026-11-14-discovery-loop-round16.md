# 2026-11-14 — discovery loop round 16: the funding tail held with its own spot

**Shipped (registration and tools; nothing run in this commit):** `scripts/research/strategy_ladder.py`
(`paid_over_the_middle`, `tail_of`, `BasisTail`, `pair_basket`, `basis_book`, `pair_unit`, `simulate_pairs`,
`basis_counts`, `basis_run`; the commands `basis-counts`, `basis`),
`docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md` §30.

**Why now.** Thomas 2026-11-14: "#1470도 병합하고 다음으로 넘어가자". #1470 was merged (`0e3eab7e`). The second
clause is read as the go-ahead for the next round, and the one next round the document offered was D9 (widen the
funding basis to a per-coin tail, default yes). That is a reading, not his "yes" to D9, and the registration says so.

**Choices that are not obvious.**

- *The tail is a rank, not a level.* H-F2 picked on an absolute level (+1.0% a week), stopped at its counts, and its
  rule forbids counting again at another level. Here a coin is picked by being in the pool's top tenth, strictly
  above the pool's median and above zero. The counts tally nothing against a funding level either.
- *Strict comparisons, and a tie at the tenth's edge is left out whole.* Much of a pool can sit at one default
  rate. "At or above the median" would keep every such coin for ever, and a boundary inside a tie would be broken
  by name order. The counts report how often either happens.
- *One hold rule, chosen after seeing H-F2's spell count.* A plain weekly top tenth turns over every week and a
  half; at 0.57% of total position a round trip that is about 20% a year in costs and the answer is arithmetic. A
  coin therefore stays while it is paid more than the typical pool coin. The registration says the rule was chosen
  with that count in hand, and the plain weekly book is never run afterwards.
- *What is held depends on the week before*, so the held sets are built once, in order, and a placebo's spell is
  keyed off them. L15's stateless `picks` / `spell_start` could not carry this.
- *A new accounting, not the v0.2 simulator.* The ladder judges on total position through `measure`; the v0.2 code
  reads index, premium and mark bars that exist for 255 symbols only. `simulate_pairs` is the held accounting on
  two legs, reading the spot and perp daily bars H-G1 already read. The weekly re-mark stands where v0.1 reset a
  pair at 1.5 times its entry; v0.1's liquidation rule (1.95 times) is kept, on the perp's last-price high.
- *The same-asset guard holds during the hold, and it is one-sided.* v0.2 needed a known-result re-run because a
  spliced series (LUNA) printed a move no pair can have. Here a held pair whose closes come apart by more than the
  spot-share search's band is closed that day; a gain printed that day is not counted, a loss is, and every such
  coin-day is listed. A splice can fail the book but cannot pass it.
- *No matched placebo.* L15's second placebo matched on the trailing move, to tell the signal from "beaten-down
  coins". A pair has no price direction to match on. The one placebo says only whether the tail's funding outlasts
  the entry week; size is the floor's job.
- *A label instead of a stop for "not a tail".* The 26-decision stop is vacuous for a rank book. If the book holds a
  quarter of the pool or more on average, the result is labelled an upper-half carry book; the verdict is not
  changed and no narrower rule is run.
- *Not a re-judgement of FUNDING_BASIS_V0.2.* B-POS's verdict, its forward screen and its script are untouched. A
  pass here buys no confirm read: that span was read by B-POS.

**Results (same day).**

- *D9 was answered before the counts.* To "I read 'next' as D9; measure it as is?" Thomas said "그대로 진행", and
  later "결과 나오면 #1473도 병합".
- *The counts did not stop the book.* A pair at 49 to 53 decisions in each later year, 17% of the pool held (under
  the 25% label), spells of three weeks. The refilled size line already said costs eat the tail's funding after
  2021: the held coins' trailing funding was 0.16% to 0.44% a week in 2022-2024 against a round trip of 1.14% of a
  leg.
- *H-B1 failed every rung and is closed.* Annual Sharpe -0.353 net over five years, -1.8% of total position a year,
  all three later years negative, p 0.196 against the same book on other pool coins. Funding received 10.4% a year,
  costs 9.3%, price -2.9%.
- *The price loss is the margin rule's days, and they do not decide the verdict.* Thirty-two days on which a perp's
  high reached 1.95 times the mark it was held from, most of the loss on coins that ran several-fold and fell back
  the same day (UNFI, TRB): the short is gone at the high and the spot is unhedged to the close. Without those days
  the book would be about +1.2% a year, still far under the floor. That figure is arithmetic on the output, not a
  second judgement.
- *The placebo line in the registration was wrong.* It said rung 2's placebo condition would be easy to pass
  because funding persists. It was not passed: 58 of 300 other-coin pair books did as well.
- *The measurement ran from the counts commit before that commit's checks had finished* (they passed while it
  ran). The code is byte-identical to the registration commit's, whose checks had passed; the registered order said
  "commit, then measure" and named no wait.

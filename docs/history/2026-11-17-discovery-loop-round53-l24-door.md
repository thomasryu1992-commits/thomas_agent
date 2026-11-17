# 2026-11-17 — discovery loop round 53: how large an effect L24's filter lets through

**Shipped (registration and tool; nothing read in this commit):** `scripts/research/search_prune.py` (`plant`,
`plant-reading`), `tests/test_research_search_prune.py`, `docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md` §71.

**Why.** Round 52 closed L24: its best (top 30, Sharpe 1.066) was matched by 38 of 200 searches on noise. That says
nothing about how large an effect the filter could have seen. Thomas chose to measure it next ("문턱 재기").

**Choices that are not obvious.**

- *Top 30 only.* Both the real best and the noise that set the cut sit there. The other tiers would first need the
  table-path gate round 52 promised; this round reads every book from its functions, so that gate is not spent.
- *Size 0 is the seed's noise search, exactly.* A planted variable replaces one of the pool's fixed noise variables
  and takes that variable's own draw as its noise (a monotone map to a normal score), so at size 0 it ranks as the
  variable it replaced, whatever the count.
- *K = 5 is the best case for combining.* The five share one signal and have independent noise; real kin share noise
  too. Its bracket is a floor.
- *The book holds four weeks; the planted signal is one week.* The bracket is therefore larger than rounds 39 and
  42's for the same rank correlation. Each size also prints one planted variable's own book, in the units the
  recorded single-variable books use.
- *The p is `reading`'s:* the seed's top-tier best against the recorded 200 bests over every tier, same tie rule.

**Result (2026-11-17 12:50Z, recorded in §71.5).** Both gates held: the recorded noise repeats 0–2 replayed exactly,
and 1 of 50 seeds passed at size 0. No size reached 80% at either count. At ρ 0.10, 17 of 50 seeds passed with five
planted variables and 6 of 50 with one. The planted variable was still picked in all three years in most seeds (100%
from ρ 0.05 with five). The registered reading is the third row: round 52's miss says nothing about effects below a
rank correlation of 0.10. The single-book translation is a level, not a lift, because the size-0 single book was not
measured. Door 1 covers the first lake snapshot only, since the judgement fingerprint hashes rules, not data.

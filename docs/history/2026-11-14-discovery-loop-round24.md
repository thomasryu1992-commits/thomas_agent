# 2026-11-14 — discovery loop round 24: the last axis on the list, leverage

**Shipped (registration and tools; nothing run in this commit):** `scripts/research/signal_lab.py` (the axis
`leverage` in `scenario_labels`, added to `CANDIDATE_AXES`), `docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md` §38.

**Choices that are not obvious.**

- *Open interest is multiplied by the day's close.* The positioning files give contracts. Dividing contracts by
  quote volume would mix units across coins and the tier's median would mean nothing. A test changes only the
  price and the label follows.
- *The check differs from the other eleven in two stated ways.* It runs with the positioning values loaded, so the
  variables it reads include the positioning ones, and its labels begin in mid-2022. The registration says both
  before the run, and that no other axis's row in that output is a re-judgement.
- *Seventh in the order, so 0.05 / 7.* With 200 placebos that means none may separate as much.
- *Without positioning values the axis has no label*, so every other run is unaffected; the label-reading book is
  re-run all the same, since the labelling code changed.

**Result (2026-11-14 15:35Z).** Not woken: separation 0.00669 against a placebo mean of 0.00725, 107 of 200 shuffled
copies at or above it (p 0.537, level 0.0071). 133 of 260 decisions carry a label. Twelve axes have now been checked
and none beats its placebos.

- *The registration's last sentence was too wide, and the result says so.* It said nothing would be left on §3.1
  and §3.2. What ended is the shape list and the list of new-axis candidates. §3.2 has two more monthly rows,
  splitting an axis finer and merging cells, and no round had run either; the earlier rounds named "a finer split"
  as unread and Thomas's own words ask for it. The result carries a row-by-row table of §3 and names the finer
  split as the next round.

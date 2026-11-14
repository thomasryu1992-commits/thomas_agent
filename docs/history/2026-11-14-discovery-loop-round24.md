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

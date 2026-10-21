# Evidence standing: one authority for whether a row's numbers may be used (E1)

- **Why:** the OI re-score came back VANISHED (`2026-10-21-oi-lookahead-4h-vanished.md`), and the flagged rows still
  steered the search. The flag capped only the verdict and the LIVE door, so parents, search centres, the ranking
  and the observation entry went on reading the contaminated numbers. Thomas rejected the per-consumer
  choices offered (block all three / parents only / leave it) as patching, so the fix is structural.
- **What:** `candidate_ranking.evidence_standing(record)` returns `{"invalid": reasons, "biased": reasons}`.
  - Each reason's kind is declared once, in `EVIDENCE_REASON_KINDS`:
    - `oi_lookahead` is INVALID;
    - `holdout_reused` is BIASED;
    - `oi_lookahead_rescore` is BIASED. It is keyed on `rescore_reason == OI_LOOKAHEAD_RESCORE_REASON`, the
      field the E2 re-score will write. A record can only withhold with it.
  - What INVALID does at each consumer:
    - `factory.rank_fusion_parents`: not a parent;
    - `generator` elite centres: not a centre, since `champion_score` was computed wrong too;
    - `rank_candidates`: sorts after every valid row, ahead of the cost-basis key;
    - `assert_observation_entry_bar`: a new term, `evidence=INVALID(...)`.
  - Both kinds cap a holdout CONFIRMED at UNDERPOWERED in `candidate_quality`, as the two flags did. Its output
    adds `evidence_invalid` and `evidence_biased`.
  - Occupants of observation slots are not evicted.
- **Decision 2:** `OI_FIX_EFFECTIVE_AT` joins `judgement_fingerprint`. It sits in `NOT_INDEXED` because
  `tunables` indexes numeric scalars and this is a timestamp. The fingerprint changes once at the next deploy.
- **Checked:** tests for kinds and cap, rank order, parents, centres and observation entry. Each consumer test
  flips with `OI_FIX_EFFECTIVE_AT`, so it shows the standing, not the setup, decides. Six mutations each
  fail a test: the parent, centre, rank and admission lines, the re-score reason, and the BIASED cap.

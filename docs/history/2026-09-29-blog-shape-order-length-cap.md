# 2026-09-29 — Blog draft: prose last in the shape, and the plan said by its cap

Two patterns from the day's manual runs:

- **Stopping after the prose.** Twice (`bcp_cbbabb9953f3c67ba332`, `bcp_29c26be0c982a4d30646`)
  the model ended its draft right after the last section, with `finish_reason` STOP at about 3,000
  output tokens. It never wrote the table, tags, capture directions, fact checks or sources that
  the shape put after `sections`. `_DRAFT_SHAPE` now follows `DRAFT_KEY_ORDER`: titles, tags,
  capture directions, table, sources and fact checks first, `intro` and `sections` last. Both
  requests say so. The revision is shown the previous draft re-serialized in that order.
  Volatile claims in the prose are still detected by the lane itself, so fact checks written
  before the prose lose no coverage.
- **Reading the floor as the aim.** Paragraph averages were 169, 153, 136, 158, 174 and 184
  against the 120~140 plan. The plan's last words were "각 문단이 120자 이상인지 세어 보고, 짧은
  문단에는 문장을 더 붙여라", and one draft recorded "각 문단 120자 이상 준수" in its findings. The
  plan now leads with "130자 안팎(120~140자)이고 140자를 넘기지 마라". It states the 150 average
  that fails, and the self-check runs both ways: over 140 drops a sentence, under 120 adds one.

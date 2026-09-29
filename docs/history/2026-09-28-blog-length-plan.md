# The blog request states a length plan that adds up, instead of three totals that did not

- **What was measured:** the lane's first package (`bcp_ae0b4edc2b8c4477cba3`, 2026-09-28) had 10
  paragraphs averaging 73 characters: 730 in total against the 1,800 floor. Groq wrote it with
  `gpt-oss-120b`, while openrouter was at 429 and google at 503.
- **Why that was the request's fault as much as the model's:**
  - The request asked for three totals at once: "문단 10~20개·문단당 70~150자" and "합계
    1,800~3,500자".
  - Those agree only well above their minimums. Ten paragraphs at the 150 ceiling is 1,500.
  - The draft met the two minimums it could satisfy literally, and so it could never reach the
    third.
- **What changed:**
  - `content_request` states one plan: intro 2 paragraphs + 5 sections × 3 paragraphs = 17
    paragraphs, each 3~4 sentences and 110~140 visible characters, about 1,870~2,380 in total.
  - It says outright that under 1,800 fails, forbids one- or two-sentence paragraphs, and asks the
    model to count before answering.
  - A revision for `body_chars` or `para_chars` restates the plan against the draft's own
    measurement: paragraphs, average and total.
  - The plan's arithmetic is tested against `blog_draft_score.STANDARDS`, so a moved standard
    breaks the test rather than the plan.
- **Not changed:** the standards themselves and the provider chain. This does not guarantee
  length. A model can still ignore a plan, which is what the one revision and `needs_edit` are
  for. The next fire is the measurement.

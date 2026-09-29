# The blog length plan, second round: a higher paragraph floor, a sample paragraph, and the short paragraphs named

- **What was measured:** after the first length plan (#1020, 110~140 characters per paragraph),
  three drafts kept the planned 17 paragraphs and missed the planned length:

  | Draft | Model | Paragraph average | Body |
  |---|---|---|---|
  | 1 | groq | 62 | 1,058 |
  | 2 | Google | 76 | 1,305 |
  | 3 | Google, the adopted revision | 93 | 1,596 |

  Every draft landed below the floor it was given.
- **What changed:**
  - The plan's floor rises to 120~150 characters per paragraph, four sentences each, which puts
    17 paragraphs at 2,040~2,550 characters. A floor is where a model's paragraphs start, not
    where they land.
  - The request shows one paragraph of the planned length (133 characters of generic procedure
    prose) as a length reference, and says not to reuse its content. It also asks the model to
    add sentences to any paragraph that falls short.
  - A length revision names each short paragraph with its current length, for example
    "섹션 2의 문단 1(현재 71자)". Table rows are excluded, and the list is capped at 12. "Make
    paragraphs longer" moved the average by about 15 characters a round; pointing at the exact
    paragraph is the concrete version.
- **Unchanged:** the standards (`blog_draft_standards.2026-09-28`). The plan's arithmetic is still
  tested against them, and the example paragraph is tested to be of the planned length.

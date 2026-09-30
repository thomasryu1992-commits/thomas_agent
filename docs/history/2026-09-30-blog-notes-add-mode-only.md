# 2026-09-30 — Blog revision: evidence notes only when the revision grows the draft

#1070 sent the evidence notes with every revision. On candidate-1070 '챗gpt 무료체험'
(`bcp_ea9ee56c8053fe3810ea`) the first draft was long (2,732 characters, 160 a paragraph) and
missed the keyword floor. Its revision was asked to cut and to place the keyword. It still
carried about 2,000 characters of notes, came to 16,553 tokens against the 16,000 budget, and
was blocked (`REVISION_BLOCKED:TOKEN_BUDGET_EXCEEDED`). Revisions had run 10k~14.8k tokens
without notes.

The notes serve one ask: adding length with specifics. `_grows(first)` is true only when a
length standard failed and the paragraph average is not over the ceiling, which is
`_length_asks`'s add branch. Only then does the request carry the notes and the "new facts only
from the notes" line. A cut, a keyword fix or a structure fix gets the old "새 사실이나 새 출처를
추가하지 마라" and no notes.

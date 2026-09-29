# 2026-09-29 — Blog revision: over-long paragraphs are named and cut, not lengthened

`bcp_5f459c95cf51fa821f6d` ('배너제작') ended `needs_edit` on `para_chars`. All 17 first-draft
paragraphs were over 150 (average 168), and the revision came back at 169. The length ask
named only paragraphs below the plan's floor (there were none), and it repeated the plan, which
ends "짧은 문단에는 문장을 더 붙여라". The generic `para_chars` ask said "긴 문단은 나누고".

`_length_asks` now reads the direction from the average. Over the standard's ceiling (150), it
asks to keep the paragraph count and cut each paragraph to the plan's 120~140 by removing
overlap, never facts, figures or the keyword. It says not to add sentences, and names each
paragraph over 140 ("섹션 2의 문단 1(현재 171자)"), capped like the short list. The lengthening
plan is not sent in that case. Under the ceiling, the plan and the short-paragraph list are sent
as before. The generic `para_chars` ask no longer says to split.

# 2026-09-30 — Blog revision: evidence notes, and a carrier's offer named

Scored on candidate-1066 (퍼플렉시티 무료 ≈55, 포토샵 누끼따기 ≈55, 구글 제미나이 사용법 ≈58),
two things from #1066 and #1063 did not hold up.

**The revision had nothing to add.** The revision run has no evidence blocks. The request says
so ("이 수정 실행에는 근거 블록이 없다"), and `_revision_previous` strips the draft's `[S#]`.
#1066's `ADD_SUBSTANCE_ASK` still told it to grow paragraphs with "근거 블록([S#])의 수치". On
'포토샵 누끼따기' the revision grew only the two intro paragraphs, each by one closer, and
stopped at 1,706 of 1,800.

The revision request now carries **evidence notes**: `_evidence_notes` lists the content run's
web hits as plain "title: snippet" lines, with no `[S#]` and no URL.

- **Which hits:** those the first draft cited when it cited any, because those are the sources
  the package carries (`_carry_first_evidence`). Otherwise every real hit; mock rows are left
  out.
- **Size:** capped at 400 characters per note and 2,000 in all. On the scored drafts the
  request is 6,000–8,000 characters, under the 19,000 skip line.
- **Wording:** new facts may come only from the notes, and names or numbers not in them are
  not to be invented. `ADD_SUBSTANCE_ASK` points at the notes.

**A carrier's offer is named (Thomas 2026-09-30).** The business-name ban turned an SKT
customers' offer (Perplexity Pro free for a year) into "특정 통신사 이용자라면", which no reader
can act on. `VENDOR_NAME_ASK` now asks for the carrier's name on a carrier-partnership benefit.
Printers, shops and marketplaces stay anonymous.

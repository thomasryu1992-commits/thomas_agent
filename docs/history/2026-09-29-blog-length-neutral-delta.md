# 2026-09-29 — Blog length: the plan said evenly, and each named paragraph says how much

- **The cap said first overshot the other way.** Before #1036 the drafts ran long (paragraph
  averages 136~184). With "140자를 넘기지 마라" at the front, the next two ran short (82 and 81).
  The plan now states the target and range evenly: "130자 안팎(120~140자)", then the two fail lines
  together ("평균이 150자를 넘거나 합계가 1,800자에 못 미치면 불합격"). Its self-check aims both
  ways at the target, not at one sentence.
- **"Add a sentence or two" was too little.** In `bcp_dad48bf154c090f00307` the named paragraphs
  (average 81) grew by 23 and the body stopped 25 short of 1,800. Each named paragraph now
  carries its distance to the target, to the nearest ten: "섹션 1의 문단 2(현재 81자, 약 50자 더)"
  or "(현재 171자, 약 40자 덜)". The request says what the amount in brackets means.
  `plan_target()` is the middle of `PLAN_PARAGRAPH_CHARS`.

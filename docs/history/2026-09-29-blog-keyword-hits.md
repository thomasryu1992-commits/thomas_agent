# 2026-09-29 — Blog draft: the keyword in the body is asked for and counted spacing-free

`bcp_0d025abf645c3ef25594` ('소상공인 스마트상점') passed with `keyword_hits` 1: the draft used the
keyword once and shortened it to '스마트상점' six times. The count was right. The request asked
for the keyword in the titles only, and a two-word keyword gets shortened in the body.

- `content_request` now asks for the keyword 3~6 times across headings and paragraphs, once in
  the first intro paragraph. The numbers come from `STANDARDS["keyword_hits"]`, and the ask says
  a shortened keyword does not count.
- `blog_draft_score.keyword_hits` counts the keyword with spacing ignored, so
  '소상공인스마트상점' and '소상공인 스마트 상점' count. A part of the keyword on its own still
  does not. Both measurements (structured and text) use it.
- `STANDARDS_VERSION` → `blog_draft_standards.2026-09-29`, because what `keyword_hits` measures
  moved. The standard stays advisory (3~6, not critical).

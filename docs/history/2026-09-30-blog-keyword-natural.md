# 2026-09-30 — Blog keyword: a natural form, counted the same

After #1042 named the paragraphs to put the keyword in, "띄어쓰기와 표기 그대로", the keyword
count was met in three manual runs out of three. Every one of them glued the keyword to the next
noun like an adjective:

- "CHATGPT사용법 계정 생성 절차" (the normal spelling 'ChatGPT' never appeared)
- "명함만들기 위한 전체보기 메뉴"
- "원하는 방수스티커제작 크기"

- `blog_draft_score.keyword_hits` now ignores letter case as well as spacing, so 'ChatGPT 사용법'
  is 'CHATGPT사용법'. `STANDARDS_VERSION` moved to `blog_draft_standards.2026-09-30`.
- `KEYWORD_FORM_ASK` goes into both the draft request and the keyword revision ask:
  - spacing and case may be written naturally and still count;
  - the keyword takes a particle and stands as a subject or object;
  - never glue it in front of another noun, with the three failures as the wrong examples.
- The named paragraphs may take the keyword by rewording one sentence or by adding one sentence
  in which it is the subject or object.

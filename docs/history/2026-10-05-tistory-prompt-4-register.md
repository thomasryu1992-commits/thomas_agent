# Tistory prompt .4: the post is written in 존댓말, and a 해라체 draft fails

- **What happened.** '캡컷 유료' (`bcp_710bdd04cd3676404b5b`, 2026-10-05) came back
  `ready_for_review` and scored 7.3/10, the best so far. But 90 of its 92 sentences ended in 해라체:
  "…언급된다.", "…수치다.", "…경우가 많다.".
  - The blog writes 존댓말: the vault's `naver-to-google-seo` rule is "존댓말 유지".
  - The request never said so.
  - The text came from the revision, answered by another chain member. That model took on the
    request's own imperative register ("…써라", "…마라"). The earlier drafts had written 합니다체.
  - Nothing measured it, so the package was `ready_for_review` with every sentence needing a
    rewrite.
- **What changed (`tistory_prompt.2026-10-05.4`).**
  - `REGISTER_RULE` sits in the content request and in every revision request: the body, FAQ
    answers and excerpt are 존댓말 even though the request itself is 해라체. Titles, headings and
    the table may end in a noun.
  - `plain_sentences` counts sentences ending in '다' that is not '니다'; quoted lines and table
    rows are skipped.
  - More than 2 such sentences, or more than 10% of the post's sentences, whichever is larger,
    fails `plain_register`. The revision is told to change only the endings.
  - On the two real drafts the count is 89/92 for '캡컷 유료' and 0/91 for '노션 AI 요금제'.
- **Naver is unchanged.**

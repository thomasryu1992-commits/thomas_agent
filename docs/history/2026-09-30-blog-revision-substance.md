# 2026-09-30 — Blog draft: substance over filler, a cleaner checklist, no leaked instructions

Three drafts on candidate-1063 (CHATGPT요금제, ai 번역기, 클로바노트 시간 추가) scored about 57 by
hand. Three problems from those drafts, fixed in order:

1. **Filler as length.** The two revisions that grew a short body gave every paragraph one more
   closing line that fits any post ("…지혜가 필요합니다", "꼼꼼한 확인이 실수를 미연에 방지합니다").
   The ledger shows the first drafts without them. The add-mode length ask ended "이유·예시·주의점을
   더해 늘려라". `ADD_SUBSTANCE_ASK` now says what to add: a specific from the evidence for that
   section's heading (a number, a menu or button name, a step, a setting) or one concrete reader
   situation. It also names the closers not to use. It rides every add-mode length ask, named
   paragraphs or not.
2. **Advice on the checklist.** Most of the detector's checks were sentences telling the reader
   what to do ("약관을 다시 한번 확인해야 합니다"), announcing the post, or saying a thing may
   change. A sentence without a digit that ends in advice, an imperative, an announcement or a
   may-change hedge is no longer a claim. A digit keeps it in ("월 10회까지이니 아껴 써야
   합니다"). Only the deterministic detector changes; the model's own checks are kept as they are.
   On the three drafts the detector's lists go 15→8, 6→2 and 6→2, and the real claims stay
   (free users reaching the latest model, the per-recording limit).
3. **The instruction in the body.** Asked to "say it is a foreign figure and may differ in
   Korea", 'ai 번역기' used no foreign figure and wrote that sentence anyway. `DOMESTIC_READER_ASK`
   puts '(해외 기준)' only on the sentence that uses a foreign source. With no foreign source,
   there is no sentence about foreign prices or conditions. None of the request's instructions
   are restated as prose.

All three are prompt wording except (2). Whether (1) and (3) move the drafts is for the next
scored runs to show.

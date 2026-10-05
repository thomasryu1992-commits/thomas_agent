# The Tistory request gets a length plan; the first live fire wrote 956 characters against 3,500

- **What happened.** The first `platform=tistory` fire was '퍼플렉시티 요금제'
  (`bcp_390f9954b2beefe109ed`, 2026-10-05). It kept the brief-first JSON, the title rule, the slug,
  the alt texts and an internal link. Its body was 956 characters and its intro 198:
  - The model (OpenRouter qwen) stopped by itself (`STOP`) at 2,607 output tokens of 12,000. It
    wrote two sentences a paragraph.
  - The one revision came back long, but with broken JSON: a section closed `}]}`, and the
    model's own notes were appended after the object. `REVISION_UNSTRUCTURED:KEPT_FIRST_DRAFT`
    kept the first draft, as designed.
- **Why.** The request asked for "3,500~5,000자" as a total. Naver learned in September that a total
  does not move a draft and a shape does (`blog_naver._length_plan`). The Tistory profile had not
  carried that over.
- **What changed (`tistory_prompt.2026-10-05.2`).**
  - The plan: 3 intro paragraphs, plus 6 H2 sections × 3 paragraphs, plus 4 FAQ answers.
    - Each paragraph is 4 sentences and 160~190 characters.
    - That totals 3,680~4,310, and the intro alone is ≥480 at the floor.
  - It is stated in the draft's JSON shape (the counts and sentence marks), and in prose with a
    length example and a count-before-answering check.
  - A length revision names each short paragraph and how much it needs.
  - The arithmetic is tested against `blog_tistory.STANDARDS`.

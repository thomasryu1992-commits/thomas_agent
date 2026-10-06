# Tistory prompt .3: the fixes a read of its first ready draft asked for

- **The read.** '노션 AI 요금제' (`bcp_c4be3ea7381cd1fa1faf`) was the first Tistory draft to clear every
  standard on its first try. Read against its own prompt, it scored 6.1/10:
  - the `.2` length example was about 요금제·해지·환불 — the lane's own subject — and the draft pasted
    it as an intro paragraph (similarity 0.95);
  - the first sentences answered nothing, so the search snippet was generalities;
  - 8 of 25 paragraphs closed on a sentence that fits any post ("…지름길입니다");
  - 5 FAQ answer sentences repeated the body;
  - 1 of 20 checked claims had a source, and the price came from a third-party blog. The shared
    Korean-reader rule forbids a foreign service's own (USD) price, which pushed the draft there;
  - ~900 characters of print-shop naming rules rode along on an AI-tool post;
  - the one internal link went to a Claude pricing post, matched on '요금제'.
- **What changed (`tistory_prompt.2026-10-05.3`).**
  - The length example is about growing herbs. A draft that reuses it (ratio ≥ 0.6) fails
    `length_example_copied`, and the revision is told to rewrite that paragraph.
  - The intro's first sentence must carry one concrete fact from the evidence: a price, limit,
    plan name or condition.
  - The request now asks for no generic closers. They are counted (`generic_closers`) and pointed at.
  - FAQ answers must not restate the body. Restatements are counted (`faq_echoes`) and pointed at.
  - Tistory has its own evidence rule, with official pricing and help pages first. An official price
    that exists only in a foreign currency is written as is with "(해외 기준)", never swapped for
    another site's won price.
  - The shared print-shop/Korean-reader block no longer goes to Tistory. Naver keeps it: the Naver
    digests are unchanged.
  - Internal-link candidates must name the target's tool or subject; an intent word or a generic
    word ('요금제', 'AI', '무료') is not a match.
  - POST.md shows the markdown body with its blank lines.
- **Not gated.** Generic closers and FAQ echoes are advisory: both detectors are heuristics, and a
  heuristic that gates would hold good drafts back.

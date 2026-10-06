# Tistory prompt .6: the JSON shape lays out the form, so the length plan is the shape the model follows

- **What happened.** The first `.5` fire, '제미나이 해지' (`bcp_5be1dc3c22fb9b985b53`), followed the
  절차형 skeleton heading for heading. It bolded 4/4 key sentences and linked 8 sources in place.
  It still wrote 4 × 3 + 3 = 15 paragraphs, 2,099 characters against the 3,500 floor, with no H3
  under its steps. The plan in prose said "21 paragraphs under the H2s and H3s", but the JSON shape
  still showed one section of three paragraphs. The model followed the shape, as Naver's drafts
  did in September.
- **What changed (`tistory_prompt.2026-10-06.6`).**
  - Each form has a `layout`: its sections in order, each with a level and a paragraph count.
    - 4 H2s per form, with the H3s its skeleton names (cases, examples, steps, claims).
    - The paragraph counts sum to the plan's 21.
    - A test pins the sum and the H2 range for every form.
  - `draft_shape(plan, target)` builds the request's JSON shape from that layout: section and
    paragraph slots, key-sentence slots on the H2s, and the plan's FAQ count. The revision request
    is shown the same shape.
  - The brief's `search_intent` slot carries the keyword's own intent (lexicon). The fire declared
    `how_to` for a cancellation search.
  - Intro sentences that only announce the post ("오늘은 … 알아보겠습니다", "…살펴보겠습니다") are
    named in the request and counted (`intro_tails`, advisory).
- **Naver is unchanged.**

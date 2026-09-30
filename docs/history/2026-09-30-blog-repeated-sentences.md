# 2026-09-30 — Blog draft: a sentence pasted twice fails, and public sites are named

Scored on candidate-1073 (2026소상공인지원금신청 ≈70, 통신판매업 신고증 ≈45, 연차 계산법 ≈72).

**Padding by copying.** '통신판매업 신고증' (`bcp_371db05812bb56548597`) came in short at 1,601
characters. Its revision reached 2,260 and passed every standard, but it got there by pasting
sentences from neighbouring paragraphs: 17 of 69 sentences were repeats, and the two intro
paragraphs were the same four sentences reordered. Across the 15 drafts scored on
2026-09-30, it is the only one with any repeat.

- `blog_draft.repeated_sentences` finds each sentence of 15 or more visible characters that
  appears twice or more across the prose. Spacing and punctuation are ignored; table rows are
  skipped. `repeat_count` counts the extra copies.
- `interpret_draft` records `measured["repeated_sentences"]`. Any repeat adds the
  `repeated_sentences` failure. It is a contract failure, counted as one in `_miss`, so a
  revision that pads by copying loses to the draft it copied from. On the real ledger pair the
  first draft (0 contract failures, body short) is kept over the revision (1).
- A revision of a draft with repeats is told to keep each once and names up to five of them.
  `ADD_SUBSTANCE_ASK` forbids growing a paragraph with a sentence from another.

**Public sites are named.** '2026소상공인지원금신청' sent the reader to "지정된 지원금 전용
포털" for a voucher applied for on 소상공인24. `VENDOR_NAME_ASK` now says government and public
sites and services (정부24, 홈택스, 위택스, 소상공인24, 고용노동부, …) are not businesses. They are
named, because the reader has to find them.

The test fixtures used one example paragraph everywhere. Each paragraph's sentences now carry
their own number (`_para`, `_vary`), so a fixture is not itself a repeat.

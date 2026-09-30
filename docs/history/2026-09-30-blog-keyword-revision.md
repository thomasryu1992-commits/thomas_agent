# 2026-09-30 — Blog revision: the keyword under its floor is asked for, in named paragraphs

After #1032 the draft request asked for the keyword 3~6 times across headings and paragraphs.
Three manual runs in a row ('고용지원금', 'X배너', 'ai 영상 만들기') still used it once. The cause
was not case or spacing. The model wrote the keyword in the intro and never again, and the
standard was advisory, so nothing asked again.

- `interpret_draft` records `keyword_hits` as a failure when the count is under the standard's
  floor (3). This triggers the one revision and keeps an unfixed draft at `needs_edit`. Over the
  ceiling (6) stays advisory, and the standard itself is still not critical (`critical_pass`
  unchanged).
- The revision ask states the range and the current count, then names the paragraphs to put the
  keyword in. These are paragraphs that do not have it yet: the first intro paragraph first,
  then each section's first paragraph, then later ones. It names as many as it takes to reach
  the middle of the range. One sentence per named paragraph changes, and nothing else in it.
- A revision that fixes the keyword but breaks the body is not taken (#1034's rule).

# 2026-09-29 — Blog draft: a raw control character inside a string does not fail the draft

`bcp_5e855e2073b66b805cfd` ('부당해고기준') wrote a complete first draft: every key, in the new
order (#1036). It still went to the legacy parser, because one paragraph ended with a raw
newline and strict JSON refuses control characters inside strings ("Invalid control
character"). The revision then had no paragraph list to name, and re-emitted the same 1,403
characters.

`blog_draft._loads` is `json.loads(..., strict=False)`, used for both the plain parse and the
repaired one. The character is kept as written, and `sanitize_paragraph` already treats an inner
newline as a line break and drops a trailing one. On that run's first draft the parse is now
structured, and the revision ask names the 17 paragraphs under 120.

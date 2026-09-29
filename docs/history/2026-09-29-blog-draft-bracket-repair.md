# 2026-09-29 — Blog draft: dropped closing brackets are put back before the legacy fallback

The candidate-1027 manual run (`bcp_9d711a421c5f713e36b6`) was a complete draft — five sections,
a table, five capture directions, tags and four titles — that failed as `legacy_markdown`: the
model (Google `gemini-flash-lite-latest`) closed the last section as `…"}]` instead of `…"]}]`,
and the revision, given the broken first draft verbatim, copied the same error.

`blog_draft.repair_brackets` now scans the draft's JSON with a bracket stack (strings and
escapes respected). A closer that matches an outer open bracket means the brackets in between
were never closed; their closers are inserted in front of it. Nothing else is changed: a surplus
closer, a draft cut off inside a string or still open at the end gets no repair, and the result
must still parse. A repaired draft is structured, so the revision is given its clean
re-serialization rather than the broken text. The package records the count in
`quality.brackets_inserted` (an optional v0.2 field) and POST.md shows it on the quality line.

Both saved drafts from that run parse after the repair (one closer each).

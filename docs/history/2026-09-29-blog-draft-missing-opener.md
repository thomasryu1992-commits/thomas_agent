# 2026-09-29 — Blog draft: a missing object opener is put back too

The first manual run on candidate-1030 (`bcp_5c5b3bdcfdea9d38cb47`) reached `ready_for_review`
only through its revision: the first draft opened its sections as `"sections": ["heading": …`
(the first section's `{` missing) and also ended the last section `…"}]` (the `]` missing). The
closer-only repair from #1030 could not start on the first gap, so the draft went to the legacy
parser.

`blog_draft.repair_brackets` now also inserts `{` in front of a key (a string followed by `:`)
that sits directly inside a list — that object was never opened. Strings are still never
touched, a colon inside a string is not a key, and the result must still parse. The count in
`quality.brackets_inserted` covers both kinds; POST.md says "빠진 괄호 N개 보정". That run's first
draft parses after the repair with two brackets put back.

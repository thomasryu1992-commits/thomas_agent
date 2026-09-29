# 2026-09-29 — Blog draft: a draft that stopped with brackets open is closed at the end

`bcp_cbbabb9953f3c67ba332` ('주휴수당 지급기준') ended `needs_edit`. Its first draft stopped after
the last section's `…"]}`: no `]` for `sections`, no root `}`, nothing after. `repair_brackets`
declined open brackets at the end by design, so the draft went to the legacy parser. The
revision then had no paragraph list to name, and came back at a 169 average.

`repair_brackets(..., close_at_end=True)` now appends the closers of brackets left open at the
end. `parse_structured` tries the outermost `{...}` first as before. Only when that cannot be
repaired does it take the text to its end and close it there. Nothing the model never wrote is
filled in: that draft parses with 5 sections and no tags or capture directions. A text that ends
inside a string or after a `,` or `:` still does not parse, and falls back as before. On that run's
first draft the revision ask now names 17 paragraphs over 140.

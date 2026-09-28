# The held blog lane, re-checked against an outside status table: nine defects real, none fixed, and the one "solved" row only solved inside the lane

- **What was checked:** a thirteen-row status table Thomas pasted on 2026-09-28. It graded the lane:
  schedule off ✅, `written_keywords()` ✅, CI ✅, nine rows ❌. Every row was read against
  `origin/main` at `cf254bc3`.
- **What it found:**
  - The nine ❌ rows are real. Seven were already in `REMAINING_WORK.md` §J: items 1–6 and Phase 4.
    Two are new there:
    - the draft score is advisory by the module's own stated design;
    - the draft is parsed from markdown with regexes.
  - `written_keywords()` (#980) reads the ledger's package rows, and there are none. The 69 Naver and
    77 Tistory posts written so far live in the vault, which the lane does not read. Enabled as it
    stands, the lane would re-pick published keywords.
  - The table omits the cause of all three failed fires: the fixed seeds. Fixing the nine rows still
    ends every fire in `NO_ELIGIBLE_KEYWORD`.
- **The call (Thomas 2026-09-28, "문서로 남겨줘"):** record it, fix nothing. The vault flow that
  makes the posts already covers what the nine rows are about:
  - live-SERP reach instead of `compIdx`;
  - used-keyword exclusion from front matter;
  - facts checked against primary sources;
  - structure checks;
  - weekly rank tracking.
- **What changed:** docs only.
  - §J gains the re-check, the two new items (7 and 8) and a revival order: keyword source first,
    then target evidence, then the rest.
  - The 08-24 note "`already_written` is never supplied" is marked half-closed.
- **Deliberately not done:**
  - No code change and no `enable`.
  - The revive-or-remove decision stays with D8's deadline, 2026-11-25.

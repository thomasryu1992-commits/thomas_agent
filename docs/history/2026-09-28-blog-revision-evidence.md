# The blog revision is handed no evidence references, and keeps the first draft's sources

- **What was measured:** package `bcp_ea2d271d8bd9cd453c2d` (2026-09-28, `사업자등록증명원`) is
  1,305 characters, so the one revision ran. It was withheld at the pipeline's automatic
  validation: "A fact cites a source this run never provided: [S1]".
- **Why:**
  - The revision is its own governed run with no evidence of its own. Its web search runs on the
    long request text, which embeds the whole first draft, and came back with 0 hits. It runs no
    keyword brief.
  - The revision request carried the first draft verbatim, `[S1]` and all.
  - The model repeated the reference, and the run's own checks correctly refused a citation of a
    source that run never had. The flaw was in the lane's revision design, not the model.
- **What changed:**
  - The first draft handed to the revision has its evidence references removed: `sources`
    emptied, fact-check `source_ref` nulled, and every `[S#]`/`[K#]` stripped. The request says
    why.
  - When the revision is adopted, the first draft's resolved sources carry over into the
    package, because the facts are frozen and those sources were checked against the content
    run's real evidence.
  - A fact check whose claim is unchanged keeps its first-draft `source_cited` state. A reworded
    claim stays `needs_manual_verification`, since the source was matched to the old wording.
- **Not changed:** the revision still runs its own web search, one wasted call per revision.
  Suppressing it would need a no-search option on `run_task`, which is out of scope here.

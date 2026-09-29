# Blog draft, third round: plan headroom, the table as its own field, capture directions kept, grouped citations

- **What was measured:** package `bcp_e8571b880a95946c9103` (2026-09-29, `사업자 폐업신고`).
  - **The body passed:** 2,645 characters, the first draft ever to clear the 1,800 floor.
  - **Paragraphs overshot:** the average was 155 against the 150 cap (asked for 120~150).
  - **The first draft's JSON broke in its table section.** The model wrote
    `{": | : | :", "paragraphs": ["구분 | …"]}` in place of a heading, then omitted `tags`,
    `image_shots`, `fact_checks` and `sources`. The draft fell to the legacy parser.
  - **The revision dropped the images.** Revising that legacy text, it returned
    `image_shots: []`, so the package had no capture direction, no table and no source.
  - **A grouped citation slipped through.** `[S1, S3]` passed the revision's reference strip.
- **What changed:**
  - The plan asks 120~140 characters per paragraph (2,040~2,380 over 17), leaving headroom
    below the 150 cap.
  - The table is its own field, `table: {after_section, rows}`.
    - It needs a header plus at least one row. A cell's own `|` becomes `/`, and a malformed
      table is dropped.
    - It is rendered deterministically as one ' | ' paragraph after its section.
    - It is not a prose paragraph, so it counts toward none of the length measures. It sets
      `tables` to 1.
    - The request forbids ' | ' rows inside `paragraphs`.
  - The revision is asked to keep the first draft's `image_shots` (4~8) and its `table`. If an
    adopted revision still drops them, a structured first draft's own are put back and the
    layout is rendered again. They are addressed by section, so they survive re-layout.
  - Grouped citations (`[S1, S3]`, `[S1,K2]`, `[S2, 3]`) are stripped from the revision's input
    and resolved key by key in `sources`.
- **Unchanged:** the standards, the package schema, and the provider chain.

# Tistory prompt .7: a cut first draft stays structured, citations survive the revision, summary sections fail

- **What happened.** The first `.6` fire, '클로바노트 유료' (`bcp_63185d84aceaf646ff6e`), wrote the
  판정형 layout with H3s and reached 3,369 characters (2,099 under `.5`). It still scored 5.7/10:
  - The first draft (Gemini, finish `STOP`) stopped at `…, {"heading": "개인용 무료 한도 …", "level": 3,`.
    That is after a comma, which closing at the end (2026-09-29) cannot mend, so it went to the
    prose parser.
  - The revision rebuilt the structure. Its run has no evidence, so it deleted every `[S#]` and
    kept the space before it. `_carry_marks` had no first-draft structure to restore citations
    from. The body shipped with 0 inline links and five `제공됩니다 .` stops.
  - The revision's last H2 was "자주 묻는 질문과 요금제 관련 최종 요약": two paragraphs of
    "정리해 드립니다 … 응원합니다" in place of the form's own fourth section.
- **What changed.**
  - `blog_draft.load_object` tries one more repair. A text left open at its end (cut, not damaged
    in the middle) is cut back to its last whole value and closed (`_complete_ends`, `_repairs`,
    at most `MAX_TAIL_CUTS` tries). The unfinished tail is dropped, never completed. The live
    draft now parses with 2 sections, 5 citations, 2 sources and 6 capture directions.
    - This parser is shared, so a Naver draft cut the same way is also read as structured now.
      Naver's request bytes do not change; the digest test still passes.
    - A draft cut after `,`, `:` or inside a string used to be pinned as "not closed". It now
      parses with every section kept. The third fire's stray quote (`…]}"}`) is read whole too.
  - When the first draft was read as prose, `carry_layout` still restores its citations from that
    text: its paragraphs, plus its JSON string values when the text is broken JSON.
  - A space left before `.`, `!`, `?` or `,` is removed from the draft's prose (`tidy_stops`;
    a decimal such as `2 .5` is left alone).
  - A section whose heading is a FAQ or a summary (자주 묻는, FAQ, Q&A, 요약, 마무리, 맺음말,
    총정리) fails `summary_sections`. The revision is told to move questions to `faq` and use a
    layout section it has not written yet. No form's layout heading matches; a test pins that.
  - `tistory_prompt.2026-10-06.7`: the content request is byte-identical to `.6`; only the
    revision gains the `summary_sections` ask.
- **Not changed.** Why Gemini stopped mid-value at 3,476 of 12,000 output tokens is not known. The
  repair recovers what was written; it does not explain the stop.

# A broken Tistory draft keeps its citations; '유료'·'플랜' read as pricing

- **What the third live Tistory fire showed.** The fire was '챗gpt 유료 가격'
  (`bcp_503b936428417a0034a8`, from the "티스토리 전용" queue section).
  - **Lost sources.** The first draft's JSON broke on a stray quote before its last brace
    (`…"]}"}`). Its text still cited `[S1]`..`[S3]`, but a draft that is not structured resolved
    no sources. The revision runs with no evidence by design and carries sources from the first
    draft, so the repaired package shipped with zero sources and 0 of 23 checks sourced.
  - **Missed overlap.** Tistory already had '챗GPT 유료 차이' (`/15`). The intent lexicon read
    '차이' as comparison and '가격' as pricing, so the two posts were classed `safe_distinct_intent`.
    They are one subject with one searcher.
- **What changed.**
  - `blog_tistory`'s fallback for a draft that is not structured now resolves the `[S#]`/`[K#]`
    citations found in the text (`blog_draft.cited_refs`) against THIS run's evidence. A citation
    the run never had is still dropped. The Naver fallback is unchanged.
  - `blog_overlap.INTENT_MARKERS` adds '유료' and '플랜' to pricing. On the vault as of 2026-10-05,
    '챗gpt 유료 가격' is now blocked by '챗GPT 유료 차이', and the queue's next candidate,
    '캡컷 유료', opens. Naver selection does not read the lexicon and is unchanged.

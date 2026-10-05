# Tistory prompt .5: the vault's editorial system — forms rotate, key sentences bold, citations link in place

- **Why.** Read beside the vault's own Tistory prompt (`naver-to-google-seo`: SKILL.md and
  `assets/prompt.txt`), the runtime wrote every post on one skeleton: 6 H2 × 3, FAQ always 4. The
  vault removed exactly that "도장" in September (1-B; "FAQ 정확히 5문항" is on its banned list). The
  runtime also lacked bold key sentences, sources at the point of use, the fixed tags, the
  category and a `refresh:` date. It also gated a slug, which the vault notes is only a file name
  on this blog (addresses are `/N`). Separately, three of four live drafts ended under 3,500
  characters at ~145 characters a paragraph.
- **What changed (`tistory_prompt.2026-10-05.5`).**
  - **The editorial plan (`blog_tistory.editorial_plan`).** It is read from the vault's Tistory
    posts in number order: `form:` and `intro_hook:`, now loaded with each post.
    - The form is one of 판정형·계산형·절차형·통설검증형, by intent affinity.
    - It is never the previous post's form, and appears at most twice in the last five.
    - 실측형 and 사례해부형 are left out: they need inputs actually run or a real case, which the
      lane does not have.
    - The intro hook is the one after the previous post's.
    - The FAQ count is 3/4/5 by the post's number.
    - Each form's skeleton and unique asset go into the request.
    - The plan is recorded in `platform_metadata.tistory.editorial` (optional, additive on v0.3).
      POST.md prints it as the vault front matter, with `category` (AI intents) and `refresh`
      (pricing-type intents, +30 days).
  - **Key sentence.** Each section names its `key_sentence`, and the renderer bolds it in place.
    A sentence that is not really in the section is not bolded.
  - **Inline citations.** A sentence may end with its `[S#]`, and the renderer turns it into a
    link to that source, in place. A marker for evidence the run never had is removed.
    - Inline-cited sentences that state a price, limit or date become source-cited fact checks.
    - A revision drops the markers, so the first draft's are restored on the same sentences
      (endings aside), and key sentences by heading.
    - A marker written after the full stop is moved inside the sentence it follows.
  - **Tags.** The fixed tags 소상공인·자영업 are appended (10 tags at most).
  - **Slug.** A missing slug is a warning, not a failure.
  - **Length.** The paragraph plan is 24 (3 intro + 21 under the form's H2s/H3s).
- **Not ported.** Google autocomplete and slot checks (a new network egress needs Thomas's approval
  and a governance update), experience paragraphs (nothing was actually run), and summary-block
  rotation.
- **Naver is unchanged** (digests hold).

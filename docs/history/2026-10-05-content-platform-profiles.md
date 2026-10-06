# The blog lane becomes a content engine with a Naver and a Tistory profile; packages go to v0.3

- **What changed.** `blog_content` is now the engine: research, selection, the two governed runs,
  the one revision with its fail-safes, the package and its record. Everything that depends on the
  platform moved into a profile (`blog_platform.PROFILES`) over two adapter modules:
  - `blog_naver` — the Naver request, revision request, legacy prose fallback, plain-text paste
    and SmartEditor checks, **moved, not re-tuned**;
  - `blog_tistory` — a new Google-facing profile: a content brief first in the draft JSON, an SEO
    title held to the vault's title rule, an ASCII slug, an excerpt, H2/H3 sections, an FAQ, alt
    text on every capture direction, internal links resolved against a runtime-built `[L#]` list,
    and a markdown paste (`PASTE.md`).
  - `blog_prompt` holds the rules both requests share (invent nothing, name what the evidence names,
    vendor/tool names, the Korean reader, section focus, the frozen-facts revision), once.
  - `blog_overlap` classifies overlap with existing posts; `blog_quality` records quality in layers.
- **How a fire picks a platform.** `platform=tistory` in the schedule's request, the same grammar
  as `target=`. No token means Naver, so both existing `content_ideation` rows are unchanged. An
  unknown or doubled `platform=` is `BLOG_PLATFORM_UNKNOWN` before any run is spent.
- **Naver is byte-identical.** The content and revision requests were captured by sha256 on main
  (d6c2f10f) before the split; `test_the_naver_prompts_are_the_bytes_their_version_names` pins
  them to `blog_naver.PROMPT_VERSION`. A deliberate prompt change bumps the version and its
  digests in the same commit. Naver selection is also unchanged: written on either platform is
  still not a Naver target.
- **`blog_content_package.v0.3`.** Every v0.2 field keeps its meaning. Added:
  - `platform`;
  - `content_intent`;
  - `platform_metadata` — exactly one key, that platform's, enforced by the schema;
  - `overlap`;
  - `quality.layers`;
  - `lineage.prompt` — prompt, profile, standards and schema versions, plus the sha256 of each
    request actually sent.

  v0.1/v0.2 rows are not rewritten: they validate against their own schemas, and
  `blog_content.package_platform` reads them as Naver. `record_published_url` takes the URL rule
  from the row's platform. `blog_rank.trackable` refuses non-Naver packages, because the tracker
  reads Naver's blog search, where a Tistory post would only ever record "not found".
- **Overlap is classified, not answered yes/no.** The five classes are `exact_duplicate`,
  `cannibalization_risk`, `platform_repurpose`, `high_topic_overlap` and `safe_distinct_intent`.
  Search intent comes from a fixed keyword lexicon, so the result is deterministic. Each match is
  recorded with its platform, keyword, reason and action.
  - Tistory blocks only its own platform. A Naver post on the keyword is the conversion the vault
    already does by hand, recorded as `rewrite_required`, and the draft is told not to carry it
    over.
  - A Naver post's `google_kw` is reserved on Tistory.
  - Intent beats spelling: the vault keeps '클로드 사용법' and '클로드 무료 사용법' side by side.
- **Quality in layers, the gate unchanged.**
  - `structural` is the old gate and still alone decides `ready_for_review` / `needs_edit`.
  - `semantic` holds deterministic proxies: intent match, title/intro alignment, section focus,
    redundancy and brief-question coverage.
  - `information_gain`, `practical_usefulness` and `audience_fit` are recorded `not_measured`.
  - `evidence` and `platform_fit` are advisory.
  - **No LLM judge runs.** The draft chain is a free model whose revisions already block often
    enough to have their own outcome code; a second call per package adds a failure surface and an
    unreproducible number.
- **Not built, deliberately.**
  - No master article: the vault's V10 check measured shared sentences between a Naver post and
    its Tistory version as the loss (6-gram 0.20 FAIL, 47 pairs averaging 0.164 on 09-27).
  - No Tistory meta-description field: Tistory has none and uses the body's first ~400
    characters, so `meta_description` is derived from them and the 400-character intro is a
    critical standard.
  - No publishing: both platforms end at a package for human review.
- **Budget.** `blog_content_tistory` is 24,000 tokens per agent and 360 s. A Tistory draft runs
  about 6,000~8,000 output tokens, at or past the Naver profile's 8,000-token output half, and its
  revision carries the whole draft back as input. The draft chain's per-member cap is 180 s for
  Tistory (120 s for Naver).
- **Open.**
  - Tistory demand is still Naver Search Ad demand, the only measured demand the runtime has. No
    Google volume or Google-slot signal exists here; the vault's `kw-pipeline slots` is outside it.
  - The Tistory request has never met a hosted model.
  - No schedule row uses `platform=tistory`; adding one is an operator decision.

# Blog drafts run on their own OpenRouter model, with the time a slower model needs

- **What was measured (2026-09-28):** all four manual blog fires ended on groq, the analysis
  chain's last member.
  - The chain's free OpenRouter model (`google/gemma-4-26b-a4b-it:free`) is served from Google AI
    Studio's shared free pool. Its real-sized requests got 429, rate-limited upstream
    (`limit_source: upstream_provider_shared_pool`), even though the account's own free quota was
    1000/1000 and a tiny call went through.
  - Google's own free tier answered 503 "high demand" on six of seven models. The pinned 2.5
    models answer 404 to new users.
  - A non-Google free model, `qwen/qwen3.8-27b:free` (ModelRun), answered a real-sized
    strict-schema draft with the Role's key, in 68 s.
- **Decision (Thomas 2026-09-28):** use it for blog drafts only, not for the whole chain.
- **What changed:**
  - `providers.with_openrouter_model()` copies a chain with its `OpenRouterProvider` member
    rebuilt for another slug. It uses the SAME `Authorization` object and adds no gate call site;
    OpenRouter's opt-in already covers whatever slug is configured. The light tier, the Mocks and
    the other members are returned as they were.
  - `blog_content.draft_provider()` applies it to the draft and its one revision when
    `MVP_BLOG_OPENROUTER_MODEL` is set. The research run keeps the analysis chain. Unset or blank
    changes nothing.
  - Time:
    - The analysis chain gives its first member min(45 s, budget/3), which is 40 s at 120 s.
      That times a 68 s model out every time.
    - `FailoverProvider` now takes a `member_timeout_cap` (default unchanged), and the draft
      chain uses 120 s.
    - The `blog_content` budget profile carries `max_runtime_seconds: 360`, so each of three
      members gets up to 120 s. A whole fire stays inside the scheduler's 1,500 s deadline.
  - Compose passes `MVP_BLOG_OPENROUTER_MODEL` to `pipeline-worker` only.
- **Not changed:** the analysis chain, its model slugs, its 120 s budget, and every other role.
  The slug is configuration in the host `.env`, not code. Unset it to return the draft to the
  analysis chain.

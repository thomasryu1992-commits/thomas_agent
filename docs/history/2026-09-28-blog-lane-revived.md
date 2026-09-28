# The weekly blog lane is revived on the vault queue, as a new row; the fixed-seed row stays disabled

- **Decision (Thomas 2026-09-28):** turn the weekly lane back on, with its seeds taken from the vault
  keyword queue. This follows the same day's fixes: target-bound evidence, the structured draft and
  the blog budget (#1012); the vault's post folders mounted into `pipeline-worker` (#1013); and
  `source=queue` (#1014). All three are deployed.
- **The one runtime state write:** `scheduler_cli add --kind content_ideation --request
  source=queue --interval-seconds 604800`, run in `thomas-scheduler` as uid 10001.
  - It created `schedule_876d53b39de29b2af417`, enabled.
  - A new row's first fire is one interval after registration: 2026-10-05T13:19:36Z. That is the
    day after the Sunday 06:30 KST `kw_pipeline` rebuild of the queue.
- **Why a new row:**
  - `scheduler_cli` has add, enable, disable and remove, but no edit.
  - The old row, `schedule_1d25e2cef74b8a48adec`, carries the fixed seeds and the three
    `NO_ELIGIBLE_KEYWORD` fires. It stays disabled, not removed, so that history remains readable.
- **What a fire does now:**
  - It reads the queue's "다음 편 후보", skips written keywords, and briefs the top five.
  - It picks one queue candidate by queue score once demand is confirmed.
  - It drafts to the structured contract, with at most one revision.
  - It records a `blog_content_package.v0.2` whose state is `ready_for_review` or `needs_edit`.
  - Nothing is published.
- **How to stop it:** `scheduler_cli disable schedule_876d53b39de29b2af417` in `thomas-scheduler`.
  The tick loop re-reads `enabled` on every pass, so no restart is needed.
- **Watch the first fire:**
  - A stale queue (> 14 days) or an exhausted one is a refusal, not a guess.
  - The draft's JSON-in-`content_draft` contract has never met a hosted model; the first fire is
    that test.
  - The 120-second run limit is unchanged for a 16k-token draft.

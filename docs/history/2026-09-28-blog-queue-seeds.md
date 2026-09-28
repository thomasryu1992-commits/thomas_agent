# Blog seeds can come from the vault keyword queue, and the choice stays inside it

- **Why:** all three weekly fires failed on the same four fixed seeds. The vault already
  rebuilds a ranked candidate list every week: `tools/kw_pipeline.py run` writes
  `analytics/keywords/queue.md` from the root crontab on Sundays at 06:30 KST. Every candidate on
  it has passed a blog-SERP reach check (top-ten median daily visitors ≤ 250, exact-title matches
  ≤ 9) that the runtime cannot make for itself. Thomas asked for it as the seed source on
  2026-09-28.
- **What changed:**
  - `source=queue` in a `content_ideation` request reads the queue's "<lane> — 다음 편 후보"
    sections, and only those. The out-of-league rejects, the variants of written posts, the
    season notes and the rank report are not seeds.
  - Candidates already written (ledger ∪ vault, `covering_keyword`) are skipped. The top five by
    queue score become the brief's seeds, which is one Search Ad call's worth.
  - Selection is restricted to those candidates. A related head term the brief surfaces never
    passed the reach check, so it is excluded.
  - Candidates are ordered by queue score, which already weighs demand by reach. The fresh brief
    still has to confirm demand, and the chosen keyword keeps the queue's spelling.
  - `selection_evidence.seed_source` records the queue date, how many candidates it listed, and
    how many were skipped. Each candidate row carries `queue_score`. Both are optional fields on
    v0.2.
  - `pipeline-worker` also mounts `analytics/keywords` read-only. The whole folder is mounted,
    not the single file, because a bind-mounted file keeps its old inode when the writer
    replaces it.
- **Fail-closed:**
  - A missing or unparseable queue is `KEYWORD_QUEUE_UNAVAILABLE`.
  - A queue older than 14 days is `KEYWORD_QUEUE_STALE`, which allows one missed weekly rebuild.
  - A queue whose candidates are all written is `NO_ELIGIBLE_KEYWORD`, with the reason "exhausted".
  - `source=queue` combined with fixed seeds is `IDEATION_INPUTS_CONFLICT`.
- **Measured on the real vault (2026-09-28):** the queue dated 2026-09-27 lists 45 candidates. 8
  are already written. The seeds would be 사업자등록증 발급, 퇴직금 지급기준, 사업자등록증명원,
  사업자등록증명원발급 and 상표등록조회.
- **Not changed:** the weekly row stays disabled, and its request still names the fixed seeds.
  Switching it to `source=queue` and enabling it are the revival decision.

# The pipeline worker reads the vault's post folders, read-only, so blog selection can see what was published

- **Why:** since #1012, rule-based keyword selection excludes every keyword the vault already wrote,
  and it refuses (`PUBLISHED_KEYWORD_SOURCE_UNAVAILABLE`) without that source. The lane runs in
  `thomas-pipeline-worker`, which mounted no vault, so the fix could not take effect (Thomas
  2026-09-28: mount it).
- **What changed:**
  - `docker-compose.yml`, `pipeline-worker` only, mounts `${THOMAS_BLOG_VAULT_DIR}/content/naver`
    and `.../content/tistory` read-only under `/app/blog_vault/content/`.
  - The literal `MVP_BLOG_PUBLISHED_ROOT: /app/blog_vault` points the adapter at them.
  - The host root is an absolute path in `.env` (`THOMAS_BLOG_VAULT_DIR`), the same rule as
    `THOMAS_WORKSPACE_DIR`. Deploys run compose from throwaway worktrees, so a relative default
    would resolve inside one.
- **Deliberately narrow:**
  - Only the two post folders are mounted. `analytics/`, `portfolio/` and the paste packages
    stay out.
  - The mount is read-only, and no other service gets it
    (`test_no_other_service_mounts_the_vault`).
  - An unset root falls back to an empty folder. The adapter reads "no posts" as unavailable,
    never as "nothing published".
- **Not changed:** the weekly `content_ideation` row stays disabled. Mounting the source enables
  nothing.

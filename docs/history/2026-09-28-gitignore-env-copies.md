# Copies of `.env` are ignored, not just `.env` itself

- **Why:** `.gitignore` matched `.env` exactly. Sessions back the file up in place before editing
  it. On 2026-09-28, `.env.bak-20260928-vault` (mode 600, secret values) sat untracked in the
  primary checkout, one `git add -A` away from a commit.
- **What changed:**
  - A `.env.*` rule under `.env`.
  - `tests/test_gitignore_secrets.py` checks `.env` and three copy names with
    `git check-ignore --no-index`, so the check does not depend on what is on disk.
- **Deliberately not done:** there is no tracked `.env.example` or similar, so there is no
  negation rule. If one is ever added, it needs a `!` line and a test case.

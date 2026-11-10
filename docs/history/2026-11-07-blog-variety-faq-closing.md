# 2026-11-07 — Blog department: the verify port reads the closing past a FAQ

**What.** A deep read of 22 high-traffic Naver blogs (vault `analytics/benchmark/상위블로그-심층조사-2026-11-07.md`)
found FAQ blocks in 27 of 82 posts. Thomas approved adding it to our drafts ("순서대로 적용하고 검토해봐"). The Naver drafting
prompt now makes the last numbered section a FAQ of 2–4 questions (skills 77db6d6).

The review found that the variety check could not cope with this. Its V4 rotation check labels a draft's closing from the
section just before the last `---`, and with a FAQ there every draft read as `기타`. A retrofitted 203편 went from
`빈칸 판정` to `기타` and picked up a V4 WARN against the previous post.

The vault's `tools/blog_variety.py` (vault c3501456) now steps back one section when that section is a FAQ (lines starting
`**Q. `). This PR regenerates the P2 port, `runtime/mvp_runtime/blog/variety.py`, with `scripts/blog_vendor.py`.

**Checked.**
- `blog_vendor.py --check` reports that the port matches the vault.
- Across every Naver post in the vault, the change alters the closing label of no existing post. Only the two retrofitted
  test copies changed.
- The vault variety regression test passes with 3 new cases (scripts b089d64). The FAQ case fails against the old code.

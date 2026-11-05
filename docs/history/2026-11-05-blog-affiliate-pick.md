# 2026-11-05 — Blog department: the morning instruction names the affiliate stock (P7)

**What.** #1378 put `blog-affiliate-place.py`'s decision into P7: the READY gate, the editorial hold, and the next
stock to place. The row said the shadow would re-pick "each reported placement from that day's vault". That could
not be built:
- No run report had ever named a placement. `blog-run-report.py` lists the top of `content/naver/` only, and placing
  keeps the stock's old `created:` date. 198편 (placed 2026-11-04) was in no report.
- The gate reads `affiliate_stock.json`, the editorial hold register, the clips and the keyword notes. All of them
  change without a date, and the container has no vault history. So a past moment cannot be rebuilt.

Thomas chose to carry the pick in the morning instruction (2026-11-05, "지시에 싣기"). After P6 the instruction has
to name the affiliate post anyway.

**Change.**
- **Generator.** `scripts/blog_vendor.py --scripts ~/.claude/scripts` generates `blog/affiliate_place.py` and
  `blog/affiliate_clips.py` from the server scripts' git, the sources' own home (appendix A: `S`). Both are cut by
  keeping: the CLI, the placement writer and `--sync-notes` go. The `sys.path`/`importlib` loads of the vault's
  `blog_taxonomy` and `affiliate_fetch` become relative imports of the ports already generated from those files.
  Without `--scripts`, the two modules are neither written nor checked, and the run says so.
- **`blog_instruction` v0.2.** Adds `affiliate`:
  - the stock count per state;
  - the pick (an id of the stock file's slug, plus its first keyword; never the file name), or the script's own
    deferral code;
  - `unavailable` when the clip folders are not mounted;
  - `error` with the exception type when the gate cannot run. A stock without keywords makes the script itself raise.

  The pick is the first stock in the script's order whose state is "배치 가능", which is what `--list` prints as
  `다음 배치:`.
- **`blog_instruction_check` v0.2.** Adds `affiliate`: each placement in the report's `affiliate_placed` is compared
  with the pick by the slug that placing keeps. `matched` is null when the instruction had no pick to compare.
- **`blog-run-report.py`** (server scripts `594702e`) writes `affiliate_placed`. These are the `content/naver/제휴/NN편-*`
  files that were absent from the vault's git at the run's start. The comparison is against the last commit before
  the start, so an autopush during the run does not hide a placement. If git cannot be read, the field is null.
- **Compose.** scheduler-maint mounts `제휴/키워드` and `제휴/클립` read-only, only those two folders. The vault's
  `제휴/` also holds `발급/` and lists that the gate does not read.
- **`blog_department_week` v0.5.** The P6 line adds the week's placements: same as the instruction, different,
  unjudged, and the instructions that could not read the stock.

**Verified on the real vault (host, 2026-11-05).**
- Port vs script `readiness()` on all ten affiliate files (two stock, eight placed; six of them ready): state,
  reasons, editorial hold, product_ready, story and bridge count were identical. Stock order was identical.
- The instruction's section on the vault matches `--list`: stock 2, ready 0, `NO_READY_STOCK`.
- A vault copy with 156편 and 198편 renamed back to stock: both the script and the port pick `제휴-카페스피커.md`.
- `placed_affiliates` with the 11-04 run's start (`2026-11-03T23:02:01Z`) returns 198편 only. With the 11-05 run's
  start it returns nothing.
- The instruction's queue is identical with and without the two new folders (98 kept, 9 not-create, 3 covered, 297
  Tistory). The keyword port's instruction path does not read them.

**Evidence will be thin.** Stock is two files, both held for review. Placements ran about one per four days
(8 in 33 days). Unless the stock is refilled, the two weeks after P6 may see zero to three comparisons.

**Left as is.**
- The affiliate post's reserved slot is not compared. A placed file is not in the report's `drafts`, so P7's Naver
  slot shadow does not see it.
- The editorial hold register and the stock JSON are read as they are at 07:10–08:02 KST. A stock the cron writes
  and places in the same run was not in the morning's instruction. It shows as `differed`, and that is the honest
  answer for a pick the instruction did not make.

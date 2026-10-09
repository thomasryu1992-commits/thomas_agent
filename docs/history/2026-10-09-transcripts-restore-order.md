# Transcripts restore: newest full at or before the point, then only the later incs; deletions stay deleted

- **Why:** #1197's runbook example applied the newest full and then every inc in stamp order. An inc
  older than that full was applied after it, putting older copies back over newer ones. Its comment
  called that harmless. It was not. Separately, an inc carries changed files but no deletions, so a
  file deleted after the full came back on restore. Thomas's review caught both (2026-10-09).
- **What this PR changes:**
  - `scripts/ops/restore_transcripts.sh` (new; bash 3.2 and BSD/GNU-common tools, because it runs on the
    Mac where the key is). It does the following, in order:
    1. Picks the point (`--until`).
    2. Picks the newest full at or before the point.
    3. Restores it.
    4. Applies only the incs after it, oldest first.
    5. Removes restored files that the final manifest does not list and that are not newer than its t0.
    6. Fails with exit 4 when the manifest lists a file that no archive carried.
  - `scripts/ops/harness_backup.sh`:
    - **Manifest.** Every transcripts archive now carries `TRANSCRIPTS_MANIFEST.txt`, the files that
      existed when it was made, headed by `# t0=` (UTC, `touch -t` form).
    - **t0 is read before the file list.** A file created while tar runs is newer than t0, so a restore
      keeps it.
    - **Log line.** `files=` does not count the manifest.
    - **Stamp override for tests.** `HARNESS_BACKUP_STAMP` overrides the stamp, for tests only.
  - Runbook §2.5 is rewritten around the script.
  - Tests:
    - A real `age` round trip (fresh throwaway key in the test's temp dir) through the backup and the restore script.
  - Four new tests: the manifest and its t0; restore applying an inc and keeping a deletion deleted;
      a newer full shadowing an older inc, with `--until` choosing the older chain; and the missing-full
      and missing-file exits.
    - The stub `age` gains a decrypt mode.
    - Three mutations were each caught: applying every inc, no deletion pass, ignoring `--until` for the full.
  - `tests/skip_ceiling.json` win32 129 → 134: the five new tests run bash and skip on Windows.
- **Not verified here:** a run on the Mac itself (bash 3.2 and BSD tools). The script avoids the known
  differences, but the first real restore on the Mac is its first run there.
- **Existing archives:** the 2026-10-09 07:32 full has no manifest. It still restores, but without the
  deletion and integrity step. Nothing is deleted or re-made here.

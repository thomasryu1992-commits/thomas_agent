# Transcripts restore proves the core assets' content; verified restores are named by what they proved

- **Why (Thomas, final review 2026-10-09):** `VERIFIED` meant the chain and the metadata matched, not that
  the content did. Hashing every conversation log every day would cost too much. The core assets are small:
  the prompt collector's state (prompt texts, review queue, links) and the per-project memory.
- **What this PR changes:**
  - `harness_backup.sh` adds a 4th manifest column, a sha256, for files under `.claude/prompt-collector/`
    and `.claude/projects/*/memory/`, plus a `# hashed=collector-state,memory` line. On 2026-10-09 that was
    161 files in the human-session set. The run took 27 s, as before.
  - `restore_transcripts.sh` hashes each restored core asset with perl `Digest::SHA` (core on macOS and Linux):
    - a mismatch → BROKEN, exit 8;
    - a copy that changed during the backup → skipped and counted.
  - Verified restores keep exit 0 and get two names: `CONTENT_VERIFIED` (chain plus every core-asset hash)
    and `CHAIN_VERIFIED` (no core asset in scope). The summary line gains `core-hash=ok:N,bad:N,skipped:N`.
  - The vault's prompt assets and workflow history are not in these archives. They live in git, whose
    objects are content-addressed, so `git fsck` and the GitHub copy are their integrity check.
  - Tests:
    - four new tests: hash columns only on core assets, a mismatch, `CHAIN_VERIFIED` without core assets,
      a changed-during-backup skip;
    - two updated for the new names;
    - one mutation caught (the comparison removed);
    - win32 skip ceiling 144 → 148.
- **Real data, scratch destination, pass-through age:** `CONTENT_VERIFIED`, `core-hash=ok:161,bad:0`, 4 s to
  restore the 435 MB trial set.

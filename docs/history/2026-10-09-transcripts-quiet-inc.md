# Transcripts inc archives stop printing a line per unchanged file

- **Why:** the first real inc after #1198 printed `file is unchanged; not dumped` for each skipped file,
  14,558 lines, to stderr. From cron that is a daily flood of discarded mail.
- **What:** `--warning=no-file-unchanged` on the transcripts tar. One test asserts the inc's stderr is
  quiet, and a mutation without the flag fails it. Nothing else changes: the members, the archive, the log line and the restore are the same.

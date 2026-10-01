# The core backup is encrypted to an age public key before it leaves the host

- **The defect:** the daily core archive held `thomas_agent/.env` (the single secret source) and
  `hermes-trial/data` (the assistant's credentials) as a 0600 `tar.gz`, and the Mac pulled it every
  day. A file mode protects nothing once the file is copied; anyone holding a copy held the secrets.
- **Decision (Thomas, 2026-09-29):** encrypt with age to a public key. Thomas generates the key pair
  on the Mac; only the public key comes to the host. Excluding `.env` instead was the alternative
  and was not chosen: encrypting the whole archive also covers the assistant's credentials.
- **What changed:**
  - `scripts/ops/harness_backup.sh`, core mode: `tar czf - … | age -R <recipients> -o <out>.part`,
    renamed to `govstate-<stamp>.tar.gz.age` only when tar (0/1), age (0) and the age version header
    all check out. No plaintext archive reaches the disk.
  - Before anything runs: `age` present, a recipients file with at least one well-formed `age1…`
    line, and no `AGE-SECRET-KEY` in it (the private key must not be on the host). Otherwise
    `FAILED mode=core stage=encrypt reason=…` and no archive: no backup is better than a plaintext one.
  - `stage=archive` vs `stage=encrypt` on every FAILED line; `enc=age recipient=<prefix>` on OK.
  - Plaintext archives from before this match no prune glob. They are removed once seven encrypted
    ones exist (`legacy-plaintext-removed=N`), the day the old retention would have removed them.
  - Candles stay plaintext: public market data, no secret.
  - `scripts/ops/backup_watch.sh` reports four cases apart: no encrypted archive, a stale one, an
    archive (tar) failure, an encryption (age / key) failure; an OK line without `enc=age` names a
    pre-encryption script still installed.
  - **Rebased over #1055 (2026-10-01).** #1055 had the watch list the core archive (`tar -tzf`) to
    catch an execution stage anchor in it. An `.age` archive cannot be listed on this host, so that
    check would have passed every day without reading anything — and git merged it without a
    conflict. The check moved to where the plaintext still exists: tar writes its member list
    (`--index-file`, names only) beside the stream, and an archive that lists the anchor, or has no
    list, is deleted before the rename (`FAILED … stage=archive reason=anchor-in-archive`). OK lines
    carry `anchor=excluded`; the watch reads that word, and an OK line without it names a script
    that never ran the check.
  - `docs/RUNBOOK_HARNESS_BACKUP_RESTORE.md`: restore starts with decryption on the Mac (§2.0);
    this host cannot read its own backups, and losing the private key loses every encrypted archive.
- **Tests:** `tests/test_ops_harness_backup.py` runs the real script on a scratch host root with
  stub docker/age (refusals, failed/truncated/non-age output, archive failure, success contents,
  legacy removal, candles unchanged) plus a real-age round trip where age is installed.
  Mutation: removing the header check, the private-key refusal, the age exit check, the anchor
  member-list check, or pointing the watch back at plaintext each fails a test. The anchor case runs
  a copy of the script with its `--exclude` line removed.
- **CI:** both pytest workflows install `age` on Linux (`apt-get`), because linux allows no skip
  and the round trip must run there; the win32 skip ceiling rises 96 → 115 for the 19 new tests
  that skip a bash script on Windows (#1055's two archive-listing tests were replaced one for one).
- **Rollout is coupled, and not part of the merge:** the host needs `age` and the public key before
  the script is installed, and the Mac pull (vault `ops/mac-backup-pull/`) must change the same day
  — its `govstate-*.tar.gz` glob does not match the new name and would silently keep only candles.

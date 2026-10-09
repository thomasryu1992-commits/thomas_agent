#!/bin/bash
# Harness backup — five roots, two modes. PR4 of the Hermes integration sequence (Thomas decision
# Q10, 2026-09-03; installed 2026-09-04). Lives in the Thomas Agent repository at
# scripts/ops/harness_backup.sh and is INSTALLED to /root/backups/backup-governance-state.sh (the
# crontab path did not change). Restore procedure: docs/RUNBOOK_HARNESS_BACKUP_RESTORE.md.
#
#   core     daily 07:45Z, keep 7   — everything that is not a candle:
#              thomas_agent/.runtime_governance_state   (approvals, ledgers, schedules, crypto state)
#              thomas_agent/THOMAS_CORE/{activations,approvals}   (root-owned Core activation, was never backed up)
#              thomas_agent/workspace                   (content-lane deliverables)
#              thomas_agent/.env                        (the single secret source, 0600)
#              hermes-trial/data                        (SOUL, MCP shims, skills, cron, memories, sessions,
#                                                        and a consistent SQLite copy via `hermes backup --quick`)
#   candles  weekly Sun 08:15Z, keep 4 — crypto/candle_archive only (unchanged from 2026-08-31).
#   transcripts  daily 07:55Z — Claude Code conversations (.claude/projects) and the prompt
#              collector's local state, encrypted like core. Full weekly (keep 2), changed files on
#              the other days (kept while their full is). Thomas 2026-10-09.
#
# The assistant's compose definition is NOT a member: since PR5 (2026-09-04) hermes is the ninth
# service of this repository's docker-compose.yml, which is code and lives in git. Backing up the
# retired /root/hermes-trial/docker-compose.yml is what broke this script for three days.
#
# Member paths are prefixed with the host directory (`thomas_agent/…`, `hermes-trial/…`) so a restore
# knows where each root goes. Archives before 2026-09-04 start at `.runtime_governance_state/` instead.
# File names keep the `govstate-` prefix: the Mac pull (`com.thomas.govstate-pull`) globs on it.
#
# The core archive is ENCRYPTED to an age public key (Thomas decision, 2026-09-29): it carries .env
# and the assistant's data, and it leaves the host every day. `tar | age -R <recipients>` streams,
# so no plaintext archive is ever written to disk. The recipients file holds PUBLIC keys only
# (`age1…`); the private key lives on the Mac, and decryption happens there — never on this host.
# A missing age binary, a missing or malformed recipients file, or a private key found in it fails
# the run before tar starts: no archive is better than a plaintext one. The file is
# `govstate-<stamp>.tar.gz.age`; the candle archive stays plaintext (public market data, no secret).
# Log: `FAILED mode=core stage=encrypt|archive …` says which half failed; `OK mode=core … enc=age
# recipient=<prefix> anchor=excluded` names the key the archive is for.
#
# Live SQLite files are NOT tarred (a WAL-mode database copied mid-write is not a backup). The
# assistant's own `hermes backup --quick` copies state.db with the sqlite backup API into
# data/state-snapshots/<stamp>-daily/, and that directory IS tarred. The workflow store (sequence 2,
# P10; V0.2 Q28) follows the same rule: the dispatch bridge copies workflow.db with the backup API
# into .runtime_governance_state/workflow/snapshots/<stamp>/ as uid 10001, that directory is tarred,
# the live workflow.db* is excluded, and the log line says `workflow-snapshot=ok|absent|FAILED`
# (absent = the manager never created the store on this host; not a failure). Only the newest
# snapshot directory is kept. The execution stage ANCHOR is excluded on purpose (Thomas 2026-09-30,
# EXECUTION_STAGE_ANTI_ROLLBACK D1 a): it vouches for the stage ledger, and restored together with the
# ledger it would vouch for an older stage. Without it a restore reads READ_ONLY until a BOOTSTRAP,
# which is the point. The archive is encrypted and cannot be listed on this host, so the check sits
# here: tar writes its member list beside the stream, and an archive that lists the anchor is
# deleted before it is renamed into place (`FAILED … stage=archive reason=anchor-in-archive`). An OK
# line says `anchor=excluded`; backup_watch.sh reads that word, never the archive.
# Recreatable caches, installed packages and logs are excluded — they
# are not state.
set -u

MODE="${1:-core}"
DEST="${HARNESS_BACKUP_DEST:-/root/backups/governance-state}"
HOST_ROOT="${HARNESS_BACKUP_HOST_ROOT:-/root}"
THOMAS=thomas_agent
HERMES=hermes-trial
STAMP="${HARNESS_BACKUP_STAMP:-$(date -u +%Y%m%d-%H%M)}"   # override for tests only
AGE="${AGE_BIN:-age}"
RECIPIENTS="${HARNESS_BACKUP_AGE_RECIPIENTS:-/root/backups/age-recipients.txt}"
umask 077
mkdir -p "$DEST" && chmod 700 "$DEST"

log() { echo "$(date -u +%FT%TZ) $*" >> "$DEST/backup.log"; }

# The encryption precondition, checked before anything is written. Prints the reason on failure.
# Only the key's prefix is ever logged; a public key is not a secret, but the log needs no more.
age_ready() {
  command -v "$AGE" >/dev/null 2>&1 || { echo "reason=no-age-binary"; return 1; }
  [ -f "$RECIPIENTS" ] || { echo "reason=no-recipients-file"; return 1; }
  # A private key here would put decryption on the host, which is what this design rules out.
  if grep -q 'AGE-SECRET-KEY' "$RECIPIENTS"; then echo "reason=private-key-in-recipients-file"; return 1; fi
  local keys bad
  keys=$(grep -v '^[[:space:]]*\(#\|$\)' "$RECIPIENTS")
  [ -n "$keys" ] || { echo "reason=no-recipient"; return 1; }
  bad=$(printf '%s\n' "$keys" | grep -cvE '^age1[02-9ac-hj-np-z]{58}$')
  [ "$bad" -eq 0 ] || { echo "reason=malformed-recipient"; return 1; }
}

case "$MODE" in
  core)
    OUT="$DEST/govstate-$STAMP.tar.gz.age"
    PART="$OUT.part"
    KEEP=7
    PRUNE_GLOB="$DEST/govstate-[0-9]*.tar.gz.age"
    # 0. Encryption first: refuse before the snapshots and before tar, so a failed precondition
    #    leaves nothing behind — and certainly no plaintext archive.
    if ! AGE_REASON=$(age_ready); then
      log "FAILED mode=core stage=encrypt $AGE_REASON"
      exit 3
    fi
    RECIPIENT_NOTE="enc=age recipient=$(grep -m1 -E '^age1' "$RECIPIENTS" | cut -c1-12)"
    # 1. A consistent copy of the assistant's SQLite state, made by the assistant itself (uid 10000).
    #    Non-fatal: if the container is down, the tar still carries everything but state.db and the
    #    log line says so — a backup with a hole you can see beats no backup.
    SNAP_NOTE="hermes-snapshot=ok"
    if ! docker exec -u 10000 hermes /opt/hermes/.venv/bin/hermes backup --quick -l daily >/dev/null 2>&1; then
      SNAP_NOTE="hermes-snapshot=FAILED"
    fi
    # Keep only the newest snapshot directory (33 MB each): older ones are in older archives.
    ls -1dt "$HOST_ROOT/$HERMES/data/state-snapshots"/*/ 2>/dev/null | tail -n +2 | xargs -r rm -rf
    # 1b. A consistent copy of the workflow store (P10), made by the dispatch bridge as uid 10001
    #     — the owner of the state directory, so the copy is the service's to write and to read
    #     back. Non-fatal like the Hermes snapshot: the archive then carries the previous
    #     snapshot directory and the log line says FAILED. No store = nothing to copy = absent.
    WF_DIR="$THOMAS/.runtime_governance_state/workflow"
    WF_NOTE="workflow-snapshot=ok"
    if [ -e "$HOST_ROOT/$WF_DIR/workflow.db" ]; then
      if docker exec -u 10001 thomas-dispatch-bridge python -m runtime.mvp_runtime.workflow_cli snapshot \
             --dest "/app/.runtime_governance_state/workflow/snapshots/$STAMP" >/dev/null 2>&1; then
        # Prune only after a good copy exists: the previous snapshot is the archive's only copy
        # until this one is (review of P10, 2026-09-14 — a failed run used to delete it).
        ls -1dt "$HOST_ROOT/$WF_DIR/snapshots"/*/ 2>/dev/null | tail -n +2 | xargs -r rm -rf
      else
        WF_NOTE="workflow-snapshot=FAILED"
        rm -rf "${HOST_ROOT:?}/$WF_DIR/snapshots/$STAMP"      # a partial copy is not a snapshot; keep the last good one
      fi
    else
      WF_NOTE="workflow-snapshot=absent"
    fi
    SNAP_NOTE="$SNAP_NOTE $WF_NOTE"
    # 2. One archive, two host roots, member paths prefixed with the directory they restore into.
    # A missing member makes tar exit 2 and this script delete the archive it just wrote, so the
    # whole backup is lost for the sake of one absent path. Name the absent path in the log —
    # `FAILED rc=2` alone said nothing for three days after PR5 retired a member (2026-09-04..06).
    MEMBERS=("$THOMAS/.runtime_governance_state"
             "$THOMAS/THOMAS_CORE/activations" "$THOMAS/THOMAS_CORE/approvals"
             "$THOMAS/workspace" "$THOMAS/.env"
             "$HERMES/data")
    MISSING=()
    for member in "${MEMBERS[@]}"; do
      [ -e "$HOST_ROOT/$member" ] || MISSING+=("$member")
    done
    if [ "${#MISSING[@]}" -gt 0 ]; then
      log "FAILED mode=$MODE stage=archive rc=2 missing=$(IFS=,; echo "${MISSING[*]}") $SNAP_NOTE"
      exit 2
    fi
    # Streamed: tar's plaintext goes straight into age and only ciphertext reaches the disk, as
    # a .part file renamed into place once both halves succeeded (a watch never sees a partial).
    # tar also writes its member list (names only, no content) to INDEX: the ciphertext cannot be
    # listed on this host, so this list is the only place the archive's contents can be checked.
    INDEX="$PART.index"
    tar czvf - --index-file="$INDEX" --warning=no-file-changed -C "$HOST_ROOT" \
        --exclude="$THOMAS/.runtime_governance_state/crypto/candle_archive" \
        --exclude="$THOMAS/.runtime_governance_state/crypto/execution_stage_anchor.json" \
        --exclude="$WF_DIR/workflow.db" --exclude="$WF_DIR/workflow.db-*" \
        --exclude="$HERMES/data/state.db" --exclude="$HERMES/data/state.db-*" \
        --exclude="$HERMES/data/kanban.db" --exclude="$HERMES/data/kanban.db-*" \
        --exclude="$HERMES/data/cron/executions.db" --exclude="$HERMES/data/cron/executions.db-*" \
        --exclude="$HERMES/data/cache" --exclude="$HERMES/data/lazy-packages" \
        --exclude="$HERMES/data/home" --exclude="$HERMES/data/bin" --exclude="$HERMES/data/.local" \
        --exclude="$HERMES/data/logs" --exclude="$HERMES/data/sandboxes" \
        --exclude="$HERMES/data/image_cache" --exclude="$HERMES/data/audio_cache" \
        --exclude="$HERMES/data/models_dev_cache.json" \
        "${MEMBERS[@]}" | "$AGE" -R "$RECIPIENTS" -o "$PART"
    PIPE=("${PIPESTATUS[@]}")
    TAR_RC=${PIPE[0]}
    AGE_RC=${PIPE[1]}
    # tar exits 1 when a live append-mode file changed under it (a valid snapshot); 2+ fails.
    if [ "$TAR_RC" -gt 1 ]; then
      rm -f "$PART" "$INDEX"
      log "FAILED mode=$MODE stage=archive rc=$TAR_RC $SNAP_NOTE"
      exit "$TAR_RC"
    fi
    # The execution stage anchor must not be in the archive (EXECUTION_STAGE_ANTI_ROLLBACK D1 a).
    # The --exclude above keeps it out; this proves it did, from what tar actually wrote. No list
    # at all is not proof of absence, so it fails the same way.
    if [ ! -s "$INDEX" ] || grep -q 'crypto/execution_stage_anchor\.json$' "$INDEX"; then
      REASON=anchor-in-archive
      [ -s "$INDEX" ] || REASON=no-member-list
      rm -f "$PART" "$INDEX"
      log "FAILED mode=$MODE stage=archive reason=$REASON $SNAP_NOTE"
      exit 2
    fi
    rm -f "$INDEX"
    # age's exit code, and then its output: an age file starts with its version line. Anything
    # else in the .part file is not ciphertext and must not be kept under an .age name.
    if [ "$AGE_RC" -ne 0 ] || [ "$(head -c 21 "$PART" 2>/dev/null)" != "age-encryption.org/v1" ]; then
      rm -f "$PART"
      log "FAILED mode=$MODE stage=encrypt rc=$AGE_RC $SNAP_NOTE"
      exit 3
    fi
    mv -f "$PART" "$OUT"
    SNAP_NOTE="$RECIPIENT_NOTE anchor=excluded $SNAP_NOTE"
    RC=0
    ;;
  transcripts)
    # Claude Code conversation transcripts (Thomas 2026-10-09): the only copy of every prompt typed
    # or pasted into a session lived on this disk. Same age recipients as core — no new key — and the
    # same `govstate-` prefix, so the Mac pull already fetches it. A full archive weekly, and on the
    # other days only the files changed since the newest transcripts archive (one hour of overlap).
    # Restore with scripts/ops/restore_transcripts.sh: the newest full at or before the chosen point,
    # then only the incs after that full, in stamp order — never an inc older than its full, which
    # would put older content back over newer (RUNBOOK §2.5). Every archive carries
    # TRANSCRIPTS_MANIFEST.txt: the files that existed when it was made, headed by `# t0=` (UTC,
    # touch -t form), so a restore can drop what was deleted since the full and keep what appeared
    # after t0.
    # `.claude/prompt-collector` (the collector's local state and its review queue, which hold prompt
    # text that never goes to the vault) rides along when it exists; `.claude/projects` must exist.
    if ! AGE_REASON=$(age_ready); then
      log "FAILED mode=transcripts stage=encrypt $AGE_REASON"
      exit 3
    fi
    RECIPIENT_NOTE="enc=age recipient=$(grep -m1 -E '^age1' "$RECIPIENTS" | cut -c1-12)"
    if [ ! -d "$HOST_ROOT/.claude/projects" ]; then
      log "FAILED mode=transcripts stage=archive rc=2 missing=.claude/projects"
      exit 2
    fi
    T_MEMBERS=(".claude/projects")
    COLLECTOR_NOTE="collector-state=absent"
    if [ -d "$HOST_ROOT/.claude/prompt-collector" ]; then
      T_MEMBERS+=(".claude/prompt-collector")
      COLLECTOR_NOTE="collector-state=included"
    fi
    FULL_DAYS="${HARNESS_TRANSCRIPTS_FULL_DAYS:-7}"
    last_full=$(ls -1t "$DEST"/govstate-transcripts-full-*.tar.gz.age 2>/dev/null | head -1)
    last_any=$(ls -1t "$DEST"/govstate-transcripts-*.tar.gz.age 2>/dev/null | head -1)
    KIND=full
    NEWER=()
    if [ -n "$last_full" ] && [ $(( $(date +%s) - $(stat -c %Y "$last_full") )) -lt $(( FULL_DAYS * 86400 - 3600 )) ]; then
      KIND=inc
      # One hour of overlap with the archive before: a file appended while that one was written
      # is carried again rather than missed.
      NEWER=(--newer-mtime="@$(( $(stat -c %Y "$last_any") - 3600 ))")
    fi
    OUT="$DEST/govstate-transcripts-$KIND-$STAMP.tar.gz.age"
    PART="$OUT.part"
    INDEX="$PART.index"
    MFDIR="$DEST/.manifest-$STAMP"
    mkdir -p "$MFDIR"
    { echo "# t0=$(date -u +%Y%m%d%H%M.%S)"
      (cd "$HOST_ROOT" && find "${T_MEMBERS[@]}" -type f | LC_ALL=C sort); } > "$MFDIR/TRANSCRIPTS_MANIFEST.txt"
    # no-file-unchanged: an inc otherwise prints "file is unchanged; not dumped" once per skipped file —
    # about fourteen thousand lines a day into cron's discarded mail on 2026-10-09.
    tar czvf - --index-file="$INDEX" --warning=no-file-changed --warning=no-file-unchanged "${NEWER[@]}" -C "$HOST_ROOT" \
        "${T_MEMBERS[@]}" -C "$MFDIR" TRANSCRIPTS_MANIFEST.txt | "$AGE" -R "$RECIPIENTS" -o "$PART"
    PIPE=("${PIPESTATUS[@]}")
    TAR_RC=${PIPE[0]}
    AGE_RC=${PIPE[1]}
    rm -f "$MFDIR/TRANSCRIPTS_MANIFEST.txt"
    rmdir "$MFDIR" 2>/dev/null
    # A session being written while tar reads it makes tar exit 1 (file changed as we read it);
    # that copy is a valid snapshot of the lines written so far. Only 2 and above is a failure.
    if [ "$TAR_RC" -gt 1 ] || [ ! -s "$INDEX" ]; then
      rm -f "$PART" "$INDEX"
      log "FAILED mode=transcripts stage=archive rc=$TAR_RC kind=$KIND"
      exit 2
    fi
    FILES=$(grep -vc -e '/$' -e '^TRANSCRIPTS_MANIFEST.txt$' "$INDEX")
    rm -f "$INDEX"
    if [ "$AGE_RC" -ne 0 ] || [ "$(head -c 21 "$PART" 2>/dev/null)" != "age-encryption.org/v1" ]; then
      rm -f "$PART"
      log "FAILED mode=transcripts stage=encrypt rc=$AGE_RC kind=$KIND"
      exit 3
    fi
    mv -f "$PART" "$OUT"
    chmod 600 "$OUT"
    # Two fulls and the incs since the older of them: every kept inc has a full to sit on.
    ls -1t "$DEST"/govstate-transcripts-full-*.tar.gz.age 2>/dev/null | tail -n +3 | xargs -r rm -f
    oldest_full=$(ls -1t "$DEST"/govstate-transcripts-full-*.tar.gz.age 2>/dev/null | tail -1)
    if [ -n "$oldest_full" ]; then
      for f in "$DEST"/govstate-transcripts-inc-*.tar.gz.age; do
        if [ -e "$f" ] && [ "$f" -ot "$oldest_full" ]; then rm -f "$f"; fi
      done
    fi
    log "OK mode=transcripts kind=$KIND $(basename "$OUT") $(du -h "$OUT" | cut -f1) files=$FILES $RECIPIENT_NOTE $COLLECTOR_NOTE"
    exit 0
    ;;
  candles)
    OUT="$DEST/govstate-candles-$STAMP.tar.gz"
    KEEP=4
    PRUNE_GLOB="$DEST/govstate-candles-*.tar.gz"
    SNAP_NOTE=""
    tar czf "$OUT" --warning=no-file-changed -C "$HOST_ROOT" \
        "$THOMAS/.runtime_governance_state/crypto/candle_archive"
    RC=$?
    ;;
  *)
    echo "usage: $0 [core|candles|transcripts]" >&2; exit 2 ;;
esac

# tar exits 1 when a live append-mode file changed under it (the content is a valid snapshot);
# only 2 and above is a failure.
if [ "$RC" -gt 1 ]; then
  log "FAILED mode=$MODE rc=$RC $SNAP_NOTE"
  rm -f "$OUT"
  exit "$RC"
fi
chmod 600 "$OUT"
ls -1t $PRUNE_GLOB 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm -f
# The plaintext core archives from before encryption match no prune glob now and would stay forever
# with .env inside. Once KEEP encrypted archives exist they are older than every kept restore point
# — exactly what the old retention would have deleted — so remove them then, not before.
if [ "$MODE" = core ] && [ "$(ls -1 $PRUNE_GLOB 2>/dev/null | wc -l)" -ge "$KEEP" ]; then
  LEGACY=$(ls -1 "$DEST"/govstate-[0-9]*.tar.gz 2>/dev/null | wc -l)
  if [ "$LEGACY" -gt 0 ]; then
    rm -f "$DEST"/govstate-[0-9]*.tar.gz
    SNAP_NOTE="$SNAP_NOTE legacy-plaintext-removed=$LEGACY"
  fi
fi
log "OK mode=$MODE $(basename "$OUT") $(du -h "$OUT" | cut -f1) kept=$(ls -1 $PRUNE_GLOB 2>/dev/null | wc -l) $SNAP_NOTE"

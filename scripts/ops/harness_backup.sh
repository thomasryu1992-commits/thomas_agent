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

# --- transcripts / transcripts-trial (Thomas 2026-10-09) -----------------------------------------
# Every archive carries TRANSCRIPTS_MANIFEST.txt, headed by
#   # t0=<UTC, touch -t form>   taken before the file list, so a file made while tar runs is newer
#   # set=  # kind=full|inc  # stamp=  # chain=<stamp of the full this archive builds on>
#   # prev=<stamp of the archive immediately before it, or none>
# then one line per file: path<TAB>size<TAB>mtime (seconds). restore_transcripts.sh walks chain and
# prev to refuse a gap (a missing inc) or an out-of-order set, and compares each restored file's
# mtime with its line to tell a stale copy from one that changed while the backup ran.
stamp_of() { basename "$1" | sed -E 's/.*-(full|inc)-([0-9]{8}-[0-9]{4})\..*/\2/'; }

select_files() {  # $1 = all|human; run inside HOST_ROOT
  if [ "$1" = all ]; then
    find .claude/projects -type f
    [ -d .claude/prompt-collector ] && find .claude/prompt-collector -type f
    return 0
  fi
  find .claude/projects -mindepth 2 -maxdepth 2 -name '*.jsonl' -type f | while IFS= read -r f; do
    if grep -q -F -e '"kind":"human"' -e '"answers"' "$f"; then
      echo "$f"
      [ -d "${f%.jsonl}" ] && find "${f%.jsonl}" -type f
    fi
  done
  find .claude/projects -path '*/memory/*' -type f
  [ -d .claude/prompt-collector ] && find .claude/prompt-collector -type f
  return 0
}

transcripts_run() {  # mode set ext compressor selection
  local mode=$1 set=$2 ext=$3 comp=$4 sel=$5
  if ! AGE_REASON=$(age_ready); then log "FAILED mode=$mode stage=encrypt $AGE_REASON"; exit 3; fi
  local recipient_note
  recipient_note="enc=age recipient=$(grep -m1 -E '^age1' "$RECIPIENTS" | cut -c1-12)"
  if [ ! -d "$HOST_ROOT/.claude/projects" ]; then
    log "FAILED mode=$mode stage=archive rc=2 missing=.claude/projects"; exit 2
  fi
  if [ "$sel" = human ]; then
    local min_gb="${HARNESS_TRIAL_MIN_FREE_GB:-10}" free_kb
    free_kb=$(df -Pk "$DEST" | awk 'NR==2 {print $4}')
    if [ "$free_kb" -lt $(( min_gb * 1024 * 1024 )) ]; then
      log "SKIPPED mode=$mode reason=disk free_gb=$(( free_kb / 1024 / 1024 )) min_gb=$min_gb"; exit 0
    fi
  fi
  local collector_note="collector-state=absent"
  [ -d "$HOST_ROOT/.claude/prompt-collector" ] && collector_note="collector-state=included"
  local full_days="${HARNESS_TRANSCRIPTS_FULL_DAYS:-7}" last_full last_any kind=full chain="$STAMP" prev=none
  local newer=()
  last_full=$(ls -1t "$DEST"/govstate-$set-full-*.$ext.age 2>/dev/null | head -1)
  last_any=$(ls -1t "$DEST"/govstate-$set-*.$ext.age 2>/dev/null | head -1)
  if [ -n "$last_full" ] && [ $(( $(date +%s) - $(stat -c %Y "$last_full") )) -lt $(( full_days * 86400 - 3600 )) ]; then
    kind=inc
    # One hour of overlap with the archive before: a file appended while that one was written
    # is carried again rather than missed.
    newer=(--newer-mtime="@$(( $(stat -c %Y "$last_any") - 3600 ))")
    chain=$(stamp_of "$last_full")
    prev=$(stamp_of "$last_any")
  fi
  local out="$DEST/govstate-$set-$kind-$STAMP.$ext.age"
  local part="$out.part" index="$out.part.index" mfdir="$DEST/.manifest-$set-$STAMP"
  mkdir -p "$mfdir"
  local t0
  t0=$(date -u +%Y%m%d%H%M.%S)
  (cd "$HOST_ROOT" && select_files "$sel") | LC_ALL=C sort -u > "$mfdir/files.txt"
  { printf '# t0=%s\n# set=%s\n# kind=%s\n# stamp=%s\n# chain=%s\n# prev=%s\n' "$t0" "$set" "$kind" "$STAMP" "$chain" "$prev"
    (cd "$HOST_ROOT" && tr '\n' '\0' < "$mfdir/files.txt" | xargs -0 -r stat -c $'%n\t%s\t%Y' 2>/dev/null)
  } > "$mfdir/TRANSCRIPTS_MANIFEST.txt"
  # no-file-unchanged: an inc otherwise prints "file is unchanged; not dumped" once per skipped file.
  tar cvf - --index-file="$index" --warning=no-file-changed --warning=no-file-unchanged "${newer[@]}" \
      -C "$HOST_ROOT" -T "$mfdir/files.txt" -C "$mfdir" TRANSCRIPTS_MANIFEST.txt \
    | $comp | "$AGE" -R "$RECIPIENTS" -o "$part"
  local pipe=("${PIPESTATUS[@]}")
  rm -f "$mfdir/TRANSCRIPTS_MANIFEST.txt" "$mfdir/files.txt"
  rmdir "$mfdir" 2>/dev/null
  # A session being written while tar reads it makes tar exit 1 (file changed as we read it);
  # that copy is a valid snapshot of the lines written so far. Only 2 and above is a failure.
  if [ "${pipe[0]}" -gt 1 ] || [ "${pipe[1]}" -ne 0 ] || [ ! -s "$index" ]; then
    rm -f "$part" "$index"
    log "FAILED mode=$mode stage=archive rc=${pipe[0]}/${pipe[1]} kind=$kind"; exit 2
  fi
  local files
  files=$(grep -vc -e '/$' -e '^TRANSCRIPTS_MANIFEST.txt$' "$index")
  rm -f "$index"
  if [ "${pipe[2]}" -ne 0 ] || [ "$(head -c 21 "$part" 2>/dev/null)" != "age-encryption.org/v1" ]; then
    rm -f "$part"
    log "FAILED mode=$mode stage=encrypt rc=${pipe[2]} kind=$kind"; exit 3
  fi
  mv -f "$part" "$out"
  chmod 600 "$out"
  # Two fulls and the incs since the older of them: every kept inc has a full to sit on.
  ls -1t "$DEST"/govstate-$set-full-*.$ext.age 2>/dev/null | tail -n +3 | xargs -r rm -f
  local oldest_full f
  oldest_full=$(ls -1t "$DEST"/govstate-$set-full-*.$ext.age 2>/dev/null | tail -1)
  if [ -n "$oldest_full" ]; then
    for f in "$DEST"/govstate-$set-inc-*.$ext.age; do
      if [ -e "$f" ] && [ "$f" -ot "$oldest_full" ]; then rm -f "$f"; fi
    done
  fi
  log "OK mode=$mode kind=$kind $(basename "$out") $(du -h "$out" | cut -f1) files=$files chain=$chain prev=$prev sel=$sel $recipient_note $collector_note"
  exit 0
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
    transcripts_run transcripts transcripts tar.gz "gzip -6" all
    ;;
  transcripts-trial)
    # Parallel trial (Thomas 2026-10-09): only sessions a person wrote in (plus their subagents and
    # tool-result files, memory, the collector's state), zstd -3 instead of gzip. Separate names
    # (govstate-trialzst-*), separate log mode; the real `transcripts` run and the watch are untouched.
    # Skipped, not failed, when the disk has less than HARNESS_TRIAL_MIN_FREE_GB (default 10) free.
    transcripts_run transcripts-trial trialzst tar.zst "zstd -3 -T2 -q" human
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
    echo "usage: $0 [core|candles|transcripts|transcripts-trial]" >&2; exit 2 ;;
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

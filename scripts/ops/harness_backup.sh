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
#              thomas_agent/.env                        (the single secret source, 0600 — inside a 0600 archive)
#              hermes-trial/data + docker-compose.yml   (SOUL, MCP shims, skills, cron, memories, sessions,
#                                                        and a consistent SQLite copy via `hermes backup --quick`)
#   candles  weekly Sun 08:15Z, keep 4 — crypto/candle_archive only (unchanged from 2026-08-31).
#
# Member paths are prefixed with the host directory (`thomas_agent/…`, `hermes-trial/…`) so a restore
# knows where each root goes. Archives before 2026-09-04 start at `.runtime_governance_state/` instead.
# File names keep the `govstate-` prefix: the Mac pull (`com.thomas.govstate-pull`) globs on it.
#
# Live SQLite files are NOT tarred (a WAL-mode database copied mid-write is not a backup). The
# assistant's own `hermes backup --quick` copies state.db with the sqlite backup API into
# data/state-snapshots/<stamp>-daily/, and that directory IS tarred. Recreatable caches, installed
# packages and logs are excluded — they are not state.
set -u

MODE="${1:-core}"
DEST="${HARNESS_BACKUP_DEST:-/root/backups/governance-state}"
HOST_ROOT=/root
THOMAS=thomas_agent
HERMES=hermes-trial
STAMP=$(date -u +%Y%m%d-%H%M)
mkdir -p "$DEST" && chmod 700 "$DEST"

log() { echo "$(date -u +%FT%TZ) $*" >> "$DEST/backup.log"; }

case "$MODE" in
  core)
    OUT="$DEST/govstate-$STAMP.tar.gz"
    KEEP=7
    PRUNE_GLOB="$DEST/govstate-[0-9]*.tar.gz"
    # 1. A consistent copy of the assistant's SQLite state, made by the assistant itself (uid 10000).
    #    Non-fatal: if the container is down, the tar still carries everything but state.db and the
    #    log line says so — a backup with a hole you can see beats no backup.
    SNAP_NOTE="hermes-snapshot=ok"
    if ! docker exec -u 10000 hermes /opt/hermes/.venv/bin/hermes backup --quick -l daily >/dev/null 2>&1; then
      SNAP_NOTE="hermes-snapshot=FAILED"
    fi
    # Keep only the newest snapshot directory (33 MB each): older ones are in older archives.
    ls -1dt "$HOST_ROOT/$HERMES/data/state-snapshots"/*/ 2>/dev/null | tail -n +2 | xargs -r rm -rf
    # 2. One archive, two host roots, member paths prefixed with the directory they restore into.
    tar czf "$OUT" --warning=no-file-changed -C "$HOST_ROOT" \
        --exclude="$THOMAS/.runtime_governance_state/crypto/candle_archive" \
        --exclude="$HERMES/data/state.db" --exclude="$HERMES/data/state.db-*" \
        --exclude="$HERMES/data/kanban.db" --exclude="$HERMES/data/kanban.db-*" \
        --exclude="$HERMES/data/cron/executions.db" --exclude="$HERMES/data/cron/executions.db-*" \
        --exclude="$HERMES/data/cache" --exclude="$HERMES/data/lazy-packages" \
        --exclude="$HERMES/data/home" --exclude="$HERMES/data/bin" --exclude="$HERMES/data/.local" \
        --exclude="$HERMES/data/logs" --exclude="$HERMES/data/sandboxes" \
        --exclude="$HERMES/data/image_cache" --exclude="$HERMES/data/audio_cache" \
        --exclude="$HERMES/data/models_dev_cache.json" \
        "$THOMAS/.runtime_governance_state" \
        "$THOMAS/THOMAS_CORE/activations" "$THOMAS/THOMAS_CORE/approvals" \
        "$THOMAS/workspace" "$THOMAS/.env" \
        "$HERMES/data" "$HERMES/docker-compose.yml"
    RC=$?
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
    echo "usage: $0 [core|candles]" >&2; exit 2 ;;
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
log "OK mode=$MODE $(basename "$OUT") $(du -h "$OUT" | cut -f1) kept=$(ls -1 $PRUNE_GLOB 2>/dev/null | wc -l) $SNAP_NOTE"

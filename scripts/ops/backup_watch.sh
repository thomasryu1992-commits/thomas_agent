#!/bin/bash
# Backup watch — the piece that was missing when the daily archive failed four days running
# (2026-09-04..07, #842). Nothing on this host read the backup log or reacted to a failure: cron is
# silent, there is no MTA, and the Mac pull globs `govstate-*` so an absent file looks like an
# uneventful day. This script is the reader. It lives in the Thomas Agent repository at
# scripts/ops/backup_watch.sh and is INSTALLED to /root/backups/backup-watch.sh, run daily at 08:00Z
# — fifteen minutes after the core backup, before the Sunday candle archive.
#
# It says nothing when the backups are current. That is the same rule the assistant's briefing
# follows: a message every morning is a message nobody reads. When something IS wrong it sends one
# Telegram message to the operator's registered control chat — the window where Thomas already acts
# on runtime matters — naming the check that failed, the last log line, and the command that fixes it.
#
# What it checks:
#   1. the newest core archive is younger than 26 h (the daily job plus an hour of slack)
#   2. the last `mode=core` line in backup.log reads OK, not FAILED
#   3. the newest candle archive is younger than 8 days
#   4. health_watch.sh has written its log in the last 30 minutes — the two watches are each
#      other's only observer, because the thing that notices a watch has stopped cannot be itself (the weekly job plus a day)
#
# Secrets: the control-bot token is read from the single secret source (/root/thomas_agent/.env,
# 0600 root) and handed to curl through a config file on stdin, so it never reaches argv or the log.
# The chat id comes from the operator's own registration file rather than a sixth copy of it.
# This script runs as root on the host; it is not a compose service and holds no secret of its own.
#
#   --dry-run   print the message that would be sent (and send nothing); exit 1 if there is one
set -u

DEST="${HARNESS_BACKUP_DEST:-/root/backups/governance-state}"
BACKUP_LOG="$DEST/backup.log"
WATCH_LOG="$DEST/watch.log"
ENV_FILE="${THOMAS_ENV_FILE:-/root/thomas_agent/.env}"
REGISTRATION="${OPERATOR_REGISTRATION:-/root/thomas_agent/.runtime_governance_state/operator_registration.json}"
HEALTH_LOG="${HEALTH_WATCH_LOG:-/root/backups/health-watch.log}"
CURL="${CURL_BIN:-curl}"
CORE_MAX_AGE_H=26
CANDLE_MAX_AGE_D=8
HEALTH_MAX_AGE_MIN=30     # health_watch.sh runs every 10 minutes; three missed runs is a dead watch
DRY_RUN=0
case "${1:-}" in
  "")         ;;
  --dry-run)  DRY_RUN=1 ;;
  *)          echo "usage: $0 [--dry-run]" >&2; exit 64 ;;
esac

NOW=$(date -u +%s)
STAMP=$(date -u +%FT%TZ)
PROBLEMS=()
BACKUP_PROBLEM=0   # a stale sibling watch is not fixed by re-running a backup; only offer that
                   # command when one of the archives is actually the thing that is wrong

log() { echo "$STAMP $*" >> "$WATCH_LOG"; }

# 1. the newest core archive, by age on disk
core=$(ls -1t "$DEST"/govstate-[0-9]*.tar.gz 2>/dev/null | head -1)
if [ -z "$core" ]; then
  PROBLEMS+=("core 아카이브가 하나도 없습니다 ($DEST)")
  BACKUP_PROBLEM=1
else
  age_h=$(( (NOW - $(stat -c %Y "$core")) / 3600 ))
  if [ "$age_h" -gt "$CORE_MAX_AGE_H" ]; then
    PROBLEMS+=("core 아카이브가 ${age_h}시간 지났습니다 (한도 ${CORE_MAX_AGE_H}h) — $(basename "$core")")
    BACKUP_PROBLEM=1
  fi
fi

# 2. what the backup script itself last said about a core run
last_core=$(grep ' mode=core ' "$BACKUP_LOG" 2>/dev/null | tail -1)
if [ -z "$last_core" ]; then
  PROBLEMS+=("backup.log 에 core 실행 기록이 없습니다")
  BACKUP_PROBLEM=1
else
  case "$last_core" in
    *" OK mode=core "*) ;;   # the backup script's own word for a complete archive
    *) PROBLEMS+=("마지막 core 실행이 실패로 끝났습니다: $last_core"); BACKUP_PROBLEM=1 ;;
  esac
fi

# 3. the weekly candle archive
candle=$(ls -1t "$DEST"/govstate-candles-*.tar.gz 2>/dev/null | head -1)
if [ -z "$candle" ]; then
  PROBLEMS+=("candle 아카이브가 하나도 없습니다")
  BACKUP_PROBLEM=1
else
  age_d=$(( (NOW - $(stat -c %Y "$candle")) / 86400 ))
  if [ "$age_d" -gt "$CANDLE_MAX_AGE_D" ]; then
    PROBLEMS+=("candle 아카이브가 ${age_d}일 지났습니다 (한도 ${CANDLE_MAX_AGE_D}d) — $(basename "$candle")")
    BACKUP_PROBLEM=1
  fi
fi

# 4. the other watch. Each of these two scripts is the only thing on this host that would notice
# the other going quiet: cron drops a line, a script is edited into a syntax error, a host is
# restored from an image with a shorter crontab, and the watch that would have told you is the
# thing that is gone. So each reads the other's log and treats a stale one as a problem of its own.
# This one runs daily, so it can only ever be a day late — but a day beats never.
if [ ! -f "$HEALTH_LOG" ]; then
  PROBLEMS+=("헬스 감시 로그가 없습니다 ($HEALTH_LOG) — health-watch.sh가 한 번도 돌지 않았습니다")
else
  health_age_min=$(( (NOW - $(stat -c %Y "$HEALTH_LOG")) / 60 ))
  if [ "$health_age_min" -lt 0 ] || [ "$health_age_min" -gt "$HEALTH_MAX_AGE_MIN" ]; then
    PROBLEMS+=("헬스 감시가 ${health_age_min}분째 아무 기록도 남기지 않았습니다 (10분마다 돌아야 합니다) — crontab -l | grep health-watch")
  fi
fi

if [ "${#PROBLEMS[@]}" -eq 0 ]; then
  [ "$DRY_RUN" -eq 1 ] && echo "OK — 백업 최신 (core $(basename "${core:-none}"), candles $(basename "${candle:-none}"))"
  log "OK checks=3"
  exit 0
fi

MESSAGE="⚠️ 백업 감시 ($STAMP)"$'\n'
for p in "${PROBLEMS[@]}"; do MESSAGE+="- $p"$'\n'; done
MESSAGE+=$'\n'"확인: tail -5 $BACKUP_LOG"$'\n'
if [ "$BACKUP_PROBLEM" -eq 1 ]; then
  MESSAGE+="복구: /root/backups/backup-governance-state.sh core"$'\n'
  MESSAGE+="복원 절차: docs/RUNBOOK_HARNESS_BACKUP_RESTORE.md"
else
  MESSAGE+="이 알림은 백업 자체가 아니라 짝이 되는 감시기에 관한 것입니다 — 위 명령을 먼저 보세요."
fi

if [ "$DRY_RUN" -eq 1 ]; then
  printf '%s\n' "$MESSAGE"
  exit 1
fi

# One message, to the chat the operator itself is registered to. A send failure is recorded rather
# than retried: the next run is in 24 h and a retry loop here would be a second thing to go wrong.
token=$(sed -n 's/^TELEGRAM_BOT_TOKEN=//p' "$ENV_FILE" 2>/dev/null | tail -1)
chat=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["chat_id"])' "$REGISTRATION" 2>/dev/null)
if [ -z "$token" ] || [ -z "$chat" ]; then
  log "PROBLEMS=${#PROBLEMS[@]} send=SKIPPED (no token or no registered chat)"
  exit 2
fi
http=$(printf 'url = "https://api.telegram.org/bot%s/sendMessage"\n' "$token" \
  | "$CURL" -sS -K - -o /dev/null -w '%{http_code}' -m 20 \
      --data-urlencode "chat_id=$chat" --data-urlencode "text=$MESSAGE" 2>/dev/null)
log "PROBLEMS=${#PROBLEMS[@]} send=$http"
[ "$http" = "200" ] || exit 3
exit 1

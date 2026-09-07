#!/bin/bash
# Container health watch — the other half of what was missing when the daily backup failed four
# days unnoticed (#843 covers the backups). Nothing on this host reads `docker inspect` either:
# every service carries a healthcheck that only ever *reports*, and `restart: unless-stopped` does
# not act on `unhealthy`. A wedged poll or a tick hung on a provider call is therefore visible in
# `docker ps` and nowhere else, and only if someone looks.
#
# Lives in the Thomas Agent repository at scripts/ops/health_watch.sh, INSTALLED to
# /root/backups/health-watch.sh, run every 10 minutes from cron.
#
# What it reports, and why each one is shaped the way it is:
#   docker unreachable      the daemon itself does not answer — reported as one line, because
#                           nine "container missing" lines would name the wrong thing
#   missing / not running   the container is gone or exited — checked against the roster below,
#                           which `tests/test_ops_health_watch.py` pins to docker-compose.yml
#   unhealthy               docker's own verdict: three consecutive probe failures, ~3 minutes
#   stuck starting          `starting` is normal for the first 15-180 s after a deploy; past
#                           STUCK_START_MINUTES it means the probe has never once passed
#   crash loop              RestartCount rose since the last run. A manual `docker restart` does
#                           NOT touch this counter and `up -d` resets it with a new container id,
#                           so a rise is the restart policy acting on a process that died
#   OOM killed              .State.OOMKilled — this host runs with ~1.5 GiB available and three
#                           services have mem_limits
#   backup watch quiet      backup_watch.sh has not written its log for over a day. The two watches
#                           are each other's only observer: what notices that a watch has stopped
#                           cannot be that watch. This one runs every ten minutes, so it is the
#                           faster half of that pair
#
# Two rules keep it from becoming noise, which is the way a watch dies:
#   * Each problem must be seen CONFIRM_RUNS times before it is sent — counted per problem, never
#     per problem set (a widening cascade would postpone the alert every time it widened), and not
#     required to be consecutive (an intermittent probe reads healthy on half the samples, and
#     requiring consecutive sightings never confirms it). A key survives MISS_TOLERANCE clean runs
#     before it is forgotten. A deploy recreates all nine containers in about a minute, which at a
#     10-minute cadence rarely lands in even one check and almost never in two. Crash loops and OOM
#     kills skip the wait: they are events rather than states, and a deploy does not produce them.
#   * It speaks on the EDGE, not the level: once a problem set is reported it stays quiet until the
#     set changes or clears, and it says so when it clears. An alert repeated every ten minutes is
#     an alert nobody reads.
#
# Silence for planned work: `touch /root/backups/health-watch.silence` suppresses sending for
# SILENCE_MAX_HOURS hours (the file's own age — a forgotten silence file cannot mute this forever).
#
# Secrets: the control-bot token is read from the single secret source and handed to curl through a
# config file on stdin, never argv and never a log; the chat id comes from the operator's own
# registration file. Root host script, not a compose service: no service's secret projection changes.
#
#   --dry-run   print what would be sent and change no state; exit 1 if there is a message
#
# Exit: 0 nothing to say · 1 a problem stands (sent, or waiting to be re-sent) · anything the
# send itself returns is only in the log — cron does not read it.
set -u

STATE_DIR="${HEALTH_WATCH_STATE_DIR:-/root/backups}"
STATE="$STATE_DIR/health-watch.state"
WATCH_LOG="$STATE_DIR/health-watch.log"
SILENCE="$STATE_DIR/health-watch.silence"
ENV_FILE="${THOMAS_ENV_FILE:-/root/thomas_agent/.env}"
REGISTRATION="${OPERATOR_REGISTRATION:-/root/thomas_agent/.runtime_governance_state/operator_registration.json}"
DOCKER="${DOCKER_BIN:-docker}"
CURL="${CURL_BIN:-curl}"
CONFIRM_RUNS="${HEALTH_WATCH_CONFIRM_RUNS:-2}"
BACKUP_WATCH_LOG="${BACKUP_WATCH_LOG:-/root/backups/governance-state/watch.log}"
BACKUP_WATCH_MAX_AGE_H=26   # backup_watch.sh runs daily at 08:00Z
MISS_TOLERANCE=2          # clean runs in a row before a pending problem is forgotten
STUCK_START_MINUTES=15
SILENCE_MAX_HOURS=6

# The roster. Container names, not compose service names (the compose file sets container_name on
# every service). tests/test_ops_health_watch.py fails if this list and docker-compose.yml diverge.
SERVICES=(
  thomas-operator
  thomas-scheduler
  thomas-scheduler-maint
  thomas-read-bridge
  thomas-dispatch-bridge
  thomas-pipeline-worker
  thomas-knowledge-bridge
  thomas-switch-bridge
  hermes
)

SERVICES_ALL=("${SERVICES[@]}")   # SERVICES is emptied when the daemon cannot answer

DRY_RUN=0
case "${1:-}" in
  "")         ;;
  --dry-run)  DRY_RUN=1 ;;
  *)          echo "usage: $0 [--dry-run]" >&2; exit 64 ;;   # a typo'd rehearsal must not page
esac
STAMP=$(date -u +%FT%TZ)
NOW=$(date -u +%s)

log() { [ "$DRY_RUN" -eq 1 ] || echo "$STAMP $*" >> "$WATCH_LOG"; }
state_get() { sed -n "s/^$1=//p" "$STATE" 2>/dev/null | tail -1; }
# A counter read back from the state file is only a number if nothing has edited that file by hand.
# Anything else would reach `$(( ))`, and under `set -u` that kills the shell before it can write
# state or log a line — every later run would die in the same place, silently, forever.
number() { case "${1:-}" in ''|*[!0-9]*) echo 0 ;; *) echo "$1" ;; esac; }

# A wedged daemon is the case this watch exists for, so it must not be the case that hangs it: an
# unbounded `docker inspect` would leave one process per cron tick and never reach a message.
docker_q() { timeout "${1}" "$DOCKER" "${@:2}" 2>/dev/null; }

# One run at a time. If a previous run is still stuck, say so rather than piling up behind it.
# `exec 9>file 2>/dev/null` would look equivalent and is not: with no command, exec's redirections
# apply to the shell for the rest of its life, so that `2>/dev/null` silently discarded every error
# this script could ever report — including the state-write failure below.
if : > "$STATE_DIR/.health-watch.lock" 2>/dev/null; then
  exec 9>"$STATE_DIR/.health-watch.lock"
  if command -v flock >/dev/null 2>&1 && ! flock -n 9; then
    log "SKIPPED (a previous run still holds the lock — check for a hung docker)"
    exit 0
  fi
fi

KEYS=()                  # stable identifiers; the signature is built from these
declare -A PROBLEM_TEXT  # key -> the line Thomas reads
declare -A IMMEDIATE     # key -> 1 when it must not wait for confirmation (an event, not a state)
NEW_RESTARTS=()          # id:count lines to carry into the next run

note() {   # note <key> <text> [immediate]
  KEYS+=("$1"); PROBLEM_TEXT["$1"]="$2"
  [ "${3:-}" = "immediate" ] && IMMEDIATE["$1"]=1
  return 0
}

# Ask the daemon one question first. Without this, a daemon that is down or wedged reports as nine
# missing containers — true in a sense, and the least useful sentence available: the containers are
# probably fine and docker is not. One line beats nine.
if ! docker_q 10 version --format '{{.Server.Version}}' >/dev/null; then
  if [ "$?" -eq 124 ]; then
    note "docker:hung" "docker 데몬이 10초 안에 응답하지 않습니다 — 호스트 IO·부하 확인 (컨테이너는 살아 있을 수 있습니다)"
  else
    note "docker:unreachable" "docker 데몬이 응답하지 않습니다 — 컨테이너 상태를 읽을 수 없습니다 (systemctl status docker)"
  fi
  SERVICES=()   # it cannot answer for any of them; asking nine times would only repeat the failure
fi

for name in "${SERVICES[@]+"${SERVICES[@]}"}"; do
  # One inspect per container: state, health, restart count, oom flag, id, start time.
  line=$(docker_q 15 inspect "$name" --format \
    '{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}|{{.RestartCount}}|{{.State.OOMKilled}}|{{.Id}}|{{.State.StartedAt}}')
  if [ -z "$line" ]; then
    note "$name:missing" "$name — 컨테이너가 없습니다 (docker inspect 실패)"
    continue
  fi
  IFS='|' read -r status health restarts oomkilled cid started <<< "$line"
  short_id="${cid:0:12}"
  NEW_RESTARTS+=("$name=$short_id:$restarts")

  # These two are read before the status branch on purpose: a container in a crash loop is usually
  # sampled as `restarting`, and skipping them there would drop the very lines that name the cause
  # and drop the exemption from the confirmation wait with them.
  if [ "$oomkilled" = "true" ]; then
    note "$name:oomkilled" "$name — OOM으로 종료된 적이 있습니다 (mem_limit 확인)" immediate
  fi
  # Same container id and a higher count is the restart policy at work; a different id means
  # `up -d` recreated it and the counter legitimately went back to zero.
  prev=$(state_get "restart_$name")
  prev_id="${prev%%:*}"; prev_count=$(number "${prev##*:}")
  if [ -n "$prev" ] && [ "$prev_id" = "$short_id" ] && [ "$(number "$restarts")" -gt "$prev_count" ]; then
    note "$name:restarted" "$name — 재시작 $(( $(number "$restarts") - prev_count ))회 (누적 $restarts, 크래시 루프)" immediate
  fi

  if [ "$status" != "running" ]; then
    note "$name:$status" "$name — 상태 $status (running 아님)"
    continue
  fi

  case "$health" in
    healthy|none) ;;
    unhealthy)
      note "$name:unhealthy" "$name — unhealthy (헬스체크 3회 연속 실패)" ;;
    starting)
      started_epoch=$(date -u -d "$started" +%s 2>/dev/null || echo "$NOW")
      up_min=$(( (NOW - started_epoch) / 60 ))
      if [ "$up_min" -ge "$STUCK_START_MINUTES" ]; then
        note "$name:stuck-starting" "$name — ${up_min}분째 starting (헬스체크가 한 번도 통과하지 못했습니다)"
      fi ;;
    *)
      note "$name:health-$health" "$name — 헬스 상태 $health" ;;
  esac
done

# The other watch. These two scripts are each other's only observer: cron drops a line, a script is
# edited into a syntax error, a host comes back from an image with a shorter crontab — and the watch
# that would have told you is the thing that is gone. Reading a log's mtime needs no docker, so this
# runs even when the daemon could not be reached.
if [ ! -f "$BACKUP_WATCH_LOG" ]; then
  note "watch:backup-missing" "백업 감시 로그가 없습니다 ($BACKUP_WATCH_LOG) — backup-watch.sh가 한 번도 돌지 않았습니다"
else
  bw_age_h=$(( (NOW - $(number "$(stat -c %Y "$BACKUP_WATCH_LOG" 2>/dev/null)")) / 3600 ))
  if [ "$bw_age_h" -lt 0 ] || [ "$bw_age_h" -gt "$BACKUP_WATCH_MAX_AGE_H" ]; then
    note "watch:backup-stale" "백업 감시가 ${bw_age_h}시간째 아무 기록도 남기지 않았습니다 (매일 08:00Z) — crontab -l | grep backup-watch"
  fi
fi

# Confirmation is counted PER PROBLEM, and a problem is not required to be seen on consecutive
# runs. Counting per problem *set* stayed silent through a widening cascade (each new failure reset
# the counter); requiring consecutive sightings stayed silent through a flapping one, because a
# health probe that fails intermittently reads healthy on half the samples and the count went back
# to zero every time. So each key carries `count:misses`: a sighting raises the count, a clean run
# raises the misses, and the key is forgotten only after MISS_TOLERANCE clean runs in a row.
declare -A SEEN_NOW
CONFIRMED=()
NEW_PENDING=()
for key in "${KEYS[@]+"${KEYS[@]}"}"; do
  SEEN_NOW["$key"]=1
  if [ "${IMMEDIATE[$key]:-0}" = "1" ]; then
    CONFIRMED+=("$key"); continue
  fi
  raw=$(state_get "pending_$key")
  seen=$(( $(number "${raw%%:*}") + 1 ))
  NEW_PENDING+=("$key=$seen:0")
  [ "$seen" -ge "$CONFIRM_RUNS" ] && CONFIRMED+=("$key")
done

# Keys that were pending and are not here this time: keep them, one miss older.
while IFS= read -r old_key; do
  [ -n "$old_key" ] || continue
  [ -n "${SEEN_NOW[$old_key]:-}" ] && continue
  raw=$(state_get "pending_$old_key")
  misses=$(( $(number "${raw##*:}") + 1 ))
  [ "$misses" -lt "$MISS_TOLERANCE" ] && NEW_PENDING+=("$old_key=$(number "${raw%%:*}"):$misses")
done < <(sed -n 's/^pending_\(.*\)=.*/\1/p' "$STATE" 2>/dev/null)

SIG=$(printf '%s\n' "${CONFIRMED[@]+"${CONFIRMED[@]}"}" | sort | paste -sd, -)
REPORTED=$(state_get reported_sig)

write_state() {
  local reported="$1" name
  [ "$DRY_RUN" -eq 1 ] && return 0
  {
    echo "reported_sig=$reported"
    for kv in "${NEW_PENDING[@]+"${NEW_PENDING[@]}"}"; do echo "pending_${kv%%=*}=${kv#*=}"; done
    for r in "${NEW_RESTARTS[@]+"${NEW_RESTARTS[@]}"}"; do echo "restart_${r%%=*}=${r#*=}"; done
    # Carry forward the restart baseline of any container this run could not inspect. Rewriting
    # only what was observed would drop all nine on a single daemon outage, and the first run
    # after it would have nothing to compare against — a crash loop across that gap would vanish.
    for name in "${SERVICES_ALL[@]}"; do
      case " ${NEW_RESTARTS[*]+"${NEW_RESTARTS[*]}"} " in *" $name="*) continue ;; esac
      old=$(state_get "restart_$name"); [ -n "$old" ] && echo "restart_$name=$old"
    done
  } > "$STATE.tmp" && mv "$STATE.tmp" "$STATE" && return 0
  # A watch that cannot remember decides nothing: every problem restarts its count each run and
  # nothing is ever confirmed. Say so where cron will mail it, and in the log.
  rm -rf "$STATE.tmp" 2>/dev/null
  echo "health_watch: STATE WRITE FAILED ($STATE) — confirmation cannot advance" >&2
  echo "$STAMP STATE-WRITE-FAILED" >> "$WATCH_LOG" 2>/dev/null || true
  return 1
}

send() {
  local text="$1" token chat http
  # A stubbed docker with a real curl is someone exercising the script, and the message it would
  # send describes containers that are not the real ones. Learned the hard way on 2026-09-07: a
  # scratch test with a fake `docker` and the live token put two false outage alerts on Thomas's
  # phone. Tests stub curl as well, so they are unaffected; DOCKER_BIN pointing at the real docker
  # (a cron line that spells the path out) is not a stub and is not blocked.
  if [ -n "${DOCKER_BIN:-}" ] && [ -z "${CURL_BIN:-}" ] \
     && [ "$(command -v "$DOCKER" 2>/dev/null)" != "$(command -v docker 2>/dev/null)" ]; then
    log "send=BLOCKED (stubbed docker, real curl — refusing to page from a test) sig=$SIG"
    return 4
  fi
  if [ -f "$SILENCE" ]; then
    local age_h=$(( (NOW - $(number "$(stat -c %Y "$SILENCE" 2>/dev/null)")) / 3600 ))
    # A future mtime (a mistyped `touch -d`, a clock that stepped back) would otherwise mute this
    # for as long as the date is wrong, which is the one thing the ceiling exists to prevent.
    if [ "$age_h" -ge 0 ] && [ "$age_h" -lt "$SILENCE_MAX_HOURS" ]; then
      log "SUPPRESSED (silence file ${age_h}h old) sig=$SIG"; return 3
    fi
  fi
  token=$(sed -n 's/^TELEGRAM_BOT_TOKEN=//p' "$ENV_FILE" 2>/dev/null | tail -1)
  chat=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["chat_id"])' "$REGISTRATION" 2>/dev/null)
  if [ -z "$token" ] || [ -z "$chat" ]; then
    log "send=SKIPPED (no token or no registered chat) sig=$SIG"; return 2
  fi
  http=$(printf 'url = "https://api.telegram.org/bot%s/sendMessage"\n' "$token" \
    | "$CURL" -sS -K - -o /dev/null -w '%{http_code}' -m 20 \
        --data-urlencode "chat_id=$chat" --data-urlencode "text=$text" 2>/dev/null)
  log "send=$http sig=$SIG"
  [ "$http" = "200" ]
}

# An event key records something that happened, not something that is wrong now, so its absence is
# not a recovery: a worker that dies once an hour would otherwise alternate ⚠️ and a ✅ claiming all
# nine are well, ten minutes before it dies again.
only_events() {
  local sig="$1" k
  [ -n "$sig" ] || return 1
  for k in ${sig//,/ }; do
    case "$k" in *:restarted|*:oomkilled) ;; *) return 1 ;; esac
  done
  return 0
}

# --- everything is well --------------------------------------------------------------------------
# "Well" means nothing is wrong AND nothing is still being remembered. Announcing recovery the
# moment a problem is out of sight would make a flapping container alternate ⚠️ and ✅ every twenty
# minutes; waiting until its key has aged out says it once, when it is actually over.
if [ "${#KEYS[@]}" -eq 0 ] && [ "${#NEW_PENDING[@]}" -eq 0 ]; then
  if [ -n "$REPORTED" ] && ! only_events "$REPORTED"; then
    msg="✅ 컨테이너 복구 ($STAMP)"$'\n\n'"직전 문제가 해소됐습니다: $REPORTED"$'\n'"지금 ${#SERVICES_ALL[@]}개 전부 정상입니다."
    if [ "$DRY_RUN" -eq 1 ]; then printf '%s\n' "$msg"; exit 1; fi
    if send "$msg"; then write_state ""; else write_state "$REPORTED"; fi
    exit 0
  fi
  [ "$DRY_RUN" -eq 1 ] && echo "OK — ${#SERVICES_ALL[@]}개 컨테이너 정상"
  log "OK services=${#SERVICES_ALL[@]}"; write_state ""; exit 0
fi

# --- nothing wrong right now, but a recent problem is still within its miss tolerance ---------------
if [ "${#KEYS[@]}" -eq 0 ]; then
  [ "$DRY_RUN" -eq 1 ] && echo "OK — ${#SERVICES_ALL[@]}개 정상 (직전 문제 ${#NEW_PENDING[@]}건은 아직 기억 중)"
  log "OK services=${#SERVICES_ALL[@]} remembering=${#NEW_PENDING[@]}"; write_state "$REPORTED"
  [ -n "$REPORTED" ] && exit 1
  exit 0
fi

# --- wrong, but not yet seen often enough ------------------------------------------------------------
if [ "${#CONFIRMED[@]}" -eq 0 ]; then
  [ "$DRY_RUN" -eq 1 ] && echo "확인 대기 ${#KEYS[@]}건 (${CONFIRM_RUNS}회 관측 필요): $(printf '%s ' "${KEYS[@]}")"
  log "PENDING keys=${#KEYS[@]} confirmed=0"; write_state "$REPORTED"
  [ -n "$REPORTED" ] && exit 1        # a reported problem still stands; 0 would read as all clear
  exit 0
fi

# --- already told, and the confirmed set has not changed --------------------------------------------
if [ "$SIG" = "$REPORTED" ]; then
  [ "$DRY_RUN" -eq 1 ] && echo "이미 보고한 문제 (재발송 없음): $SIG"
  log "UNCHANGED sig=$SIG"; write_state "$REPORTED"; exit 1
fi

MESSAGE="⚠️ 컨테이너 헬스 ($STAMP)"$'\n'
while IFS= read -r key; do MESSAGE+="- ${PROBLEM_TEXT[$key]}"$'\n'; done < <(printf '%s\n' "${CONFIRMED[@]}" | sort)
MESSAGE+=$'\n'"확인: docker ps --format '{{.Names}} {{.Status}}'"$'\n'
MESSAGE+="로그: docker logs <이름> --tail 50"$'\n'
MESSAGE+="배포 절차: docs/DEPLOYMENT.md · 계획된 작업이면 touch /root/backups/health-watch.silence"

if [ "$DRY_RUN" -eq 1 ]; then printf '%s\n' "$MESSAGE"; exit 1; fi
# Not delivered — a refusal, a suppression, a 500 — is not the same as told: keep the previous
# reported set so the next run tries again instead of filing an undelivered outage as announced.
if send "$MESSAGE"; then write_state "$SIG"; else write_state "$REPORTED"; fi
exit 1

#!/bin/bash
# Restore the Claude Code transcripts from the encrypted `transcripts` archives (harness_backup.sh,
# Thomas 2026-10-09). Runs where the private key is — the Mac — so it keeps to bash 3.2 and the
# BSD/GNU tools both systems share (no mapfile, no `stat`, no `date -d`).
#
#   restore_transcripts.sh <archive-dir> <target-dir> [--until YYYYmmdd-HHMM] [--identity <age key>]
#
# The order is the point of this script. An archive is a full (every file) or an inc (only the
# files changed since the archive before it). A restore to a point P takes:
#   1. the newest FULL whose stamp is at or before P,
#   2. then only the INCs after that full and at or before P, oldest first.
# An inc older than the chosen full is never applied: applied after the full it would put older
# copies of files back over newer ones (the first RUNBOOK example did exactly that).
#
# An inc carries changed files, not deletions, so a file deleted after the full would come back.
# Every archive also carries TRANSCRIPTS_MANIFEST.txt — the files that existed when it was made,
# headed by `# t0=<UTC, touch -t form>`. After the last archive is applied, a restored file that is
# missing from that manifest and not newer than t0 was deleted before that backup, and is removed.
# A file newer than t0 appeared while the backup ran and is kept. Archives made before the manifest
# existed (2026-10-09 07:32 and earlier) skip this step, and the summary says so.
#
# The integrity check that ends the run: every file the final manifest lists must exist in the
# target. Exit 0 = restored and complete, 2 = no usable full, 3 = a decrypt or untar failed,
# 4 = restored but the manifest lists files the archives did not carry, 64 = usage.
set -u
AGE="${AGE_BIN:-age}"
[ $# -ge 2 ] || { echo "usage: $0 <archive-dir> <target-dir> [--until YYYYmmdd-HHMM] [--identity <key>]" >&2; exit 64; }
DIR=$1; TARGET=$2; shift 2
UNTIL=""
KEY="${GOVSTATE_AGE_KEY:-$HOME/.config/thomas-govstate/age-key.txt}"
while [ $# -gt 0 ]; do
  case "$1" in
    --until)    UNTIL=$2; shift 2 ;;
    --identity) KEY=$2; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 64 ;;
  esac
done

# "<stamp> <kind> <path>", oldest first. Archive paths must not contain spaces.
LIST=$(for f in "$DIR"/govstate-transcripts-full-*.tar.gz.age "$DIR"/govstate-transcripts-inc-*.tar.gz.age; do
         [ -e "$f" ] || continue
         b=$(basename "$f")
         kind=$(echo "$b" | sed -E 's/^govstate-transcripts-(full|inc)-.*/\1/')
         stamp=$(echo "$b" | sed -E 's/^govstate-transcripts-(full|inc)-([0-9]{8}-[0-9]{4})\.tar\.gz\.age$/\2/')
         echo "$stamp $kind $f"
       done | LC_ALL=C sort)
[ -n "$LIST" ] || { echo "no transcripts archives in $DIR" >&2; exit 2; }
[ -n "$UNTIL" ] || UNTIL=$(echo "$LIST" | tail -1 | cut -d' ' -f1)
FULL=$(echo "$LIST" | awk -v u="$UNTIL" '$2 == "full" && $1 <= u' | tail -1)
[ -n "$FULL" ] || { echo "no full archive at or before $UNTIL" >&2; exit 2; }
FULL_STAMP=$(echo "$FULL" | cut -d' ' -f1)
INCS=$(echo "$LIST" | awk -v f="$FULL_STAMP" -v u="$UNTIL" '$2 == "inc" && $1 > f && $1 <= u')
SKIPPED=$(echo "$LIST" | awk -v f="$FULL_STAMP" '$2 == "inc" && $1 < f' | grep -c . )

mkdir -p "$TARGET"
APPLIED=0
for line in "$FULL" $( [ -n "$INCS" ] && echo "$INCS" | tr ' ' '|' ); do
  path=$(echo "$line" | tr '|' ' ' | cut -d' ' -f3)
  rm -f "$TARGET/TRANSCRIPTS_MANIFEST.txt"          # the manifest that survives is the last archive's own
  "$AGE" -d -i "$KEY" "$path" | tar xzf - -C "$TARGET"
  rc=("${PIPESTATUS[@]}")
  if [ "${rc[0]}" -ne 0 ] || [ "${rc[1]}" -ne 0 ]; then
    echo "FAILED at $(basename "$path"): age rc=${rc[0]} tar rc=${rc[1]}" >&2
    exit 3
  fi
  APPLIED=$((APPLIED + 1))
done

MANIFEST="$TARGET/TRANSCRIPTS_MANIFEST.txt"
DELETED=0; MISSING=0
if [ -f "$MANIFEST" ]; then
  T0=$(sed -n '1s/^# t0=//p' "$MANIFEST")
  TZ=UTC touch -t "$T0" "$TARGET/.t0"
  grep -v '^#' "$MANIFEST" | LC_ALL=C sort > "$TARGET/.listed"
  (cd "$TARGET" && find .claude -type f ! -newer .t0 2>/dev/null | LC_ALL=C sort) > "$TARGET/.old"
  LC_ALL=C comm -23 "$TARGET/.old" "$TARGET/.listed" > "$TARGET/.gone"
  while IFS= read -r f; do
    rm -f "$TARGET/$f" && DELETED=$((DELETED + 1))
  done < "$TARGET/.gone"
  (cd "$TARGET" && find .claude -type f 2>/dev/null | LC_ALL=C sort) > "$TARGET/.now"
  MISSING=$(LC_ALL=C comm -13 "$TARGET/.now" "$TARGET/.listed" | grep -c .)
  rm -f "$TARGET/.t0" "$TARGET/.listed" "$TARGET/.old" "$TARGET/.gone" "$TARGET/.now"
  NOTE="deleted=$DELETED missing=$MISSING"
else
  NOTE="manifest=absent (archives before 2026-10-09 manifests) — deletions not applied, integrity not checked"
fi
echo "restored until=$UNTIL full=$FULL_STAMP archives=$APPLIED skipped-older-incs=$SKIPPED $NOTE"
[ "$MISSING" -eq 0 ] || exit 4
exit 0

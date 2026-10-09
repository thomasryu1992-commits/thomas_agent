#!/bin/bash
# Restore the Claude Code transcripts from the encrypted archives that harness_backup.sh writes
# (`transcripts`, gzip; `transcripts-trial`, zstd). Runs where the private key is — the Mac — so it
# keeps to bash 3.2 and the tools both systems share (no mapfile, no GNU stat/date; perl reads mtimes).
#
#   restore_transcripts.sh <archive-dir> <target-dir> [--until YYYYmmdd-HHMM] [--identity <age key>]
#                          [--set transcripts|trialzst|fullzst] [--expect-head YYYYmmdd-HHMM]
#
# Order: the newest FULL at or before the point, then only the INCs after it, oldest first. An inc older
# than the chosen full is never applied (it would put older copies over newer ones).
#
# What the archives let this script prove, and what they do not:
#   - Chain. Every archive's TRANSCRIPTS_MANIFEST.txt names its full (`# chain=`) and the archive just
#     before it (`# prev=`). A missing middle inc, an inc from another chain or an archive renamed out of
#     order breaks the walk → exit 5. FULL + INC-A + INC-C without INC-B is refused even when every file
#     is present.
#   - The last inc. Nothing inside the archives can show that a newer inc existed and is missing. Pass
#     the newest stamp the host logged (`grep 'mode=transcripts ' backup.log | tail -1`) as
#     --expect-head; without it the summary says the tail is unchecked.
#   - Files. The final manifest lists every file with its size and mtime when that backup ran:
#       a listed file that is absent                      → missing   → exit 4
#       a restored copy older than its line (mtime)      → stale     → exit 6
#       a restored copy newer than its line              → changed while the backup ran — expected, counted
#       a restored file not listed and not newer than t0 → deleted before that backup — removed again
#     Size and mtime, not checksums: a same-second rewrite to the same size would pass. The
#     transcripts are append-only logs, so that case is not expected.
#   - Content. The core assets (the prompt collector's state and the per-project memory) carry a sha256
#     in the manifest (2026-10-09 final review). Each restored one is hashed and compared; a copy that
#     changed while the backup ran (newer mtime) is skipped, not failed. A mismatch → BROKEN, exit 8.
#     The conversation logs are not hashed (GBs a day): for them size + mtime + the chain is the proof.
#   - Archives from before the chain headers (2026-10-09 08:20 and earlier) restore, but the run ends
#     with exit 7 (restored, not verifiable) unless a later failure says more.
# Verdicts (exit 0 for both verified ones, so existing callers keep working):
#   CONTENT_VERIFIED  chain walked, tail ok if asked, nothing missing or stale, every core-asset hash matched
#   CHAIN_VERIFIED    the same, but no core-asset hash to check (none in scope or a manifest without them)
# Exit: 0 verified · 2 no usable full · 3 decrypt/decompress/untar failed · 4 missing · 5 chain broken ·
#       6 stale · 7 restored but not verifiable · 8 core-asset content mismatch · 64 usage. With several
#       problems the first in this order wins: 3, 5, 8, 6, 4, 7.
set -u
AGE="${AGE_BIN:-age}"
[ $# -ge 2 ] || { echo "usage: $0 <archive-dir> <target-dir> [--until S] [--identity K] [--set transcripts|trialzst|fullzst] [--expect-head S]" >&2; exit 64; }
DIR=$1; TARGET=$2; shift 2
UNTIL=""; SET=transcripts; HEAD_WANT=""
KEY="${GOVSTATE_AGE_KEY:-$HOME/.config/thomas-govstate/age-key.txt}"
while [ $# -gt 0 ]; do
  case "$1" in
    --until)       UNTIL=$2; shift 2 ;;
    --identity)    KEY=$2; shift 2 ;;
    --set)         SET=$2; shift 2 ;;
    --expect-head) HEAD_WANT=$2; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 64 ;;
  esac
done
case "$SET" in
  transcripts) EXT=tar.gz ;;
  trialzst|fullzst) EXT=tar.zst ;;
  *) echo "unknown set: $SET" >&2; exit 64 ;;
esac

# "<stamp> <kind> <path>", oldest first. Archive paths must not contain spaces.
LIST=$(for f in "$DIR"/govstate-$SET-full-*.$EXT.age "$DIR"/govstate-$SET-inc-*.$EXT.age; do
         [ -e "$f" ] || continue
         b=$(basename "$f")
         kind=$(echo "$b" | sed -E "s/^govstate-$SET-(full|inc)-.*/\1/")
         stamp=$(echo "$b" | sed -E "s/^govstate-$SET-(full|inc)-([0-9]{8}-[0-9]{4})\..*/\2/")
         echo "$stamp $kind $f"
       done | LC_ALL=C sort)
[ -n "$LIST" ] || { echo "no $SET archives in $DIR" >&2; exit 2; }
[ -n "$UNTIL" ] || UNTIL=$(echo "$LIST" | tail -1 | cut -d' ' -f1)
FULL=$(echo "$LIST" | awk -v u="$UNTIL" '$2 == "full" && $1 <= u' | tail -1)
[ -n "$FULL" ] || { echo "no full archive at or before $UNTIL" >&2; exit 2; }
FULL_STAMP=$(echo "$FULL" | cut -d' ' -f1)
INCS=$(echo "$LIST" | awk -v f="$FULL_STAMP" -v u="$UNTIL" '$2 == "inc" && $1 > f && $1 <= u')
SKIPPED=$(echo "$LIST" | awk -v f="$FULL_STAMP" '$2 == "inc" && $1 < f' | grep -c . )

header() { sed -n "s/^# $1=//p" "$TARGET/TRANSCRIPTS_MANIFEST.txt" | head -1; }

mkdir -p "$TARGET"
APPLIED=0; LAST=""; BROKEN=""; LEGACY=0
for line in "$FULL" $( [ -n "$INCS" ] && echo "$INCS" | tr ' ' '|' ); do
  line=$(echo "$line" | tr '|' ' ')
  stamp=$(echo "$line" | cut -d' ' -f1); kind=$(echo "$line" | cut -d' ' -f2); path=$(echo "$line" | cut -d' ' -f3)
  rm -f "$TARGET/TRANSCRIPTS_MANIFEST.txt"          # the manifest that survives is the last archive's own
  if [ "$EXT" = tar.zst ]; then
    "$AGE" -d -i "$KEY" "$path" | zstd -dc -q | tar xf - -C "$TARGET"
  else
    "$AGE" -d -i "$KEY" "$path" | tar xzf - -C "$TARGET"
  fi
  rc=("${PIPESTATUS[@]}")
  for x in "${rc[@]}"; do
    if [ "$x" -ne 0 ]; then echo "FAILED at $(basename "$path"): rc=${rc[*]}" >&2; exit 3; fi
  done
  if [ ! -f "$TARGET/TRANSCRIPTS_MANIFEST.txt" ] || [ -z "$(header chain)" ]; then
    LEGACY=1                                     # made before the chain headers: cannot be walked
  else
    h_stamp=$(header stamp); h_chain=$(header chain); h_prev=$(header prev)
    if [ "$h_stamp" != "$stamp" ]; then
      BROKEN="${BROKEN:-archive $(basename "$path") says stamp=$h_stamp (renamed or out of order)}"
    elif [ "$kind" = full ] && [ "$h_chain" != "$stamp" ]; then
      BROKEN="${BROKEN:-full $stamp names chain=$h_chain}"
    elif [ "$kind" = inc ] && [ "$h_chain" != "$FULL_STAMP" ]; then
      BROKEN="${BROKEN:-inc $stamp belongs to chain $h_chain, not $FULL_STAMP}"
    elif [ "$kind" = inc ] && [ -n "$LAST" ] && [ "$h_prev" != "$LAST" ]; then
      BROKEN="${BROKEN:-inc $stamp follows $h_prev, which is missing (last applied $LAST)}"
    fi
  fi
  LAST=$stamp
  APPLIED=$((APPLIED + 1))
done
HEAD_NOTE="tail=unchecked (pass --expect-head)"
if [ -n "$HEAD_WANT" ]; then
  if [ "$HEAD_WANT" = "$LAST" ]; then HEAD_NOTE="tail=ok"
  else HEAD_NOTE="tail=MISSING"; BROKEN="${BROKEN:-the newest archive applied is $LAST, the host logged $HEAD_WANT}"; fi
fi

MANIFEST="$TARGET/TRANSCRIPTS_MANIFEST.txt"
DELETED=0; MISSING=0; STALE=0; CHANGED=0; HASH_OK=0; HASH_BAD=0; HASH_SKIP=0
if [ -f "$MANIFEST" ]; then
  T0=$(header t0)
  TZ=UTC touch -t "$T0" "$TARGET/.t0"
  grep -v '^#' "$MANIFEST" | cut -f1 | LC_ALL=C sort > "$TARGET/.listed"
  (cd "$TARGET" && find .claude -type f ! -newer .t0 2>/dev/null | LC_ALL=C sort) > "$TARGET/.old"
  LC_ALL=C comm -23 "$TARGET/.old" "$TARGET/.listed" > "$TARGET/.gone"
  while IFS= read -r f; do
    rm -f "$TARGET/$f" && DELETED=$((DELETED + 1))
  done < "$TARGET/.gone"
  COUNTS=$(grep -v '^#' "$MANIFEST" | T="$TARGET" perl -MDigest::SHA -ne '
      chomp; my ($p, $s, $m, $h) = split /\t/;
      my @st = stat("$ENV{T}/$p");
      if (!@st) { $miss++; next }
      if (defined $m && $st[9] < $m) { $stale++ } elsif (defined $m && $st[9] > $m) { $chg++; $hskip++ if $h; next }
      if ($h) { my $got = Digest::SHA->new(256)->addfile("$ENV{T}/$p")->hexdigest; if ($got eq $h) { $hok++ } else { $hbad++ } }
      END { printf "%d %d %d %d %d %d\n", $miss, $stale, $chg, $hok, $hbad, $hskip }')
  MISSING=$(echo "$COUNTS" | cut -d' ' -f1); STALE=$(echo "$COUNTS" | cut -d' ' -f2); CHANGED=$(echo "$COUNTS" | cut -d' ' -f3)
  HASH_OK=$(echo "$COUNTS" | cut -d' ' -f4); HASH_BAD=$(echo "$COUNTS" | cut -d' ' -f5); HASH_SKIP=$(echo "$COUNTS" | cut -d' ' -f6)
  rm -f "$TARGET/.t0" "$TARGET/.listed" "$TARGET/.old" "$TARGET/.gone"
else
  LEGACY=1
fi

if [ -n "$BROKEN" ]; then STATUS=BROKEN; CODE=5
elif [ "$HASH_BAD" -gt 0 ]; then STATUS=BROKEN; CODE=8; BROKEN="content: $HASH_BAD core-asset file(s) do not match their sha256"
elif [ "$STALE" -gt 0 ]; then STATUS=STALE; CODE=6
elif [ "$MISSING" -gt 0 ]; then STATUS=INCOMPLETE; CODE=4
elif [ "$LEGACY" -eq 1 ]; then STATUS=UNVERIFIED; CODE=7
elif [ "$HASH_OK" -gt 0 ]; then STATUS=CONTENT_VERIFIED; CODE=0
else STATUS=CHAIN_VERIFIED; CODE=0
fi
echo "$STATUS set=$SET until=$UNTIL full=$FULL_STAMP archives=$APPLIED skipped-older-incs=$SKIPPED last=$LAST $HEAD_NOTE deleted=$DELETED missing=$MISSING stale=$STALE changed-during-backup=$CHANGED core-hash=ok:$HASH_OK,bad:$HASH_BAD,skipped:$HASH_SKIP${BROKEN:+ ($BROKEN)}"
exit $CODE

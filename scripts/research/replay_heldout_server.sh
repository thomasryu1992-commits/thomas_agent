#!/usr/bin/env bash
# Server share of HISTORICAL_FORWARD_REPLAY_HELDOUT_V0.1 §5, from a clean checkout at the merged SHA, one sandbox at
# a time (resumable: re-running continues every shard after its last fire and skips finished leak checks):
#   setsid nohup scripts/research/replay_heldout_server.sh "$PWD" > ~/research/replay-heldout/logs/driver.log 2>&1 < /dev/null &
set -u
code="$1"                                   # the clean checkout
base="$HOME/research/replay-heldout"
mkdir -p "$base/shards" "$base/xcheck" "$base/leak" "$base/logs"
cd "$code"
run() { scripts/research/sandbox_run.sh --official -- python -m scripts.research.forward_replay_heldout "$@"; }
# 1. the cross-check piece the Mac runs too
run simulate --context heldout-4h --arm A --start 2021-12-08 --end 2021-12-10 \
  --out "$base/xcheck/heldout-4h.A.server.jsonl" > "$base/logs/xcheck.log" 2>&1
# 2. the fire leak checks: arm x context x first/middle/last anchor
for arm in A B; do
  for ctx in heldout-1d heldout-4h; do
    for day in 2022-06-06 2024-07-01 2026-07-27; do
      out="$base/leak/$arm.$ctx.$day.json"
      [ -s "$out" ] && continue
      run leak-check --arm "$arm" --context "$ctx" --at "$day" > "$out.tmp" 2> "$base/logs/leak.$arm.$ctx.$day.log" \
        && mv "$out.tmp" "$out"
    done
  done
done
# 3. the 1d shards, A then B (the 4h shards are the Mac's: replay_heldout_mac.sh)
for arm in A B; do
  run simulate --context heldout-1d --arm "$arm" --out "$base/shards/heldout-1d.$arm.jsonl" \
    > "$base/logs/heldout-1d.$arm.log" 2>&1
done
echo "server share done $(date -u +%FT%TZ)" >> "$base/logs/done.log"

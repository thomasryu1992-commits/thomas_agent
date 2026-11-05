#!/usr/bin/env bash
# Server share of HISTORICAL_FORWARD_REPLAY_V0.1, run from a clean checkout at the merged SHA, one sandbox at a time:
#   setsid nohup scripts/research/replay_server.sh "$PWD" > ~/research/replay/logs/driver.log 2>&1 < /dev/null &
set -u
code="$1"                                   # the clean checkout
base="$HOME/research/replay"
mkdir -p "$base/shards" "$base/xcheck" "$base/leak" "$base/logs"
cd "$code"
run() { scripts/research/sandbox_run.sh --official -- python -m scripts.research.forward_replay "$@"; }
# 1. the cross-check piece the Mac runs too
run simulate --context pooled-4h --arm A --start 2021-12-08 --end 2021-12-10 --out "$base/xcheck/pooled-4h.A.server.jsonl" \
  > "$base/logs/xcheck.log" 2>&1
# 2. the fire leak checks: arm x context x first/middle/last anchor
for arm in A B; do
  for ctx in pooled-1d pooled-4h BTCUSDT-1h ETHUSDT-1h SOLUSDT-1h BNBUSDT-1h DOGEUSDT-1h; do
    for day in 2022-06-06 2024-07-01 2026-07-27; do
      out="$base/leak/$arm.$ctx.$day.json"
      [ -s "$out" ] && continue
      run leak-check --arm "$arm" --context "$ctx" --at "$day" > "$out.tmp" 2> "$base/logs/leak.$arm.$ctx.$day.log" \
        && mv "$out.tmp" "$out"
    done
  done
done
# 3. the shards, A then B (resumable: re-running this script continues each)
for arm in A B; do
  for ctx in pooled-1d pooled-4h; do
    run simulate --context "$ctx" --arm "$arm" --out "$base/shards/$ctx.$arm.jsonl" > "$base/logs/$ctx.$arm.log" 2>&1
  done
done
echo "server share done $(date -u +%FT%TZ)" >> "$base/logs/done.log"

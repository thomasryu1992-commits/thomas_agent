#!/usr/bin/env bash
# HISTORICAL_FORWARD_REPLAY_HELDOUT_V0.1 §5: the Mac's share — the heldout-4h context, arms A and B, one process
# each, resumable (re-running continues every shard after its last fire).
#
#   scripts/research/replay_heldout_mac.sh <public-cache-dir> <out-dir> [--start YYYY-MM-DD --end YYYY-MM-DD]
#
# With --start/--end it runs arm A only: the cross-check piece the server also runs.
# Refuses a dirty checkout, a Python other than 3.12, and any MVP_* variable (forward_replay checks that too).
set -euo pipefail
cache="${1:?usage: replay_heldout_mac.sh <public-cache-dir> <out-dir> [--start D --end D]}"
out="${2:?usage: replay_heldout_mac.sh <public-cache-dir> <out-dir> [--start D --end D]}"
shift 2
repo="$(cd "$(dirname "$0")/../.." && pwd)"
py="${REPLAY_PYTHON:-$repo/.venv-replay/bin/python}"
if [ -n "$(git -C "$repo" status --porcelain)" ]; then
  echo "refused: $repo has uncommitted changes; the shard's code_digest would not match the server's" >&2; exit 2
fi
"$py" -c 'import sys; sys.exit(0 if sys.version_info[:2] == (3, 12) else 1)' \
  || { echo "refused: $py is not Python 3.12 (the server's research venv is 3.12)" >&2; exit 2; }
if env | grep -q '^MVP_'; then echo "refused: an MVP_* variable is set" >&2; exit 2; fi
for s in ADAUSDT AVAXUSDT LINKUSDT LTCUSDT DOTUSDT; do
  [ -s "$cache/binance/klines/$s.4h.jsonl" ] || { echo "refused: no $cache/binance/klines/$s.4h.jsonl" >&2; exit 2; }
done
mkdir -p "$out"
keep=""
command -v caffeinate >/dev/null 2>&1 && keep="caffeinate -is"
arms="A B"
[ $# -gt 0 ] && arms="A"
cd "$repo"
echo "commit $(git rev-parse --short HEAD); shards in $out"
for arm in $arms; do
  $keep "$py" -m scripts.research.forward_replay_heldout simulate --context heldout-4h --arm "$arm" --cache "$cache" \
    --out "$out/heldout-4h.$arm.jsonl" "$@" > "$out/heldout-4h.$arm.log" 2>&1 &
  echo "started heldout-4h $arm (pid $!, log $out/heldout-4h.$arm.log)"
done
wait
echo "done: $(ls "$out"/heldout-4h.*.jsonl | wc -l | tr -d ' ') shards"

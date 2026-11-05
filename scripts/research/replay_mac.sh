#!/usr/bin/env bash
# HISTORICAL_FORWARD_REPLAY_V0.1 §6: the Mac's share of the replay — the five 1h contexts, one process each,
# resumable (re-running continues every shard after its last fire).
#
#   scripts/research/replay_mac.sh <public-cache-dir> <out-dir> [--start YYYY-MM-DD --end YYYY-MM-DD]
#
# Refuses a dirty checkout, a Python other than 3.12, and any MVP_* variable (forward_replay checks that too).
# The cache is the copied <server>:~/research/cache (its binance/ directory); nothing here reads the network.
set -euo pipefail
cache="${1:?usage: replay_mac.sh <public-cache-dir> <out-dir> [--start D --end D]}"
out="${2:?usage: replay_mac.sh <public-cache-dir> <out-dir> [--start D --end D]}"
shift 2
repo="$(cd "$(dirname "$0")/../.." && pwd)"
py="${REPLAY_PYTHON:-$repo/.venv-replay/bin/python}"
if [ -n "$(git -C "$repo" status --porcelain)" ]; then
  echo "refused: $repo has uncommitted changes; the shard's code_digest would not match the server's" >&2; exit 2
fi
"$py" -c 'import sys; sys.exit(0 if sys.version_info[:2] == (3, 12) else 1)' \
  || { echo "refused: $py is not Python 3.12 (the server's research venv is 3.12)" >&2; exit 2; }
if env | grep -q '^MVP_'; then echo "refused: an MVP_* variable is set" >&2; exit 2; fi
[ -d "$cache/binance" ] || { echo "refused: no $cache/binance (copy the server's public cache first)" >&2; exit 2; }
mkdir -p "$out"
keep=""
command -v caffeinate >/dev/null 2>&1 && keep="caffeinate -is"
cd "$repo"
echo "commit $(git rev-parse --short HEAD); shards in $out"
for ctx in BTCUSDT-1h ETHUSDT-1h SOLUSDT-1h BNBUSDT-1h DOGEUSDT-1h; do
  $keep "$py" -m scripts.research.forward_replay simulate --context "$ctx" --cache "$cache" \
    --out "$out/$ctx.A.jsonl" "$@" > "$out/$ctx.A.log" 2>&1 &
  echo "started $ctx (pid $!, log $out/$ctx.A.log)"
done
wait
echo "done: $(ls "$out"/*.A.jsonl | wc -l | tr -d ' ') shards"

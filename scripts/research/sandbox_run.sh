#!/usr/bin/env bash
# Run one command in the research sandbox (RESEARCH_SANDBOX_V0.1.md §4.3), as `claude`.
#
#   sandbox_run.sh [--official] [--lake <id>|current] [--code <dir>] -- <command> [args...]
#
# - Capped: memory 700M (no swap), CPU 50%, nice 10, ionice idle (systemd --user scope).
# - No network (sandbox_site/sitecustomize.py), no secrets (empty environment), no live state: the code
#   tree's state directory is a link to a per-run copy of the lake snapshot's research files.
# - Refuses a code tree that holds a real state directory, and any 15m argument.
# - Refuses to START inside the factory window (08:00-09:00 UTC, `fetch_public.FACTORY_WINDOW_UTC`) or in the
#   30 minutes before it: a run takes ~10 minutes, and the 2026-10-21 re-score ran 08:12-08:21 beside the
#   factory. The cap and nice keep a run from starving it; not starting there keeps it from sharing at all.
# - --official: the tree must be clean and its HEAD on origin/main (merged, reviewed code only).
# - Every run is recorded under ~/research/runs/<run id>/ (command, sha, dirty, lake id, exit, times).
set -euo pipefail

official=0
lake_id="current"
code=""
while [ $# -gt 0 ]; do
  case "$1" in
    --official) official=1; shift ;;
    --lake) lake_id="$2"; shift 2 ;;
    --code) code="$2"; shift 2 ;;
    --) shift; break ;;
    *) echo "usage: sandbox_run.sh [--official] [--lake ID|current] [--code DIR] -- CMD..." >&2; exit 2 ;;
  esac
done
[ $# -gt 0 ] || { echo "no command" >&2; exit 2; }

# RESEARCH_NOW_UTC (HH:MM) exists for the test of this rule; nothing else sets it.
now_hm="${RESEARCH_NOW_UTC:-$(date -u +%H:%M)}"
now_min=$(( 10#${now_hm%%:*} * 60 + 10#${now_hm##*:} ))
window_start=$(( 8 * 60 )); window_end=$(( 9 * 60 )); lead=30
if [ "$now_min" -ge $(( window_start - lead )) ] && [ "$now_min" -lt "$window_end" ]; then
  echo "refused: $now_hm UTC is in or within ${lead} min of the factory window (08:00-09:00 UTC); start after 09:00" >&2
  exit 2
fi

lake_root="${RESEARCH_LAKE_ROOT:-/var/lib/thomas-measure/research-lake}"
cache="${RESEARCH_CACHE:-$HOME/research/cache}"
venv="${RESEARCH_VENV:-$HOME/research/venv}"
runs="${RESEARCH_RUNS:-$HOME/research/runs}"
mem="${RESEARCH_MEMORY_MAX:-700M}"
cpu="${RESEARCH_CPU_QUOTA:-50%}"
site="$(cd "$(dirname "$0")/sandbox_site" && pwd)"
code="$(cd "${code:-.}" && git rev-parse --show-toplevel)"

for arg in "$@"; do
  case "$arg" in *15m*) echo "refused: 15m does not fit the sandbox (RESEARCH_SANDBOX_V0.1 §3)" >&2; exit 2 ;; esac
done

snapshot="$(readlink -f "$lake_root/$lake_id" || true)"
if [ -z "$snapshot" ] || [ ! -f "$snapshot/manifest.json" ]; then
  echo "no lake snapshot at $lake_root/$lake_id" >&2; exit 2
fi
state_link="$code/.runtime_governance_state"
if [ -e "$state_link" ] && [ ! -L "$state_link" ]; then
  echo "refused: $state_link is a real directory; the sandbox only runs on a tree without state" >&2; exit 2
fi
if [ -L "$state_link" ]; then
  case "$(readlink "$state_link")" in "$runs"/*) ;; *) echo "refused: $state_link points outside $runs" >&2; exit 2 ;; esac
fi
mkdir -p "$runs"
exec 9>"$runs/.lock-$(printf '%s' "$code" | sha256sum | cut -c1-16)"
flock -n 9 || { echo "refused: another sandbox run is using $code" >&2; exit 2; }

sha="$(git -C "$code" rev-parse HEAD)"
dirty=0; [ -z "$(git -C "$code" status --porcelain --untracked-files=no)" ] || dirty=1
if [ "$official" = 1 ]; then
  [ "$dirty" = 0 ] || { echo "refused: --official needs a clean tree" >&2; exit 2; }
  git -C "$code" fetch -q origin main
  git -C "$code" merge-base --is-ancestor "$sha" origin/main || { echo "refused: --official needs HEAD on origin/main" >&2; exit 2; }
fi

run_id="$(date -u +%Y%m%dT%H%M%SZ)-$$"
run="$runs/$run_id"
mkdir -p "$run/state/crypto"
for f in "$snapshot"/*; do
  name="$(basename "$f")"
  [ "$name" = "manifest.json" ] || ln -s "$f" "$run/state/crypto/$name"
done
ln -sfn "$run/state" "$state_link"
trap 'rm -f "$state_link"' EXIT

started="$(date -u +%FT%TZ)"
set +e
systemd-run --user --scope -q -p MemoryMax="$mem" -p MemorySwapMax=0 -p CPUQuota="$cpu" \
  nice -n 10 ionice -c3 \
  env -i HOME="$HOME" LANG=C.UTF-8 PATH="$venv/bin:/usr/bin:/bin" PYTHONDONTWRITEBYTECODE=1 \
      PYTHONPATH="$site:$code" RESEARCH_CACHE="$cache" RESEARCH_LAKE="$snapshot" RESEARCH_RUN="$run" \
  bash -c 'cd "$1" && shift && exec "$@"' _ "$code" "$@" > >(tee "$run/stdout") 2> >(tee "$run/stderr" >&2)
status=$?
set -e
python3 - "$run/run.json" "$@" <<PY
import json, sys
json.dump({"command": sys.argv[2:], "code": "$code", "sha": "$sha", "dirty": $dirty == 1,
           "official": $official == 1, "lake_snapshot": "$(basename "$snapshot")", "started": "$started",
           "ended": "$(date -u +%FT%TZ)", "exit": $status, "memory_max": "$mem", "cpu_quota": "$cpu"},
          open(sys.argv[1], "w"), indent=1)
PY
echo "research run $run_id: exit $status (lake $(basename "$snapshot"), sha ${sha:0:8}$([ $dirty = 1 ] && echo ', dirty'))" >&2
exit $status

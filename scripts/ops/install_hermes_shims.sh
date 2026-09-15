#!/usr/bin/env bash
# Check or install the versioned Hermes MCP shims (integrations/hermes/mcp/) on the host's Hermes
# data root. Sequence 2, P02 (2026-09-14): the repository copy is the source of truth, the host
# copy is what the gateway runs.
#
#   --check    (default) compare each installed file with the repository copy; print same/DIFFERS/
#              MISSING per file; exit 1 on any difference; write nothing.
#   --install  copy the repository files over; a changed file is first backed up beside itself as
#              <name>.bak-<UTC stamp>; owner 10000:10000, mode 0644 (the gateway's uid, see
#              docs/DEPLOYMENT.md). Exit 0 when done.
#
# The MCP servers are spawned by the Hermes gateway, so an install takes effect at the next
# gateway start (`docker restart hermes`, or `docker compose -p thomas_agent restart hermes` from a
# clean main worktree). This script never restarts anything and never touches the runtime image.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SRC="$REPO_ROOT/integrations/hermes/mcp"
DEST="${HERMES_MCP_DIR:-/root/hermes-trial/data/mcp}"
OWNER_UID="${HERMES_UID:-10000}"
OWNER_GID="${HERMES_GID:-10000}"
FILES=(thomas_door_client.py read_bridge_mcp.py dispatch_bridge_mcp.py switch_bridge_mcp.py knowledge_bridge_mcp.py)

mode="${1:---check}"
case "$mode" in
  --check|--install) ;;
  *) echo "usage: $0 [--check|--install]" >&2; exit 64 ;;
esac

drift=0
for f in "${FILES[@]}"; do
  if [ ! -f "$SRC/$f" ]; then echo "ERROR: repository copy missing: $SRC/$f" >&2; exit 2; fi
  if [ ! -f "$DEST/$f" ]; then
    echo "MISSING  $DEST/$f"; drift=1
  elif ! cmp -s "$SRC/$f" "$DEST/$f"; then
    echo "DIFFERS  $DEST/$f"; drift=1
  else
    echo "same     $DEST/$f"
  fi
done

if [ "$mode" = "--check" ]; then
  if [ "$drift" -eq 0 ]; then
    echo "OK: the installed shims match integrations/hermes/mcp"
  else
    echo "DRIFT: review the difference, then either update integrations/hermes/mcp (the repository is the source of truth) or run: $0 --install"
  fi
  exit "$drift"
fi

stamp="$(date -u +%Y%m%d-%H%M%S)"
mkdir -p "$DEST"
for f in "${FILES[@]}"; do
  if [ -f "$DEST/$f" ] && ! cmp -s "$SRC/$f" "$DEST/$f"; then
    cp -p "$DEST/$f" "$DEST/$f.bak-$stamp"
    echo "backup   $DEST/$f.bak-$stamp"
  fi
  # cp + chown + chmod, not `install -o`: the host's coreutils is uutils (Ubuntu 26.04), whose
  # `install` refuses a numeric owner with no passwd entry ("invalid user: '10000'") where GNU
  # accepts it, and 10000 is the gateway's in-container uid with no host account (2026-09-15).
  # Copy beside the target and rename, so the gateway never reads a half-written shim.
  cp "$SRC/$f" "$DEST/$f.tmp-install"
  chown "$OWNER_UID:$OWNER_GID" "$DEST/$f.tmp-install"
  chmod 0644 "$DEST/$f.tmp-install"
  mv -f "$DEST/$f.tmp-install" "$DEST/$f"
done
echo "installed ${#FILES[@]} shim(s) to $DEST. Restart the hermes gateway for the change to take effect."

#!/bin/bash
# Copy-truncate rotation for the one Hermes log outside its own rotation: tools/mcp_tool.py
# appends MCP server stderr to data/logs/mcp-stderr.log with a plain file handle (10.7 MB on
# 2026-09-04, never rotated). The writer keeps the descriptor open in append mode, so truncating
# the file after copying it is safe — the next write lands at the new end. logrotate is not used
# because the directory is owned by uid 10000, which has no passwd entry on the host, and
# logrotate's `su` directive needs one.
# PR4 of the Hermes integration sequence; installed to /root/backups/, cron daily 07:40Z.
set -u
F="${1:-/root/hermes-trial/data/logs/mcp-stderr.log}"
MAX=$((10 * 1024 * 1024))
KEEP=3
[ -f "$F" ] || exit 0
[ "$(stat -c %s "$F")" -gt "$MAX" ] || exit 0
for i in $(seq $((KEEP - 1)) -1 1); do
  [ -f "$F.$i" ] && mv -f "$F.$i" "$F.$((i + 1))"
done
cp -p "$F" "$F.1" && : > "$F"
rm -f "$F.$((KEEP + 1))"
echo "$(date -u +%FT%TZ) rotated $(basename "$F") ($(du -h "$F.1" | cut -f1))" >> "$(dirname "$F")/rotate.log"

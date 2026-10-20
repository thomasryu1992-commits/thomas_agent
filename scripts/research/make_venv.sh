#!/usr/bin/env bash
# Build the research sandbox's Python 3.12 environment (RESEARCH_SANDBOX_V0.1.md §4.3), as `claude`.
# The interpreter is the one the deploy runner already vetted and installed (CR-8b), not a new download.
# Re-run it if the runner is upgraded and that path moves. Needs the network (pip), so it runs outside
# the sandbox.
set -euo pipefail
here="$(cd "$(dirname "$0")/../.." && pwd)"
venv="${RESEARCH_VENV:-$HOME/research/venv}"
python="$(ls -1d /opt/thomas-deploy/*/python/bin/python3.12 2>/dev/null | tail -1 || true)"
if [ -z "$python" ] || [ ! -x "$python" ]; then
  echo "no vetted python3.12 under /opt/thomas-deploy/*/python/bin" >&2
  exit 2
fi
"$python" -m venv --clear "$venv"
"$venv/bin/python" -m pip install --quiet --disable-pip-version-check -r "$here/requirements-runtime.txt"
"$venv/bin/python" -c "import sys; print('research venv', sys.version.split()[0], sys.prefix)"

"""Import bridge to the shared governance helpers under ``scripts/``.

Four runtime modules (binding, permission, approval, consumption) each mutated
``sys.path`` at import time to reach ``scripts/lib`` — an import side effect the repo's
own conventions forbid, times four. The mutation now happens in exactly one place:
importing this module makes ``lib.*`` and the ``validate_*`` script helpers importable.

Usage::

    from . import _scripts_bridge  # noqa: F401  (side effect: scripts/ on sys.path)
    from lib.action_fingerprint import compute_action_fingerprint
"""

from __future__ import annotations

import sys

from .paths import repo_root as _repo_root

_SCRIPTS_DIR = str(_repo_root() / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

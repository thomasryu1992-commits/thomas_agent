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
    # APPEND, never insert(0, ...). `scripts/` has 68 top-level names — `lib` among them —
    # and putting it first made every one of them win the import race for the whole process,
    # ahead of the standard library and every installed package. Nothing needed that: the
    # runtime imports exactly three names from here, and appending still finds all three.
    #
    # It also swaps the failure mode for a strictly better one. First: a dependency that
    # expects its own `lib` silently gets this one, and breaks somewhere unrelated with
    # nothing pointing back. Last: if a name really does collide, the import here fails
    # loudly at module load, on the one name that collided.
    sys.path.append(_SCRIPTS_DIR)

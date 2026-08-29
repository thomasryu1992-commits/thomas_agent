"""The crypto state root — one definition, shared by both trading planes.

``paper.py`` and ``live_pnl.py`` each carried their own ``STATE_REL`` and ``state_dir``,
byte-identical and coupled by nothing. The paper plane's copy rooted the positions, the
outcome ledger and every retention store (they import it from ``paper``); the live plane's
copy rooted the live ledger, the budget, the probe and the risk limits (they import it from
``live_pnl``). A change to either copy would have silently re-rooted half the crypto state
away from the other half, with no error anywhere on the way down — the two planes would
simply have stopped seeing each other's files.

Both modules now re-export from here, so their importers keep their import lines and the two
copies cannot drift. A leaf on purpose: this module imports nothing from the crypto package,
so anything in it may import the root without creating a cycle.
"""

from __future__ import annotations

from pathlib import Path

from ..paths import repo_root as _repo_root

STATE_REL = ".runtime_governance_state/crypto"


def state_dir(root: Path | None = None) -> Path:
    return (root if root is not None else _repo_root()) / STATE_REL

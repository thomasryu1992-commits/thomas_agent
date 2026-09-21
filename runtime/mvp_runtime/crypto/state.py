"""The crypto state root — one definition, shared by both trading planes.

``paper.py`` and ``live_pnl.py`` each carried their own ``STATE_REL`` and ``state_dir``,
byte-identical and coupled by nothing. The paper plane's copy rooted the positions, the
outcome ledger and every retention store (they import it from ``paper``); the live plane's
copy rooted the live ledger, the budget, the probe and the risk limits (they import it from
``live_pnl``). A change to either copy would have silently re-rooted half the crypto state
away from the other half, with no error anywhere on the way down — the two planes would
simply have stopped seeing each other's files.

Both modules now re-export from here, so their importers keep their import lines and the two
copies cannot drift. Since crypto PR7b-1 the retention stores, the budget, the probe, the risk limits
and the live order path import it from here directly: `paper` and `live_pnl` sit above them in the
lane's layer order. A leaf on purpose: this module imports nothing from the crypto package,
so anything in it may import the root without creating a cycle.
"""

from __future__ import annotations

from pathlib import Path

from ..errors import ToolError
from ..paths import repo_root as _repo_root

STATE_REL = ".runtime_governance_state/crypto"

# --- the venue axis (PR1d-0, 2026-09-16) ----------------------------------------------
#
# Until now "live" meant one venue, so the live execution state — the daily order counter, the
# position book, the outcome ledger, the bracket breaker, the registered budget — sat in one
# unqualified directory. A second venue (the signed testnet PR1d needs, Thomas decision 2) has
# to be able to place, reconcile and settle an order WITHOUT any of it being read back as live:
# a testnet fill counted against the live daily cap, or a testnet bracket refusal tripping the
# live breaker, is the failure mode this axis exists to make impossible.
#
# Mainnet keeps the paths it has always had, byte for byte. That is deliberate: every record on
# the machine stays exactly where it is and keeps verifying, so this change migrates nothing and
# can be rolled back by deploying the previous image. Another venue gets its own subtree and
# therefore its own counter, book, ledger and breaker — separation by construction rather than
# by a field every reader has to remember to filter on.
VENUE_MAINNET = "binance_futures"
VENUE_TESTNET = "binance_futures_testnet"
VENUES: tuple[str, ...] = (VENUE_MAINNET, VENUE_TESTNET)
VENUE_UNKNOWN = "CRYPTO_VENUE_UNKNOWN"


def state_dir(root: Path | None = None) -> Path:
    return (root if root is not None else _repo_root()) / STATE_REL


def venue_state_dir(root: Path | None = None, *, venue: str = VENUE_MAINNET) -> Path:
    """Where one venue's execution state lives.

    ``VENUE_MAINNET`` is the historical root unchanged — no migration, and a record written
    before this existed is still read from the same place by the same code. Every other venue
    lives under ``venues/<venue>/``, so a store opened for one venue cannot name another's file.

    An unknown venue raises rather than defaulting: a typo that silently resolved to mainnet
    would write testnet state into the live book, which is the one outcome this function exists
    to prevent.
    """
    if venue == VENUE_MAINNET:
        return state_dir(root)
    if venue not in VENUES:
        raise ToolError(VENUE_UNKNOWN, f"{venue!r} is not a venue; known venues are {', '.join(VENUES)}")
    return state_dir(root) / "venues" / venue

"""Shared test scaffolding — the one definition of the local-Core skip guard.

Seventeen test files each carried their own ``LOCAL_POINTER`` + ``requires_local_core``
copy; the next change to how a local Core activation is detected would have meant
seventeen edits (or, worse, sixteen). Import from here instead.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime.mvp_runtime.binding import DEFAULT_POINTER_REL
from runtime.mvp_runtime.predmarket.market_data import MockPredMarketCollector

REPO_ROOT = Path(__file__).resolve().parents[1]
LOCAL_POINTER = REPO_ROOT / DEFAULT_POINTER_REL

# Binding-dependent tests skip on a core-neutral checkout; they run on any machine with a
# local Core activation (see the `verify` skill, "Core activation (local, per-machine)").
#
# Not on CI, despite what this comment used to say. Both workflows that run pytest
# (mvp-runtime-tests, thomas-agent-runtime-validation) call
# scripts/ci_activate_core_for_tests.py first, precisely so these run rather than skip —
# that script's own docstring says so. The checkout this skips on is a *developer's*: run
# the suite without a local activation and ~195 tests quietly vanish, which is worth
# knowing before reading a green local run as full coverage.
requires_local_core = pytest.mark.skipif(
    not LOCAL_POINTER.is_file(), reason="no local Core activation")


class LiveLikePredMarketCollector:
    """The predmarket mock's data wearing the provenance of a real read.

    The Safety-Flag gate defaults to a mock that honestly reports ``is_synthetic=True``, and
    both predmarket callers now treat that as a venue that did not answer. So a test *about*
    a watch scan or a propose run has to say what it always meant — assume the gate was open —
    instead of quietly riding the default. The mock keeps telling the truth about itself,
    because the production guard depends on exactly that.

    Patch it over ``select_pred_market_collector`` in the module under test::

        monkeypatch.setattr(mod, "select_pred_market_collector",
                            lambda venue, **kw: LiveLikePredMarketCollector(venue))
    """

    def __init__(self, venue: str) -> None:
        self._inner = MockPredMarketCollector(venue)
        self.venue = self._inner.venue
        self.source = self._inner.source
        self.tool_id = self._inner.tool_id
        self.tool_version = self._inner.tool_version
        self.network_egress = False

    def list_markets(self, **kwargs):
        snapshot = self._inner.list_markets(**kwargs)
        snapshot.is_synthetic = False
        return snapshot

    def read_resolutions(self, **kwargs):
        snapshot = self._inner.read_resolutions(**kwargs)
        snapshot.is_synthetic = False
        return snapshot

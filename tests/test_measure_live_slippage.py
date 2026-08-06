"""The sign convention on realized slippage, pinned because the first run got it backwards.

`REMAINING_WORK.md` §F8: the store's median edge sits 1.3 bps above `DEFAULT_SLIPPAGE_BPS`, so
the sign of a slippage measurement is not cosmetic — read the wrong way it turns the one live
observation this runtime has from 7.8x the assumption into an improvement on it.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from measure_live_slippage import _adverse_bps  # noqa: E402


def test_a_short_bought_back_above_its_stop_is_adverse():
    """The real one: ETHUSDT's stop rested at 1900.5 and filled at 1904.96 closing a SHORT.

    `side` on a live outcome is the CLOSING order's side, not the position's direction — both
    rows on this machine read `BUY` against SHORT positions. Reading it as the direction inverts
    the sign, which is exactly what the first version of this script did.
    """
    assert _adverse_bps(1900.5, 1904.96, "BUY", "stop_loss") == pytest.approx(23.47, abs=0.01)


def test_a_long_sold_below_its_stop_is_adverse():
    assert _adverse_bps(100.0, 99.0, "SELL", "stop_loss") == pytest.approx(100.0)


def test_filling_better_than_intended_reads_negative():
    """A stop that fills better is possible and must not be reported as a cost."""
    assert _adverse_bps(100.0, 99.0, "BUY", "stop_loss") == pytest.approx(-100.0)


def test_a_fill_exactly_at_the_intended_price_is_zero():
    """DOGEUSDT's stop did this. It is also the control a resting take-profit must produce."""
    assert _adverse_bps(0.07054, 0.07054, "BUY", "stop_loss") == 0.0


@pytest.mark.parametrize("side", ["SHORT", "LONG", "", "sell "])
def test_an_unreadable_side_measures_nothing_rather_than_guessing(side):
    """A direction where a side was expected would silently halve or invert every figure."""
    assert _adverse_bps(100.0, 101.0, side, "stop_loss") is None


@pytest.mark.parametrize("intended,realized", [(0.0, 100.0), (100.0, 0.0), (-1.0, 100.0)])
def test_an_unusable_price_measures_nothing(intended, realized):
    assert _adverse_bps(intended, realized, "BUY", "stop_loss") is None


def test_an_entry_is_adverse_in_the_mirror_of_an_exit():
    """The entry BUYS to open a long, so paying MORE than the plan assumed is the cost.

    Same function as the exit legs, and that is the point: the adverse direction is a property
    of the order's side, not of whether it opens or closes.
    """
    assert _adverse_bps(100.0, 100.5, "BUY", "entry") == pytest.approx(50.0)
    assert _adverse_bps(100.0, 99.5, "SELL", "entry") == pytest.approx(50.0)

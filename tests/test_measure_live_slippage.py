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


# --- the summary line has to survive being quoted on its own ---------------------


def _summary(*stops: float) -> str:
    """The `stop fills:` line for the given adverse readings, at the real modelled rate."""
    from measure_live_slippage import render

    rendered = render({
        "modelled_bps": 3.0, "unmeasurable": 0,
        "entries_without_intent": 0, "canaries_without_intent": 0,
        "measured": [
            {"symbol": "X", "close_reason": "stop_loss",
             "intended": 100.0, "realized": 100.0, "adverse_bps": bps}
            for bps in stops
        ],
    })
    return next(line for line in rendered.splitlines() if line.startswith("stop fills:"))


def test_each_multiple_names_the_figure_it_divides():
    """The live readings on 2026-08-07, whose old rendering read
    `median 11.73 bps  worst 23.47  against 3.0 modelled (7.8x)` — where the one multiple
    trailed the whole line and 7.8 is the WORST's, not the median's (3.9)."""
    line = _summary(0.0, 23.47)
    assert "median 11.73 bps (3.9x modelled 3.0)" in line
    assert "worst 23.47 (7.8x)" in line


def test_the_multiples_differ_when_the_readings_do():
    """The pin that matters: one number cannot stand for both unless they are equal."""
    line = _summary(3.0, 30.0)
    assert "median 16.50 bps (5.5x modelled 3.0)" in line
    assert "worst 30.00 (10.0x)" in line


def test_one_reading_makes_median_and_worst_agree():
    """The case the old line was accidentally right for, and the reason it survived."""
    line = _summary(23.47)
    assert "median 23.47 bps (7.8x modelled 3.0)" in line
    assert "worst 23.47 (7.8x)" in line


# --- the ledger rotates; the measurement must not lose the rows it rotated out ----------


def _opened(position_id: str, *, stop: float, intended: float, fill: float, side: str) -> dict:
    return {"record": {"live_opened": {
        "position": {"position_id": position_id},
        "entry": {"symbol": "ETHUSDT", "intended_price": intended, "fill": {"avg_price": fill},
                  "submit_response": {"side": side}, "created_at": "2026-08-10T00:00:00Z"},
        "bracket": [{"stop_price": stop}, {"price": stop * 1.1}],
    }}}


def _stop_outcome(position_id: str, *, exit_price: float, side: str, strategy_id: str,
                  stop_price: float | None = None) -> dict:
    return {"outcome_closed": True, "position_id": position_id, "close_reason": "stop_loss",
            "symbol": "ETHUSDT", "exit_price": exit_price, "side": side,
            "strategy_id": strategy_id, "stop_price": stop_price,
            "closed_at_utc": "2026-08-11T00:00:00Z"}


def test_a_position_whose_ledger_rows_rotated_into_the_archive_is_still_measured(tmp_path):
    """2026-09-28: every live position (opened 08-04..21) had rotated out of `records.jsonl`, so
    the report read "no exit leg has both an intended and a realized price yet" over a sample
    that existed. The rows sit in `runtime_ledger/archive/`, and the measurement reads them."""
    import json
    from measure_live_slippage import _ledger_rows, measure

    ledger = tmp_path / ".runtime_governance_state" / "runtime_ledger"
    (ledger / "archive").mkdir(parents=True)
    row = _opened("pos_a", stop=1900.5, intended=2000.0, fill=2001.0, side="SELL")
    (ledger / "archive" / "records.2026-08-20T000000Z.jsonl").write_text(json.dumps(row) + "\n")
    (ledger / "records.jsonl").write_text("")

    result = measure(
        ledger_rows=_ledger_rows(tmp_path),
        outcomes=[_stop_outcome("pos_a", exit_price=1904.96, side="BUY", strategy_id="S005-GEN-700")],
        canaries=[],
    )
    stops = [r for r in result["measured"] if r["close_reason"] == "stop_loss"]
    assert [r["adverse_bps"] for r in stops] == [pytest.approx(23.47, abs=0.01)]
    assert [r["close_reason"] for r in result["measured"]].count("entry") == 1
    assert result["unmeasurable"] == 0


def test_a_probe_stop_is_measured_from_its_own_outcome_and_reported_apart():
    """A probe's position never reaches the ledger's `live_opened`, but its outcome records the
    stop it rested at. Where both exist the submitted bracket wins."""
    from measure_live_slippage import measure, render

    result = measure(
        ledger_rows=[_opened("pos_s", stop=100.0, intended=101.0, fill=101.0, side="SELL")],
        outcomes=[
            _stop_outcome("pos_s", exit_price=100.5, side="BUY", strategy_id="S1",
                          stop_price=999.0),
            _stop_outcome("pos_p", exit_price=99.9, side="SELL", strategy_id="PROBE-batch",
                          stop_price=100.0),
        ],
        canaries=[],
    )
    by_source = {r["source"]: r["adverse_bps"] for r in result["measured"]
                 if r["close_reason"] == "stop_loss"}
    assert by_source == {"strategy": pytest.approx(50.0), "probe": pytest.approx(10.0)}
    text = render(result)
    assert "  strategy stops: n=1  mean 50.00 bps" in text
    assert "  probe stops: n=1  mean 10.00 bps" in text
    assert "modelled 1.4" in text

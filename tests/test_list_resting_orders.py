"""Seeing a conditional order the runtime has forgotten.

On 2026-08-03T04:28:58Z a ``closePosition`` STOP_MARKET was accepted by the venue, could not be
found by the query that followed, was recorded as ``placed: false``, and was therefore never
cancelled when the naked position was closed. Nothing in this repo could then answer whether it
is resting: ``fetch_order`` only knows ids the runtime already holds, and
``/fapi/v1/openOrders`` stopped returning conditional orders when Binance moved them to the Algo
service. **The one class of order that can be orphaned was the one class nothing could see.**

What these pin:

- both readers are consulted, because the plain one alone reports an emptiness that is not true;
- an unreadable venue refuses rather than reporting "nothing is resting", which is the failure
  this tool exists to end;
- unowned is decided by the runtime's own bracket ids, and no open positions means every
  resting order is unowned — the state that matters right after a naked close.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.errors import MvpRuntimeError
from scripts import list_resting_orders as lister


class _Adapter:
    network_egress = True

    def __init__(self, *, plain=(), algo=(), error=None):
        self._plain = list(plain)
        self._algo = list(algo)
        self._error = error

    def open_orders(self, symbol=None, *, timeout_seconds=10):
        if self._error:
            raise MvpRuntimeError(self._error, "scripted failure")
        return [o for o in self._plain if symbol is None or o.get("symbol") == symbol]

    def algo_open_orders(self, symbol=None, *, timeout_seconds=10):
        if self._error:
            raise MvpRuntimeError(self._error, "scripted failure")
        return [o for o in self._algo if symbol is None or o.get("symbol") == symbol]


THE_ORPHAN = {"symbol": "ETHUSDT", "side": "BUY", "orderType": "STOP_MARKET",
              "clientAlgoId": "TAI_ETHUSDT_SL_764f36ae2ac555eeeb",
              "triggerPrice": "1887.86", "closePosition": True, "algoStatus": "NEW"}
A_TARGET = {"symbol": "ETHUSDT", "side": "BUY", "type": "LIMIT",
            "clientOrderId": "TAI_ETHUSDT_TP_189ffb9fd7ff5a5874",
            "price": "1791.55", "status": "NEW"}


def _run(monkeypatch, *, adapter, positions=(), argv=None):
    monkeypatch.setattr(lister, "select_order_adapter", lambda: adapter)
    monkeypatch.setattr(lister, "list_open_live_positions", lambda root=None: list(positions))
    return lister.main(argv or [])


# --- both readers, because one of them is blind -------------------------------

def test_a_conditional_order_is_found(monkeypatch, capsys):
    """The plain endpoint returns none of these since the migration. Consulting only it would
    report an emptiness that is not true — which is how the orphan stayed invisible."""
    rc = _run(monkeypatch, adapter=_Adapter(plain=[], algo=[THE_ORPHAN]))
    out = capsys.readouterr().out
    assert rc == lister.EXIT_UNOWNED
    assert "STOP_MARKET" in out and "1887.86" in out
    assert THE_ORPHAN["clientAlgoId"] in out


def test_plain_orders_are_still_listed(monkeypatch, capsys):
    rc = _run(monkeypatch, adapter=_Adapter(plain=[A_TARGET], algo=[]))
    assert rc == lister.EXIT_UNOWNED
    assert "LIMIT" in capsys.readouterr().out


def test_nothing_resting_is_a_clean_exit(monkeypatch, capsys):
    rc = _run(monkeypatch, adapter=_Adapter())
    assert rc == lister.EXIT_OK
    assert "none" in capsys.readouterr().out


# --- an unreadable venue is not an empty one ----------------------------------

def test_an_unreadable_venue_refuses(monkeypatch, capsys):
    """Reporting 'nothing is resting' from a failed read is the exact failure this ends."""
    rc = _run(monkeypatch, adapter=_Adapter(error="ORDER_REJECTED"))
    assert rc == lister.EXIT_BLOCKED
    assert "could not be read" in capsys.readouterr().out


# --- ownership ----------------------------------------------------------------

def test_a_leg_on_an_open_position_is_owned(monkeypatch, capsys):
    position = {"stop_client_order_id": THE_ORPHAN["clientAlgoId"],
                "take_profit_client_order_id": A_TARGET["clientOrderId"]}
    rc = _run(monkeypatch, adapter=_Adapter(plain=[A_TARGET], algo=[THE_ORPHAN]),
              positions=[position])
    assert rc == lister.EXIT_OK
    assert "UNOWNED" not in capsys.readouterr().out


def test_no_open_positions_makes_everything_unowned(monkeypatch, capsys):
    """The state right after a naked close, and the one that matters: the book is flat, so
    anything still resting belongs to nobody."""
    rc = _run(monkeypatch, adapter=_Adapter(algo=[THE_ORPHAN]), positions=[])
    assert rc == lister.EXIT_UNOWNED
    assert "does not own" in capsys.readouterr().out


def test_a_leg_from_a_different_position_is_not_owned(monkeypatch):
    other = {"stop_client_order_id": "TAI_BTCUSDT_SL_something_else"}
    assert _run(monkeypatch, adapter=_Adapter(algo=[THE_ORPHAN]),
                positions=[other]) == lister.EXIT_UNOWNED


def test_ownership_reads_both_bracket_fields(monkeypatch):
    assert lister.BRACKET_ID_FIELDS == (
        "stop_client_order_id", "take_profit_client_order_id")


# --- it cannot do anything but look -------------------------------------------

def test_it_has_no_way_to_place_or_cancel():
    """The safety property that lets this be run at any time, including mid-incident."""
    import inspect

    source = inspect.getsource(lister)
    for forbidden in ("cancel_order", "submit(", "place_bracket_leg", "--cancel"):
        assert forbidden not in source, f"{forbidden} would make this more than a read"


def test_the_dry_run_says_it_asked_nothing(monkeypatch, capsys):
    class _Inert(_Adapter):
        network_egress = False

    _run(monkeypatch, adapter=_Inert())
    assert "DRY RUN" in capsys.readouterr().out


# --- the path itself ------------------------------------------------------------

def test_the_list_path_is_the_read_one_not_the_mass_cancel_one():
    """`GET /fapi/v1/openAlgoOrders` lists them. `/fapi/v1/algoOpenOrders` — the same words in
    the other order — is `DELETE`: cancel EVERY open algo order on the account.

    This constant pointed at the second one and 404'd on the live account 2026-08-03, because
    the verb was GET. Pinned literally, because the two names are one transposition apart and
    only one of them is a read."""
    from runtime.mvp_runtime.crypto import live_execution as lx

    assert lx.ALGO_OPEN_ORDERS_PATH == "/fapi/v1/openAlgoOrders"


def test_no_bulk_cancel_path_exists_in_the_module():
    """Nothing here cancels in bulk: `cancel_order` names one id, because a cancel that names
    its target cannot take out a leg protecting a position it was never asked about. A constant
    for the bulk path would be a loaded gun beside a very similar name."""
    from runtime.mvp_runtime.crypto import live_execution as lx

    source = open(lx.__file__, encoding="utf-8").read()
    assert source.count("/fapi/v1/algoOpenOrders") == 1, (
        "the mass-cancel path may appear only in the comment that warns about it"
    )

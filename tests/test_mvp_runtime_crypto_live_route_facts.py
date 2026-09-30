"""What the live leg's fact reads hand forward (crypto refactor plan PR-14).

`live_route._run_gated_live_leg` reads its facts in two steps, `_read_leg_facts` before anything
settles and `_read_entry_facts` once the pass reaches the entry decision. The route's own tests pin
what each fact does to the decision. A mutation run of that split found four hand-offs they did not
pin, because an earlier door refuses first or a later one repeats the check:

- the book and the reconciliation that step 1 read are the ones the entry decision is judged on,
  and the reconciliation's status is stamped on the record;
- a watched position that left the book ends its PROTECTION_UNKNOWN episode on the next pass;
- the re-read judges today's loss knowing the account was read;
- an outcome the executing leg built is written to the ledger step 1 selected.
"""

from __future__ import annotations

from typing import Any

from runtime.mvp_runtime.crypto import live_leg, live_route, protection_watch
from tests.test_mvp_runtime_crypto_live_route import (
    BAR_00,
    NOW,
    SYMBOL,
    _Ledger,
    _snapshot,
    _Venue,
    _wire_whole_leg,
)


def _to_the_decision(tmp_path, monkeypatch, *, positions):
    """Run the leg with the given book up to a stubbed entry decision; hand back what the decision
    was given, the record, and the reconciliation the leg was handed."""
    monkeypatch.setenv("MVP_LIVE_TRADING", "real")
    monkeypatch.setattr(live_route, "read_account", lambda **kw: (_snapshot(), {}))
    monkeypatch.setattr(live_route, "list_open_live_positions", lambda root: list(positions))
    reconciliation = {"status": "RECONCILED", "books": {}, "marker": "the leg's own read"}
    monkeypatch.setattr(live_route, "reconcile_positions", lambda local, snapshot, now: reconciliation)
    monkeypatch.setattr(live_route, "_settle_or_protect", lambda record, position, **kw: None)
    seen: dict[str, Any] = {}

    def _plan(plan, **kw):
        seen.update(kw)
        return {"status": "REFUSED", "ready": False, "reasons": ["stubbed"]}

    monkeypatch.setattr(live_route, "plan_live_entry", _plan)
    record = live_route.run_live_leg(
        live_routable_strategy_ids={"S1"}, route=None, feature_row={"timestamp": NOW},
        verdict={"allow_new_position": True}, symbol=SYMBOL, collector=object(), now=NOW,
        root=tmp_path,
    )
    return seen, record, reconciliation


def test_the_decision_is_judged_on_the_book_and_the_reconciliation_step_one_read(tmp_path, monkeypatch):
    """A position on another symbol is exposure the decision must count, and the reconciliation is
    the leg's own read, not a second one."""
    elsewhere = {"symbol": "ETHUSDT", "position_id": "p-eth", "status": "OPEN"}
    seen, record, reconciliation = _to_the_decision(tmp_path, monkeypatch, positions=[elsewhere])

    assert seen["local_positions"] == [elsewhere]
    assert seen["reconciliation"] is reconciliation
    assert record["live_reconcile_status"] == "RECONCILED"


def test_a_watched_position_that_left_the_book_ends_its_episode_on_the_next_pass(tmp_path, monkeypatch):
    gone = {"symbol": SYMBOL, "position_id": "p-gone", "status": "OPEN"}
    protection_watch.observe_unknown(gone, {}, now=NOW, root=tmp_path, ids_missing_code="IDS_MISSING")
    assert set(protection_watch.read_watch(tmp_path)) == {"p-gone"}

    _to_the_decision(tmp_path, monkeypatch, positions=[])

    assert protection_watch.read_watch(tmp_path) == {}


def test_the_re_read_judges_todays_loss_knowing_the_account_was_read(tmp_path, monkeypatch):
    venue = _Venue()
    run = _wire_whole_leg(tmp_path, monkeypatch, venue)
    real = live_route.reread_entry_facts
    handed: list[dict[str, Any]] = []

    def _spy(**kw):
        handed.append(kw)
        return real(**kw)

    monkeypatch.setattr(live_route, "reread_entry_facts", _spy)

    opened = run("2026-07-28T04:05:00Z", BAR_00)

    assert opened["live_route_status"] == live_route.ROUTE_OPENED, opened["live_reason_codes"]
    [kw] = handed
    assert kw["venue_required"] is True
    assert kw["venue_realized_pnl_usdt"] == 0.0


def test_an_outcome_the_entry_built_is_written_to_the_ledger_step_one_selected(tmp_path, monkeypatch):
    venue = _Venue()
    run = _wire_whole_leg(tmp_path, monkeypatch, venue)
    ledger = _Ledger()
    monkeypatch.setattr(live_route, "select_live_ledger", lambda **kw: ledger)
    naked_close = {"status": live_leg.ENTRY_NAKED_CLOSED, "reason_codes": [], "entry": None,
                   "outcome": {"settlement_id": "s-naked", "realized_pnl_usdt": -0.12}}
    monkeypatch.setattr(live_leg, "execute_live_entry", lambda decision, **kw: naked_close)

    run("2026-07-28T04:05:00Z", BAR_00)

    assert [o["settlement_id"] for o in ledger.appended] == ["s-naked"]

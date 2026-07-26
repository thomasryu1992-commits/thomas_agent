"""PM1 observations — the rows the report is built from, and the scan that produces them.

The report answers three questions: how often, how large, and **how long**. Two properties
here decide whether those answers mean anything.

**A non-reading is a row.** "How often" is a ratio, and its denominator is the attempts. A
scan that silently omitted the times it could not price a group would claim opportunities
were observable during hours when nobody could see anything.

**A row that cannot prove itself is refused, not averaged in.** These records are evidence
for a decision about real money, so the store is self-hashed and fails closed on tampering —
the same posture as the confirmed groups they refer to.
"""

from __future__ import annotations

import json

import pytest

from runtime.mvp_runtime.errors import ToolError
from runtime.mvp_runtime.predmarket import observations as obs
from runtime.mvp_runtime.predmarket import pairs
from runtime.mvp_runtime.predmarket.market_data import KALSHI, POLYMARKET

NOW = "2026-07-26T12:00:00Z"


@pytest.fixture
def state(tmp_path, monkeypatch):
    """A throwaway state root for both stores (they share a directory)."""
    monkeypatch.setattr(pairs, "_repo_root", lambda: tmp_path)
    return tmp_path


def _group(root, *legs):
    record = pairs.build_event_group(
        legs=list(legs) or [
            {"venue": KALSHI, "market_id": "KALSHI-MOCK-00"},
            {"venue": POLYMARKET, "market_id": "POLYMARKET-MOCK-00"},
        ],
        criteria_note="both settle on the same official statement for this event",
        confirmed_by="thomas",
        now=NOW,
    )
    return pairs.confirm_group(record, root=root)


# --- the store ------------------------------------------------------------------

def test_an_observation_round_trips_and_is_hash_verified(state):
    obs.append_observations([{"event_id": "e1", "net_edge": 0.04, "observed_at_utc": NOW}], root=state)
    rows = obs.read_observations(state)
    assert len(rows) == 1 and rows[0]["net_edge"] == 0.04

    path = obs.observations_path(state)
    row = json.loads(path.read_text(encoding="utf-8").strip())
    row["net_edge"] = 0.40  # a tenfold better edge, by hand
    path.write_text(json.dumps(row, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(ToolError) as exc:
        obs.read_observations(state)
    assert exc.value.reason_code == obs.OBSERVATIONS_TAMPERED


def test_an_empty_store_is_not_an_unreadable_one(state):
    assert obs.read_observations(state) == []
    obs.observations_path(state).parent.mkdir(parents=True, exist_ok=True)
    obs.observations_path(state).write_text("{not json\n", encoding="utf-8")
    with pytest.raises(ToolError) as exc:
        obs.read_observations(state)
    assert exc.value.reason_code == obs.OBSERVATIONS_UNREADABLE


# --- the scan -------------------------------------------------------------------

def test_a_scan_with_no_confirmed_groups_reads_nothing(state):
    """A scan is measurement, not discovery: with nothing confirmed there is no venue worth
    asking, and spending calls to learn nothing is not a neutral default."""
    scan = obs.run_watch_scan(now=NOW, root=state)
    assert scan["groups_observed"] == 0
    assert scan["venues_read"] == [] and scan["observation_count"] == 0


def test_a_scan_prices_every_pairing_in_every_confirmed_group(state):
    """The mock venues quote the same events at deliberately different prices, so a scan over
    a confirmed group produces a priced reading."""
    _group(state)
    scan = obs.run_watch_scan(now=NOW, root=state)
    assert scan["groups_observed"] == 1
    assert sorted(scan["venues_read"]) == [KALSHI, POLYMARKET]
    assert scan["observation_count"] == 1
    assert scan["readable_count"] == 1
    assert scan["persisted_count"] == 1
    assert obs.read_observations(state)[0]["event_id"] == pairs.read_groups(state)[0]["event_id"]


def test_a_three_leg_group_yields_three_observations_from_one_confirmation(state):
    """What the group generalisation bought: the third venue's pairings are enumerated, not
    confirmed again. Predict.fun is unquoted and its fees unread, so those two pairings are
    recorded as non-readings — which is the honest state, not a gap."""
    _group(
        state,
        {"venue": KALSHI, "market_id": "KALSHI-MOCK-00"},
        {"venue": POLYMARKET, "market_id": "POLYMARKET-MOCK-00"},
        {"venue": "binance", "market_id": "BINANCE-MOCK-00"},
    )
    scan = obs.run_watch_scan(now=NOW, root=state)
    assert scan["observation_count"] == 3
    assert scan["readable_count"] < 3


def test_a_non_reading_is_still_a_row(state):
    """'How often' is a ratio whose denominator is the attempts. Dropping the times a group
    could not be priced would claim it was observable when it was not."""
    _group(
        state,
        {"venue": KALSHI, "market_id": "KALSHI-MOCK-00"},
        {"venue": POLYMARKET, "market_id": "no-such-market"},
    )
    scan = obs.run_watch_scan(now=NOW, root=state)
    assert scan["observation_count"] == 1 and scan["readable_count"] == 0
    row = obs.read_observations(state)[0]
    assert obs.MARKET_NOT_LISTED in row["reasons"]
    assert row["net_edge"] is None and row["is_opportunity"] is False


def test_a_venue_outage_is_distinguished_from_a_delisted_market(state, monkeypatch):
    """Two different findings. A venue that did not answer is an outage; a venue that
    answered and no longer lists the market means the group is stale or resolved."""
    _group(state)

    from runtime.mvp_runtime.predmarket import market_data as md

    real = md.collect_pred_markets

    def _fail_kalshi(venue, **kw):
        if venue == KALSHI:
            raise ToolError("TOOL_TRANSPORT", "venue unreachable")
        return real(venue, **kw)

    monkeypatch.setattr(obs, "collect_pred_markets", _fail_kalshi)
    scan = obs.run_watch_scan(now=NOW, root=state)
    assert scan["venue_errors"] == {KALSHI: "TOOL_TRANSPORT"}
    assert scan["venues_read"] == [POLYMARKET]
    row = obs.read_observations(state)[0]
    assert obs.VENUE_UNREADABLE in row["reasons"]


def test_a_dry_run_produces_the_scan_without_writing_anything(state):
    _group(state)
    scan = obs.run_watch_scan(now=NOW, root=state, persist=False)
    assert scan["observation_count"] == 1 and scan["persisted_count"] == 0
    assert obs.read_observations(state) == []


def test_a_scan_confirms_nothing(state):
    """A scan can never add an event group. Observation follows confirmation, never the
    other way round — the module holds no writer for the group store."""
    obs.run_watch_scan(now=NOW, root=state)
    assert pairs.read_groups(state) == []
    assert not hasattr(obs, "confirm_group")
    scan = obs.run_watch_scan(now=NOW, root=state)
    assert scan["authorizes_trading"] is False


# --- the scheduler kind ---------------------------------------------------------

def test_the_scan_kind_is_registered_and_watch_is_the_default():
    from runtime.mvp_runtime import scheduler

    assert scheduler.KIND_PM_SCAN in scheduler.KINDS
    assert scheduler.KIND_PM_SCAN == "pm_scan"


def test_discovery_says_it_is_not_scheduled_rather_than_doing_the_watch_scan(state, monkeypatch):
    """Discovery is candidate generation for the operator, not measurement. Running the watch
    scan under that name would look like coverage that never happened."""
    from runtime.mvp_runtime import scheduler
    from runtime.mvp_runtime.scheduler import Schedule

    calls: list[str] = []
    monkeypatch.setattr(obs, "run_watch_scan", lambda **kw: calls.append("ran") or {})

    schedule = Schedule(
        schedule_id="sch_1", kind=scheduler.KIND_PM_SCAN, request="discovery",
        interval_seconds=21600, enabled=True, created_by="thomas", created_at=NOW,
        next_run_at=NOW,
    )
    result = scheduler._execute(  # noqa: SLF001 - the dispatch under test
        schedule, now=NOW, ledger=None, working_memory=None, programization=None,
        provider=None, search_tool=None, repo_root=state, executor=lambda **kw: {},
    )
    assert result == "skipped_discovery_not_scheduled_yet"
    assert calls == []


def test_a_watch_schedule_actually_runs_the_scan(state, monkeypatch):
    from runtime.mvp_runtime import scheduler
    from runtime.mvp_runtime.scheduler import Schedule

    _group(state)
    schedule = Schedule(
        schedule_id="sch_2", kind=scheduler.KIND_PM_SCAN, request="watch",
        interval_seconds=120, enabled=True, created_by="thomas", created_at=NOW,
        next_run_at=NOW,
    )
    result = scheduler._execute(  # noqa: SLF001
        schedule, now=NOW, ledger=None, working_memory=None, programization=None,
        provider=None, search_tool=None, repo_root=state, executor=lambda **kw: {},
    )
    assert result.startswith("pm_scan watch:")
    assert len(obs.read_observations(state)) == 1

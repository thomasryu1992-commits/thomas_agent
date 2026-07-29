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
from runtime.mvp_runtime.predmarket.market_data import KALSHI, POLYMARKET, SYNTHETIC_SOURCE
from tests._helpers import LiveLikePredMarketCollector

NOW = "2026-07-26T12:00:00Z"


@pytest.fixture
def state(tmp_path, monkeypatch):
    """A throwaway state root for both stores (they share a directory)."""
    monkeypatch.setattr(pairs, "_repo_root", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def live_venues(monkeypatch):
    """Scan against data claiming to have come from the venues, not from the mock."""
    monkeypatch.setattr(
        obs, "select_pred_market_collector",
        lambda venue, **kw: LiveLikePredMarketCollector(venue),
    )


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


def test_a_scan_prices_every_pairing_in_every_confirmed_group(state, live_venues):
    """The two venues quote the same events at deliberately different prices, so a scan over
    a confirmed group produces a priced reading."""
    _group(state)
    scan = obs.run_watch_scan(now=NOW, root=state)
    assert scan["groups_observed"] == 1
    assert sorted(scan["venues_read"]) == [KALSHI, POLYMARKET]
    assert scan["observation_count"] == 1
    assert scan["readable_count"] == 1
    assert scan["persisted_count"] == 1
    assert obs.read_observations(state)[0]["event_id"] == pairs.read_groups(state)[0]["event_id"]


def test_a_three_leg_group_yields_three_observations_from_one_confirmation(state, live_venues):
    """What the group generalisation bought: the third venue's pairings are enumerated, not
    confirmed again. One confirmation, three pairings, three rows.

    It used to also assert the third venue came back unreadable, because its fee rate was
    unknown on the watch path and an unknown cost is not a zero cost. That was true and is
    now fixed — the venue has a fallback schedule — so the assertion that survives is the one
    about the group, not the one about the gap.
    """
    _group(
        state,
        {"venue": KALSHI, "market_id": "KALSHI-MOCK-00"},
        {"venue": POLYMARKET, "market_id": "POLYMARKET-MOCK-00"},
        {"venue": "binance", "market_id": "BINANCE-MOCK-00"},
    )
    scan = obs.run_watch_scan(now=NOW, root=state)
    assert scan["observation_count"] == 3
    assert scan["groups_observed"] == 1
    assert {leg["venue"] for row in obs.read_observations(state) for leg in row["legs"]} == {
        KALSHI, POLYMARKET, "binance"}


def test_a_non_reading_is_still_a_row(state, live_venues):
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


def test_a_venue_outage_is_distinguished_from_a_delisted_market(state, monkeypatch, live_venues):
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


# === the fee rate a confirmed leg would otherwise lose ==============================

def _leg(venue, market_id, **extra):
    return {"venue": venue, "market_id": market_id, **extra}


def test_a_confirmed_leg_keeps_the_fee_rate_the_by_id_reread_cannot_carry(state, monkeypatch):
    """The defect: a confirmed group could accumulate denominator and never produce a reading.

    The watch scan re-reads a leg by id — one order-book call, which is what makes a two-minute
    cadence affordable — and that response is a price and nothing else. Binance states its fee
    per topic on the *listing* endpoint, so a confirmed Binance leg arrived with
    `fee_rate_bps=None`, its cost came back unknowable, and `net_edge` was `None` on every
    observation. Measured on the one confirmed group: both legs quoted, `gross_edge=-0.011`,
    `net_edge=null`, `readable 0/1` — forever.
    """
    from runtime.mvp_runtime.predmarket import market_data as md

    record = pairs.build_event_group(
        legs=[_leg(KALSHI, "K-FEE", fee_rate_bps=200, fee_rate_read_at=NOW),
              _leg(POLYMARKET, "P-FEE", fee_rate_bps=150, fee_rate_read_at=NOW)],
        criteria_note="both settle on the same official statement for this event",
        confirmed_by="thomas", now=NOW,
    )
    pairs.confirm_group(record, root=state)
    assert [leg.get("fee_rate_bps") for leg in pairs.read_groups(state)[0]["legs"]] == [200.0, 150.0]

    seen: dict[str, float | None] = {}

    def _capture(left, right, **kw):
        seen[left.venue] = left.fee_rate_bps
        seen[right.venue] = right.fee_rate_bps
        return {"net_edge": None, "is_opportunity": False}

    # The by-id re-read the scan actually makes: a price, and no rate.
    def _priced_but_rateless(venue, **kw):
        markets = [md.PredMarket(venue=venue, market_id=mid, group_id=None, title="",
                                 close_time=None, status=None, fee_rate_bps=None)
                   for mid in kw.get("market_ids") or []]
        return ({"markets": [m.as_dict() for m in markets], "is_synthetic": False}, {})

    monkeypatch.setattr(obs, "collect_pred_markets", _priced_but_rateless)
    monkeypatch.setattr(obs.opportunity, "evaluate_pairing", _capture)
    obs.run_watch_scan(now=NOW, root=state)

    assert seen == {KALSHI: 200.0, POLYMARKET: 150.0}


def test_a_rate_the_venue_states_now_beats_the_one_captured_at_confirmation(state, monkeypatch):
    """`live_sizing` states the rule this bends — *venue filters are an input, never a memory* —
    and a rate captured weeks ago is exactly the memory it warns about. So the stored value is a
    fallback for the read that structurally cannot carry one, never an override of a read that
    can."""
    from runtime.mvp_runtime.predmarket import market_data as md

    pairs.confirm_group(pairs.build_event_group(
        legs=[_leg(KALSHI, "K-FEE", fee_rate_bps=200, fee_rate_read_at=NOW),
              _leg(POLYMARKET, "P-FEE", fee_rate_bps=150, fee_rate_read_at=NOW)],
        criteria_note="both settle on the same official statement for this event",
        confirmed_by="thomas", now=NOW,
    ), root=state)

    seen: dict[str, float | None] = {}

    def _capture(left, right, **kw):
        seen[left.venue] = left.fee_rate_bps
        seen[right.venue] = right.fee_rate_bps
        return {"net_edge": None, "is_opportunity": False}

    def _venue_states_a_new_rate(venue, **kw):
        markets = [md.PredMarket(venue=venue, market_id=mid, group_id=None, title="",
                                 close_time=None, status=None, fee_rate_bps=999)
                   for mid in kw.get("market_ids") or []]
        return ({"markets": [m.as_dict() for m in markets], "is_synthetic": False}, {})

    monkeypatch.setattr(obs, "collect_pred_markets", _venue_states_a_new_rate)
    monkeypatch.setattr(obs.opportunity, "evaluate_pairing", _capture)
    obs.run_watch_scan(now=NOW, root=state)

    assert seen == {KALSHI: 999.0, POLYMARKET: 999.0}      # the live rate, not 200/150


def test_a_leg_confirmed_before_this_existed_is_not_broken_by_it(state, monkeypatch):
    """Every group already in the store predates the field. They must keep working exactly as
    they did — unreadable where the venue states no rate, which is the state this fixes going
    forward rather than retroactively."""
    from runtime.mvp_runtime.predmarket import market_data as md

    pairs.confirm_group(pairs.build_event_group(
        legs=[_leg(KALSHI, "K-OLD"), _leg(POLYMARKET, "P-OLD")],
        criteria_note="both settle on the same official statement for this event",
        confirmed_by="thomas", now=NOW,
    ), root=state)
    stored = pairs.read_groups(state)[0]["legs"]
    assert all("fee_rate_bps" not in leg for leg in stored)

    seen: dict[str, float | None] = {}

    def _capture(left, right, **kw):
        seen[left.venue] = left.fee_rate_bps
        return {"net_edge": None, "is_opportunity": False}

    def _rateless(venue, **kw):
        markets = [md.PredMarket(venue=venue, market_id=mid, group_id=None, title="",
                                 close_time=None, status=None, fee_rate_bps=None)
                   for mid in kw.get("market_ids") or []]
        return ({"markets": [m.as_dict() for m in markets], "is_synthetic": False}, {})

    monkeypatch.setattr(obs, "collect_pred_markets", _rateless)
    monkeypatch.setattr(obs.opportunity, "evaluate_pairing", _capture)
    obs.run_watch_scan(now=NOW, root=state)

    assert seen == {KALSHI: None}      # unchanged, not crashed


def test_a_mock_answer_never_becomes_a_priced_observation(state):
    """The worse of the two pre-fix failures — note no ``live_venues``, so this runs on the
    gate's real default, which is the mock.

    Reachable through the propose path: a both-venues-on-mock ``propose`` yields clean-looking
    candidates (the mocks carry the same titles at different prices), and confirming one writes
    mock ids into the group store. Every later scan then *matches* those ids and priced them —
    measured pre-fix: ``net_edge = -0.0243``, counted ``readable 1/1``. A number computed
    entirely from ``(venue, index)`` sitting in the store the 2–4 week report is built from,
    inside the denominator of "how often".
    """
    _group(state)
    scan = obs.run_watch_scan(now=NOW, root=state)
    row = obs.read_observations(state)[0]
    assert obs.SOURCE_SYNTHETIC in row["reasons"]
    assert row["net_edge"] is None and row["is_opportunity"] is False
    assert scan["readable_count"] == 0


def test_a_mock_answer_is_not_recorded_as_a_delisted_market(state):
    """The other pre-fix failure, and the one a correctly-confirmed group hits.

    A real group holds the venues' own ids. The mock knows only its own, so it returns zero
    matches — successfully, unlike an outage — and every leg was filed ``MARKET_NOT_LISTED``:
    the runtime reporting that Thomas's confirmed market had been delisted, about a venue it
    never reached. Only ``MARKET_NOT_LISTED`` says anything about the market, so it is the one
    reason that must never be inferred from a venue the scan did not read.
    """
    _group(
        state,
        {"venue": KALSHI, "market_id": "KXBTCD-25DEC31-B100000"},
        {"venue": POLYMARKET, "market_id": "0xabc123"},
    )
    obs.run_watch_scan(now=NOW, root=state)
    row = obs.read_observations(state)[0]
    assert obs.SOURCE_SYNTHETIC in row["reasons"]
    assert obs.MARKET_NOT_LISTED not in row["reasons"]
    assert obs.VENUE_UNREADABLE not in row["reasons"]


def test_a_venue_the_scan_never_reached_is_not_a_venue_it_read(state):
    """``venues_read`` is the report's coverage and the console's degraded list. A mock answer
    must count as neither read nor quietly fine."""
    _group(state)
    scan = obs.run_watch_scan(now=NOW, root=state)
    assert scan["venues_read"] == []
    assert scan["venue_errors"] == {KALSHI: SYNTHETIC_SOURCE, POLYMARKET: SYNTHETIC_SOURCE}
    assert scan["readable_count"] == 0
    assert "degraded=" in obs.scan_status_line(scan)


def test_the_three_non_reading_reasons_stay_distinct(state, monkeypatch):
    """One venue down, one venue on the mock, one market genuinely gone — three findings that
    must not collapse into each other. Only ``MARKET_NOT_LISTED`` claims anything about the
    market, so it is the only one that may be inferred from a venue's silence."""
    from runtime.mvp_runtime.predmarket import market_data as md

    real = md.collect_pred_markets

    def _kalshi_is_down(venue, **kw):
        if venue == KALSHI:
            raise ToolError("TOOL_TRANSPORT", "venue unreachable")
        return real(venue, **kw)

    # Polymarket answers for real; Kalshi is down; Binance is left on the gate's mock default.
    def _collector(venue, **kw):
        if venue == POLYMARKET:
            return LiveLikePredMarketCollector(venue)
        return md.MockPredMarketCollector(venue)

    _group(
        state,
        {"venue": KALSHI, "market_id": "KALSHI-MOCK-00"},
        {"venue": POLYMARKET, "market_id": "no-such-market"},
        {"venue": "binance", "market_id": "BINANCE-MOCK-00"},
    )
    monkeypatch.setattr(obs, "select_pred_market_collector", _collector)
    monkeypatch.setattr(obs, "collect_pred_markets", _kalshi_is_down)
    obs.run_watch_scan(now=NOW, root=state)

    seen = {reason for row in obs.read_observations(state) for reason in row["reasons"]}
    assert seen == {obs.VENUE_UNREADABLE, obs.SOURCE_SYNTHETIC, obs.MARKET_NOT_LISTED}


def test_a_dry_run_produces_the_scan_without_writing_anything(state, live_venues):
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


def test_discovery_never_runs_the_watch_scan_under_a_different_name(state, monkeypatch):
    """Discovery is candidate generation for the operator; the watch scan is measurement.
    Running the second under the first's name would put readings in the report that were
    never taken — coverage that never happened.

    It now does real work rather than reporting itself unscheduled, but the boundary is the
    same one, and it is what this test is for.
    """
    from runtime.mvp_runtime import scheduler
    from runtime.mvp_runtime.predmarket import proposals
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
    assert result.startswith("pm_scan discovery:")
    assert calls == []
    # No observation was recorded, and no group was confirmed.
    assert obs.read_observations(state) == []
    assert pairs.read_groups(state) == []
    # The run itself is recorded, or a scheduled discovery would be invisible.
    assert len(proposals.read_proposals(state)) == 1


def test_a_watch_schedule_actually_runs_the_scan(state, monkeypatch, live_venues):
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


def test_the_console_line_calls_the_ratio_priced_not_readable():
    """It counts observations that produced a net edge. That fails on a one-sided book, not
    on a venue we could not reach — and "64/65 readable" sent a reader hunting an outage that
    was not there, beside a scan whose only reason was NOT_QUOTED and whose 65 legs had all
    been fetched. Whether a venue answered at all is `venue_errors`, printed as `degraded=`.
    """
    line = obs.scan_status_line({
        "scan_kind": "watch", "opportunity_count": 22, "readable_count": 64,
        "observation_count": 65, "groups_observed": 65, "venue_errors": {},
    })
    assert "64/65 priced" in line
    assert "readable" not in line

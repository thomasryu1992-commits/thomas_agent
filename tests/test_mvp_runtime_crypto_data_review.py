"""Data-gap reviewer — loop ① of the three review loops (data → templates → fusion).

The M4b proposer posture applied to data: deterministic inventory, one budgeted model
call, shape-judged suggestions, a record that collects and installs nothing, degraded-
never-blocking on provider failure, and a scheduled fire that ledgers + best-effort
delivers the sheet."""

from __future__ import annotations

import json

import pytest

from runtime.mvp_runtime.control import ControlStore
from runtime.mvp_runtime.crypto import market_data
from runtime.mvp_runtime.crypto.data_review import (
    DATA_REVIEW_LEDGER_KIND,
    MAX_SUGGESTIONS_PER_RUN,
    MockDataReviewProvider,
    build_data_inventory,
    evaluate_suggestion,
    format_review_report,
    review_data_gaps,
)
from runtime.mvp_runtime.errors import ProviderError
from runtime.mvp_runtime.scheduler import (
    KIND_DATA_REVIEW,
    ScheduleStore,
    build_schedule,
    run_due,
)
from runtime.mvp_runtime.store import LEDGER_REL, RECORDS_FILE, LedgerStore

NOW = "2026-07-25T06:00:00Z"


# --- inventory ----------------------------------------------------------------

def test_inventory_reports_latest_feed_status_and_performance():
    cycles = [
        {"kind": "crypto_cycle", "record": {"feeds": {"funding": "degraded", "liquidations": "absent"}}},
        {"kind": "crypto_cycle", "record": {"feeds": {"funding": "ok", "liquidations": "ok"}}},
    ]
    outcomes = [
        {"timeframe": "1d", "win_loss": "WIN", "result_R": 2.0},
        {"timeframe": "1d", "win_loss": "LOSS", "result_R": -1.0},
        {"timeframe": "15m", "win_loss": "LOSS", "result_R": -1.0},
        {"timeframe": None, "win_loss": "LOSS", "result_R": "garbage"},  # odd row tolerated
    ]
    inv = build_data_inventory(cycles, outcomes, contexts=[("BTCUSDT", "1d")])
    assert inv["feed_status"] == {"funding": "ok", "liquidations": "ok"}  # latest wins
    assert inv["performance_by_timeframe"]["1d"] == {
        "closed": 2, "wins": 1, "total_R": 1.0, "win_rate": 0.5}
    assert inv["performance_by_timeframe"]["15m"]["win_rate"] == 0.0
    assert "unknown" in inv["performance_by_timeframe"]
    assert inv["traded_contexts"] == ["BTCUSDT 1d"]
    assert "close" in inv["mintable_features"] and inv["current_sources"]
    assert inv["venue"] == market_data.BINANCE_FUTURES


def test_the_inventory_reports_the_venues_own_vocabulary():
    """This record is read by a model asked what data is worth ADDING.

    The global vocabulary would tell it `liquidation_spike_ratio` is already covered on a
    venue that has no liquidation feed — suppressing exactly the suggestion worth having."""
    binance = build_data_inventory([], [])
    hyperliquid = build_data_inventory([], [], venue=market_data.HYPERLIQUID)

    assert "liquidation_spike_ratio" in binance["mintable_features"]
    assert "liquidation_spike_ratio" not in hyperliquid["mintable_features"]
    assert "close" in hyperliquid["mintable_features"]
    assert hyperliquid["venue"] == market_data.HYPERLIQUID


def test_an_undeclared_venue_yields_an_empty_vocabulary_not_a_raise():
    # A description of what exists should report the emptiness, not refuse to be built. The
    # gates that must refuse an unknown venue (`known_features`, the template gate) do.
    inv = build_data_inventory([], [], venue="not_a_venue")
    assert inv["mintable_features"] == []
    assert inv["venue"] == "not_a_venue"


# --- suggestion judgment ------------------------------------------------------

def test_mock_provider_exercises_accept_and_reject():
    record = review_data_gaps(build_data_inventory([], []),
                              provider=MockDataReviewProvider(), now=NOW)
    assert record["suggested_count"] == 2 and record["accepted_count"] == 1
    accepted = [s for s in record["suggestions"] if s["accepted"]]
    assert accepted[0]["name"] == "open_interest_history"
    rejected = [s for s in record["suggestions"] if not s["accepted"]]
    assert "missing_rationale" in rejected[0]["problems"]
    assert record["collection_effect"] == "NONE"
    assert record["record_sha256"].startswith("sha256:")


def test_already_collected_source_is_rejected():
    verdict = evaluate_suggestion(
        {"name": "binance_futures_funding", "data_kind": "positioning",
         "rationale": "r", "expected_use": "u"},
        index=1, known_sources=frozenset({"binance_futures_funding"}))
    assert not verdict["accepted"] and "already_collected" in verdict["problems"]


def test_suggestions_capped_per_run():
    class Flood(MockDataReviewProvider):
        _ANSWER = {"suggestions": [
            {"name": f"s{i}", "data_kind": "other", "rationale": "r", "expected_use": "u"}
            for i in range(20)
        ]}
    record = review_data_gaps(build_data_inventory([], []), provider=Flood(), now=NOW)
    assert record["suggested_count"] == MAX_SUGGESTIONS_PER_RUN


# --- degradation --------------------------------------------------------------

def test_provider_failure_degrades_never_raises():
    class Failing:
        network_egress = False

        def generate(self, prompt, *, max_output_tokens, timeout_seconds):
            raise ProviderError("PROVIDER_UNAVAILABLE", "boom")

    record = review_data_gaps(build_data_inventory([], []), provider=Failing(), now=NOW)
    assert record["degraded"] == "DATA_REVIEW_DEGRADED"
    assert record["suggested_count"] == 0 and record["accepted_count"] == 0
    assert record["invocation"] is None


def test_unparseable_answer_degrades():
    class Garbage(MockDataReviewProvider):
        _ANSWER = {"summary": "no json here at all"}
    record = review_data_gaps(build_data_inventory([], []), provider=Garbage(), now=NOW)
    assert record["degraded"] == "DATA_REVIEW_DEGRADED"


# --- report sheet -------------------------------------------------------------

def test_report_names_accepted_and_rejected():
    record = review_data_gaps(build_data_inventory([], []),
                              provider=MockDataReviewProvider(), now=NOW)
    sheet = format_review_report(record)
    assert "open_interest_history" in sheet and "malformed_suggestion" in sheet
    assert "수집 효력 없음" in sheet


# --- scheduled fire -----------------------------------------------------------

def test_scheduled_data_review_fire_ledgers_the_record(tmp_path, monkeypatch):
    monkeypatch.delenv("MVP_VALIDATOR_PROVIDER", raising=False)
    schedule = build_schedule(kind=KIND_DATA_REVIEW, request="", interval_seconds=86400,
                              created_by="op", now="2026-07-24T06:00:00Z")
    store = ScheduleStore(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.add(schedule)
    ledger = LedgerStore(tmp_path / LEDGER_REL)
    summary = run_due(store, now=NOW, control_store=ControlStore(tmp_path),
                      ledger=ledger, repo_root=tmp_path)
    assert summary["fired"] == 1
    status = summary["results"][0]["status"]
    assert status.startswith("data_review=")
    assert "sheet_not_sent:" in status  # no operator channel in a bare tmp root
    rows = [json.loads(line) for line in
            (tmp_path / LEDGER_REL / RECORDS_FILE).read_text(encoding="utf-8").splitlines()]
    reviews = [r for r in rows if r["kind"] == DATA_REVIEW_LEDGER_KIND]
    assert len(reviews) == 1
    assert reviews[0]["record"]["collection_effect"] == "NONE"

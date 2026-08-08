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
    DATA_REVIEW_DEGRADED,
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


def test_an_undeclared_venue_has_no_vocabulary_rather_than_an_empty_one():
    """`market_data` states the rule this used to break, about this exact table:

        "this venue provides nothing" and "nobody has said what this venue provides"
        must not produce the same silence.

    It returned `[]` for both. An empty list is the claim that nothing is mintable; `None`
    is the absence of an answer, which is what an undeclared venue actually leaves behind.
    """
    inv = build_data_inventory([], [], venue="not_a_venue")
    assert inv["mintable_features"] is None
    assert inv["venue"] == "not_a_venue"
    # Still built, not refused — this record describes, and the module degrades rather
    # than blocks. The gates that must refuse an unknown venue still do.
    assert inv["current_sources"] and "feed_status" in inv


def test_an_unknown_vocabulary_degrades_before_the_provider_is_paid():
    """The review asks what data is worth ADDING. Against a venue nobody has described,
    every suggestion reads as new because nothing is known to be covered — so the answer
    could not be used, and buying it is the avoidable half."""
    class Counting(MockDataReviewProvider):
        calls = 0

        def generate(self, prompt, *, max_output_tokens, timeout_seconds):
            type(self).calls += 1
            return super().generate(
                prompt, max_output_tokens=max_output_tokens, timeout_seconds=timeout_seconds
            )

    provider = Counting()
    record = review_data_gaps(
        build_data_inventory([], [], venue="not_a_venue"), provider=provider, now=NOW
    )
    assert Counting.calls == 0, "an unusable answer must not be bought"
    assert record["degraded"] == DATA_REVIEW_DEGRADED
    assert "not_a_venue" in record["degraded_reason"]
    assert record["suggested_count"] == 0 and record["accepted_count"] == 0
    assert record["invocation"] is None
    # The shape of every other record — built once at the bottom, so nothing drifts.
    assert record["collection_effect"] == "NONE"
    assert record["record_sha256"].startswith("sha256:")


def test_a_declared_venue_still_calls_the_provider():
    # The inverse pin: the new gate must not degrade a review that has a real vocabulary.
    record = review_data_gaps(build_data_inventory([], []), provider=MockDataReviewProvider(),
                              now=NOW)
    assert "degraded" not in record and record["invocation"] is not None


# --- the inventory stays true -------------------------------------------------

# Every series `attach_feeds` reports a status for, as of 2026-08-08. A SNAPSHOT and a
# decision point, the `SHARED_ACROSS_MODULES` precedent in `test_diagnostic_code_index`:
# the list is not a second source of truth, it is the thing that makes the ninth series a
# choice somebody makes rather than one that lands unnoticed.
COLLECTED_SERIES = frozenset({
    "funding", "mark_prices", "index_prices", "premium_index",
    "positioning", "liquidations", "open_interest", "open_interest_1h",
})


def test_a_new_collected_series_forces_the_inventory_to_be_revisited(tmp_path):
    """What actually failed here was not the list — it was that nothing pointed at it.

    Four sources shipped after 2026-07-25 and `CURRENT_SOURCES` moved for none of them, so
    the review was told the runtime collects four series while it collects eight, and
    `evaluate_suggestion` was checking `already_collected` against the short list. Both of
    those are silent: an under-reported inventory produces a plausible review that
    re-proposes collected data, and no test in the repo read the two together.
    """
    from runtime.mvp_runtime.crypto import cycle
    from runtime.mvp_runtime.crypto.data_review import CURRENT_SOURCES

    class _Feed:
        feed_id = "fake"

        def liquidation_history(self, symbol, *, days, timeout_seconds):
            return []

        def open_interest_history(self, symbol, *, days, timeout_seconds, **kwargs):
            return []

    # A collector with no capabilities: every leg reports `absent`, which is a status all
    # the same. This test is about which series EXIST, not about whether they answered.
    _, status = cycle.attach_feeds(
        {"symbol": "BTCUSDT", "timeframe": "1d", "candles": []},
        collector=object(), liquidation_feed=_Feed(), now=NOW, root=tmp_path,
    )
    assert set(status) == COLLECTED_SERIES, (
        "attach_feeds gained or lost a series — update CURRENT_SOURCES in data_review.py "
        "in the same change, then this pin"
    )
    # The store-backed accumulators have no feed_status key at all (`candle_archive` is its
    # own schedule kind), so the pin above cannot see them and they are named directly.
    sources = {s["source"] for s in CURRENT_SOURCES}
    assert {"coinalyze_open_interest", "coinalyze_open_interest_1h",
            "binance_futures_positioning", "dex_candle_archive"} <= sources


def test_an_accumulating_source_says_it_feeds_nothing_yet():
    """Collected and usable-by-a-template are different facts, and the reviewer needs both.

    Listing `positioning` bare would read as "covered", suppressing the suggestion that
    matters most about it — that nothing reads it. Omitting it would invite a proposal to
    collect what is already accumulating. Only the qualified entry is honest."""
    from runtime.mvp_runtime.crypto.data_review import CURRENT_SOURCES

    by_source = {s["source"]: s["content"] for s in CURRENT_SOURCES}
    for source in ("coinalyze_open_interest_1h", "binance_futures_positioning",
                   "dex_candle_archive"):
        assert "Feeds no feature yet" in by_source[source], source
    # And the inverse: a series a family actually reads must not carry the disclaimer.
    assert "Feeds no feature yet" not in by_source["coinalyze_open_interest"]


# --- suggestion judgment ------------------------------------------------------

def test_mock_provider_exercises_accept_and_reject():
    record = review_data_gaps(build_data_inventory([], []),
                              provider=MockDataReviewProvider(), now=NOW)
    assert record["suggested_count"] == 2 and record["accepted_count"] == 1
    accepted = [s for s in record["suggestions"] if s["accepted"]]
    assert accepted[0]["name"] == "orderbook_depth_imbalance"
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
    assert "orderbook_depth_imbalance" in sheet and "malformed_suggestion" in sheet
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

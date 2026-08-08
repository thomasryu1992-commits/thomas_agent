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


class _FailingProvider:
    """A provider that fails the way the live one did: a shape error, not a transport one."""

    network_egress = False

    def generate(self, prompt, *, max_output_tokens, timeout_seconds):
        raise ProviderError("MALFORMED_RESPONSE",
                            "hosted provider response missing required analysis fields")



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


# --- a stalled loop reaches the failure alert ---------------------------------

def test_one_degraded_review_stays_quiet(tmp_path):
    """Degrade-never-block is right for one bad fire. The sheet carries `DEGRADED: {reason}`
    and the fire completes — the documented posture, unchanged."""
    from runtime.mvp_runtime.crypto import data_review

    record = review_data_gaps(build_data_inventory([], []),
                              provider=_FailingProvider(), now=NOW)
    assert record["degraded"] == DATA_REVIEW_DEGRADED
    assert data_review.review_loop_is_stalled(record, None) is False
    assert data_review.review_loop_is_stalled(record, "data_review=3/5 review=x") is False


def test_a_second_degraded_review_in_a_row_is_a_stall():
    """`scheduler`'s candle-archive branch settled this shape: off on purpose is quiet, on and
    not working RAISES, because a COMPLETED summary reaches nobody. At a weekly cadence two in
    a row is already a fortnight of a review loop producing nothing."""
    from runtime.mvp_runtime.crypto import data_review

    record = review_data_gaps(build_data_inventory([], []),
                              provider=_FailingProvider(), now=NOW)
    assert data_review.review_loop_is_stalled(record, "data_review=0/0 review=abc") is True
    # The raised status is recognised too, or raising would clear the marker the next fire
    # reads and the alert would fire every OTHER week.
    assert data_review.review_loop_is_stalled(
        record, f"failed:{data_review.DATA_REVIEW_STALLED}") is True


def test_a_healthy_review_is_never_a_stall():
    """`0/0` is uniquely the degraded signature — `review_data_gaps` only ever produces
    `suggested_count == 0` with `degraded` set — so a healthy run cannot be read as one."""
    from runtime.mvp_runtime.crypto import data_review

    healthy = review_data_gaps(build_data_inventory([], []),
                               provider=MockDataReviewProvider(), now=NOW)
    assert "degraded" not in healthy
    assert data_review.review_loop_is_stalled(healthy, "data_review=0/0 review=abc") is False


def test_the_scheduled_fire_raises_on_a_stalled_loop(tmp_path, monkeypatch):
    """End to end: the second degraded fire lands as a FAILED scheduler event, which is an
    alert surface, instead of a COMPLETED one, which is not."""
    from runtime.mvp_runtime.crypto import data_review
    from runtime.mvp_runtime import providers

    monkeypatch.setattr(providers, "select_validator_provider",
                        lambda **kwargs: _FailingProvider())
    schedule = build_schedule(kind=KIND_DATA_REVIEW, request="", interval_seconds=86400,
                              created_by="op", now="2026-07-24T06:00:00Z")
    store = ScheduleStore(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.add(schedule)
    ledger = LedgerStore(tmp_path / LEDGER_REL)

    first = run_due(store, now=NOW, control_store=ControlStore(tmp_path),
                    ledger=ledger, repo_root=tmp_path)
    assert first["fired"] == 1 and first["failed"] == 0
    assert first["results"][0]["status"].startswith("data_review=0/0")

    second = run_due(store, now="2026-07-26T06:00:00Z", control_store=ControlStore(tmp_path),
                     ledger=ledger, repo_root=tmp_path)
    assert second["failed"] == 1
    assert data_review.DATA_REVIEW_STALLED in second["results"][0]["status"]
    # The evidence and the operator's copy are not the price of the alert: both fires
    # ledgered their record before raising.
    rows = [json.loads(line) for line in
            (tmp_path / LEDGER_REL / RECORDS_FILE).read_text(encoding="utf-8").splitlines()]
    assert len([r for r in rows if r["kind"] == DATA_REVIEW_LEDGER_KIND]) == 2

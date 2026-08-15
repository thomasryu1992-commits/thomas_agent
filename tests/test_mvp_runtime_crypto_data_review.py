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
    build_review_prompt,
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


# --- the inventory stays true -------------------------------------------------

# Every series `attach_feeds` reports a status for, as of 2026-08-15. A SNAPSHOT and a
# decision point, the `SHARED_ACROSS_MODULES` precedent in `test_diagnostic_code_index`:
# the list is not a second source of truth, it is the thing that makes the ninth series a
# choice somebody makes rather than one that lands unnoticed.
#
# It worked. ``orderbook`` is the ninth, and this pin is what stopped it landing unnoticed —
# `CURRENT_SOURCES` had been updated in the same change but this line had not, so the guard
# failed on exactly the half that was forgotten rather than on the half that was done.
COLLECTED_SERIES = frozenset({
    "funding", "mark_prices", "index_prices", "premium_index",
    "positioning", "liquidations", "open_interest", "open_interest_1h",
    "orderbook",
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
            "binance_futures_positioning", "dex_candle_archive",
            "binance_futures_order_book"} <= sources


def test_an_accumulating_source_says_it_feeds_nothing_yet():
    """Collected and usable-by-a-template are different facts, and the reviewer needs both.

    Listing an accumulator bare would read as "covered", suppressing the suggestion that
    matters most about it — that nothing reads it. Omitting it would invite a proposal to
    collect what is already accumulating. Only the qualified entry is honest.

    Asserted against the CODE rather than a list of names, because this claim is exactly the
    one that goes stale: `positioning_store`'s own docstring said "it feeds nothing" long
    after `_positioning_columns` started reading it, and that stale line is where this
    module's first draft of the entry came from."""
    from runtime.mvp_runtime.crypto import factory, features
    from runtime.mvp_runtime.crypto.data_review import CURRENT_SOURCES

    by_source = {s["source"]: s["content"] for s in CURRENT_SOURCES}
    for source in ("coinalyze_open_interest_1h", "dex_candle_archive"):
        assert "Feeds no feature yet" in by_source[source], source
    # A series a family actually reads must not carry the disclaimer. Positioning belongs
    # here and not above: `attach_positioning` puts the store's rows on the snapshot and
    # `_positioning_columns` turns them into MINTABLE columns. What is gated is whether the
    # two POSITIONING_FAMILIES are offered, which is a different sentence.
    assert "Feeds no feature yet" not in by_source["coinalyze_open_interest"]
    assert "Feeds no feature yet" not in by_source["binance_futures_positioning"]
    assert "positioning_*" in by_source["binance_futures_positioning"]
    numeric, categorical = factory.known_features(market_data.BINANCE_FUTURES)
    mintable = numeric | frozenset(categorical)
    assert set(features.POSITIONING_NUMERIC_COLUMNS) <= mintable, (
        "the entry claims the positioning columns are mintable — if they stop being, the "
        "'feeds no feature yet' wording is the honest one again"
    )


# --- suggestion judgment ------------------------------------------------------

def test_mock_provider_exercises_accept_and_reject():
    record = review_data_gaps(build_data_inventory([], []),
                              provider=MockDataReviewProvider(), now=NOW)
    assert record["suggested_count"] == 2 and record["accepted_count"] == 1
    accepted = [s for s in record["suggestions"] if s["accepted"]]
    assert accepted[0]["name"] == "option_implied_volatility"
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
    assert "option_implied_volatility" in sheet and "malformed_suggestion" in sheet
    assert "수집 효력 없음" in sheet


# --- scheduled fire -----------------------------------------------------------

def _worker_in_process(monkeypatch, tmp_path, provider=None):
    """Route the scheduler's data-review delegation to the worker's own job handler.

    Since Phase 2 D5 the model call runs in `pipeline-worker`, so a scheduled fire crosses a
    socket. These tests keep exercising the whole chain — scheduler builds the inventory,
    worker runs the review, scheduler appends and judges — by replacing only the transport.
    The alternative (patching `delegate_data_review` away) would leave the worker's job path,
    which is where the model call now lives, untested by every one of them.
    """
    from runtime.mvp_runtime import pipeline_worker, scheduler as sched
    from runtime.mvp_runtime.control import ControlStore as _CS

    def _call(path, frame, **kwargs):
        return pipeline_worker.apply_work(
            frame, control_store=_CS(tmp_path),
            providers={"validator_provider": provider} if provider is not None else None,
        )

    monkeypatch.setattr(sched.socket_door, "call_door", _call)


def test_scheduled_data_review_fire_ledgers_the_record(tmp_path, monkeypatch):
    monkeypatch.delenv("MVP_VALIDATOR_PROVIDER", raising=False)
    _worker_in_process(monkeypatch, tmp_path)
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


def test_a_delegated_review_is_stamped_and_uniquely_identified(tmp_path, monkeypatch):
    """A record written on the far side of the door still knows when it was written.

    `build_worker_door`'s `_apply` calls `apply_work` without a `now` — so `None` is what a
    job actually receives in production, and `_worker_in_process` above reproduces that by
    omitting it too. The pipeline path never noticed because `run_task` resolves `None`
    against the clock; the job path did not, and the data review stamps `created_at` from it
    AND seeds `review_id` off it.

    So the 2026-08-15 fire — the first to run through this door — wrote `created_at: null` and
    `review_id: data_review_246e68234b481e1cb348`, which is `short_id("data_review", {"at":
    None})`: a CONSTANT. Every weekly review after it would have been filed under the same id,
    making the ledger's `trace_id` and the schedule's `last_status` identical week to week.

    Asserted as a derivation rather than as "not that literal id": the id being a function of
    the stamp is the property that makes it unique per fire, and it survives a change to
    either the seed or the id format."""
    from runtime.mvp_runtime import timeutil
    from runtime.read_only_kernel import integrity

    monkeypatch.delenv("MVP_VALIDATOR_PROVIDER", raising=False)
    _worker_in_process(monkeypatch, tmp_path)
    schedule = build_schedule(kind=KIND_DATA_REVIEW, request="", interval_seconds=86400,
                              created_by="op", now="2026-07-24T06:00:00Z")
    store = ScheduleStore(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.add(schedule)
    ledger = LedgerStore(tmp_path / LEDGER_REL)
    run_due(store, now=NOW, control_store=ControlStore(tmp_path),
            ledger=ledger, repo_root=tmp_path)
    rows = [json.loads(line) for line in
            (tmp_path / LEDGER_REL / RECORDS_FILE).read_text(encoding="utf-8").splitlines()]
    record = [r for r in rows if r["kind"] == DATA_REVIEW_LEDGER_KIND][0]["record"]

    created_at = record["created_at"]
    assert created_at is not None
    assert timeutil.FIXED_UTC_PATTERN.match(created_at), created_at
    assert record["review_id"] == integrity.short_id("data_review", {"at": created_at})
    assert record["review_id"] != integrity.short_id("data_review", {"at": None})


# --- the prompt asks for a shape the provider will actually hand back ---------

class _RealParseProvider:
    """A provider that is fake only in the wire: it returns whatever a model would have
    returned, through the REAL ``GroqProvider._parse``.

    The point is that nothing here re-states the required key set. The answer is built from
    the review prompt's OWN stated example, and the acceptance is the production parser, so
    a prompt edit that goes back to asking for a bare object fails this test at the provider
    exactly as it failed on the live host."""

    model_id = "groq"
    model_version = "test"
    network_egress = False

    def __init__(self, answer: dict):
        self._answer = answer

    def generate(self, prompt: str, *, max_output_tokens: int, timeout_seconds: int):
        from runtime.mvp_runtime.providers import GroqProvider

        wire = json.dumps({
            "choices": [{"message": {"content": json.dumps(self._answer)}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        })
        return GroqProvider(authorization=None)._parse(wire)


def _prompt_example(prompt: str) -> dict:
    """The example object the prompt itself states — read out of the prompt, never copied."""
    line = next(ln for ln in prompt.splitlines() if ln.lstrip().startswith('{"summary"'))
    return json.loads(line)


def test_the_review_prompt_asks_for_a_shape_the_hosted_parser_accepts():
    """The defect that made this review degrade on every live fire it ever had.

    The hosted providers parse every answer through `_parse_hosted_response`, which rejects
    anything missing summary/key_findings/facts. v1 of this prompt asked for a bare
    ``{"suggestions": [...]}``, so a perfectly good answer died as MALFORMED_RESPONSE before
    this module saw it — 2026-08-01 and 2026-08-08 in the ledger, reproduced 3-of-4 live on
    2026-08-10. Asserting the property (an answer written to this prompt survives the real
    parser) rather than the spelling (the word "summary" appears): the prompt could be
    reworded freely and this still holds, and could not go back to the bare object."""
    inventory = build_data_inventory([], [])
    answer = _prompt_example(build_review_prompt(inventory))
    record = review_data_gaps(inventory, provider=_RealParseProvider(answer), now=NOW)

    assert "degraded" not in record, record.get("degraded_reason")
    assert record["suggested_count"] >= 1
    assert record["invocation"]["prompt_version"] == record["prompt_version"]


def test_the_bare_suggestions_object_is_what_the_provider_refuses():
    """The other half of the same fact, stated once so the shape above reads as necessary
    rather than as taste: strip the envelope from the prompt's own example and the identical
    suggestion list fails at the provider with the live host's exact reason_code."""
    inventory = build_data_inventory([], [])
    example = _prompt_example(build_review_prompt(inventory))
    bare = {"suggestions": example["suggestions"]}

    with pytest.raises(ProviderError) as caught:
        _RealParseProvider(bare).generate("x", max_output_tokens=10, timeout_seconds=1)
    assert caught.value.reason_code == "MALFORMED_RESPONSE"

    # And the module's own posture over that failure is unchanged: degraded, never blocking.
    record = review_data_gaps(inventory, provider=_RealParseProvider(bare), now=NOW)
    assert record["degraded"] == DATA_REVIEW_DEGRADED
    assert record["suggested_count"] == 0


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
    _worker_in_process(monkeypatch, tmp_path, provider=_FailingProvider())
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

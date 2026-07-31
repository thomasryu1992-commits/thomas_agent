"""The backfill walk's three terminal states, which the operator acts on differently.

A leg unmapped because the listing ran out is permanently unreadable — its topic is gone and
no later run brings it back. A leg unmapped because the walk hit its budget is merely unread.
The first version of this script collapsed both into "topic no longer listed", which told an
operator to write off groups that were only further down the page.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.errors import ToolError
from runtime.mvp_runtime.predmarket import market_data as md
from scripts import backfill_predmarket_topic_ids as bf


class _Listing:
    """A fake Binance collector serving `pages` of topics, each holding one market."""

    LIST_PATH = "/list"
    DETAIL_PATH = "/detail"
    PAGE_LIMIT = 2

    def __init__(self, topics):
        self._topics = list(topics)
        self.detail_calls = 0

    def _signed_get(self, path, params, *, timeout_seconds):
        if path == self.LIST_PATH:
            offset = int(params["offset"])
            page = self._topics[offset:offset + self.PAGE_LIMIT]
            return {"marketTopics": [
                {"marketTopicId": t, "question": f"q{t}", "endDate": 1893456000000,
                 "status": "REGISTERED"} for t in page]}
        self.detail_calls += 1
        topic = str(params["marketTopicId"])
        return {"marketTopicId": topic, "markets": [{
            "marketId": int(topic), "conditionId": f"0x{topic}", "tradingStatus": "OPEN",
            "tradeVolume": "1", "question": f"q{topic}",
            "outcomes": [{"name": "YES", "tokenId": f"tok{topic}"}]}]}


def _walk(monkeypatch, topics, wanted, *, max_topics=100):
    listing = _Listing(topics)
    monkeypatch.setattr(md, "select_pred_market_collector", lambda venue, **kw: listing)
    return bf.discover_topic_ids(set(wanted), max_topics=max_topics), listing


def test_finding_every_leg_stops_the_walk_early(monkeypatch):
    """The remaining pages would cost signed calls to confirm something already known, against
    endpoints weighted 200 that share a key with the running scan."""
    (found, read, stop), listing = _walk(monkeypatch, ["11", "22", "33", "44"], ["11:tok11"])
    assert found == {"11:tok11": "11"}
    assert stop == bf.STOP_COMPLETE
    assert listing.detail_calls == 1        # not four


def test_running_out_of_listing_is_reported_as_permanent(monkeypatch):
    """The topic has left the listing, so no later run maps this leg. That is the finding."""
    (found, read, stop), _ = _walk(monkeypatch, ["11", "22"], ["11:tok11", "99:tok99"])
    assert found == {"11:tok11": "11"}
    assert stop == bf.STOP_EXHAUSTED


def test_running_out_of_budget_is_not_reported_as_permanent(monkeypatch):
    """The distinction the first version lost. This leg may be one page further on, and an
    operator told 'no longer listed' would write off a group that is merely unread."""
    (found, read, stop), _ = _walk(
        monkeypatch, ["11", "22", "33", "44"], ["44:tok44"], max_topics=2)
    assert found == {}
    assert stop == bf.STOP_CAPPED
    assert read == 2


def test_a_closed_gate_refuses_rather_than_writing_a_mocks_ids(monkeypatch):
    """A confirmation is durable. The mock answers successfully, so without this door a
    rehearsal would write ids derived from (venue, index) onto operator-confirmed groups."""
    monkeypatch.setattr(
        md, "select_pred_market_collector",
        lambda venue, **kw: md.MockPredMarketCollector(md.BINANCE))
    with pytest.raises(ToolError) as exc:
        bf.discover_topic_ids({"1:t"}, max_topics=10)
    assert exc.value.reason_code == md.SYNTHETIC_SOURCE


def test_only_confirmed_binance_legs_without_an_id_are_wanted():
    groups = [
        {"status": "CONFIRMED", "event_id": "e1", "legs": [
            {"venue": "binance", "market_id": "1:t"},                       # wanted
            {"venue": "kalshi", "market_id": "K-1"},                        # other venue
        ]},
        {"status": "CONFIRMED", "event_id": "e2", "legs": [
            {"venue": "binance", "market_id": "2:t", "topic_id": "already"},  # has one
        ]},
        {"status": "RETIRED", "event_id": "e3", "legs": [
            {"venue": "binance", "market_id": "3:t"},                       # withdrawn
        ]},
    ]
    assert bf.legs_needing_topic_id(groups) == {"e1": ["1:t"]}

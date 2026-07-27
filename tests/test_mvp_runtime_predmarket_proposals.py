"""Discovery runs, recorded — and the reason a scheduled run has to write anything down.

``propose`` by hand answers "what is pairable right now?". The question PM1 needs answered
is "what became pairable while nobody was looking?" — a pairing that appeared and resolved
between two hand-run commands leaves no trace it was ever missed. Hence a cadence, and hence
a store: without one, a scheduled run is the same work producing output nobody reads.

The tests that carry weight are the ones about what a record must *not* let you conclude:
that a truncated list was complete, that a run confirmed something, or that forty unchanged
pairings are forty new ones.
"""

from __future__ import annotations

import json

import pytest

from runtime.mvp_runtime.errors import ToolError
from runtime.mvp_runtime.predmarket import pairs, proposals

NOW = "2026-07-27T08:00:00Z"


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(pairs, "_repo_root", lambda: tmp_path)
    return tmp_path


def _candidate(left="K1", right="P1", left_venue="kalshi", right_venue="polymarket"):
    return {
        "left_venue": left_venue, "left_market_id": left,
        "right_venue": right_venue, "right_market_id": right,
        "left_title": f"question {left}", "right_title": f"question {right}",
        "title_similarity": 0.9,
    }


def _result(candidates=None, **over):
    base = {
        "matching_version": "predmarket_matching.v0.1",
        "venues": ["kalshi", "polymarket"],
        "market_counts": {"kalshi": 12, "polymarket": 9},
        "judged_count": 108,
        "candidates": list(candidates if candidates is not None else [_candidate()]),
        "near_miss_total": 71,
        "boilerplate_only_count": 4,
        "screening": {"kalshi": {"excluded_count": 3}},
        "venue_errors": {},
    }
    base.update(over)
    return base


# --- the record -----------------------------------------------------------------

def test_a_record_carries_what_was_refused_as_well_as_what_was_found():
    """A run that proposed nothing because every market was screened out is a different
    finding from one that judged everything and matched nothing."""
    record = proposals.build_proposal_record(_result(candidates=[]), now=NOW)
    assert record["candidate_count"] == 0
    assert record["judged_count"] == 108
    assert record["screening"]["kalshi"]["excluded_count"] == 3
    assert record["near_miss_total"] == 71 and record["boilerplate_only_count"] == 4


def test_a_venue_that_did_not_answer_is_not_a_venue_with_nothing_to_offer():
    record = proposals.build_proposal_record(
        _result(candidates=[], venue_errors={"binance": "TOOL_TRANSPORT"}), now=NOW)
    assert record["venue_errors"] == {"binance": "TOOL_TRANSPORT"}


def test_a_truncated_candidate_list_says_it_was_truncated():
    """No silent caps. A short list that did not say it was short reads as a complete one,
    and the pairing that mattered is the one that fell off the end."""
    record = proposals.build_proposal_record(
        _result([_candidate(f"K{i}", f"P{i}") for i in range(10)]), now=NOW, limit=4)
    assert record["candidate_count"] == 10        # what was found
    assert len(record["candidates"]) == 4         # what was kept
    assert record["candidates_truncated"] == 6


def test_near_misses_are_counted_but_not_carried():
    """Thousands of diagnostic rows at a six-hour cadence make the store unreadable within a
    day. The count still travels, so the record says how much was refused."""
    record = proposals.build_proposal_record(_result(), now=NOW)
    assert "near_misses" not in record
    assert record["near_miss_total"] == 71


def test_a_record_says_on_its_face_that_it_confirms_nothing():
    record = proposals.build_proposal_record(_result(), now=NOW)
    assert record["confirms_nothing"] is True
    assert record["authorizes_trading"] is False
    # And the module holds no writer for the group store.
    for name in ("confirm_group", "build_event_group", "add_leg"):
        assert not hasattr(proposals, name)


def test_two_runs_with_different_findings_get_different_ids():
    a = proposals.build_proposal_record(_result(), now=NOW)
    b = proposals.build_proposal_record(_result(candidates=[]), now=NOW)
    assert a["proposal_id"] != b["proposal_id"]


# --- the store ------------------------------------------------------------------

def test_a_proposal_round_trips_and_is_hash_verified(state):
    proposals.append_proposal(proposals.build_proposal_record(_result(), now=NOW), root=state)
    rows = proposals.read_proposals(state)
    assert len(rows) == 1 and rows[0]["candidate_count"] == 1

    path = proposals.proposals_path(state)
    row = json.loads(path.read_text(encoding="utf-8").strip())
    row["candidate_count"] = 99                     # a much better day, by hand
    path.write_text(json.dumps(row, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(ToolError) as exc:
        proposals.read_proposals(state)
    assert exc.value.reason_code == proposals.PROPOSALS_TAMPERED


def test_an_empty_store_is_not_an_unreadable_one(state):
    assert proposals.read_proposals(state) == []
    proposals.proposals_path(state).parent.mkdir(parents=True, exist_ok=True)
    proposals.proposals_path(state).write_text("{not json\n", encoding="utf-8")
    with pytest.raises(ToolError) as exc:
        proposals.read_proposals(state)
    assert exc.value.reason_code == proposals.PROPOSALS_UNREADABLE


def test_the_store_is_append_only(state):
    for i in range(3):
        proposals.append_proposal(
            proposals.build_proposal_record(_result([_candidate(f"K{i}")]), now=NOW), root=state)
    assert len(proposals.read_proposals(state)) == 3


# --- what changed ---------------------------------------------------------------

def test_only_pairings_no_earlier_run_proposed_count_as_new():
    """The point of a cadence. An operator re-reading forty unchanged pairings every six
    hours stops reading them, and the one new pairing arrives in a list they have learned
    to skip."""
    first = proposals.build_proposal_record(
        _result([_candidate("K1"), _candidate("K2", "P2")]), now=NOW)
    second = proposals.build_proposal_record(
        _result([_candidate("K1"), _candidate("K2", "P2"), _candidate("K3", "P3")]), now=NOW)

    assert len(proposals.new_candidates(first, [])) == 2
    fresh = proposals.new_candidates(second, [first])
    assert [c["left_market_id"] for c in fresh] == ["K3"]


def test_the_same_two_markets_paired_on_different_venues_are_different_candidates():
    """A market id alone does not identify a leg — the venue is part of the key, or a Kalshi
    ticker would suppress a Binance pairing that happens to share a market id."""
    first = proposals.build_proposal_record(_result([_candidate("X", "Y")]), now=NOW)
    other = proposals.build_proposal_record(
        _result([_candidate("X", "Y", left_venue="binance")]), now=NOW)
    assert len(proposals.new_candidates(other, [first])) == 1


def test_the_status_line_is_ascii_and_reports_the_cut(state):
    record = proposals.build_proposal_record(
        _result([_candidate(f"K{i}", f"P{i}") for i in range(10)],
                venue_errors={"binance": "TOOL_TRANSPORT"}),
        now=NOW, limit=4)
    line = proposals.proposal_status_line(record, new_count=2)
    line.encode("ascii")
    assert "10 candidate(s)" in line and "6 not recorded" in line
    assert "2 new" in line and "degraded=binance" in line

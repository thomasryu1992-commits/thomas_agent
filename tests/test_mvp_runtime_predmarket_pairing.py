"""PM1 event pairing — the matcher proposes, the operator decides, the store proves it.

A wrong pair is the worst failure this track has: it does not raise, it produces a stable
price gap between two different questions forever, and every number downstream stays
internally consistent while being about nothing. So the tests here are mostly about refusals.

Three properties carry the weight:

1. **Nothing auto-confirms.** No similarity score, however high, puts a pair in the store.
2. **Unknown is never mismatch.** A Kalshi market has no category; a matcher that read that
   as disagreement would refuse every Kalshi pair — the same family of bug as reading an
   absent price as zero.
3. **The refusal is diagnosable.** Every judgement carries which gate failed and by how
   much, because that record is the only thing that makes the rules improvable later.
"""

from __future__ import annotations

import json

import pytest

from runtime.mvp_runtime.errors import ToolError
from runtime.mvp_runtime.predmarket import matching, pairs
from runtime.mvp_runtime.predmarket.market_data import KALSHI, POLYMARKET, PredMarket

NOW = "2026-07-26T12:00:00Z"


def _market(venue, market_id, title, *, close="2026-12-31T23:59:00Z", category=None):
    return PredMarket(
        venue=venue, market_id=market_id, group_id=None, title=title,
        close_time=close, status="active", category=category,
    )


def _kalshi(title="Will the Fed cut rates in December?", **kw):
    return _market(KALSHI, kw.pop("market_id", "FED-26DEC-CUT"), title, **kw)


def _poly(title="Will the Fed cut rates in December?", **kw):
    return _market(POLYMARKET, kw.pop("market_id", "tok-yes-fed"), title, **kw)


# --- normalization and scoring --------------------------------------------------

def test_venue_phrasings_of_one_event_fold_together():
    """The synonym map is where a diagnosed miss gets fixed, so its entries must actually
    fold: FOMC and Fed are the same institution, BTC and Bitcoin the same asset."""
    assert matching.normalize_tokens("Will the FOMC cut rates?") == ("fed", "cut", "rates")
    assert "bitcoin" in matching.normalize_tokens("Will BTC close above 100k?")


def test_numbers_are_kept_because_they_are_the_question():
    """'above 100k' and 'above 90k' are different questions. A matcher that dropped digits
    would pair them and report a permanent, entirely fake spread."""
    high = _kalshi("Will BTC close above 100k on Dec 31?")
    low = _poly("Will Bitcoin close above 90k on Dec 31?")
    assert matching.judge_pair(high, low).is_candidate is False


def test_identical_questions_score_one_and_pair():
    judged = matching.judge_pair(_kalshi(), _poly())
    assert judged.title_similarity == 1.0
    assert judged.is_candidate is True and judged.refusals == ()


def test_a_missing_category_is_unknown_not_a_mismatch():
    """Kalshi market objects carry no category at all. Reading that as disagreement would
    refuse every Kalshi pairing — absence is not evidence."""
    judged = matching.judge_pair(_kalshi(), _poly(category="Economics"))
    assert judged.category_agreement is None
    assert judged.is_candidate is True


def test_two_stated_categories_that_disagree_do_refuse():
    """A disagreement between two STATED categories is real evidence, unlike a missing one."""
    judged = matching.judge_pair(
        _kalshi(category="Politics"), _poly(category="Economics")
    )
    assert judged.category_agreement is False
    assert matching.CATEGORY_CONFLICT in judged.refusals
    assert judged.is_candidate is False


def test_an_unreadable_close_time_refuses_rather_than_assuming_agreement():
    """None is unknown, not zero: two markets whose close times cannot be compared have not
    passed that gate."""
    judged = matching.judge_pair(_kalshi(close="not a date"), _poly())
    assert judged.close_delta_hours is None
    assert matching.CLOSE_TIME_UNKNOWN in judged.refusals


def test_same_question_different_month_is_refused():
    judged = matching.judge_pair(_kalshi(), _poly(close="2026-06-30T23:59:00Z"))
    assert matching.CLOSE_TOO_FAR_APART in judged.refusals
    assert judged.close_delta_hours > matching.MAX_CLOSE_DELTA_HOURS


# --- the diagnosable near miss --------------------------------------------------

def test_a_near_miss_records_which_gate_failed_and_by_how_much():
    """The record that makes the rules improvable. When a later LLM pass proposes a pair
    these rules missed, this row already answers 'why did we miss it?' — without it the gap
    can be observed but never closed."""
    judged = matching.judge_pair(
        _kalshi("Will the Fed cut rates in December?"),
        _poly("Will the central bank lower interest rates in December?"),
    )
    assert judged.is_candidate is False
    assert judged.near_miss() is True
    row = judged.as_dict()
    assert matching.TITLE_TOO_DIFFERENT in row["refusals"]
    assert row["thresholds"]["min_title_similarity"] == matching.MIN_TITLE_SIMILARITY
    assert "december" in row["shared_tokens"]
    # The tokens that did NOT match are the actionable half: they name the synonym to add.
    assert row["unshared_tokens"]


def test_unrelated_questions_are_dropped_not_kept_as_near_misses():
    """Everything below the near-miss floor is simply a different question; keeping those
    would drown the record that is supposed to be read."""
    result = matching.generate_candidates(
        [_kalshi("Will the Fed cut rates in December?")],
        [_poly("Will it rain in Seoul tomorrow?")],
    )
    assert result["candidates"] == [] and result["near_misses"] == []
    assert result["judged_count"] == 1


def test_generation_judges_every_pairing_and_ranks_the_best_first():
    kalshi = [_kalshi(market_id="K1"), _kalshi("Will BTC close above 100k?", market_id="K2")]
    poly = [_poly(market_id="P1"), _poly("Will Bitcoin close above 100k?", market_id="P2")]
    result = matching.generate_candidates(kalshi, poly)
    assert result["judged_count"] == 4
    assert result["confirms_nothing"] is True
    scores = [c["title_similarity"] for c in result["candidates"]]
    assert scores == sorted(scores, reverse=True)


# --- confirmation is an operator act --------------------------------------------

def _record(**over):
    params = dict(
        kalshi_market_id="FED-26DEC-CUT",
        polymarket_market_id="tok-yes-fed",
        kalshi_title="Fed cuts in December",
        polymarket_title="Will the Fed cut rates in December?",
        criteria_note="both settle on the official FOMC statement for the December meeting",
        confirmed_by="thomas",
        now=NOW,
    )
    params.update(over)
    return pairs.build_pair_record(**params)


@pytest.mark.parametrize("note", ["", "   ", "ok", "checked"])
def test_confirming_without_a_resolution_criteria_note_is_refused(note):
    """The one input no algorithm can supply. Two markets can ask an identical question and
    settle differently on the same news; the note is the operator saying they looked."""
    with pytest.raises(ToolError) as exc:
        _record(criteria_note=note)
    assert exc.value.reason_code == pairs.MISSING_CRITERIA_NOTE


def test_a_confirmed_pair_authorizes_observation_and_says_so():
    record = _record()
    assert record["status"] == pairs.CONFIRMED
    assert record["authorizes_trading"] is False
    assert record["resolution_criteria_note"].startswith("both settle")


def test_the_pair_id_is_the_same_on_every_machine():
    assert pairs.pair_id("K1", "P1") == pairs.pair_id("K1", "P1")
    assert pairs.pair_id("K1", "P1") != pairs.pair_id("K1", "P2")


def test_a_pair_round_trips_and_is_hash_verified(tmp_path):
    pairs.confirm_pair(_record(), root=tmp_path)
    stored = pairs.read_pairs(tmp_path)
    assert [p["kalshi_market_id"] for p in stored] == ["FED-26DEC-CUT"]

    # A hand-edited pair is detected, not trusted: observing against a pairing nobody
    # approved in this form is exactly what the store exists to prevent.
    path = pairs.pairs_path(tmp_path)
    row = json.loads(path.read_text(encoding="utf-8").strip())
    row["polymarket_market_id"] = "tok-someone-elses-market"
    path.write_text(json.dumps(row, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(ToolError) as exc:
        pairs.read_pairs(tmp_path)
    assert exc.value.reason_code == pairs.PAIRS_TAMPERED


def test_one_market_belongs_to_at_most_one_pair(tmp_path):
    """Two pairs sharing a leg would count one exposure twice and claim two different
    Polymarket questions are the same Kalshi event."""
    pairs.confirm_pair(_record(), root=tmp_path)
    with pytest.raises(ToolError) as exc:
        pairs.confirm_pair(_record(polymarket_market_id="tok-other"), root=tmp_path)
    assert exc.value.reason_code == pairs.MARKET_ALREADY_PAIRED

    with pytest.raises(ToolError) as exc:
        pairs.confirm_pair(_record(kalshi_market_id="OTHER-TICKER"), root=tmp_path)
    assert exc.value.reason_code == pairs.MARKET_ALREADY_PAIRED


def test_an_empty_store_is_not_an_unreadable_one(tmp_path):
    """No pairs yet is a normal state that allows the CLI to run; an unreadable store means
    the operator's decisions cannot be recovered and must fail closed."""
    assert pairs.read_pairs(tmp_path) == []
    pairs.pairs_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    pairs.pairs_path(tmp_path).write_text("{not json\n", encoding="utf-8")
    with pytest.raises(ToolError) as exc:
        pairs.read_pairs(tmp_path)
    assert exc.value.reason_code == pairs.PAIRS_UNREADABLE


def test_retiring_keeps_the_history_and_frees_both_markets(tmp_path):
    """The correction path. The record that a pair was once confirmed, by whom and why it
    was withdrawn, is what a resolution-mismatch investigation reads."""
    stored = pairs.confirm_pair(_record(), root=tmp_path)
    retired = pairs.retire_pair(
        stored["pair_id"], reason="venues resolved differently in July", retired_by="thomas", now=NOW,
        root=tmp_path,
    )
    assert retired["status"] == pairs.RETIRED
    assert retired["retired_reason"].startswith("venues resolved")
    assert pairs.read_pairs(tmp_path) == []
    assert len(pairs.read_pairs(tmp_path, include_retired=True)) == 1

    # Both markets are pairable again — and the retired row still verifies.
    pairs.confirm_pair(_record(), root=tmp_path)
    assert len(pairs.read_pairs(tmp_path)) == 1


def test_retiring_a_pair_that_is_not_confirmed_is_refused(tmp_path):
    with pytest.raises(ToolError) as exc:
        pairs.retire_pair("nope", reason="x", retired_by="thomas", now=NOW, root=tmp_path)
    assert exc.value.reason_code == pairs.PAIR_NOT_FOUND


def test_the_matcher_cannot_write_a_pair():
    """The structural half of 'nothing auto-confirms': the module that scores has no way to
    store, and imports nothing that does."""
    assert not hasattr(matching, "confirm_pair")
    source = (matching.__doc__ or "").lower()
    assert "proposes only" in source or "proposal, never a pair" in source


# --- the operator's door --------------------------------------------------------

@pytest.fixture
def cli_root(tmp_path, monkeypatch):
    """Point the store's default root at tmp_path, so the CLI writes nowhere real."""
    monkeypatch.setattr(pairs, "_repo_root", lambda: tmp_path)
    return tmp_path


def test_propose_confirms_nothing(cli_root, capsys):
    from runtime.mvp_runtime.predmarket import pairs_cli

    assert pairs_cli.main(["propose", "--limit", "4"]) == 0
    out = capsys.readouterr().out
    assert "candidate(s)" in out
    assert "Nothing above is a pair" in out
    # The store is untouched: proposing is a read.
    assert pairs.read_pairs(cli_root) == []


def test_confirm_without_criteria_is_blocked_not_recorded(cli_root, capsys):
    from runtime.mvp_runtime.predmarket import pairs_cli

    code = pairs_cli.main(["confirm", "K1", "P1", "--criteria", "ok"])
    assert code == 2  # EXIT_BLOCKED
    assert pairs.MISSING_CRITERIA_NOTE in capsys.readouterr().err
    assert pairs.read_pairs(cli_root) == []


def test_confirm_then_list_round_trips_through_the_cli(cli_root, capsys):
    from runtime.mvp_runtime.predmarket import pairs_cli

    note = "both settle on the official FOMC statement for the December meeting"
    assert pairs_cli.main(["confirm", "K1", "P1", "--criteria", note]) == 0
    capsys.readouterr()

    assert pairs_cli.main(["list"]) == 0
    out = capsys.readouterr().out
    assert "kalshi:K1" in out and "polymarket:P1" in out
    assert note in out

    # And a confirmed market stops being proposed — the operator is never invited to
    # confirm a pairing the store would only refuse.
    stored = pairs.read_pairs(cli_root)
    assert len(stored) == 1 and stored[0]["authorizes_trading"] is False

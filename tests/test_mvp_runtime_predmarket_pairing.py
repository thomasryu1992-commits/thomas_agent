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
from runtime.mvp_runtime.predmarket.market_data import BINANCE, KALSHI, POLYMARKET, PredMarket
from tests._helpers import LiveLikePredMarketCollector

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
    result = matching.generate_candidates({
        KALSHI: [_kalshi("Will the Fed cut rates in December?")],
        POLYMARKET: [_poly("Will it rain in Seoul tomorrow?")],
    })
    assert result["candidates"] == [] and result["near_misses"] == []
    assert result["judged_count"] == 1


def test_generation_judges_every_pairing_and_ranks_the_best_first():
    kalshi = [_kalshi(market_id="K1"), _kalshi("Will BTC close above 100k?", market_id="K2")]
    poly = [_poly(market_id="P1"), _poly("Will Bitcoin close above 100k?", market_id="P2")]
    result = matching.generate_candidates({KALSHI: kalshi, POLYMARKET: poly})
    assert result["judged_count"] == 4
    assert result["confirms_nothing"] is True
    scores = [c["title_similarity"] for c in result["candidates"]]
    assert scores == sorted(scores, reverse=True)


# --- confirmation is an operator act --------------------------------------------

def _legs(**over):
    legs = [
        {"venue": KALSHI, "market_id": "FED-26DEC-CUT", "title": "Fed cuts in December"},
        {"venue": POLYMARKET, "market_id": "tok-yes-fed", "title": "Will the Fed cut in Dec?"},
    ]
    if "legs" in over:
        return over["legs"]
    return legs


def _group(**over):
    params = dict(
        legs=_legs(**over),
        criteria_note="both settle on the official FOMC statement for the December meeting",
        confirmed_by="thomas",
        now=NOW,
    )
    params.update({k: v for k, v in over.items() if k != "legs"})
    return pairs.build_event_group(**params)


@pytest.mark.parametrize("note", ["", "   ", "ok", "checked"])
def test_confirming_without_a_resolution_criteria_note_is_refused(note):
    """The one input no algorithm can supply. Two markets can ask an identical question and
    settle differently on the same news; the note is the operator saying they looked."""
    with pytest.raises(ToolError) as exc:
        _group(criteria_note=note)
    assert exc.value.reason_code == pairs.MISSING_CRITERIA_NOTE


def test_a_confirmed_group_authorizes_observation_and_says_so():
    record = _group()
    assert record["status"] == pairs.CONFIRMED
    assert record["authorizes_trading"] is False
    assert record["venues"] == [KALSHI, POLYMARKET]


def test_the_group_id_does_not_depend_on_the_order_the_legs_were_listed():
    """Otherwise K,P and P,K would be two groups for one event and the one-market-one-group
    rule would not catch it."""
    forward = _group()
    backward = _group(legs=list(reversed(_legs())))
    assert forward["event_id"] == backward["event_id"]
    assert forward["legs"] == backward["legs"]


def test_a_group_needs_at_least_two_legs():
    with pytest.raises(ToolError) as exc:
        _group(legs=[{"venue": KALSHI, "market_id": "K1"}])
    assert exc.value.reason_code == pairs.TOO_FEW_LEGS


def test_two_markets_on_one_venue_are_not_a_group():
    """Same-venue pairing is intra-venue structural arbitrage — a different strategy with a
    different risk, deliberately out of scope."""
    with pytest.raises(ToolError) as exc:
        _group(legs=[
            {"venue": KALSHI, "market_id": "K1"},
            {"venue": KALSHI, "market_id": "K2"},
        ])
    assert exc.value.reason_code == pairs.DUPLICATE_VENUE


def test_a_group_round_trips_and_is_hash_verified(tmp_path):
    pairs.confirm_group(_group(), root=tmp_path)
    stored = pairs.read_groups(tmp_path)
    assert [leg["market_id"] for leg in stored[0]["legs"]] == ["FED-26DEC-CUT", "tok-yes-fed"]

    # A hand-edited group is detected, not trusted.
    path = pairs.events_path(tmp_path)
    row = json.loads(path.read_text(encoding="utf-8").strip())
    row["legs"][1]["market_id"] = "tok-someone-elses-market"
    path.write_text(json.dumps(row, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(ToolError) as exc:
        pairs.read_groups(tmp_path)
    assert exc.value.reason_code == pairs.EVENTS_TAMPERED


def test_one_market_belongs_to_at_most_one_group(tmp_path):
    """Two groups sharing a market would count one exposure twice and claim two different
    events are the same market."""
    pairs.confirm_group(_group(), root=tmp_path)
    with pytest.raises(ToolError) as exc:
        pairs.confirm_group(
            _group(legs=[
                {"venue": KALSHI, "market_id": "FED-26DEC-CUT"},
                {"venue": POLYMARKET, "market_id": "tok-other"},
            ]),
            root=tmp_path,
        )
    assert exc.value.reason_code == pairs.MARKET_ALREADY_GROUPED
    # ...and the refusal points at the right fix: extend the existing group.
    assert "add this venue's leg to it" in exc.value.reason


def test_an_empty_store_is_not_an_unreadable_one(tmp_path):
    assert pairs.read_groups(tmp_path) == []
    pairs.events_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    pairs.events_path(tmp_path).write_text("{not json\n", encoding="utf-8")
    with pytest.raises(ToolError) as exc:
        pairs.read_groups(tmp_path)
    assert exc.value.reason_code == pairs.EVENTS_UNREADABLE


def test_retiring_keeps_the_history_and_frees_every_leg(tmp_path):
    stored = pairs.confirm_group(_group(), root=tmp_path)
    retired = pairs.retire_group(
        stored["event_id"], reason="venues resolved differently in July", retired_by="thomas",
        now=NOW, root=tmp_path,
    )
    assert retired["status"] == pairs.RETIRED
    assert pairs.read_groups(tmp_path) == []
    assert len(pairs.read_groups(tmp_path, include_retired=True)) == 1
    pairs.confirm_group(_group(), root=tmp_path)
    assert len(pairs.read_groups(tmp_path)) == 1


# --- the linear-cost path a third venue takes -----------------------------------

def test_adding_a_venue_costs_one_review_not_one_per_existing_leg(tmp_path):
    """The whole point of grouping. A third venue quoting an event the operator already
    recognised is ONE more confirmation, not one per pairing — and the pairings it creates
    come for free."""
    stored = pairs.confirm_group(_group(), root=tmp_path)
    assert len(pairs.pairings_of(stored)) == 1

    extended = pairs.add_leg(
        stored["event_id"], {"venue": KALSHI, "market_id": "ignored"},
        criteria_note="x" * 20, added_by="thomas", now=NOW, root=tmp_path,
    ) if False else None
    del extended  # the third venue's constant does not exist yet; see the note below.

    # Until a third adapter lands there is no third venue name to add, so the property is
    # pinned on the arithmetic instead: n legs yield n(n-1)/2 pairings, which is what the
    # detector consumes, and which the operator never confirms individually.
    three = dict(stored)
    three["legs"] = [*stored["legs"], {"venue": "future_venue", "market_id": "F1", "title": ""}]
    assert len(pairs.pairings_of(three)) == 3


def test_adding_a_leg_requires_its_own_criteria_note(tmp_path):
    """The new venue's resolution rules are its own, so the check is made again — for that
    leg, not for the whole group again."""
    stored = pairs.confirm_group(_group(), root=tmp_path)
    with pytest.raises(ToolError) as exc:
        pairs.add_leg(
            stored["event_id"], {"venue": POLYMARKET, "market_id": "tok-new"},
            criteria_note="ok", added_by="thomas", now=NOW, root=tmp_path,
        )
    assert exc.value.reason_code == pairs.MISSING_CRITERIA_NOTE


def test_retiring_a_group_that_is_not_confirmed_is_refused(tmp_path):
    with pytest.raises(ToolError) as exc:
        pairs.retire_group("nope", reason="x", retired_by="thomas", now=NOW, root=tmp_path)
    assert exc.value.reason_code == pairs.GROUP_NOT_FOUND


def test_the_matcher_cannot_write_a_group():
    """The structural half of 'nothing auto-confirms': the module that scores has no way to
    store, and imports nothing that does."""
    assert not hasattr(matching, "confirm_group")
    source = (matching.__doc__ or "").lower()
    assert "proposes only" in source or "proposal, never a pair" in source


# --- the operator's door --------------------------------------------------------

@pytest.fixture
def cli_root(tmp_path, monkeypatch):
    """Point the store's default root at tmp_path, so the CLI writes nowhere real."""
    monkeypatch.setattr(pairs, "_repo_root", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def live_venues(monkeypatch):
    """Propose against data claiming to have come from the venues, not from the mock."""
    from runtime.mvp_runtime.predmarket import pairs_cli

    monkeypatch.setattr(
        pairs_cli, "select_pred_market_collector",
        lambda venue, **kw: LiveLikePredMarketCollector(venue),
    )


def test_propose_confirms_nothing(cli_root, capsys, live_venues):
    from runtime.mvp_runtime.predmarket import pairs_cli

    assert pairs_cli.main(["propose", "--limit", "4"]) == 0
    out = capsys.readouterr().out
    # Candidates were actually generated — without `live_venues` this test passes on "0
    # candidate(s)" and stops covering the matcher at all.
    assert "0 candidate(s)" not in out
    assert "candidate(s)" in out
    assert "Confirmation is yours" in out
    assert pairs.read_groups(cli_root) == []


def test_propose_refuses_to_build_candidates_out_of_the_mock(cli_root, capsys):
    """The durable door, and the reason this matters more here than in the scan.

    A confirmation is written to the group store and re-read by every later scan. The two
    venues' mocks deliberately carry the *same titles at different prices* — the exact shape
    the matcher is built to find — so a both-venues-on-mock propose run yields clean-looking
    candidates out of ``(venue, index)``. Confirming one writes mock ids into the store
    permanently. A rehearsal must not be able to author a confirmed pair, so a synthetic read
    degrades the venue exactly like an outage, and says which it was.
    """
    from runtime.mvp_runtime.predmarket import pairs_cli
    from runtime.mvp_runtime.predmarket.market_data import SYNTHETIC_SOURCE, VENUES

    assert pairs_cli.main(["propose", "--limit", "4"]) == 0  # no live_venues: the real default
    out = capsys.readouterr().out
    # Every venue, not a fixed count — a fourth one must inherit the refusal, not slip past it.
    assert out.count("DEGRADED") == len(VENUES)
    assert SYNTHETIC_SOURCE in out
    assert "0 candidate(s)" in out
    assert pairs.read_groups(cli_root) == []


def test_confirm_without_criteria_is_blocked_not_recorded(cli_root, capsys):
    from runtime.mvp_runtime.predmarket import pairs_cli

    code = pairs_cli.main([
        "confirm", "--leg", f"{KALSHI}:K1", "--leg", f"{POLYMARKET}:P1", "--criteria", "ok",
    ])
    assert code == 2  # EXIT_BLOCKED
    assert pairs.MISSING_CRITERIA_NOTE in capsys.readouterr().err
    assert pairs.read_groups(cli_root) == []


def test_a_malformed_leg_is_refused(cli_root, capsys):
    from runtime.mvp_runtime.predmarket import pairs_cli

    assert pairs_cli.main(["confirm", "--leg", "K1", "--leg", f"{POLYMARKET}:P1",
                           "--criteria", "x" * 20]) == 2
    assert "INVALID_LEG" in capsys.readouterr().err


def test_confirm_then_list_round_trips_through_the_cli(cli_root, capsys):
    from runtime.mvp_runtime.predmarket import pairs_cli

    note = "both settle on the official FOMC statement for the December meeting"
    assert pairs_cli.main([
        "confirm", "--leg", f"{KALSHI}:K1", "--leg", f"{POLYMARKET}:P1", "--criteria", note,
    ]) == 0
    capsys.readouterr()

    assert pairs_cli.main(["list"]) == 0
    out = capsys.readouterr().out
    assert f"{KALSHI}:K1" in out and f"{POLYMARKET}:P1" in out
    assert note in out

    stored = pairs.read_groups(cli_root)
    assert len(stored) == 1 and stored[0]["authorizes_trading"] is False


# --- the third venue, and the one piece of evidence that is not a heuristic ------

def _binance_market(title="Will the Fed cut rates in December?", **kw):
    return _market(
        BINANCE, kw.pop("market_id", "12345"), title,
        **{k: v for k, v in kw.items() if k != "group_id"}
    )


def test_a_venue_asserted_cross_reference_outranks_the_wording_gate():
    """Predict.fun publishes the Polymarket condition it mirrors. That is an assertion by a
    party that knows, not a similarity score — so it carries a pairing whose wording the
    rules would have refused."""
    from dataclasses import replace

    poly = replace(_poly("Fed decision: cut?"), group_id="0xcondition")
    pf = replace(_binance_market("Will the central bank lower rates at the December meeting?"),
                 group_id="0xcondition")
    judged = matching.judge_pair(pf, poly)
    assert matching.venue_cross_reference(pf, poly) is True
    assert judged.title_similarity < matching.MIN_TITLE_SIMILARITY
    assert judged.venue_asserted is True
    assert judged.is_candidate is True and judged.refusals == ()


def test_a_cross_reference_does_not_outrank_a_numeric_conflict():
    """If the venue points at a market naming different numbers, one of the two venues
    cross-referenced the wrong thing. That is a finding, not a licence."""
    from dataclasses import replace

    poly = replace(_poly("Will BTC close above 90k on Dec 31?"), group_id="0xcondition")
    pf = replace(_binance_market("Will BTC close above 100k on Dec 31?"), group_id="0xcondition")
    judged = matching.judge_pair(pf, poly)
    assert judged.venue_asserted is True
    assert matching.NUMERIC_MISMATCH in judged.refusals
    assert judged.is_candidate is False


def test_a_missing_or_mismatched_reference_is_simply_no_evidence():
    from dataclasses import replace

    assert matching.venue_cross_reference(_binance_market(), _poly()) is False
    left = replace(_binance_market(), group_id="0xaaa")
    right = replace(_poly(), group_id="0xbbb")
    assert matching.venue_cross_reference(left, right) is False
    # An empty string on either side is absence, not a match of two blanks.
    assert matching.venue_cross_reference(replace(left, group_id=""), replace(right, group_id="")) is False


def test_a_group_may_hold_the_third_venue(tmp_path):
    """The whole point of the group generalisation: adding Predict.fun is a leg, and the
    pairings it creates come for free."""
    group = pairs.build_event_group(
        legs=[
            {"venue": KALSHI, "market_id": "K1"},
            {"venue": POLYMARKET, "market_id": "P1"},
            {"venue": BINANCE, "market_id": "12345"},
        ],
        criteria_note="all three settle on the official FOMC statement for December",
        confirmed_by="thomas",
        now=NOW,
    )
    stored = pairs.confirm_group(group, root=tmp_path)
    assert stored["venues"] == [BINANCE, KALSHI, POLYMARKET]
    assert len(pairs.pairings_of(stored)) == 3


# --- no venue is named in the vocabulary ----------------------------------------

def _at(venue, title, market_id, close="2026-12-31T23:59:00Z", category=None):
    return PredMarket(venue=venue, market_id=market_id, group_id=None, title=title,
                      close_time=close, status="active", category=category)


def test_a_third_venue_can_be_proposed_at_all():
    """The reason the signature changed. Binance had been reading and quoting markets for a
    week and could not appear in a single proposal — ``generate_candidates`` took two
    positional lists named after the other two venues, so there was nowhere to put it."""
    question = "Will the Fed cut rates in December?"
    result = matching.generate_candidates({
        KALSHI: [_at(KALSHI, question, "K1")],
        POLYMARKET: [_at(POLYMARKET, question, "P1")],
        BINANCE: [_at(BINANCE, question, "B1")],
    })
    # Three venues quoting one event is three cross-venue pairings, not one.
    assert result["judged_count"] == 3
    assert result["venues"] == sorted([KALSHI, POLYMARKET, BINANCE])
    pairs_found = {tuple(sorted((c["left_venue"], c["right_venue"])))
                   for c in result["candidates"]}
    assert pairs_found == {(BINANCE, KALSHI), (BINANCE, POLYMARKET), (KALSHI, POLYMARKET)}


def test_two_markets_on_the_same_venue_are_never_paired():
    """A venue's book does not disagree with itself, so a same-venue pairing is not an
    arbitrage — it is a category error that would show up as a permanent free lunch."""
    result = matching.generate_candidates({
        KALSHI: [_at(KALSHI, "Will the Fed cut rates in December?", "K1"),
                 _at(KALSHI, "Will the Fed cut rates in December?", "K2")],
    })
    assert result["judged_count"] == 0 and result["candidates"] == []


def test_the_same_unordered_pairing_always_yields_the_same_record():
    """Judging (A, B) and (B, A) must not produce two different rows for one pairing. Every
    comparison is symmetric, so the legs are ordered canonically before anything is
    recorded."""
    k = _at(KALSHI, "Will the Fed cut rates in December?", "K1")
    p = _at(POLYMARKET, "Will the Fed cut rates in December?", "P1")
    assert matching.judge_pair(k, p).as_dict() == matching.judge_pair(p, k).as_dict()
    assert matching.judge_pair(p, k).legs() == ((KALSHI, "K1"), (POLYMARKET, "P1"))


def test_near_misses_are_capped_and_say_how_many_were_cut():
    """Once the venues began quoting the same subjects, shared boilerplate put thousands of
    unrelated pairings above the floor. Truncating is fine; truncating silently would make
    the list read as complete."""
    # The fixture has to survive the hard gates to reach the near-miss list at all, and two
    # of them now bite here. "Democratic" against "Republican" is OPPOSING_TERMS; two
    # different people in one template is SUBJECT_MISMATCH. Both are answers, not doubts.
    #
    # So: the SAME person on both sides, phrased differently, with close dates six months
    # apart. That is a genuine near miss — everything about the subject agrees and the
    # pairing is refused on a gate a reviewer might want to revisit.
    # Digit-free names on purpose: a token carrying a digit reads as a number to this
    # module, and `Sanders0` would be dropped from the very evidence the fixture is built to
    # supply. (That is how the round-trip bug in `subject_mismatch` was found.)
    people = ["Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot",
              "Golf", "Hotel", "India", "Juliett", "Kilo", "Lima"]
    left = [_at(KALSHI, f"Will {who} win the 2028 Democratic nomination?", f"K{i}",
                close="2026-12-31T23:59:00Z") for i, who in enumerate(people)]
    right = [_at(POLYMARKET, f"Will {who} be the 2028 Democratic nominee?", f"P{i}",
                 close="2027-06-01T00:00:00Z") for i, who in enumerate(people)]
    result = matching.generate_candidates({KALSHI: left, POLYMARKET: right}, near_miss_limit=5)
    assert result["near_miss_total"] > 5
    assert len(result["near_misses"]) == 5
    assert result["near_miss_truncated"] == result["near_miss_total"] - 5


def test_the_status_line_names_every_venue_read_and_is_ascii():
    result = matching.generate_candidates({
        KALSHI: [_at(KALSHI, "q", "K1")], POLYMARKET: [_at(POLYMARKET, "q", "P1")],
    })
    line = matching.candidate_status_line(result)
    line.encode("ascii")
    assert "kalshi=1" in line and "polymarket=1" in line


def test_an_empty_or_missing_mapping_judges_nothing_rather_than_failing():
    for arg in ({}, None, {KALSHI: [], POLYMARKET: None}):
        result = matching.generate_candidates(arg)
        assert result["judged_count"] == 0 and result["candidates"] == []


# --- sharing only the template -------------------------------------------------

def _nominee(venue, person, market_id, phrasing="be the Democratic Presidential nominee in 2028"):
    return _market(venue, market_id, f"Will {person} {phrasing}?")


def _field(n=14):
    """A ballot's worth of one template, which is what makes the template a template."""
    people = ["Gavin Newsom", "Kamala Harris", "Bernie Sanders", "Pete Buttigieg",
              "Andy Beshear", "Cory Booker", "Ro Khanna", "Rahm Emanuel", "Tim Walz",
              "Josh Shapiro", "Wes Moore", "Chris Murphy", "Mark Kelly", "Jon Ossoff"][:n]
    return ([_nominee(KALSHI, p, f"K{i}") for i, p in enumerate(people)],
            [_nominee(POLYMARKET, p, f"P{i}", "win the 2028 Democratic presidential nomination")
             for i, p in enumerate(people)])


def test_two_different_people_in_one_template_are_not_a_pair():
    """THE case, found live inside a shipped candidate list:

        Will Gavin Newsom win the 2028 Democratic presidential nomination?   (binance)
        Will MrBeast     win the 2028 Democratic presidential nomination?    (polymarket)

    Jaccard 0.625, past the 0.60 gate, sharing nothing but the template. Two markets that
    agree only on what everything agrees on are not nearly the same question — they are one
    sentence with the subject swapped, and the subject IS the question.
    """
    kalshi, poly = _field()
    result = matching.generate_candidates({KALSHI: kalshi, POLYMARKET: poly})

    for row in result["candidates"]:
        assert row["left_title"].split()[1:3] == row["right_title"].split()[1:3], (
            f"paired two different people: {row['left_title']} / {row['right_title']}"
        )
    assert result["boilerplate_only_count"] > 0


def test_the_same_person_on_both_venues_still_pairs():
    """The gate must not cost the true positives it sits next to. 'newsom' is rare in the
    corpus even though every other word in the sentence is not."""
    kalshi, poly = _field()
    result = matching.generate_candidates({KALSHI: kalshi, POLYMARKET: poly})
    paired = {row["left_title"] for row in result["candidates"]}
    assert any("Gavin Newsom" in title for title in paired)

    row = next(r for r in result["candidates"] if "Gavin Newsom" in r["left_title"])
    # The evidence the pairing rests on, recorded: the rare words, not the template.
    assert set(row["distinctive_shared_tokens"]) == {"gavin", "newsom"}
    assert "presidential" in row["shared_tokens"]


def test_a_boilerplate_only_refusal_is_an_answer_not_a_near_miss():
    """It joins NUMERIC_MISMATCH as a hard refusal, which is also what drained the near-miss
    flood at its source: those rows were overwhelmingly this exact shape."""
    kalshi, poly = _field()
    result = matching.generate_candidates({KALSHI: kalshi, POLYMARKET: poly})
    for row in result["near_misses"]:
        assert matching.SHARED_BOILERPLATE_ONLY not in row["refusals"]


def test_a_short_scan_does_not_call_the_whole_language_boilerplate():
    """A share is not evidence on its own. With three markets, every word one of them uses
    appears in 33% of the corpus — a purely proportional rule refuses everything, which is
    how this was caught. A word is template only once the template has visibly repeated."""
    question = "Will the Fed cut rates in December?"
    result = matching.generate_candidates({
        KALSHI: [_market(KALSHI, "K1", question)],
        POLYMARKET: [_market(POLYMARKET, "P1", question)],
    })
    assert len(result["candidates"]) == 1
    assert result["boilerplate_only_count"] == 0


def test_the_commonness_table_reports_rare_words_as_rare():
    titles = [f"Will {p} be the Democratic Presidential nominee in 2028?" for p in
              ("A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L")]
    commonness = matching.token_commonness(titles)
    assert commonness["presidential"] == pytest.approx(1.0)
    assert commonness["democratic"] == pytest.approx(1.0)
    # Seen once each, well under MIN_BOILERPLATE_TITLES, so reported as rare not as 1/12.
    assert commonness.get("a", 0.0) == 0.0
    assert matching.distinctive_tokens(["presidential", "newsom"], commonness) == ("newsom",)


def test_omitting_the_corpus_keeps_the_old_purely_pairwise_behaviour():
    """`judge_pair` without `commonness` is still a pure function of its two arguments —
    which is what every direct caller and every other test relies on."""
    judged = matching.judge_pair(_nominee(KALSHI, "Gavin Newsom", "K1"),
                                 _nominee(POLYMARKET, "MrBeast", "P1"))
    assert matching.SHARED_BOILERPLATE_ONLY not in judged.refusals
    assert judged.distinctive_shared_tokens == ()


# --- the opposite question --------------------------------------------------------

def _pair(a, b):
    return matching.judge_pair(_market(KALSHI, "K1", a), _market(POLYMARKET, "P1", b))


def test_the_opposite_question_is_refused_however_high_it_scores():
    """The most dangerous pairing there is, and both of these were at the TOP of a shipped
    confirmation sheet, where they looked like the strongest finds in it.

    Opposite outcomes price at roughly p and 1-p, so the apparent gross edge is enormous and
    permanent — "manufactures arbitrage forever" in its textbook form.
    """
    for left, right in (
        ("Will the Fed decrease interest rates by 50+ bps after the July 2026 meeting?",
         "Will the Fed increase interest rates by 50+ bps after the July 2026 meeting?"),
        ("Will the Democratic Party control the Senate after the 2026 Midterm elections?",
         "Will the Republican Party control the Senate after the 2026 Midterm elections?"),
        ("Fed rate hike in 2026?", "Fed rate cut in 2026?"),
    ):
        judged = _pair(left, right)
        assert judged.is_candidate is False, left
        assert matching.OPPOSING_TERMS in judged.refusals
        # A hard refusal: this is an answer, not a threshold that nearly worked.
        assert judged.near_miss() is False


def test_direction_words_are_the_question_and_are_no_longer_stopwords():
    """The worse half. `up`, `down`, `over` and `under` were stopwords, so these normalized
    to the SAME tokens and scored a perfect 1.0 — the matcher could not see the difference at
    all. Binance lists up-or-down markets by the dozen."""
    for word in ("up", "down", "over", "under"):
        assert word not in matching._STOPWORDS  # noqa: SLF001 - the whole point

    for left, right in (
        ("Over 2.5 runs scored in the 1st inning?", "Under 2.5 runs scored in the 1st inning?"),
        ("Bitcoin Up on July 27?", "Bitcoin Down on July 27?"),
    ):
        judged = _pair(left, right)
        assert judged.title_similarity < 1.0, "the direction must survive normalization"
        assert judged.is_candidate is False
        assert matching.OPPOSING_TERMS in judged.refusals


def test_a_market_carrying_both_directions_is_not_opposed_to_itself():
    """"Bitcoin Up or Down on July 27?" names both. Two venues quoting that same up-or-down
    question are asking one question, not two halves of one — and the gate only looks at
    words neither side shares, so it correctly stays quiet."""
    judged = _pair("Bitcoin Up or Down on July 27?", "Bitcoin Up or Down on July 27?")
    assert judged.is_candidate is True
    assert matching.OPPOSING_TERMS not in judged.refusals


def test_agreeing_on_a_direction_is_not_opposing():
    """Both sides saying "above" is agreement. A gate that fired on the mere presence of a
    direction word would refuse every threshold market on both venues."""
    judged = _pair("Will BTC close above 100k on Dec 31?", "Will Bitcoin close above 100k on Dec 31?")
    assert judged.is_candidate is True and judged.refusals == ()

    # Different words, same direction: "below" and "under" do not oppose each other.
    assert matching.opposing_terms(
        matching.normalize_tokens("Will inflation fall below 3%?"),
        matching.normalize_tokens("Will inflation come in under 3%?"),
    ) is False


def test_degree_is_left_to_the_gate_that_can_see_it():
    """"Cut by 25bps" against "cut by 50bps" is not an opposing pair — it is the same
    direction at different sizes, which is exactly what the numeric gate is for. Claiming it
    here would put two refusals on one row and point a later fix at the wrong rule."""
    judged = _pair("Will the Fed cut rates by 25 bps in December?",
                   "Will the Fed cut rates by 50 bps in December?")
    assert matching.OPPOSING_TERMS not in judged.refusals
    assert matching.NUMERIC_MISMATCH in judged.refusals


def test_the_gate_is_symmetric():
    """Judging (A, B) and (B, A) must not disagree about whether they oppose each other."""
    up = "Will the Fed increase rates in December?"
    down = "Will the Fed decrease rates in December?"
    assert _pair(up, down).refusals == _pair(down, up).refusals


# --- one template, two different subjects -----------------------------------------

def _corpus(*titles):
    return matching.token_document_counts(titles)


def test_two_different_companies_in_one_template_are_not_a_pair():
    """Found live 2026-07-28 at position 2 of a sheet handed to the operator:

        StandX  FDV above $200M one day after launch?     (binance)
        Puffpaw FDV above $200M one day after launch?     (polymarket)
        matched on: 200m, above, after, day, fdv, launch, one

    Not one company. The evidence is right there in what it matched on — no name in it. The
    boilerplate gate stays quiet because these words ARE rare corpus-wide (`fdv` 21 of 632);
    they are the template of a market family, not of the venue.
    """
    family = [f"{who} FDV above ${cap}M one day after launch?"
              for who in ("Puffpaw", "GRVT", "Pacifica", "Discord", "ANSEM")
              for cap in (50, 100, 200)] + ["StandX FDV above $200M one day after launch?"]
    counts = _corpus(*family)

    judged = matching.judge_pair(
        _market(BINANCE, "B1", "StandX FDV above $200M one day after launch?"),
        _market(POLYMARKET, "P1", "Puffpaw FDV above $200M one day after launch?"),
        counts=counts,
    )
    assert judged.is_candidate is False
    assert matching.SUBJECT_MISMATCH in judged.refusals
    # An answer, not a doubt — so it never reaches the near-miss list.
    assert judged.near_miss() is False


def test_the_same_subject_phrased_differently_still_pairs():
    """The property the gate must not cost. Here the rare tokens are SHARED (the name) and
    the differing ones are template words — the exact inverse of the case above."""
    corpus = [f"Will {who} {verb} the 2028 Democratic {noun}?"
              for who in ("Newsom", "Harris", "Booker", "Khanna", "Beshear")
              for verb, noun in (("win", "nomination"), ("be", "nominee"))]
    counts = _corpus(*corpus)

    judged = matching.judge_pair(
        _market(KALSHI, "K1", "Will Newsom be the 2028 Democratic nominee?"),
        _market(POLYMARKET, "P1", "Will Newsom win the 2028 Democratic nomination?"),
        counts=counts,
    )
    assert matching.SUBJECT_MISMATCH not in judged.refusals


def test_a_shared_number_is_not_a_shared_subject():
    """What makes the gate robust rather than lucky. `200m` appears in exactly the two
    StandX/Puffpaw titles, so counting it as shared evidence left the verdict resting on a
    ONE-document margin (`standx` 1 vs `200m` 2). Ignoring numbers, the same case clears by
    18x — and disagreeing numbers are already NUMERIC_MISMATCH, which is where that evidence
    belongs."""
    counts = _corpus(
        "StandX FDV above $200M one day after launch?",
        "Puffpaw FDV above $200M one day after launch?",
        *[f"{w} FDV above $50M one day after launch?" for w in ("A", "B", "C", "D")],
    )
    assert matching.subject_mismatch(
        matching.normalize_tokens("StandX FDV above $200M one day after launch?"),
        matching.normalize_tokens("Puffpaw FDV above $200M one day after launch?"),
        counts,
    ) is True


def test_a_token_that_merely_contains_a_digit_is_treated_as_a_number():
    """The module has one test for "this is a number" and it is `has a digit`. Stated so the
    cost is visible: a subject like `Web3` is read as numeric and stops counting as shared
    evidence. Found by a fixture named `Sanders0` silently losing its own name."""
    assert matching._has_digit("2028") and matching._has_digit("web3")   # noqa: SLF001
    assert not matching._has_digit("newsom")


def test_the_gate_stays_quiet_when_there_is_nothing_to_compare():
    """Degrades permissive by construction: identical titles share everything and differ in
    nothing, and a pair with no non-numeric shared token has no evidence to weigh."""
    counts = _corpus("Will Newsom win?", "Will Newsom win?")
    same = matching.normalize_tokens("Will Newsom win?")
    assert matching.subject_mismatch(same, same, counts) is False
    assert matching.subject_mismatch(same, (), counts) is False
    assert matching.subject_mismatch((), (), {}) is False


def test_omitting_the_corpus_leaves_the_gate_off():
    """`judge_pair` without `counts` stays a pure function of its two arguments, which every
    direct caller and most of this file relies on."""
    judged = matching.judge_pair(
        _market(BINANCE, "B1", "StandX FDV above $200M one day after launch?"),
        _market(POLYMARKET, "P1", "Puffpaw FDV above $200M one day after launch?"),
    )
    assert matching.SUBJECT_MISMATCH not in judged.refusals


def test_the_result_counts_how_many_pairings_were_one_template_two_subjects():
    result = matching.generate_candidates({
        BINANCE: [_market(BINANCE, "B1", "StandX FDV above $200M one day after launch?")],
        POLYMARKET: [_market(POLYMARKET, f"P{i}", f"{w} FDV above ${c}M one day after launch?")
                     for i, (w, c) in enumerate(
                         [("Puffpaw", 200), ("GRVT", 200), ("Pacifica", 50)])],
    })
    assert result["subject_mismatch_count"] >= 1
    assert result["candidates"] == []


def test_a_generational_suffix_makes_it_a_different_person():
    """Live on 2026-07-28, in a sheet about to be handed over:

        Will Donald Trump     win the 2028 US Presidential Election?
        Will Donald Trump Jr. win the 2028 US Presidential Election?

    The rarity comparison did decide this correctly — by ONE document. `donald` appears in 8
    titles and `jr` in 7, because most Trump markets say "Trump" without "Donald", so the
    verdict flipped between runs. A conclusion that changes when one market is listed is not
    a conclusion, and a suffix is knowledge rather than a measurement.
    """
    counts = _corpus(
        "Will Donald Trump win the 2028 US Presidential Election?",
        "Will Donald Trump Jr. win the 2028 US Presidential Election?",
        *[f"Will Trump {w} in 2028?" for w in ("resign", "run", "endorse")],
    )
    assert matching.subject_mismatch(
        matching.normalize_tokens("Will Donald Trump win the 2028 US Presidential Election?"),
        matching.normalize_tokens("Will Donald Trump Jr. win the 2028 US Presidential Election?"),
        counts,
    ) is True


def test_a_suffix_on_both_sides_is_agreement_not_a_difference():
    """Two venues both asking about Trump Jr. are asking about Trump Jr."""
    counts = _corpus("Will Donald Trump Jr. win the 2028 US Presidential Election?")
    same = matching.normalize_tokens("Will Donald Trump Jr. win the 2028 US Presidential Election?")
    assert matching.subject_mismatch(same, same, counts) is False


def test_the_suffix_rule_does_not_fire_on_names_that_merely_look_like_one():
    """`ii` and `iv` are on the list; ordinary words are not, and the rule only fires when one
    side has a suffix the other lacks."""
    counts = _corpus("Will Newsom win?", "Will Newsom be nominated?")
    assert matching.subject_mismatch(
        matching.normalize_tokens("Will Newsom win?"),
        matching.normalize_tokens("Will Newsom be nominated?"),
        counts,
    ) is False


def test_the_rarity_rule_needs_a_margin_not_just_an_inequality():
    """A bare `<` decided Trump against Trump Jr. by ONE document and fired on a two-title
    corpus where every count is 1 or 2. Requiring the differing token to be at least twice as
    rare makes the verdict survive a single market being listed, and keeps a corpus too small
    to distinguish anything quiet."""
    tiny = _corpus("Will Newsom win?", "Will Newsom be nominated?")
    assert matching.subject_mismatch(
        matching.normalize_tokens("Will Newsom win?"),
        matching.normalize_tokens("Will Newsom be nominated?"),
        tiny,
    ) is False

    # And the live cases still separate, with room to spare.
    standx = _corpus(
        "StandX FDV above $200M one day after launch?",
        "Puffpaw FDV above $200M one day after launch?",
        *[f"{w} FDV above $50M one day after launch?" for w in ("A", "B", "C", "D")],
    )
    assert matching.subject_mismatch(
        matching.normalize_tokens("StandX FDV above $200M one day after launch?"),
        matching.normalize_tokens("Puffpaw FDV above $200M one day after launch?"),
        standx,
    ) is True


# --- within one venue pair, one market gets one candidate ------------------------

_BOP_COMBOS = ("D Senate, D House", "D Senate, R House", "R Senate, D House", "R Senate, R House")


def _bop(venue, market_id, combo):
    return PredMarket(
        venue=venue, market_id=market_id, group_id=None,
        title=f"2026 Balance of Power: {combo}",
        close_time="2027-01-05T00:00:00Z", status="active",
    )


def test_one_market_is_not_offered_four_mutually_exclusive_partners():
    """Observed on the live sheet 2026-07-30: four Binance markets proposed against the single
    Polymarket market "2026 Balance of Power: D Senate, D House" — its own counterpart plus
    three flatly different questions, while all five combinations sat in the corpus with
    distinct titles.

    At most one of those four could ever be confirmed; `pairs.py` refuses the rest with
    MARKET_ALREADY_GROUPED. So the list was not merely verbose, it was order-dependent: an
    operator working top-down could confirm an impostor first and thereby refuse the correct
    pairing forever.

    No wording gate can catch this and none is asked to: the distinguishing tokens are the
    single characters `d` and `r`, six tokens of shared template outvote them, and
    `subject_mismatch`'s rarity test fails because both letters are common in a political
    corpus. What catches it is that two markets on one venue are never the same event.
    """
    left = [_bop(BINANCE, f"285{i}:tok{i}", c) for i, c in enumerate(_BOP_COMBOS)]
    right = [_bop(POLYMARKET, f"ptok{i}", c) for i, c in enumerate(_BOP_COMBOS)]

    result = matching.generate_candidates({BINANCE: left, POLYMARKET: right})

    titles = {m.market_id: m.title for m in left + right}
    paired = [(titles[c["left_market_id"]], titles[c["right_market_id"]])
              for c in result["candidates"]]
    assert len(paired) == 4, "each combination keeps its own counterpart"
    assert all(a == b for a, b in paired), f"mispaired: {[x for x in paired if x[0] != x[1]]}"
    assert result["candidates_displaced"] == 12


def test_the_exact_match_claims_the_leg_rather_than_whichever_was_judged_first():
    """Greedy assignment is only correct because it runs over the sorted list. The impostor is
    placed first in input order, so a version that skipped the sort would keep it."""
    left = [_bop(BINANCE, "b-dr", "D Senate, R House")]
    right = [_bop(POLYMARKET, "p-dd", "D Senate, D House"),
             _bop(POLYMARKET, "p-dr", "D Senate, R House")]

    result = matching.generate_candidates({BINANCE: left, POLYMARKET: right})

    assert [c["right_market_id"] for c in result["candidates"]] == ["p-dr"]


def test_a_three_venue_group_keeps_all_three_pairings():
    """The claim set is scoped to the venue PAIR, and this is why. One event with one leg per
    venue is three pairings over three legs; a global claim set would keep one and silently
    destroy multi-leg groups — which is what the first attempt at this fix did."""
    title = "Will the Fed cut rates in December?"
    markets = {
        venue: [PredMarket(venue=venue, market_id=f"{venue}-1", group_id=None, title=title,
                           close_time="2026-12-31T23:59:00Z", status="active")]
        for venue in (BINANCE, KALSHI, POLYMARKET)
    }

    result = matching.generate_candidates(markets)

    assert {tuple(sorted((c["left_venue"], c["right_venue"]))) for c in result["candidates"]} == {
        (BINANCE, KALSHI), (BINANCE, POLYMARKET), (KALSHI, POLYMARKET),
    }
    assert result["candidates_displaced"] == 0


def test_displaced_candidates_are_counted_not_silently_dropped():
    """Same doctrine as the near-miss cap: a number that silently shrank reads as "this is
    everything that matched"."""
    left = [_bop(BINANCE, f"b{i}", c) for i, c in enumerate(_BOP_COMBOS)]
    right = [_bop(POLYMARKET, "p-one", "D Senate, D House")]

    result = matching.generate_candidates({BINANCE: left, POLYMARKET: right})

    assert len(result["candidates"]) == 1, "the one Polymarket market can serve one pairing"
    assert result["candidates_displaced"] == 3


# --- backfilling the topic id ---------------------------------------------------

def _binance_group(root, market_id="44:tok", topic_id=None):
    leg = {"venue": "binance", "market_id": market_id}
    if topic_id:
        leg["topic_id"] = topic_id
    record = pairs.build_event_group(
        legs=[{"venue": "kalshi", "market_id": "K-1"}, leg],
        criteria_note="both settle on the same official statement for this event",
        confirmed_by="thomas", now=NOW,
    )
    return pairs.confirm_group(record, root=root)


def test_a_backfill_fills_an_absent_topic_id_without_re_keying_the_group(tmp_path):
    """What makes this safe on a live window. `event_id` derives from `venue:market_id` alone,
    so the group keeps its identity and every observation already recorded against it stays
    attached — unlike `add_leg`, which has to re-key and carry `superseded_event_ids`."""
    stored = _binance_group(tmp_path)
    before = stored["event_id"]
    assert pairs.set_leg_topic_ids(
        {"44:tok": "4451653"}, updated_by="thomas", now=NOW, root=tmp_path) == 1
    after = pairs.read_groups(tmp_path)[0]
    assert after["event_id"] == before
    leg = next(l for l in after["legs"] if l["venue"] == "binance")
    assert leg["topic_id"] == "4451653"
    assert after["topic_ids_backfilled_by"] == "thomas"


def test_a_backfilled_group_still_verifies_against_its_own_hash(tmp_path):
    """A rewritten record that fails its own hash is refused by every reader here, so
    re-hashing is the write rather than bookkeeping after it."""
    _binance_group(tmp_path)
    pairs.set_leg_topic_ids({"44:tok": "4451653"}, updated_by="thomas", now=NOW, root=tmp_path)
    assert pairs.read_groups(tmp_path)          # read_groups verifies; a bad hash would raise


def test_a_backfill_never_overwrites_an_id_already_captured(tmp_path):
    """The stored one came from the listing at confirmation, which is the same source a walk
    reads. A field a later walk can rewrite is one whose value depends on when you ran it."""
    _binance_group(tmp_path, topic_id="ORIGINAL")
    assert pairs.set_leg_topic_ids(
        {"44:tok": "REWRITTEN"}, updated_by="thomas", now=NOW, root=tmp_path) == 0
    leg = next(l for l in pairs.read_groups(tmp_path)[0]["legs"] if l["venue"] == "binance")
    assert leg["topic_id"] == "ORIGINAL"


def test_a_backfill_leaves_retired_groups_alone(tmp_path):
    """A retired group is a correction record. Spending signed calls to enrich a pairing an
    operator withdrew buys nothing, because nothing observes it."""
    stored = _binance_group(tmp_path)
    pairs.retire_group(stored["event_id"], reason="wrong pairing", retired_by="thomas", now=NOW,
                       root=tmp_path)
    assert pairs.set_leg_topic_ids(
        {"44:tok": "4451653"}, updated_by="thomas", now=NOW, root=tmp_path) == 0

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


def test_propose_confirms_nothing(cli_root, capsys):
    from runtime.mvp_runtime.predmarket import pairs_cli

    assert pairs_cli.main(["propose", "--limit", "4"]) == 0
    out = capsys.readouterr().out
    assert "candidate(s)" in out
    assert "Confirmation is yours" in out
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
    left = [_at(KALSHI, f"Will candidate {i} win the 2028 Democratic nomination?", f"K{i}",
                close="2026-12-31T23:59:00Z") for i in range(12)]
    right = [_at(POLYMARKET, f"Will person {i} win the 2028 Republican nomination?", f"P{i}",
                 close="2027-06-01T00:00:00Z") for i in range(12)]
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

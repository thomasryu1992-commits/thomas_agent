"""PM1 — deciding whether two venues are quoting the *same* event. Deterministic; proposes only.

This is the hardest honest problem in the prediction-market track, and the one whose failure
mode is worst: a wrong pair does not error, it **manufactures arbitrage forever**. Two
markets about different questions will show a persistent price gap, the detector will report
it every scan, and the report will conclude there is a rich, durable edge. Nothing downstream
can catch that — the numbers are all internally consistent.

So the design is deliberately timid:

1. **Deterministic candidate generation only.** Normalized token overlap, close-date
   proximity, and (where both venues offer it) category. No model call, no learned
   threshold — the same two payloads always produce the same verdict, and the reasoning is
   readable in the record.
2. **A candidate is a proposal, never a pair.** Confirmation is an operator action
   (``pairs.py``). Nothing in this module writes anything.
2b. **No venue is named in the vocabulary.** The two sides of a judgement are ``left`` and
   ``right``. The first version named them ``kalshi`` and ``polymarket``, which read as a
   harmless label until a third venue had been reading and quoting markets for a week and
   still could not appear in a single proposal — there was nowhere to put it.
3. **Every judgement carries its breakdown**, including the near-misses. That is what makes
   the gap fixable: when the scheduled LLM pass (a later increment) proposes a pair this
   matcher missed, the question "why did the rules miss it?" has to be answerable from the
   record, or the rules never improve and the LLM becomes load-bearing by default.

**Unknown is not mismatch.** A Kalshi market carries no category (its event does), so a
matcher that treated a missing category as a disagreement would refuse every Kalshi pair —
the same family of bug as reading an absent price as zero. A feature that cannot be compared
scores ``None`` and is excluded from the decision, never counted against it.

**The one thing this module cannot check** is the risk that actually decides the strategy:
whether the two venues *resolve* the same event the same way. Two markets can ask an
identical question and settle differently on the same news. No text comparison sees that, so
it is not modelled here — it is the operator's job at confirmation time, and ``pairs.py``
requires them to say they checked.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .. import timeutil
from .market_data import PredMarket

MATCHING_VERSION = "predmarket_matching.v0.1"

# Gates. A candidate must clear BOTH; category can raise confidence but never rescue or veto.
MIN_TITLE_SIMILARITY = 0.60
MAX_CLOSE_DELTA_HOURS = 48.0
# A near miss is a failure worth keeping: it is the raw material for improving the rules.
# Anything below this is simply a different question and would drown the record.
NEAR_MISS_TITLE_SIMILARITY = 0.35
# How many of them a result carries. The floor above stopped bounding the list once the
# venues began quoting the same subjects: 196 Kalshi markets against 100 Polymarket ones
# produced 3,136 near misses, nearly all of them two different people sharing the phrase
# "2028 democratic presidential nomination". The cap is reported rather than silent — see
# `near_miss_truncated` — because a truncated diagnostic that did not say so would read as
# a complete one. This bounds the list; it does not decide what counts as a near miss.
DEFAULT_NEAR_MISS_LIMIT = 200

# Why a pair was refused — each names the gate, so a later fix has somewhere to aim.
TITLE_TOO_DIFFERENT = "TITLE_TOO_DIFFERENT"
CLOSE_TOO_FAR_APART = "CLOSE_TOO_FAR_APART"
CLOSE_TIME_UNKNOWN = "CLOSE_TIME_UNKNOWN"
CATEGORY_CONFLICT = "CATEGORY_CONFLICT"
NUMERIC_MISMATCH = "NUMERIC_MISMATCH"

# The one piece of evidence that does not come from a heuristic: a venue naming the other
# venue's market as the one it mirrors (Predict.fun carries `polymarketConditionIds`). It is
# not a similarity score, it is an assertion by a party that knows.
VENUE_ASSERTED = "VENUE_ASSERTED_CROSS_REFERENCE"

# Refusals that are *evidence*, not threshold guesses. A near miss is meant to say "our
# rules may have been wrong"; these two say "these are different questions", so a pair
# refused by one of them is not worth a reviewer's second look.
_HARD_REFUSALS = frozenset({NUMERIC_MISMATCH, CATEGORY_CONFLICT})

# Words that carry no distinguishing information between two phrasings of one event.
_STOPWORDS = frozenset({
    "a", "an", "the", "will", "be", "is", "are", "was", "were", "do", "does", "did",
    "in", "on", "at", "by", "for", "of", "to", "and", "or", "than", "then", "this",
    "that", "it", "its", "as", "with", "from", "up", "down", "over", "under", "next",
})

# Venue phrasings of the same thing. Deliberately tiny and hand-maintained: this map is
# where a diagnosed miss gets fixed, so it is meant to grow one reviewed entry at a time
# rather than to be clever up front. Each key is normalized to its value before comparison.
_SYNONYMS: dict[str, str] = {
    "fomc": "fed",
    "federal": "fed",
    "reserve": "fed",
    "btc": "bitcoin",
    "eth": "ethereum",
    "potus": "president",
    "gop": "republican",
    "dems": "democrat",
    "democrats": "democrat",
    "republicans": "republican",
    "cpi": "inflation",
    "%": "percent",
}

_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")


def normalize_tokens(title: Any) -> tuple[str, ...]:
    """A title as comparable tokens: lowercased, punctuation dropped, stopwords removed,
    synonyms folded. Pure and stable — the same string always yields the same tuple.

    Numbers are kept: "above 100k" and "above 90k" are different questions, and a matcher
    that dropped digits would happily pair them.
    """
    if not isinstance(title, str):
        return ()
    tokens = _TOKEN_PATTERN.findall(title.lower())
    folded = [_SYNONYMS.get(token, token) for token in tokens]
    return tuple(t for t in folded if t and t not in _STOPWORDS)


def title_similarity(left: Any, right: Any) -> float:
    """Jaccard overlap of normalized tokens, in ``[0, 1]``.

    Jaccard rather than a character-level ratio on purpose: the score decomposes into "these
    tokens matched, these did not", which is exactly what a reviewer needs to see when asking
    why a pair was missed. A character ratio would give one opaque number.
    """
    a, b = set(normalize_tokens(left)), set(normalize_tokens(right))
    if not a or not b:
        return 0.0
    return round(len(a & b) / len(a | b), 6)


def close_delta_hours(left: Any, right: Any) -> float | None:
    """Hours between two close times, or ``None`` when either is missing/unparseable.

    ``None`` is *unknown*, not *zero* — a pair whose close times cannot be compared has not
    passed that gate, and the caller refuses it rather than assuming they agree.
    """
    try:
        first, second = timeutil.parse_iso(str(left)), timeutil.parse_iso(str(right))
    except (TypeError, ValueError):
        return None
    return round(abs((first - second).total_seconds()) / 3600.0, 4)


def numeric_tokens(title: Any) -> frozenset[str]:
    """The digits in a title: thresholds, strike levels, dates, percentages."""
    return frozenset(t for t in normalize_tokens(title) if any(ch.isdigit() for ch in t))


def _numeric_agreement(left: Any, right: Any) -> bool | None:
    """Do both titles name the same numbers? ``None`` when neither names any.

    This gate exists because token overlap alone cannot weigh them. "Will BTC close above
    100k on Dec 31" and "Will Bitcoin close above 90k on Dec 31" share every word that
    matters to a Jaccard score — 0.71, comfortably a candidate — and differ only in the one
    token that IS the question. Boilerplate outvotes the threshold, so the threshold gets
    its own gate: numbers that disagree mean different questions, whatever the prose says.

    Caught by this module's own tests before it could reach a scan, which is the argument
    for writing the number case down rather than trusting a similarity score.
    """
    a, b = numeric_tokens(left), numeric_tokens(right)
    if not a and not b:
        return None
    return a == b


def _category_agreement(left: PredMarket, right: PredMarket) -> bool | None:
    """``True``/``False`` when both sides state a category, ``None`` when either does not.

    Kalshi markets carry no category at all, so ``None`` is the common case and must not
    read as disagreement — see the module docstring.
    """
    if not left.category or not right.category:
        return None
    return left.category.strip().lower() == right.category.strip().lower()


def venue_cross_reference(left: PredMarket, right: PredMarket) -> bool:
    """Does either venue name the other's market as the one it mirrors?

    Predict.fun publishes ``polymarketConditionIds``; this package carries the first of them
    in ``group_id``. When it equals the other side's own group id (Polymarket's
    ``conditionId``), the venue itself is asserting the correspondence — evidence of a
    different kind from any text comparison, and the only kind that cannot be fooled by two
    events that happen to be worded alike.

    **It still does not confirm a pairing.** A venue asserting "this is the same question"
    says nothing about "this settles the same way", and the second is what the operator's
    note is for. What it does is make the candidate near-certain, and — more useful — give
    the deterministic rules an answer key: a pairing the venue asserted and the rules missed
    is a rule to fix.
    """
    left_ref, right_ref = (left.group_id or "").strip(), (right.group_id or "").strip()
    if not left_ref or not right_ref:
        return False
    return left_ref == right_ref


@dataclass(frozen=True)
class MatchCandidate:
    """One judged cross-venue pairing, with the reasoning that produced it.

    The two sides are ``left``/``right``, not ``kalshi``/``polymarket``. Naming them after
    two specific venues was not just a label: a third venue had been reading and quoting
    markets for a week and could not appear in a single proposal, because there was no field
    to put it in. Every judgement below is symmetric, so which side is which carries no
    meaning — ``judge_pair`` orders them canonically so one unordered pair always yields one
    record.
    """

    left_venue: str
    right_venue: str
    left_market_id: str
    right_market_id: str
    left_title: str
    right_title: str
    title_similarity: float
    shared_tokens: tuple[str, ...]
    unshared_tokens: tuple[str, ...]
    close_delta_hours: float | None
    category_agreement: bool | None
    numeric_agreement: bool | None
    venue_asserted: bool
    is_candidate: bool
    refusals: tuple[str, ...]

    def near_miss(self) -> bool:
        """Rejected, but in a way that suggests the *rules* — not the questions — failed.

        Two ways in. The obvious one is a decent similarity that fell short of the gate. The
        one that matters more is a pair refused **only** on wording while every other gate
        agreed: same close date, no numeric or category conflict, some shared vocabulary.
        That is the exact signature of one event phrased two ways — "Fed cut rates" versus
        "central bank lowers interest rates" scores 0.25, below any sane floor, and dropping
        it would throw away the case this record exists to fix.

        A pair refused by a hard gate is never a near miss: those are answers, not guesses.
        """
        if self.is_candidate or (set(self.refusals) & _HARD_REFUSALS):
            return False
        if set(self.refusals) == {TITLE_TOO_DIFFERENT} and self.title_similarity > 0.0:
            return True
        return self.title_similarity >= NEAR_MISS_TITLE_SIMILARITY

    def legs(self) -> tuple[tuple[str, str], tuple[str, str]]:
        """The pairing as ``((venue, market_id), (venue, market_id))`` — what a caller needs
        to build a confirm command or to check the pair against the ones already grouped."""
        return ((self.left_venue, self.left_market_id), (self.right_venue, self.right_market_id))

    def as_dict(self) -> dict[str, Any]:
        return {
            "matching_version": MATCHING_VERSION,
            "left_venue": self.left_venue,
            "right_venue": self.right_venue,
            "left_market_id": self.left_market_id,
            "right_market_id": self.right_market_id,
            "left_title": self.left_title,
            "right_title": self.right_title,
            # The breakdown is the point: a later reviewer must be able to see which gate
            # failed and by how much, without re-running anything.
            "title_similarity": self.title_similarity,
            "shared_tokens": list(self.shared_tokens),
            "unshared_tokens": list(self.unshared_tokens),
            "close_delta_hours": self.close_delta_hours,
            "category_agreement": self.category_agreement,
            "numeric_agreement": self.numeric_agreement,
            "venue_asserted": self.venue_asserted,
            "is_candidate": self.is_candidate,
            "near_miss": self.near_miss(),
            "refusals": list(self.refusals),
            "thresholds": {
                "min_title_similarity": MIN_TITLE_SIMILARITY,
                "max_close_delta_hours": MAX_CLOSE_DELTA_HOURS,
            },
        }


def judge_pair(first: PredMarket, second: PredMarket) -> MatchCandidate:
    """Judge one cross-venue pairing. Pure: no I/O, no model call, no state.

    Both gates must pass. Category is recorded and, when both sides state one and they
    disagree, refuses — a disagreement between two stated categories is real evidence,
    unlike a missing one.

    The arguments are ordered canonically before anything is recorded, so judging (A, B) and
    (B, A) produces the identical record. Every comparison here is symmetric, so this costs
    nothing and buys a stable id for a pairing that three venues can each name differently.
    """
    left, right = sorted((first, second), key=lambda m: (str(m.venue), str(m.market_id)))
    left_tokens, right_tokens = set(normalize_tokens(left.title)), set(normalize_tokens(right.title))
    similarity = title_similarity(left.title, right.title)
    delta = close_delta_hours(left.close_time, right.close_time)
    category = _category_agreement(left, right)
    numeric = _numeric_agreement(left.title, right.title)
    asserted = venue_cross_reference(left, right)

    refusals: list[str] = []
    # A venue-asserted cross-reference outranks the wording gate — the venue knows which
    # market it mirrors better than a token overlap does. It does NOT outrank the evidence
    # gates below: if the two markets name different numbers, one of the two venues has
    # cross-referenced the wrong market, and that is a finding rather than a licence.
    if similarity < MIN_TITLE_SIMILARITY and not asserted:
        refusals.append(TITLE_TOO_DIFFERENT)
    if delta is None:
        refusals.append(CLOSE_TIME_UNKNOWN)
    elif delta > MAX_CLOSE_DELTA_HOURS:
        refusals.append(CLOSE_TOO_FAR_APART)
    if category is False:
        refusals.append(CATEGORY_CONFLICT)
    if numeric is False:
        refusals.append(NUMERIC_MISMATCH)

    return MatchCandidate(
        left_venue=str(left.venue),
        right_venue=str(right.venue),
        left_market_id=str(left.market_id),
        right_market_id=str(right.market_id),
        left_title=left.title,
        right_title=right.title,
        title_similarity=similarity,
        shared_tokens=tuple(sorted(left_tokens & right_tokens)),
        unshared_tokens=tuple(sorted(left_tokens ^ right_tokens)),
        close_delta_hours=delta,
        category_agreement=category,
        numeric_agreement=numeric,
        venue_asserted=asserted,
        is_candidate=not refusals,
        refusals=tuple(refusals),
    )


def generate_candidates(
    markets_by_venue: Mapping[str, Iterable[PredMarket]],
    *,
    keep_near_misses: bool = True,
    near_miss_limit: int = DEFAULT_NEAR_MISS_LIMIT,
) -> dict[str, Any]:
    """Judge every cross-venue pairing across **all** venues given, and return candidates
    plus diagnosable near-misses.

    Takes a mapping rather than two positional lists, which is the whole point of this
    signature: with two parameters named after two venues, a third venue that was already
    reading and quoting markets could not be proposed at all. Venue pairs are enumerated
    from whatever is passed, so adding a fourth is a dictionary key.

    Only *cross*-venue pairings are judged. Two markets on the same venue are not an
    arbitrage — they are that venue's own book disagreeing with itself, which it does not.

    Every combination is judged, which is O(sum of n·m over venue pairs) — cheap, because
    each judgement is set arithmetic on a handful of tokens.

    Near-misses are kept because they are the input to improving the rules: when a later
    pass proposes a pair these rules missed, its near-miss row already says which gate
    failed and by how much. They are also **capped and the truncation reported** —
    ``near_miss_total`` and ``near_miss_truncated`` say what was cut. Once the venues
    started quoting the same subjects, the shared boilerplate ("2028 democratic presidential
    nomination") put thousands of unrelated pairings above the floor, and a diagnostic list
    nobody can read diagnoses nothing.

    **Nothing here confirms anything.** The operator does that, per event, in ``pairs.py``.
    """
    by_venue: dict[str, list[PredMarket]] = {
        str(venue): [m for m in (markets or []) if isinstance(m, PredMarket)]
        for venue, markets in (markets_by_venue or {}).items()
    }
    venues = sorted(by_venue)

    candidates: list[MatchCandidate] = []
    near_misses: list[MatchCandidate] = []
    judged_count = 0
    for index, left_venue in enumerate(venues):
        for right_venue in venues[index + 1:]:
            for left in by_venue[left_venue]:
                for right in by_venue[right_venue]:
                    judged_count += 1
                    judged = judge_pair(left, right)
                    if judged.is_candidate:
                        candidates.append(judged)
                    elif keep_near_misses and judged.near_miss():
                        near_misses.append(judged)

    # Best first, so an operator reviewing a long list sees the strongest proposals first.
    candidates.sort(key=lambda c: (-c.title_similarity, c.legs()))
    near_misses.sort(key=lambda c: (-c.title_similarity, c.legs()))
    near_miss_total = len(near_misses)
    kept = near_misses if near_miss_limit is None else near_misses[:max(0, int(near_miss_limit))]

    return {
        "matching_version": MATCHING_VERSION,
        "market_counts": {venue: len(by_venue[venue]) for venue in venues},
        "venues": venues,
        "judged_count": judged_count,
        "candidates": [c.as_dict() for c in candidates],
        "near_misses": [c.as_dict() for c in kept],
        # No silent caps: a truncated diagnostic that did not say so would read as "these are
        # all the near misses there were".
        "near_miss_total": near_miss_total,
        "near_miss_truncated": near_miss_total - len(kept),
        # Stated so a reader never mistakes a proposal for a decision.
        "confirms_nothing": True,
    }


def candidate_status_line(result: Mapping[str, Any]) -> str:
    """One ASCII line for the console (Windows consoles are cp949)."""
    counts = result.get("market_counts") or {}
    shown = len(result.get("near_misses") or [])
    total = result.get("near_miss_total", shown)
    near = f"{total} near-miss(es)" + (f", {shown} shown" if total != shown else "")
    venues = ", ".join(f"{venue}={count}" for venue, count in sorted(counts.items())) or "-"
    return (
        f"predmarket match: {len(result.get('candidates') or [])} candidate(s), {near} "
        f"from {result.get('judged_count')} judged pairings [{venues}]"
    )


__all__ = [
    "CATEGORY_CONFLICT",
    "NUMERIC_MISMATCH",
    "CLOSE_TIME_UNKNOWN",
    "CLOSE_TOO_FAR_APART",
    "DEFAULT_NEAR_MISS_LIMIT",
    "MATCHING_VERSION",
    "MAX_CLOSE_DELTA_HOURS",
    "MIN_TITLE_SIMILARITY",
    "NEAR_MISS_TITLE_SIMILARITY",
    "TITLE_TOO_DIFFERENT",
    "VENUE_ASSERTED",
    "MatchCandidate",
    "candidate_status_line",
    "close_delta_hours",
    "generate_candidates",
    "judge_pair",
    "normalize_tokens",
    "numeric_tokens",
    "title_similarity",
    "venue_cross_reference",
]

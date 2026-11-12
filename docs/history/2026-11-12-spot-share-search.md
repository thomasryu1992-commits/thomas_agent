# 2026-11-12 — the spot share search: one attempt, with independence from M04 written into the rule

`docs/proposals/SPOT_SHARE_SEARCH_V0.1.md`, `scripts/research/spot_share_search.py`.

**Why the overlap is a rule and not a footnote.** The survey dropped coin age because M04's diagnostic had shaped
it, then recommended spot share, which rests on the same "less crowded side" idea as M04. Held to one standard,
a pass of S01 could be M04 seen through another variable. Earlier searches printed the leg overlap beside the
result; here a pass is not a candidate unless the overlap is under a limit fixed before the run, over a minimum
number of decisions (M04's inputs start in 2022, so the comparison covers about three of the five years).

**The ticker is not the asset.** `spot_pair` matches a perp to a spot pair by name. Counting days by price showed
one real case (the old LUNA perp against the new LUNA spot) and one artefact of the check itself (`1000SATS`, whose
spot pair carries the prefix too, so no multiple applies). A day now counts only when the two closes agree within
a band, and a wrong-asset stretch is a named cause instead of a silently wrong share.

**"Nothing to fetch" was wrong.** The survey said the spot files were on disk. Five pairs were served by the
archive and never fetched, because the funding-basis work fetched only its own targets. The tool therefore fetches
first and writes down which months the archive's own listing holds, so "not served" is read from the listing and a
file that should exist but does not is UNEXPLAINED and stops the run.

**The span was not chosen.** The universe has under four members until 2020-04, which makes the first weeks
empty. Starting later would have been a choice made after looking at counts; using `basket_search.DEV` as the ten
price attempts did is not.

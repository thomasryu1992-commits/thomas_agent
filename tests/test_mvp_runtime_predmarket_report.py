"""PM1's exit artifact — and the three ways a duration can lie.

The report answers how often, how large, and **how long**. The third decides whether PM3 is
worth building: it puts minutes of human approval between seeing an edge and taking it, so
an edge that lives thirty seconds is unreachable however frequent or fat it is.

Which makes the persistence number the one worth attacking. Every test below is about an
arithmetic that would look right and be wrong:

- calling a single sighting "zero seconds" and concluding opportunities never persist;
- stitching an episode across an outage and inventing the minutes nobody watched;
- reporting an episode that is still running as though it had ended;
- dividing by scans instead of readings, so a venue being down reads as a quiet market.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.predmarket import observations as obs
from runtime.mvp_runtime.predmarket import report

NOW = "2026-08-20T00:00:00Z"


def _row(minute, *, is_opportunity=True, net_edge=0.04, event="e1",
         legs=(("kalshi", "K1"), ("polymarket", "P1")), reasons=None):
    return {
        "event_id": event,
        "legs": [{"venue": v, "market_id": m} for v, m in legs],
        "net_edge": net_edge,
        "is_opportunity": is_opportunity,
        "reasons": list(reasons or []),
        "observed_at_utc": f"2026-08-01T00:{minute:02d}:00Z",
    }


# --- the pairing key ------------------------------------------------------------

def test_a_pairing_is_the_same_pairing_whichever_order_its_legs_are_in():
    """Two spellings of one pairing would split its episodes in half — halving every
    duration in the report, which is the number the whole phase exists to produce."""
    forward = _row(0, legs=(("kalshi", "K1"), ("polymarket", "P1")))
    backward = _row(0, legs=(("polymarket", "P1"), ("kalshi", "K1")))
    assert report.pairing_key(forward) == report.pairing_key(backward)


def test_different_events_are_different_pairings():
    assert report.pairing_key(_row(0, event="e1")) != report.pairing_key(_row(0, event="e2"))


# --- duration is an interval ----------------------------------------------------

def test_one_sighting_is_not_zero_seconds():
    """It lasted somewhere between an instant and the gap to the scans either side. The
    witnessed lower bound is 0; the report must still carry an upper bound, or the artifact
    concludes 'opportunities never persist' from a signal that says nothing of the kind."""
    eps = report.episodes([_row(0, is_opportunity=False), _row(2), _row(4, is_opportunity=False)])
    assert len(eps) == 1
    assert eps[0]["duration_lower_seconds"] == 0.0
    assert eps[0]["duration_upper_seconds"] == 120.0
    assert eps[0]["observation_count"] == 1


def test_a_run_of_sightings_measures_the_span_it_witnessed():
    eps = report.episodes([_row(0, is_opportunity=False), _row(2), _row(4), _row(6),
                           _row(8, is_opportunity=False)])
    assert len(eps) == 1
    assert eps[0]["duration_lower_seconds"] == 240.0     # 00:02 -> 00:06, witnessed
    assert eps[0]["duration_upper_seconds"] == 360.0     # ...and up to the next reading
    assert eps[0]["ended"] == "ended_observed"


def test_an_episode_still_running_has_no_upper_bound_rather_than_a_wrong_one():
    """Right-censored. Filling in an upper bound here would be the report inventing its own
    most important number."""
    eps = report.episodes([_row(0, is_opportunity=False), _row(2), _row(4)])
    assert eps[0]["ended"] == "ended_open"
    assert eps[0]["duration_upper_seconds"] is None
    assert eps[0]["duration_lower_seconds"] == 120.0


def test_an_opportunity_already_running_at_the_first_reading_says_so():
    """An edge that was there before we looked did not begin when we looked."""
    eps = report.episodes([_row(0), _row(2), _row(4, is_opportunity=False)])
    assert eps[0]["started_open"] is True


def test_the_second_episode_is_not_marked_as_already_running():
    eps = report.episodes([_row(0), _row(2, is_opportunity=False), _row(4),
                           _row(6, is_opportunity=False)])
    assert [e["started_open"] for e in eps] == [True, False]


# --- a gap ends knowledge, not the opportunity ----------------------------------

def test_an_outage_cuts_the_episode_instead_of_being_stitched_across():
    """'Seen at 00:00 and again at 00:30' is not evidence it was there at 00:15. Stitching
    would inflate exactly the number PM3 turns on."""
    eps = report.episodes([_row(0), _row(2), _row(30), _row(32)], max_gap_seconds=360)
    assert len(eps) == 2
    assert eps[0]["ended"] == "ended_gap"
    assert eps[0]["duration_lower_seconds"] == 120.0
    assert eps[1]["duration_lower_seconds"] == 120.0


def test_a_gap_inside_tolerance_is_a_missed_scan_not_an_outage():
    """One or two missed 2-minute scans is noise; the episode continues."""
    eps = report.episodes([_row(0), _row(4), _row(8)], max_gap_seconds=360)
    assert len(eps) == 1 and eps[0]["duration_lower_seconds"] == 480.0


def test_a_gap_ended_episode_reports_no_upper_bound():
    """The next reading came too late to bound anything."""
    eps = report.episodes([_row(0), _row(2), _row(40)], max_gap_seconds=360)
    assert eps[0]["ended"] == "ended_gap" and eps[0]["duration_upper_seconds"] is None


def test_rows_are_ordered_before_anything_is_measured():
    """The store is append-only but a report must not depend on that: out-of-order rows
    would otherwise produce negative or fragmented durations."""
    eps = report.episodes([_row(6), _row(2), _row(4)])
    assert len(eps) == 1 and eps[0]["duration_lower_seconds"] == 240.0


# --- how often, and against what denominator ------------------------------------

def test_frequency_divides_by_readings_not_by_attempts():
    """A venue that was down produced attempts, not readings. Dividing by attempts would let
    an outage read as a market with no opportunities in it."""
    rows = [_row(0), _row(2, is_opportunity=False),
            _row(4, is_opportunity=False, net_edge=None, reasons=[obs.VENUE_UNREADABLE]),
            _row(6, is_opportunity=False, net_edge=None, reasons=[obs.VENUE_UNREADABLE])]
    built = report.build_pm1_report(rows, now=NOW)
    pairing = built["pairings"][0]
    assert pairing["observation_count"] == 4
    assert pairing["readable_count"] == 2
    assert pairing["opportunity_rate"] == 0.5          # 1 of 2 readings, not 1 of 4 attempts
    assert built["coverage"] == 0.5


def test_a_report_that_could_not_see_enough_says_so_instead_of_answering():
    rows = [_row(i, is_opportunity=False, net_edge=None, reasons=[obs.VENUE_UNREADABLE])
            for i in range(0, 10, 2)] + [_row(10)]
    built = report.build_pm1_report(rows, now=NOW)
    assert built["verdict"] == report.INSUFFICIENT_COVERAGE
    assert built["is_exit_artifact"] is False


def test_an_afternoon_of_data_is_a_progress_check_not_the_exit_artifact():
    """The roadmap asks for two to four weeks. A short run must never be mistaken for the
    thing that decides whether PM2 starts."""
    built = report.build_pm1_report([_row(0), _row(2), _row(4)], now=NOW)
    assert built["verdict"] == report.INSUFFICIENT_WINDOW
    assert built["is_exit_artifact"] is False


def test_a_long_enough_well_covered_window_is_the_artifact():
    rows = []
    for day in range(1, 20):
        rows.append({**_row(0), "observed_at_utc": f"2026-08-{day:02d}T00:00:00Z"})
        rows.append({**_row(2, is_opportunity=False), "observed_at_utc": f"2026-08-{day:02d}T00:02:00Z"})
    built = report.build_pm1_report(rows, now=NOW)
    assert built["window"]["span_days"] > report.EXIT_ARTIFACT_MIN_DAYS
    assert built["verdict"] == report.SUFFICIENT and built["is_exit_artifact"] is True


def test_an_empty_store_reports_nothing_rather_than_zero_opportunities():
    """'No observations' and 'observed, found nothing' are different findings, and only one
    of them is about the market."""
    built = report.build_pm1_report([], now=NOW)
    assert built["verdict"] == report.NO_OBSERVATIONS
    assert built["pairing_count"] == 0 and built["is_exit_artifact"] is False


# --- how large ------------------------------------------------------------------

def test_the_net_edge_summary_separates_all_readings_from_the_winning_ones():
    """Median net edge across everything answers 'how far apart do these venues sit'; median
    across opportunities answers 'what is on the table when there is something'. Reporting
    one as the other overstates or understates by the whole width of the loss-making side."""
    rows = [_row(0, net_edge=0.05), _row(2, net_edge=0.07),
            _row(4, is_opportunity=False, net_edge=-0.03)]
    pairing = report.build_pm1_report(rows, now=NOW)["pairings"][0]
    assert pairing["opportunity_net_edge"]["median"] == pytest.approx(0.06)
    assert pairing["opportunity_net_edge"]["max"] == pytest.approx(0.07)
    assert pairing["net_edge"]["min"] == pytest.approx(-0.03)


def test_a_stale_pairing_is_reported_separately_from_an_outage():
    """A market that stopped being listed means the group is resolved or wrong — an operator
    action. A venue that did not answer means the scanner had a bad night."""
    rows = [_row(0, is_opportunity=False, net_edge=None, reasons=[obs.MARKET_NOT_LISTED]),
            _row(2, is_opportunity=False, net_edge=None, reasons=[obs.VENUE_UNREADABLE]),
            _row(4)]
    incidents = report.build_pm1_report(rows, now=NOW)["incidents"]
    assert incidents[obs.MARKET_NOT_LISTED] == 1
    assert incidents[obs.VENUE_UNREADABLE] == 1


def test_a_scan_that_ran_on_the_mock_explains_its_own_collapsed_coverage():
    """The third incident kind. A run whose Safety-Flag gate was closed reads every group as
    SOURCE_SYNTHETIC: no row is a reading, so coverage falls to zero and the verdict is
    INSUFFICIENT_COVERAGE — which is right, and useless on its own. Without the tally the
    operator sees coverage collapse with both other counts at zero and nothing saying why."""
    rows = [_row(i, is_opportunity=False, net_edge=None, reasons=[obs.SOURCE_SYNTHETIC])
            for i in range(0, 10, 2)]
    built = report.build_pm1_report(rows, now=NOW)
    assert built["verdict"] == report.INSUFFICIENT_COVERAGE
    assert built["is_exit_artifact"] is False
    assert built["incidents"][obs.SOURCE_SYNTHETIC] == 5
    assert built["incidents"][obs.MARKET_NOT_LISTED] == 0
    assert built["incidents"][obs.VENUE_UNREADABLE] == 0


def test_pairings_are_measured_independently():
    rows = [_row(0, legs=(("kalshi", "K1"), ("polymarket", "P1"))),
            _row(2, legs=(("kalshi", "K1"), ("polymarket", "P1"))),
            _row(0, legs=(("kalshi", "K2"), ("polymarket", "P2")), is_opportunity=False)]
    built = report.build_pm1_report(rows, now=NOW)
    assert built["pairing_count"] == 2
    counts = {p["opportunity_count"] for p in built["pairings"]}
    assert counts == {0, 2}


# --- the rendering and the boundary ---------------------------------------------

def test_the_rendered_report_is_ascii_and_names_the_censoring():
    rows = [_row(0), _row(2), _row(4)]        # still open at the end of the record
    text = report.render_pm1_report_text(report.build_pm1_report(rows, now=NOW))
    text.encode("ascii")
    assert "still open" in text and "lower bounds" in text
    assert "INSUFFICIENT_WINDOW" in text


def test_the_report_authorizes_nothing():
    built = report.build_pm1_report([_row(0)], now=NOW)
    assert built["authorizes_trading"] is False
    assert "Observation only" in report.render_pm1_report_text(built)
    # No writer for anything: a report reads the store and returns a dict.
    for name in ("append_observations", "confirm_group", "run_watch_scan"):
        assert not hasattr(report, name)


def test_the_report_reads_the_store_when_no_rows_are_passed(tmp_path, monkeypatch):
    from runtime.mvp_runtime.predmarket import pairs

    monkeypatch.setattr(pairs, "_repo_root", lambda: tmp_path)
    obs.append_observations([_row(0), _row(2)], root=tmp_path)
    built = report.build_pm1_report(now=NOW, root=tmp_path)
    assert built["observation_count"] == 2 and built["pairing_count"] == 1


# --- the window can be named ----------------------------------------------------

def test_since_bounds_the_window_and_says_it_did():
    """The rules that produce a row changed while rows accumulated — what counted as a
    reading, whether a Binance leg could be priced, twice what counted as an opportunity.
    Frequency computed across a rule change is a number about two instruments.

    A bounded report has to say so, or it is indistinguishable from one over everything —
    which is the mistake it exists to prevent."""
    rows = [_row(0), _row(2), _row(4), _row(6)]
    bounded = report.build_pm1_report(rows, now=NOW, since="2026-08-01T00:04:00Z")

    assert bounded["observation_count"] == 2                 # minute 4 and 6
    assert bounded["window"]["since"] == "2026-08-01T00:04:00Z"
    assert bounded["window"]["excluded_before_since"] == 2
    assert "BOUNDED" in report.render_pm1_report_text(bounded)


def test_an_unbounded_report_says_nothing_about_bounds():
    """The flag must not leak into the normal case: a report over everything should read
    exactly as it did before this existed."""
    built = report.build_pm1_report([_row(0), _row(2)], now=NOW)
    assert built["window"]["since"] is None
    assert built["window"]["excluded_before_since"] == 0
    assert "BOUNDED" not in report.render_pm1_report_text(built)


def test_since_filters_and_never_deletes():
    """The store is append-only and self-hashed because it is evidence. The answer to 'these
    rows were made under an older rule' is to be able to say so, not to destroy them."""
    rows = [_row(0), _row(2), _row(4)]
    report.build_pm1_report(rows, now=NOW, since="2026-08-01T00:04:00Z")
    assert len(rows) == 3                                    # the caller's list is untouched

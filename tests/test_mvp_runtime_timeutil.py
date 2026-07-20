"""timeutil — the one place the runtime's fixed UTC timestamp form is produced and parsed.

Every expiry in the runtime (approvals, permission decisions, safety-flag activations,
working-memory retention, schedules) is enforced by a *string* comparison that is only a
correct time comparison for the fixed ``YYYY-MM-DDThh:mm:ssZ`` form. So the failure mode
these tests guard is not "wrong error message" — it is a timestamp that is silently wrong
by the machine's UTC offset, in the never-expires direction for a negative one.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from runtime.mvp_runtime import timeutil


def test_now_is_the_fixed_utc_form():
    now = timeutil.utc_now_iso()
    assert len(now) == 20 and now.endswith("Z")
    assert timeutil.parse_iso(now).tzinfo is not None


@pytest.mark.parametrize("value", ["2026-07-20T09:00:00Z", "2026-07-20T09:00:00+00:00"])
def test_parse_accepts_explicit_utc(value):
    assert timeutil.parse_iso(value) == datetime(2026, 7, 20, 9, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("value", ["2026-07-20T09:00:00", "2026-07-20"])
def test_parse_rejects_a_naive_timestamp(value):
    """Assuming UTC for an offset-less value produces an expiry wrong by the local
    offset; the schema-level format check is a no-op without an optional dependency, so
    this is the check that actually runs."""
    with pytest.raises(ValueError):
        timeutil.parse_iso(value)


def test_format_converts_to_utc_before_stamping_z():
    """Stamping Z without converting labels the wrong instant as UTC."""
    kst = timezone(timedelta(hours=9))
    assert timeutil.format_iso(datetime(2026, 7, 20, 18, 0, tzinfo=kst)) == "2026-07-20T09:00:00Z"
    assert timeutil.format_iso(datetime(2026, 7, 20, 9, 0, tzinfo=timezone.utc)) == "2026-07-20T09:00:00Z"


def test_arithmetic_on_a_non_utc_input_stays_correct():
    """plus_minutes("...+09:00", 60) used to return a time nine hours off, still labelled Z —
    and every expiry check downstream is a string compare that trusts the label."""
    assert timeutil.plus_minutes("2026-07-20T18:00:00+09:00", 60) == "2026-07-20T10:00:00Z"
    assert timeutil.plus_seconds("2026-07-20T18:00:00+09:00", 60) == "2026-07-20T09:01:00Z"


def test_arithmetic_round_trips_the_canonical_form():
    assert timeutil.plus_minutes("2026-07-20T09:00:00Z", 30) == "2026-07-20T09:30:00Z"
    assert timeutil.plus_seconds("2026-07-20T09:00:00Z", 90) == "2026-07-20T09:01:30Z"

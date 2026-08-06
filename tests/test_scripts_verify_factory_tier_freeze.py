"""A freeze is not verified by a zero — it is verified by a zero AFTER the window.

`scripts/verify_factory_tier_freeze` exists to answer "did disabling these schedules
actually stop the mints", and the way that question goes wrong is silent: every tier
reads zero before its due time, so a check run in the morning grades a freeze PASS on
evidence that has not happened yet. These tests pin the two rules that close it — grade
against the cancelled `next_run_at` rather than `last_run_at`, and refuse to grade at all
while that occurrence is still ahead.
"""

from __future__ import annotations

from scripts.verify_factory_tier_freeze import (
    ACTIVE,
    FAIL,
    PASS,
    UNPROVEN,
    grade_tier,
    tier_of,
)


class _Schedule:
    """The three fields the grader reads off `scheduler.Schedule`."""

    def __init__(self, *, enabled: bool, next_run_at: str, last_run_at: str | None = None):
        self.enabled = enabled
        self.next_run_at = next_run_at
        self.last_run_at = last_run_at


def _candidate(timeframe: str, created_at_utc: str) -> dict:
    return {"created_at_utc": created_at_utc, "strategy_spec": {"timeframe": timeframe}}


FROZEN_1H = [_Schedule(enabled=False, next_run_at="2026-08-06T08:09:10Z",
                       last_run_at="2026-08-05T08:09:26Z")] * 5


def test_a_frozen_tier_is_unproven_until_its_cancelled_occurrence_passes():
    """05:00Z, freeze applied, fire due 08:09Z. Nothing minted — and nothing proved."""
    verdict, why, minted = grade_tier(FROZEN_1H, [], tier="1h", now="2026-08-06T05:00:00Z")
    assert verdict == UNPROVEN, why
    assert minted == 0
    assert "has not passed" in why


def test_the_same_tier_passes_once_the_occurrence_is_behind_it():
    verdict, why, _ = grade_tier(FROZEN_1H, [], tier="1h", now="2026-08-06T08:30:00Z")
    assert verdict == PASS, why
    assert "2026-08-06" in why


def test_a_frozen_tier_that_minted_anyway_fails():
    records = [_candidate("1h", "2026-08-06T08:09:30Z")]
    verdict, why, minted = grade_tier(FROZEN_1H, records, tier="1h", now="2026-08-06T08:30:00Z")
    assert verdict == FAIL, why
    assert minted == 1


def test_mints_from_another_tier_do_not_fail_this_one():
    """The 4h block shares the 1h block's due time, so its mints land in the same minute."""
    records = [_candidate("4h", "2026-08-06T08:09:30Z")]
    verdict, _, minted = grade_tier(FROZEN_1H, records, tier="1h", now="2026-08-06T08:30:00Z")
    assert verdict == PASS
    assert minted == 0


def test_a_long_frozen_tier_is_graded_on_its_own_stale_occurrence_not_today():
    """15m was frozen 2026-08-04. Reading `last_run_at` would leave it forever ungradeable,
    because disabling freezes that stamp; `next_run_at` still names the cancelled fire."""
    frozen_15m = [_Schedule(enabled=False, next_run_at="2026-08-04T08:09:10Z",
                            last_run_at="2026-08-03T08:09:37Z")] * 5
    # A 15m candidate exists in the store, but from BEFORE the freeze — it must not count.
    records = [_candidate("15m", "2026-08-03T08:10:00Z")]
    verdict, why, minted = grade_tier(frozen_15m, records, tier="15m", now="2026-08-06T05:00:00Z")
    assert verdict == PASS, why
    assert minted == 0
    assert "2026-08-04" in why


def test_an_enabled_tier_carries_no_verdict():
    """Nothing to verify about a tier that is supposed to mint; it reports, it does not grade."""
    active = [_Schedule(enabled=True, next_run_at="2026-08-07T03:35:20Z",
                        last_run_at="2026-08-06T03:35:30Z")] * 5
    records = [_candidate("1d", "2026-08-06T03:35:30Z")]
    verdict, _, minted = grade_tier(active, records, tier="1d", now="2026-08-06T05:00:00Z")
    assert verdict == ACTIVE
    assert minted == 1


def test_a_tier_with_one_schedule_still_enabled_is_not_frozen():
    """Four of five disabled is not a freeze, and must never read PASS."""
    partly = [*[_Schedule(enabled=False, next_run_at="2026-08-06T08:09:10Z")] * 4,
              _Schedule(enabled=True, next_run_at="2026-08-06T08:09:10Z")]
    verdict, _, _ = grade_tier(partly, [], tier="1h", now="2026-08-06T08:30:00Z")
    assert verdict == ACTIVE


def test_the_latest_next_run_at_is_the_one_graded():
    """Sibling schedules stamp seconds apart; a window is past only when the last one is."""
    staggered = [_Schedule(enabled=False, next_run_at="2026-08-06T08:09:10Z"),
                 _Schedule(enabled=False, next_run_at="2026-08-06T08:09:50Z")]
    verdict, _, _ = grade_tier(staggered, [], tier="1h", now="2026-08-06T08:09:30Z")
    assert verdict == UNPROVEN


def test_the_context_free_legacy_schedule_names_no_tier():
    assert tier_of("BTCUSDT 1h") == "1h"
    assert tier_of("") is None
    assert tier_of("BTCUSDT") is None

"""Time rules for approvals, the lifetime-versus-deadline invariant, and clock trust.

The numbers themselves are not decided here (CR-10, CR-11 and CR-19 values wait for C4
measurements). What is decided, and enforced, is the shape:

- an approval's validity window is checked on the wall clock; an action's deadline runs on the
  monotonic clock;
- an action starts only when the approval stays valid for the whole deadline, so no effect is
  ever cut off by an expiry halfway through;
- therefore the maximum approval lifetime must be at least the longest deadline, and a policy
  that breaks that refuses to start;
- a clock that went backwards past its tolerance, jumped against the monotonic clock, or whose
  synchronisation is not confirmed starts no new effect.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from types import MappingProxyType
from typing import Any, Mapping

from .reasons import RunnerRefused

_UTC_SECOND = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")


def parse_utc_second(value: Any) -> datetime:
    """A ``YYYY-MM-DDTHH:MM:SSZ`` stamp as an aware UTC datetime, or ``APPROVAL_MALFORMED``."""
    if not isinstance(value, str) or not _UTC_SECOND.match(value):
        raise RunnerRefused("APPROVAL_MALFORMED")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        raise RunnerRefused("APPROVAL_MALFORMED") from None


@dataclass(frozen=True)
class TimePolicy:
    """Configuration constants. Every value is in whole seconds and must be positive."""

    max_lifetime_s: int
    issue_skew_s: int
    deadlines_s: Mapping[str, int] = field(default_factory=dict)
    clock_jump_s: int = 5
    backstep_tolerance_s: int = 5

    def __post_init__(self) -> None:
        object.__setattr__(self, "deadlines_s", MappingProxyType(dict(self.deadlines_s)))


def check_time_policy(policy: TimePolicy) -> None:
    """``RUNNER_STARTUP_BLOCKED`` unless every value is a positive whole number of seconds and the
    maximum approval lifetime covers the longest deadline."""
    values = [policy.max_lifetime_s, policy.issue_skew_s, policy.clock_jump_s,
              policy.backstep_tolerance_s, *policy.deadlines_s.values()]
    if not policy.deadlines_s or any(
            not isinstance(v, int) or isinstance(v, bool) or v <= 0 for v in values):
        raise RunnerRefused("RUNNER_STARTUP_BLOCKED")
    if policy.max_lifetime_s < max(policy.deadlines_s.values()):
        raise RunnerRefused("RUNNER_STARTUP_BLOCKED")


@dataclass(frozen=True)
class ClockAnchor:
    """The wall and monotonic readings taken together when the runner started."""

    wall: datetime
    mono: float


def check_clock(policy: TimePolicy, anchor: ClockAnchor, *, now_wall: datetime, now_mono: float,
                last_seen_wall: datetime | None, sync_confirmed: bool) -> None:
    """``CLOCK_UNTRUSTED`` when the clock went back, jumped, or is not confirmed synchronised."""
    if not sync_confirmed:
        raise RunnerRefused("CLOCK_UNTRUSTED")
    if last_seen_wall is not None and now_wall < last_seen_wall - timedelta(seconds=policy.backstep_tolerance_s):
        raise RunnerRefused("CLOCK_UNTRUSTED")
    wall_elapsed = (now_wall - anchor.wall).total_seconds()
    mono_elapsed = now_mono - anchor.mono
    if mono_elapsed < 0 or abs(wall_elapsed - mono_elapsed) > policy.clock_jump_s:
        raise RunnerRefused("CLOCK_UNTRUSTED")


def check_approval_window(policy: TimePolicy, *, action: str, issued_at: datetime, expires_at: datetime,
                          now: datetime, cutoff_at: datetime | None = None) -> None:
    """The approval's window against ``now`` and the action's deadline, in a fixed order."""
    deadline = policy.deadlines_s.get(action)
    if deadline is None:
        raise RunnerRefused("VERB_NOT_PERMITTED")
    if expires_at <= issued_at or (expires_at - issued_at).total_seconds() > policy.max_lifetime_s:
        raise RunnerRefused("APPROVAL_LIFETIME_EXCEEDS_POLICY")
    if cutoff_at is not None and issued_at < cutoff_at:
        raise RunnerRefused("APPROVAL_BEFORE_CUTOFF")
    if issued_at > now + timedelta(seconds=policy.issue_skew_s):
        raise RunnerRefused("APPROVAL_NOT_YET_VALID")
    if now >= expires_at:
        raise RunnerRefused("APPROVAL_EXPIRED")
    if now + timedelta(seconds=deadline) > expires_at:
        raise RunnerRefused("APPROVAL_TOO_SHORT_FOR_ACTION")

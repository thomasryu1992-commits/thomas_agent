"""Single source for the MVP runtime's UTC timestamp handling.

Every runtime timestamp is an RFC3339 UTC instant in the fixed
``YYYY-MM-DDThh:mm:ssZ`` form. Parsing accepts the trailing ``Z`` (which
``datetime.fromisoformat`` did not accept before 3.11). These helpers previously
lived, byte-identical, in intake/pipeline/permission/prime/safety_gate — one home
now, so the format cannot drift between producers and validators of the same field.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

_ISO_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def utc_now_iso() -> str:
    """Current UTC instant as ``YYYY-MM-DDThh:mm:ssZ``."""
    return datetime.now(timezone.utc).strftime(_ISO_FORMAT)


def parse_iso(value: str) -> datetime:
    """Parse an RFC3339 UTC timestamp, accepting the ``Z`` suffix.

    Raises ``ValueError`` (via ``datetime.fromisoformat``) on a malformed value;
    callers that need a typed failure catch it and re-raise their own error.
    """
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def format_iso(dt: datetime) -> str:
    """Format a datetime as ``YYYY-MM-DDThh:mm:ssZ`` (seconds precision, UTC ``Z``)."""
    return dt.strftime(_ISO_FORMAT)


def plus_minutes(now: str, minutes: int) -> str:
    """Return ``now`` (RFC3339) advanced by ``minutes``, in the same fixed form."""
    return format_iso(parse_iso(now) + timedelta(minutes=minutes))


def plus_seconds(now: str, seconds: int) -> str:
    """Return ``now`` (RFC3339) advanced by ``seconds``, in the same fixed form."""
    return format_iso(parse_iso(now) + timedelta(seconds=seconds))

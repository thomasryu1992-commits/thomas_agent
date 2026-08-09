"""Single source for the RFC3339 UTC stamp the governance scripts write.

Thirteen scripts each carried their own

    datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

— a four-call chain whose failure mode is silent. Drop ``.replace(microsecond=0)`` and the
value gains microseconds; the runtime compares these timestamps **lexicographically** (expiries,
``next_run_at``), so a stamp in a different width is not merely ugly, it orders wrong against
every other stamp in the store. That is the class of bug ``runtime/mvp_runtime/timeutil.py``
exists to have removed once, on the runtime side.

**Why this is not just an import of `timeutil`.** `_scripts_bridge.py` already puts ``scripts/``
on ``sys.path`` so the *runtime* can reach ``scripts/lib``. Having the scripts import the runtime
back would make that dependency bidirectional — and most of these are ``validate_*`` gates whose
job is to validate the runtime. A gate that imports what it gates is a worse property than a
duplicated helper, so the two packages keep their own copy of this one rule, deliberately.
``REMAINING_WORK.md`` §G4b is the authority for that split.

The format is the same fixed ``YYYY-MM-DDThh:mm:ssZ`` the runtime writes, and
``test_scripts_utctime.py`` pins the two against each other so the halves cannot drift apart
without a test saying so.
"""

from __future__ import annotations

from datetime import datetime, timezone

#: The one stamp format the governance records use; identical to ``timeutil._ISO_FORMAT``.
ISO_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def utc_now() -> str:
    """Current UTC instant as ``YYYY-MM-DDThh:mm:ssZ``."""
    return datetime.now(timezone.utc).strftime(ISO_FORMAT)


def format_utc(moment: datetime) -> str:
    """Format a datetime as ``YYYY-MM-DDThh:mm:ssZ``, converting to UTC first.

    Converting rather than relabelling: stamping ``Z`` on a ``+09:00`` datetime without moving
    it names the wrong instant as UTC, and every expiry check downstream is a string compare
    that trusts the label. A naive datetime is assumed to already be UTC, which is what the
    callers that build one from parsed governance records mean.
    """
    if moment.tzinfo is not None:
        moment = moment.astimezone(timezone.utc)
    return moment.strftime(ISO_FORMAT)

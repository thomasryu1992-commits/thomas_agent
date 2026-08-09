"""The scripts' UTC stamp and the runtime's are one format kept in two places on purpose.

`REMAINING_WORK.md` §G4b: `_scripts_bridge.py` already puts `scripts/` on `sys.path` so the
*runtime* can reach `scripts/lib`, and most of the scripts that stamp a timestamp are `validate_*`
gates whose job is to validate the runtime. Having them import the runtime back would make that
dependency bidirectional — a gate importing what it gates. So the rule lives twice, deliberately,
and these tests are what keep the two copies from becoming two formats.

The failure they exist to catch is silent: drop `.replace(microsecond=0)` from the four-call chain
these replaced and the stamp grows a microsecond field. Every expiry check downstream is a
**lexicographic** string compare, so a wider stamp does not merely look different, it orders wrong
against every other stamp in the store.
"""

from __future__ import annotations

import pathlib
import re
import sys
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from lib import utctime  # noqa: E402

from runtime.mvp_runtime import timeutil  # noqa: E402

FIXED = datetime(2026, 8, 8, 9, 41, 5, 123456, tzinfo=timezone.utc)


def test_the_two_halves_produce_the_same_stamp():
    """One format, two homes. If these ever disagree, a record written by a script and one written
    by the runtime stop comparing correctly — which is the whole reason the duplication is allowed."""
    assert utctime.format_utc(FIXED) == timeutil.format_iso(FIXED) == "2026-08-08T09:41:05Z"
    assert utctime.ISO_FORMAT == "%Y-%m-%dT%H:%M:%SZ"
    assert timeutil.FIXED_UTC_PATTERN.match(utctime.utc_now())


def test_an_offset_is_converted_not_relabelled():
    """Stamping `Z` on a `+09:00` datetime without moving it names the wrong instant as UTC, and
    every expiry check downstream is a string compare that trusts the label."""
    kst = FIXED.astimezone(timezone(timedelta(hours=9)))
    assert kst.hour != FIXED.hour  # the input really is in another offset
    assert utctime.format_utc(kst) == timeutil.format_iso(kst) == "2026-08-08T09:41:05Z"


def test_the_stamp_is_second_resolution_and_fixed_width():
    """The microsecond field is the silent failure: it makes the value longer, and a lexicographic
    compare against a narrower stamp is then wrong rather than merely inconsistent."""
    now = utctime.utc_now()
    assert len(now) == 20 and now.endswith("Z") and "." not in now
    assert utctime.format_utc(FIXED) < utctime.format_utc(FIXED + timedelta(seconds=1))


def test_no_script_has_gone_back_to_hand_rolling_the_chain():
    """The four-call chain is what §G4b removed. `build_i0_4_contract_set_lock.py` keeps its copy
    on purpose — it is a frozen i0.4 artifact builder that nothing invokes and that imports neither
    `lib` nor `runtime`, so giving it an import would cost it its standalone property to remove a
    line that has no caller. Everything else must be gone; a new one here is a regression."""
    allowed = {"scripts/build_i0_4_contract_set_lock.py", "scripts/lib/utctime.py"}
    chain = re.compile(r"datetime\.now\(timezone\.utc\)\.replace\(microsecond=0\)\.isoformat\(\)")
    strf = re.compile(r'strftime\(\s*["\']%Y-%m-%dT%H:%M:%SZ["\']\s*\)')
    offenders = []
    for path in sorted((ROOT / "scripts").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if rel in allowed:
            continue
        text = path.read_text(encoding="utf-8")
        if chain.search(text) or strf.search(text):
            offenders.append(rel)
    assert not offenders, f"these hand-roll the UTC stamp again: {offenders}"

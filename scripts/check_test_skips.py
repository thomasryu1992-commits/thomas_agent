#!/usr/bin/env python3
"""Fail a green test run that quietly stopped testing things.

A skipped test and a passing test look the same in `pytest -q`'s final line unless you read the
count, and nothing reads the count. That is not hypothetical here: the suite has **208 tests
gated on a local Core activation** (`tests/_helpers.requires_local_core`), and both CI workflows
run `scripts/ci_activate_core_for_tests.py` first precisely so those run. Measured on this
repository at 3c31bf8: with the activation, `4383 passed, 2 skipped`; without it, `4175 passed,
210 skipped`. Both are green. If that activation step ever stops taking effect — a moved
pointer path, a changed default, an exception swallowed by a shell that keeps going — CI reports
success over a suite missing a twentieth of its coverage, and the signal that it happened is a
number nobody looks at.

Two checks, and they are deliberately different in kind:

1. **Forbidden reasons — absolute, no baseline.** Some skips are *impossible* in a correctly
   provisioned CI run, and "no local Core activation" is one: the workflow activates a Core
   before pytest, so a test skipping for want of one means the activation did not take. This
   needs no recorded number, cannot drift, and is not affected by which OS or interpreter the
   job ran on. It is the check that actually catches the failure above.

2. **A total ceiling — relative, per platform, opt-in.** Catches the smaller version: a skip
   added and forgotten. It needs a measured baseline per platform, and a *measured* one:
   the two skips seen locally are `root can chown to any gid; the refusal cannot be provoked`,
   which a CI runner (non-root) will not skip at all. A number guessed from a developer's
   machine would be wrong in both directions, so an unrecorded platform reports and passes
   rather than inventing one. `tests/skip_ceiling.json` says how to record it.

Usage:

    python -m pytest tests/ -q --junitxml=junit.xml
    python scripts/check_test_skips.py --junit junit.xml
"""

from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DEFAULT_JUNIT = ROOT / "junit.xml"
DEFAULT_CEILINGS = ROOT / "tests" / "skip_ceiling.json"

# Named node ids per forbidden reason, so the failure is identifiable without being a wall.
_EXAMPLES_PER_REASON = 5

# The ceiling failure lists every skip, which is bounded by the ceiling being small. If a run
# ever exceeds this it is the forbidden-reason check's kind of problem, not the ceiling's.
_MAX_LISTED_SKIPS = 40

# Skip reasons that mean the environment is wrong, not that the test does not apply here. Match
# is on the reason text pytest records, which for a `skipif` is the `reason=` argument verbatim.
FORBIDDEN_REASONS: dict[str, str] = {
    "no local Core activation": (
        "tests/_helpers.requires_local_core gates 208 tests on a local Core activation, and "
        "the workflow runs scripts/ci_activate_core_for_tests.py before pytest so they run. "
        "One of them skipping means that step did not take effect"
    ),
}


def _platform_key() -> str:
    """`linux` / `win32` / `darwin` — `sys.platform`, so a developer and CI agree on the name."""
    return sys.platform


def collect_skips(junit_path: Path) -> list[tuple[str, str]]:
    """Every skipped test in the report as ``(node_id, reason)``. Fail-closed on a bad file."""
    tree = ET.parse(junit_path)
    skips: list[tuple[str, str]] = []
    for case in tree.iter("testcase"):
        for skipped in case.findall("skipped"):
            node = f"{case.get('classname', '')}::{case.get('name', '')}"
            skips.append((node, skipped.get("message") or ""))
    return skips


def load_ceiling(ceilings_path: Path, platform: str) -> int | None:
    """The recorded ceiling for ``platform``, or ``None`` when none has been measured."""
    if not ceilings_path.is_file():
        return None
    data = json.loads(ceilings_path.read_text(encoding="utf-8"))
    value = (data.get("ceilings") or {}).get(platform)
    return value if isinstance(value, int) else None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fail a test run whose skips hide missing coverage.",
    )
    parser.add_argument("--junit", type=Path, default=DEFAULT_JUNIT,
                        help="pytest --junitxml report to read (default: junit.xml)")
    parser.add_argument("--ceilings", type=Path, default=DEFAULT_CEILINGS,
                        help="recorded per-platform skip ceilings (default: tests/skip_ceiling.json)")
    parser.add_argument("--platform", default=_platform_key(),
                        help="platform key to check against (default: sys.platform)")
    args = parser.parse_args()

    if not args.junit.is_file():
        print(f"FAIL: no junit report at {args.junit}")
        print("The pytest step must run with --junitxml for this check to have anything to read.")
        return 1

    skips = collect_skips(args.junit)
    total = len(skips)
    print(f"Skipped tests on {args.platform}: {total}")

    forbidden = [(node, reason) for node, reason in skips if reason.strip() in FORBIDDEN_REASONS]
    if forbidden:
        print("FAIL: tests skipped for a reason that cannot be legitimate in CI")
        # Grouped, with a handful of examples per reason. This fires on a whole gate coming
        # loose — 208 tests at a time — and 208 near-identical lines bury the sentence that
        # says what to do about it.
        by_reason: dict[str, list[str]] = {}
        for node, reason in forbidden:
            by_reason.setdefault(reason.strip(), []).append(node)
        for reason, nodes in sorted(by_reason.items()):
            print(f" - {len(nodes)} test(s) skipped: {reason!r}")
            print(f"   why this is a failure: {FORBIDDEN_REASONS[reason]}")
            for node in nodes[:_EXAMPLES_PER_REASON]:
                print(f"   e.g. {node}")
            if len(nodes) > _EXAMPLES_PER_REASON:
                print(f"   ... and {len(nodes) - _EXAMPLES_PER_REASON} more")
        print(f"({len(forbidden)} of {total} skips)")
        return 1

    ceiling = load_ceiling(args.ceilings, args.platform)
    if ceiling is None:
        print(f"PASS: no ceiling recorded for {args.platform!r}; reporting only")
        print(f"To enforce it, set ceilings.{args.platform} to {total} in {args.ceilings.name} "
              f"— from a CI run, never from a developer machine (see that file).")
        return 0

    if total > ceiling:
        print(f"FAIL: {total} skipped tests on {args.platform}, ceiling is {ceiling}")
        print("Skips in this run:")
        for node, reason in skips[:_MAX_LISTED_SKIPS]:
            print(f" - {node} :: {reason}")
        if total > _MAX_LISTED_SKIPS:
            print(f" ... and {total - _MAX_LISTED_SKIPS} more")
        print("If the new skips are intended, raise the ceiling in the same commit that adds "
              "them, so the raise is reviewed with its cause.")
        return 1

    if total < ceiling:
        print(f"PASS: {total} skipped tests on {args.platform}, under the ceiling of {ceiling}")
        print(f"The ceiling can be lowered to {total}; a ceiling above the truth stops catching "
              f"the next skip.")
        return 0

    print(f"PASS: {total} skipped tests on {args.platform}, at the recorded ceiling")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Read-only check: did a disabled factory tier actually stop minting?

    docker exec thomas-scheduler python -m scripts.verify_factory_tier_freeze

Writes nothing and decides nothing. It answers one question an operator has after
disabling ``crypto_factory`` schedules — *did the freeze take?* — and it exists because
the obvious way to answer it is wrong in a way that reads like success.

**Zero mints is not evidence of a freeze until the fire window has passed.** The tiers
share due times (the 1h/4h block at ~08:09Z, the 1d block at ~03:35Z), so a frozen tier
and a tier whose fire is simply still hours away produce the identical observation: no
candidates today. Checking at 05:00Z and reading "1h minted nothing" as PASS confirms
nothing at all. So a frozen tier is graded against **its own stale ``next_run_at``** —
the occurrence it would have run at — and reads UNPROVEN until that moment is in the
past. Same posture ``robustness.holdout_status`` takes toward a missing holdout block:
absence of evidence is not evidence.

``next_run_at`` rather than ``last_run_at`` for the same reason, one step further on:
disabling a schedule freezes both stamps, so ``last_run_at`` never advances again and a
tier frozen last week could never prove anything on any later day. Its ``next_run_at``
still names the occurrence the freeze cancelled, which is exactly the occurrence to
grade — and it dates the check automatically, so no ``--day`` guessing.

Enabled tiers carry no verdict. There is nothing to verify about a tier that is supposed
to be minting; they are printed so the freeze is read against the whole rotation rather
than a single line of it.

Why the freeze matters enough to verify: ``pool.attempts_by_context`` counts every
distinct candidate per ``(symbol_scope, timeframe)`` at READ time and never decays, so
each mint raises ``robustness.selection_adjusted_z`` for every sibling in that context.
A tier that was disabled but kept firing would go on charging that price silently —
``docs/TRADING_STRATEGY_REVIEW_RECORD.md`` (2026-08-06) carries the measurement and the
decision. The state it verifies lives in ``.runtime_governance_state/schedules.jsonl``,
which is per-machine and never committed, so nothing in the repo can be read instead.

Both stores are read through their own authorities (``scheduler.ScheduleStore`` and
``pool.read_candidates``) rather than re-parsed here.

Exit codes: ``0`` nothing contradicts the freeze (including UNPROVEN tiers), ``1`` a
frozen tier minted anyway, ``2`` the stores could not be read.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping

from runtime.mvp_runtime import scheduler, timeutil
from runtime.mvp_runtime.crypto import pool
from runtime.mvp_runtime.errors import ToolError

FACTORY_KIND = scheduler.KIND_FACTORY

ACTIVE = "active"
PASS = "PASS"
FAIL = "FAIL"
UNPROVEN = "UNPROVEN"


def tier_of(request: str) -> str | None:
    """The timeframe a ``crypto_factory`` schedule mines, from its ``"<SYMBOL> <TF>"``
    request. ``None`` for the legacy context-free schedule, which mines a default rather
    than a named tier and so cannot be graded against one."""
    parts = str(request or "").split()
    return parts[-1] if len(parts) >= 2 else None


def group_schedules(schedules: list[Any]) -> dict[str, list[Any]]:
    grouped: dict[str, list[Any]] = defaultdict(list)
    for schedule in schedules:
        if schedule.kind != FACTORY_KIND:
            continue
        tier = tier_of(schedule.request)
        if tier is not None:
            grouped[tier].append(schedule)
    return dict(grouped)


def mints_on(records: list[Mapping[str, Any]], *, tier: str, day: str) -> int:
    """How many candidates this tier minted on ``day`` (UTC ``YYYY-MM-DD``)."""
    total = 0
    for record in records:
        created = record.get("created_at_utc")
        if not isinstance(created, str) or not created.startswith(day):
            continue
        spec = record.get("strategy_spec")
        if isinstance(spec, Mapping) and spec.get("timeframe") == tier:
            total += 1
    return total


def cancelled_occurrence(schedules: list[Any]) -> str | None:
    """The occurrence a frozen tier would have run at — the latest ``next_run_at`` it
    still carries. Latest rather than earliest: the schedules in a tier are disabled
    within seconds of each other but stamp slightly different times, and grading against
    the last of them is the one that cannot call a window passed while a sibling's is
    still ahead."""
    stamps = [s.next_run_at for s in schedules if isinstance(s.next_run_at, str) and s.next_run_at]
    return max(stamps) if stamps else None


def grade_tier(schedules: list[Any], records: list[Mapping[str, Any]], *, tier: str, now: str
               ) -> tuple[str, str, int]:
    """``(verdict, why, minted)`` for one tier."""
    enabled = [s for s in schedules if s.enabled]
    if enabled:
        today = now[:10]
        return ACTIVE, f"{len(enabled)}/{len(schedules)} enabled", mints_on(records, tier=tier, day=today)

    occurrence = cancelled_occurrence(schedules)
    if occurrence is None:
        return UNPROVEN, "frozen, but no schedule carries a next_run_at to grade against", 0
    if timeutil.parse_iso(occurrence) > timeutil.parse_iso(now):
        return UNPROVEN, f"frozen; its cancelled occurrence {occurrence} has not passed yet", 0

    day = occurrence[:10]
    minted = mints_on(records, tier=tier, day=day)
    if minted:
        return FAIL, f"frozen, yet minted {minted} on {day} (occurrence {occurrence})", minted
    return PASS, f"frozen, and minted 0 on {day} — its occurrence {occurrence} passed", 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--root", default=None, type=Path, help="repo root override")
    parser.add_argument("--now", default=None, metavar="ISO8601",
                        help="treat this as the current time (testing)")
    args = parser.parse_args(argv)

    now = args.now or timeutil.utc_now_iso()
    try:
        schedules = scheduler.ScheduleStore.default(args.root).list()
        records = pool.read_candidates(args.root)
    except ToolError as exc:
        print(f"cannot read the stores: {exc.reason_code}: {exc}", file=sys.stderr)
        return 2

    grouped = group_schedules(schedules)
    if not grouped:
        print(f"no {FACTORY_KIND} schedules carry a context — nothing to grade.")
        return 0

    print(f"factory tier freeze — now {now}\n")
    print(f"  {'tier':>5} {'enabled':>9} {'minted':>7}  verdict")
    graded = {}
    for tier in sorted(grouped, key=lambda t: (len(t), t)):
        verdict, why, minted = grade_tier(grouped[tier], records, tier=tier, now=now)
        graded[tier] = verdict
        enabled = sum(1 for s in grouped[tier] if s.enabled)
        print(f"  {tier:>5} {f'{enabled}/{len(grouped[tier])}':>9} {minted:>7}  {verdict} — {why}")

    unproven = sorted(t for t, v in graded.items() if v == UNPROVEN)
    if unproven:
        # Said out loud because the table's zeros look like the answer. They are not.
        print(f"\n{len(unproven)} tier(s) UNPROVEN ({', '.join(unproven)}): frozen, but the "
              f"occurrence the freeze cancelled has not passed. Re-run after it does — a zero "
              f"before the window is indistinguishable from a zero because of the freeze.")
    if FAIL in graded.values():
        print("\nA frozen tier minted anyway. Check whether the schedule was re-enabled "
              "(`scheduler_cli list`) before assuming the scheduler is at fault.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

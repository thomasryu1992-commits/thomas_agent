#!/usr/bin/env python3
"""Operator tool: where the hypothesis channel's proposals fall — counts only, no returns.

The model proposes a rule, deterministic code judges it twice, and a rule that passes both becomes a
trial walked forward. Each stage records why it turned a proposal down, and until now nothing printed
those reasons side by side:

- **the proposer** (``crypto/proposer.py`` ``evaluate_proposal``): a proposal is rejected as ``parse``,
  ``timeframe``, ``form`` or ``validator`` before it is ever an acceptance;
- **the trial screen** (``crypto/factory.py`` ``_screen_trials``, on a cohort fire): an acceptance is
  refused as ``known_family``, ``duplicate_rule_hash``, ``unsuppliable_feature``, a lattice refusal,
  ``scoring_error``, ``no_trades``, ``pace_unreadable`` or ``too_slow``, or it is minted as a trial.

This reads both record kinds from the ledger and prints, for a window of time: how many proposals each
stage saw, how many it passed, and the refusals by reason, by family and by day.

**What it never prints.** Both record kinds carry numbers a search could fit to (expectancy, scores,
candidate evidence) and model-written text (rationale, hypothesis, exception detail). The output is
built from a closed set of keys: counts, reason names, family names, days and statuses, each held to a
shape or counted under one fixed label. Nothing else leaves the row it was read from.

Reads only, so it needs no host-root guard and writes nothing. It reads the ledger under the ledger's
own lock, so run it where the runtime's state is mounted::

    docker exec thomas-scheduler python -m scripts.trial_refusals
    docker exec thomas-scheduler python -m scripts.trial_refusals --since 2026-11-13
    docker exec thomas-scheduler python -m scripts.trial_refusals --days 7 --json

A damaged ledger refuses the run (``EXIT_BLOCKED``).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime import timeutil  # noqa: E402
from runtime.mvp_runtime.cli_common import EXIT_OK, EXIT_USAGE, force_utf8_io, report_block  # noqa: E402
from runtime.mvp_runtime.crypto.proposer import (  # noqa: E402
    FACTORY_LEDGER_KIND,
    PROPOSAL_LEDGER_KIND,
    REFUSAL_FAMILY_SHAPE,
    REFUSAL_REASON_SHAPE,
)
from runtime.mvp_runtime.errors import MvpRuntimeError  # noqa: E402
from runtime.mvp_runtime.store import LedgerStore  # noqa: E402

DEFAULT_DAYS = 30                           # the proposer's own backlog window
OTHER = "(other)"                           # a name that does not fit its shape is counted here, never printed
NOT_ASKED = "(not asked)"                   # a factory fire with no trial block: a single-symbol fire screens nothing
STATUS_SHAPE = re.compile(r"^[a-z][a-z_:]{0,39}$")
TIMEFRAME_SHAPE = re.compile(r"^[0-9]{1,2}[mhdw]$")
VERSION_SHAPE = re.compile(r"^[A-Za-z0-9_.-]{1,24}$")
DAY_SHAPE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
ISO_SHAPE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")


def _held(value: Any, shape: re.Pattern[str]) -> str:
    return value if isinstance(value, str) and shape.match(value) else OTHER


def _bump(counts: dict[str, int], key: str, by: int = 1) -> None:
    counts[key] = counts.get(key, 0) + by


def _reason(entry: Mapping[str, Any], field: str) -> str:
    """The reason's name. A proposer rejection may read ``parse: <exception text>``; only the name is kept."""
    raw = entry.get(field)
    return _held(raw.split(":", 1)[0].strip() if isinstance(raw, str) else raw, REFUSAL_REASON_SHAPE)


def tally(rows: Iterable[Mapping[str, Any]], *, since: str) -> dict[str, Any]:
    """Both stages' counts over the rows whose own ``created_at`` is at or after ``since``. Pure.

    ``rows`` are ledger rows (``{kind, record}``); any other kind, a row with no record and a record with
    no readable time are skipped, the last counted as ``undated``. Every value in the result is a count,
    or a name held to its shape."""
    proposer: dict[str, Any] = {"fires": 0, "proposed": 0, "accepted": 0, "rejected": 0, "rejected_by_reason": {},
                                "by_timeframe": {}, "by_prompt_version": {}, "first": None, "last": None}
    screen: dict[str, Any] = {"fires": 0, "fires_by_status": {}, "screened": 0, "minted": 0, "refused": 0,
                              "refused_by_reason": {}, "refused_by_family": {}, "minted_by_family": {},
                              "first": None, "last": None}
    days: dict[str, dict[str, int]] = {}
    undated = 0

    def day_of(created: str) -> dict[str, int]:
        return days.setdefault(_held(created[:10], DAY_SHAPE), {
            "proposed": 0, "accepted": 0, "screened": 0, "minted": 0, "refused": 0})

    def span(block: dict[str, Any], created: str) -> None:
        block["first"] = created if block["first"] is None or created < block["first"] else block["first"]
        block["last"] = created if block["last"] is None or created > block["last"] else block["last"]

    for row in rows:
        if not isinstance(row, Mapping) or row.get("kind") not in (PROPOSAL_LEDGER_KIND, FACTORY_LEDGER_KIND):
            continue
        record = row.get("record")
        if not isinstance(record, Mapping):
            continue
        created = record.get("created_at")
        if not (isinstance(created, str) and ISO_SHAPE.match(created)):
            undated += 1
            continue
        if created < since:
            continue
        day = day_of(created)
        if row["kind"] == PROPOSAL_LEDGER_KIND:
            proposer["fires"] += 1
            span(proposer, created)
            _bump(proposer["by_timeframe"], _held(record.get("timeframe"), TIMEFRAME_SHAPE))
            _bump(proposer["by_prompt_version"], _held(record.get("prompt_version"), VERSION_SHAPE))
            for proposal in record.get("proposals") or []:
                if not isinstance(proposal, Mapping):
                    continue
                proposer["proposed"] += 1
                day["proposed"] += 1
                if proposal.get("accepted") is True:
                    proposer["accepted"] += 1
                    day["accepted"] += 1
                else:
                    proposer["rejected"] += 1
                    _bump(proposer["rejected_by_reason"], _reason(proposal, "reject_reason"))
            continue
        screen["fires"] += 1
        span(screen, created)
        trial = record.get("trial")
        if not isinstance(trial, Mapping):
            _bump(screen["fires_by_status"], NOT_ASKED)
            continue
        _bump(screen["fires_by_status"], _held(trial.get("status"), STATUS_SHAPE))
        for entry in trial.get("minted") or []:
            if isinstance(entry, Mapping):
                screen["screened"] += 1
                screen["minted"] += 1
                day["screened"] += 1
                day["minted"] += 1
                _bump(screen["minted_by_family"], _held(entry.get("family"), REFUSAL_FAMILY_SHAPE))
        for entry in trial.get("refused") or []:
            if isinstance(entry, Mapping):
                reason = _reason(entry, "reason")
                screen["screened"] += 1
                screen["refused"] += 1
                day["screened"] += 1
                day["refused"] += 1
                _bump(screen["refused_by_reason"], reason)
                family = screen["refused_by_family"].setdefault(_held(entry.get("family"), REFUSAL_FAMILY_SHAPE), {})
                _bump(family, reason)
    return {"since": since, "proposer": proposer, "trial_screen": screen,
            "by_day": {day: days[day] for day in sorted(days)}, "undated_rows": undated}


def _ranked(counts: Mapping[str, int]) -> str:
    return " · ".join(f"{name} {n}" for name, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))) or "없음"


def render(result: Mapping[str, Any]) -> str:
    proposer, screen = result["proposer"], result["trial_screen"]
    lines = [
        "가설 통로: 제안이 어디서 떨어지는가 (수만 센다. 수익·점수는 읽지 않는다)",
        f"기간: {result['since']} 이후",
        "",
        f"[1] 제안 단계 — 제안 발화 {proposer['fires']}회"
        + (f" ({proposer['first']} – {proposer['last']})" if proposer["fires"] else ""),
        f"    제안 {proposer['proposed']}개 → 받아들임 {proposer['accepted']}개, 거절 {proposer['rejected']}개",
        f"    거절 사유: {_ranked(proposer['rejected_by_reason'])}",
        f"    주기: {_ranked(proposer['by_timeframe'])}   프롬프트: {_ranked(proposer['by_prompt_version'])}",
        "",
        f"[2] 트라이얼 심사 — factory 발화 {screen['fires']}회"
        + (f" ({screen['first']} – {screen['last']})" if screen["fires"] else ""),
        f"    발화의 상태: {_ranked(screen['fires_by_status'])}",
        f"    심사한 제안 {screen['screened']}개 → 트라이얼 {screen['minted']}개, 거절 {screen['refused']}개",
        f"    거절 사유: {_ranked(screen['refused_by_reason'])}",
    ]
    if screen["refused_by_family"]:
        lines.append("    계열별 거절:")
        lines += [f"      {family}: {_ranked(reasons)}" for family, reasons in sorted(screen["refused_by_family"].items())]
    if screen["minted_by_family"]:
        lines.append(f"    트라이얼이 된 계열: {_ranked(screen['minted_by_family'])}")
    lines += ["", "[3] 날짜별 (UTC) — 제안 / 받아들임 · 심사 / 트라이얼 / 거절"]
    lines += [f"    {day}  {c['proposed']} / {c['accepted']} · {c['screened']} / {c['minted']} / {c['refused']}"
              for day, c in result["by_day"].items()] or ["    없음"]
    if result["undated_rows"]:
        lines.append(f"\n시각을 읽을 수 없어 뺀 기록: {result['undated_rows']}개")
    lines.append(f"\n'{OTHER}'는 이름이 정해진 모양에 맞지 않아 그대로 찍지 않은 것이다.")
    return "\n".join(lines)


def _rows(root: Path, since: str) -> Iterable[Mapping[str, Any]]:
    """The two kinds this reads, archives included. ``appended_since`` bounds which files are opened; the
    rows are still filtered on their own time in :func:`tally`."""
    return LedgerStore.default(root).iter_records_with_archive(
        appended_since=since, kinds=[PROPOSAL_LEDGER_KIND, FACTORY_LEDGER_KIND])


def _since(args: argparse.Namespace, now: str) -> str | None:
    if args.since is None:
        return timeutil.plus_minutes(now, -args.days * 24 * 60) if args.days > 0 else None
    stamp = args.since if "T" in args.since else f"{args.since}T00:00:00Z"
    return stamp if ISO_SHAPE.match(stamp) else None


def main(argv: list[str] | None = None, *, root: Path | None = None, now: str | None = None) -> int:
    force_utf8_io()
    parser = argparse.ArgumentParser(prog="trial_refusals", description=__doc__.splitlines()[0])
    window = parser.add_mutually_exclusive_group()
    window.add_argument("--days", type=int, default=DEFAULT_DAYS, help=f"how far back to read (default {DEFAULT_DAYS})")
    window.add_argument("--since", default=None, help="read from this UTC day or instant on: 2026-11-13 or 2026-11-13T03:11:00Z")
    parser.add_argument("--json", action="store_true", help="print the tally as JSON")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:                                   # argparse exits 2, which is this CLI's BLOCKED
        return EXIT_OK if exc.code == 0 else EXIT_USAGE
    since = _since(args, now if now is not None else timeutil.utc_now_iso())
    if since is None:
        print("usage: --days takes a positive number; --since takes 2026-11-13 or 2026-11-13T03:11:00Z", file=sys.stderr)
        return EXIT_USAGE
    try:
        result = tally(_rows(root if root is not None else ROOT, since), since=since)
    except MvpRuntimeError as exc:
        return report_block(exc)
    print(json.dumps(result, ensure_ascii=False, indent=1, sort_keys=True) if args.json else render(result))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())

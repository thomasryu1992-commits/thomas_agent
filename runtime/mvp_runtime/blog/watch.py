"""The outsourced-side watch (BLOG_DEPARTMENT v0.2, P6 prerequisite, Thomas 2026-11-05 "기본값대로").

After P6 the drafting cron writes nothing without an instruction, and the system must know the days the outsourced
side stopped (v0.2 §3). The host script built for that, ``mac-routine-watch.py``, was never scheduled, and it reads
git history and host logs the container does not have. This module judges the same outsourced work from the vault
files mounted read-only, with the script's own rules generated into ``blog/watch_rules.py``:

* ``RUN_REPORT``: the drafting cron's run report (``ops/blog-runs/<KST day>.json``, P1) for the last day past its
  deadline (``DAILY_DEADLINE``, 12:00 KST): ``missing``, ``unreadable``, ``draft_failed`` (``steps.draft`` is not 0)
  or ``skipped`` (after P6, a run with no instruction);
* ``MAC_DRAFT_STALE`` and ``MAC_TREND_STALE``: the Mac's draft-saving routine (``ops/naver-draft/daily.log``) and
  trend routine (``analytics/naver-trend/run.log``). The last run block that ended ``exit 0`` must be no older than
  ``ROUTINE_MAX_H``. The script measures that from the commit the block arrived in; without git history this reads
  the block's own header as KST. The Mac's clock is UTC+7 or KST, so the age is at most two hours too old, never too
  young — on a 36-hour limit, an early warning, never a late one;
* ``TREND_SEEDS_EMPTY``: ``analytics/keywords/seeds_trend.txt`` has no candidate line. ``unknown`` while the trend
  routine itself is not ok, as in the script, so one cause is not reported twice;
* ``WEEKLY_MISSING``: from Monday 21:00 KST (``WEEKLY_DEADLINE``), the week's Naver statistics note and snapshot, and
  the snapshot's "## 해석과 결정" filled beyond the template (``TEMPLATE_LINE``).

Each check is ``ok``, ``problem`` or ``unknown`` (a folder not mounted), with stable reason codes. The host-side
checks of the script — vault fetch and unpushed commits, the Hermes send log, the weekly keyword queue's host log —
are not outsourced work and stay with the host.

**Shadow (Thomas 2026-11-05: "기록만, 알림은 P6에서").** A fire records ``blog_watch`` when the set of states differs
from the last record, or once a KST day; it notifies nobody. Turning the notice on is part of P6. Records carry check
codes, reason codes and dates, never a path's content, a keyword or a title.
"""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from . import watch_rules as rules

RECORD_KIND = "blog_watch"
RECORD_SCHEMA = "schemas/blog_watch.v0.1.schema.json"
RECORD_SCHEMA_VERSION = "blog_watch.v0.1"
CHECKS = ("RUN_REPORT", "MAC_DRAFT_STALE", "MAC_TREND_STALE", "TREND_SEEDS_EMPTY", "WEEKLY_MISSING")
SEEDS = ("analytics", "keywords", "seeds_trend.txt")
_HEADER_TIME = re.compile(r"^=== (\d{4}-\d{2}-\d{2} \d{2}:\d{2}) ===$")
_SNAPSHOT_SECTION = re.compile(r"^## 해석과 결정[^\n]*\n(.*?)(?=^## |\Z)", re.M | re.S)


def _result(state: str, reasons: list[str] | None = None, subject: str | None = None) -> dict[str, Any]:
    return {"state": state, "reasons": reasons or [], "subject": subject}


def check_report(root: Path, now: datetime) -> dict[str, Any]:
    """The run report of the last KST day past the drafting cron's deadline."""
    from .. import blog_department

    day = (now.astimezone(rules.KST) - rules.DAILY_DEADLINE).date().isoformat()
    folder = root.joinpath(*blog_department.RUNS_DIR)
    if not folder.is_dir():
        return _result("unknown", ["unmounted"], day)
    path = folder / f"{day}.json"
    if not path.is_file():
        return _result("problem", ["missing"], day)
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return _result("problem", ["unreadable"], day)
    if not (isinstance(report, Mapping) and report.get("schema") == blog_department.RUN_REPORT_SCHEMA):
        return _result("problem", ["unreadable"], day)
    steps = report.get("steps") if isinstance(report.get("steps"), Mapping) else {}
    reasons = (["draft_failed"] if steps.get("draft") != 0 else []) + (["skipped"] if report.get("skipped") else [])
    return _result("problem" if reasons else "ok", reasons, day)


def check_routine(root: Path, code: str, now: datetime) -> dict[str, Any]:
    """One Mac routine: its last run block that ended ``exit 0``, by the block's header read as KST."""
    path = root / rules.ROUTINES[code][0]
    if not path.parent.is_dir():
        return _result("unknown", ["unmounted"])
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return _result("problem", ["no_log"])
    blocks = rules.run_blocks(text)
    ok = [i for i, (_, rc) in enumerate(blocks) if rc == 0]
    if not ok:
        return _result("problem", ["no_success"])
    when = datetime.strptime(_HEADER_TIME.match(blocks[ok[-1]][0]).group(1), "%Y-%m-%d %H:%M").replace(tzinfo=rules.KST)
    subject = when.date().isoformat()
    if now - when <= timedelta(hours=rules.ROUTINE_MAX_H):
        return _result("ok", [], subject)
    later = [rc for _, rc in blocks[ok[-1] + 1:]]
    reasons = ["stale"] + (["failed_since"] if any(rc not in (None, 0) for rc in later) else []) \
        + (["cut_since"] if any(rc is None for rc in later) else [])
    return _result("problem", reasons, subject)


def check_seeds(root: Path, trend: Mapping[str, Any]) -> dict[str, Any]:
    if trend["state"] != "ok":
        return _result("unknown", ["trend_not_ok"])
    path = root.joinpath(*SEEDS)
    if not path.parent.is_dir():
        return _result("unknown", ["unmounted"])
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return _result("problem", ["missing"])
    n = sum(1 for line in lines if line.strip() and not line.lstrip().startswith("#"))
    return _result("ok" if n else "problem", [] if n else ["empty"])


def check_weekly(root: Path, now: datetime) -> dict[str, Any]:
    """The last week past Monday 21:00 KST: the statistics note, the snapshot, and the snapshot's interpretation."""
    sunday = rules.last_sunday((now.astimezone(rules.KST) - rules.WEEKLY_DEADLINE).date()).isoformat()
    folder = root / "analytics"
    if not folder.is_dir():
        return _result("unknown", ["unmounted"], sunday)
    # The Mac commits Hangul names decomposed (NFD); compare them composed, as the script does.
    names = {unicodedata.normalize("NFC", p.name): p for p in folder.iterdir()}
    reasons = []
    if f"네이버-주간통계-{sunday}.md" not in names:
        reasons.append("stats_missing")
    snapshot = names.get(f"{sunday}.md")
    if snapshot is None:
        reasons.append("snapshot_missing")
    else:
        m = _SNAPSHOT_SECTION.search(snapshot.read_text(encoding="utf-8", errors="replace"))
        filled = m and any(line.strip() and not rules.TEMPLATE_LINE.match(line.strip())
                           for line in m.group(1).splitlines())
        if not filled:
            reasons.append("unfilled")
    return _result("problem" if reasons else "ok", reasons, sunday)


def judge(root: Path, now: datetime) -> dict[str, dict[str, Any]]:
    trend = check_routine(root, "MAC_TREND_STALE", now)
    return {"RUN_REPORT": check_report(root, now), "MAC_DRAFT_STALE": check_routine(root, "MAC_DRAFT_STALE", now),
            "MAC_TREND_STALE": trend, "TREND_SEEDS_EMPTY": check_seeds(root, trend),
            "WEEKLY_MISSING": check_weekly(root, now)}


def build_record(checks: Mapping[str, Mapping[str, Any]], previous: Mapping[str, Any] | None, *,
                 now: str) -> dict[str, Any]:
    from runtime.read_only_kernel import integrity

    from .. import schema_cache
    from ..paths import repo_root

    problems = [code for code in CHECKS if checks[code]["state"] == "problem"]
    before = set((previous or {}).get("problems") or [])
    record = {
        "schema_version": RECORD_SCHEMA_VERSION,
        "watch_id": integrity.short_id("blogwatch", {"at": now, "checks": checks}),
        "recorded_at": now,
        "mode": "shadow",
        "checks": {code: dict(checks[code]) for code in CHECKS},
        "problems": problems,
        "new": [code for code in problems if code not in before],
        "resolved": [code for code in CHECKS if code in before and code not in problems],
    }
    schema_cache.validate_against_schema(record, repo_root() / RECORD_SCHEMA, RECORD_KIND)
    return record


def _states(record: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not record:
        return None
    return {code: (c.get("state"), tuple(c.get("reasons") or []), c.get("subject"))
            for code, c in (record.get("checks") or {}).items()}


def fire(ledger: Any, *, now: str, vault_root: Path | None = None) -> str:
    """Judge the outsourced side; record when anything changed since the last record, or once a KST day. An absent
    vault raises (``BLOG_VAULT_UNAVAILABLE``) so the fire is a failure."""
    from .. import blog_department, timeutil

    root = blog_department.vault_root(vault_root)
    at = timeutil.parse_iso(now).astimezone(timezone.utc)
    checks = judge(root, at)
    last = None
    for record in blog_department.records(ledger, RECORD_KIND):
        last = record
    today = at.astimezone(rules.KST).date()
    same_day = last is not None and timeutil.parse_iso(last["recorded_at"]).astimezone(rules.KST).date() == today
    summary = ",".join(f"{c}:{checks[c]['state']}" for c in CHECKS if checks[c]["state"] != "ok") or "all_ok"
    if same_day and _states(last) == _states({"checks": checks}):
        return f"blog_watch_unchanged({summary})"
    record = build_record(checks, last, now=now)
    ledger.append_records(record["watch_id"], {RECORD_KIND: record})
    return f"blog_watch_recorded({summary})"

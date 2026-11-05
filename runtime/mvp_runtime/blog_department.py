"""The blog department, observed: what the vault's post folders say happened this week. Reads only.

BLOG_DEPARTMENT_V0.1.md stages 1 and 2 (Thomas 2026-10-26). Blog production runs outside the runtime: the
claude cron drafts into the vault, the Mac and an SSH session publish. The organisation does not run it; it
reads what it did, once a week, from the one place every step leaves a mark — each post's front matter in
``content/naver/`` and ``content/tistory/`` — and reports and records that:

* **status now** — ``draft`` / ``hold`` / ``scheduled`` / ``published`` per platform, as the files say;
* **new drafts** — posts whose ``created:`` date falls in the window;
* **published** — ``status: published`` posts whose ``published:`` date falls in the window;
* **upcoming** — unpublished posts whose ``scheduled:`` slot falls in the next seven days. On Naver that slot is
  the plan (the post stays ``draft`` until it goes out); on Tistory a ``scheduled`` status is a real reservation.
  Either way it is called what it is, never "published".
* **runs** (v0.2 P1, Thomas 2026-10-26) — the drafting cron is outsourced work, and it reports each run as
  ``ops/blog-runs/<KST date>.json`` (``blog-run-report.py`` on the host). The week counts the reports dated in the
  window: how many days reported, how many drafts they name, how many of those the cron's own re-run of
  ``blog-preflight`` / ``blog_variety`` failed or could not run, and how many runs consumed a system instruction
  (none until P6). A report folder that is not mounted is ``None`` — absent, never zero. A report that does not
  parse as ``blog_run_report.v1`` is counted as unreadable and contributes nothing else.
* **stages** (v0.3; v0.4 the P4a/P4b run) — each stage's progress towards its switch condition (§4), from the records its own fire
  writes: P2's comparison and P3's agreeing weeks as the message already showed them, and the shadows P4a–P7 (the
  calendar span of the newest run of agreeing bundle days, since v0.4; the feedback days, where the reported drafts sat in the instruction, the slot
  checks). The switch is judged from these records, not from anyone's notes.

Dates are KST calendar dates, as the vault writes them. A file without readable front matter is counted as
unparsed, never guessed at; a platform folder with no posts at all refuses, like an absent one. Nothing here writes the vault, calls a model, or reaches the network; the vault
arrives as a read-only mount (``MVP_BLOG_VAULT_ROOT``).
"""

from __future__ import annotations

import json
import os
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterator, Mapping

from runtime.read_only_kernel import integrity

from . import schema_cache, timeutil
from .errors import ToolError
from .paths import repo_root as _repo_root

BLOG_VAULT_ROOT_ENV = "MVP_BLOG_VAULT_ROOT"
PLATFORMS = ("naver", "tistory")
STATUSES = ("draft", "hold", "scheduled", "published")
WINDOW_DAYS = 7
RECORD_KIND = "blog_department_week"
RECORD_SCHEMA = "schemas/blog_department_week.v0.4.schema.json"
RECORD_SCHEMA_VERSION = "blog_department_week.v0.4"
RUNS_DIR = ("ops", "blog-runs")
RUN_REPORT_SCHEMA = "blog_run_report.v1"
# Written by the lane's daily ``blog_verify`` fire (``blog/verify.py``); named here so the weekly report can read it
# without the core importing the lane.
VERIFY_RECORD_KIND = "blog_verification"
# Written by the lane's daily ``blog_analyze`` fire (``blog/analysis.py``), one per week, again when its sources change.
ANALYSIS_RECORD_KIND = "blog_analysis"
# Written by the lane's shadows (``blog/kw_replay.py``, ``kw_direct.py``, ``feedback.py``, ``instruction.py``,
# ``slot.py``); read by ``stages`` for each one's progress towards its switch condition (§4).
KW_REPLAY_RECORD_KIND = "blog_kw_replay"
KW_DIRECT_RECORD_KIND = "blog_kw_direct"
FEEDBACK_RECORD_KIND = "blog_feedback"
INSTRUCTION_RECORD_KIND = "blog_instruction"
INSTRUCTION_CHECK_RECORD_KIND = "blog_instruction_check"
SLOT_RECORD_KIND = "blog_slot"
_PICK_SOURCES = ("in_instruction", "next_topics", "queue", "tistory_list", "history_filtered", "outside", "no_keyword")
_SLOT_COUNTS = ("compared", "agreed", "differed", "moved", "not_compared", "tistory_checked", "tistory_agreed",
                "tistory_differed", "tistory_moved", "tistory_not_checked")
_KST = timedelta(hours=9)
# The department's first ledger record was written on 2026-10-26 (P0). An archive rotated before then holds none of
# its kinds, so ``records`` never opens one.
LEDGER_SINCE = "2026-10-26T00:00:00Z"
_FIELD = re.compile(r"^([a-z_]+):[ \t]*(.*)$")
_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})")
_WANTED = frozenset({"status", "created", "published", "scheduled"})


def vault_root(root: str | Path | None = None) -> Path:
    """The mounted vault root, from the argument or ``MVP_BLOG_VAULT_ROOT``. Unset is a typed refusal: an
    empty week and a vault that is not there must never read the same."""
    value = root if root is not None else os.environ.get(BLOG_VAULT_ROOT_ENV)
    if not value:
        raise ToolError("BLOG_VAULT_UNAVAILABLE", f"{BLOG_VAULT_ROOT_ENV} is not set; the vault is not mounted here")
    path = Path(value)
    missing = [p for p in PLATFORMS if not (path / "content" / p).is_dir()]
    if missing:
        raise ToolError("BLOG_VAULT_UNAVAILABLE", f"the vault at {path} has no content folder for {missing}")
    return path


def front_matter(text: str, wanted: frozenset[str] = _WANTED) -> dict[str, str] | None:
    """The ``key: value`` lines named in ``wanted`` (by default the ones this module reads), or None when the file
    has no front matter block."""
    if not text.startswith("---"):
        return None
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None
    fields: dict[str, str] = {}
    for line in parts[1].splitlines():
        match = _FIELD.match(line)
        if match and match.group(1) in wanted:
            fields[match.group(1)] = match.group(2).strip().strip('"')
    return fields


def _date(value: str | None) -> str | None:
    match = _DATE.match(value or "")
    return match.group(1) if match else None


def _kst_date(iso: str, days: int = 0) -> str:
    return (timeutil.parse_iso(iso) + _KST + timedelta(days=days)).strftime("%Y-%m-%d")


def _failed(check: Any) -> bool:
    return isinstance(check, Mapping) and (check.get("exit") not in (0, None) or bool(check.get("fail")))


def _unrun(check: Any) -> bool:
    return not isinstance(check, Mapping) or check.get("exit") is None


def read_runs(base: Path, since: str, until: str) -> dict[str, int] | None:
    """The outsourced drafting runs reported in ``[since, until)``, as counts. ``None`` when the folder is absent."""
    folder = base.joinpath(*RUNS_DIR)
    if not folder.is_dir():
        return None
    row = {"reports": 0, "unreadable": 0, "days_without_report": 0, "draft_step_failed": 0, "instructed": 0,
           "drafts": 0, "preflight_fail": 0, "variety_fail": 0, "checks_unrun": 0, "distinct_keywords": 0}
    keywords: set[str] = set()
    days: set[str] = set()
    for path in sorted(folder.glob("*.json")):
        day = _date(path.stem)
        if not day or not since <= day < until:
            continue
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            report = None
        if not (isinstance(report, Mapping) and report.get("schema") == RUN_REPORT_SCHEMA
                and isinstance(report.get("drafts"), list) and isinstance(report.get("steps"), Mapping)):
            row["unreadable"] += 1
            continue
        row["reports"] += 1
        days.add(day)
        if report["steps"].get("draft") != 0:
            row["draft_step_failed"] += 1
        if report.get("instruction_id"):
            row["instructed"] += 1
        for draft in report["drafts"]:
            if not isinstance(draft, Mapping):
                continue
            row["drafts"] += 1
            row["preflight_fail"] += _failed(draft.get("preflight"))
            row["variety_fail"] += _failed(draft.get("variety"))
            row["checks_unrun"] += _unrun(draft.get("preflight")) or _unrun(draft.get("variety"))
            if isinstance(draft.get("keyword"), str) and draft["keyword"]:
                keywords.add(draft["keyword"])
    row["days_without_report"] = WINDOW_DAYS - len(days)
    row["distinct_keywords"] = len(keywords)
    return row


def observe(root: str | Path | None = None, *, now: str) -> dict[str, Any]:
    """Each platform's counts over the window ending at ``now``: ``[since, until)`` for what happened, and
    ``[until, ahead)`` for what is planned."""
    base = vault_root(root)
    since, until, ahead = _kst_date(now, -WINDOW_DAYS), _kst_date(now), _kst_date(now, WINDOW_DAYS)
    platforms: dict[str, dict[str, Any]] = {}
    for platform in PLATFORMS:
        row = {"files": 0, "unparsed": 0, "status": {s: 0 for s in (*STATUSES, "other")},
               "new_drafts": 0, "published": 0, "upcoming": 0}
        for path in sorted((base / "content" / platform).glob("*.md")):
            row["files"] += 1
            try:
                fields = front_matter(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError):
                fields = None
            if fields is None or "status" not in fields:
                row["unparsed"] += 1
                continue
            status = fields["status"] if fields["status"] in STATUSES else "other"
            row["status"][status] += 1
            created, published, scheduled = (_date(fields.get(k)) for k in ("created", "published", "scheduled"))
            if created and since <= created < until:
                row["new_drafts"] += 1
            if status == "published" and published and since <= published < until:
                row["published"] += 1
            if status != "published" and scheduled and until <= scheduled < ahead:
                row["upcoming"] += 1
        if row["files"] == 0:
            # Each folder holds well over a hundred posts. None at all is a mount that does not reach the files
            # (or a folder this user cannot list), never a quiet week.
            raise ToolError("BLOG_VAULT_UNAVAILABLE", f"the vault at {base} has no posts under content/{platform}")
        platforms[platform] = row
    return {"since": since, "until": until, "ahead": ahead, "platforms": platforms,
            "runs": read_runs(base, since, until)}


def records(ledger: Any, kind: str) -> Iterator[Mapping[str, Any]]:
    """Every ``kind`` record, archives included, in append order. ``iter_records`` reads the active file only, and
    rotation trims that to the newest rows. A done-set or a count built from it forgets whatever rotated out, so a
    fire redoes the work and a run of days shrinks (2026-11-04: P4a replayed three bundles a second time). Every
    reader of the department's kinds, the lane's and this module's, goes through here. Consume it to the end: the
    appender's lock is held while it runs."""
    for row in ledger.iter_records_with_archive(appended_since=LEDGER_SINCE, kinds=[kind]):
        record = row.get("record") if row.get("kind") == kind else None
        if isinstance(record, Mapping):
            yield record


def verification_summary(ledger: Any, *, now: str) -> dict[str, Any]:
    """The week's ``blog_verification`` records (run dates in ``[since, until)``), summed: how many reported drafts
    the system could compare, how many agreed, and which rule codes differed most. Never carries a path."""
    since, until = _kst_date(now, -WINDOW_DAYS), _kst_date(now)
    out = {"runs": 0, "drafts": 0, "compared": 0, "agreed": 0, "differed": 0, "not_compared": 0, "codes": []}
    counter: dict[str, int] = {}
    for record in records(ledger, VERIFY_RECORD_KIND):
        if not since <= str(record.get("run_date")) < until:
            continue
        out["runs"] += 1
        for key in ("drafts", "compared", "agreed", "differed", "not_compared"):
            out[key] += int(record["summary"][key])
        for draft in record.get("drafts") or []:
            for code in draft.get("differ") or []:
                counter[code] = counter.get(code, 0) + 1
    out["codes"] = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
    return out


def analysis_summary(ledger: Any, *, now: str) -> dict[str, Any] | None:
    """The latest recorded statistics week that ended before today (KST), from the latest ``blog_analysis`` record
    for it, plus how many recorded weeks in a row, newest first, agreed with the weekly review on every number both
    had — P3's switch condition. None when no week is recorded."""
    until = _kst_date(now)
    weeks: dict[str, Mapping[str, Any]] = {}
    for record in records(ledger, ANALYSIS_RECORD_KIND):
        sunday = str((record.get("week") or {}).get("sunday") or "")
        if sunday and sunday < until:
            weeks[sunday] = record          # appended in order: the last one for a week is its latest
    if not weeks:
        return None
    ordered = sorted(weeks, reverse=True)
    latest = weeks[ordered[0]]
    in_a_row = 0
    for sunday in ordered:
        summary = weeks[sunday]["summary"]
        if not (summary["compared"] and summary["agreed"] == summary["compared"]):
            break
        if in_a_row and (date.fromisoformat(ordered[in_a_row - 1]) - date.fromisoformat(sunday)).days != 7:
            break
        in_a_row += 1
    naver, browser, capture = latest["naver"], latest.get("browser") or {}, latest.get("capture") or {}
    return {
        "week": dict(latest["week"]),
        "naver_views": naver["views"], "naver_search_inflow": naver["search_inflow"],
        "tistory_views": (browser.get("tistory") or {}).get("views"),
        "gsc_impressions": (browser.get("gsc") or {}).get("impressions"),
        "gsc_clicks": (browser.get("gsc") or {}).get("clicks"),
        # The ledger stores keys sorted; the review reads the classes answer-first.
        "capture_median": {name: capture["classes"][name]["median"] for name in ("답형", "이름만", "이동형")
                           if name in (capture.get("classes") or {})},
        "skill_row": latest["summary"]["skill_row"], "compared": latest["summary"]["compared"],
        "agreed": latest["summary"]["agreed"], "differed": list(latest["summary"]["differed"]),
        "weeks_agreeing_in_a_row": in_a_row,
    }


def _latest(ledger: Any, kind: str, key: str) -> dict[str, Mapping[str, Any]]:
    """Each ``kind`` record under its ``key`` value; appended in order, so the last one for a key is its latest
    (a re-run after a regeneration supersedes the stale one)."""
    out: dict[str, Mapping[str, Any]] = {}
    for record in records(ledger, kind):
        if record.get(key):
            out[str(record[key])] = record
    return out


# Root's keyword cron runs ``kw-pipeline run --max-age 2``: it reuses ``queue.md`` while the file is younger than two
# days, and a full run (the only kind that leaves a bundle) stamps the file when it ends, about half an hour after the
# 07:10 KST start. So a healthy cron leaves a bundle at least every third day. A longer gap is a run that never came
# and was never checked, and §4 counts only checked days, so it ends the run of agreeing days.
BUNDLE_GAP_DAYS = 3


def _agreeing_run(days: Mapping[str, bool]) -> dict[str, Any]:
    """The newest run of bundle days that all agreed, as P4a/P4b's switch condition counts it (§4: fourteen days in
    which every bundle received was compared and agreed). ``run_days`` is the calendar span from its first bundle day
    to its last, both included; ``run_bundle_days`` is how many bundle days it holds. A day with no bundle neither
    counts nor breaks the run, unless the gap to the next bundle day exceeds ``BUNDLE_GAP_DAYS``. A day that did not
    agree, or such a gap, ends it."""
    run: list[str] = []
    for day in sorted(days, reverse=True):
        if not days[day]:
            break
        if run and (date.fromisoformat(run[-1]) - date.fromisoformat(day)).days > BUNDLE_GAP_DAYS:
            break
        run.append(day)
    if not run:
        return {"run_first": None, "run_last": None, "run_days": 0, "run_bundle_days": 0}
    return {"run_first": run[-1], "run_last": run[0],
            "run_days": (date.fromisoformat(run[0]) - date.fromisoformat(run[-1])).days + 1,
            "run_bundle_days": len(run)}


def _keyword_stage(ledger: Any, kind: str, since: str, until: str) -> dict[str, Any] | None:
    """P4a (``blog_kw_replay``) or P4b (``blog_kw_direct``): each bundle's latest record. A bundle day is good when
    every bundle of that day was compared and agreed; any other state — a stale generator, a replay miss, a block, an
    error — breaks the run, as §4 counts only after a regeneration. The day is the bundle name's date, as root's cron
    wrote it."""
    bundles = _latest(ledger, kind, "bundle")
    if not bundles:
        return None
    days: dict[str, bool] = {}
    week = {"bundles": 0, "agreed": 0, "differed": 0, "stale": 0, "other": 0}
    for name, record in bundles.items():
        day = f"{name[:4]}-{name[4:6]}-{name[6:8]}"
        compared = record.get("state") == "compared"
        days[day] = days.get(day, True) and compared and record.get("agree") is True
        if since <= day < until:
            week["bundles"] += 1
            if compared:
                week["agreed" if record.get("agree") is True else "differed"] += 1
            else:
                week["stale" if record.get("state") == "generator_stale" else "other"] += 1
    return {**week, "days": len(days), "first": min(days), **_agreeing_run(days)}


def _feedback_stage(ledger: Any, since: str, until: str) -> dict[str, Any] | None:
    """P5 (``blog_feedback``): the history days with a shadow record, and what the guideline moved this week."""
    days = _latest(ledger, FEEDBACK_RECORD_KIND, "history_date")
    if not days:
        return None
    week = [record["summary"] for day, record in days.items() if since <= day < until]
    return {"days": len(days), "first": min(days), "week_days": len(week),
            "week_days_moved": sum(1 for s in week if s["rank_moved"]),
            "week_rank_moved": sum(int(s["rank_moved"]) for s in week),
            "week_adjusted": sum(int(s["adjusted"]) for s in week)}


def _instruction_stage(ledger: Any, since: str, until: str) -> dict[str, Any] | None:
    """P6 (``blog_instruction`` and ``blog_instruction_check``): the mornings an instruction was built, and where
    the week's reported drafts sat in it. ``outside`` is split by platform — it is what the switch must absorb or
    close first (the proposal's P6 row). Keywords are counted, never carried."""
    built = _latest(ledger, INSTRUCTION_RECORD_KIND, "run_date")
    checks = _latest(ledger, INSTRUCTION_CHECK_RECORD_KIND, "run_date")
    if not built and not checks:
        return None
    week_built = [r for day, r in built.items() if since <= day < until]
    week_checks = [r for day, r in checks.items() if since <= day < until]
    checked = [r for r in week_checks if r.get("state") == "checked"]
    out: dict[str, Any] = {
        "days": len(built), "first": min(built) if built else None, "week_built": len(week_built),
        "week_history_not_today": sum(1 for r in week_built if r.get("history_today") is False),
        "week_checked": len(checked), "week_no_instruction": len(week_checks) - len(checked),
        "week_late": sum(1 for r in checked if r.get("timing") == "late"),
        "drafts": sum(int(r["summary"]["drafts"]) for r in checked),
        **{source: sum(int(r["summary"][source]) for r in checked) for source in _PICK_SOURCES},
    }
    for platform in ("naver", "tistory"):
        out[f"outside_{platform}"] = sum(1 for r in checked for p in r.get("picks") or []
                                         if p.get("source") == "outside" and p.get("platform") == platform)
    return out


def _slot_stage(ledger: Any, since: str, until: str) -> dict[str, Any] | None:
    """P7 (``blog_slot``): the week's Naver comparisons and Tistory rule checks, summed. A v0.1 record (written
    before the Tistory rules) counted Tistory drafts without checking them: they count as not checked."""
    days = _latest(ledger, SLOT_RECORD_KIND, "run_date")
    if not days:
        return None
    out: dict[str, Any] = {"days": len(days), "first": min(days), "week_days": 0, **{k: 0 for k in _SLOT_COUNTS}}
    for day, record in days.items():
        if not since <= day < until:
            continue
        summary = record["summary"]
        out["week_days"] += 1
        for key in _SLOT_COUNTS:
            fallback = summary.get("tistory_not_compared", 0) if key == "tistory_not_checked" else 0
            out[key] += int(summary.get(key, fallback))
    return out


def stages(ledger: Any, *, now: str) -> dict[str, Any]:
    """Each shadow stage's progress towards its switch condition (§4), from its own records: the week ending at
    ``now`` summed, plus the days on record and, for P4a/P4b, the bundle days agreeing in a row. A stage with no
    record yet is None — never zero."""
    since, until = _kst_date(now, -WINDOW_DAYS), _kst_date(now)
    return {"kw_replay": _keyword_stage(ledger, KW_REPLAY_RECORD_KIND, since, until),
            "kw_direct": _keyword_stage(ledger, KW_DIRECT_RECORD_KIND, since, until),
            "feedback": _feedback_stage(ledger, since, until),
            "instruction": _instruction_stage(ledger, since, until),
            "slot": _slot_stage(ledger, since, until)}


def _record_stages(observation: Mapping[str, Any]) -> dict[str, Any]:
    """P2 and P3 as the weekly message reads them (counts only: the differing rule codes and the statistics stay in
    the message), beside the shadows' progress."""
    verification, analysis = observation.get("verification"), observation.get("analysis")
    return {
        "verify": None if verification is None else {
            k: verification[k] for k in ("runs", "drafts", "compared", "agreed", "differed", "not_compared")},
        "analysis": None if analysis is None else {
            "sunday": analysis["week"]["sunday"], "compared": analysis["compared"], "agreed": analysis["agreed"],
            "weeks_agreeing_in_a_row": analysis["weeks_agreeing_in_a_row"]},
        **{k: None for k in ("kw_replay", "kw_direct", "feedback", "instruction", "slot")},
        **(observation.get("stages") or {}),
    }


def build_record(observation: Mapping[str, Any], *, now: str) -> dict[str, Any]:
    """The week's observation as a ledger record, validated against its closed schema before it leaves.
    The schema is read from the code's own tree, never from a state root a caller passes."""
    body = {"window": {"since": observation["since"], "until": observation["until"], "ahead": observation["ahead"]},
            "platforms": observation["platforms"], "runs": observation.get("runs"),
            "stages": _record_stages(observation)}
    record = {
        "schema_version": RECORD_SCHEMA_VERSION,
        "observation_id": integrity.short_id("blogweek", {"observed_at": now, **body}),
        "observed_at": now,
        **body,
    }
    schema_cache.validate_against_schema(record, _repo_root() / RECORD_SCHEMA, RECORD_KIND)
    return record

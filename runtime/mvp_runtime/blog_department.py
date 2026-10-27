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
from typing import Any, Mapping

from runtime.read_only_kernel import integrity

from . import schema_cache, timeutil
from .errors import ToolError
from .paths import repo_root as _repo_root

BLOG_VAULT_ROOT_ENV = "MVP_BLOG_VAULT_ROOT"
PLATFORMS = ("naver", "tistory")
STATUSES = ("draft", "hold", "scheduled", "published")
WINDOW_DAYS = 7
RECORD_KIND = "blog_department_week"
RECORD_SCHEMA = "schemas/blog_department_week.v0.2.schema.json"
RECORD_SCHEMA_VERSION = "blog_department_week.v0.2"
RUNS_DIR = ("ops", "blog-runs")
RUN_REPORT_SCHEMA = "blog_run_report.v1"
# Written by the lane's daily ``blog_verify`` fire (``blog/verify.py``); named here so the weekly report can read it
# without the core importing the lane.
VERIFY_RECORD_KIND = "blog_verification"
# Written by the lane's daily ``blog_analyze`` fire (``blog/analysis.py``), one per week, again when its sources change.
ANALYSIS_RECORD_KIND = "blog_analysis"
_KST = timedelta(hours=9)
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


def front_matter(text: str) -> dict[str, str] | None:
    """The ``key: value`` lines this module reads, or None when the file has no front matter block."""
    if not text.startswith("---"):
        return None
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None
    fields: dict[str, str] = {}
    for line in parts[1].splitlines():
        match = _FIELD.match(line)
        if match and match.group(1) in _WANTED:
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


def verification_summary(ledger: Any, *, now: str) -> dict[str, Any]:
    """The week's ``blog_verification`` records (run dates in ``[since, until)``), summed: how many reported drafts
    the system could compare, how many agreed, and which rule codes differed most. Never carries a path."""
    since, until = _kst_date(now, -WINDOW_DAYS), _kst_date(now)
    out = {"runs": 0, "drafts": 0, "compared": 0, "agreed": 0, "differed": 0, "not_compared": 0, "codes": []}
    counter: dict[str, int] = {}
    for row in ledger.iter_records(kinds=[VERIFY_RECORD_KIND]):
        record = row.get("record") if row.get("kind") == VERIFY_RECORD_KIND else None
        if not isinstance(record, Mapping) or not since <= str(record.get("run_date")) < until:
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
    for row in ledger.iter_records(kinds=[ANALYSIS_RECORD_KIND]):
        record = row.get("record") if row.get("kind") == ANALYSIS_RECORD_KIND else None
        sunday = str((record or {}).get("week", {}).get("sunday")) if isinstance(record, Mapping) else ""
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


def build_record(observation: Mapping[str, Any], *, now: str) -> dict[str, Any]:
    """The week's observation as a ledger record, validated against its closed schema before it leaves.
    The schema is read from the code's own tree, never from a state root a caller passes."""
    body = {"window": {"since": observation["since"], "until": observation["until"], "ahead": observation["ahead"]},
            "platforms": observation["platforms"], "runs": observation.get("runs")}
    record = {
        "schema_version": RECORD_SCHEMA_VERSION,
        "observation_id": integrity.short_id("blogweek", {"observed_at": now, **body}),
        "observed_at": now,
        **body,
    }
    schema_cache.validate_against_schema(record, _repo_root() / RECORD_SCHEMA, RECORD_KIND)
    return record

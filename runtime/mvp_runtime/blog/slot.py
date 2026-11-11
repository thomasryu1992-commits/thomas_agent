"""P7 of BLOG_DEPARTMENT v0.2, in shadow: the system recomputes each reported Naver draft's slot with the vault's rule.

P7 moves the slot calculation into the lane so the instruction can carry it (v0.2 §4). Its switch condition is two
weeks in which the instruction's slots and the real reservations are the same. This module builds that evidence
ahead of P6 (Thomas 2026-11-03: "P7 슬롯 그림자", compared once per reported draft). The vault's
``tools/naver_slot.py`` is generated into ``blog/naver_slot.py`` by ``scripts/blog_vendor.py``. For each Naver draft
a run report names, ``next_slot`` runs on the vault as it stood when the draft was written, and the answer is compared
with the ``scheduled`` the report recorded.

**The moment is rebuilt, not recorded.** ``next_slot`` reads only the other Naver drafts' ``status``/``scheduled`` and
the draft's own file name. The drafts written after this one carry higher 편 numbers, and the day the rule looks at
(the latest scheduled day) lies about two weeks ahead. So the moment is the vault's drafts now, minus every draft
numbered above this one. The drafting shell runs ``naver_slot.py fix`` before it writes the report, so the reported
value already has the rule's minutes. What is compared is the writer's choice of day and window.

**Tistory is checked against the queue's rules, not re-planned** (Thomas 2026-11-03, "규칙 검사"). The planner in
``tools/tistory_queue.py`` reads the clock, re-checks the whole corpus with ``blog_variety`` in subprocesses, and
reads image files and the Mac-maintained ``browser_check``, so its answer is not a function of state the vault keeps.
What is a function of that state is whether a reported slot obeys the queue's rules. ``scripts/blog_vendor.py`` keeps
those rules (the slots, the per-day caps, ``key``, ``jitter``, ``kind``, ``is_new``) in ``blog/tistory_queue.py``, and
each reported Tistory draft gets five checks:
- ``minute``: the reported minute is ``jitter`` of its slot and the file name (the queue's per-file offset).
- ``unique``: no other Tistory post takes the same slot, or the whole day for a date-only date. The other posts
  are what the queue counts as occupied, published and scheduled posts, plus the drafts of the same report (which
  the queue placed while planning). Any other draft with an old ``scheduled`` is left out: a draft the Mac's variety
  pass held keeps its slot in the file, and the queue hands that slot to the next draft.
- ``only_cap`` / ``new_cap``: that day holds at most ``ONLY_PER_DAY`` Tistory-only posts and ``NEW_PER_DAY`` new
  ones, counting this draft at its reported slot (null when the cap does not apply to the draft). Fast posts
  (``fast: true``, Thomas 2026-11-11) are outside both counts; for a fast draft ``only_cap`` is the fast cap instead
  (at most ``FAST_PER_DAY`` fast posts that day) and ``new_cap`` is null, so the record's closed shape does not move.
- ``after_run``: the slot lies at least ``MARGIN_MIN`` minutes after the run started (the queue's cutoff).
The same-day 1-C conflict, ``variety`` and ``browser_check`` are not checked.

**Fast drafts** (Thomas 2026-11-11: four a day, three regular and one fast). A Naver draft with ``fast: true`` is
compared with ``next_fast``, not ``next_slot``: the first free evening slot from the day the draft was written
(Thomas 2026-11-11, "당일 저녁부터 순차적으로"), taken from its ``created`` date (no ``created`` gives ``no_created``). The rule leaves fast drafts out of the regular days and windows.

**Affiliate placements** (v0.3, 2026-11-05) are Naver posts too. ``blog-affiliate-place.py`` moves a stock draft to
``content/naver/제휴/<N>편-<slug>.md`` with the slot the writer chose, and the run report names it under
``affiliate_placed`` with its ``scheduled``, not under ``drafts``. Each is compared exactly as a draft (the rule already
reads the 제휴 folder), flagged ``affiliate``, and counted in the Naver totals and on its own. A report written before
the placement carried its slot gives ``no_slot``.

**A later move** is not a difference. The record keeps the file's ``scheduled`` now beside the reported one
(``moved``), so a slot moved by hand after the report is visible.

The record names a draft by its number only (편 number, Tistory file number), never its path (the file name is the
keyword). Report paths are data written outside the system: anything that does not resolve under the vault's
``content/naver/`` (or the queue's two levels of ``content/tistory/``) is refused unopened.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
from collections.abc import Iterator
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Mapping

RECORD_KIND = "blog_slot"
RECORD_SCHEMA = "schemas/blog_slot.v0.3.schema.json"   # v0.1 (Naver only), v0.2 (no affiliate) stay for their records
RECORD_SCHEMA_VERSION = "blog_slot.v0.3"
LOOKBACK_DAYS = 3
_SLOT = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")


def resolve(vault_root: Path, rel: Any, platform: str = "naver") -> Path | None:
    """The draft's path under ``<vault>/content/<platform>``, or None for anything that leaves it. A Tistory path
    must sit where the queue looks: in the folder or one subfolder down."""
    if not isinstance(rel, str) or not rel.startswith(f"content/{platform}/") or not rel.endswith(".md"):
        return None
    base = (vault_root / "content" / platform).resolve()
    path = (vault_root / rel).resolve()
    if base not in path.parents:
        return None
    if platform == "tistory" and base not in (path.parent, path.parent.parent):
        return None
    return path


@contextlib.contextmanager
def _as_of(ns: Any, episode: int, me: str) -> Iterator[None]:
    """While open, the port's ``drafts`` sees the vault as it stood when draft ``episode`` was written: every draft
    numbered above it is left out. Drafts without a number are kept (they are not this run's)."""
    real = ns.drafts

    def drafts(vault: str) -> list[tuple[str, dict]]:
        return [(f, d) for f, d in real(vault) if (ns.episode(f) or 0) < episode or os.path.basename(f) == me]

    ns.drafts = drafts
    try:
        yield
    finally:
        ns.drafts = real


def _tail(ns: Any, vault: str, me: str) -> dict[str, Any]:
    """What ``next_slot`` decides from: the latest scheduled day among the other drafts, its windows, the count.
    Recorded so a difference can be explained from the record alone."""
    days: dict[str, set[int]] = {}
    counted = 0
    for f, d in ns.drafts(vault):
        if os.path.basename(f) == me or d.get("status") != "draft" or ns.is_fast(d):
            continue
        counted += 1
        w = ns.when(d.get("scheduled", ""))
        if w:   # the same reading as the rule's next_slot: a day from the cutover on is counted by order
            days.setdefault(w[0], set()).add(len(days.get(w[0], ())) if w[1] and w[0] >= ns.CUTOVER
                                             else ns.window_of(w[1]) if w[1] else -1)
    last = max(days) if days else None
    return {"date": last, "windows": sorted(days[last]) if last else [], "drafts": counted}


def compare_draft(vault_root: Path, draft: Mapping[str, Any], *, affiliate: bool = False) -> dict[str, Any]:
    """One reported Naver draft (or affiliate placement): its state and, when compared, whether the rule gives the
    reported slot."""
    from . import naver_slot as ns

    row: dict[str, Any] = {"episode": None, "state": "", "agree": None, "reported": None, "computed": None,
                           "file_now": None, "moved": None, "tail": None, "affiliate": affiliate}
    path = resolve(vault_root, draft.get("path"))
    if path is None:
        row["state"] = "outside"
        return row
    episode = ns.episode(str(path))
    if episode is None:
        row["state"] = "no_episode"
        return row
    row["episode"] = episode
    if not path.is_file():
        row["state"] = "missing"
        return row
    reported = draft.get("scheduled")
    if not (isinstance(reported, str) and _SLOT.match(reported)):
        row["state"] = "no_slot"
        return row
    row["reported"] = reported
    front = ns.fm(path.read_text(encoding="utf-8"))
    now = front.get("scheduled") or None
    row["file_now"] = now if isinstance(now, str) and _SLOT.match(now) else None
    row["moved"] = row["file_now"] != reported
    ns.configure(vault_root)
    me = path.name
    fast = ns.is_fast(front)
    written = re.match(r"\d{4}-\d{2}-\d{2}", front.get("created") or "")
    if fast and not written:
        row["state"] = "no_created"
        return row
    try:
        with _as_of(ns, episode, me):
            row["tail"] = _tail(ns, ns.VAULT, me)
            computed = (ns.next_fast(ns.VAULT, str(path), today=date.fromisoformat(written[0])) if fast
                        else ns.next_slot(ns.VAULT, str(path)))
    except SystemExit:          # the rule's own refusal: no draft has a scheduled day to continue from
        row["state"] = "no_tail"
        return row
    except Exception as exc:  # noqa: BLE001 — one draft's crash is recorded, never the whole report's
        row["state"] = f"error:{type(exc).__name__}"
        return row
    row.update(state="compared", computed=computed, agree=computed == reported)
    return row


def tistory_schedule(vault_root: Path) -> dict[Path, tuple[dict[str, Any], str]]:
    """Every Tistory post the queue sees, with the date it holds now: ``published`` for a published post, otherwise
    ``scheduled`` for a scheduled post or a draft that carries one. Posts with no date (holds, undated drafts) are
    left out. Which drafts count as occupying is decided per report (``check_tistory``)."""
    from . import tistory_queue as tq

    base = vault_root / "content" / "tistory"
    out: dict[Path, tuple[dict[str, Any], str]] = {}
    for path in sorted(base.glob("*.md")) + sorted(base.glob("*/*.md")):
        r = tq.fm(str(path))
        status = r.get("status")
        when = r.get("published") if status == "published" else r.get("scheduled") if status in ("scheduled", "draft") else None
        if when:
            out[path.resolve()] = (r, when)
    return out


def check_tistory(vault_root: Path, draft: Mapping[str, Any], started_at: Any,
                  schedule: Mapping[Path, tuple[dict[str, Any], str]],
                  siblings: frozenset[Path] = frozenset()) -> dict[str, Any]:
    """One reported Tistory draft: its state and, when checked, the queue rules its reported slot passes.
    ``siblings`` are the resolved paths of the report's Tistory drafts, the only drafts counted as occupying."""
    from .. import timeutil
    from . import tistory_queue as tq

    row: dict[str, Any] = {"number": None, "state": "", "agree": None, "reported": None, "file_now": None,
                           "moved": None, "checks": None}
    path = resolve(vault_root, draft.get("path"), "tistory")
    if path is None:
        row["state"] = "outside"
        return row
    number = re.match(r"(\d+)", path.name)
    if not number:
        row["state"] = "no_number"
        return row
    row["number"] = int(number[1])
    if not path.is_file():
        row["state"] = "missing"
        return row
    reported = draft.get("scheduled")
    if not (isinstance(reported, str) and _SLOT.match(reported)):
        row["state"] = "no_slot"
        return row
    row["reported"] = reported
    try:
        me = tq.fm(str(path))
        now = me.get("scheduled") or None
        row["file_now"] = now if isinstance(now, str) and _SLOT.match(now) else None
        row["moved"] = row["file_now"] != reported
        folded, day = tq.key(reported), reported[:10]
        others = [(r, when) for p, (r, when) in schedule.items()
                  if p != path and (r.get("status") != "draft" or p in siblings)]
        on_day = [r for r, when in others if when[:10] == day] + [me]
        try:
            started = timeutil.parse_iso(started_at) if isinstance(started_at, str) else None
        except ValueError:
            started = None
        at = datetime.combine(datetime.fromisoformat(day).date(), time.fromisoformat(reported[11:]), tq.KST)
        regular = [r for r in on_day if not tq.is_fast(r)]
        checks = {
            "minute": reported[11:] == tq.jitter(folded[11:], path.name),
            "unique": not any(tq.key(when) in (folded, day) for _, when in others),
            "only_cap": (sum(1 for r in on_day if tq.is_fast(r)) <= tq.FAST_PER_DAY if tq.is_fast(me)
                         else sum(1 for r in regular if tq.kind(r) != "conversion") <= tq.ONLY_PER_DAY
                         if tq.kind(me) != "conversion" else None),
            "new_cap": (sum(1 for r in regular if tq.is_new(r)) <= tq.NEW_PER_DAY
                        if tq.is_new(me) and not tq.is_fast(me) else None),
            "after_run": at >= started + timedelta(minutes=tq.MARGIN_MIN) if started is not None else None,
        }
    except Exception as exc:  # noqa: BLE001 — one draft's crash is recorded, never the whole report's
        row["state"] = f"error:{type(exc).__name__}"
        return row
    row.update(state="checked", checks=checks, agree=all(v for v in checks.values() if v is not None))
    return row


def build_record(run_date: str, rows: list[dict[str, Any]], tistory: list[dict[str, Any]], *,
                 now: str) -> dict[str, Any]:
    """One report's comparison as a ledger record, validated against its closed schema."""
    from runtime.read_only_kernel import integrity

    from .. import schema_cache
    from ..paths import repo_root

    compared = [r for r in rows if r["state"] == "compared"]
    summary = {"naver": len(rows), "compared": len(compared), "agreed": sum(1 for r in compared if r["agree"]),
               "differed": sum(1 for r in compared if not r["agree"]),
               "moved": sum(1 for r in compared if r["moved"]),
               "not_compared": len(rows) - len(compared),
               "affiliate": sum(1 for r in rows if r["affiliate"]),
               "affiliate_compared": sum(1 for r in compared if r["affiliate"]),
               "affiliate_agreed": sum(1 for r in compared if r["affiliate"] and r["agree"])}
    checked = [r for r in tistory if r["state"] == "checked"]
    summary.update(tistory=len(tistory), tistory_checked=len(checked),
                   tistory_agreed=sum(1 for r in checked if r["agree"]),
                   tistory_differed=sum(1 for r in checked if not r["agree"]),
                   tistory_moved=sum(1 for r in checked if r["moved"]),
                   tistory_not_checked=len(tistory) - len(checked))
    record = {
        "schema_version": RECORD_SCHEMA_VERSION,
        "slot_id": integrity.short_id("blogslot", {"run_date": run_date, "checked_at": now, "drafts": rows,
                                                   "tistory": tistory}),
        "checked_at": now,
        "run_date": run_date,
        "drafts": rows,
        "tistory": tistory,
        "summary": summary,
    }
    schema_cache.validate_against_schema(record, repo_root() / RECORD_SCHEMA, RECORD_KIND)
    return record


def fire(ledger: Any, *, now: str, vault_root: Path | None = None) -> str:
    """Compare every run report of the last ``LOOKBACK_DAYS`` KST days that has no slot record yet; one record per
    report. An absent vault raises (``BLOG_VAULT_UNAVAILABLE``) so the fire is a failure."""
    from .. import blog_department, timeutil

    root = blog_department.vault_root(vault_root)
    folder = root.joinpath(*blog_department.RUNS_DIR)
    today = (timeutil.parse_iso(now) + timedelta(hours=9)).date()
    since = (today - timedelta(days=LOOKBACK_DAYS)).isoformat()
    done = {record.get("run_date") for record in blog_department.records(ledger, RECORD_KIND)}
    recorded: list[str] = []
    totals = {"compared": 0, "agreed": 0, "tistory_checked": 0, "tistory_agreed": 0}
    schedule: dict[Path, tuple[dict[str, Any], str]] | None = None
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        run_date = path.stem
        if not (re.fullmatch(r"\d{4}-\d{2}-\d{2}", run_date) and since <= run_date <= today.isoformat()) or run_date in done:
            continue
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        if not (isinstance(report, Mapping) and report.get("schema") == blog_department.RUN_REPORT_SCHEMA):
            continue
        drafts = [d for d in report.get("drafts") or [] if isinstance(d, Mapping)] \
            if isinstance(report.get("drafts"), list) else []
        rows = [compare_draft(root, d) for d in drafts if d.get("platform") == "naver"]
        named = {d.get("path") for d in drafts}
        placed = report.get("affiliate_placed") if isinstance(report.get("affiliate_placed"), list) else []
        rows += [compare_draft(root, p, affiliate=True) for p in placed
                 if isinstance(p, Mapping) and p.get("path") not in named]
        reported = [d for d in drafts if d.get("platform") == "tistory"]
        if reported and schedule is None:
            schedule = tistory_schedule(root)
        siblings = frozenset(p for p in (resolve(root, d.get("path"), "tistory") for d in reported) if p is not None)
        tistory = [check_tistory(root, d, report.get("started_at"), schedule or {}, siblings) for d in reported]
        record = build_record(run_date, rows, tistory, now=now)
        ledger.append_records(record["slot_id"], {RECORD_KIND: record})
        recorded.append(run_date)
        for key in totals:
            totals[key] += record["summary"][key]
    if not recorded:
        return "blog_slot_nothing_new"
    return (f"blog_slotted={','.join(recorded)} compared={totals['compared']} agreed={totals['agreed']} "
            f"tistory_checked={totals['tistory_checked']} tistory_agreed={totals['tistory_agreed']}")

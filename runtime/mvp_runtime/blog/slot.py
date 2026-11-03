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

**What is not compared.**
- **Tistory.** ``tools/tistory_queue.py`` reads the clock, re-checks the whole corpus with ``blog_variety`` in
  subprocesses, and reads image files and the Mac-maintained ``browser_check``. Its answer is not a function of
  state the vault keeps, so a recomputation hours later would mostly measure the corpus moving. Its drafts are
  counted, never compared, until a recording step exists (P4a's precedent).
- **A later move.** The record keeps the file's ``scheduled`` now beside the reported one (``moved``), so a slot
  moved by hand after the report is visible and is not counted as a difference.

The record names a draft by its 편 number only, never its path (the file name is the keyword). Report paths are data
written outside the system: anything that does not resolve under the vault's ``content/naver/`` is refused unopened.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any, Mapping

RECORD_KIND = "blog_slot"
RECORD_SCHEMA = "schemas/blog_slot.v0.1.schema.json"
RECORD_SCHEMA_VERSION = "blog_slot.v0.1"
LOOKBACK_DAYS = 3
_SLOT = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")


def resolve(vault_root: Path, rel: Any) -> Path | None:
    """The draft's path under ``<vault>/content/naver``, or None for anything that leaves it."""
    if not isinstance(rel, str) or not rel.startswith("content/naver/") or not rel.endswith(".md"):
        return None
    naver = (vault_root / "content" / "naver").resolve()
    path = (vault_root / rel).resolve()
    return path if naver in path.parents else None


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
        if os.path.basename(f) == me or d.get("status") != "draft":
            continue
        counted += 1
        w = ns.when(d.get("scheduled", ""))
        if w:
            days.setdefault(w[0], set()).add(ns.window_of(w[1]) if w[1] else -1)
    last = max(days) if days else None
    return {"date": last, "windows": sorted(days[last]) if last else [], "drafts": counted}


def compare_draft(vault_root: Path, draft: Mapping[str, Any]) -> dict[str, Any]:
    """One reported Naver draft: its state and, when compared, whether the rule gives the reported slot."""
    from . import naver_slot as ns

    row: dict[str, Any] = {"episode": None, "state": "", "agree": None, "reported": None, "computed": None,
                           "file_now": None, "moved": None, "tail": None}
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
    now = ns.fm(path.read_text(encoding="utf-8")).get("scheduled") or None
    row["file_now"] = now if isinstance(now, str) and _SLOT.match(now) else None
    row["moved"] = row["file_now"] != reported
    ns.configure(vault_root)
    me = path.name
    try:
        with _as_of(ns, episode, me):
            row["tail"] = _tail(ns, ns.VAULT, me)
            computed = ns.next_slot(ns.VAULT, str(path))
    except SystemExit:          # the rule's own refusal: no draft has a scheduled day to continue from
        row["state"] = "no_tail"
        return row
    except Exception as exc:  # noqa: BLE001 — one draft's crash is recorded, never the whole report's
        row["state"] = f"error:{type(exc).__name__}"
        return row
    row.update(state="compared", computed=computed, agree=computed == reported)
    return row


def build_record(run_date: str, rows: list[dict[str, Any]], tistory: int, *, now: str) -> dict[str, Any]:
    """One report's comparison as a ledger record, validated against its closed schema."""
    from runtime.read_only_kernel import integrity

    from .. import schema_cache
    from ..paths import repo_root

    compared = [r for r in rows if r["state"] == "compared"]
    summary = {"naver": len(rows), "compared": len(compared), "agreed": sum(1 for r in compared if r["agree"]),
               "differed": sum(1 for r in compared if not r["agree"]),
               "moved": sum(1 for r in compared if r["moved"]),
               "not_compared": len(rows) - len(compared), "tistory_not_compared": tistory}
    record = {
        "schema_version": RECORD_SCHEMA_VERSION,
        "slot_id": integrity.short_id("blogslot", {"run_date": run_date, "checked_at": now, "drafts": rows}),
        "checked_at": now,
        "run_date": run_date,
        "drafts": rows,
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
    done = {row["record"].get("run_date") for row in ledger.iter_records(kinds=[RECORD_KIND])
            if row.get("kind") == RECORD_KIND}
    recorded: list[str] = []
    totals = {"compared": 0, "agreed": 0}
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
        tistory = sum(1 for d in drafts if d.get("platform") == "tistory")
        record = build_record(run_date, rows, tistory, now=now)
        ledger.append_records(record["slot_id"], {RECORD_KIND: record})
        recorded.append(run_date)
        totals["compared"] += record["summary"]["compared"]
        totals["agreed"] += record["summary"]["agreed"]
    if not recorded:
        return "blog_slot_nothing_new"
    return f"blog_slotted={','.join(recorded)} compared={totals['compared']} agreed={totals['agreed']}"

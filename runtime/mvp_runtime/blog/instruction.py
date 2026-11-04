"""P6 of BLOG_DEPARTMENT v0.2, built ahead in shadow: the instruction the system would hand the drafting cron.

At P6 the drafting cron stops choosing its own topics and reads an instruction instead (v0.2 §3-1, §4). Thomas chose
on 2026-11-03 to build that instruction ahead and leave it unread ("P6 지시 그림자 미리"). Every morning, after the
production keyword run and before the drafting cron starts, this module writes what the system would instruct.
Once the day's run report arrives, it records where each draft the cron actually wrote sits in that instruction.
Nothing reads the instruction yet. The cron, its prompt and its skills are unchanged, and the read door does not serve
it: opening that read is P6's own act.

**What the instruction holds** is the whole set the cron chooses from today (the prompt's STEP 0), each list in the
order the cron reads it:

* ``next_topics``: the weekly review's open follow-ups (``next_topics.tsv``), which the cron reads before the queue;
* ``queue``: per lane, the production run's history candidates that would be new posts (``decision.action`` is
  ``create_new``) and that no vault post covers yet, in score order after the latest P5 guideline (``blog_feedback``)
  is applied. Each entry carries its rank, its rank without the guideline, and the days to its season peak, if any;
* ``tistory``: the cron's own Tistory list, ``tistory-pick --rank`` (``tistory_rank``), offline.

Naver and Tistory slots are not in it. P7 (``blog_slot``) already compares each reported slot with the rule, and a
Tistory slot depends on the file name the writer has not chosen yet.

**The moment.** The production keyword run starts at 22:10 UTC and writes ``history/<UTC date>.json``. The drafting
cron starts at 23:02 UTC (08:02 KST). The instruction is built at the first fire inside that gap, once the day's
history file exists. If the run reused its queue and wrote none, the build waits until ``LATE_FROM`` and uses the
newest older history, marked ``history_today: false``. After ``DRAFT_START`` nothing is built that day: the vault then
holds the cron's own drafts, so a later instruction would already have seen what it is compared with. The port reads
the vault through ``blog/kw_pipeline.py`` with no transport, so it opens no URL. The RSS read it would make fails and
the port falls back to the vault's status, as it does on any failed read.

**The check** names each reported draft's source:

* ``next_topics``, ``queue`` (with lane and rank) or ``tistory_list`` (with rank): the pick was in the instruction;
* ``history_filtered``: in the production history, but the instruction left it out (an update, or a covered keyword);
* ``outside``: in none of them, e.g. a variant of a held keyword, or a Tistory question found by 1-C discovery.

``outside`` is not a disagreement to fix in this module. Those are the sources P6 has to absorb or close, because
after P6 the cron writes only what it was told. ``timing`` says whether the instruction existed before the cron
started. A report on or after the first instruction's day with no instruction is ``no_instruction``. After P6 that
day writes nothing.

Records carry keywords (as ``blog_feedback`` does), never a title, URL or path.
"""

from __future__ import annotations

import contextlib
import importlib
import io
import json
import re
import tempfile
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from .feedback import HISTORY_DIR, KW_MAX, apply_shadow
from .kw_replay import _norm

RECORD_KIND = "blog_instruction"
RECORD_SCHEMA = "schemas/blog_instruction.v0.1.schema.json"
RECORD_SCHEMA_VERSION = "blog_instruction.v0.1"
CHECK_KIND = "blog_instruction_check"
CHECK_SCHEMA = "schemas/blog_instruction_check.v0.1.schema.json"
CHECK_SCHEMA_VERSION = "blog_instruction_check.v0.1"
FEEDBACK_KIND = "blog_feedback"

BUILD_FROM = time(7, 10)      # KST: kw-daily.sh starts (root cron 22:10 UTC)
LATE_FROM = time(7, 50)       # KST: no history today by now, so the run reused its queue; build from the last one
DRAFT_START = time(8, 2)      # KST: blog-daily-draft.sh starts (23:02 UTC); nothing is built after it
LOOKBACK_DAYS = 3
LANE_MAX = 40
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
KST = timezone(timedelta(hours=9))


# --- the instruction ---------------------------------------------------------------------------------------------

def history_for(root: Path, utc_day: str) -> tuple[str, Mapping[str, Any]] | None:
    """The newest readable history file dated ``utc_day`` or earlier (the run names it by its UTC date)."""
    folder = root.joinpath(*HISTORY_DIR)
    for path in sorted(folder.glob("*.json"), reverse=True) if folder.is_dir() else []:
        if not _DAY.match(path.stem) or path.stem > utc_day:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        if isinstance(data, Mapping) and isinstance(data.get("candidates"), list):
            return path.stem, data
    return None


def latest_feedback(ledger: Any) -> Mapping[str, Any] | None:
    from .. import blog_department

    last = None
    for record in blog_department.records(ledger, FEEDBACK_KIND):
        last = record
    return last


def _port(root: Path, clock: datetime, tmp: str) -> Any:
    """The generated keyword pipeline pointed at the vault with no transport (every URL is refused)."""
    from . import affiliate, kw_taxonomy
    from . import kw_pipeline as kp

    for module in (kw_taxonomy, affiliate, kp):        # fresh module state, as the replay does
        importlib.reload(module)
    kp.configure(root, cache_dir=Path(tmp) / "cache", clock=clock, epoch=clock.timestamp(), has_vol=False,
                 decision_mode=None, transport=None)
    return kp


def gather(root: Path, history: Mapping[str, Any], run_date: str, *, clock: datetime) -> dict[str, Any]:
    """What the cron would choose from on ``run_date``: open follow-ups, the history candidates that are new and
    uncovered (with season), and the Tistory ranking. The port's prints are discarded."""
    day = date.fromisoformat(run_date)
    with tempfile.TemporaryDirectory(prefix="blog-instruction-") as tmp, \
            contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        kp = _port(root, clock, tmp)
        posts = kp.own_posts()
        follow = [{"kw": str(c["kw"]).strip()[:KW_MAX], "lane": str(c.get("lane") or "")[:LANE_MAX]}
                  for c in kp.next_topics(posts, today=day) if c.get("state") == "열림"]
        kept, filtered = [], Counter()
        for raw in history.get("candidates") or []:
            if not isinstance(raw, Mapping) or not str(raw.get("kw") or "").strip():
                continue
            decision = raw.get("decision") if isinstance(raw.get("decision"), Mapping) else {}
            if decision.get("action") != "create_new":
                filtered["not_create"] += 1
            elif kp.covered_by(str(raw["kw"]), posts):
                filtered["covered"] += 1
            else:
                hit = kp.season_hits(str(raw["kw"]), day)
                kept.append({**raw, "_season": hit[0] if hit else None})
        tistory = [str(r["kw"]).strip()[:KW_MAX] for r in kp.tistory_rank(today=run_date)["rows"]]
    return {"next_topics": follow, "kept": kept, "filtered": filtered, "tistory": tistory}


def queue(kept: list[Mapping[str, Any]], feedback: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """The kept candidates ranked per lane with the latest guideline's factors (P5), best first."""
    clusters = [c for c in (feedback or {}).get("clusters") or [] if isinstance(c, Mapping)]
    shadow = apply_shadow({"candidates": kept}, {"clusters": clusters})
    rows = [{"kw": c["kw"], "lane": c["lane"], "rank": c["rank_after"], "rank_unadjusted": c["rank_before"],
             "factor": c["factor"], "season_days": raw["_season"]}
            for c, raw in zip(shadow["candidates"], kept)]
    return sorted(rows, key=lambda r: (r["lane"], r["rank"]))


def build_instruction(gathered: Mapping[str, Any], history_date: str, history_today: bool,
                      feedback: Mapping[str, Any] | None, run_date: str, *, now: str) -> dict[str, Any]:
    from runtime.read_only_kernel import integrity

    from .. import schema_cache
    from ..paths import repo_root

    rows = queue(gathered["kept"], feedback)
    body = {"run_date": run_date, "history_date": history_date,
            "feedback_id": (feedback or {}).get("feedback_id"),
            "naver": {"next_topics": gathered["next_topics"], "queue": rows}, "tistory": gathered["tistory"]}
    record = {
        "schema_version": RECORD_SCHEMA_VERSION,
        # The factors are floats, which a fingerprint payload refuses; the body goes in as its JSON text.
        "instruction_id": integrity.short_id("bloginstruction", {"at": now, "body": json.dumps(
            body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))}),
        "recorded_at": now,
        "mode": "shadow",
        **body,
        "history_today": history_today,
        "summary": {"next_topics": len(gathered["next_topics"]), "queue": len(rows),
                    "queue_lanes": dict(sorted(Counter(r["lane"] for r in rows).items())),
                    "filtered_not_create": gathered["filtered"]["not_create"],
                    "filtered_covered": gathered["filtered"]["covered"],
                    "tistory": len(gathered["tistory"])},
    }
    schema_cache.validate_against_schema(record, repo_root() / RECORD_SCHEMA, RECORD_KIND)
    return record


# --- the check ---------------------------------------------------------------------------------------------------

def locate(draft: Mapping[str, Any], instruction: Mapping[str, Any], history: Mapping[str, Any] | None) -> dict:
    """Where one reported draft's keyword sits in the instruction."""
    platform = draft.get("platform") if draft.get("platform") in ("naver", "tistory") else "other"
    keyword = str(draft.get("keyword") or "").strip()[:KW_MAX]
    row = {"platform": platform, "keyword": keyword or None, "source": "no_keyword", "lane": None, "rank": None,
           "season_days": None}
    if not keyword:
        return row
    key = _norm(keyword)
    if platform == "tistory":
        ranks = {_norm(kw): i for i, kw in enumerate(instruction["tistory"], 1)}
        if key in ranks:
            return {**row, "source": "tistory_list", "rank": ranks[key]}
    elif platform == "naver":
        follow = next((c for c in instruction["naver"]["next_topics"] if _norm(c["kw"]) == key), None)
        if follow is not None:
            return {**row, "source": "next_topics", "lane": follow["lane"] or None}
        hit = next((q for q in instruction["naver"]["queue"] if _norm(q["kw"]) == key), None)
        if hit is not None:
            return {**row, "source": "queue", "lane": hit["lane"], "rank": hit["rank"],
                    "season_days": hit["season_days"]}
    pool = [c.get("kw") for c in (history or {}).get("candidates") or [] if isinstance(c, Mapping)]
    pool += [c.get("kw") for c in (history or {}).get("tistory") or [] if isinstance(c, Mapping)]
    if any(_norm(kw) == key for kw in pool if kw):
        return {**row, "source": "history_filtered"}
    return {**row, "source": "outside"}


def build_check(run_date: str, report: Mapping[str, Any], instruction: Mapping[str, Any] | None,
                history: Mapping[str, Any] | None, *, now: str) -> dict[str, Any]:
    from runtime.read_only_kernel import integrity

    from .. import schema_cache, timeutil
    from ..paths import repo_root

    drafts = [d for d in report.get("drafts") or [] if isinstance(d, Mapping)] \
        if isinstance(report.get("drafts"), list) else []
    started = report.get("started_at") if isinstance(report.get("started_at"), str) else None
    picks = [locate(d, instruction, history) for d in drafts] if instruction else []
    timing = None
    if instruction and started:
        try:
            timing = "ready" if timeutil.parse_iso(instruction["recorded_at"]) <= timeutil.parse_iso(started) else "late"
        except (TypeError, ValueError):
            timing = None
    sources = Counter(p["source"] for p in picks)
    body = {"run_date": run_date, "instruction_id": (instruction or {}).get("instruction_id"),
            "state": "checked" if instruction else "no_instruction", "started_at": started, "timing": timing,
            "picks": picks}
    record = {
        "schema_version": CHECK_SCHEMA_VERSION,
        "check_id": integrity.short_id("bloginstructioncheck", {"at": now, **body}),
        "recorded_at": now,
        "mode": "shadow",
        **body,
        "summary": {"drafts": len(drafts), "picks": len(picks),
                    "in_instruction": sum(sources[s] for s in ("next_topics", "queue", "tistory_list")),
                    **{s: sources[s] for s in ("next_topics", "queue", "tistory_list", "history_filtered",
                                               "outside", "no_keyword")}},
    }
    schema_cache.validate_against_schema(record, repo_root() / CHECK_SCHEMA, CHECK_KIND)
    return record


# --- the fire (scheduler kind ``blog_instruction``) -------------------------------------------------------------

def _records(ledger: Any, kind: str) -> dict[str, Mapping[str, Any]]:
    """The latest record per ``run_date`` of ``kind``."""
    from .. import blog_department

    out: dict[str, Mapping[str, Any]] = {}
    for record in blog_department.records(ledger, kind):
        if isinstance(record.get("run_date"), str):
            out[record["run_date"]] = record
    return out


def fire(ledger: Any, *, now: str, vault_root: Path | None = None) -> str:
    """Build today's instruction inside the morning gap, then check every run report of the last ``LOOKBACK_DAYS``
    KST days that has none yet. An absent vault raises (``BLOG_VAULT_UNAVAILABLE``) so the fire is a failure."""
    from .. import blog_department, timeutil

    root = blog_department.vault_root(vault_root)
    at = timeutil.parse_iso(now).astimezone(timezone.utc)
    local = at.astimezone(KST)
    run_date = local.date().isoformat()
    built = _records(ledger, RECORD_KIND)
    parts: list[str] = []
    if BUILD_FROM <= local.time() < DRAFT_START and run_date not in built:
        found = history_for(root, at.date().isoformat())
        today = found is not None and found[0] == at.date().isoformat()
        if found is not None and (today or local.time() >= LATE_FROM):
            record = build_instruction(gather(root, found[1], run_date, clock=at), found[0], today,
                                       latest_feedback(ledger), run_date, now=now)
            ledger.append_records(record["instruction_id"], {RECORD_KIND: record})
            built[run_date] = record
            s = record["summary"]
            parts.append(f"blog_instruction={run_date}(next={s['next_topics']},queue={s['queue']},"
                         f"tistory={s['tistory']})")
        else:
            parts.append("blog_instruction_waiting_history")

    checked = _records(ledger, CHECK_KIND)
    first = min(built) if built else None
    since = (local.date() - timedelta(days=LOOKBACK_DAYS)).isoformat()
    folder = root.joinpath(*blog_department.RUNS_DIR)
    done = []
    for path in sorted(folder.glob("*.json")) if folder.is_dir() and first else []:
        day = path.stem
        if not (_DAY.match(day) and max(since, first) <= day <= run_date) or day in checked:
            continue
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        if not (isinstance(report, Mapping) and report.get("schema") == blog_department.RUN_REPORT_SCHEMA):
            continue
        instruction = built.get(day)
        history = history_for(root, instruction["history_date"]) if instruction else None
        record = build_check(day, report, instruction,
                             history[1] if history and history[0] == instruction["history_date"] else None, now=now)
        ledger.append_records(record["check_id"], {CHECK_KIND: record})
        s = record["summary"]
        done.append(f"{day}:{record['state']}" + (f"(in={s['in_instruction']}/{s['picks']},{record['timing']})"
                                                  if record["state"] == "checked" else ""))
    if done:
        parts.append("checked=" + ",".join(done))
    return " ".join(parts) or "blog_instruction_nothing_new"

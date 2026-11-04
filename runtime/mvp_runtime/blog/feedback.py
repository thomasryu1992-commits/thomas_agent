"""P5 of BLOG_DEPARTMENT v0.2: a keyword-priority guideline from the recorded statistics, applied in shadow.

The loop may change one thing on its own, keyword priority (v0.2 §7 L3). This module computes how it would: from
the latest ``blog_analysis`` week (P3) it judges each Naver cluster's posts against all posts, and where the evidence
clears #1183's floor it sets a bounded factor on the score of that cluster's queue candidates. Nothing reads the
result yet. The record keeps the guideline and the ranks it would have changed, which is P5's switch condition
("지침이 바꾼 순위가 기록에 남는다").

Inputs, all read-only:

* the latest ``blog_analysis`` record for a week that ended before today — views per post for the week (a Naver
  post is its 편 number). The collector's table is the week's top 100 by views, matched to a post by the
  ``url:`` in its front matter;
* each Naver post's front matter — ``status``, ``published``, ``cluster``, ``url``;
* the latest ``analytics/keywords/history/<date>.json`` the production keyword run wrote — its ``candidates``
  (keyword, lane, score, ``decision.cluster``). The ``ranks`` table is never read: #1183 marks those ranks
  UNVERIFIED (a rank may belong to another of the blog's posts).

The evidence rules are #1183's (§6, §7, Thomas 2026-10-08):

* a post counts once ``D+14`` has passed by the week's Sunday;
* a post's week is ``measured`` (in the table), ``below_table`` (absent from it, so its views are at most the
  table's lowest — neither zero nor unknown), or ``unknown`` (absent, has no ``url:`` the collector could match,
  and the table has rows it could not match to a post);
* a cluster is judged only with at least ``MIN_POSTS`` posts and at most ``MAX_MISSING`` unknown, and only against
  posts of the same platform; otherwise it is ``INSUFFICIENT_EVIDENCE`` and its factor is 1;
* a median is an order statistic with ``below_table`` posts at the bottom, so it is either a measured number or
  ``below_table`` — never a number made up for the posts the table left out.

A candidate whose cluster is ``unresolved``, or has no judged posts, keeps its score. Tistory candidates are left
alone (the per-post views are Naver's).
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping

from .kw_replay import TOP_PER_LANE, _norm

HISTORY_DIR = ("analytics", "keywords", "history")
NAVER_DIR = ("content", "naver")
ANALYSIS_KIND = "blog_analysis"
MIN_AGE_DAYS = 14
MIN_POSTS = 5
MAX_MISSING = 0.30
UP_RATIO, DOWN_RATIO = 2.0, 0.5
UP_FACTOR, DOWN_FACTOR = 1.2, 0.8
KW_MAX = 80

_POST = re.compile(r"^(\d{1,5})편")
_DAY = re.compile(r"^(\d{4}-\d{2}-\d{2})")
_URL = re.compile(r"blog\.naver\.com/thomasai/\d{9,}")
_HISTORY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CLUSTER = re.compile(r"^[a-z0-9][a-z0-9-]{0,59}$")
_WANTED = frozenset({"status", "published", "cluster", "url"})

BELOW = "below_table"


# --- the posts ---------------------------------------------------------------------------------------------------

def read_posts(root: Path) -> list[dict[str, Any]]:
    """Every published Naver post with its number, publish date, cluster and whether it has a matchable ``url:``."""
    from .. import blog_department

    posts = []
    for path in sorted(root.joinpath(*NAVER_DIR).glob("*.md")):
        number = _POST.match(path.name)
        try:
            fields = blog_department.front_matter(path.read_text(encoding="utf-8"), _WANTED)
        except (OSError, UnicodeDecodeError):
            fields = None
        if not number or not fields or fields.get("status") != "published":
            continue
        day = _DAY.match(fields.get("published") or "")
        cluster = (fields.get("cluster") or "").strip()
        posts.append({"post": int(number.group(1)), "published": day.group(1) if day else None,
                      "cluster": cluster if _CLUSTER.match(cluster) else None,
                      "url": bool(_URL.search(fields.get("url") or ""))})
    return posts


def post_states(posts: Iterable[Mapping[str, Any]], week: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The posts old enough to judge on the week (``D+14`` by its Sunday), each with its state and views."""
    cutoff = (date.fromisoformat(week["week"]["sunday"]) - timedelta(days=MIN_AGE_DAYS)).isoformat()
    naver = week.get("naver") or {}
    views = {row["post"]: row["views"] for row in naver.get("posts") or []}
    unmatched = int(naver.get("posts_unmapped") or 0)
    rows = []
    for post in posts:
        if not post["published"] or post["published"] > cutoff:
            continue
        if post["post"] in views:
            state, value = "measured", views[post["post"]]
        elif post["url"] or not unmatched:
            state, value = BELOW, None
        else:
            state, value = "unknown", None
        rows.append({"post": post["post"], "cluster": post["cluster"], "state": state, "views": value})
    return rows


def median(rows: list[Mapping[str, Any]]) -> int | str | None:
    """The upper-middle of the known rows with ``below_table`` lowest: a number, ``below_table``, or None if none."""
    known = [r for r in rows if r["state"] != "unknown"]
    if not known:
        return None
    ordered = sorted(known, key=lambda r: (r["state"] == "measured", r["views"] or 0))
    middle = ordered[len(ordered) // 2]
    return middle["views"] if middle["state"] == "measured" else BELOW


# --- the guideline -----------------------------------------------------------------------------------------------

def _ratio(value: int | str | None, base: int | str | None) -> float | None:
    if not isinstance(value, int) or not isinstance(base, int) or base <= 0:
        return None
    return round(value / base, 3)


def guideline(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Each cluster judged against all posts of the week: its counts, median, ratio, verdict and factor."""
    base = median(rows)
    clusters: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["cluster"]:
            clusters[row["cluster"]].append(row)
    judged = []
    for name in sorted(clusters):
        group = clusters[name]
        counts = {state: sum(1 for r in group if r["state"] == state) for state in ("measured", BELOW, "unknown")}
        missing = round(counts["unknown"] / len(group), 3)
        mid = median(group)
        ratio = _ratio(mid, base)
        verdict, factor, reason = "INSUFFICIENT_EVIDENCE", 1.0, None
        if len(group) < MIN_POSTS:
            reason = "posts_below_floor"
        elif missing > MAX_MISSING:
            reason = "missing_above_ceiling"
        elif not isinstance(base, int):
            reason = "baseline_below_table"
        elif mid == BELOW or (ratio is not None and ratio <= DOWN_RATIO):
            verdict, factor = "CANDIDATE", DOWN_FACTOR
        elif ratio is not None and ratio >= UP_RATIO:
            verdict, factor = "CANDIDATE", UP_FACTOR
        else:
            verdict = "NO_DIFFERENCE"
        judged.append({"cluster": name, "posts": len(group), **counts, "missing_rate": missing,
                       "median": mid, "ratio": ratio, "verdict": verdict, "reason": reason, "factor": factor})
    return {"baseline": {"posts": len(rows), "median": base,
                         "measured": sum(1 for r in rows if r["state"] == "measured"),
                         BELOW: sum(1 for r in rows if r["state"] == BELOW),
                         "unknown": sum(1 for r in rows if r["state"] == "unknown")},
            "clusters": judged}


# --- applied in shadow -------------------------------------------------------------------------------------------

def _score(candidate: Mapping[str, Any]) -> float:
    value = candidate.get("score")
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def _ranked(cands: list[dict[str, Any]], key: str) -> dict[int, int]:
    """Rank within each lane by ``key`` descending; ties keep the history file's order."""
    lanes: dict[str, list[int]] = defaultdict(list)
    for i, c in enumerate(cands):
        lanes[c["lane"]].append(i)
    ranks = {}
    for members in lanes.values():
        for rank, i in enumerate(sorted(members, key=lambda i: (-cands[i][key], i)), 1):
            ranks[i] = rank
    return ranks


def apply_shadow(history: Mapping[str, Any], judged: Mapping[str, Any]) -> dict[str, Any]:
    """The history file's candidates with the guideline's factors applied: each one's score and lane rank before and
    after, the top ``TOP_PER_LANE`` per lane before and after, and what moved."""
    factors = {c["cluster"]: c for c in judged["clusters"]}
    cands = []
    for raw in history.get("candidates") or []:
        if not isinstance(raw, Mapping) or not str(raw.get("kw") or "").strip():
            continue
        decision = raw.get("decision") if isinstance(raw.get("decision"), Mapping) else {}
        cluster = str(decision.get("cluster") or "unresolved")
        entry = factors.get(cluster)
        effect = ("unresolved" if cluster == "unresolved" else "no_guideline" if entry is None
                  else "adjusted" if entry["factor"] != 1.0 else "unchanged")
        before = _score(raw)
        factor = entry["factor"] if entry else 1.0
        cands.append({"kw": str(raw["kw"]).strip()[:KW_MAX], "lane": str(raw.get("lane") or "")[:40],
                      "cluster": cluster[:60], "effect": effect, "factor": factor,
                      "score_before": before, "score_after": round(before * factor, 1)})
    before, after = _ranked(cands, "score_before"), _ranked(cands, "score_after")
    for i, c in enumerate(cands):
        c["rank_before"], c["rank_after"] = before[i], after[i]

    def tops(rank: dict[int, int]) -> dict[str, list[str]]:
        lanes: dict[str, list[tuple[int, str]]] = defaultdict(list)
        for i, c in enumerate(cands):
            if rank[i] <= TOP_PER_LANE:
                lanes[c["lane"]].append((rank[i], c["kw"]))
        return {lane: [kw for _, kw in sorted(items)] for lane, items in sorted(lanes.items())}

    top_before, top_after = tops(before), tops(after)
    entered = sorted({kw for lane, kws in top_after.items() for kw in kws if kw not in top_before.get(lane, [])})
    left = sorted({kw for lane, kws in top_before.items() for kw in kws if kw not in top_after.get(lane, [])})
    effects = defaultdict(int)
    for c in cands:
        effects[c["effect"]] += 1
    return {"candidates": cands, "top_before": top_before, "top_after": top_after,
            "summary": {"candidates": len(cands),
                        **{name: effects[name] for name in ("adjusted", "unchanged", "no_guideline", "unresolved")},
                        "rank_moved": sum(1 for c in cands if c["rank_before"] != c["rank_after"]),
                        "top_entered": entered, "top_left": left}}


# --- the daily fire (scheduler kind ``blog_feedback``) -----------------------------------------------------------

RECORD_KIND = "blog_feedback"
RECORD_SCHEMA = "schemas/blog_feedback.v0.1.schema.json"
RECORD_SCHEMA_VERSION = "blog_feedback.v0.1"


def latest_week(ledger: Any, *, today: str) -> Mapping[str, Any] | None:
    """The latest ``blog_analysis`` record of the latest week that ended before ``today`` (KST)."""
    from .. import blog_department

    weeks: dict[str, Mapping[str, Any]] = {}
    for record in blog_department.records(ledger, ANALYSIS_KIND):
        sunday = str((record.get("week") or {}).get("sunday") or "")
        if sunday and sunday < today:
            weeks[sunday] = record          # appended in order: the last one for a week is its latest
    return weeks[max(weeks)] if weeks else None


def latest_history(root: Path, *, today: str) -> tuple[str, Mapping[str, Any]] | None:
    """The newest readable history file dated today or earlier, with its date."""
    folder = root.joinpath(*HISTORY_DIR)
    for path in sorted(folder.glob("*.json"), reverse=True) if folder.is_dir() else []:
        if not _HISTORY.match(path.stem) or path.stem > today:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        if isinstance(data, Mapping) and isinstance(data.get("candidates"), list):
            return path.stem, data
    return None


def build_record(week: Mapping[str, Any], history_date: str, posts: list[Mapping[str, Any]],
                 history: Mapping[str, Any], *, now: str) -> dict[str, Any]:
    """The guideline and its shadow as a ledger record, validated against its closed schema. ``inputs_id`` names
    what was read, so the next fire records again only when it changed."""
    from runtime.read_only_kernel import integrity

    from .. import schema_cache
    from ..paths import repo_root

    rows = post_states(posts, week)
    judged = guideline(rows)
    shadow = apply_shadow(history, judged)
    clusters = judged["clusters"]
    read = json.dumps({"week": week.get("sources_id"), "posts": rows, "history": history_date,
                       "candidates": shadow["candidates"]}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    record = {
        "schema_version": RECORD_SCHEMA_VERSION,
        "feedback_id": integrity.short_id("blogfeedback", {"at": now, "read": read}),
        "inputs_id": integrity.short_id("blogfeedbackin", {"read": read}),
        "recorded_at": now,
        "mode": "shadow",
        "week": {"monday": week["week"]["monday"], "sunday": week["week"]["sunday"],
                 "analysis_id": week.get("analysis_id")},
        "history_date": history_date,
        "baseline": judged["baseline"],
        "clusters": clusters,
        **shadow,
        "summary": {**shadow["summary"],
                    "clusters_judged": len(clusters),
                    "clusters_candidate": sorted(c["cluster"] for c in clusters if c["verdict"] == "CANDIDATE"),
                    "clusters_insufficient": sum(1 for c in clusters if c["verdict"] == "INSUFFICIENT_EVIDENCE")},
    }
    schema_cache.validate_against_schema(record, repo_root() / RECORD_SCHEMA, RECORD_KIND)
    return record


def fire(ledger: Any, *, now: str, vault_root: Path | None = None) -> str:
    """Record the guideline for the latest analysed week applied to the latest history file, unless the latest
    record read the same thing. An absent vault raises (``BLOG_VAULT_UNAVAILABLE``)."""
    from .. import blog_department, timeutil

    root = blog_department.vault_root(vault_root)
    today = (timeutil.parse_iso(now) + timedelta(hours=9)).date().isoformat()
    week = latest_week(ledger, today=today)
    if week is None:
        return "blog_feedback_no_analysis"
    found = latest_history(root, today=today)
    if found is None:
        return "blog_feedback_no_history"
    record = build_record(week, found[0], read_posts(root), found[1], now=now)
    last = None
    for previous in blog_department.records(ledger, RECORD_KIND):
        last = previous.get("inputs_id")
    if last == record["inputs_id"]:
        return "blog_feedback_nothing_new"
    ledger.append_records(record["feedback_id"], {RECORD_KIND: record})
    summary = record["summary"]
    return (f"blog_feedback={record['week']['sunday']}/{found[0]}"
            f"(candidate_clusters={len(summary['clusters_candidate'])},rank_moved={summary['rank_moved']})")

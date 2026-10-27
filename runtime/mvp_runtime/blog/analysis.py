"""P3 of BLOG_DEPARTMENT v0.2: the week's blog statistics, read from what the Mac collected, recorded on the ledger.

Collecting the statistics stays outsourced: it needs the owner's logins (Naver Creator Advisor, the Tistory admin,
Search Console), so the Mac collects them every Monday into the vault. The system reads those files, never the
services:

* ``analytics/naver-weekly/<sunday>.json`` — the Naver week (``summary`` only; the ``raw`` block is API replies):
  views, unique visitors, inflow, search inflow and the official search share, the home feed, views per post and the
  top inflow queries;
* ``analytics/weekly-browser/<sunday>.json`` — the Tistory week (views and where they came from), Search Console
  totals, its per-page table, and the index count;
* ``analytics/keywords/capture.tsv`` — the capture rate (weekly inflow ÷ weekly search volume) per query, which the
  weekly review computes with the Search Ad API. The volume lookup is a network read and stays outsourced; the
  system summarises the rows (median per query class).

The weekly review skill writes its own numbers for the same week into ``analytics/README.md``. The record carries
both and whether each pair agrees — P3's switch condition (v0.2 §4) is two recorded weeks in a row on which they do.
A field either side does not have is ``None`` and never counts as a disagreement.

Files arrive late and are refilled (a browser run redone at noon, the README row written after the collection), so
the daily fire re-records a week whenever what it reads for that week changed. Dates are KST; a week is named by
its Sunday. A record carries no title and no URL: a Naver post is its 편 number, a Tistory post its page number.
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Mapping

NAVER_DIR = ("analytics", "naver-weekly")
BROWSER_DIR = ("analytics", "weekly-browser")
CAPTURE_FILE = ("analytics", "keywords", "capture.tsv")
SKILL_INDEX = ("analytics", "README.md")
QUERY_CLASSES = ("답형", "이름만", "이동형")
TOP_QUERIES = 20
QUERY_MAX = 80

_POST = re.compile(r"^(\d{1,5})편")
_PAGE = re.compile(r"^/(?:m/)?(\d{1,6})$")
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        match = re.fullmatch(r"\s*(\d[\d,]*)\s*", value)
        return int(match.group(1).replace(",", "")) if match else None
    return None


def _float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        match = re.fullmatch(r"\s*(\d[\d,]*(?:\.\d+)?)\s*%?\s*", value)
        return float(match.group(1).replace(",", "")) if match else None
    return None


def _get(data: Any, *keys: str) -> Any:
    for key in keys:
        if not isinstance(data, Mapping):
            return None
        data = data.get(key)
    return data


def _load(path: Path) -> Mapping[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    return data if isinstance(data, Mapping) else None


def _query(value: Any) -> str | None:
    return value.strip()[:QUERY_MAX] if isinstance(value, str) and value.strip() else None


# --- the three sources -------------------------------------------------------------------------------------------

def read_naver(data: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """The Naver week from the collector's ``summary``, or None when there is none."""
    summary = _get(data, "summary")
    if not isinstance(summary, Mapping):
        return None
    posts, unmapped = [], 0
    for row in summary.get("글별") if isinstance(summary.get("글별"), list) else []:
        match = _POST.match(str(_get(row, "편") or ""))
        views = _int(_get(row, "조회"))
        if not match or views is None:
            unmapped += 1
            continue
        posts.append({"post": int(match.group(1)), "views": views})
    queries = []
    for row in summary.get("검색어") if isinstance(summary.get("검색어"), list) else []:
        query, count = _query(_get(row, "검색어")), _int(_get(row, "건수"))
        if query and count is not None:
            queries.append({"query": query, "inflow": count})
    return {
        "views": _int(_get(summary, "조회수", "이번주")),
        "views_prev": _int(_get(summary, "조회수", "지난주")),
        "unique_visitors": _int(_get(summary, "순방문자", "이번주")),
        "inflow": _int(summary.get("유입수")),
        "search_inflow": _int(summary.get("검색유입")),
        "search_share_pct": _float(summary.get("공식검색유입률")),
        "home_feed": _int(summary.get("홈판")),
        "posts": posts,
        "posts_unmapped": unmapped,
        "queries": queries[:TOP_QUERIES],
    }


def read_browser(data: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """The Tistory and Search Console week from the browser collection, or None when there is none. A part the run
    skipped (a login that lapsed) is None inside, never zero."""
    if data is None:
        return None
    inflow = _get(data, "tistory", "inflow", "result")
    tistory = None
    if isinstance(inflow, Mapping) and _int(inflow.get("count")) is not None:
        tistory = {"views": _int(inflow.get("count")),
                   "search": _int(_get(inflow, "searchEngine", "total")),
                   "daum": _int(_get(inflow, "searchEngine", "list", "daum")),
                   "google": _int(_get(inflow, "searchEngine", "list", "google")),
                   "sns": _int(_get(inflow, "sns", "total")),
                   "referral": _int(_get(inflow, "ref", "total"))}
    totals = _get(data, "gsc", "totals")
    gsc = None
    if isinstance(totals, Mapping) and totals:
        span = _get(data, "gsc", "range")
        span = span if isinstance(span, list) and len(span) == 2 and all(isinstance(d, str) for d in span) else [None, None]
        gsc = {"start": span[0], "end": span[1],
               "clicks": _int(totals.get("Total clicks")),
               "impressions": _int(totals.get("Total impressions")),
               "ctr_pct": _float(totals.get("Average CTR")),
               "position": _float(totals.get("Average position"))}
    pages = []
    for row in _get(data, "gsc_pages", "rows") if isinstance(_get(data, "gsc_pages", "rows"), list) else []:
        if not (isinstance(row, list) and len(row) >= 4):
            continue
        match = _PAGE.match(str(row[0]))
        clicks, impressions, position = _int(row[1]), _int(row[2]), _float(row[3])
        if match and clicks is not None and impressions is not None:
            pages.append({"page": int(match.group(1)), "clicks": clicks, "impressions": impressions,
                          "position": position})
    index = _get(data, "index")
    return {
        "tistory": tistory,
        "gsc": gsc,
        "gsc_pages": pages,
        "indexed": _int(_get(index, "indexed")),
        "not_indexed": _int(_get(index, "not_indexed")),
    }


def read_capture(text: str | None, week_end: str) -> dict[str, Any] | None:
    """The week's capture rows, summarised: per query class how many have a ratio and their median (the weekly
    review's own median, the upper middle), and how many the volume API could not size and their inflow. None when
    the week has no rows."""
    if text is None:
        return None
    rows = [line.split("\t") for line in text.splitlines() if line.startswith(week_end + "\t")]
    rows = [r for r in rows if len(r) >= 7]
    if not rows:
        return None
    classes = {}
    for name in QUERY_CLASSES:
        ratios = sorted(r for r in (_float(row[5]) for row in rows if row[6].strip() == name) if r is not None)
        classes[name] = {"queries": len(ratios), "median": ratios[len(ratios) // 2] if ratios else None}
    unsized = [row for row in rows if _float(row[5]) is None]
    return {"queries": len(rows), "classes": classes, "unsized": len(unsized),
            "unsized_inflow": sum(_float(row[3]) or 0.0 for row in unsized)}


# --- what the weekly review wrote --------------------------------------------------------------------------------

def _first(pattern: str, cell: str | None) -> float | None:
    if cell is None or "미확인" in cell:
        return None
    match = re.search(pattern, cell)
    return float(match.group(1).replace(",", "")) if match else None


def read_skill_row(text: str | None, week_end: str) -> dict[str, float | None] | None:
    """The numbers the weekly review wrote in the README row linked to ``<week_end>.md``. A cell it marked
    ``미확인`` or wrote in another shape gives None for that number. None when there is no row."""
    if text is None:
        return None
    row = next((line for line in text.splitlines()
                if line.startswith("|") and f"({week_end}.md)" in line), None)
    if row is None:
        return None
    cells = [c.strip() for c in row.strip().strip("|").split("|")]
    if len(cells) < 6:
        return None
    naver, tistory, impressions, clicks = cells[2], cells[3], cells[4], cells[5]
    number = r"(\d[\d,]*)"
    return {
        "naver_views": _first(number, naver),
        "naver_search_inflow": _first(r"=\s*" + number, naver),
        "naver_search_share_pct": _first(r"검색 (\d+(?:\.\d+)?)%", naver),
        "naver_home_share_pct": _first(r"홈판 (\d+(?:\.\d+)?)%", naver),
        "tistory_views": _first(r"조회 " + number, tistory),
        "tistory_search": _first(r"검색 " + number, tistory),
        "tistory_daum": _first(r"다음 " + number, tistory),
        "tistory_google": _first(r"구글 " + number, tistory),
        "gsc_impressions": _first(r"^" + number, impressions),
        "gsc_indexed": _first(r"색인 " + number, impressions),
        "gsc_clicks": _first(r"^" + number, clicks),
    }


def system_numbers(naver: Mapping[str, Any] | None, browser: Mapping[str, Any] | None) -> dict[str, float | None]:
    """The system's side of each number the README row carries, computed the way the weekly review computes it:
    the search share is the official rate, the home-feed share is the home feed over inflow, both to one decimal."""
    n = naver or {}
    tistory = (browser or {}).get("tistory") or {}
    gsc = (browser or {}).get("gsc") or {}
    home = (round(n["home_feed"] / n["inflow"] * 100, 1)
            if n.get("home_feed") is not None and n.get("inflow") else None)
    share = round(n["search_share_pct"], 1) if n.get("search_share_pct") is not None else None
    return {
        "naver_views": n.get("views"),
        "naver_search_inflow": n.get("search_inflow"),
        "naver_search_share_pct": share,
        "naver_home_share_pct": home,
        "tistory_views": tistory.get("views"),
        "tistory_search": tistory.get("search"),
        "tistory_daum": tistory.get("daum"),
        "tistory_google": tistory.get("google"),
        "gsc_impressions": gsc.get("impressions"),
        "gsc_indexed": (browser or {}).get("indexed"),
        "gsc_clicks": gsc.get("clicks"),
    }


def compare(system: Mapping[str, Any], skill: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Each README number against the system's: ``agree`` is None when either side has no value."""
    rows = []
    for field, mine in system.items():
        theirs = (skill or {}).get(field)
        agree = None if mine is None or theirs is None else abs(float(mine) - float(theirs)) < 1e-9
        rows.append({"field": field, "system": mine, "skill": theirs, "agree": agree})
    return rows


# --- the daily fire (scheduler kind ``blog_analyze``) ------------------------------------------------------------

RECORD_KIND = "blog_analysis"
RECORD_SCHEMA = "schemas/blog_analysis.v0.1.schema.json"
RECORD_SCHEMA_VERSION = "blog_analysis.v0.1"
LOOKBACK_DAYS = 14


def _text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def analyse_week(root: Path, week_end: str) -> dict[str, Any] | None:
    """Everything the record holds for one week, or None when the Naver file for it is absent or unreadable."""
    naver = read_naver(_load(root.joinpath(*NAVER_DIR, f"{week_end}.json")))
    if naver is None:
        return None
    browser_path = root.joinpath(*BROWSER_DIR, f"{week_end}.json")
    browser = read_browser(_load(browser_path)) if browser_path.is_file() else None
    skill = read_skill_row(_text(root.joinpath(*SKILL_INDEX)), week_end)
    return {"naver": naver, "browser": browser,
            "capture": read_capture(_text(root.joinpath(*CAPTURE_FILE)), week_end),
            "skill": skill, "comparison": compare(system_numbers(naver, browser), skill)}


def build_record(week_end: str, parts: Mapping[str, Any], *, now: str) -> dict[str, Any]:
    """The week as a ledger record, validated against its closed schema. ``sources_id`` names what was read, so
    the next fire re-records only when it changed."""
    from runtime.read_only_kernel import integrity

    from .. import schema_cache
    from ..paths import repo_root

    monday = (date.fromisoformat(week_end) - timedelta(days=6)).isoformat()
    compared = [row for row in parts["comparison"] if row["agree"] is not None]
    body = {"naver": parts["naver"], "browser": parts["browser"], "capture": parts["capture"],
            "comparison": parts["comparison"]}
    # The fingerprint refuses floats (shares, medians), so it is taken over the body's canonical JSON text.
    read = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    record = {
        "schema_version": RECORD_SCHEMA_VERSION,
        "analysis_id": integrity.short_id("bloganalysis", {"week_end": week_end, "analysed_at": now, "read": read}),
        "sources_id": integrity.short_id("blogsources", {"week_end": week_end, "read": read}),
        "analysed_at": now,
        "week": {"monday": monday, "sunday": week_end},
        **body,
        "summary": {"skill_row": parts["skill"] is not None, "compared": len(compared),
                    "agreed": sum(1 for row in compared if row["agree"]),
                    "differed": sorted(row["field"] for row in compared if not row["agree"])},
    }
    schema_cache.validate_against_schema(record, repo_root() / RECORD_SCHEMA, RECORD_KIND)
    return record


def fire(ledger: Any, *, now: str, vault_root: Path | None = None) -> str:
    """Record every week whose Sunday falls in the last ``LOOKBACK_DAYS`` KST days and has a Naver file, unless the
    latest record for that week read the same thing. An absent vault raises (``BLOG_VAULT_UNAVAILABLE``)."""
    from .. import blog_department, timeutil

    root = blog_department.vault_root(vault_root)
    folder = root.joinpath(*NAVER_DIR)
    today = (timeutil.parse_iso(now) + timedelta(hours=9)).date()
    since = (today - timedelta(days=LOOKBACK_DAYS)).isoformat()
    latest: dict[str, str] = {}
    for row in ledger.iter_records(kinds=[RECORD_KIND]):
        record = row.get("record") if row.get("kind") == RECORD_KIND else None
        if isinstance(record, Mapping):
            latest[str(_get(record, "week", "sunday"))] = str(record.get("sources_id"))
    recorded = []
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        week_end = path.stem
        if not (re.fullmatch(r"\d{4}-\d{2}-\d{2}", week_end) and since <= week_end < today.isoformat()):
            continue
        try:
            if date.fromisoformat(week_end).weekday() != 6:
                continue
        except ValueError:
            continue
        parts = analyse_week(root, week_end)
        if parts is None:
            continue
        record = build_record(week_end, parts, now=now)
        if latest.get(week_end) == record["sources_id"]:
            continue
        ledger.append_records(record["analysis_id"], {RECORD_KIND: record})
        latest[week_end] = record["sources_id"]
        summary = record["summary"]
        recorded.append(f"{week_end}({summary['agreed']}/{summary['compared']})")
    if not recorded:
        return "blog_analysis_nothing_new"
    return "blog_analysed=" + ",".join(recorded)

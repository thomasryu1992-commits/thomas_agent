"""Where a published blog package ranks for its own keyword — observed, recorded, not acted on.

Phase 4 of the content lane (proposal §5): once the operator records a package's
`published_url` (`scripts.record_published_url`), the lane can look its `target_keyword` up
and see where the post sits. This module is that lookup and its record, and nothing more:

- **Only packages with a recorded URL are tracked.** The runtime never publishes and cannot
  observe a publish, so a `draft` package has nothing to find.
- **Checkpoints, not a schedule.** D+1, D+7, D+14 and D+28 after publication. This module says
  which one is due; nothing here registers a scheduler row, and the weekly lane stays disabled.
- **Append-only.** One `blog_rank_snapshot` row per observation. A checkpoint already taken is
  not re-taken, and no row is ever rewritten.
- **Not feedback yet.** A single rank is noise — the blog tab reshuffles daily. Nothing reads
  these rows into keyword selection; that is a later decision, made on several observations.

The lookup reuses the lane's existing read-only client (`BlogCompetitionTool`, API HUB blog
search, `sort=sim`) — no second network stack. That ordering is Naver's relevance order for
the blog search API, which is a proxy for the blog tab, not the tab itself; `source` says so on
every record.

Pure except :func:`check_rank`, which calls the injected tool.
"""

from __future__ import annotations

import re
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

from runtime.read_only_kernel import integrity

from .errors import ToolBlocked, ToolError

SNAPSHOT_SCHEMA_VERSION = "blog_rank_snapshot.v0.1"
SNAPSHOT_RECORD_KIND = "blog_rank_snapshot"
RANK_SOURCE = "naver_apihub.blog_search.sort_sim"
# The documented per-request cap of the blog search; beyond it the post is "not in the window".
RESULT_WINDOW = 100
CHECKPOINTS: tuple[tuple[str, int], ...] = (("D+1", 1), ("D+7", 7), ("D+14", 14), ("D+28", 28))

PACKAGE_NOT_PUBLISHED = "PACKAGE_NOT_PUBLISHED"
PLATFORM_NOT_TRACKED = "PLATFORM_NOT_TRACKED"
CHECKPOINT_NOT_DUE = "CHECKPOINT_NOT_DUE"
CHECKPOINT_ALREADY_TAKEN = "CHECKPOINT_ALREADY_TAKEN"
RANK_SNAPSHOT_SCHEMA_INVALID = "RANK_SNAPSHOT_SCHEMA_INVALID"

__all__ = [
    "CHECKPOINTS",
    "SNAPSHOT_RECORD_KIND",
    "check_rank",
    "checkpoint_status",
    "normalize_post_url",
    "rank_in",
    "taken_checkpoints",
    "trackable",
]

_ISO = "%Y-%m-%dT%H:%M:%SZ"
_NAVER_BLOG_HOSTS = frozenset({"blog.naver.com", "m.blog.naver.com"})
_POSTVIEW_RE = re.compile(r"^/(?:PostView|PostList)\.(?:naver|nhn)$", re.I)


def normalize_post_url(url: Any) -> str | None:
    """One comparison key per post, however the URL was spelled. None when unparseable.

    A Naver blog post is ``blog.naver.com/<blogId>/<logNo>`` in all of its spellings: the
    mobile host, ``PostView.naver?blogId=…&logNo=…`` (and the old ``.nhn``), http or https,
    trailing slashes, tracking query strings and fragments. Any other URL keeps its lowercased
    host and its path, without the query and fragment."""
    text = str(url or "").strip()
    if not text:
        return None
    if "://" not in text:
        text = "https://" + text
    try:
        parts = urllib.parse.urlsplit(text)
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    if not host:
        return None
    path = re.sub(r"/+", "/", parts.path or "/").rstrip("/")
    if host in _NAVER_BLOG_HOSTS:
        if _POSTVIEW_RE.match(path):
            query = urllib.parse.parse_qs(parts.query)
            blog_id = (query.get("blogId") or [""])[0]
            log_no = (query.get("logNo") or [""])[0]
            if blog_id and log_no.isdigit():
                return f"blog.naver.com/{blog_id.lower()}/{log_no}"
            return None
        segments = [s for s in path.split("/") if s]
        if len(segments) >= 2 and segments[1].isdigit():
            return f"blog.naver.com/{segments[0].lower()}/{segments[1]}"
        return None
    return f"{host}{path}"


def rank_in(links: Iterable[Any], published_url: str) -> tuple[int | None, str | None, int]:
    """``(rank_position, matched_url, window)`` — 1-based, or ``(None, None, window)``."""
    want = normalize_post_url(published_url)
    listed = list(links)
    if want is None:
        return None, None, len(listed)
    for position, link in enumerate(listed, start=1):
        if normalize_post_url(link) == want:
            return position, str(link), len(listed)
    return None, None, len(listed)


def trackable(package: Mapping[str, Any]) -> bool:
    """Only a NAVER package the operator recorded as published, with its URL.

    The lookup is Naver's blog search, so a Tistory post (a v0.3 package with
    ``platform: tistory``) is never in its window: tracking it would record "not found" at every
    checkpoint — a rank that reads as a measurement and is not one. Rows without ``platform``
    (v0.1/v0.2) are Naver packages."""
    return (str(package.get("platform") or "naver") == "naver"
            and package.get("publish_state") == "published" and bool(package.get("published_url")))


def _parse(ts: str) -> datetime:
    return datetime.strptime(ts, _ISO).replace(tzinfo=timezone.utc)


def checkpoint_status(
    published_at: str, now: str, taken: Iterable[str] = (),
) -> dict[str, Any]:
    """``{"due", "missed", "latest", "next"}`` — labels, or None / [] where there is none.

    The due checkpoint is the LATEST one whose day has arrived and that was not taken; earlier
    untaken ones are missed, not back-filled — a D+1 measured on day 10 would be a day-10 rank
    wearing the wrong label."""
    elapsed = _parse(now) - _parse(published_at)
    taken = set(taken)
    arrived = [label for label, days in CHECKPOINTS if elapsed >= timedelta(days=days)]
    upcoming = [label for label, days in CHECKPOINTS if elapsed < timedelta(days=days)]
    due = arrived[-1] if arrived and arrived[-1] not in taken else None
    missed = [label for label in arrived[:-1] if label not in taken]
    return {"due": due, "missed": missed, "latest": arrived[-1] if arrived else None,
            "next": upcoming[0] if upcoming else None}


def taken_checkpoints(rows: Iterable[Mapping[str, Any]], package_id: str) -> list[str]:
    """Checkpoints already recorded for ``package_id`` among ledger rows."""
    out: list[str] = []
    for row in rows:
        if row.get("kind") != SNAPSHOT_RECORD_KIND:
            continue
        record = row.get("record") or {}
        if record.get("package_id") == package_id and record.get("checkpoint") not in out:
            out.append(record.get("checkpoint"))
    return out


def check_rank(
    package: Mapping[str, Any],
    *,
    tool: Any,
    now: str,
    checkpoint: str,
    timeout_seconds: int = 15,
) -> dict[str, Any]:
    """One snapshot for ``package`` at ``checkpoint``. Refuses an unpublished package.

    A failed lookup is recorded, not raised: ``degraded`` with the reason and a null rank —
    "we could not look" is a different fact from "the post was not in the window", and the
    record keeps them apart."""
    if not trackable(package):
        raise ToolError(PACKAGE_NOT_PUBLISHED,
                        f"{package.get('package_id')} has no recorded published_url; "
                        "record it with scripts.record_published_url first")
    if checkpoint not in dict(CHECKPOINTS):
        raise ToolError(CHECKPOINT_NOT_DUE, f"unknown checkpoint {checkpoint!r}")
    rank: int | None = None
    matched: str | None = None
    window = 0
    degraded_reason: str | None = None
    try:
        result = tool.competition(str(package["target_keyword"]), display=RESULT_WINDOW,
                                  timeout_seconds=timeout_seconds)
        rank, matched, window = rank_in(getattr(result, "links", []) or [],
                                        str(package["published_url"]))
    except (ToolError, ToolBlocked) as exc:
        degraded_reason = str(getattr(exc, "reason_code", "TOOL_ERROR"))
    record = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "package_id": str(package["package_id"]),
        "target_keyword": str(package["target_keyword"]),
        "published_url": str(package["published_url"]),
        "checkpoint": checkpoint,
        "checked_at_utc": now,
        "rank_position": rank,
        "result_window": window,
        "matched_url": matched,
        "source": RANK_SOURCE,
        "degraded": degraded_reason is not None,
        "degraded_reason": degraded_reason,
    }
    record["snapshot_id"] = integrity.short_id("brs", {
        "package_id": record["package_id"], "checkpoint": checkpoint, "checked_at_utc": now,
    })
    return record

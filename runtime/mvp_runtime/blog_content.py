"""One weekly blog package: research the keywords, draft against the winner, score the draft.

The content lane has had every part of this since 2026-08-10 and has never once run it on its
own. `content.general` has executed exactly once in the ledger's history, the Naver adapters
are live and measured, `blog_content_package.v0.1` has a closed schema with no producer, and
`schedules.jsonl` holds no content kind at all. What was missing was never a capability — it
was the wiring that puts the capabilities in sequence without a human in the middle of each
step.

This module is that sequence, and it lives in the worker because that is where the credentials
are: the Naver keys and the model providers are on `thomas-pipeline-worker` and nowhere else
(`scheduler-maint` carries neither), so the scheduler delegates the whole job here rather than
collecting anything itself — the same cut `crypto_data_review` makes for the same reason.

Two governed runs, in order:

1. **research** with `keyword_seeds`, which runs the Naver brief inside the run and leaves a
   `keyword_research` record with measured monthly demand per candidate;
2. **content** with the winning keyword as `naver_keywords`, which drafts against that
   measured evidence rather than against a guess.

Then the draft is parsed into a package, **scored against the lane's standards**, validated
against the closed schema, and appended to the ledger.

**The file write is governed and optional (§J decision 2, Thomas 2026-08-30).** The package
is still the ledger row — the record is the authority and the two files are its RENDERING
(proposal §4b): ``POST.md`` for the operator to read, ``PASTE.txt`` to paste into the editor
verbatim (SmartEditor ONE renders no markdown, so the paste file carries zero formatting
symbols). Both go through ``workspace.run_write`` — same confinement, same create-only rule,
same audit record — behind ``MVP_WORKSPACE_WRITER=real`` on the worker. With the flag closed
this module writes nothing, exactly as before the decision; with it open, a failed write
DEGRADES the fire's sheet rather than failing it, because a package that exists only as a
record is the lane's founding state, not an error.

**The scoring is advisory here, not a gate.** `blog_draft_score` says whether the draft cleared
Thomas's standards and the answer rides in the record and the operator's sheet. It does not
suppress the package: a short draft that an operator can lengthen is worth more than a fire
that produced nothing and said why in a log line. The two drafts that prompted the scorer's
existence were both half the minimum length and were both delivered — the fix for that is
measuring, not discarding.

**The engine and its platforms (2026-10-05).** This module is the engine: the research and
selection, the two governed runs, the one revision and its fail-safes, the package and its record.
Everything that depends on where the post goes — the request, how an answer is read and measured,
the paste format, the platform's fit checks — is the platform profile's (`blog_platform`, over
`blog_naver` and `blog_tistory`), chosen by `platform=` in the schedule's request (Naver when
absent). The overlap rules are `blog_overlap`'s and the quality layers `blog_quality`'s.
"""

from __future__ import annotations

import datetime
import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from runtime.read_only_kernel import integrity, schema_validation

from . import (
    blog_draft,
    blog_draft_score,
    blog_overlap,
    blog_platform,
    blog_prompt,
    blog_quality,
    naver_research,
    timeutil,
)
from .errors import MvpRuntimeError, ToolError
from .pipeline import run_task

PACKAGE_SCHEMA_VERSION = "blog_content_package.v0.3"
PACKAGE_RECORD_KIND = "blog_content_package"
# Every version a ledger row may carry. v0.1 rows (none exist on the host, but the schema shipped)
# and v0.2 rows (every package before 2026-10-05) stay valid against their own schema; readers
# pick the schema by the row's `schema_version` and the platform by :func:`package_platform`.
PACKAGE_SCHEMA_VERSIONS = ("blog_content_package.v0.1", "blog_content_package.v0.2",
                           "blog_content_package.v0.3")

# The schema's own ceilings, mirrored here so the parser truncates deterministically instead of
# handing the validator a draft-shaped reason to fail the whole fire. A model that emits 24
# capture markers has produced a usable draft with too many notes, not an invalid one.
MAX_TITLES = 5
MAX_BODY_BLOCKS = blog_draft.MAX_BODY_BLOCKS
MAX_TAGS = 30
MAX_IMAGE_SHOTS = 20
MAX_FACT_CHECKS = 30
MAX_METRICS = 50

# The competition vocabulary the package schemas accept; anything else is "unknown".
_AD_COMPETITION_LEVELS = frozenset({"높음", "중간", "낮음"})

# There is no competition gate in selection, deliberately. The one competition number the brief
# carries for every row is Search Ad's `compIdx`, and that is ADVERTISER BID competition — how
# many advertisers bid on the keyword — not how hard the blog tab is. Gating on it excluded eight
# of the ten rows of every weekly brief (2026-09-06..09-20, all `NO_ELIGIBLE_KEYWORD`) for a
# reason that says nothing about blog ranking. It is still recorded, as `ad_competition`, beside
# `blog_competing_posts` where the API HUB leg answered; neither is a gate, because the runtime
# has no measured blog-SERP difficulty to gate on and inventing one would be worse.
# `low_volume` rows are Search Ad's own "too small to report" marker and are not a target.

# Where the vault's published posts live, when the deployment mounts one. Unset means the
# lane cannot know what was already published outside it (every post so far), and rule-based
# selection refuses rather than re-picking a published keyword (see `run_content_ideation`).
PUBLISHED_ROOT_ENV = "MVP_BLOG_PUBLISHED_ROOT"
# The vault's layout: one front-matter file per post under these platform folders
# (the same folders `tools/kw_pipeline.py own_posts` reads).
_PUBLISHED_PLATFORM_DIRS = ("naver", "tistory")

NO_ELIGIBLE_KEYWORD = "NO_ELIGIBLE_KEYWORD"
PUBLISHED_KEYWORD_SOURCE_UNAVAILABLE = "PUBLISHED_KEYWORD_SOURCE_UNAVAILABLE"
KEYWORD_QUEUE_UNAVAILABLE = "KEYWORD_QUEUE_UNAVAILABLE"
KEYWORD_QUEUE_STALE = "KEYWORD_QUEUE_STALE"
IDEATION_INPUTS_CONFLICT = "IDEATION_INPUTS_CONFLICT"

# `source=queue` in the schedule's request: take the seeds from the vault keyword pipeline's
# queue (`tools/kw_pipeline.py run` -> `analytics/keywords/queue.md`) instead of a fixed list.
# The fixed list was the root cause of all three `NO_ELIGIBLE_KEYWORD` fires: the same four
# seeds every week, so the same answer every week.
QUEUE_SOURCE_TOKEN = "source=queue"
QUEUE_REL = "analytics/keywords/queue.md"
# The queue is rebuilt weekly (root crontab, Sunday 06:30 KST). Two weeks allows one missed
# rebuild; older than that, its reach measurements (top-ten visitors, exact-title count) are
# describing a SERP that has moved on.
QUEUE_MAX_AGE_DAYS = 14
# The OpenRouter model for the DRAFT and its revision only (Thomas 2026-09-28). The analysis
# chain's free OpenRouter model is served from Google AI Studio's shared free pool, which was
# rate-limiting upstream while Google's own free tier answered 503 on six of seven models. So
# every blog draft fell to the chain's last member, groq. Unset or blank keeps the analysis
# chain as it is; the research run never uses this.
BLOG_OPENROUTER_MODEL_ENV = "MVP_BLOG_OPENROUTER_MODEL"
# Per-member ceiling for the draft chain. The measured non-Google free model took 68 s for one
# real-sized draft, and the analysis chain's 45 s cap (40 s at a 120 s budget) would have timed
# it out every time. With the blog profile's 360 s, each member gets up to 120 s. A platform
# whose draft is longer names its own (`PlatformProfile.draft_member_timeout_seconds`).
BLOG_DRAFT_MEMBER_TIMEOUT_SECONDS = 120


def draft_provider(provider: Any, member_timeout_cap: int = BLOG_DRAFT_MEMBER_TIMEOUT_SECONDS) -> Any:
    """The provider the draft and its revision run on: ``provider`` with its OpenRouter member
    swapped to ``MVP_BLOG_OPENROUTER_MODEL`` when that is set, ``provider`` itself otherwise."""
    model = os.environ.get(BLOG_OPENROUTER_MODEL_ENV, "").strip()
    if not model or provider is None:
        return provider
    from . import providers as _providers  # function-local: only the drafting path needs it
    return _providers.with_openrouter_model(
        provider, model, member_timeout_cap=member_timeout_cap)


# Search Ad takes at most five hint keywords per call, and the brief is one call.
MAX_QUEUE_SEEDS = naver_research.MAX_HINT_KEYWORDS
IDEATION_RESEARCH_BLOCKED = "IDEATION_RESEARCH_BLOCKED"
IDEATION_CONTENT_BLOCKED = "IDEATION_CONTENT_BLOCKED"
IDEATION_REVISION_BLOCKED = "IDEATION_REVISION_BLOCKED"
BLOG_PACKAGE_SCHEMA_INVALID = "BLOG_PACKAGE_SCHEMA_INVALID"

__all__ = [
    "BLOG_PACKAGE_SCHEMA_INVALID",
    "PLATFORM_TOKEN",
    "IDEATION_CONTENT_BLOCKED",
    "IDEATION_RESEARCH_BLOCKED",
    "NO_ELIGIBLE_KEYWORD",
    "PACKAGE_RECORD_KIND",
    "PUBLISHED_KEYWORD_SOURCE_UNAVAILABLE",
    "PublishedKeywords",
    "VaultPublishedKeywordSource",
    "build_package",
    "package_platform",
    "parse_platform",
    "parse_seeds",
    "run_content_ideation",
    "select_target_keyword",
    "written_keywords",
]


# `platform=tistory` in the schedule's request: which platform the week's package is for. Absent
# means Naver (`blog_platform.DEFAULT_PLATFORM`), so every row written before it is unchanged.
PLATFORM_TOKEN = "platform="


def parse_platform(request: str) -> tuple[str | None, str]:
    """``(platform or None, the request without it)``. Two ``platform=`` tokens that disagree are
    refused rather than one winning — the operator asked for two things."""
    named: list[str] = []
    kept: list[str] = []
    for part in str(request or "").split(","):
        if part.strip().lower().startswith(PLATFORM_TOKEN):
            named.append(part.split("=", 1)[1].strip().lower())
        else:
            kept.append(part)
    if len(set(named)) > 1:
        raise ToolError(blog_platform.BLOG_PLATFORM_UNKNOWN,
                        f"the request names more than one platform: {', '.join(named)}")
    return (named[0] if named else None), ",".join(kept)


def parse_request(request: str) -> tuple[list[str], str | None, bool]:
    """``(seeds, target override, use_queue)`` from a schedule's request column.

    ``source=queue`` asks for the vault queue as the seed source (see ``QUEUE_SOURCE_TOKEN``);
    everything else is :func:`parse_seeds`'s grammar, unchanged."""
    use_queue = False
    kept: list[str] = []
    for part in str(request or "").split(","):
        if part.strip().lower() == QUEUE_SOURCE_TOKEN:
            use_queue = True
        else:
            kept.append(part)
    seeds, target = parse_seeds(",".join(kept))
    return seeds, target, use_queue


def parse_seeds(request: str) -> tuple[list[str], str | None]:
    """``"미리캔버스, 포스터제작, target=스마트스토어"`` -> (seeds, target override).

    The schedule's free-text `request` column carries the seeds, exactly as `crypto_factory`
    carries a symbol list there. `target=` is the operator's override for one fire: it skips
    selection and drafts against the named keyword, which is what makes the automatic rule a
    default rather than a decision the code took on its own behalf.
    """
    seeds: list[str] = []
    target: str | None = None
    for part in str(request or "").split(","):
        item = part.strip()
        if not item:
            continue
        if item.lower().startswith("target="):
            candidate = item.split("=", 1)[1].strip()
            if candidate:
                target = candidate
            continue
        seeds.append(item)
    return seeds, target


@dataclass(frozen=True)
class PublishedKeywords:
    """What an outside source says was already written: topic keywords and post tags.

    Two lists because they are matched differently (`blog_overlap.covering_keyword`): a topic keyword
    covers its near variants, a tag only its exact spelling — a short tag like '인스타그램'
    would otherwise swallow every new topic that mentions it."""

    keywords: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    # One entry per post: platform, path, title, url, keywords, tags (2026-10-05). The two lists
    # above stay the union over both platforms — the Naver rule reads them exactly as before —
    # and `posts` is what a per-platform reading (Tistory's overlap policy, internal links) needs.
    posts: tuple[Mapping[str, Any], ...] = ()

    def for_platform(self, platform: str) -> "PublishedKeywords":
        """Only ``platform``'s posts, as the same two lists."""
        mine = [p for p in self.posts if p.get("platform") == platform]
        return PublishedKeywords(
            keywords=tuple(_dedupe([k for p in mine for k in p.get("keywords") or ()])),
            tags=tuple(_dedupe([t for p in mine for t in p.get("tags") or ()])),
            posts=tuple(mine))


class VaultPublishedKeywordSource:
    """Keywords of the posts in the Obsidian vault — where every post so far was written.

    Reads the front matter the vault's own keyword pipeline reads (`tools/kw_pipeline.py
    own_posts`): `keywords: [..]`, `google_kw:` and `tags: [..]` of every
    `content/{naver,tistory}/**/*.md`. Drafts count as well as published posts, which is the
    ledger's own rule — a post waiting to be published is already that keyword's week.

    Fails closed (``PUBLISHED_KEYWORD_SOURCE_UNAVAILABLE``) when the root is missing or holds no
    post at all: a wrong mount path reads as "nothing was ever published", which is exactly the
    answer that would re-draft a published keyword.
    """

    def __init__(self, root: Path | str):
        self.root = Path(root)

    def load(self) -> PublishedKeywords:
        if not self.root.is_dir():
            raise ToolError(PUBLISHED_KEYWORD_SOURCE_UNAVAILABLE,
                            f"published-post root is not a directory: {self.root}")
        keywords: list[str] = []
        tags: list[str] = []
        posts: list[dict[str, Any]] = []
        for platform in _PUBLISHED_PLATFORM_DIRS:
            for path in sorted((self.root / "content" / platform).glob("**/*.md")):
                front = _front_matter(path.read_text(encoding="utf-8", errors="replace"))
                if front is None:
                    continue
                post_keywords = _front_list(front, "keywords")
                keywords.extend(post_keywords)
                google = _front_value(front, "google_kw")
                if google:
                    keywords.append(google)
                post_tags = _front_list(front, "tags")
                tags.extend(post_tags)
                # The folder is the platform: it is where the vault files a post, and it is what
                # `kw_pipeline own_posts` reads. A `platform:` line that disagrees is not trusted
                # over it.
                posts.append({"platform": platform, "path": str(path.relative_to(self.root)),
                              "title": _front_value(front, "title") or None,
                              "url": _front_value(front, "url") or None,
                              "status": _front_value(front, "status") or None,
                              "keywords": tuple(post_keywords), "tags": tuple(post_tags)})
                if google:
                    # A Naver post's `google_kw` is the keyword its Google (Tistory) version is
                    # written for — reserved there, whether or not that version exists yet.
                    posts.append({"platform": "tistory" if platform == "naver" else platform,
                                  "path": str(path.relative_to(self.root)),
                                  "title": _front_value(front, "title") or None, "url": None,
                                  "keywords": (google,), "tags": (), "reserved": True})
        if not posts:
            raise ToolError(PUBLISHED_KEYWORD_SOURCE_UNAVAILABLE,
                            f"no post front matter under {self.root}/content/{{naver,tistory}}")
        return PublishedKeywords(keywords=tuple(_dedupe(keywords)), tags=tuple(_dedupe(tags)),
                                 posts=tuple(posts))


_FRONT_MATTER_RE = re.compile(r"^---\n(.*?)\n---", re.S)


def _front_matter(text: str) -> str | None:
    match = _FRONT_MATTER_RE.match(text)
    return match.group(1) if match else None


def _front_value(front: str, key: str) -> str:
    match = re.search(rf"^{re.escape(key)}:[ \t]*(.*)$", front, re.M)
    return match.group(1).strip().strip("\"'") if match else ""


def _front_list(front: str, key: str) -> list[str]:
    raw = _front_value(front, key).strip("[]")
    return [item.strip().strip("\"'") for item in raw.split(",") if item.strip().strip("\"'")]


def _dedupe(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    return [v for v in values if not (v in seen or seen.add(v))]


def select_target_keyword(
    metrics: Sequence[Mapping[str, Any]],
    *,
    already_written: Sequence[str] = (),
    already_tagged: Sequence[str] = (),
    queue: Sequence[QueueCandidate] | None = None,
    covered_by: Callable[[str], str | None] | None = None,
) -> tuple[str | None, dict[str, Any]]:
    """The most-searched keyword with measured demand that is not already written. Pure.

    With ``queue`` (the seeds came from the vault queue) the choice is restricted to those
    candidates and ordered by the queue's score, which already weighs demand by reach; this
    brief's fresh measurement still has to confirm demand. A related keyword the brief
    surfaced is not eligible — it never went through the queue's reach check. The chosen
    keyword keeps the queue's spelling (Search Ad strips the spaces).

    Returns ``(keyword, reasoning)``; the reasoning is recorded so a week's choice can be
    argued with rather than believed. Refusing (``None``) when nothing qualifies is a real
    outcome — a fire that drafts against a keyword the rule excluded would be worse than a
    fire that reports it found none. The competition columns ride along in ``considered``
    for the operator and gate nothing (see the note above ``PUBLISHED_ROOT_ENV``).

    ``covered_by`` replaces the "already written" test for a platform with its own overlap
    policy (Tistory: `blog_overlap.blocks_selection`); without it the rule is the lane's original
    one over ``already_written``/``already_tagged``.
    """
    considered: list[dict[str, Any]] = []
    best: tuple[tuple[float, int], Mapping[str, Any], str] | None = None
    by_key = {naver_research.normalize_keyword(c.keyword): c for c in queue or ()}
    measured: set[str] = set()
    for row in metrics:
        keyword = str(row.get("keyword") or "").strip()
        if not keyword:
            continue
        total = row.get("monthly_total")
        total = int(total) if isinstance(total, (int, float)) else 0
        reason = None
        candidate = by_key.get(naver_research.normalize_keyword(keyword))
        covered = (covered_by(keyword) if covered_by is not None
                   else blog_overlap.covering_keyword(keyword, already_written, already_tagged))
        if queue is not None and candidate is None:
            reason = "not a queue candidate (never passed the queue's reach check)"
        elif covered is not None:
            reason = f"already written ({covered})"
        elif row.get("low_volume"):
            reason = "volume below the venue's reporting floor"
        elif total <= 0:
            reason = "no measured demand"
        entry: dict[str, Any] = {
            "keyword": keyword,
            "monthly_total": total,
            "ad_competition": str(row.get("competition") or "unknown"),
            "low_volume": bool(row.get("low_volume")),
            "excluded_because": reason,
        }
        if isinstance(row.get("competing_posts"), int):
            entry["blog_competing_posts"] = row["competing_posts"]
        if candidate is not None:
            entry["queue_score"] = candidate.score
            measured.add(naver_research.normalize_keyword(candidate.keyword))
        considered.append(entry)
        rank = (candidate.score if candidate is not None else 0.0, total)
        if reason is None and (best is None or rank > best[0]):
            best = (rank, row, candidate.keyword if candidate is not None else keyword)
    for key, candidate in by_key.items():
        if key not in measured:
            considered.append({"keyword": candidate.keyword, "monthly_total": 0,
                               "ad_competition": "unknown", "low_volume": False,
                               "queue_score": candidate.score,
                               "excluded_because": "no row for it in this brief"})
    if queue is not None:
        # Queue rows first, in score order, so the record reads as the candidate list it was.
        considered.sort(key=lambda e: -float(e.get("queue_score", -1)))
    reasoning = {
        "rule": ("highest vault-queue score among queue candidates with measured demand, not "
                 "already written (the queue's reach check stands in for blog difficulty)"
                 if queue is not None else
                 "highest measured monthly demand among keywords not already written "
                 "(ad competition and blog post counts are recorded, not gated)"),
        "considered": considered[:MAX_TAGS],
    }
    if best is None:
        return None, reasoning
    return str(best[2]).strip(), reasoning


def package_platform(record: Mapping[str, Any]) -> str:
    """The platform a package row is for. v0.1/v0.2 rows carry no `platform`: every one of them
    was a Naver package (the only platform the lane had), so that is what they are read as."""
    return str(record.get("platform") or blog_platform.DEFAULT_PLATFORM)


def written_keywords(
    ledger: Any, published: PublishedKeywords | None = None, *, platform: str | None = None,
) -> list[str]:
    """Every keyword already used: ledger packages (archives included) UNION published posts.

    What :func:`select_target_keyword` excludes as "already written". The ledger half alone was
    true only inside the lane, which has never produced a package — every post so far (69 Naver,
    77 Tistory by 2026-09-28) was written in the vault, so a ledger-only answer was "nothing"
    and a revived lane would have re-picked published keywords. Drafted counts, not only
    published; ``target=`` still forces one. Archives are read because packages are records,
    and records rotate.

    ``platform`` narrows both halves to one platform's posts and packages (None = every
    platform, the Naver rule's reading)."""
    if published is not None and platform is not None:
        published = published.for_platform(platform)
    keywords: set[str] = set(published.keywords) if published is not None else set()
    if ledger is None:
        return sorted(keywords)
    for row in ledger.iter_records_with_archive(kinds=[PACKAGE_RECORD_KIND]):
        if not isinstance(row, Mapping) or row.get("kind") != PACKAGE_RECORD_KIND:
            continue
        record = row.get("record")
        if not isinstance(record, Mapping):
            continue
        keyword = str(record.get("target_keyword") or "").strip()
        if keyword and (platform is None or package_platform(record) == platform):
            keywords.add(keyword)
    return sorted(keywords)


def existing_content(ledger: Any, published: PublishedKeywords | None) -> list[blog_overlap.ExistingContent]:
    """Everything already written, on both platforms, as the overlap policy reads it: every
    keyword of every vault post, and every ledger package's target."""
    items = blog_overlap.from_published_posts(published.posts if published is not None else ())
    if ledger is None:
        return items
    for row in ledger.iter_records_with_archive(kinds=[PACKAGE_RECORD_KIND]):
        record = row.get("record") if isinstance(row, Mapping) and row.get("kind") == PACKAGE_RECORD_KIND else None
        if isinstance(record, Mapping) and str(record.get("target_keyword") or "").strip():
            items.append(blog_overlap.ExistingContent(
                platform=package_platform(record), keyword=str(record["target_keyword"]),
                title=(record.get("title_candidates") or [None])[0], ref=record.get("package_id"),
                origin="ledger"))
    return items


@dataclass(frozen=True)
class QueueCandidate:
    keyword: str
    score: float
    lane: str


@dataclass(frozen=True)
class KeywordQueue:
    as_of: str
    candidates: tuple[QueueCandidate, ...]


_QUEUE_TITLE_RE = re.compile(r"^#\s+키워드 큐\s+—\s+(?P<date>\d{4}-\d{2}-\d{2})\s*$", re.M)
_QUEUE_LANE_RE = re.compile(r"^##\s+(?P<lane>.+?)\s+—\s+다음 편 후보\s*$")
_QUEUE_ITEM_RE = re.compile(
    r"^\d+\.\s+\*\*(?P<keyword>[^*]+?)\*\*\s+—\s+점수\s+(?P<score>[\d,]+(?:\.\d+)?)")


class VaultKeywordQueue:
    """The vault keyword pipeline's queue, read as the lane's seed source.

    Only the "<lane> — 다음 편 후보" sections count. Those candidates already passed
    `kw_pipeline`'s reach gate (top-ten median daily visitors <= 250, exact-title matches <= 9),
    the blog-SERP check this runtime cannot make for itself. The queue's other sections are
    deliberately not seeds: "지금 체급 밖" is the gate's own rejects, "보유 키워드의 변형"
    and "시즌 임박" point at posts that already exist, and "내 글 순위" is a rank report.

    Fails closed: a missing or unparseable queue is ``KEYWORD_QUEUE_UNAVAILABLE``, one older
    than ``QUEUE_MAX_AGE_DAYS`` is ``KEYWORD_QUEUE_STALE``.
    """

    def __init__(self, root: Path | str):
        self.root = Path(root)

    def load(self, now: str) -> KeywordQueue:
        path = self.root / QUEUE_REL
        if not path.is_file():
            raise ToolError(KEYWORD_QUEUE_UNAVAILABLE, f"no keyword queue at {path}")
        text = path.read_text(encoding="utf-8", errors="replace")
        title = _QUEUE_TITLE_RE.search(text)
        if title is None:
            raise ToolError(KEYWORD_QUEUE_UNAVAILABLE,
                            f"{path} has no '# 키워드 큐 — YYYY-MM-DD' title; not a queue")
        as_of = title.group("date")
        age = (timeutil.parse_iso(now).date() - datetime.date.fromisoformat(as_of)).days
        if age > QUEUE_MAX_AGE_DAYS:
            raise ToolError(KEYWORD_QUEUE_STALE,
                            f"the keyword queue is from {as_of}, {age} days old "
                            f"(limit {QUEUE_MAX_AGE_DAYS}); rebuild it with kw_pipeline run")
        candidates: list[QueueCandidate] = []
        lane: str | None = None
        for line in text.splitlines():
            if line.startswith("## "):
                heading = _QUEUE_LANE_RE.match(line)
                lane = heading.group("lane").strip() if heading else None
                continue
            if lane is None:
                continue
            item = _QUEUE_ITEM_RE.match(line.strip())
            if item:
                candidates.append(QueueCandidate(
                    keyword=item.group("keyword").strip(),
                    score=float(item.group("score").replace(",", "")), lane=lane))
        if not candidates:
            raise ToolError(KEYWORD_QUEUE_UNAVAILABLE, f"{path} lists no '다음 편 후보' candidates")
        return KeywordQueue(as_of=as_of, candidates=tuple(candidates))


def queue_seeds(
    queue: KeywordQueue, *, already_written: Sequence[str] = (), already_tagged: Sequence[str] = (),
    limit: int = MAX_QUEUE_SEEDS, covered_by: Callable[[str], str | None] | None = None,
) -> tuple[list[QueueCandidate], list[str]]:
    """``(seeds, skipped)`` — the highest-scored queue candidates not already written, at most
    ``limit`` (one Search Ad call), across every lane; ``skipped`` names the written ones.

    The queue is rebuilt weekly but posts are written daily, so a candidate can be published
    between the rebuild and the fire (kw_pipeline's own `--max-age` reuse has the same check)."""
    ranked = sorted(queue.candidates, key=lambda c: -c.score)
    seeds: list[QueueCandidate] = []
    skipped: list[str] = []
    seen: set[str] = set()
    for candidate in ranked:
        key = naver_research.normalize_keyword(candidate.keyword)
        if key in seen:
            continue
        seen.add(key)
        covered = (covered_by(candidate.keyword) if covered_by is not None else
                   blog_overlap.covering_keyword(candidate.keyword, already_written, already_tagged))
        if covered is not None:
            skipped.append(candidate.keyword)
            continue
        if len(seeds) < limit:
            seeds.append(candidate)
    return seeds, skipped


def select_published_source(inputs: Mapping[str, Any] | None = None) -> VaultPublishedKeywordSource | None:
    """The configured published-post source, or None. No host path is written into the code:
    the root comes from the fire's inputs or from ``MVP_BLOG_PUBLISHED_ROOT``."""
    root = (inputs or {}).get("published_root") or os.environ.get(PUBLISHED_ROOT_ENV, "").strip()
    return VaultPublishedKeywordSource(root) if root else None


def package_schema_path(version: str, root: Path | None = None) -> Path:
    """The closed schema a package row of ``version`` validates against. Fails closed on a
    version this module does not know."""
    if version not in PACKAGE_SCHEMA_VERSIONS:
        raise ToolError(BLOG_PACKAGE_SCHEMA_INVALID, f"unknown package schema_version {version!r}")
    from .paths import repo_root as _repo_root
    return (root if root is not None else _repo_root()) / "schemas" / f"{version}.schema.json"


def _ad_competition(value: Any) -> str:
    text = str(value or "").strip()
    return text if text in _AD_COMPETITION_LEVELS else "unknown"


def _degraded_code(record: Mapping[str, Any]) -> str | None:
    legs = record.get("degraded_legs") or {}
    if legs:
        return f"KEYWORD_BRIEF_DEGRADED:{','.join(sorted(legs))}"
    code = record.get("degraded_reason_code")
    return str(code) if code else None


def selection_evidence(
    record: Mapping[str, Any] | None,
    reasoning: Mapping[str, Any],
    *,
    selected_keyword: str,
    mode: str,
    seeds: Sequence[str],
    now: str,
    seed_source: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """How the target was chosen: the selection brief's candidates and the rule's verdict on
    each. Nothing here describes the target's own numbers — that is :func:`target_evidence`."""
    evidence: dict[str, Any] = {
        "mode": mode,
        "rule": str(reasoning.get("rule") or "operator override")[:300],
        "selected_keyword": selected_keyword,
        "seeds": [str(s) for s in seeds][:30],
        "as_of": str((record or {}).get("created_at") or now),
        "degraded": bool((record or {}).get("degraded")) if record is not None else mode == "rule",
        "candidates": [],
    }
    if isinstance(record, Mapping):
        code = _degraded_code(record)
        if evidence["degraded"] and code:
            evidence["degraded_reason_code"] = code[:80]
    for entry in (reasoning.get("considered") or [])[:MAX_METRICS]:
        candidate = {
            "keyword": str(entry.get("keyword")),
            "monthly_total": int(entry.get("monthly_total") or 0),
            "ad_competition": _ad_competition(entry.get("ad_competition")),
            "low_volume": bool(entry.get("low_volume")),
            "excluded_because": (str(entry["excluded_because"])[:300]
                                 if entry.get("excluded_because") else None),
        }
        if isinstance(entry.get("blog_competing_posts"), int):
            candidate["blog_competing_posts"] = entry["blog_competing_posts"]
        if isinstance(entry.get("queue_score"), (int, float)):
            candidate["queue_score"] = float(entry["queue_score"])
        evidence["candidates"].append(candidate)
    if seed_source:
        evidence["seed_source"] = dict(seed_source)
    return evidence


def target_evidence(target: str, record: Mapping[str, Any] | None, *, now: str) -> dict[str, Any]:
    """The target's own numbers, from the brief run on the target — or an explicit absence.

    Every number is bound to ``target`` by normalized equality (Search Ad hands the row back
    space-stripped and upper-cased). The failures this replaced all borrowed another keyword's
    number: ``metrics[0]`` (the top-volume row), a SUM of three keywords' post counts, the first
    seed's trend. So when the target's row is not in the brief the status is ``missing`` and
    the numbers are absent — never zero, never the neighbour's.
    """
    want = naver_research.normalize_keyword(target)
    evidence: dict[str, Any] = {"keyword": target, "status": "missing",
                                "as_of": now, "degraded": True}
    if not isinstance(record, Mapping):
        evidence["degraded_reason_code"] = "TARGET_BRIEF_ABSENT"
        return evidence
    evidence["as_of"] = str(record.get("created_at") or now)
    row = next((r for r in (record.get("metrics") or [])
                if isinstance(r, Mapping) and naver_research.normalize_keyword(r.get("keyword")) == want),
               None)
    reasons: list[str] = []
    code = _degraded_code(record) if record.get("degraded") else None
    if code:
        reasons.append(code)
    counts = ("monthly_pc", "monthly_mobile", "monthly_total")
    if row is not None and not all(
            isinstance(row.get(k), int) and not isinstance(row.get(k), bool) for k in counts):
        # A matching row without its counts is not a measurement; reading the gaps as 0 would
        # claim "no demand" for a keyword nobody measured.
        reasons.insert(0, "TARGET_ROW_INCOMPLETE")
        row = None
    elif row is None:
        reasons.insert(0, "TARGET_ROW_ABSENT")
    if row is not None:
        evidence["status"] = "measured"
        evidence.update({
            "matched_keyword": str(row.get("keyword")),
            "monthly_pc": row["monthly_pc"],
            "monthly_mobile": row["monthly_mobile"],
            "monthly_total": row["monthly_total"],
            "low_volume": bool(row.get("low_volume")),
            "ad_competition": _ad_competition(row.get("competition")),
            "volume_source": str(row.get("source") or "unknown"),
        })
        if isinstance(row.get("competing_posts"), int):
            evidence["blog_competing_posts"] = row["competing_posts"]
            evidence["blog_competing_posts_query"] = str(row.get("competing_posts_query") or target)
        else:
            reasons.append("TARGET_BLOG_COUNT_ABSENT")
    trend_keyword = record.get("trend_keyword")
    points = record.get("trend_points") or []
    if points and naver_research.normalize_keyword(trend_keyword) == want:
        evidence["trend_keyword"] = str(trend_keyword)
        evidence["trend_points"] = [dict(p) for p in points][:60]
    else:
        reasons.append("TARGET_TREND_ABSENT")
    evidence["degraded"] = bool(reasons)
    if reasons:
        evidence["degraded_reason_code"] = ";".join(reasons)[:120]
    return evidence


def _trace_id(result: Mapping[str, Any] | None) -> str | None:
    """The trace id of a governed run's task, or None when there was no such run."""
    records = (result or {}).get("records") or {}
    for kind in ("task", "received_task"):
        trace = ((records.get(kind) or {}).get("identity") or {}).get("trace_id")
        if trace:
            return str(trace)
    return None


def interpret_draft(
    text: str, target_keyword: str, records: Mapping[str, Any] | None = None, *,
    profile: blog_platform.PlatformProfile | None = None,
    context: blog_prompt.DraftContext | None = None,
) -> dict[str, Any]:
    """One model answer as the package's parts, its measurement and what it fails — read by the
    platform's own interpreter (`blog_naver.interpret`, `blog_tistory.interpret`). Pure.

    ``records`` is the run's own record set: sources and fact-check references resolve against
    the evidence THAT run had, never another run's."""
    profile = profile or blog_platform.resolve(None)
    return profile.interpret(text, target_keyword, records, context)


def prompt_lineage(
    profile: blog_platform.PlatformProfile, *, request: str | None, revision: str | None = None,
) -> dict[str, Any]:
    """Which prompt, profile, standards and schema produced a package — the versions, and the
    digest of each request actually sent, so two packages' drafts can be compared knowing
    whether they were asked the same thing (a version names the intent, a digest the bytes)."""
    def digest(text: str | None) -> str | None:
        return hashlib.sha256(text.encode("utf-8")).hexdigest() if text is not None else None

    return {
        "platform": profile.name,
        "prompt_version": profile.prompt_version,
        "platform_profile_version": profile.profile_version,
        "quality_standard_version": profile.standards_version,
        "schema_version": PACKAGE_SCHEMA_VERSION,
        "request_sha256": digest(request),
        "revision_request_sha256": digest(revision),
    }


def _unread_overlap(target: str, platform: str) -> dict[str, Any]:
    return blog_overlap.assess(target, platform, (), source_state="unavailable")


def build_package(
    *,
    target_keyword: str,
    draft: Mapping[str, Any],
    selection: Mapping[str, Any],
    target: Mapping[str, Any],
    lineage: Mapping[str, Any],
    quality: Mapping[str, Any],
    now: str,
    profile: blog_platform.PlatformProfile | None = None,
    overlap: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble one `blog_content_package.v0.3` from an :func:`interpret_draft` result. Pure —
    no ledger, no clock, no I/O.

    ``title_candidates`` has minItems 1 in the schema, and a draft that produced no title is
    still a package worth reviewing — so the target keyword stands in, and the quality record
    already names `title_candidates` as a failure (the package is `needs_edit`).

    The common fields are v0.2's, unchanged; v0.3 adds what makes the package platform-aware:
    ``platform``, ``content_intent``, ``platform_metadata`` (one key, the platform's), the
    ``overlap`` record, and ``lineage.prompt`` (:func:`prompt_lineage`)."""
    profile = profile or blog_platform.resolve(None)
    titles = list(draft.get("title_candidates") or []) or [target_keyword]
    body = str(draft.get("body_paste") or "") or target_keyword
    brief = draft.get("content_brief") or {}
    lineage = dict(lineage)
    lineage.setdefault("prompt", prompt_lineage(profile, request=None))
    if profile.name == blog_platform.DEFAULT_PLATFORM:
        metadata: dict[str, Any] = {"paste_format": profile.paste_format, "editor": "smarteditor_one"}
    else:
        metadata = dict(draft.get("platform_metadata") or {})
        metadata.pop("seo_title_problems", None)
        metadata["brief"] = dict(brief)
    package: dict[str, Any] = {
        "schema_version": PACKAGE_SCHEMA_VERSION,
        "created_at_utc": now,
        "platform": profile.name,
        "target_keyword": target_keyword,
        "content_intent": {"keyword_intent": blog_overlap.keyword_intent(target_keyword),
                           "declared_intent": brief.get("search_intent")},
        "selection_evidence": dict(selection),
        "target_evidence": dict(target),
        "lineage": lineage,
        "title_candidates": titles[:MAX_TITLES],
        "body_paste": body,
        "body_blocks": list(draft.get("body_blocks") or [])[:MAX_BODY_BLOCKS],
        "tags": list(draft.get("tags") or [])[:MAX_TAGS],
        "image_shots": list(draft.get("image_shots") or [])[:MAX_IMAGE_SHOTS],
        "sources": list(draft.get("sources") or []),
        "fact_checks": list(draft.get("fact_checks") or [])[:MAX_FACT_CHECKS],
        "quality": dict(quality),
        "overlap": dict(overlap) if overlap is not None else _unread_overlap(target_keyword, profile.name),
        "platform_metadata": {profile.name: metadata},
        "publish_state": "draft",
    }
    seed = {"target_keyword": target_keyword, "body_paste": package["body_paste"], "created_at_utc": now}
    if profile.name != blog_platform.DEFAULT_PLATFORM:
        # Naver ids keep their seed; another platform's package of the same keyword at the same
        # minute must not collide with it.
        seed["platform"] = profile.name
    package["package_id"] = integrity.short_id("bcp", seed)
    return package


def quality_record(
    final: Mapping[str, Any],
    *,
    first_failures: Sequence[str],
    revision_count: int,
    revision_outcome: str | None,
    revision_detail: str | None = None,
    profile: blog_platform.PlatformProfile | None = None,
    layers: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """What the package's reviewer needs to know about the draft's quality, in one place.

    `ready_for_review` only when the final draft cleared every critical standard AND the
    structural contract (three titles, structured output) and the keyword's floor; otherwise `needs_edit`. Neither
    state publishes anything — both are a draft waiting for a human.

    ``layers`` (`blog_quality.layers`) rides beside the gate and never moves it: the state is
    still decided by ``failures`` alone."""
    profile = profile or blog_platform.resolve(None)
    record: dict[str, Any] = {
        "standards_version": profile.standards_version,
        "draft_format": final["draft_format"],
        "quality_state": "ready_for_review" if not final["failures"] else "needs_edit",
        "critical_pass": not blog_draft_score.critical_failures(dict(final["measured"]), dict(profile.standards)),
        "failures": list(final["failures"]),
        "first_draft_failures": list(first_failures),
        "revision_count": int(revision_count),
        "measured": {k: int(v) for k, v in final["measured"].items()},
    }
    if revision_outcome:
        record["revision_outcome"] = revision_outcome[:120]
    if revision_detail:
        record["revision_detail"] = revision_detail[:300]
    if int(final.get("brackets_inserted") or 0) > 0:
        record["brackets_inserted"] = int(final["brackets_inserted"])
    record["layers"] = dict(layers) if layers is not None else blog_quality.layers(
        final, target=str(final.get("target_keyword") or ""), target_evidence={},
        platform_checks=[])
    return record


def render_paste_txt(package: Mapping[str, Any]) -> str:
    """The editor paste, verbatim: `PASTE.txt` for Naver — zero formatting symbols, paragraph
    breaks only: SmartEditor ONE renders no markdown, so anything beyond plain text would land in
    the published post as literal characters (proposal §4b's founding observation) — and
    `PASTE.md` for Tistory, whose markdown the runtime itself wrote (`blog_tistory.render_markdown`)."""
    body = str(package.get("body_paste") or "")
    return body if body.endswith("\n") else body + "\n"


def render_post_md(package: Mapping[str, Any]) -> str:
    """`POST.md` — the operator's single reading file, per the §4b table: evidence, titles,
    body, edit directives, capture directives, tags, pre-publish checks. A rendering of the
    package record, never an authority of its own."""
    platform = package_platform(package)
    profile = blog_platform.PROFILES.get(platform)
    lines: list[str] = [f"# {package.get('target_keyword')}", ""]
    if package.get("platform"):
        lines += [f"- 플랫폼: {platform} · 붙여넣기 파일: {profile.paste_file if profile else '-'}", ""]
    lines += ["## 타깃 키워드 근거 (이 키워드 자체를 조사한 값)"]
    lines += _render_target_evidence(package.get("target_evidence") or {})
    lines += ["", "## 선정 근거 (후보 비교 — 아래 숫자는 각 후보의 것)"]
    lines += _render_selection_evidence(package.get("selection_evidence") or {})

    quality = package.get("quality") or {}
    if quality:
        state = quality.get("quality_state")
        lines += ["", f"## 품질 상태: {state}"]
        lines += [f"- 기준: {quality.get('standards_version')} · 초안 형식: {quality.get('draft_format')}"
                  f" · 자동 수정 {quality.get('revision_count')}회"
                  + (f" ({quality['revision_outcome']})" if quality.get("revision_outcome") else "")
                  + (f" · 빠진 괄호 {quality['brackets_inserted']}개 보정"
                     if quality.get("brackets_inserted") else "")]
        if quality.get("revision_detail"):
            label = ("자동 수정이 막힌 이유"
                     if str(quality.get("revision_outcome") or "").startswith("REVISION_BLOCKED")
                     else "자동 수정 메모")
            lines += [f"- {label}: {quality['revision_detail']}"]
        if quality.get("failures"):
            lines += [f"- ⚠ 남은 미달 항목: {', '.join(quality['failures'])} — 발행 전 직접 손볼 것"]
        # Advisory only (see `blog_draft.ECHO_OVERLAP`): read from the body itself, never gating.
        body_headings = {b.get("paragraph_index") for b in package.get("body_blocks") or []
                         if b.get("action") == "heading"}
        prose = [p for i, p in enumerate(str(package.get("body_paste") or "").split("\n\n"))
                 if i not in body_headings]
        # Thin evidence, pointed at: over 28 scored drafts on 2026-09-30, every one citing fewer
        # than two web sources scored 58 or under ('망고보드 ai', one official home page, read as
        # generalities). Two or more ranged 45~72, so the count is a warning, never a gate.
        web = [s for s in package.get("sources") or [] if str(s.get("source_ref") or "").startswith("[S")]
        if len(web) < blog_quality.MIN_WEB_SOURCES:
            lines += [f"- ⚠ 근거가 적음: 인용된 웹 출처 {len(web)}개 — 메뉴 이름·수치 같은 구체 내용이 "
                      "부족할 수 있으니 공식 도움말·안내 페이지로 보강할 것(자동 판정 아님)"]
        sites = blog_draft.site_names_in_body(package.get("sources") or [], prose,
                                              str(package.get("target_keyword") or ""))
        if sites:
            lines += [f"- 본문에 출처 사이트 이름이 나옴: {', '.join(f'「{n}」' for n in sites)} — 제작·판매 "
                      "업체라면 '업체 A'처럼 익명으로 바꾸고, 앱·도구라면 그대로 둘 것(자동 판정 아님)"]
        echoes = blog_draft.echo_sentences(prose)
        if echoes:
            named = ", ".join(f"「{e[:50]}」" for e in echoes[:5])
            lines += [f"- 앞 문장을 다른 말로 되풀이한 듯한 문장 {len(echoes)}개(자동 판정 아님 — 읽고 "
                      f"지우거나 새 정보로 바꿀 것): {named}"]
        lines += _render_layers(quality.get("layers") or {})
    lines += _render_overlap(package.get("overlap") or {})
    lines += _render_tistory_metadata((package.get("platform_metadata") or {}).get("tistory") or {})

    lines += ["", "## 제목 후보 (소제목과 별개)"]
    lines += [f"{i}. {t}" for i, t in enumerate(package.get("title_candidates") or [], start=1)]

    headings = {b.get("paragraph_index") for b in package.get("body_blocks") or []
                if b.get("action") == "heading"}
    paragraphs = str(package.get("body_paste") or "").split("\n\n")
    if profile is not None and profile.paste_format == "markdown":
        lines += ["", f"## 본문 ({profile.paste_file}와 같은 마크다운)", ""]
        lines += paragraphs
    else:
        lines += ["", "## 본문 (소제목 표시는 이 파일에만 — PASTE.txt는 기호 없음)", ""]
        lines += [f"### {p}" if i in headings else p for i, p in enumerate(paragraphs)]

    shots = package.get("image_shots") or []
    if shots:
        lines += ["", "## 캡처 지시 (이미지 생성이 아니라 실제 화면 캡처)"]
        lines += [f"- {s.get('after_paragraph')}번째 문단 뒤: {s.get('what_to_capture')}"
                  + (f" ({s['tool_name']})" if s.get("tool_name") else "")
                  + (f" — alt: {s['alt_text']}" if s.get("alt_text") else "") for s in shots]

    tags = package.get("tags") or []
    if tags:
        lines += ["", "## 태그", " ".join(f"#{t}" for t in tags)]

    sources = package.get("sources") or []
    lines += ["", "## 출처 (이 실행의 근거로 확인된 것만)"]
    lines += ([f"- {s.get('source_ref')} {s.get('title') or ''} {s.get('url') or ''}".rstrip()
               for s in sources] or ["- (확인된 출처 없음)"])

    checks = package.get("fact_checks") or []
    lines += ["", "## 발행 전 확인 (자동 판정 아님 — 사람이 1차 출처로 확인)"]
    if checks:
        for c in checks:
            state = c.get("verification_state", "needs_manual_verification")
            ref = f" · 근거 {c['source_ref']}" if c.get("source_ref") else ""
            lines.append(f"- [ ] {c.get('claim')} — {c.get('why')} ({state}{ref})")
    else:
        lines += ["- (표시된 변동 정보 문장 없음 — 가격·무료범위·기능 문장이 있는지 직접 볼 것)"]

    lines += ["", f"---", f"package_id: {package.get('package_id')} · publish 후: "
              f"`python -m scripts.record_published_url --package-id {package.get('package_id')} --url <URL>`"]
    return "\n".join(lines) + "\n"


_STATE_MARK = {"ok": "✓", "pass": "✓", "warn": "⚠", "thin": "⚠", "degraded": "⚠", "fail": "✗",
               "not_measured": "–"}


def _render_layers(layers: Mapping[str, Any]) -> list[str]:
    """The four quality layers, each with its own state — no single score. Only what needs a
    look is itemised; the rest is a count."""
    if not layers:
        return []
    structural = layers.get("structural") or {}
    semantic = (layers.get("semantic") or {}).get("signals") or {}
    evidence = layers.get("evidence") or {}
    fit = layers.get("platform_fit") or {}
    lines = ["", "## 품질 레이어 (참고용 — 검토 상태는 위 미달 항목만으로 정해진다)",
             f"- 구조(structural): {_STATE_MARK.get(structural.get('state'), '?')} {structural.get('state')}",
             f"- 근거(evidence): {_STATE_MARK.get(evidence.get('state'), '?')} {evidence.get('state')} — 웹 출처 "
             f"{evidence.get('web_sources')}개, 키워드 행 {evidence.get('keyword_rows_cited')}개, 확인 대상 문장 "
             f"{evidence.get('fact_checks')}개 중 출처 연결 {evidence.get('fact_checks_source_cited')}개"]
    measured = [n for n, sig in semantic.items() if sig.get("state") != "not_measured"]
    lines.append(f"- 의미(semantic, {(layers.get('semantic') or {}).get('method')}): 측정 {len(measured)}개, "
                 f"측정 안 함 {len(semantic) - len(measured)}개(판단이 필요한 항목)")
    for name, sig in semantic.items():
        if sig.get("state") == "warn":
            lines.append(f"  - ⚠ {name}: {sig.get('note')}")
    lines.append(f"- 플랫폼 적합(platform_fit): {_STATE_MARK.get(fit.get('state'), '?')} {fit.get('state')}")
    for check in fit.get("checks") or []:
        if check.get("state") in ("warn", "fail"):
            lines.append(f"  - {_STATE_MARK[check['state']]} {check.get('check')}: {check.get('detail')}")
    return lines


def _render_overlap(overlap: Mapping[str, Any]) -> list[str]:
    decision = overlap.get("decision") or {}
    if not overlap:
        return []
    lines = ["", "## 기존 글과의 겹침"]
    if overlap.get("source_state") != "measured":
        return lines + ["- 기존 글 목록을 읽지 못했다 — 겹침은 판정되지 않음(겹침 없음이 아님)"]
    lines.append(f"- 판정: {decision.get('overlap_type') or '없음'} → {decision.get('action')} — "
                 f"{decision.get('reason')}")
    for match in (overlap.get("matches") or [])[:5]:
        lines.append(f"  - {match.get('existing_platform')}: {match.get('existing_keyword')}"
                     + (f" 「{match['existing_title']}」" if match.get("existing_title") else "")
                     + f" — {match.get('overlap_type')} ({match.get('action')})")
    return lines


def _render_tistory_metadata(meta: Mapping[str, Any]) -> list[str]:
    if not meta:
        return []
    brief = meta.get("brief") or {}
    lines = ["", "## 티스토리 발행 정보",
             f"- SEO 제목: {meta.get('seo_title')}",
             f"- 슬러그: {meta.get('slug') or '(없음 — 직접 정할 것)'}",
             f"- 요약(excerpt): {meta.get('excerpt') or '(없음)'}",
             "- 검색 결과 요약(티스토리에는 메타 디스크립션 칸이 없어 본문 첫 400자가 쓰인다): "
             f"{str(meta.get('meta_description') or '')[:160]}…"]
    links = meta.get("internal_links") or []
    lines.append(f"- 내부 링크 {len(links)}개 (후보 {meta.get('internal_link_candidates')}개, "
                 f"후보 출처 {meta.get('internal_link_source_state')})"
                 + "".join(f"\n  - {link['anchor_text']} → {link['url']}" for link in links))
    if brief:
        lines += [f"- 검색 의도: {brief.get('search_intent') or '-'} · 독자: {brief.get('audience') or '-'}",
                  f"- 관점: {brief.get('article_angle') or '-'}"]
        if brief.get("user_questions"):
            lines.append("- 검색자 질문: " + " / ".join(brief["user_questions"]))
        if brief.get("secondary_keywords"):
            lines.append("- 보조 키워드: " + ", ".join(
                k["keyword"] + ("" if k.get("measured") else "(미측정)") for k in brief["secondary_keywords"]))
    return lines


def _render_target_evidence(evidence: Mapping[str, Any]) -> list[str]:
    """The target's numbers, each labelled with the keyword it belongs to — or the absence,
    said out loud. Never a neighbouring row's number standing in."""
    lines = [f"- 타깃 키워드: {evidence.get('keyword')}"]
    if evidence.get("status") != "measured":
        lines.append("- ⚠ 타깃 키워드 자체의 검색량 행이 조사 결과에 없다 — 검색량·경쟁 수치 없음"
                     " (다른 키워드 수치로 대신하지 않음)")
    else:
        mock = str(evidence.get("volume_source") or "").startswith("mock.")
        lines.append(
            f"- 월간검색수 ({evidence.get('matched_keyword')}): PC {evidence.get('monthly_pc')} / "
            f"모바일 {evidence.get('monthly_mobile')} (합 {evidence.get('monthly_total')}, "
            f"출처: {evidence.get('volume_source')})"
            + (" — 목업 데이터, 실측 아님" if mock else ""))
        lines.append(f"- 광고 입찰 경쟁도(검색광고 compIdx, 블로그 난이도 아님): "
                     f"{evidence.get('ad_competition')}")
        if "blog_competing_posts" in evidence:
            lines.append(f"- 블로그 문서수 ('{evidence.get('blog_competing_posts_query')}' 검색): "
                         f"{evidence['blog_competing_posts']}")
        else:
            lines.append("- 블로그 문서수: 조회 실패 — 값 없음")
    if evidence.get("trend_points"):
        points = evidence["trend_points"]
        lines.append(f"- 추세 ({evidence.get('trend_keyword')}, 창 안 최고=100): "
                     + ", ".join(f"{p.get('period')} {p.get('ratio')}" for p in points[-6:]))
    else:
        lines.append("- 추세: 타깃 키워드의 추세 없음")
    lines.append(f"- 근거 시점: {evidence.get('as_of')}")
    if evidence.get("degraded"):
        lines.append(f"- ⚠ degraded: {evidence.get('degraded_reason_code')} — 숫자를 재확인할 것")
    return lines


def _render_selection_evidence(evidence: Mapping[str, Any]) -> list[str]:
    if evidence.get("mode") == "operator_override":
        lines = ["- 운영자 지정(target=) — 규칙 선정 아님"]
    else:
        lines = [f"- 규칙: {evidence.get('rule')}"]
    for c in (evidence.get("candidates") or [])[:10]:
        # Normalized: the queue spells '사업자등록증 발급', Search Ad '사업자등록증발급' — the
        # first package printed its own choice as a plain candidate.
        chosen = naver_research.normalize_keyword(c.get("keyword")) == naver_research.normalize_keyword(
            evidence.get("selected_keyword"))
        verdict = "선정" if chosen else (
            c.get("excluded_because") or "후보")
        posts = (f", 블로그 문서 {c['blog_competing_posts']}"
                 if "blog_competing_posts" in c else "")
        lines.append(f"  - {c.get('keyword')}: 월 {c.get('monthly_total')}, 광고경쟁 "
                     f"{c.get('ad_competition')}{posts} — {verdict}")
    return lines


def package_dir(package: Mapping[str, Any]) -> str:
    """`blog/<날짜>-<슬러그>-<id끝4>` — §4b's shape, with the package-id suffix making the
    create-only write collision-free when one keyword produces two packages."""
    slug = re.sub(r"[\\/:*?\"<>|\s]+", "-", str(package.get("target_keyword") or "")).strip("-")
    name = f"{str(package.get('created_at_utc'))[:10]}-{slug}-{str(package.get('package_id'))[-4:]}"
    platform = package_platform(package)
    # Naver keeps the folder it always had; another platform's packages get their own.
    return f"blog/{name}" if platform == blog_platform.DEFAULT_PLATFORM else f"blog/{platform}/{name}"


def _write_package_files(
    package: Mapping[str, Any], *, writer: Any, ledger: Any, now: str, repo_root: Path | None,
) -> tuple[bool, list[str], str]:
    """Render the package to `POST.md`/`PASTE.txt` through the governed write — or don't.

    Returns ``(written, files, note)``. The dry-run writer (flag closed) writes nothing and
    says so, which is the lane's pre-decision behavior unchanged. A refused write degrades:
    the record already exists and IS the package, so the sheet reports the refusal instead
    of the fire failing over a rendering.
    """
    from . import workspace as _workspace  # function-local: only the write path needs it

    if writer is None:
        writer = _workspace.select_writer()
    if not getattr(writer, "filesystem_write", False):
        return False, [], "not enabled on this deployment"

    base = package_dir(package)
    files: list[str] = []
    try:
        paste_file = blog_platform.resolve(package_platform(package)).paste_file
        for name, content in (("POST.md", render_post_md(package)),
                              (paste_file, render_paste_txt(package))):
            result, record = _workspace.run_write(
                f"{base}/{name}", content, writer=writer, now=now, root=repo_root,
            )
            if ledger is not None:
                ledger.append_records(package.get("package_id"), {"write_use": record})
            files.append(result.relative_path)
    except MvpRuntimeError as exc:
        code = getattr(exc, "reason_code", type(exc).__name__)
        return False, files, f"write refused: {code} — the package stays a ledger row"
    return True, files, f"workspace/{base}"


def _no_eligible_message(reasoning: Mapping[str, Any]) -> str:
    """Why nothing qualified, counted by reason — the operator's next step depends on which
    (all already written: widen the seeds; all low volume: different seeds; an empty brief:
    the research leg)."""
    considered = reasoning.get("considered") or []
    if not considered:
        return "the keyword brief returned no rows to choose from"
    counts: dict[str, int] = {}
    for entry in considered:
        reason = str(entry.get("excluded_because") or "")
        reason = "already written" if reason.startswith("already written") else reason
        counts[reason] = counts.get(reason, 0) + 1
    detail = ", ".join(f"{reason}: {n}" for reason, n in sorted(counts.items()))
    return f"no keyword had measured demand and was unused; considered {len(considered)} ({detail})"


# The revision request carries the whole first draft back to the model; intake refuses a
# request over `intake.MAX_REQUEST_CHARS` (20,000), so the revision is skipped — and says so —
# rather than truncating the draft it is meant to revise.
MAX_REVISION_REQUEST_CHARS = 19_000

def _miss(parts: Mapping[str, Any], standards: Mapping[str, Any] | None = None) -> tuple[int, float]:
    """How far a draft is from passing, for choosing between the first draft and its revision:
    the contract failures (titles, structure) first, then the critical standards' relative
    distance (:func:`blog_draft_score.shortfall`), under the platform's own ``standards``.
    Smaller is closer."""
    standards = dict(standards) if standards is not None else blog_draft_score.STANDARDS
    contract = [f for f in parts["failures"] if f not in standards]
    return len(contract), blog_draft_score.shortfall(parts["measured"], standards)


def _miss_detail(second: Mapping[str, Any]) -> str:
    """Why the revision was not taken, in the package: what IT failed, with its numbers."""
    measured = second.get("measured") or {}
    missed = ", ".join(f"{f} {measured[f]}" if f in measured else f for f in second["failures"])
    return f"수정본이 기준에서 더 벗어나 첫 초안을 유지함 (수정본 미달: {missed})"[:300]


def _carry_first_evidence(
    first: Mapping[str, Any], second: dict[str, Any],
    profile: blog_platform.PlatformProfile | None = None,
) -> dict[str, Any]:
    """The revised draft, with the evidence the FIRST draft resolved carried over.

    The revision may not change facts, and it ran with no evidence of its own, so its sources
    are necessarily empty and its fact checks unsourced. The first draft's sources did resolve
    against the content run's real evidence and stay true of the same facts. A fact check whose
    claim is unchanged keeps its first-draft state; a claim the revision reworded stays
    `needs_manual_verification`, because the source was checked against the old wording, not
    the new. What else a revision dropped (capture directions, a table, the platform's own
    parts) the profile restores (`PlatformProfile.carry_layout`)."""
    profile = profile or blog_platform.resolve(None)
    carried = dict(second)
    carried["sources"] = list(first.get("sources") or [])
    by_claim = {c["claim"]: c for c in first.get("fact_checks") or []
                if c.get("verification_state") == blog_draft.VERIFICATION_SOURCE_CITED}
    carried["fact_checks"] = [by_claim.get(c["claim"], c) for c in second.get("fact_checks") or []]
    measured = dict(second.get("measured") or {})
    if second.get("draft_format") == blog_draft.DRAFT_FORMAT_STRUCTURED:
        measured["sources"] = len(carried["sources"])
        profile.carry_layout(first, carried, measured)
    carried["measured"] = measured
    return carried


def _draft_text(result: Mapping[str, Any]) -> str:
    """The Role's deliverable itself — `content_draft` — not the rendered reply.

    The rendered `final_response` wraps the draft in a `## Draft` heading and appends the
    run's review sections, and the lane used to parse and SCORE that whole reply as the post.
    It is only the fallback for a run whose agent output did not carry the key."""
    records = result.get("records") or {}
    rso = (records.get("agent_output") or {}).get("role_specific_output") or {}
    draft = rso.get("content_draft")
    if isinstance(draft, str) and draft.strip():
        return draft
    return str(result.get("final_response") or "")


def _run(kind: str, request: str, *, blocked_code: str, **kwargs: Any) -> dict[str, Any]:
    result = run_task(request, request_kind=kind, **kwargs)
    if result.get("status") != "COMPLETED":
        # The inner code AND its message ride along. The first blog fire's revision died as
        # `IDEATION_REVISION_BLOCKED` alone: the pipeline's own reason (PROVIDER_ERROR, and which
        # chain member failed how) was in the run's result and nowhere after it.
        block = result.get("block") or {}
        inner = str(block.get("reason_code") or result.get("status"))
        detail = str(block.get("message") or "")[:300]
        raise ToolError(
            blocked_code,
            f"the {kind} run did not complete: {inner}" + (f" — {detail}" if detail else ""),
            data={"inner_reason_code": inner, "inner_message": detail},
        )
    return result


MAX_LINK_CANDIDATES = 8


def link_candidates(
    target: str, published: PublishedKeywords | None, platform: str,
) -> blog_prompt.DraftContext:
    """The internal-link list for a ``platform`` draft: that platform's published posts with an
    https URL that share a topic word with ``target``, most shared first (path breaks ties, so the
    list is the same every time). An unrelated link is noise to a reader and to a search engine,
    so a post with nothing in common is never offered to fill the list."""
    if published is None:
        return blog_prompt.DraftContext(link_source_state="unavailable")
    want = set(blog_overlap.content_words(target)) | {blog_overlap.topic_core(target)}
    want.discard("")
    scored: list[tuple[int, str, Mapping[str, Any]]] = []
    for post in published.posts:
        # Published only: a scheduled post already has its URL in the vault (`status: scheduled`,
        # `url: …/75`) and a link to it is dead until it goes out — the vault's own rule is to
        # link after publishing. https only, as the package schema requires.
        if (post.get("platform") != platform or post.get("reserved") or post.get("status") != "published"
                or not str(post.get("url") or "").startswith("https://")):
            continue
        words: set[str] = set()
        for text in (post.get("title") or "", *(post.get("keywords") or ()), *(post.get("tags") or ())):
            words |= set(blog_overlap.content_words(text)) | {blog_overlap.topic_core(text)}
        shared = len(want & words)
        if shared:
            scored.append((shared, str(post.get("path")), post))
    scored.sort(key=lambda item: (-item[0], item[1]))
    candidates = tuple(
        blog_prompt.LinkCandidate(ref=f"[L{n}]", title=str(post.get("title") or post.get("path"))[:200],
                                  url=str(post["url"])[:500],
                                  keyword=str((post.get("keywords") or [""])[0]))
        for n, (_shared, _path, post) in enumerate(scored[:MAX_LINK_CANDIDATES], start=1))
    return blog_prompt.DraftContext(link_candidates=candidates, link_source_state="measured")


def _load_published(source: Any, *, required: bool) -> PublishedKeywords | None:
    """The published posts. Rule-based selection cannot run without them (``required``); an
    operator override only loses what they add — the overlap record and the internal links —
    and says so, rather than failing a fire the operator already decided."""
    if source is None:
        if required:
            raise ToolError(
                PUBLISHED_KEYWORD_SOURCE_UNAVAILABLE,
                f"rule-based selection needs the published posts; set {PUBLISHED_ROOT_ENV} "
                "(or pass target= to name the keyword yourself)",
            )
        return None
    if required:
        return source.load()
    try:
        return source.load()
    except (ToolError, OSError):
        return None


def run_content_ideation(
    inputs: Mapping[str, Any],
    *,
    providers: Mapping[str, Any] | None = None,
    ledger: Any = None,
    working_memory: Any = None,
    programization: Any = None,
    now: str,
    repo_root: Path | None = None,
    writer: Any = None,
    published_source: Any = None,
    keyword_queue: Any = None,
) -> dict[str, Any]:
    """Research -> pick -> draft -> score -> package. Returns the sheet the scheduler renders.

    Raises ``ToolError`` when a governed run blocks or no keyword qualifies; the scheduler
    turns that into the fire's status, which is the same shape a blocked data-review fire has.
    ``platform=`` in the request picks the profile (Naver when absent) before anything is spent.
    """
    platform_name, request_rest = parse_platform(str(inputs.get("seeds") or ""))
    profile = blog_platform.resolve(platform_name)
    seeds, target_override, use_queue = parse_request(request_rest)
    if use_queue and seeds:
        raise ToolError(IDEATION_INPUTS_CONFLICT,
                        f"{QUEUE_SOURCE_TOKEN} takes the seeds from the vault queue; drop the "
                        f"fixed seeds ({', '.join(seeds)}) or drop {QUEUE_SOURCE_TOKEN}")
    if not seeds and not target_override and not use_queue:
        raise ToolError("IDEATION_INPUTS_REQUIRED", "no keyword seeds and no target= override")
    resolved = dict(providers or {})
    common: dict[str, Any] = {
        "provider": resolved.get("provider"),
        "validator_provider": resolved.get("validator_provider"),
        "search_tool": resolved.get("search_tool"),
        "working_memory": working_memory,
        "programization": programization,
        "store": ledger,
        "repo_root": repo_root,
        "now": now,
        # The scheduler's own identity, as `pipeline_worker.SCHEDULER_PROFILE` states it: this
        # runs inside the worker on the maintenance lane's behalf, and intake admits no
        # `human` requester type — the first weekly fire would have blocked at intake.
        "requester_id": "mvp.scheduler",
        "requester_type": "scheduler",
        "channel": "scheduler",
        "source_ref": str(inputs.get("source_ref") or "scheduler:content_ideation"),
        "authenticated": True,
    }

    keyword_record: Mapping[str, Any] | None = None
    research: Mapping[str, Any] | None = None
    reasoning: dict[str, Any] = {"rule": "operator override", "considered": []}
    target = target_override
    # Before any research is spent: without the published posts the rule cannot know what
    # was already written outside the lane, and "nothing" is the one wrong answer here.
    source = published_source if published_source is not None else select_published_source(inputs)
    published = _load_published(source, required=target is None)
    # The ledger's packages always count; `source_state` below speaks for the vault alone.
    existing = existing_content(ledger, published)
    extra_written = [str(k) for k in (inputs.get("already_written") or [])]
    covered_by: Callable[[str], str | None] | None = None
    if profile.name != blog_platform.DEFAULT_PLATFORM:
        # This platform's overlap policy replaces the lane's both-platforms rule: only its own
        # posts (and the operator's `already_written`, read as its own) block a keyword.
        mine = existing + [blog_overlap.ExistingContent(platform=profile.name, keyword=k, origin="request")
                           for k in extra_written]
        covered_by = lambda keyword: blog_overlap.blocks_selection(keyword, profile.name, mine)  # noqa: E731
    queue_candidates: list[QueueCandidate] | None = None
    seed_source: dict[str, Any] = {"kind": "request"}
    if use_queue and target is None:
        queue_source = keyword_queue if keyword_queue is not None else VaultKeywordQueue(
            getattr(source, "root", ""))
        loaded = queue_source.load(now)
        queue_candidates, skipped = queue_seeds(
            loaded, already_written=written_keywords(ledger, published),
            already_tagged=published.tags if published is not None else (),
            covered_by=covered_by)
        seed_source = {"kind": "vault_queue", "as_of": loaded.as_of,
                       "candidates_read": len(loaded.candidates),
                       "skipped_written": len(skipped)}
        if not queue_candidates:
            raise ToolError(NO_ELIGIBLE_KEYWORD,
                            f"the vault queue ({loaded.as_of}) is exhausted: all "
                            f"{len(loaded.candidates)} candidates are already written; "
                            "rebuild it with kw_pipeline run")
        seeds = [c.keyword for c in queue_candidates]
    if seeds:
        research = _run(
            "research",
            "이 시드 키워드들의 측정된 검색 수요와 경쟁 강도를 정리하고, "
            "다음 블로그 글의 주제 후보를 근거와 함께 제시해라: " + ", ".join(seeds),
            blocked_code=IDEATION_RESEARCH_BLOCKED,
            keyword_seeds=", ".join(seeds),   # the brief splits a string; a list has no `.split`
            **common,
        )
        keyword_record = (research.get("records") or {}).get("keyword_research")
        if target is None:
            metrics = (keyword_record or {}).get("metrics") or []
            already_written = list(written_keywords(ledger, published))
            already_written += extra_written
            target, reasoning = select_target_keyword(
                metrics, already_written=already_written,
                already_tagged=published.tags if published is not None else (),
                queue=queue_candidates, covered_by=covered_by,
            )
    if not target:
        raise ToolError(NO_ELIGIBLE_KEYWORD, _no_eligible_message(reasoning))

    mode = "operator_override" if target_override else "rule"
    overlap = blog_overlap.assess(
        target, profile.name, existing, mode=mode,
        source_state="measured" if published is not None else "unavailable")
    links = link_candidates(target, published, profile.name)
    context = blog_prompt.DraftContext(
        link_candidates=links.link_candidates, link_source_state=links.link_source_state,
        repurpose_from=tuple({"platform": m["existing_platform"], "keyword": m["existing_keyword"],
                              "title": m["existing_title"]}
                             for m in overlap["matches"]
                             if m["existing_platform"] != profile.name
                             and m["action"] in (blog_overlap.REWRITE_REQUIRED, blog_overlap.REVIEW)))
    draft_common = {**common, "provider": draft_provider(
        common.get("provider"), member_timeout_cap=profile.draft_member_timeout_seconds)}
    # The content leg runs the brief on the target (`keyword_seeds=target`), so the target's
    # own evidence is gathered inside the same governed run that drafts against it.
    content_text = profile.content_request(target, context)
    content = _run(
        "content", content_text,
        blocked_code=IDEATION_CONTENT_BLOCKED,
        keyword_seeds=target,   # `run_task` has no `naver_keywords`; the brief keyword is `keyword_seeds`
        # The web search looks for the target, not for the drafting brief: sent whole, the brief
        # (mostly about paragraphs and JSON) found posts about "문단" and GPT prompts.
        search_query=target,
        # The draft and its JSON frame need more than the generic 4,000-token output half
        # (review B9); the profile is bound to `content` runs and refused anywhere else.
        budget_profile=profile.budget_profile,
        **draft_common,
    )
    content_records = content.get("records") or {}
    first = interpret_draft(_draft_text(content), target, content_records, profile=profile, context=context)

    # At most ONE automatic revision, and only when the first draft missed something that
    # gates (a critical standard, the structured contract, the three titles). The request
    # names only what failed. Whatever the revision returns, the fire ends with a package:
    # `needs_edit` is a state for a human, not a reason to hide the draft.
    final = first
    revision: Mapping[str, Any] | None = None
    revision_text: str | None = None
    revision_outcome: str | None = None
    revision_detail: str | None = None
    if first["failures"]:
        request = profile.revision_request(target, first, _draft_text(content), content_records, context)
        if len(request) > MAX_REVISION_REQUEST_CHARS:
            revision_outcome = "REVISION_SKIPPED:REQUEST_TOO_LONG"
        else:
            revision_text = request
            revision_common = {k: v for k, v in draft_common.items() if k != "source_ref"}
            try:
                revision = _run(
                    "content", request, blocked_code=IDEATION_REVISION_BLOCKED,
                    source_ref=f"{common['source_ref']}:revision",
                    budget_profile=profile.budget_profile, **revision_common,
                )
            except ToolError as exc:
                inner = (exc.data or {})
                revision_outcome = f"REVISION_BLOCKED:{inner.get('inner_reason_code') or exc.reason_code}"
                revision_detail = inner.get("inner_message") or None
            else:
                second = interpret_draft(_draft_text(revision), target, revision.get("records"),
                                         profile=profile, context=context)
                if (second["draft_format"] != blog_draft.DRAFT_FORMAT_STRUCTURED
                        and first["draft_format"] == blog_draft.DRAFT_FORMAT_STRUCTURED):
                    # A revision that lost the structure is worse than the draft it revised.
                    revision_outcome = "REVISION_UNSTRUCTURED:KEPT_FIRST_DRAFT"
                elif second["failures"] and _miss(second, profile.standards) > _miss(first, profile.standards):
                    # One revision is all there is, so it must not leave the package worse than
                    # the draft it revised: a draft 3 characters over the paragraph ceiling was
                    # once replaced by one 579 characters under the body floor.
                    revision_outcome = "REVISION_FURTHER_OFF:KEPT_FIRST_DRAFT"
                    revision_detail = _miss_detail(second)
                else:
                    final = _carry_first_evidence(first, second, profile)
                    revision_outcome = "REVISED" if not second["failures"] else "REVISED_STILL_FAILING"

    # The target's evidence is the content run's OWN brief (`keyword_seeds=target`), which was
    # previously discarded in favour of the selection brief. The selection brief stays, as what
    # it is: the comparison the choice was made from.
    target_record = content_records.get("keyword_research")
    content_trace = _trace_id(content)
    lineage = {
        "selection_research_trace_id": _trace_id(research) if research is not None else None,
        "target_research_trace_id": content_trace if target_record is not None else None,
        "content_trace_id": content_trace,
        "revision_trace_id": _trace_id(revision) if revision is not None else None,
        "prompt": prompt_lineage(profile, request=content_text, revision=revision_text),
    }
    target_ev = target_evidence(target, target_record, now=now)
    layers = blog_quality.layers(final, target=target, target_evidence=target_ev,
                                 platform_checks=profile.platform_checks(final, target),
                                 overlap=overlap)
    quality = quality_record(final, first_failures=first["failures"],
                             revision_count=1 if revision is not None else 0,
                             revision_outcome=revision_outcome,
                             revision_detail=revision_detail, profile=profile, layers=layers)
    package = build_package(
        target_keyword=target, draft=final,
        selection=selection_evidence(keyword_record, reasoning, selected_keyword=target,
                                     mode=mode, seeds=seeds, now=now, seed_source=seed_source),
        target=target_ev, lineage=lineage, quality=quality, now=now,
        profile=profile, overlap=overlap,
    )
    try:
        schema_validation.validate_against_schema(
            package, package_schema_path(PACKAGE_SCHEMA_VERSION, repo_root), "blog_content_package")
    except schema_validation.RuntimeSchemaError as exc:
        raise ToolError(BLOG_PACKAGE_SCHEMA_INVALID, str(exc)) from exc
    lines, critical_pass = blog_draft_score.scorecard(final["measured"], dict(profile.standards))
    score = {
        "standards_version": profile.standards_version,
        "critical_pass": bool(critical_pass),
        "quality_state": quality["quality_state"],
        "measured": final["measured"],
    }

    if ledger is not None:
        ledger.append_records(package["package_id"], {PACKAGE_RECORD_KIND: package})

    written, files, write_note = _write_package_files(
        package, writer=writer, ledger=ledger, now=now, repo_root=repo_root,
    )

    return {
        "package": package,
        "package_id": package["package_id"],
        "platform": profile.name,
        "target_keyword": target,
        "selection": reasoning,
        "score": score,
        "scorecard_lines": lines,
        "target_evidence": package["target_evidence"],
        "overlap": package["overlap"]["decision"],
        "lineage": package["lineage"],
        "trace_ids": [t for t in dict.fromkeys(
            v for k, v in package["lineage"].items() if k != "prompt") if t],
        "written": written,
        "files": files,
        "filesystem_write": write_note,
    }

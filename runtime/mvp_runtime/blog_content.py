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
"""

from __future__ import annotations

import datetime
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from runtime.read_only_kernel import integrity, schema_validation

from . import blog_draft, blog_draft_score, budgets, naver_research, timeutil
from .errors import MvpRuntimeError, ToolError
from .pipeline import run_task

PACKAGE_SCHEMA_VERSION = "blog_content_package.v0.2"
PACKAGE_RECORD_KIND = "blog_content_package"
# Every version a ledger row may carry. v0.1 rows (none exist on the host, but the schema shipped)
# stay valid against their own schema; readers pick the schema by the row's `schema_version`.
PACKAGE_SCHEMA_VERSIONS = ("blog_content_package.v0.1", "blog_content_package.v0.2")

# The schema's own ceilings, mirrored here so the parser truncates deterministically instead of
# handing the validator a draft-shaped reason to fail the whole fire. A model that emits 24
# capture markers has produced a usable draft with too many notes, not an invalid one.
MAX_TITLES = 5
MAX_BODY_BLOCKS = 100
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
# it out every time. With the blog profile's 360 s, each member gets up to 120 s.
BLOG_DRAFT_MEMBER_TIMEOUT_SECONDS = 120


def draft_provider(provider: Any) -> Any:
    """The provider the draft and its revision run on: ``provider`` with its OpenRouter member
    swapped to ``MVP_BLOG_OPENROUTER_MODEL`` when that is set, ``provider`` itself otherwise."""
    model = os.environ.get(BLOG_OPENROUTER_MODEL_ENV, "").strip()
    if not model or provider is None:
        return provider
    from . import providers as _providers  # function-local: only the drafting path needs it
    return _providers.with_openrouter_model(
        provider, model, member_timeout_cap=BLOG_DRAFT_MEMBER_TIMEOUT_SECONDS)


# Search Ad takes at most five hint keywords per call, and the brief is one call.
MAX_QUEUE_SEEDS = naver_research.MAX_HINT_KEYWORDS
IDEATION_RESEARCH_BLOCKED = "IDEATION_RESEARCH_BLOCKED"
IDEATION_CONTENT_BLOCKED = "IDEATION_CONTENT_BLOCKED"
IDEATION_REVISION_BLOCKED = "IDEATION_REVISION_BLOCKED"
BLOG_PACKAGE_SCHEMA_INVALID = "BLOG_PACKAGE_SCHEMA_INVALID"

__all__ = [
    "BLOG_PACKAGE_SCHEMA_INVALID",
    "IDEATION_CONTENT_BLOCKED",
    "IDEATION_RESEARCH_BLOCKED",
    "NO_ELIGIBLE_KEYWORD",
    "PACKAGE_RECORD_KIND",
    "PUBLISHED_KEYWORD_SOURCE_UNAVAILABLE",
    "PublishedKeywords",
    "VaultPublishedKeywordSource",
    "covering_keyword",
    "build_package",
    "parse_seeds",
    "run_content_ideation",
    "select_target_keyword",
    "written_keywords",
]


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

    Two lists because they are matched differently (:func:`covering_keyword`): a topic keyword
    covers its near variants, a tag only its exact spelling — a short tag like '인스타그램'
    would otherwise swallow every new topic that mentions it."""

    keywords: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()


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
        posts = 0
        for platform in _PUBLISHED_PLATFORM_DIRS:
            for path in sorted((self.root / "content" / platform).glob("**/*.md")):
                front = _front_matter(path.read_text(encoding="utf-8", errors="replace"))
                if front is None:
                    continue
                posts += 1
                keywords.extend(_front_list(front, "keywords"))
                google = _front_value(front, "google_kw")
                if google:
                    keywords.append(google)
                tags.extend(_front_list(front, "tags"))
        if posts == 0:
            raise ToolError(PUBLISHED_KEYWORD_SOURCE_UNAVAILABLE,
                            f"no post front matter under {self.root}/content/{{naver,tistory}}")
        return PublishedKeywords(keywords=tuple(_dedupe(keywords)), tags=tuple(_dedupe(tags)))


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


def covering_keyword(
    keyword: str, written: Sequence[str] = (), tags: Sequence[str] = (),
) -> str | None:
    """The already-written keyword (or tag) that covers ``keyword``, or None.

    The vault pipeline's rule (`kw_pipeline.covered_by`), ported rather than re-invented,
    because exact string equality re-picks '미리캔버스 포스터' after '미리캔버스포스터':

    - equal after :func:`naver_research.normalize_keyword`;
    - one contains the other, the shorter at least 4 characters and at least 60% of the longer
      ('휴무안내문' ⊂ '추석휴무안내문' is covered; '네이버플레이스' ⊂
      '네이버플레이스영업시간변경' is a new topic);
    - the word sets overlap by Jaccard >= 2/3, compared as integers (0.67 dropped 2/3);
    - a tag covers only on exact normalized equality, and only at 4+ characters.
    """
    norm = naver_research.normalize_keyword(keyword)
    if not norm:
        return None
    tokens = set(str(keyword).casefold().split())
    for other in written:
        other_norm = naver_research.normalize_keyword(other)
        if not other_norm:
            continue
        if other_norm == norm:
            return str(other)
        short, long_ = sorted((len(norm), len(other_norm)))
        if (other_norm in norm or norm in other_norm) and short >= 4 and short * 10 >= long_ * 6:
            return str(other)
        other_tokens = set(str(other).casefold().split())
        if tokens and other_tokens and 3 * len(tokens & other_tokens) >= 2 * len(tokens | other_tokens):
            return str(other)
    for tag in tags:
        tag_norm = naver_research.normalize_keyword(tag)
        if len(tag_norm) >= 4 and tag_norm == norm:
            return str(tag)
    return None


def select_target_keyword(
    metrics: Sequence[Mapping[str, Any]],
    *,
    already_written: Sequence[str] = (),
    already_tagged: Sequence[str] = (),
    queue: Sequence[QueueCandidate] | None = None,
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
        covered = covering_keyword(keyword, already_written, already_tagged)
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


def written_keywords(ledger: Any, published: PublishedKeywords | None = None) -> list[str]:
    """Every keyword already used: ledger packages (archives included) UNION published posts.

    What :func:`select_target_keyword` excludes as "already written". The ledger half alone was
    true only inside the lane, which has never produced a package — every post so far (69 Naver,
    77 Tistory by 2026-09-28) was written in the vault, so a ledger-only answer was "nothing"
    and a revived lane would have re-picked published keywords. Drafted counts, not only
    published; ``target=`` still forces one. Archives are read because packages are records,
    and records rotate."""
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
        if keyword:
            keywords.add(keyword)
    return sorted(keywords)


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
    limit: int = MAX_QUEUE_SEEDS,
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
        if covering_keyword(candidate.keyword, already_written, already_tagged) is not None:
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


# The `\s+` after the hashes is load-bearing, not style. A Korean hashtag line —
# `#미리캔버스 #포스터제작` — also begins with `#`, and without the required space this regex
# claimed it as a heading, so every draft's tag line became a title and `tags` came back empty.
_HEADING_RE = re.compile(r"^\s{0,3}#{1,4}\s+(?P<text>.+?)\s*$")
_CAPTURE_RE = re.compile(r"\[캡처:\s*(?P<what>[^\]]+)\]")
_TAG_RE = re.compile(r"#([^\s#]{1,40})")


def _parse_draft(draft: str) -> dict[str, Any]:
    """Split one plain-text draft into the package's paste body and its editor instructions.

    The paste body is what goes into SmartEditor, so the markers the editor cannot interpret
    are lifted out of it and become instructions beside it: a heading line becomes a
    `body_blocks` entry, a `[캡처: …]` marker becomes an `image_shots` entry naming the
    paragraph it followed, and trailing `#tags` become `tags`. Everything is truncated at the
    schema's ceiling rather than allowed to fail validation — see the constants above.
    """
    tags: list[str] = []
    blocks: list[dict[str, Any]] = []
    shots: list[dict[str, Any]] = []
    kept: list[str] = []

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", draft or "") if p.strip()]
    for para in paragraphs:
        captures = _CAPTURE_RE.findall(para)
        body = _CAPTURE_RE.sub("", para).strip()
        # Asked first: a paragraph that is only hashtags is the draft's tag line, not a
        # paragraph and not a heading.
        if body and not _TAG_RE.sub("", body).strip() and _TAG_RE.search(body):
            tags.extend(_TAG_RE.findall(body))
            continue
        heading = _HEADING_RE.match(body)
        if heading:
            body = heading.group("text").strip()
        if body:
            kept.append(body)
            index = len(kept) - 1
            if heading:
                blocks.append({"paragraph_index": index, "action": "heading"})
            for what in captures:
                shots.append({"after_paragraph": index, "what_to_capture": what.strip()})
        elif captures:
            index = max(len(kept) - 1, 0)
            for what in captures:
                shots.append({"after_paragraph": index, "what_to_capture": what.strip()})

    # Tags may also trail the final paragraph rather than standing alone.
    if kept:
        trailing = _TAG_RE.findall(kept[-1])
        if trailing and not _TAG_RE.sub("", kept[-1]).strip():
            tags.extend(trailing)
            kept.pop()

    seen: set[str] = set()
    unique_tags = [t for t in tags if not (t in seen or seen.add(t))]
    return {
        "body_paste": "\n\n".join(kept),
        "body_blocks": blocks[:MAX_BODY_BLOCKS],
        "image_shots": shots[:MAX_IMAGE_SHOTS],
        "tags": unique_tags[:MAX_TAGS],
        "paragraph_count": len(kept),
    }


_TITLE_RE = re.compile(r"^\s{0,3}#\s+(?P<text>.+?)\s*$")


def _legacy_title_candidates(draft: str) -> list[str]:
    """Titles from a prose draft: its level-1 headings (``# …``) only.

    Every heading used to qualify, so a section called '프롬프트 만들기' was offered as the post's
    title. A section heading is a section; a draft with no ``#`` title line yields no candidate,
    and the quality check names the gap rather than a heading standing in for one."""
    titles: list[str] = []
    for line in (draft or "").splitlines():
        match = _TITLE_RE.match(line)
        if match:
            text = match.group("text").strip()
            if text and text not in titles:
                titles.append(text[:100])
    return titles[:MAX_TITLES]


def interpret_draft(
    text: str, target_keyword: str, records: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """One model answer as the package's parts, its measurement and what it fails. Pure.

    The structured JSON contract (`blog_draft`) is the primary path; a draft that is not a
    usable JSON document goes through the legacy regex parser and is recorded as such — and
    failing the structured contract is itself one of the draft's failures, so it is what the
    one revision is asked to fix first. ``records`` is the run's own record set: sources and
    fact-check references resolve against the evidence THAT run had, never another run's."""
    index = blog_draft.evidence_index(records)
    structured, parse_reason = blog_draft.parse_structured(text)
    if structured is not None:
        # Kept out of the draft itself: the revision request re-serializes that draft.
        brackets_inserted = int(structured.pop("brackets_inserted", 0) or 0)
        rendered = blog_draft.render_blocks(structured)
        sources = blog_draft.resolve_sources(structured["sources"], index)
        prose = list(structured["intro"]) + [
            p for section in structured["sections"] for p in section["paragraphs"]]
        measured = blog_draft_score.measure_structured(
            intro=structured["intro"], sections=structured["sections"],
            image_count=len(rendered["image_shots"]), tags=structured["tags"],
            source_count=len(sources), keyword=target_keyword,
            has_table=structured.get("table") is not None)
        parts = {
            "draft_format": blog_draft.DRAFT_FORMAT_STRUCTURED,
            "title_candidates": structured["title_candidates"],
            "body_paste": rendered["body_paste"],
            "body_blocks": rendered["body_blocks"][:MAX_BODY_BLOCKS],
            "tags": structured["tags"][:MAX_TAGS],
            "image_shots": rendered["image_shots"],
            "sources": sources,
            "fact_checks": blog_draft.fact_checks(structured["fact_checks"], prose, index),
            "structured": structured,
            "brackets_inserted": brackets_inserted,
        }
    else:
        parsed = _parse_draft(text)
        paragraphs = [p for p in parsed["body_paste"].split("\n\n") if p.strip()]
        heading_indexes = {b["paragraph_index"] for b in parsed["body_blocks"]}
        prose = [p for i, p in enumerate(paragraphs) if i not in heading_indexes]
        measured = blog_draft_score.measure(text, target_keyword)
        parts = {
            "draft_format": blog_draft.DRAFT_FORMAT_LEGACY,
            "parse_reason": parse_reason,
            "title_candidates": _legacy_title_candidates(text),
            "body_paste": parsed["body_paste"],
            "body_blocks": parsed["body_blocks"],
            "tags": parsed["tags"],
            "image_shots": parsed["image_shots"],
            "sources": [],
            "fact_checks": blog_draft.fact_checks([], prose, index),
            "structured": None,
        }
    failures = list(blog_draft_score.critical_failures(measured))
    if len(parts["title_candidates"]) < blog_draft.MIN_TITLES:
        failures.append("title_candidates")
    if parts["draft_format"] != blog_draft.DRAFT_FORMAT_STRUCTURED:
        failures.append("structured_output")
    # The keyword under its floor is a failure the revision is asked to fix, though not a
    # critical standard. Asked for 3~6 in the request alone (#1032), three drafts in a row used
    # it once (2026-09-30). Over the ceiling stays advisory.
    if 0 <= measured["keyword_hits"] < blog_draft_score.STANDARDS["keyword_hits"].low:
        failures.append("keyword_hits")
    # A sentence pasted twice is a failure of the contract, not a standard's distance: counted
    # in `_miss` as one, so a revision that pads by copying loses to the draft it copied from.
    repeated = blog_draft.repeated_sentences(prose)
    measured = dict(measured, repeated_sentences=blog_draft.repeat_count(prose))
    if repeated:
        failures.append("repeated_sentences")
    parts.update({"measured": measured, "failures": failures, "repeated": repeated})
    return parts


def build_package(
    *,
    target_keyword: str,
    draft: Mapping[str, Any],
    selection: Mapping[str, Any],
    target: Mapping[str, Any],
    lineage: Mapping[str, Any],
    quality: Mapping[str, Any],
    now: str,
) -> dict[str, Any]:
    """Assemble one `blog_content_package.v0.2` from an :func:`interpret_draft` result. Pure —
    no ledger, no clock, no I/O.

    ``title_candidates`` has minItems 1 in the schema, and a draft that produced no title is
    still a package worth reviewing — so the target keyword stands in, and the quality record
    already names `title_candidates` as a failure (the package is `needs_edit`)."""
    titles = list(draft.get("title_candidates") or []) or [target_keyword]
    body = str(draft.get("body_paste") or "") or target_keyword
    package: dict[str, Any] = {
        "schema_version": PACKAGE_SCHEMA_VERSION,
        "created_at_utc": now,
        "target_keyword": target_keyword,
        "selection_evidence": dict(selection),
        "target_evidence": dict(target),
        "lineage": dict(lineage),
        "title_candidates": titles[:MAX_TITLES],
        "body_paste": body,
        "body_blocks": list(draft.get("body_blocks") or [])[:MAX_BODY_BLOCKS],
        "tags": list(draft.get("tags") or [])[:MAX_TAGS],
        "image_shots": list(draft.get("image_shots") or [])[:MAX_IMAGE_SHOTS],
        "sources": list(draft.get("sources") or []),
        "fact_checks": list(draft.get("fact_checks") or [])[:MAX_FACT_CHECKS],
        "quality": dict(quality),
        "publish_state": "draft",
    }
    package["package_id"] = integrity.short_id("bcp", {
        "target_keyword": target_keyword,
        "body_paste": package["body_paste"],
        "created_at_utc": now,
    })
    return package


def quality_record(
    final: Mapping[str, Any],
    *,
    first_failures: Sequence[str],
    revision_count: int,
    revision_outcome: str | None,
    revision_detail: str | None = None,
) -> dict[str, Any]:
    """What the package's reviewer needs to know about the draft's quality, in one place.

    `ready_for_review` only when the final draft cleared every critical standard AND the
    structural contract (three titles, structured output) and the keyword's floor; otherwise `needs_edit`. Neither
    state publishes anything — both are a draft waiting for a human."""
    record: dict[str, Any] = {
        "standards_version": blog_draft_score.STANDARDS_VERSION,
        "draft_format": final["draft_format"],
        "quality_state": "ready_for_review" if not final["failures"] else "needs_edit",
        "critical_pass": not blog_draft_score.critical_failures(final["measured"]),
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
    return record


def render_paste_txt(package: Mapping[str, Any]) -> str:
    """`PASTE.txt` — the editor paste, verbatim. Zero formatting symbols, paragraph breaks
    only: SmartEditor ONE renders no markdown, so anything beyond plain text would land in
    the published post as literal characters (proposal §4b's founding observation)."""
    body = str(package.get("body_paste") or "")
    return body if body.endswith("\n") else body + "\n"


def render_post_md(package: Mapping[str, Any]) -> str:
    """`POST.md` — the operator's single reading file, per the §4b table: evidence, titles,
    body, edit directives, capture directives, tags, pre-publish checks. A rendering of the
    package record, never an authority of its own."""
    lines: list[str] = [f"# {package.get('target_keyword')}", ""]
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
        if len(web) < MIN_WEB_SOURCES:
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

    lines += ["", "## 제목 후보 (소제목과 별개)"]
    lines += [f"{i}. {t}" for i, t in enumerate(package.get("title_candidates") or [], start=1)]

    headings = {b.get("paragraph_index") for b in package.get("body_blocks") or []
                if b.get("action") == "heading"}
    paragraphs = str(package.get("body_paste") or "").split("\n\n")
    lines += ["", "## 본문 (소제목 표시는 이 파일에만 — PASTE.txt는 기호 없음)", ""]
    lines += [f"### {p}" if i in headings else p for i, p in enumerate(paragraphs)]

    shots = package.get("image_shots") or []
    if shots:
        lines += ["", "## 캡처 지시 (이미지 생성이 아니라 실제 화면 캡처)"]
        lines += [f"- {s.get('after_paragraph')}번째 문단 뒤: {s.get('what_to_capture')}"
                  + (f" ({s['tool_name']})" if s.get("tool_name") else "") for s in shots]

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
    return f"blog/{str(package.get('created_at_utc'))[:10]}-{slug}-{str(package.get('package_id'))[-4:]}"


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
        for name, content in (("POST.md", render_post_md(package)),
                              ("PASTE.txt", render_paste_txt(package))):
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

# The draft's LENGTH PLAN: a concrete shape, not a set of totals. The first package (2026-09-28,
# 730 characters against the 1,800 floor) showed why. The old request asked for "10~20
# paragraphs, 70~150 characters each, 1,800~3,500 in total" at once. Those three are consistent
# only well above their minimums: ten paragraphs at the 150 ceiling is 1,500. The model hit the
# two minimums it could satisfy literally (10 paragraphs, average 73) and landed at 730. A plan
# that adds up states the one shape that clears every standard. Its arithmetic is checked against
# `blog_draft_score.STANDARDS` in the tests, so moving a standard cannot silently break it.
PLAN_INTRO_PARAGRAPHS = 2
PLAN_SECTIONS = 5
PLAN_PARAGRAPHS_PER_SECTION = 3
# 120~150 since the second measured round (2026-09-29): asked for 110~140, the drafts averaged
# 62, 76 and 93, always below the floor they were given. A floor is where a model's paragraphs
# start, not where they land, so the plan's floor sits above the standard's need.
# 120~140 since the third round (2026-09-29): asked for 120~150, the draft averaged 155 and
# broke the standard's 150 ceiling the other way. The plan leaves the ceiling headroom.
PLAN_PARAGRAPH_CHARS = (120, 140)          # visible characters, whitespace excluded
PLAN_SENTENCES_PER_PARAGRAPH = "4"
# One paragraph of the planned length, shown as a LENGTH reference only (its content is
# generic on purpose and the request says not to reuse it). A total alone did not move the
# drafts; a concrete paragraph shows what "130 characters" looks like.
LENGTH_EXAMPLE_PARAGRAPH = (
    "신청 화면에 들어가면 먼저 본인 인증을 요구합니다. 공동인증서와 간편인증 가운데 편한 방법을 "
    "고르면 되고, 인증이 끝나면 신청서 작성 단계로 바로 넘어갑니다. 여기서 입력한 정보는 나중에 "
    "고치기 번거로우니 제출 버튼을 누르기 전에 한 번 더 확인하는 편이 안전합니다. 제출이 끝나면 "
    "접수 번호가 화면에 나타납니다."
)
# How many off-plan paragraphs (short or long) a revision names one by one; more than this and
# the list is noise.
MAX_NAMED_SHORT_PARAGRAPHS = 12


def plan_paragraphs() -> int:
    return PLAN_INTRO_PARAGRAPHS + PLAN_SECTIONS * PLAN_PARAGRAPHS_PER_SECTION


def plan_target() -> int:
    """The paragraph length the plan aims at: the middle of its range."""
    low, high = PLAN_PARAGRAPH_CHARS
    return (low + high) // 2


def plan_body_chars() -> tuple[int, int]:
    low, high = PLAN_PARAGRAPH_CHARS
    return plan_paragraphs() * low, plan_paragraphs() * high


def _length_plan() -> str:
    """The plan, centred on the paragraph's target and its cap.

    Until 2026-09-29 the plan's last words were "각 문단이 120자 이상인지 세어 보고, 짧은 문단에는
    문장을 더 붙여라", and the drafts read the floor as the aim: averages 169, 153, 136, 158, 174 and
    184 against 120~140, one draft noting "각 문단 120자 이상 준수" in its own findings. The
    self-check is now both ways.

    The cap is not said louder than the floor: said first ("140자를 넘기지 마라", #1036), the next
    two drafts averaged 82 and 81 — below the floor the other way. Target, range, and the two
    fail lines, evenly."""
    low, high = PLAN_PARAGRAPH_CHARS
    ceiling = blog_draft_score.STANDARDS["para_chars"].high
    total_low, total_high = plan_body_chars()
    return (
        f"분량 계획(이대로 써라): intro 문단 {PLAN_INTRO_PARAGRAPHS}개 + sections "
        f"{PLAN_SECTIONS}개 × 섹션마다 paragraphs {PLAN_PARAGRAPHS_PER_SECTION}개 = 문단 "
        f"{plan_paragraphs()}개. 문단 하나는 {PLAN_SENTENCES_PER_PARAGRAPH}문장, 공백 빼고 "
        f"{plan_target()}자 안팎({low}~{high}자). 합계 약 {total_low:,}~{total_high:,}자이고, 문단 "
        f"평균이 {ceiling}자를 넘거나 합계가 1,800자에 못 미치면 불합격이다. 한두 문장짜리 문단을 만들지 마라 — 각 문단은 방법·이유·예시·주의점 중 "
        f"둘 이상을 담아 풀어 써라. 문단 하나의 길이는 이 정도다(길이만 참고하고 내용은 따라 쓰지 "
        f"마라): 「{LENGTH_EXAMPLE_PARAGRAPH}」 JSON을 내기 전에 문단이 {plan_paragraphs()}개인지, 각 "
        f"문단이 {low}~{high}자인지 세어 보고, {high}자를 넘는 문단은 덜어내고 {low}자에 못 미치는 "
        f"문단은 더해서 {plan_target()}자 안팎으로 맞춰라."
    )


def _length_asks(measured: Mapping[str, Any], structured: Mapping[str, Any] | None = None) -> str:
    """For a revision: the plan against what the draft actually measured — and, for a structured
    draft, the paragraphs off the plan named one by one. "Make paragraphs longer" moved the
    average from 62 to 93 over three rounds; pointing at the exact paragraph is the concrete
    version.

    Which way depends on the average. Over the standard's ceiling, the ask is to cut: the
    paragraphs above the plan are named and the plan's "add sentences" is left out — on
    2026-09-29 (`bcp_5f459c95cf51fa821f6d`) every one of 17 paragraphs was over 150, the
    revision was told only which paragraphs were SHORT (none) and to add sentences to short
    ones, and it came back at 169 against 168."""
    current = (f"현재 문단 {measured.get('paragraphs', '-')}개·문단 평균 "
               f"{measured.get('para_chars', '-')}자·합계 {measured.get('body_chars', '-')}자.")
    low, high = PLAN_PARAGRAPH_CHARS
    ceiling = blog_draft_score.STANDARDS["para_chars"].high
    if int(measured.get("para_chars") or 0) > ceiling:
        # The cut has a floor too: on 2026-09-29 (`bcp_e9717849588d1f438c66`) a draft 3 over the
        # ceiling (average 153) came back with every paragraph halved — 71 on average, the body
        # 1,221 against a 1,800 floor. So only the named paragraphs lose one sentence each.
        body_floor = blog_draft_score.STANDARDS["body_chars"].low
        ask = (f"{current} 문단 평균이 상한 {ceiling}자를 넘었다. 문단 수는 그대로 두고 {high}자를 넘는 "
               f"문단만 공백 빼고 {low}~{high}자로 줄여라 — 그 문단마다 적힌 만큼만(대개 한 문장) 덜어내고, 사실·수치·"
               f"키워드는 지우지 마라. 나머지 문단은 손대지 마라. 어떤 문단도 {low}자 아래로 줄이지 마라 "
               f"— 본문 합계가 {body_floor:,}자 아래면 불합격이다. 문장을 더 붙이지 마라.")
        named = _named(_off_plan_paragraphs(structured, lambda n: n > high) if structured else [])
        if named:
            ask += (f" {high}자를 넘는 문단(번호는 0부터, 괄호는 {plan_target()}자까지 덜어낼 양): "
                    f"{named}. 이 문단마다 적힌 만큼 덜어내 {low}~{high}자로 맞춰라.")
        return ask
    ask = f"{current} {_length_plan()}"
    named = _named(_off_plan_paragraphs(structured, lambda n: n < low) if structured else [])
    if named:
        ask += (f" {low}자에 못 미치는 문단(번호는 0부터, 괄호는 {plan_target()}자까지 더할 양): "
                f"{named}. 이 문단마다 적힌 만큼 늘려라.")
    return f"{ask} {ADD_SUBSTANCE_ASK}"


# What a paragraph grows by. Told to add "이유·예시·주의점", both revisions on 2026-09-30 that grew
# a short body ('CHATGPT요금제' bcp_7f50c01bc7f35aee465a, 'ai 번역기' bcp_4ad51169ab0a26df545d)
# gave every paragraph one more closing line that fits any post — "…지혜가 필요합니다",
# "꼼꼼한 확인이 실수를 미연에 방지합니다" — and the length passed on filler.
#
# The revision runs with no evidence blocks, so "근거 블록([S#])의 수치" (#1066) pointed at nothing:
# on candidate-1066 '포토샵 누끼따기' (bcp_746f52917537a5764286) the revision grew only its two
# intro paragraphs, each by one more closer. The specifics now come from the evidence notes the
# revision request carries (:func:`_evidence_notes`).
ADD_SUBSTANCE_ASK = (
    "더하는 문장에는 그 섹션 소제목에 대한 구체적인 내용 — 아래 근거 메모에 있는 수치·메뉴나 버튼 이름·"
    "절차 단계·설정값, 또는 독자가 겪는 구체적인 상황 하나 — 을 담아라. '…이 중요합니다'·'…지혜가 "
    "필요합니다'·'…도움이 됩니다'·'…주의가 필요합니다'처럼 어느 글에나 붙는 맺음 문장으로 늘리지 마라. "
    "다른 문단에 이미 있는 문장을 옮겨 오거나 되풀이해서 늘리지도 마라 — 반복된 문장이 있으면 불합격이다. "
    "바로 앞 문장을 다른 말로 다시 말하는 문장(예: '…버튼을 누르면 등록이 완료됩니다.' 뒤의 '버튼을 누르는 "
    "순간 바로 등록이 끝납니다.')도 늘린 것이 아니다 — 더하는 문장은 그 문단에 아직 없는 정보여야 한다."
)


def _named(paragraphs: Sequence[str]) -> str:
    """The named list, capped at :data:`MAX_NAMED_SHORT_PARAGRAPHS` with the rest counted."""
    if not paragraphs:
        return ""
    cap = MAX_NAMED_SHORT_PARAGRAPHS
    more = f" 외 {len(paragraphs) - cap}개" if len(paragraphs) > cap else ""
    return ", ".join(paragraphs[:cap]) + more


def _off_plan_paragraphs(structured: Mapping[str, Any], off: Callable[[int], bool]) -> list[str]:
    """Each prose paragraph whose visible length ``off`` flags, labelled for the revision with how
    far it is from the plan's target: "섹션 1의 문단 2(현재 81자, 약 50자 더)".

    The amount is the concrete version of "add a sentence or two": asked that way on 2026-09-29
    (`bcp_dad48bf154c090f00307`), paragraphs averaging 81 grew by 23 and the body stopped 25
    short of 1,800."""
    out: list[str] = []
    for i, paragraph in enumerate(structured.get("intro") or []):
        n = len("".join(str(paragraph).split()))
        if off(n):
            out.append(f"도입 문단 {i}({_gap(n)})")
    for s_index, section in enumerate(structured.get("sections") or []):
        for p_index, paragraph in enumerate(section.get("paragraphs") or []):
            if " | " in paragraph:
                continue          # a table row block is not prose to lengthen or cut
            n = len("".join(str(paragraph).split()))
            if off(n):
                out.append(f"섹션 {s_index}의 문단 {p_index}({_gap(n)})")
    return out


def _gap(n: int) -> str:
    """``"현재 81자, 약 50자 더"`` — the distance to the plan's target, to the nearest ten."""
    gap = abs(plan_target() - n)
    amount = max(10, (gap + 5) // 10 * 10)
    return f"현재 {n}자, 약 {amount}자 {'더' if n < plan_target() else '덜'}"


_FAILURE_ASKS = {
    "body_chars": "본문 문단(도입·섹션 문단)의 글자수 합을 공백 제외 1,800~3,500자로 맞춰라",
    "headings": "섹션(소제목)을 4~7개로 맞춰라",
    "para_chars": "문단 평균 길이를 공백 제외 70~150자로 맞춰라(방법은 아래 분량 지시를 따른다)",
    "title_candidates": "title_candidates에 소제목과 다른 제목 후보를 3~5개 넣어라(타깃 키워드를 앞쪽에 자연스럽게)",
    "structured_output": "content_draft를 지정한 JSON 객체 하나로만 출력하라(설명·마크다운 금지)",
    "repeated_sentences": ("같은 문장을 두 번 이상 쓰지 마라 — 반복된 문장은 한 곳에만 남기고, 나머지 자리는 "
                           "그 문단 소제목에 맞는 다른 내용으로 바꿔라"),
}
MAX_NAMED_REPEATS = 5
MIN_WEB_SOURCES = 2
# The failures a length plan answers. When any of them is asked, the plan rides along once.
_LENGTH_FAILURES = frozenset({"body_chars", "para_chars"})

# The long prose goes LAST. Twice on 2026-09-29 (`bcp_cbbabb9953f3c67ba332`,
# `bcp_29c26be0c982a4d30646`) the model stopped of its own accord (finish_reason STOP, ~3,000
# output tokens) right after the last section, and everything the shape put after `sections` —
# table, tags, capture directions, fact checks, sources — was never written. With the short
# fields first, a draft that stops after its prose has lost nothing.
DRAFT_KEY_ORDER = ("title_candidates", "tags", "image_shots", "table", "sources", "fact_checks",
                   "intro", "sections")
_DRAFT_SHAPE = (
    '{"title_candidates": ["제목 후보 3~5개"], '
    '"tags": ["태그(# 없이)"], '
    '"image_shots": [{"after_section": 0, "what_to_capture": "캡처할 실제 화면", "tool_name": null}], '
    '"table": {"after_section": 1, "rows": [["구분", "항목1", "항목2"], ["행 이름", "값", "값"]]}, '
    '"sources": [{"source_ref": "[S1]", "title": null}], '
    '"fact_checks": [{"claim": "본문 문장", "why": "확인이 필요한 이유", "source_ref": null}], '
    '"intro": ' + json.dumps([f"도입 문단 {i}({PLAN_SENTENCES_PER_PARAGRAPH}문장)"
                              for i in range(1, PLAN_INTRO_PARAGRAPHS + 1)], ensure_ascii=False) + ', '
    '"sections": [{"heading": "소제목", "paragraphs": '
    + json.dumps([f"문단 {i}({PLAN_SENTENCES_PER_PARAGRAPH}문장)"
                  for i in range(1, PLAN_PARAGRAPHS_PER_SECTION + 1)], ensure_ascii=False) + '}]}'
)


def _draft_shape(target: str) -> str:
    """The first request's shape: :data:`_DRAFT_SHAPE` with the keyword's places marked — the
    intro's first paragraph and each section's first, 1 + 5 = 6 at most, the standard's ceiling.

    The same lever as the sentence count: the shape is what the model follows. Said in prose
    ("3~6회, intro 첫 문단에 1회"), first drafts still came in at one ('캔바 사용법',
    bcp_00e628a8e70a59473c6e, candidate-1079), and the revision that would have fixed it was
    blocked — so the keyword rode on a revision that may not run."""
    shape = json.loads(_DRAFT_SHAPE)
    n = PLAN_SENTENCES_PER_PARAGRAPH
    shape["intro"][0] = f"도입 문단 1({n}문장, '{target}' 1회)"
    # Marked on each section's FIRST paragraph (#1081), all three drafts on candidate-1081 put the
    # keyword at the head of every section's first sentence — "미리캔버스 공동작업 환경을…",
    # "…효율을 극대화하려면", "망고보드 ai 시스템은…" — six times, a pattern a reader sees. The
    # mark is now on the second paragraph and on three sections of five: 1 + 3 = 4, the middle of
    # the standard's 3~6.
    shape["sections"][0]["paragraphs"][1] = (
        f"문단 2({n}문장, 섹션 5개 중 3개에서만 '{target}' 1회 — 문장 중간에)")
    return json.dumps(shape, ensure_ascii=False)


# The sentence count is in the SHAPE, not only in the plan's prose. Over 21 first drafts from the
# same model (gemini-flash-lite, 2026-09-30) the paragraph count — which the shape and the plan
# both carry — was 17 every time, while "문단 하나는 4문장", said in prose only, split the drafts in
# two: about 4 sentences a paragraph gave 2,100~2,900 characters and passed; about 3 gave
# 1,530~1,810 and fell under the 1,800 floor, which then cost a revision that padded (10 of 21).
# More paragraphs is not the lever: the 4-sentence drafts already run 160~180 a paragraph, and 22
# of them would pass the 3,500 ceiling.


# How the keyword may be written. Asked for "띄어쓰기와 표기 그대로" in named paragraphs (#1042),
# three drafts in a row glued it to the next noun as if it were an adjective: "CHATGPT사용법
# 계정 생성 절차", "명함만들기 위한 전체보기 메뉴", "원하는 방수스티커제작 크기" (2026-09-30).
# The count ignores spacing and letter case, so the natural form costs nothing.
KEYWORD_FORM_ASK = (
    "키워드는 띄어쓰기와 대소문자를 읽기 자연스럽게 바꿔 써도 같은 키워드로 센다(예: '명함만들기' → "
    "'명함 만들기', 'CHATGPT사용법' → 'ChatGPT 사용법'). 키워드 뒤에는 조사(을/를, 은/는, 에서, "
    "으로 등)를 붙여 문장의 주어·목적어로 넣고, 다른 명사 앞에 꾸밈말처럼 붙이지 마라(틀린 예: "
    "'명함만들기 위한 메뉴', 'ChatGPT 사용법 계정 생성 절차')."
)


def _keyword_ask(target: str) -> str:
    """The body's keyword use, in the scorer's own numbers.

    Until 2026-09-29 the request asked for the keyword in the titles only, and a two-word
    keyword was shortened in the body ('소상공인 스마트상점' once, '스마트상점' six times)."""
    standard = blog_draft_score.STANDARDS["keyword_hits"]
    return (f"소제목과 문단을 합쳐 '{target}'를 {standard.low}~{standard.high}회 쓰고 그중 1회는 "
            f"intro 첫 문단에 넣는다. 키워드 일부만 떼어 줄여 쓴 것은 세지 않는다. {KEYWORD_FORM_ASK}")


def _keyword_revision_ask(target: str, measured: Mapping[str, Any],
                          structured: Mapping[str, Any] | None) -> str:
    """The keyword under its floor, with the paragraphs to put it in named.

    Saying "3~6회" in the request did not move it; the named paragraphs are the concrete
    version, as they were for length. The places are paragraphs that do not have the keyword
    yet — the first intro paragraph first, then the first paragraph of each section — as many
    as it takes to reach the middle of the range."""
    standard = blog_draft_score.STANDARDS["keyword_hits"]
    hits = int(measured.get("keyword_hits") or 0)
    ask = (f"'{target}'를 소제목과 문단을 합쳐 {standard.low}~{standard.high}회 써라(현재 {hits}회). "
           f"키워드 일부만 떼어 줄여 쓴 것은 세지 않는다. {KEYWORD_FORM_ASK}")
    places = _keyword_places(structured, target) if structured else []
    need = max(1, (standard.low + standard.high) // 2 - hits)
    if places:
        ask += (f" 키워드를 넣을 문단(번호는 0부터): {', '.join(places[:need])}. 이 문단마다 "
                "키워드를 한 번씩 넣어라 — 한 문장을 고치거나 키워드가 주어·목적어로 들어간 문장 "
                "하나를 더해 자연스럽게 넣고, 그 문단의 다른 내용은 그대로 둬라.")
    return ask


def _keyword_places(structured: Mapping[str, Any], target: str) -> list[str]:
    """Paragraphs without the keyword, spread over the post: the first intro paragraph, then
    each section's first paragraph, then each section's later ones."""
    def lacks(paragraph: Any) -> bool:
        return blog_draft_score.keyword_hits(str(paragraph), target) == 0

    places: list[str] = []
    intro = structured.get("intro") or []
    if intro and lacks(intro[0]):
        places.append("도입 문단 0")
    sections = structured.get("sections") or []
    depth = max((len(s.get("paragraphs") or []) for s in sections), default=0)
    for p_index in range(depth):
        for s_index, section in enumerate(sections):
            paragraphs = section.get("paragraphs") or []
            if p_index < len(paragraphs) and " | " not in paragraphs[p_index] \
                    and lacks(paragraphs[p_index]):
                places.append(f"섹션 {s_index}의 문단 {p_index}")
    return places


# The rule against invention alone pushed the drafts to prose true of any tool: a '캡컷 사용법'
# post (bcp_c82a3c17ded878ca24ea, 2026-09-30) said "load, cut, add captions, save" with the
# app named once and none of the menu names its own [S1]/[S3]/[S4] carried. Use what the
# evidence says — by name — is the other half of "invent nothing".
#
# Named, the specifics can also be over-followed: '명함만들기' (bcp_d3be61f8a0fa8b85c920,
# 2026-09-30) walked one blog's menu path (전체보기 → 명함제작 → 이지템플릿) without saying
# whose site it was, and lifted its broken phrase "제작가이드도 참조도 하구요" into the post.
# And for a Korean reader: '명함제작업체' (bcp_366916fc7176de5a9db8) priced business cards in
# dollars from a US printer's page ("100장 기준 31.25달러").
# No business names (Thomas 2026-09-30, applying the blog's 2026-09-03 "상호명 금지" to the lane):
# three posts named and priced real printers (누리애드·비즈하우스·오프린트미·네모디·한미프린트) and
# a freelance marketplace, which reads as a recommendation. Software, apps and AI tools stay
# nameable — they are what the blog writes about ('캡컷 사용법', 'ChatGPT 사용법').
# A carrier's partnership is the exception (Thomas 2026-09-30): '퍼플렉시티 무료'
# (bcp_3ffcacf2c0ff175ef7a0) turned an SKT customers' offer into "특정 통신사 이용자라면", which no
# reader can act on.
# A tool is not a business either, and has to be SAID to be one: with "도구 이름은 써도 된다" (may),
# '스티커만들기' (bcp_b8dcea9bca60d9a7a883, candidate-1074) cited Canva's and Adobe Firefly's pages
# and called them "온라인 서비스" and "특정 앱" throughout. The tool's name is now a must.
# A shop's product title is the shop's search-engine copy, not a name: '배너입간판'
# (bcp_6625fde02d5be7682185) carried "매장광고판 카페입간판 용도로", "패트지 현수막제작 인쇄" and
# "플랜카드제작" over from the listings into its sentences.
# Where a tool ends and a business begins, by what the reader does there: "must name tools" had
# '스티커소량제작' (bcp_05eb135d21e34a31429a, candidate-1076) write "마플 같은 플랫폼" — a print
# shop with an editor. A place the reader pays to have something made or sold is a business,
# editor or not; software the reader operates is a tool.
# A public site is not a business at all (Thomas 2026-09-30): '2026소상공인지원금신청'
# (bcp_756592c6972ca6129a32) sent the reader to "지정된 지원금 전용 포털" for a voucher applied for
# on 소상공인24 — a government portal the reader has to find by name.
VENDOR_NAME_ASK = (
    "[이름 규칙] 업체인지 도구인지는 독자가 그곳에서 하는 일로 가른다. "
    "업체: 독자가 돈을 내고 물건을 만들어 받거나 사는 곳(인쇄·제작·주문·판매·배송 — 예: 마플·레드프린팅·"
    "비즈하우스, 편집기가 딸린 인쇄 주문 사이트도 여기)이다. 업체·가게·인쇄소·쇼핑몰·판매 사이트·중개 "
    "플랫폼의 이름은 본문·제목·표·캡처 지시 어디에도 쓰지 마라 — '온라인 인쇄 업체 A'·'업체 B'처럼 "
    "익명으로 쓰거나 업종으로만 불러라. "
    "도구: 독자가 직접 조작하는 소프트웨어(앱·편집 도구·메신저·AI — 예: 캔바·어도비 파이어플라이·"
    "ChatGPT·당근·카카오톡)다. 앱·소프트웨어·AI 도구는 업체가 아니다 — 근거에 나온 도구의 이름(키워드가 "
    "다루는 도구 포함)은 반드시 그대로 밝혀라. '온라인 서비스'·'특정 앱'·'편집 도구'처럼 흐리게 부르지 마라. "
    "통신사: 통신사 제휴 혜택(특정 통신사 고객만 받는 요금제·구독 혜택 등)은 그 통신사 이름을 밝혀라 — "
    "독자가 자기가 대상인지 알아야 한다. "
    "공공: 정부·공공기관의 사이트와 서비스(정부24·홈택스·위택스·소상공인24·고용노동부 등)는 업체가 "
    "아니다 — 독자가 직접 찾아가야 하는 곳이니 이름을 그대로 밝혀라. "
    "상품명: 쇼핑몰 상품명(검색용 단어를 이어 붙인 긴 이름, 예: '철제 배너거치대 A형 선반 입간판 "
    "매장광고판 카페입간판')은 그대로 옮기지 말고 'A형 철제 입간판'처럼 제품의 종류로 짧게 불러라."
)
# The reader lives in Korea. Asked as "해외 자료를 꼭 써야 하면 해외 기준이라 한국과 다를 수 있다고
# 밝혀라", 'ai 번역기' (bcp_4ad51169ab0a26df545d, 2026-09-30) used no foreign figure at all and still
# wrote a paragraph of it — "외화로 표시된 가격 정책이나 해외 기준의 서비스 조건을 그대로 적용하기
# 어렵습니다": the instruction, restated as prose. The note belongs to the sentence that uses the
# foreign source, and the request's own instructions do not become body sentences.
DOMESTIC_READER_ASK = (
    "독자는 한국에서 사는 사람이다 — 해외 업체·해외 서비스의 조건이나 달러·엔 같은 외화 가격은 쓰지 "
    "마라. 해외 자료의 내용을 꼭 써야 하면 그 내용을 쓴 문장 안에서만 '(해외 기준)'이라고 붙이고, 해외 "
    "자료를 쓰지 않았다면 해외 기준·외화·국내와의 차이에 대한 문장을 따로 만들지 마라. 이 요청에 적힌 "
    "지시(분량·키워드·독자·출처 규칙)를 본문 문장으로 옮겨 쓰지 마라."
)
EVIDENCE_SPECIFICS_ASK = (
    "근거 블록([S#])에 나온 구체적인 내용 — 메뉴·버튼·기능 이름, 절차 단계, 설정값 — 을 섹션마다 "
    "최소 1개 본문에 그 이름 그대로 쓰고, 그 근거를 sources에 넣어라. 어떤 도구·주제에도 똑같이 "
    "들어맞는 일반론 문장만으로 문단을 채우지 마라. 근거에 없는 이름이나 설정값을 지어내지는 마라. "
    "근거 글의 문장이나 어구는 옮기지 말고 네 말로 풀어 써라. 특정 앱에서만 통하는 메뉴 경로를 "
    "쓸 때는 어느 앱의 메뉴인지 밝히고(업체 사이트라면 아래처럼 익명으로), 한 근거 글의 순서를 그대로 "
    "따라가지 말고 여러 근거를 섞어라. " + VENDOR_NAME_ASK + " " + DOMESTIC_READER_ASK
)


# Each section on its own heading's subject. The same '캡컷 사용법' draft put cutting under
# "기본 설치 및 시작하기" and the final audio check under "자르기와 구간 편집하기" — a subject
# the last section then covered again — and mentioned saving in four places.
SECTION_FOCUS_ASK = (
    "각 섹션의 paragraphs는 그 heading이 말하는 내용만 다뤄라 — 다른 섹션의 주제를 앞당겨 쓰거나 "
    "되풀이하지 마라. 한 섹션의 문단들은 서로 다른 하위 내용(예: 방법 → 예시 → 주의점)을 다루고, "
    "같은 말을 문장만 바꿔 반복하지 마라. intro는 글 전체를 소개만 하고 본문의 절차를 미리 쓰지 마라."
)


def content_request(target: str) -> str:
    """The blog request: the structured contract, the length plan, the standards, and the
    no-invention rule."""
    return (
        f"'{target}' 키워드로 네이버 블로그 글 초안을 작성해라. content_draft 필드에는 아래 형식의 "
        f"JSON 객체 하나만 문자열로 넣어라(마크다운·설명 금지): {_draft_shape(target)}\n"
        "키는 위 순서대로 써라 — 짧은 항목을 먼저 모두 쓰고 intro와 sections를 맨 끝에 써라. 형식의 "
        f"'문단 1({PLAN_SENTENCES_PER_PARAGRAPH}문장)' 같은 자리표시는 글에 옮기지 말고, 문단마다 그 수만큼 "
        f"실제 문장을 채워라. '{target}' 1회라고 표시된 문단(도입 첫 문단, 그리고 섹션 5개 중 3개의 둘째 "
        "문단)에는 그 키워드를 한 번 자연스럽게 넣어라 — 키워드로 문장이나 섹션을 시작하지 말고 문장 "
        "중간에 넣어라.\n"
        f"{_length_plan()}\n"
        f"규칙: title_candidates는 소제목과 별개인 글 제목 3~5개이고 각각 '{target}'를 앞쪽에 "
        f"자연스럽게 포함한다. {_keyword_ask(target)} image_shots 4~8개(after_section은 0부터 센 섹션 번호, 생성 "
        "이미지가 아니라 실제 화면 캡처), 표 1개는 table 필드에만(첫 행이 머리글이고 데이터 행은 3개 이상, 머리글 칸에는 '항목1'·'값' 같은 자리표시 말고 비교하는 대상의 실제 이름을 써라, "
        "paragraphs 안에 ' | ' 행을 쓰지 마라 — 표는 문단 수에 세지 않는다), tags 3~8개, sources 2~5개. 가격·무료 범위·사용 한도·기능 제공 여부·정책·버전·"
        "날짜를 쓴 문장은 모두 fact_checks에 넣어라. 근거 블록([S#]·[K#])에 없는 수치·가격·"
        "출처를 지어내지 마라 — 근거가 없으면 source_ref를 null로 둬라. 문단 안에 #, **, > 같은 "
        f"마크다운 기호를 쓰지 마라. {EVIDENCE_SPECIFICS_ASK} {SECTION_FOCUS_ASK}"
    )


def revision_request(
    target: str, first: Mapping[str, Any], text: str, records: Mapping[str, Any] | None = None,
) -> str:
    """The one revision's request: only the failed items, the facts frozen, the draft attached.

    A length failure also carries the plan against the draft's own measurement: "longer" is what
    the first request already said, and the first package showed that saying it was not enough.

    ``records`` is the CONTENT run's record set. Its evidence goes along as unnumbered notes
    (:func:`_evidence_notes`): the revision has no evidence blocks of its own, and told to grow a
    paragraph with nothing new to say, it grew it with closers."""
    measured = first.get("measured") or {}
    asks = [f"- {_FAILURE_ASKS.get(f, f)} (현재 {measured.get(f, '-')})" for f in first["failures"]
            if f != "keyword_hits"]
    if _LENGTH_FAILURES & set(first["failures"]):
        asks.append(f"- {_length_asks(measured, first.get('structured'))}")
    if "keyword_hits" in first["failures"]:
        asks.append(f"- {_keyword_revision_ask(target, measured, first.get('structured'))}")
    if first.get("repeated"):
        named = ", ".join(f"「{r[:60]}」" for r in first["repeated"][:MAX_NAMED_REPEATS])
        more = f" 외 {len(first['repeated']) - MAX_NAMED_REPEATS}개" if len(first["repeated"]) > MAX_NAMED_REPEATS else ""
        asks.append(f"- 두 번 이상 나온 문장: {named}{more}")
    if _grows(first):
        facts = ("첫 초안의 사실·수치·가격·날짜는 바꾸지 말고 새 출처를 추가하지 마라. 새 사실은 아래 근거 메모에 "
                 "있는 것만 쓸 수 있다 — 메모에 없는 이름·수치를 지어내지 마라. 분량을 늘릴 때는 근거 메모의 구체적인 "
                 "내용이나 이미 쓴 내용의 방법·예시를 풀어 써라. ")
        notes = (f"근거 메모(첫 초안을 쓸 때 본 자료에서 발췌, 번호를 붙여 인용하지 말 것):\n"
                 f"{_evidence_notes(first, records)}\n")
    else:
        facts = "사실·수치·가격·날짜는 바꾸지 말고 새 사실이나 새 출처를 추가하지 마라. "
        notes = ""
    return (
        f"아래 '{target}' 네이버 블로그 초안을 고쳐라. 고칠 항목은 다음뿐이다:\n" + "\n".join(asks)
        + "\n" + facts + "문단을 늘리거나 줄일 때도 그 섹션 소제목의 내용 "
        "안에서만 하고, 다른 섹션의 주제를 끌어오지 마라. image_shots 4~8개와 table은 첫 초안의 것을 "
        "그대로 유지하라(없으면 새로 채워라). 이 수정 실행에는 근거 블록이 없다 — [S1]·[K1] 같은 "
        "근거 번호를 본문·facts·fact_checks 어디에도 쓰지 말고, sources는 빈 목록 []으로 둬라(첫 "
        "초안의 출처는 그대로 유지된다). content_draft에는 같은 JSON 형식으로 전체 초안을 다시 "
        f"넣어라(키는 이 순서대로, intro와 sections를 맨 끝에): {_DRAFT_SHAPE}\n"
        f"{notes}이전 초안:\n{_revision_previous(first, text)}"
    )


def _grows(first: Mapping[str, Any]) -> bool:
    """Whether the revision is asked to ADD length — the only ask the evidence notes serve.

    Sent with every revision (#1070), the notes pushed a cut-and-keyword revision on
    '챗gpt 무료체험' (bcp_ea9ee56c8053fe3810ea, first draft 2,732 characters at 160 a paragraph)
    to 16,553 tokens against the 16,000 budget, and it was blocked; revisions had run 10k~14.8k
    without them. Over the paragraph ceiling the length ask cuts (:func:`_length_asks`), and a
    keyword or structure fix adds no facts."""
    if not _LENGTH_FAILURES & set(first.get("failures") or []):
        return False
    ceiling = blog_draft_score.STANDARDS["para_chars"].high
    return int((first.get("measured") or {}).get("para_chars") or 0) <= ceiling


# Per note and in all: the Tavily snippets run 70~1,300 characters, and the revision request
# already carries the whole first draft under intake's 20,000-character cap.
MAX_EVIDENCE_NOTE_CHARS = 400
MAX_EVIDENCE_NOTES_CHARS = 2_000


def _evidence_notes(first: Mapping[str, Any], records: Mapping[str, Any] | None) -> str:
    """The content run's web evidence as plain notes for the revision: title and snippet, no
    `[S#]` and no URL.

    The sources the first draft cited, when it cited any — the package carries exactly those
    (:func:`_carry_first_evidence`), so what the revision adds from them stays covered.
    Otherwise every hit the run had. Mock rows are not evidence (:func:`blog_draft.evidence_index`)."""
    hits = [(f"S{n}", hit) for n, hit in
            enumerate(((records or {}).get("tool_use") or {}).get("hits") or [], start=1)
            if isinstance(hit, Mapping) and not str(hit.get("source") or "").startswith("mock")]
    cited = {str(s.get("source_ref") or "").strip("[]") for s in first.get("sources") or []}
    chosen = [hit for key, hit in hits if key in cited] or [hit for _key, hit in hits]
    notes: list[str] = []
    total = 0
    for hit in chosen:
        snippet = " ".join(blog_draft.strip_evidence_refs(str(hit.get("snippet") or "")).split())
        if not snippet or blog_draft.looks_garbled(snippet):
            continue          # a mis-decoded page has nothing the revision could use
        title = " ".join(str(blog_draft.readable_title(hit.get("title"), hit.get("url")) or "").split())
        note = f"- {title}: {snippet[:MAX_EVIDENCE_NOTE_CHARS]}" if title else f"- {snippet[:MAX_EVIDENCE_NOTE_CHARS]}"
        if total + len(note) > MAX_EVIDENCE_NOTES_CHARS:
            break
        notes.append(note)
        total += len(note)
    return "\n".join(notes) or "- (없음 — 이미 쓴 내용만으로 고쳐라)"


def _miss(parts: Mapping[str, Any]) -> tuple[int, float]:
    """How far a draft is from passing, for choosing between the first draft and its revision:
    the contract failures (titles, structure) first, then the critical standards' relative
    distance (:func:`blog_draft_score.shortfall`). Smaller is closer."""
    contract = [f for f in parts["failures"] if f not in blog_draft_score.STANDARDS]
    return len(contract), blog_draft_score.shortfall(parts["measured"])


def _miss_detail(second: Mapping[str, Any]) -> str:
    """Why the revision was not taken, in the package: what IT failed, with its numbers."""
    measured = second.get("measured") or {}
    missed = ", ".join(f"{f} {measured[f]}" if f in measured else f for f in second["failures"])
    return f"수정본이 기준에서 더 벗어나 첫 초안을 유지함 (수정본 미달: {missed})"[:300]


def _revision_previous(first: Mapping[str, Any], text: str) -> str:
    """The first draft as the revision sees it: its evidence references taken out.

    The revision is its own governed run, and it has no evidence of its own. Its web search runs
    on this request text and comes back empty, and it runs no keyword brief. So any `[S#]`/`[K#]`
    carried into it cites a source THAT run never had. The pipeline's validation said exactly
    that on 2026-09-28 ("A fact cites a source this run never provided: [S1]") and withheld the
    revision. The first draft's resolved sources are carried into the package instead
    (:func:`_carry_first_evidence`)."""
    if first.get("structured") is not None:
        draft = dict(first["structured"])
        draft["sources"] = []
        draft["fact_checks"] = [{**c, "source_ref": None} for c in draft.get("fact_checks") or []]
        # In the shape's order, so the revision is shown the prose last too.
        draft = {k: draft[k] for k in DRAFT_KEY_ORDER if k in draft}
        previous = json.dumps(draft, ensure_ascii=False)
    else:
        previous = text
    return blog_draft.strip_evidence_refs(previous)


def _carry_first_evidence(first: Mapping[str, Any], second: dict[str, Any]) -> dict[str, Any]:
    """The revised draft, with the evidence the FIRST draft resolved carried over.

    The revision may not change facts, and it ran with no evidence of its own, so its sources
    are necessarily empty and its fact checks unsourced. The first draft's sources did resolve
    against the content run's real evidence and stay true of the same facts. A fact check whose
    claim is unchanged keeps its first-draft state; a claim the revision reworded stays
    `needs_manual_verification`, because the source was checked against the old wording, not
    the new."""
    carried = dict(second)
    carried["sources"] = list(first.get("sources") or [])
    by_claim = {c["claim"]: c for c in first.get("fact_checks") or []
                if c.get("verification_state") == blog_draft.VERIFICATION_SOURCE_CITED}
    carried["fact_checks"] = [by_claim.get(c["claim"], c) for c in second.get("fact_checks") or []]
    measured = dict(second.get("measured") or {})
    if second.get("draft_format") == blog_draft.DRAFT_FORMAT_STRUCTURED:
        measured["sources"] = len(carried["sources"])
        _carry_first_layout(first, carried, measured)
    carried["measured"] = measured
    return carried


def _carry_first_layout(first: Mapping[str, Any], carried: dict[str, Any], measured: dict[str, Any]) -> None:
    """Capture directions and the table a revision dropped, taken from the first draft.

    Measured 2026-09-29: the revision came back with `image_shots: []`, and the package lost all
    of its capture directions. The request now says to keep them. When a revision still drops
    them, the first draft's own (section-addressed, so they survive re-layout) are put back and
    the paste layout is rendered again. Only a structured first draft has them in that form."""
    old = first.get("structured") if first.get("draft_format") == blog_draft.DRAFT_FORMAT_STRUCTURED else None
    new = carried.get("structured")
    if not old or not new:
        return
    patched = dict(new)
    last = max(len(new.get("sections") or []) - 1, 0)
    if not new.get("image_shots") and old.get("image_shots"):
        patched["image_shots"] = [dict(shot, after_section=min(shot["after_section"], last))
                                  for shot in old["image_shots"]]
    if new.get("table") is None and old.get("table") is not None:
        patched["table"] = dict(old["table"], after_section=min(old["table"]["after_section"], last))
    if patched == new:
        return
    rendered = blog_draft.render_blocks(patched)
    carried.update({"structured": patched, "body_paste": rendered["body_paste"],
                    "body_blocks": rendered["body_blocks"][:MAX_BODY_BLOCKS],
                    "image_shots": rendered["image_shots"]})
    measured["images"] = len(rendered["image_shots"])
    measured["tables"] = 1 if patched.get("table") is not None else measured.get("tables", 0)


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
    """
    seeds, target_override, use_queue = parse_request(str(inputs.get("seeds") or ""))
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
    published: PublishedKeywords | None = None
    if target is None:
        # Before any research is spent: without the published posts the rule cannot know what
        # was already written outside the lane, and "nothing" is the one wrong answer here.
        source = published_source if published_source is not None else select_published_source(inputs)
        if source is None:
            raise ToolError(
                PUBLISHED_KEYWORD_SOURCE_UNAVAILABLE,
                f"rule-based selection needs the published posts; set {PUBLISHED_ROOT_ENV} "
                "(or pass target= to name the keyword yourself)",
            )
        published = source.load()
    queue_candidates: list[QueueCandidate] | None = None
    seed_source: dict[str, Any] = {"kind": "request"}
    if use_queue and target is None:
        queue_source = keyword_queue if keyword_queue is not None else VaultKeywordQueue(
            getattr(source, "root", ""))
        loaded = queue_source.load(now)
        queue_candidates, skipped = queue_seeds(
            loaded, already_written=written_keywords(ledger, published),
            already_tagged=published.tags if published is not None else ())
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
            already_written += [str(k) for k in (inputs.get("already_written") or [])]
            target, reasoning = select_target_keyword(
                metrics, already_written=already_written,
                already_tagged=published.tags if published is not None else (),
                queue=queue_candidates,
            )
    if not target:
        raise ToolError(NO_ELIGIBLE_KEYWORD, _no_eligible_message(reasoning))

    mode = "operator_override" if target_override else "rule"
    draft_common = {**common, "provider": draft_provider(common.get("provider"))}
    # The content leg runs the brief on the target (`keyword_seeds=target`), so the target's
    # own evidence is gathered inside the same governed run that drafts against it.
    content = _run(
        "content", content_request(target),
        blocked_code=IDEATION_CONTENT_BLOCKED,
        keyword_seeds=target,   # `run_task` has no `naver_keywords`; the brief keyword is `keyword_seeds`
        # The web search looks for the target, not for the drafting brief: sent whole, the brief
        # (mostly about paragraphs and JSON) found posts about "문단" and GPT prompts.
        search_query=target,
        # The draft and its JSON frame need more than the generic 4,000-token output half
        # (review B9); the profile is bound to `content` runs and refused anywhere else.
        budget_profile=budgets.BLOG_CONTENT_BUDGET_PROFILE,
        **draft_common,
    )
    content_records = content.get("records") or {}
    first = interpret_draft(_draft_text(content), target, content_records)

    # At most ONE automatic revision, and only when the first draft missed something that
    # gates (a critical standard, the structured contract, the three titles). The request
    # names only what failed. Whatever the revision returns, the fire ends with a package:
    # `needs_edit` is a state for a human, not a reason to hide the draft.
    final = first
    revision: Mapping[str, Any] | None = None
    revision_outcome: str | None = None
    revision_detail: str | None = None
    if first["failures"]:
        request = revision_request(target, first, _draft_text(content), content_records)
        if len(request) > MAX_REVISION_REQUEST_CHARS:
            revision_outcome = "REVISION_SKIPPED:REQUEST_TOO_LONG"
        else:
            revision_common = {k: v for k, v in draft_common.items() if k != "source_ref"}
            try:
                revision = _run(
                    "content", request, blocked_code=IDEATION_REVISION_BLOCKED,
                    source_ref=f"{common['source_ref']}:revision",
                    budget_profile=budgets.BLOG_CONTENT_BUDGET_PROFILE, **revision_common,
                )
            except ToolError as exc:
                inner = (exc.data or {})
                revision_outcome = f"REVISION_BLOCKED:{inner.get('inner_reason_code') or exc.reason_code}"
                revision_detail = inner.get("inner_message") or None
            else:
                second = interpret_draft(_draft_text(revision), target, revision.get("records"))
                if (second["draft_format"] != blog_draft.DRAFT_FORMAT_STRUCTURED
                        and first["draft_format"] == blog_draft.DRAFT_FORMAT_STRUCTURED):
                    # A revision that lost the structure is worse than the draft it revised.
                    revision_outcome = "REVISION_UNSTRUCTURED:KEPT_FIRST_DRAFT"
                elif second["failures"] and _miss(second) > _miss(first):
                    # One revision is all there is, so it must not leave the package worse than
                    # the draft it revised: a draft 3 characters over the paragraph ceiling was
                    # once replaced by one 579 characters under the body floor.
                    revision_outcome = "REVISION_FURTHER_OFF:KEPT_FIRST_DRAFT"
                    revision_detail = _miss_detail(second)
                else:
                    final = _carry_first_evidence(first, second)
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
    }
    quality = quality_record(final, first_failures=first["failures"],
                             revision_count=1 if revision is not None else 0,
                             revision_outcome=revision_outcome,
                             revision_detail=revision_detail)
    package = build_package(
        target_keyword=target, draft=final,
        selection=selection_evidence(keyword_record, reasoning, selected_keyword=target,
                                     mode=mode, seeds=seeds, now=now, seed_source=seed_source),
        target=target_evidence(target, target_record, now=now),
        lineage=lineage, quality=quality, now=now,
    )
    try:
        schema_validation.validate_against_schema(
            package, package_schema_path(PACKAGE_SCHEMA_VERSION, repo_root), "blog_content_package")
    except schema_validation.RuntimeSchemaError as exc:
        raise ToolError(BLOG_PACKAGE_SCHEMA_INVALID, str(exc)) from exc
    lines, critical_pass = blog_draft_score.scorecard(final["measured"])
    score = {
        "standards_version": blog_draft_score.STANDARDS_VERSION,
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
        "target_keyword": target,
        "selection": reasoning,
        "score": score,
        "scorecard_lines": lines,
        "target_evidence": package["target_evidence"],
        "lineage": package["lineage"],
        "trace_ids": [t for t in dict.fromkeys(package["lineage"].values()) if t],
        "written": written,
        "files": files,
        "filesystem_write": write_note,
    }

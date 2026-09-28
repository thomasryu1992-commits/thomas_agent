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

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from runtime.read_only_kernel import integrity

from . import blog_draft_score, naver_research, timeutil
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
IDEATION_RESEARCH_BLOCKED = "IDEATION_RESEARCH_BLOCKED"
IDEATION_CONTENT_BLOCKED = "IDEATION_CONTENT_BLOCKED"
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
) -> tuple[str | None, dict[str, Any]]:
    """The most-searched keyword with measured demand that is not already written. Pure.

    Returns ``(keyword, reasoning)``; the reasoning is recorded so a week's choice can be
    argued with rather than believed. Refusing (``None``) when nothing qualifies is a real
    outcome — a fire that drafts against a keyword the rule excluded would be worse than a
    fire that reports it found none. The competition columns ride along in ``considered``
    for the operator and gate nothing (see the note above ``PUBLISHED_ROOT_ENV``).
    """
    considered: list[dict[str, Any]] = []
    best: tuple[int, Mapping[str, Any]] | None = None
    for row in metrics:
        keyword = str(row.get("keyword") or "").strip()
        if not keyword:
            continue
        total = row.get("monthly_total")
        total = int(total) if isinstance(total, (int, float)) else 0
        reason = None
        covered = covering_keyword(keyword, already_written, already_tagged)
        if covered is not None:
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
        considered.append(entry)
        if reason is None and (best is None or total > best[0]):
            best = (total, row)
    reasoning = {
        "rule": "highest measured monthly demand among keywords not already written "
                "(ad competition and blog post counts are recorded, not gated)",
        "considered": considered[:MAX_TAGS],
    }
    if best is None:
        return None, reasoning
    return str(best[1].get("keyword")).strip(), reasoning


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
        evidence["candidates"].append(candidate)
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


def _title_candidates(draft: str, target_keyword: str, parsed: Mapping[str, Any]) -> list[str]:
    """At least one title, because the schema requires it and a package without one is unusable.

    The first heading is the draft's own title when it has one; the keyword is the fallback, so
    this never returns empty for a draft that produced any text at all.
    """
    titles: list[str] = []
    for para in re.split(r"\n\s*\n", draft or ""):
        match = _HEADING_RE.match(para.strip())
        if match:
            text = match.group("text").strip()
            if text and text not in titles:
                titles.append(text)
        if len(titles) >= MAX_TITLES:
            break
    if not titles:
        first = (parsed.get("body_paste") or "").split("\n", 1)[0].strip()
        titles = [first[:80]] if first else [target_keyword]
    return titles[:MAX_TITLES]


def build_package(
    *,
    target_keyword: str,
    draft: str,
    selection: Mapping[str, Any],
    target: Mapping[str, Any],
    lineage: Mapping[str, Any],
    now: str,
) -> dict[str, Any]:
    """Assemble one `blog_content_package.v0.2`. Pure — no ledger, no clock, no I/O."""
    parsed = _parse_draft(draft)
    package: dict[str, Any] = {
        "schema_version": PACKAGE_SCHEMA_VERSION,
        "created_at_utc": now,
        "target_keyword": target_keyword,
        "selection_evidence": dict(selection),
        "target_evidence": dict(target),
        "lineage": dict(lineage),
        "title_candidates": _title_candidates(draft, target_keyword, parsed),
        "body_paste": parsed["body_paste"],
        "body_blocks": parsed["body_blocks"],
        "tags": parsed["tags"],
        "image_shots": parsed["image_shots"],
        # The draft's own sourcing is not machine-extractable from plain text, and inventing
        # entries would be worse than an empty list the operator can see is empty.
        "fact_checks": [],
        "publish_state": "draft",
    }
    package["package_id"] = integrity.short_id("bcp", {
        "target_keyword": target_keyword,
        "body_paste": package["body_paste"],
        "created_at_utc": now,
    })
    return package


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

    lines += ["", "## 제목 후보"]
    lines += [f"{i}. {t}" for i, t in enumerate(package.get("title_candidates") or [], start=1)]

    lines += ["", "## 본문 (PASTE.txt와 동일)", "", str(package.get("body_paste") or "")]

    blocks = package.get("body_blocks") or []
    if blocks:
        lines += ["", "## 편집 지시"]
        lines += [f"- {b.get('paragraph_index')}번째 문단: {b.get('action')} — {b.get('note')}"
                  for b in blocks]

    shots = package.get("image_shots") or []
    if shots:
        lines += ["", "## 캡처 지시 (이미지 생성이 아니라 실제 화면 캡처)"]
        lines += [f"- {s.get('after_paragraph')}번째 문단 뒤: {s.get('what_to_capture')}"
                  f" ({s.get('tool_name')})" for s in shots]

    tags = package.get("tags") or []
    if tags:
        lines += ["", "## 태그", " ".join(f"#{t}" for t in tags)]

    checks = package.get("fact_checks") or []
    lines += ["", "## 발행 전 확인"]
    if checks:
        lines += [f"- [ ] {c.get('claim')} — {c.get('why')}" for c in checks]
    else:
        lines += ["- (기계 추출된 검증 문장 없음 — 가격·무료범위·기능 문장을 직접 표시할 것)"]

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
        verdict = "선정" if c.get("keyword") == evidence.get("selected_keyword") else (
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


def _run(kind: str, request: str, *, blocked_code: str, **kwargs: Any) -> dict[str, Any]:
    result = run_task(request, request_kind=kind, **kwargs)
    if result.get("status") != "COMPLETED":
        block = result.get("block") or {}
        raise ToolError(
            blocked_code,
            f"the {kind} run did not complete: "
            f"{block.get('reason_code') or result.get('status')}",
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
) -> dict[str, Any]:
    """Research -> pick -> draft -> score -> package. Returns the sheet the scheduler renders.

    Raises ``ToolError`` when a governed run blocks or no keyword qualifies; the scheduler
    turns that into the fire's status, which is the same shape a blocked data-review fire has.
    """
    seeds, target_override = parse_seeds(str(inputs.get("seeds") or ""))
    if not seeds and not target_override:
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
            )
    if not target:
        raise ToolError(NO_ELIGIBLE_KEYWORD, _no_eligible_message(reasoning))

    mode = "operator_override" if target_override else "rule"
    content = _run(
        "content",
        f"'{target}' 키워드로 네이버 블로그 글 초안을 작성해라. "
        f"소제목 4~7개, 본문 1,800자 이상, 이미지 지시는 [캡처: …] 형식으로 넣어라.",
        blocked_code=IDEATION_CONTENT_BLOCKED,
        keyword_seeds=target,   # `run_task` has no `naver_keywords`; the brief keyword is `keyword_seeds`
        **common,
    )
    draft = str(content.get("final_response") or "")

    # The target's evidence is the content run's OWN brief (`keyword_seeds=target`), which was
    # previously discarded in favour of the selection brief. The selection brief stays, as what
    # it is: the comparison the choice was made from.
    target_record = (content.get("records") or {}).get("keyword_research")
    content_trace = _trace_id(content)
    lineage = {
        "selection_research_trace_id": _trace_id(research) if research is not None else None,
        "target_research_trace_id": content_trace if target_record is not None else None,
        "content_trace_id": content_trace,
        "revision_trace_id": None,
    }
    package = build_package(
        target_keyword=target, draft=draft,
        selection=selection_evidence(keyword_record, reasoning, selected_keyword=target,
                                     mode=mode, seeds=seeds, now=now),
        target=target_evidence(target, target_record, now=now),
        lineage=lineage, now=now,
    )
    # Scored on the draft the model produced, NOT on `body_paste`: the paste body has had its
    # capture markers and tag lines lifted out, so scoring it would report zero images and zero
    # hashtags for a draft that has both.
    measured = blog_draft_score.measure(draft, target)
    lines, critical_pass = blog_draft_score.scorecard(measured)
    score = {
        "standards_version": blog_draft_score.STANDARDS_VERSION,
        "critical_pass": bool(critical_pass),
        "measured": measured,
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

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

import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from runtime.read_only_kernel import integrity

from . import blog_draft_score, timeutil
from .errors import MvpRuntimeError, ToolError
from .pipeline import run_task

PACKAGE_SCHEMA_VERSION = "blog_content_package.v0.1"
PACKAGE_RECORD_KIND = "blog_content_package"

# The schema's own ceilings, mirrored here so the parser truncates deterministically instead of
# handing the validator a draft-shaped reason to fail the whole fire. A model that emits 24
# capture markers has produced a usable draft with too many notes, not an invalid one.
MAX_TITLES = 5
MAX_BODY_BLOCKS = 100
MAX_TAGS = 30
MAX_IMAGE_SHOTS = 20
MAX_FACT_CHECKS = 30
MAX_METRICS = 50

# The seven fields `keyword_evidence.metrics` names, and the only seven. The brief's own rows
# carry an eighth on their top entries — `run_keyword_brief` attaches `competing_posts` where
# the competition leg answered (`naver_research.py`) — and the schema's item is
# `additionalProperties: false`, so handing the rows straight through fails validation every
# time. Nobody had hit it because nothing had ever assembled a package. The count is not
# dropped: the schema already carries it one level up as `total_competing_posts`, which is
# where a per-evidence total belongs anyway.
_METRIC_FIELDS = (
    "keyword", "monthly_pc", "monthly_mobile", "monthly_total",
    "competition", "low_volume", "source",
)

# Competition rated this high means the first page is already owned; the lane's leverage is in
# measured demand nobody has answered yet (proposal §2). `low_volume` rows are Search Ad's own
# "too small to report" marker and are not a target either.
_UNWINNABLE_COMPETITION = frozenset({"높음", "HIGH", "high"})

NO_ELIGIBLE_KEYWORD = "NO_ELIGIBLE_KEYWORD"
IDEATION_RESEARCH_BLOCKED = "IDEATION_RESEARCH_BLOCKED"
IDEATION_CONTENT_BLOCKED = "IDEATION_CONTENT_BLOCKED"
BLOG_PACKAGE_SCHEMA_INVALID = "BLOG_PACKAGE_SCHEMA_INVALID"

__all__ = [
    "BLOG_PACKAGE_SCHEMA_INVALID",
    "IDEATION_CONTENT_BLOCKED",
    "IDEATION_RESEARCH_BLOCKED",
    "NO_ELIGIBLE_KEYWORD",
    "PACKAGE_RECORD_KIND",
    "build_package",
    "parse_seeds",
    "run_content_ideation",
    "select_target_keyword",
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


def select_target_keyword(
    metrics: Sequence[Mapping[str, Any]], *, already_written: Sequence[str] = ()
) -> tuple[str | None, dict[str, Any]]:
    """The most-searched keyword that is winnable and not already used. Pure and deterministic.

    Returns ``(keyword, reasoning)``; the reasoning is recorded so a week's choice can be
    argued with rather than believed. Refusing (``None``) when nothing qualifies is a real
    outcome — a fire that drafts against a keyword the rule excluded would be worse than a
    fire that reports it found none.
    """
    used = {str(k).strip() for k in already_written if str(k).strip()}
    considered: list[dict[str, Any]] = []
    best: tuple[int, Mapping[str, Any]] | None = None
    for row in metrics:
        keyword = str(row.get("keyword") or "").strip()
        if not keyword:
            continue
        total = row.get("monthly_total")
        total = int(total) if isinstance(total, (int, float)) else 0
        reason = None
        if keyword in used:
            reason = "already written"
        elif row.get("low_volume"):
            reason = "volume below the venue's reporting floor"
        elif str(row.get("competition") or "") in _UNWINNABLE_COMPETITION:
            reason = "competition high"
        elif total <= 0:
            reason = "no measured demand"
        considered.append({"keyword": keyword, "monthly_total": total, "excluded_because": reason})
        if reason is None and (best is None or total > best[0]):
            best = (total, row)
    reasoning = {
        "rule": "highest measured monthly demand among winnable, unused keywords",
        "considered": considered[:MAX_TAGS],
    }
    if best is None:
        return None, reasoning
    return str(best[1].get("keyword")).strip(), reasoning


def _keyword_evidence(record: Mapping[str, Any] | None) -> dict[str, Any]:
    """The brief, in the shape the package schema names. Absent brief is an honest empty one."""
    if not isinstance(record, Mapping):
        return {"as_of": timeutil.utc_now_iso(), "degraded": True,
                "degraded_reason_code": "KEYWORD_BRIEF_ABSENT", "metrics": []}
    rows = [m for m in (record.get("metrics") or []) if isinstance(m, Mapping)]
    # A row missing any of the seven cannot satisfy the item schema, and half a row is not
    # evidence — it is dropped rather than emitted incomplete, which would fail the whole
    # package on the validator and take the draft down with it.
    metrics = [
        {field: row[field] for field in _METRIC_FIELDS}
        for row in rows[:MAX_METRICS]
        if all(field in row for field in _METRIC_FIELDS)
    ]
    evidence: dict[str, Any] = {
        "as_of": str(record.get("created_at") or timeutil.utc_now_iso()),
        "degraded": bool(record.get("degraded")),
        "metrics": metrics,
    }
    legs = record.get("degraded_legs")
    if evidence["degraded"] and legs:
        evidence["degraded_reason_code"] = f"KEYWORD_BRIEF_DEGRADED:{','.join(sorted(legs))}"
    competing = [m.get("competing_posts") for m in rows
                 if isinstance(m.get("competing_posts"), int)]
    if competing:
        evidence["total_competing_posts"] = sum(competing)
    points = record.get("trend_points")
    if points:
        evidence["trend_points"] = list(points)
    return evidence


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
    *, target_keyword: str, draft: str, keyword_record: Mapping[str, Any] | None, now: str,
) -> dict[str, Any]:
    """Assemble one `blog_content_package.v0.1`. Pure — no ledger, no clock, no I/O."""
    parsed = _parse_draft(draft)
    package: dict[str, Any] = {
        "schema_version": PACKAGE_SCHEMA_VERSION,
        "created_at_utc": now,
        "target_keyword": target_keyword,
        "keyword_evidence": _keyword_evidence(keyword_record),
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
    evidence = package.get("keyword_evidence") or {}
    metrics = (evidence.get("metrics") or [{}])[0]
    lines: list[str] = [f"# {package.get('target_keyword')}", ""]

    lines += ["## 선정 근거"]
    lines += [f"- 타깃 키워드: {package.get('target_keyword')}"]
    if metrics:
        lines += [
            f"- 월간검색수: PC {metrics.get('monthly_pc')} / 모바일 {metrics.get('monthly_mobile')}"
            f" (합 {metrics.get('monthly_total')})",
            f"- 경쟁정도: {metrics.get('competition')} / 경쟁 문서수: {evidence.get('total_competing_posts')}",
            f"- 근거 시점: {evidence.get('as_of')} (출처: {metrics.get('source')})",
        ]
    if evidence.get("degraded"):
        lines += ["- ⚠ 근거 수집이 degraded 상태에서 만들어진 패키지다 — 숫자를 재확인할 것"]

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
    reasoning: dict[str, Any] = {"rule": "operator override", "considered": []}
    target = target_override
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
            target, reasoning = select_target_keyword(
                metrics, already_written=inputs.get("already_written") or ()
            )
    if not target:
        raise ToolError(
            NO_ELIGIBLE_KEYWORD,
            "no seed keyword was winnable, in demand and unused; "
            f"considered {len(reasoning.get('considered') or [])}",
        )

    content = _run(
        "content",
        f"'{target}' 키워드로 네이버 블로그 글 초안을 작성해라. "
        f"소제목 4~7개, 본문 1,800자 이상, 이미지 지시는 [캡처: …] 형식으로 넣어라.",
        blocked_code=IDEATION_CONTENT_BLOCKED,
        keyword_seeds=target,   # `run_task` has no `naver_keywords`; the brief keyword is `keyword_seeds`
        **common,
    )
    draft = str(content.get("final_response") or "")

    package = build_package(
        target_keyword=target, draft=draft, keyword_record=keyword_record, now=now
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
        "keyword_evidence": package["keyword_evidence"],
        "trace_ids": [t for t in (
            ((content.get("records") or {}).get("task") or {}).get("identity", {}).get("trace_id"),
        ) if t],
        "written": written,
        "files": files,
        "filesystem_write": write_note,
    }

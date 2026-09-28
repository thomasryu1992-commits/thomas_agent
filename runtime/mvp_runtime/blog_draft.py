"""The blog lane's structured draft: what the model is asked for, and how it becomes a package.

Until 2026-09-28 the content run returned one markdown string and `blog_content` recovered the
post's parts from it with regexes — every heading became a "title candidate" (a section called
'프롬프트 만들기' was offered as the post's title), and nothing could carry a fact to check. The
contract here is explicit fields instead: title candidates separate from section headings, tags,
capture directions tied to a section, the claims that need checking, and the sources the draft
cites. The regex parser in `blog_content` stays as the legacy fallback for a model that answers
in prose anyway, and the package records which path produced it.

**The Role contract is not changed.** `content.general` returns `content_draft: string` and is
hash-bound in `ROLE_REGISTRY.yaml` under Thomas's change control; a blog-shaped required field
there would also force every other content request to produce a blog. So the blog request asks
for a JSON document INSIDE that string, and this module parses and coerces it. Coercion is
deterministic and never invents: an entry of the wrong shape is dropped, a list is truncated
at the package schema's ceiling, and a source reference is kept only if it resolves to evidence
the run actually had.

Pure: no I/O, no clock.
"""

from __future__ import annotations

import json
import re
from typing import Any, Mapping, Sequence

__all__ = [
    "DRAFT_FORMAT_LEGACY",
    "DRAFT_FORMAT_STRUCTURED",
    "MIN_TITLES",
    "detect_fact_checks",
    "evidence_index",
    "fact_checks",
    "parse_structured",
    "render_blocks",
    "resolve_sources",
    "sanitize_paragraph",
]

DRAFT_FORMAT_STRUCTURED = "structured"
DRAFT_FORMAT_LEGACY = "legacy_markdown"

# Title candidates are an explicit field with its own bounds: at least three alternatives for
# the operator to choose from, at most the package schema's five.
MIN_TITLES = 3
MAX_TITLES = 5
MAX_SECTIONS = 12
MAX_PARAGRAPHS_PER_SECTION = 12
MAX_TAGS = 30
MAX_IMAGE_SHOTS = 20
MAX_FACT_CHECKS = 30
MAX_SOURCES = 10

VERIFICATION_NEEDS_MANUAL = "needs_manual_verification"
VERIFICATION_SOURCE_CITED = "source_cited"

_FENCE_RE = re.compile(r"^\s*```[a-zA-Z]*\s*\n?|\n?\s*```\s*$")
_CAPTURE_RE = re.compile(r"\[캡처\s*[::]\s*(?P<what>[^\]]+)\]")
_REF_RE = re.compile(r"^\[?(?P<kind>[SK])(?P<n>\d{1,3})\]?$")
# Markdown a SmartEditor paste would show as literal characters (proposal §4b).
_TABLE_RULE_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")


def _text(value: Any, limit: int) -> str:
    return str(value).strip()[:limit] if isinstance(value, str) else ""


def _str_list(values: Any, *, limit: int, cap: int) -> list[str]:
    if not isinstance(values, list):
        return []
    out: list[str] = []
    for value in values:
        text = _text(value, limit)
        if text and text not in out:
            out.append(text)
    return out[:cap]


def sanitize_paragraph(text: str) -> str:
    """One paragraph with the markdown control syntax removed, line by line.

    Heading hashes, emphasis markers, inline code ticks, quote markers and table rule lines
    are formatting the editor does not interpret; they would land in the post as characters.
    A `' | '` table row is kept — it is plain text the operator turns into a table."""
    lines: list[str] = []
    for line in str(text or "").splitlines():
        if _TABLE_RULE_RE.match(line):
            continue
        line = re.sub(r"^\s{0,3}#{1,6}\s+", "", line)
        line = re.sub(r"^\s{0,3}>\s?", "", line)
        line = line.replace("**", "").replace("__", "").replace("`", "")
        lines.append(line.rstrip())
    return "\n".join(ln for ln in lines if ln.strip()).strip()


def _strip_captures(text: str) -> tuple[str, list[str]]:
    """Capture markers a model left inside a paragraph become directions, not body text."""
    captures = [m.group("what").strip() for m in _CAPTURE_RE.finditer(text)]
    return _CAPTURE_RE.sub("", text).strip(), captures


def parse_structured(text: str) -> tuple[dict[str, Any] | None, str | None]:
    """``(draft, None)`` for a usable structured draft, ``(None, reason)`` otherwise.

    Tolerates exactly two kinds of wrapping a model adds — a code fence, and prose before or
    after the object — by taking the outermost ``{...}``. Anything that is not then a JSON
    object with at least one section holding prose is not a structured draft, and the caller
    falls back to the legacy parser and says so."""
    raw = _FENCE_RE.sub("", str(text or "").strip())
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return None, "NO_JSON_OBJECT"
    try:
        data = json.loads(raw[start:end + 1])
    except ValueError:
        return None, "JSON_UNPARSEABLE"
    if not isinstance(data, dict):
        return None, "JSON_NOT_OBJECT"

    draft_captures: list[tuple[int, str]] = []
    intro: list[str] = []
    for paragraph in _str_list(data.get("intro"), limit=2000, cap=MAX_PARAGRAPHS_PER_SECTION):
        body, captures = _strip_captures(paragraph)
        draft_captures += [(-1, c) for c in captures]
        body = sanitize_paragraph(body)
        if body:
            intro.append(body)

    sections: list[dict[str, Any]] = []
    for item in data.get("sections") if isinstance(data.get("sections"), list) else []:
        if not isinstance(item, dict):
            continue
        heading = sanitize_paragraph(_text(item.get("heading"), 100))
        paragraphs: list[str] = []
        for paragraph in _str_list(item.get("paragraphs"), limit=2000,
                                   cap=MAX_PARAGRAPHS_PER_SECTION):
            body, captures = _strip_captures(paragraph)
            draft_captures += [(len(sections), c) for c in captures]
            body = sanitize_paragraph(body)
            if body:
                paragraphs.append(body)
        if heading and paragraphs:
            sections.append({"heading": heading, "paragraphs": paragraphs})
        if len(sections) >= MAX_SECTIONS:
            break
    if not sections:
        return None, "NO_SECTIONS"

    shots: list[dict[str, Any]] = []
    for item in data.get("image_shots") if isinstance(data.get("image_shots"), list) else []:
        if not isinstance(item, dict):
            continue
        what = _text(item.get("what_to_capture"), 500)
        after = item.get("after_section")
        if not what or not isinstance(after, int) or isinstance(after, bool):
            continue
        shot = {"after_section": min(max(after, -1), len(sections) - 1), "what_to_capture": what}
        tool = _text(item.get("tool_name"), 100)
        if tool:
            shot["tool_name"] = tool
        shots.append(shot)
    shots += [{"after_section": s, "what_to_capture": w} for s, w in draft_captures]

    checks: list[dict[str, Any]] = []
    for item in data.get("fact_checks") if isinstance(data.get("fact_checks"), list) else []:
        if isinstance(item, dict) and _text(item.get("claim"), 500):
            checks.append({
                "claim": _text(item.get("claim"), 500),
                "why": _text(item.get("why"), 300) or "모델이 확인 필요로 표시한 문장",
                "source_ref": _text(item.get("source_ref"), 20) or None,
            })

    sources: list[dict[str, Any]] = []
    for item in data.get("sources") if isinstance(data.get("sources"), list) else []:
        if isinstance(item, dict) and _text(item.get("source_ref"), 20):
            sources.append({"source_ref": _text(item.get("source_ref"), 20),
                            "title": _text(item.get("title"), 200) or None})

    tags = [t.lstrip("#").strip() for t in _str_list(data.get("tags"), limit=50, cap=MAX_TAGS)]
    return {
        "title_candidates": [sanitize_paragraph(t) for t in _str_list(
            data.get("title_candidates"), limit=100, cap=MAX_TITLES) if sanitize_paragraph(t)],
        "intro": intro,
        "sections": sections,
        "tags": [t for t in tags if t],
        "image_shots": shots[:MAX_IMAGE_SHOTS],
        "fact_checks": checks[:MAX_FACT_CHECKS],
        "sources": sources[:MAX_SOURCES],
    }, None


def render_blocks(draft: Mapping[str, Any]) -> dict[str, Any]:
    """The structured draft as the package's paste body and editor directions. Deterministic.

    Paragraph order is intro, then each section's heading followed by its paragraphs. Every
    heading gets a `heading` block at its paragraph index; every capture direction lands after
    the last paragraph of the section it names (intro-level shots after the intro)."""
    paragraphs: list[str] = list(draft.get("intro") or [])
    blocks: list[dict[str, Any]] = []
    section_end: list[int] = []
    for section in draft.get("sections") or []:
        blocks.append({"paragraph_index": len(paragraphs), "action": "heading"})
        paragraphs.append(section["heading"])
        paragraphs.extend(section["paragraphs"])
        section_end.append(len(paragraphs) - 1)
    intro_end = max(len(draft.get("intro") or []) - 1, 0)
    shots = []
    for shot in draft.get("image_shots") or []:
        index = shot["after_section"]
        after = section_end[index] if 0 <= index < len(section_end) else intro_end
        rendered = {"after_paragraph": after, "what_to_capture": shot["what_to_capture"]}
        if shot.get("tool_name"):
            rendered["tool_name"] = shot["tool_name"]
        shots.append(rendered)
    return {"body_paste": "\n\n".join(paragraphs), "body_blocks": blocks,
            "image_shots": shots[:MAX_IMAGE_SHOTS], "paragraph_count": len(paragraphs)}


# --- evidence a draft may cite ----------------------------------------------------------

def evidence_index(records: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    """``{"S1": {...}, "K2": {...}}`` — what a ``[S#]``/``[K#]`` in THIS run's draft points to.

    Numbered exactly as the worker numbers them in the prompt (`_search_context`,
    `_keyword_context`). Mock rows are left out: a reference to a deterministic fixture is not
    a source, and counting it would let a closed gate look like a cited draft."""
    records = records or {}
    index: dict[str, dict[str, Any]] = {}
    for n, hit in enumerate((records.get("tool_use") or {}).get("hits") or [], start=1):
        if isinstance(hit, Mapping) and not str(hit.get("source") or "").startswith("mock"):
            index[f"S{n}"] = {"title": hit.get("title"), "url": hit.get("url")}
    for n, row in enumerate((records.get("keyword_research") or {}).get("metrics") or [], start=1):
        if isinstance(row, Mapping) and not str(row.get("source") or "").startswith("mock"):
            index[f"K{n}"] = {"title": f"Naver 검색광고 키워드 도구: {row.get('keyword')}",
                              "url": None}
    return index


def _resolve(ref: Any, index: Mapping[str, Any]) -> str | None:
    match = _REF_RE.match(str(ref or "").strip())
    if not match:
        return None
    key = f"{match.group('kind')}{int(match.group('n'))}"
    return key if key in index else None


def resolve_sources(sources: Sequence[Mapping[str, Any]], index: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The draft's cited sources that resolve to this run's evidence — the rest are dropped.
    A reference the run never had is an invented source, however plausible its title."""
    out: list[dict[str, Any]] = []
    for source in sources:
        key = _resolve(source.get("source_ref"), index)
        if key is None or any(s["source_ref"] == f"[{key}]" for s in out):
            continue
        entry = {"source_ref": f"[{key}]", "title": index[key].get("title") or source.get("title")}
        if index[key].get("url"):
            entry["url"] = index[key]["url"]
        out.append(entry)
    return out


# --- claims that change without notice ----------------------------------------------------

# Each category names what makes a sentence worth checking before publishing. Order matters
# only for the label: a sentence is flagged once, under the first category that matches.
_CLAIM_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("price", re.compile(r"\d[\d,.]*\s*(원|만\s?원|달러|엔)|[$₩]\s?\d|USD|KRW|가격|요금|구독료"),
     "가격·요금은 공지 없이 바뀐다"),
    ("free_tier", re.compile(r"무료"), "무료 제공 범위는 자주 바뀐다"),
    ("usage_limit", re.compile(r"\d[\d,]*\s*(회|건|개|분|시간|GB|MB|크레딧|토큰|자|장)\s*(까지|이하|이내|제한|한도)|한도|제한"),
     "사용량 한도는 요금제·시점마다 다르다"),
    ("version", re.compile(r"(?i)\bv\d+(\.\d+)*\b|버전|GPT-?\d|\d+\.\d+\s*(모델|버전)"),
     "버전·모델명은 교체된다"),
    ("date", re.compile(r"20\d\d\s*년|20\d\d[-./]\d{1,2}|\d{1,2}\s*월\s*\d{1,2}\s*일"),
     "날짜·기한이 지났을 수 있다"),
    ("api", re.compile(r"\bAPI\b"), "API 제공 여부·조건은 바뀐다"),
    ("policy", re.compile(r"정책|약관|규정|법령|의무화"), "정책·규정은 개정된다"),
    ("availability", re.compile(r"출시|지원(?:합니다|한다|돼|됩니다|하지)|제공(?:합니다|한다|됩니다|하지)|사용할 수 있|이용할 수 있|베타|종료"),
     "기능 제공 여부는 지역·요금제·시점마다 다르다"),
)
_SENTENCE_RE = re.compile(r"[^.!?。\n]+[.!?。]?")


def detect_fact_checks(paragraphs: Sequence[str]) -> list[dict[str, Any]]:
    """Sentences stating something that changes without notice — price, free-tier scope, usage
    limits, versions, dates, API availability, policy, feature availability.

    Deterministic, so a draft that says '무료 플랜은 월 10회까지' is flagged whether or not the
    model listed it. Flagging is all this does: every entry starts
    ``needs_manual_verification``, and nothing here can mark a claim verified."""
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    for paragraph in paragraphs:
        for sentence in _SENTENCE_RE.findall(paragraph):
            sentence = sentence.strip()
            if len(sentence) < 8 or sentence in seen:
                continue
            for category, pattern, why in _CLAIM_PATTERNS:
                if pattern.search(sentence):
                    seen.add(sentence)
                    found.append({"claim": sentence[:500], "why": why, "category": category})
                    break
    return found


def fact_checks(
    model_checks: Sequence[Mapping[str, Any]],
    paragraphs: Sequence[str],
    index: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """The package's pre-publication checklist: the model's flagged claims, then every volatile
    sentence the detector finds that the model did not list.

    ``verification_state`` is ``source_cited`` only when the claim's ``source_ref`` resolves to
    real evidence this run had, and ``needs_manual_verification`` otherwise — including every
    reference to a source the run never saw. Neither is "verified": a cited source is a place
    to check, not a check, and ``verified_at`` stays null until a human records otherwise."""
    out: list[dict[str, Any]] = []
    claims: set[str] = set()
    for check in model_checks:
        claim = str(check.get("claim") or "").strip()[:500]
        if not claim or claim in claims:
            continue
        key = _resolve(check.get("source_ref"), index)
        claims.add(claim)
        out.append({
            "claim": claim,
            "why": str(check.get("why") or "")[:300] or "모델이 확인 필요로 표시한 문장",
            "category": "model_flagged",
            "verification_state": VERIFICATION_SOURCE_CITED if key else VERIFICATION_NEEDS_MANUAL,
            "source_ref": f"[{key}]" if key else None,
            "verified_at": None,
        })
    for check in detect_fact_checks(paragraphs):
        if check["claim"] in claims or any(check["claim"] in c or c in check["claim"] for c in claims):
            continue
        claims.add(check["claim"])
        out.append({**check, "verification_state": VERIFICATION_NEEDS_MANUAL,
                    "source_ref": None, "verified_at": None})
    return out[:MAX_FACT_CHECKS]

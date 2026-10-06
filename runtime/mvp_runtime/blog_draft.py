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
from typing import Any, Iterator, Mapping, Sequence
from urllib.parse import urlparse

__all__ = [
    "DRAFT_FORMAT_LEGACY",
    "DRAFT_FORMAT_STRUCTURED",
    "MIN_TITLES",
    "cited_refs",
    "claim_key",
    "ref_keys",
    "detect_fact_checks",
    "evidence_index",
    "fact_checks",
    "load_object",
    "parse_prose",
    "parse_structured",
    "prose_title_candidates",
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
# The package schema's ceiling on editor directions (`body_blocks`).
MAX_BODY_BLOCKS = 100
# How many whole values back a cut-off draft is tried (`_repairs`): the last one nearly always
# parses; a few more cover a key string that only looked like a list element.
MAX_TAIL_CUTS = 8
# The table is its own field (2026-09-29). Asked to write it as ' | ' rows inside `paragraphs`,
# a model broke the JSON exactly there: it replaced a section's `"heading": …` with the key
# `": | : | :"`, and the draft lost every key after it (tags, image shots, fact checks, sources).
MAX_TABLE_ROWS = 15
MAX_TABLE_CELLS = 6

VERIFICATION_NEEDS_MANUAL = "needs_manual_verification"
VERIFICATION_SOURCE_CITED = "source_cited"

_FENCE_RE = re.compile(r"^\s*```[a-zA-Z]*\s*\n?|\n?\s*```\s*$")
_CAPTURE_RE = re.compile(r"\[캡처\s*[::]\s*(?P<what>[^\]]+)\]")
_REF_RE = re.compile(r"^\[?(?P<kind>[SK])(?P<n>\d{1,3})\]?$")
# A grouped citation — "[S1, S3]", "[S1,K2]", "[S1, 3]". Models write these; the single-ref
# pattern above does not match them.
_GROUP_REF_RE = re.compile(r"\[\s*[SK]\d{1,3}(?:\s*,\s*[SK]?\d{1,3})*\s*\]")
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


_CLOSER = {"{": "}", "[": "]"}


def _is_key(raw: str, quote: int) -> bool:
    """Whether the string opening at ``raw[quote]`` is followed by ``:`` — an object key."""
    i, escaped = quote + 1, False
    while i < len(raw):
        if escaped:
            escaped = False
        elif raw[i] == "\\":
            escaped = True
        elif raw[i] == '"':
            break
        i += 1
    rest = raw[i + 1:].lstrip()
    return rest.startswith(":")


def repair_brackets(raw: str, *, close_at_end: bool = False) -> tuple[str, int] | None:
    """``raw`` with the brackets a model dropped put back, and how many were inserted.

    On 2026-09-29 both drafts of a run wrote the end of the last section as ``…"}]`` instead of
    ``…"]}]`` — the ``]`` closing its ``paragraphs`` was missing — and the whole draft, table,
    capture directions and all, fell to the legacy parser as one 3,893-character paragraph. The
    next run's first draft also opened its sections as ``"sections": ["heading": …`` — the ``{``
    of the first section was missing.

    Two gaps are filled, nothing else. A key (a string followed by ``:``) directly inside a list
    means the object holding it was never opened, so ``{`` goes in front of it. A closer that
    does not match the innermost open bracket but does match an outer one means the brackets in
    between were never closed, so their closers go in front of it. Text inside strings is never
    touched. ``None`` — no repair — when there is nothing to insert, when a closer matches no open
    bracket (a surplus, not a gap), or when the text ends inside a string.

    Brackets still open at the end are closed only with ``close_at_end``: a later run's first
    draft (`bcp_cbbabb9953f3c67ba332`) ended ``…"]}`` after its last section — the ``]`` of
    ``sections`` and the root ``}`` never written, and nothing after them. The closers go on the
    end; whatever the model never wrote (tags, capture directions) stays absent, never filled.
    Without it, open brackets at the end are no repair. The caller still requires the result to
    parse — a text cut after a ``,`` or a ``:`` does not."""
    out: list[str] = []
    stack: list[str] = []
    inserted = 0
    in_string = escaped = False
    for pos, ch in enumerate(raw):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            if stack and stack[-1] == "[" and _is_key(raw, pos):
                out.append("{")
                stack.append("{")
                inserted += 1
            in_string = True
        elif ch in _CLOSER:
            stack.append(ch)
        elif ch in ("}", "]"):
            if not stack:
                return None
            if _CLOSER[stack[-1]] != ch:
                depth = next((i for i in range(len(stack) - 1, -1, -1)
                              if _CLOSER[stack[i]] == ch), None)
                if depth is None:
                    return None
                while len(stack) - 1 > depth:
                    out.append(_CLOSER[stack.pop()])
                    inserted += 1
            stack.pop()
        out.append(ch)
    if in_string or (stack and not close_at_end):
        return None
    while stack:
        out.append(_CLOSER[stack.pop()])
        inserted += 1
    if not inserted:
        return None
    return "".join(out), inserted


def _complete_ends(raw: str) -> list[int]:
    """Where a text that was CUT OFF could end on a whole value: just after each closer, and just
    after each string inside a list — ``[]`` for a text that was not cut (nothing left open at
    its end) or that closes a bracket it never opened.

    `bcp_63185d84aceaf646ff6e` ('클로바노트 유료', 2026-10-06): the first draft stopped at
    ``…, {"heading": "개인용 무료 한도 …", "level": 3,`` — after a comma, which closing at the end
    cannot mend. Cut back to the last whole value, its four sections, citations and capture
    directions were all there; it went to the prose parser instead, and the revision that
    rebuilt the structure had no first-draft citations to carry back (every [S#] lost)."""
    ends: list[int] = []
    stack: list[str] = []
    in_string = escaped = False
    for pos, ch in enumerate(raw):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
                if stack and stack[-1] == "[":
                    ends.append(pos + 1)
        elif ch == '"':
            in_string = True
        elif ch in _CLOSER:
            stack.append(ch)
        elif ch in ("}", "]"):
            while stack and _CLOSER[stack[-1]] != ch:      # a dropped closer: repair_brackets' gap
                stack.pop()
            if not stack:
                return []
            stack.pop()
            ends.append(pos + 1)
    return ends if (stack or in_string) else []


def _repairs(raw: str, whole: str) -> Iterator[tuple[str, int] | None]:
    """The repairs :func:`load_object` tries, in order: the outermost ``{...}``; the text to its
    end with what it left open closed there; and, for a text cut mid-value, the text cut back to
    each of its last whole values (:func:`_complete_ends`) and closed. The cut drops only the
    unfinished tail — a half-written element is never completed, a missing key never filled."""
    yield repair_brackets(raw)
    yield repair_brackets(whole, close_at_end=True)
    for end in reversed(_complete_ends(whole)[-MAX_TAIL_CUTS:]):
        yield repair_brackets(whole[:end], close_at_end=True)


def _loads(text: str) -> Any:
    """``json.loads`` that accepts a raw control character (a newline, a tab) inside a string.

    Strict JSON refuses them, and on 2026-09-29 (`bcp_5e855e2073b66b805cfd`) one raw newline at
    the end of one paragraph sent an otherwise complete draft — every key, in order — to the
    legacy parser. The character is kept as written; `sanitize_paragraph` already treats a
    newline inside a paragraph as a line break and drops a trailing one."""
    return json.loads(text, strict=False)


def load_object(text: str) -> tuple[dict[str, Any] | None, int, str | None]:
    """``(object, brackets_inserted, None)`` for text that holds one JSON object, ``(None, 0,
    reason)`` otherwise — the tolerance half of :func:`parse_structured`, shared with a platform
    that reads its own extra keys from the same object (`blog_tistory`)."""
    raw = _FENCE_RE.sub("", str(text or "").strip())
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return None, 0, "NO_JSON_OBJECT"
    whole = raw[start:].rstrip()
    raw = raw[start:end + 1]
    inserted = 0
    try:
        data = _loads(raw)
    except ValueError:
        # The outermost {...} first, as before; then the text to its end, closed there; then a
        # text cut mid-value, cut back to a whole value and closed (`_repairs`).
        for repaired in _repairs(raw, whole):
            if repaired is None:
                continue
            try:
                data = _loads(repaired[0])
            except ValueError:
                continue
            inserted = repaired[1]
            break
        else:
            return None, 0, "JSON_UNPARSEABLE"
    if not isinstance(data, dict):
        return None, 0, "JSON_NOT_OBJECT"
    return data, inserted, None


def parse_structured(text: str, *, extended: bool = False) -> tuple[dict[str, Any] | None, str | None]:
    """``(draft, None)`` for a usable structured draft, ``(None, reason)`` otherwise.

    Tolerates exactly two kinds of wrapping a model adds — a code fence, and prose before or
    after the object — by taking the outermost ``{...}``, and two kinds of damage: brackets left
    out (:func:`repair_brackets`), including a draft that stopped with brackets still open, and
    raw control characters inside strings (:func:`_loads`). Anything that is not then a JSON object with at
    least one section holding prose is not a structured draft, and the caller falls back to the
    legacy parser and says so. A repaired draft says how many closers it needed in
    ``brackets_inserted``; the key is 0 otherwise.

    ``extended`` keeps two fields only a markdown platform renders: a section's heading
    ``level`` (2 or 3) and a capture direction's ``alt_text``. Off, the draft is exactly what it
    always was — the Naver revision re-serializes it, so an extra key would change its request."""
    data, inserted, reason = load_object(text)
    if data is None:
        return None, reason
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
            section = {"heading": heading, "paragraphs": paragraphs}
            if extended:
                level = item.get("level")
                section["level"] = level if level in (2, 3) and not isinstance(level, bool) else 2
                key = sanitize_paragraph(_text(item.get("key_sentence"), 300))
                if key:
                    section["key_sentence"] = key
            sections.append(section)
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
        if extended:
            alt = sanitize_paragraph(_text(item.get("alt_text"), 200))
            if alt:
                shot["alt_text"] = alt
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
        "table": _parse_table(data.get("table"), len(sections)),
        "title_candidates": [sanitize_paragraph(t) for t in _str_list(
            data.get("title_candidates"), limit=100, cap=MAX_TITLES) if sanitize_paragraph(t)],
        "intro": intro,
        "sections": sections,
        "tags": [t for t in tags if t],
        "image_shots": shots[:MAX_IMAGE_SHOTS],
        "fact_checks": checks[:MAX_FACT_CHECKS],
        "sources": sources[:MAX_SOURCES],
        "brackets_inserted": inserted,
    }, None


def _parse_table(value: Any, section_count: int) -> dict[str, Any] | None:
    """``{"after_section": n, "rows": [[cell, …], …]}`` or None. At least a header and one row;
    cells lose any '|' of their own (it is the rendered separator) and are capped; a malformed
    table is dropped, never guessed at."""
    if not isinstance(value, dict):
        return None
    rows: list[list[str]] = []
    for row in value.get("rows") if isinstance(value.get("rows"), list) else []:
        if not isinstance(row, list):
            continue
        cells = [sanitize_paragraph(str(c)).replace("|", "/").strip() for c in row[:MAX_TABLE_CELLS]
                 if isinstance(c, (str, int, float)) and str(c).strip()]
        if len(cells) >= 2:
            rows.append(cells)
    if len(rows) < 2:
        return None
    after = value.get("after_section")
    after = after if isinstance(after, int) and not isinstance(after, bool) else section_count - 1
    return {"after_section": min(max(after, 0), max(section_count - 1, 0)),
            "rows": rows[:MAX_TABLE_ROWS]}


def table_paragraph(table: Mapping[str, Any]) -> str:
    """The table as one paste paragraph: a row per line, cells joined by ' | '."""
    return "\n".join(" | ".join(row) for row in table["rows"])


def render_blocks(draft: Mapping[str, Any]) -> dict[str, Any]:
    """The structured draft as the package's paste body and editor directions. Deterministic.

    Paragraph order is intro, then each section's heading followed by its paragraphs. Every
    heading gets a `heading` block at its paragraph index; every capture direction lands after
    the last paragraph of the section it names (intro-level shots after the intro)."""
    paragraphs: list[str] = list(draft.get("intro") or [])
    blocks: list[dict[str, Any]] = []
    section_end: list[int] = []
    table = draft.get("table")
    for index, section in enumerate(draft.get("sections") or []):
        blocks.append({"paragraph_index": len(paragraphs), "action": "heading"})
        paragraphs.append(section["heading"])
        paragraphs.extend(section["paragraphs"])
        if table and table["after_section"] == index:
            paragraphs.append(table_paragraph(table))
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


# --- the legacy prose draft (a model that answered in prose anyway) ------------------------

# The `\s+` after the hashes is load-bearing, not style. A Korean hashtag line —
# `#미리캔버스 #포스터제작` — also begins with `#`, and without the required space this regex
# claimed it as a heading, so every draft's tag line became a title and `tags` came back empty.
_PROSE_HEADING_RE = re.compile(r"^\s{0,3}#{1,4}\s+(?P<text>.+?)\s*$")
_PROSE_CAPTURE_RE = re.compile(r"\[캡처:\s*(?P<what>[^\]]+)\]")
_PROSE_TAG_RE = re.compile(r"#([^\s#]{1,40})")


def parse_prose(draft: str) -> dict[str, Any]:
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
        captures = _PROSE_CAPTURE_RE.findall(para)
        body = _PROSE_CAPTURE_RE.sub("", para).strip()
        # Asked first: a paragraph that is only hashtags is the draft's tag line, not a
        # paragraph and not a heading.
        if body and not _PROSE_TAG_RE.sub("", body).strip() and _PROSE_TAG_RE.search(body):
            tags.extend(_PROSE_TAG_RE.findall(body))
            continue
        heading = _PROSE_HEADING_RE.match(body)
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
        trailing = _PROSE_TAG_RE.findall(kept[-1])
        if trailing and not _PROSE_TAG_RE.sub("", kept[-1]).strip():
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


_PROSE_TITLE_RE = re.compile(r"^\s{0,3}#\s+(?P<text>.+?)\s*$")


def prose_title_candidates(draft: str) -> list[str]:
    """Titles from a prose draft: its level-1 headings (``# …``) only.

    Every heading used to qualify, so a section called '프롬프트 만들기' was offered as the post's
    title. A section heading is a section; a draft with no ``#`` title line yields no candidate,
    and the quality check names the gap rather than a heading standing in for one."""
    titles: list[str] = []
    for line in (draft or "").splitlines():
        match = _PROSE_TITLE_RE.match(line)
        if match:
            text = match.group("text").strip()
            if text and text not in titles:
                titles.append(text[:100])
    return titles[:MAX_TITLES]


# --- evidence a draft may cite ----------------------------------------------------------

# A page served in EUC-KR and read as something else arrives from the search tool with its
# Hangul already gone: gov.kr's "식품영업신고 | 민원안내 및 신청 | 정부24" came back as
# "ǰ û | οȳ  û | 24" ('영업신고증 발급', bcp_5e11bdec987364f7116c) and was printed as a source
# title. The bytes are lost, so nothing can be decoded back; what can be done is to recognise it.
# Korean and English text never reach these blocks (Latin Extended-A/B, IPA, Greek, Armenian
# through Thaana), and two of them in one short title is not a foreign word.
_GARBLED_RE = re.compile(r"[\u0100-\u03ff\u0530-\u07bf]")
MIN_GARBLED_CHARS = 2


def looks_garbled(text: Any) -> bool:
    """Whether ``text`` carries the marks of a mis-decoded Korean page."""
    return len(_GARBLED_RE.findall(str(text or ""))) >= MIN_GARBLED_CHARS


def readable_title(title: Any, url: Any = None) -> str | None:
    """``title`` unless it is garbled; then the URL's host, which is at least true."""
    if title and not looks_garbled(title):
        return str(title)
    host = urlparse(str(url or "")).netloc
    return host or None


def evidence_index(records: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    """``{"S1": {...}, "K2": {...}}`` — what a ``[S#]``/``[K#]`` in THIS run's draft points to.

    Numbered exactly as the worker numbers them in the prompt (`_search_context`,
    `_keyword_context`). Mock rows are left out: a reference to a deterministic fixture is not
    a source, and counting it would let a closed gate look like a cited draft."""
    records = records or {}
    index: dict[str, dict[str, Any]] = {}
    for n, hit in enumerate((records.get("tool_use") or {}).get("hits") or [], start=1):
        if isinstance(hit, Mapping) and not str(hit.get("source") or "").startswith("mock"):
            index[f"S{n}"] = {"title": readable_title(hit.get("title"), hit.get("url")), "url": hit.get("url")}
    for n, row in enumerate((records.get("keyword_research") or {}).get("metrics") or [], start=1):
        if isinstance(row, Mapping) and not str(row.get("source") or "").startswith("mock"):
            index[f"K{n}"] = {"title": f"Naver 검색광고 키워드 도구: {row.get('keyword')}",
                              "url": None}
    return index


def _keys(ref: Any) -> list[str]:
    """Every evidence key a reference names: "[S1]" -> ["S1"]; "[S1, S3]" -> ["S1", "S3"];
    "[S1, 3]" -> ["S1", "S3"] (a bare number continues the previous kind)."""
    text = str(ref or "").strip()
    match = _REF_RE.match(text)
    if match:
        return [f"{match.group('kind')}{int(match.group('n'))}"]
    if not _GROUP_REF_RE.fullmatch(text):
        return []
    keys: list[str] = []
    kind = "S"
    for part in text.strip("[] ").split(","):
        part = part.strip()
        if part[:1] in ("S", "K"):
            kind, part = part[0], part[1:]
        if part.isdigit():
            keys.append(f"{kind}{int(part)}")
    return keys


def _resolve(ref: Any, index: Mapping[str, Any]) -> str | None:
    """The first key of ``ref`` this run's evidence has, or None."""
    return next((key for key in _keys(ref) if key in index), None)


def ref_keys(ref: Any) -> list[str]:
    """Every evidence key one citation names: "[S1, 3]" -> ["S1", "S3"]."""
    return _keys(ref)


def cited_refs(text: str) -> list[str]:
    """Every single or grouped ``[S#]``/``[K#]`` citation in ``text``, in order — for a draft
    whose JSON could not be read, the citations it still carries."""
    return _GROUP_REF_RE.findall(str(text or ""))


def strip_evidence_refs(text: str) -> str:
    """``text`` with every single or grouped ``[S#]``/``[K#]`` citation removed."""
    return _GROUP_REF_RE.sub("", str(text or ""))


# A shop's page title ends in the shop's name: "현수막 제작 | 플랜카드·플래카드 당일제작 | 네모디".
# The request forbids business names, yet '현수막당일제작' (bcp_d624e52360df22c9b825) wrote "네모디
# 같은 곳" beside two shops it had made anonymous. Whether a name is a shop or a tool is not
# something this can tell ('워드바이스 AI' ends a title the same way), so the name is pointed at
# for the editor, never failed. Over 27 packages the names found in a body were 비즈하우스, 마플,
# 네모디 (shops) and 워드바이스 AI, 페이퍼팔 (tools).
_TITLE_SPLIT_RE = re.compile(r"\s[|｜\-–—:]\s|\s?[|｜]\s?")
_SITE_NAME_CHARS = (2, 10)


def source_site_name(title: Any) -> str | None:
    """The site name a page title ends with, when the last segment is a short Korean name."""
    parts = [p.strip() for p in _TITLE_SPLIT_RE.split(str(title or "")) if p.strip()]
    if len(parts) < 2:
        return None
    last = re.sub(r"\s*\(.*?\)\s*", "", parts[-1]).strip()
    low, high = _SITE_NAME_CHARS
    if not re.search(r"[가-힣]", last) or not low <= len(last.replace(" ", "")) <= high:
        return None
    return last


def site_names_in_body(sources: Sequence[Mapping[str, Any]], paragraphs: Sequence[str],
                       target_keyword: str = "") -> list[str]:
    """Each cited source's site name that the body uses (spacing ignored), unless the name is
    part of the target keyword itself ('카카오' in '카카오톡 채널추가')."""
    body = re.sub(r"\s", "", "".join(str(p) for p in paragraphs))
    target = re.sub(r"\s", "", str(target_keyword or ""))
    found: list[str] = []
    for source in sources:
        name = source_site_name(source.get("title"))
        key = re.sub(r"\s", "", name or "")
        if key and key in body and key not in target and name not in found:
            found.append(name)
    return found


def resolve_sources(sources: Sequence[Mapping[str, Any]], index: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The draft's cited sources that resolve to this run's evidence — the rest are dropped.
    A reference the run never had is an invented source, however plausible its title."""
    out: list[dict[str, Any]] = []
    for source in sources:
        for key in _keys(source.get("source_ref")):
            if key not in index or any(s["source_ref"] == f"[{key}]" for s in out):
                continue
            entry = {"source_ref": f"[{key}]",
                     "title": index[key].get("title") or readable_title(source.get("title"))}
            if index[key].get("url"):
                entry["url"] = index[key]["url"]
            out.append(entry)
    return out[:MAX_SOURCES]


# --- claims that change without notice ----------------------------------------------------

# Each category names what makes a sentence worth checking before publishing. Order matters
# only for the label: a sentence is flagged once, under the first category that matches.
_CLAIM_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    # A price word alone is advice ("가격만 보고 고르지 마라"), not a price: it needs a number in
    # the same sentence. 9 of 11 checks on '명함제작업체' (2026-09-30) were that advice.
    ("price", re.compile(r"\d[\d,.]*\s*(원|만\s?원|달러|엔)|[$₩]\s?\d|USD|KRW|^(?=.*\d).*(가격|요금|구독료)"),
     "가격·요금은 공지 없이 바뀐다"),
    ("free_tier", re.compile(r"무료"), "무료 제공 범위는 자주 바뀐다"),
    ("usage_limit", re.compile(r"\d[\d,]*\s*(회|건|개|분|시간|GB|MB|크레딧|토큰|자|장)\s*(까지|이하|이내|제한|한도)|^(?=.*\d).*(한도|제한)"),
     "사용량 한도는 요금제·시점마다 다르다"),
    # "구버전 쿠폰은 폐기하라" is not a version claim; a version needs a number or a model name.
    ("version", re.compile(r"(?i)\bv\d+(\.\d+)*\b|^(?=.*\d).*버전|GPT-?\d|\d+\.\d+\s*(모델|버전)"),
     "버전·모델명은 교체된다"),
    ("date", re.compile(r"20\d\d\s*년|20\d\d[-./]\d{1,2}|\d{1,2}\s*월\s*\d{1,2}\s*일"),
     "날짜·기한이 지났을 수 있다"),
    ("api", re.compile(r"\bAPI\b"), "API 제공 여부·조건은 바뀐다"),
    ("policy", re.compile(r"정책|약관|규정|법령|의무화"), "정책·규정은 개정된다"),
    ("availability", re.compile(r"출시|지원(?:합니다|한다|돼|됩니다|하지)|제공(?:합니다|한다|됩니다|하지)|사용할 수 있|이용할 수 있|베타|종료"),
     "기능 제공 여부는 지역·요금제·시점마다 다르다"),
)
# A period between two digits is a decimal point, not a sentence end: "31.25달러부터" was cut
# into "…31." and "25달러부터 시작하는 등…" (2026-09-30).
_SENTENCE_RE = re.compile(r"(?:[^.!?。\n]|(?<=\d)\.(?=\d))+[.!?。]?")


# A sentence that tells the reader what to do, announces the post, or only says a thing may
# change states nothing to check: of 16 checks on 'CHATGPT요금제' (2026-09-30) most were
# "약관을 다시 한번 확인해야 합니다" and "정책이나 가격은 향후 변경될 수 있음을 염두에 두어야
# 합니다". A digit keeps the sentence in ("월 10회까지이니 아껴 써야 합니다" still carries a limit).
_NOT_A_CLAIM_RE = re.compile(
    r"(?:야\s*(?:합니다|한다|해요)"
    r"|(?:것이|편이)\s*(?:좋|안전|중요|현명|유리|합리적|바람직|낫)\S*"
    r"|필요(?:합니다|하다|해요)"
    r"|(?:세요|십시오|바랍니다)"
    r"|(?:살펴|알아)\s*보겠습니다"
    r"|(?:달라질|바뀔|변경될|조정될|다를)\s*수\s*있(?:습니다|다|어요))[.!?。]?$"
)


def _states_nothing(sentence: str) -> bool:
    return not re.search(r"\d", sentence) and bool(_NOT_A_CLAIM_RE.search(sentence))


def claim_key(claim: str) -> str:
    """A claim with spacing, punctuation and its sentence ending taken off, for telling that the
    model's "…제공한다." and the detector's "…제공합니다." are the same sentence."""
    text = re.sub(r"[\s.!?。,'\"]", "", claim)
    return re.sub(r"(합니다|습니다|입니다|한다|된다|이다|다|요)$", "", text)


# A sentence this long, seen twice in one post, is a copy — not a common phrase. On candidate-1073
# '통신판매업 신고증' (bcp_371db05812bb56548597) the revision grew a 1,601-character body to 2,260
# by pasting sentences from neighbouring paragraphs: 17 of 69 sentences were repeats, and the two
# intro paragraphs were the same four sentences reordered. Drafts before it had none.
MIN_REPEATED_SENTENCE_CHARS = 15


def repeated_sentences(paragraphs: Sequence[str]) -> list[str]:
    """Each sentence that appears more than once across ``paragraphs``, once, in order of its
    second appearance. Spacing and punctuation are ignored for the comparison; a table row and
    a sentence shorter than :data:`MIN_REPEATED_SENTENCE_CHARS` visible characters are not
    counted."""
    seen: set[str] = set()
    repeated: dict[str, str] = {}
    for paragraph in paragraphs:
        for sentence in _SENTENCE_RE.findall(str(paragraph)):
            sentence = sentence.strip()
            key = re.sub(r"[\s.!?。,'\"]", "", sentence)
            if len(key) < MIN_REPEATED_SENTENCE_CHARS or " | " in sentence:
                continue
            if key in seen:
                repeated.setdefault(key, sentence)
            seen.add(key)
    return list(repeated.values())


def repeat_count(paragraphs: Sequence[str]) -> int:
    """How many sentences are extra copies: 2 appearances of one sentence count 1."""
    keys = [re.sub(r"[\s.!?。,'\"]", "", s.strip()) for p in paragraphs
            for s in _SENTENCE_RE.findall(str(p)) if " | " not in s]
    keys = [k for k in keys if len(k) >= MIN_REPEATED_SENTENCE_CHARS]
    return len(keys) - len(set(keys))


# A sentence that says the one before it again in other words. On candidate-1076 '카카오톡
# 채널추가' (bcp_728c1a0f771268689758) the padding revision closed paragraph after paragraph with
# one ("…채널추가 버튼을 누르면 즉시 친구 등록이 완료됩니다. 버튼을 누르는 순간 바로 등록이
# 끝납니다."). Measured over 21 drafts as the share of a sentence's character pairs already used
# earlier in its paragraph, 0.45 flags three of that draft's sentences and none in the other 20.
# Reworded echoes mostly score lower than that, so this is a pointer for the editor, not a gate:
# at any cut that catches more, sound sentences in good drafts start to show.
ECHO_OVERLAP = 0.45


def _pairs(text: str) -> set[str]:
    return {text[i:i + 2] for i in range(len(text) - 1)}


def echo_sentences(paragraphs: Sequence[str]) -> list[str]:
    """Each sentence at least :data:`ECHO_OVERLAP` of whose character pairs already appear in one
    earlier sentence of the same paragraph. Spacing and punctuation are ignored; table rows and
    sentences under :data:`MIN_REPEATED_SENTENCE_CHARS` are skipped."""
    found: list[str] = []
    for paragraph in paragraphs:
        sentences = [x.strip() for x in _SENTENCE_RE.findall(str(paragraph)) if x.strip()]
        keys = [re.sub(r"[\s.!?。,'\"]", "", x) for x in sentences]
        for i in range(1, len(sentences)):
            if len(keys[i]) < MIN_REPEATED_SENTENCE_CHARS or " | " in sentences[i]:
                continue
            pairs = _pairs(keys[i])
            if any(len(pairs & _pairs(keys[j])) / len(pairs) >= ECHO_OVERLAP for j in range(i)):
                found.append(sentences[i])
    return found


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
            if len(sentence) < 8 or sentence in seen or " | " in sentence or _states_nothing(sentence):
                continue          # a table row, advice or a hedge is not a sentence claiming anything
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
        if not claim or claim_key(claim) in claims:
            continue
        key = _resolve(check.get("source_ref"), index)
        claims.add(claim_key(claim))
        out.append({
            "claim": claim,
            "why": str(check.get("why") or "")[:300] or "모델이 확인 필요로 표시한 문장",
            "category": "model_flagged",
            "verification_state": VERIFICATION_SOURCE_CITED if key else VERIFICATION_NEEDS_MANUAL,
            "source_ref": f"[{key}]" if key else None,
            "verified_at": None,
        })
    for check in detect_fact_checks(paragraphs):
        found = claim_key(check["claim"])
        if found in claims or any(found in c or c in found for c in claims):
            continue
        claims.add(found)
        out.append({**check, "verification_state": VERIFICATION_NEEDS_MANUAL,
                    "source_ref": None, "verified_at": None})
    return out[:MAX_FACT_CHECKS]

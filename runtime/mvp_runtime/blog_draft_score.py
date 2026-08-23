"""The lane's operating standards for a Naver blog draft, and the measurement of them.

**These numbers are not Naver's published cutoffs.** They are Thomas's practical operating
standards for this blog (stated 2026-08-10), and this module is where they live — one place,
checkable, rather than a table copied into a doc, a prompt and a reviewer's head.

Why measurement at all: the first two drafts the lane produced both looked fine and were both
**half the minimum length** — 990 and 1,019 characters against a 1,800 floor. Nobody noticed
until it was measured. Reading a draft does not reveal its length; counting does.

This was `scripts/score_blog_draft.py` alone until 2026-08-23, which meant the standard could
only be applied by a human who remembered to run it — and for the two drafts above, nobody did.
Moving the measurement into the runtime lets the lane score its own output at the moment it
produces it; the script stays as the hand-run CLI over the same code, so there is still exactly
one place the numbers live.

Measurement honesty: character counts and paragraph splits are exact. Headings, tables and
sources are **heuristics** over plain text — the paste format carries no markup by design
(SmartEditor does not interpret markdown), so there is nothing unambiguous to count. Each
heuristic names itself in the output so a surprising number can be argued with rather than
believed.

Pure: no I/O, no clock, no state. `measure` and `scorecard` are the whole surface.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Stamped into every score record so a rendered scorecard can be read against the standards
# that produced it. Bump when a number in STANDARDS moves — a score is only comparable to
# another score taken under the same bar.
STANDARDS_VERSION = "blog_draft_standards.2026-08-10"

__all__ = ["STANDARDS", "STANDARDS_VERSION", "Standard", "measure", "scorecard"]

# --- the standards (Thomas, 2026-08-10) ------------------------------------------------
#
# `critical` marks the ★★★★★ rows: length, heading count and paragraph length. Those three
# are what a reader feels immediately and what the dwell-time signal reflects, so they gate.
# The rest are reported and do not.

@dataclass(frozen=True)
class Standard:
    label: str
    low: int
    high: int | None  # None = no upper bound ("1개 이상"), rendered as "N+"
    critical: bool = False
    note: str = ""

    def within(self, value: int) -> bool:
        return value >= self.low and (self.high is None or value <= self.high)

    def rendered_bound(self) -> str:
        return f"{self.low}+" if self.high is None else f"{self.low}~{self.high}"


STANDARDS: dict[str, Standard] = {
    "body_chars": Standard("본문 글자수 (공백 제외)", 1800, 3500, critical=True),
    "headings": Standard("소제목", 4, 7, critical=True, note="heuristic: short unpunctuated line"),
    "paragraphs": Standard("문단", 10, 20),
    "para_chars": Standard("문단 평균 글자", 70, 150, critical=True),
    "images": Standard("이미지 지시", 4, 8, note="counts [캡처: …] markers"),
    "tables": Standard("표", 1, None, note="heuristic: a line containing ' | '"),
    "sources": Standard("출처", 2, 5, note="heuristic: http(s) links or [S#]"),
    "keyword_hits": Standard("핵심 키워드 반복", 3, 6, note="needs --keyword"),
    "hashtags": Standard("해시태그", 3, 8, note="counts #word"),
}

# A heading in the paste format is a short line standing alone in its paragraph with no
# sentence-ending punctuation — that is the shape the lane's own drafts use. Deliberately
# conservative: a long line is prose even if it looks like a title.
#
# The length rule is the weak half of the heuristic and the punctuation rule is the strong
# one: Korean prose ends in 다./요./까? and a heading does not. 40 leaves room for a real
# title ("미리캔버스 포스터 제작, 소상공인을 위한 30분 완성 가이드" is 31) without
# swallowing body paragraphs, which run 70-150.
_HEADING_MAX_CHARS = 40
_SENTENCE_ENDINGS = ".!?。？！"
# Lines that are structurally something else. Found by testing the heuristic rather than
# trusting it: a table row and a source line are both short, standalone and unpunctuated, so
# the shape rule alone counted each as a heading and inflated the count by two.
_NOT_HEADING_MARKERS = (" | ", "http://", "https://")
_NOT_HEADING_PREFIXES = ("[", "#", "출처", "참고", "-", "*", "•")


def _visible_chars(text: str) -> int:
    """Characters excluding all whitespace — the count Korean blog guidance means."""
    return len(re.sub(r"\s", "", text))


def _paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def _is_heading_line(line: str) -> bool:
    """Whether one line reads as a heading. Shape only — placement is the caller's rule."""
    line = line.strip()
    if not line or _visible_chars(line) > _HEADING_MAX_CHARS:
        return False
    if line.endswith(tuple(_SENTENCE_ENDINGS)):
        return False
    if any(marker in line for marker in _NOT_HEADING_MARKERS):
        return False
    return not line.startswith(_NOT_HEADING_PREFIXES)


def _heading_lines(paragraph: str) -> list[str]:
    """Heading lines in a paragraph — at most its first.

    Per LINE rather than per paragraph, because real drafts glue a section heading to the
    prose beneath it with a single newline ("2. 문구 바꾸기" then straight into the sentence).
    The first version required a heading to stand alone in its own paragraph and scored a
    draft with four sections as having zero — a false FAIL on a compliant draft, which is the
    worse direction for a gate to be wrong in.

    Only the first line can be one: a short unpunctuated line in the MIDDLE of a paragraph is
    a wrapped clause, not a title.
    """
    lines = [ln for ln in paragraph.splitlines() if ln.strip()]
    if not lines or not _is_heading_line(lines[0]):
        return []
    # A one-line paragraph is a heading on its own; a multi-line one needs the rest to be
    # prose, or the whole thing is a list and its first item is not a title.
    if len(lines) > 1 and all(_is_heading_line(ln) for ln in lines[1:]):
        return []
    return [lines[0]]


def measure(text: str, keyword: str | None = None) -> dict[str, int]:
    """Every number the scorecard reports. Pure; the CLI only formats it."""
    paragraphs = _paragraphs(text)
    headings = [h for p in paragraphs for h in _heading_lines(p)]
    # Body text is every line that is not a heading — including the prose a heading is glued
    # to, which is why this subtracts lines rather than dropping whole paragraphs.
    body: list[str] = []
    for paragraph in paragraphs:
        rest = [ln for ln in paragraph.splitlines() if ln.strip() and ln not in headings]
        if rest:
            body.append("\n".join(rest))
    body_lengths = [_visible_chars(p) for p in body] or [0]
    return {
        "body_chars": _visible_chars(text),
        "headings": len(headings),
        "paragraphs": len(paragraphs),
        "para_chars": sum(body_lengths) // len(body_lengths),
        "images": len(re.findall(r"\[캡처\s*[::]", text)),
        "tables": sum(1 for line in text.splitlines() if " | " in line) and 1 or 0,
        "sources": len(re.findall(r"https?://|\[S\d+\]", text)),
        "keyword_hits": text.count(keyword) if keyword else -1,
        "hashtags": len(re.findall(r"#\w+", text)),
    }


def scorecard(measured: dict[str, int]) -> tuple[list[str], bool]:
    """``(lines, ok)`` — ok is False when any critical criterion is outside its range."""
    lines: list[str] = []
    ok = True
    for key, standard in STANDARDS.items():
        value = measured[key]
        bound = standard.rendered_bound()
        note = f"  ({standard.note})" if standard.note else ""
        star = " *" if standard.critical else "  "
        if value < 0:  # not measurable (e.g. keyword_hits with no --keyword)
            lines.append(f"{star} {standard.label:<22} {'—':>6}   [{bound}]  SKIPPED{note}")
            continue
        within = standard.within(value)
        mark = "OK   " if within else ("MISS " if standard.critical else "under" if value < standard.low else "over ")
        if not within and standard.critical:
            ok = False
        lines.append(f"{star} {standard.label:<22} {value:>6}   [{bound}]  {mark}{note}")
    return lines, ok

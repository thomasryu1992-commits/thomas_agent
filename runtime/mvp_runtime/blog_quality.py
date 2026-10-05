"""The package's quality, in layers a reviewer can read — never one number. Pure.

`blog_draft_score` measures structure against the lane's standards, and that stays the gate: it
decides `ready_for_review` / `needs_edit`, exactly as before. What it cannot say is whether the
draft is ABOUT what was searched, whether it rests on evidence, or whether it fits the platform it
is for. Those were left to the reviewer's eye; they are now recorded next to the structure, each
in its own layer:

- **structural** — the standards and the contract (the gating failures, unchanged);
- **semantic** — deterministic proxies for what a reader would judge: the title and intro carry
  the keyword, each section stays on its heading, nothing is said twice, the brief's questions
  are answered, the declared intent matches the keyword's. A proxy names itself and its method.
  Three judgements have no honest deterministic proxy — information gain, practical usefulness,
  audience fit — and are recorded ``not_measured`` rather than guessed;
- **evidence** — how much of the draft rests on what the run actually retrieved;
- **platform_fit** — the platform's own checks (`blog_naver.platform_checks`,
  `blog_tistory.platform_checks`).

**No LLM judge runs here, deliberately (2026-10-05).** The draft already rides a free model chain
whose revision is blocked often enough to have its own outcome code; a second model call per
package would add a failure surface and a number nobody could reproduce. Semantic, evidence and
platform-fit layers are advisory: they never change the review state and never publish anything.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from . import blog_draft, blog_draft_score, blog_overlap

__all__ = ["MIN_WEB_SOURCES", "SEMANTIC_METHOD", "layers"]

SEMANTIC_METHOD = "deterministic_proxy"
# Thin evidence: over 28 scored drafts on 2026-09-30, every one citing fewer than two web sources
# scored 58 or under; two or more ranged 45~72. A warning, never a gate.
MIN_WEB_SOURCES = 2
SECTION_FOCUS_MIN_PERCENT = 60
JUDGE_ONLY = ("information_gain", "practical_usefulness", "audience_fit")
_JUDGE_NOTE = "needs a reader's judgement; no LLM judge runs in this lane"
_STOP = frozenset({"방법", "정리", "이유", "기준", "차이", "주의", "주의점", "하기", "먼저", "실제", "예시"})


def _signal(state: str, value: int | None, note: str) -> dict[str, Any]:
    return {"state": state, "value": value, "note": note[:200]}


def _content_tokens(text: str, drop: set[str]) -> set[str]:
    tokens: set[str] = set()
    for token in blog_overlap.content_words(text):
        token = re.sub(r"[^\w]", "", token)
        if len(token) >= 2 and token not in drop and token not in _STOP:
            tokens.add(token)
    return tokens


def _section_focus(sections: Sequence[Mapping[str, Any]], target: str) -> dict[str, Any]:
    """Of the sections whose heading has a word of its own (not the keyword's), the share whose
    paragraphs use one of those words. A heading the body never mentions is a section that
    drifted to another subject — the '캡컷 사용법' draft that cut under '설치' (2026-09-30)."""
    drop = _content_tokens(target, set())
    judged = focused = 0
    for section in sections:
        words = _content_tokens(str(section.get("heading") or ""), drop)
        if not words:
            continue
        judged += 1
        body = re.sub(r"\s", "", " ".join(section.get("paragraphs") or [])).casefold()
        if any(word in body for word in words):
            focused += 1
    if not judged:
        return _signal("not_measured", None, "no heading carries a word of its own")
    value = 100 * focused // judged
    return _signal("ok" if value >= SECTION_FOCUS_MIN_PERCENT else "warn", value,
                   f"{focused}/{judged} sections use their heading's own words")


def _title_alignment(titles: Sequence[str], intro: Sequence[str], target: str) -> dict[str, Any]:
    with_keyword = sum(1 for t in titles if blog_draft_score.keyword_hits(t, target) > 0)
    in_intro = bool(intro) and blog_draft_score.keyword_hits(str(intro[0]), target) > 0
    value = 100 * with_keyword // len(titles) if titles else 0
    ok = titles and with_keyword * 3 >= len(titles) * 2 and in_intro
    return _signal("ok" if ok else "warn", value,
                   f"{with_keyword}/{len(titles)} titles carry the keyword; first intro paragraph "
                   + ("does" if in_intro else "does not"))


def _topic_coverage(brief: Mapping[str, Any] | None, headings: Sequence[str],
                    faq: Sequence[Mapping[str, Any]], target: str) -> dict[str, Any]:
    questions = list((brief or {}).get("user_questions") or [])
    if not questions:
        return _signal("not_measured", None, "no brief questions to cover")
    drop = _content_tokens(target, set())
    covered_text = re.sub(r"\s", "", " ".join(list(headings) + [str(q.get("question")) for q in faq])).casefold()
    answered = sum(1 for q in questions
                   if any(w in covered_text for w in _content_tokens(q, drop)))
    value = 100 * answered // len(questions)
    return _signal("ok" if value >= 60 else "warn", value,
                   f"{answered}/{len(questions)} brief questions surface in a heading or FAQ question")


def _intent_match(brief: Mapping[str, Any] | None, target: str) -> dict[str, Any]:
    lexicon = blog_overlap.keyword_intent(target)
    declared = (brief or {}).get("search_intent")
    if not declared:
        return _signal("not_measured", None, f"no declared intent (keyword reads as {lexicon})")
    if lexicon == "informational" or declared == lexicon:
        return _signal("ok", None, f"declared {declared}, keyword reads as {lexicon}")
    return _signal("warn", None, f"declared {declared}, but the keyword reads as {lexicon}")


def _evidence(parts: Mapping[str, Any], target_evidence: Mapping[str, Any]) -> dict[str, Any]:
    sources = parts.get("sources") or []
    web = sum(1 for s in sources if str(s.get("source_ref") or "").startswith("[S"))
    rows = sum(1 for s in sources if str(s.get("source_ref") or "").startswith("[K"))
    checks = parts.get("fact_checks") or []
    cited = sum(1 for c in checks if c.get("verification_state") == blog_draft.VERIFICATION_SOURCE_CITED)
    measured = target_evidence.get("status") == "measured"
    if not measured or target_evidence.get("degraded"):
        state = "degraded"
    elif web < MIN_WEB_SOURCES:
        state = "thin"
    else:
        state = "ok"
    record = {"state": state, "web_sources": web, "keyword_rows_cited": rows,
              "fact_checks": len(checks), "fact_checks_source_cited": cited,
              "target_evidence": "measured" if measured else "missing"}
    if target_evidence.get("degraded_reason_code"):
        record["degraded_reason_code"] = str(target_evidence["degraded_reason_code"])[:120]
    return record


def layers(
    parts: Mapping[str, Any], *, target: str, target_evidence: Mapping[str, Any],
    platform_checks: Sequence[Mapping[str, Any]], overlap: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The four layers for one interpreted draft (`blog_naver.interpret`/`blog_tistory.interpret`).
    ``overlap`` is the package's overlap record; a decision that needs review is a platform-fit
    warning, so it is seen where the other platform questions are."""
    structured = parts.get("structured") or {}
    sections = structured.get("sections") or []
    intro = structured.get("intro") or []
    brief = parts.get("content_brief")
    faq = (parts.get("platform_metadata") or {}).get("faq") or []
    failures = list(parts.get("failures") or [])
    prose = list(intro) + [p for s in sections for p in s.get("paragraphs") or []]
    repeats = int((parts.get("measured") or {}).get("repeated_sentences") or 0)
    echoes = len(blog_draft.echo_sentences(prose))
    semantic_signals: dict[str, Any] = {
        "search_intent_match": _intent_match(brief, target),
        "title_body_alignment": _title_alignment(parts.get("title_candidates") or [], intro, target),
        "section_focus": (_section_focus(sections, target) if sections
                          else _signal("not_measured", None, "not a structured draft")),
        "redundancy": _signal("ok" if not (repeats or echoes) else "warn", repeats + echoes,
                              f"{repeats} repeated, {echoes} echoing sentences"),
        "topic_coverage": _topic_coverage(brief, [s.get("heading", "") for s in sections], faq, target),
    }
    for name in JUDGE_ONLY:
        semantic_signals[name] = _signal("not_measured", None, _JUDGE_NOTE)
    checks = [dict(c) for c in platform_checks]
    if overlap and overlap.get("decision", {}).get("action") not in (None, blog_overlap.ALLOW):
        decision = overlap["decision"]
        checks.append({"check": "cross_platform_overlap",
                       "state": "fail" if decision["action"] == blog_overlap.OPERATOR_OVERRIDE else "warn",
                       "detail": f"{decision['overlap_type']} → {decision['action']}: {decision['reason']}"[:300]})
    fit_states = {c["state"] for c in checks}
    return {
        "structural": {"state": "pass" if not failures else "fail", "failures": failures},
        "semantic": {"method": SEMANTIC_METHOD, "signals": semantic_signals},
        "evidence": _evidence(parts, target_evidence),
        "platform_fit": {"state": "fail" if "fail" in fit_states else "warn" if "warn" in fit_states else "ok",
                         "checks": [{**c, "detail": str(c.get("detail") or "")[:300]} for c in checks]},
    }

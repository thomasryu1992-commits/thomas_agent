"""The Naver platform profile: SmartEditor plain text, the lane's measured standards, and the
request that was tuned against them draft by draft.

Everything here was `blog_content` until 2026-10-05, when the content engine was split from its
platforms so Tistory could have its own (`blog_tistory`). Nothing was re-tuned in the move: the
request and the revision request are the same bytes they were, pinned by digest against
:data:`PROMPT_VERSION` in `tests/test_mvp_runtime_blog_platform.py`. The comments are the
history of why each sentence exists — read them before changing one, and bump the version when
you do.

What makes this Naver's and not the engine's:

- the paste is SmartEditor ONE plain text, so the draft's markup is lifted out of it
  (`blog_draft.render_blocks`) and the legacy prose parser below reads `[캡처: …]` and `#tag`
  markers — the editor's own conventions;
- the standards are `blog_draft_score.STANDARDS` (Thomas 2026-08-10: 1,800~3,500 characters,
  4~7 headings, 70~150 a paragraph, the keyword 3~6 times);
- the request's length plan, keyword placement and capture directions are tuned to those.

The common rules (invent nothing, vendor names, the Korean reader, section focus) are
`blog_prompt`'s and are shared with every platform.

Pure: no I/O, no clock.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Mapping, Sequence

from . import blog_draft, blog_draft_score, blog_prompt

PLATFORM = "naver"
# Bump when the request or the revision request changes a byte; the digest test names the
# version a prompt belongs to, so two packages' drafts can be compared knowing whether they were
# asked the same thing.
PROMPT_VERSION = "naver_prompt.2026-10-05"
# What the profile is beyond its prompt: paste format, standards, renderers.
PROFILE_VERSION = "naver_profile.2026-10-05"
PASTE_FILE = "PASTE.txt"


def interpret(
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
            "body_blocks": rendered["body_blocks"][:blog_draft.MAX_BODY_BLOCKS],
            "tags": structured["tags"][:blog_draft.MAX_TAGS],
            "image_shots": rendered["image_shots"],
            "sources": sources,
            "fact_checks": blog_draft.fact_checks(structured["fact_checks"], prose, index),
            "structured": structured,
            "brackets_inserted": brackets_inserted,
        }
    else:
        parsed = blog_draft.parse_prose(text)
        paragraphs = [p for p in parsed["body_paste"].split("\n\n") if p.strip()]
        heading_indexes = {b["paragraph_index"] for b in parsed["body_blocks"]}
        prose = [p for i, p in enumerate(paragraphs) if i not in heading_indexes]
        measured = blog_draft_score.measure(text, target_keyword)
        parts = {
            "draft_format": blog_draft.DRAFT_FORMAT_LEGACY,
            "parse_reason": parse_reason,
            "title_candidates": blog_draft.prose_title_candidates(text),
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
    return f"{ask} {blog_prompt.ADD_SUBSTANCE_ASK}"



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
    **blog_prompt.COMMON_FAILURE_ASKS,
}


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



def _keyword_ask(target: str) -> str:
    """The body's keyword use, in the scorer's own numbers.

    Until 2026-09-29 the request asked for the keyword in the titles only, and a two-word
    keyword was shortened in the body ('소상공인 스마트상점' once, '스마트상점' six times)."""
    standard = blog_draft_score.STANDARDS["keyword_hits"]
    return (f"소제목과 문단을 합쳐 '{target}'를 {standard.low}~{standard.high}회 쓰고 그중 1회는 "
            f"intro 첫 문단에 넣는다. 키워드 일부만 떼어 줄여 쓴 것은 세지 않는다. {blog_prompt.KEYWORD_FORM_ASK}")


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
           f"키워드 일부만 떼어 줄여 쓴 것은 세지 않는다. {blog_prompt.KEYWORD_FORM_ASK}")
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


# The request's Naver parts, each a sentence of the one prompt (joined by `blog_prompt.compose`).
KEY_ORDER_RULE = "키는 위 순서대로 써라 — 짧은 항목을 먼저 모두 쓰고 intro와 sections를 맨 끝에 써라."
PLACEHOLDER_RULE = (f"형식의 '문단 1({PLAN_SENTENCES_PER_PARAGRAPH}문장)' 같은 자리표시는 글에 옮기지 "
                    "말고, 문단마다 그 수만큼 실제 문장을 채워라.")
LAYOUT_RULE = (
    "image_shots 4~8개(after_section은 0부터 센 섹션 번호, 생성 이미지가 아니라 실제 화면 캡처), 표 1개는 "
    "table 필드에만(첫 행이 머리글이고 데이터 행은 3개 이상, 머리글 칸에는 '항목1'·'값' 같은 자리표시 말고 "
    "비교하는 대상의 실제 이름을 써라, paragraphs 안에 ' | ' 행을 쓰지 마라 — 표는 문단 수에 세지 않는다), "
    "tags 3~8개, sources 2~5개."
)
REVISION_KEEP_LAYOUT_RULE = "image_shots 4~8개와 table은 첫 초안의 것을 그대로 유지하라(없으면 새로 채워라)."


def _keyword_placement_rule(target: str) -> str:
    """Where the shape marked the keyword (:func:`_draft_shape`), said once in prose."""
    return (f"'{target}' 1회라고 표시된 문단(도입 첫 문단, 그리고 섹션 5개 중 3개의 둘째 문단)에는 그 "
            "키워드를 한 번 자연스럽게 넣어라 — 키워드로 문장이나 섹션을 시작하지 말고 문장 중간에 넣어라.")


def _title_rule(target: str) -> str:
    return (f"규칙: title_candidates는 소제목과 별개인 글 제목 3~5개이고 각각 '{target}'를 앞쪽에 "
            "자연스럽게 포함한다.")


def content_request(target: str) -> str:
    """The blog request: the structured contract, the length plan, the standards, and the
    no-invention rule."""
    return blog_prompt.compose(
        [f"'{target}' 키워드로 네이버 블로그 글 초안을 작성해라.",
         blog_prompt.output_contract(_draft_shape(target))],
        [KEY_ORDER_RULE, PLACEHOLDER_RULE, _keyword_placement_rule(target)],
        [_length_plan()],
        [_title_rule(target), _keyword_ask(target), LAYOUT_RULE, blog_prompt.FACT_CHECK_RULE,
         blog_prompt.NO_INVENTION_RULE, blog_prompt.PLAIN_PARAGRAPH_RULE,
         blog_prompt.EVIDENCE_SPECIFICS_ASK, blog_prompt.SECTION_FOCUS_ASK],
    )


def revision_request(
    target: str, first: Mapping[str, Any], text: str, records: Mapping[str, Any] | None = None,
) -> str:
    """The one revision's request: only the failed items, the facts frozen, the draft attached.

    A length failure also carries the plan against the draft's own measurement: "longer" is what
    the first request already said, and the first package showed that saying it was not enough.

    ``records`` is the CONTENT run's record set. Its evidence goes along as unnumbered notes
    (:func:`blog_prompt.evidence_notes`): the revision has no evidence blocks of its own, and told
    to grow a paragraph with nothing new to say, it grew it with closers."""
    measured = first.get("measured") or {}
    asks = [f"- {_FAILURE_ASKS.get(f, f)} (현재 {measured.get(f, '-')})" for f in first["failures"]
            if f != "keyword_hits"]
    if _LENGTH_FAILURES & set(first["failures"]):
        asks.append(f"- {_length_asks(measured, first.get('structured'))}")
    if "keyword_hits" in first["failures"]:
        asks.append(f"- {_keyword_revision_ask(target, measured, first.get('structured'))}")
    if first.get("repeated"):
        asks.append(f"- {blog_prompt.repeated_sentences_ask(first['repeated'])}")
    grows = _grows(first)
    return blog_prompt.compose(
        [f"아래 '{target}' 네이버 블로그 초안을 고쳐라.", "고칠 항목은 다음뿐이다:"],
        *([[ask] for ask in asks] or [[]]),
        [blog_prompt.revision_facts_rule(grows), blog_prompt.REVISION_SECTION_SCOPE_RULE,
         REVISION_KEEP_LAYOUT_RULE, blog_prompt.REVISION_NO_EVIDENCE_RULE,
         blog_prompt.revision_output_contract(_DRAFT_SHAPE)],
        *blog_prompt.revision_tail(first, text, records, grows=grows, key_order=DRAFT_KEY_ORDER),
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


def carry_layout(first: Mapping[str, Any], carried: dict[str, Any], measured: dict[str, Any]) -> None:
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
                    "body_blocks": rendered["body_blocks"][:blog_draft.MAX_BODY_BLOCKS],
                    "image_shots": rendered["image_shots"]})
    measured["images"] = len(rendered["image_shots"])
    measured["tables"] = 1 if patched.get("table") is not None else measured.get("tables", 0)


# What SmartEditor ONE would show as literal characters (proposal §4b).
_MARKUP_LINE_RE = re.compile(r"^\s{0,3}(#{1,6}\s|>|\*\*|[-*]\s)", re.M)


def platform_checks(parts: Mapping[str, Any], target: str) -> list[dict[str, Any]]:
    """The Naver fit of a draft. Advisory: what gates (length, headings, titles) is already in
    `failures` through the standards."""
    body = str(parts.get("body_paste") or "")
    markup = len(_MARKUP_LINE_RE.findall(body)) + body.count("**")
    tags = len(parts.get("tags") or [])
    shots = len(parts.get("image_shots") or [])
    return [
        {"check": "plain_text_paste", "state": "ok" if not markup else "fail",
         "detail": f"{markup} markdown control sequences in the paste"},
        {"check": "tags", "state": "ok" if blog_draft_score.STANDARDS["hashtags"].within(tags) else "warn",
         "detail": f"{tags} tags (Naver caps a post at 30)"},
        {"check": "capture_directions", "state": "ok" if blog_draft_score.STANDARDS["images"].within(shots) else "warn",
         "detail": f"{shots} real-screen capture directions"},
    ]

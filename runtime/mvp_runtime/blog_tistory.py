"""The Tistory platform profile: a post found through Google (and Daum), written in markdown.

Not the Naver request with the name changed. The two platforms are read differently, and the vault
has measured how (`naver-to-google-seo` skill, 2026-09-07..10-04):

- **The reader arrives from a search box and wants the answer.** The first paragraph answers the
  query — no greeting, no empathy opener — and the post is found again by its structure: H2/H3
  headings that are themselves searchable phrases, a table that stays a table, an FAQ section.
- **There is no meta-description field.** Tistory writes the body's first ~400 characters into
  `<meta name="description">` and og:description (12 of 12 published posts, 2026-09-18). So the
  intro, before any heading or table, is at least 400 characters of plain prose — a post whose
  first table started at 248 characters went out with its cells glued into the search snippet.
  :func:`interpret` records that snippet as ``meta_description`` and says where it came from,
  rather than asking the model for a description the platform would never show.
- **The paste is markdown.** Tistory's editor takes it, so the runtime writes the headings, the
  table, the internal links and the reference list around the draft's plain paragraphs
  (:func:`render_markdown`) — the model writes no markup, exactly as on Naver.
- **Length is depth, not a keyword count.** 3,500 characters is Thomas's editorial floor
  (2026-09-14), not an SEO rule; a short draft is asked for an unanswered question, never for
  padding. No keyword-repetition standard exists here: Google is not read by density.
- **The title starts with the main keyword** (within its first 15 characters), stays within 45
  (2026-10-03), carries a subtitle only after ': ', and holds no non-ASCII symbol — a dash went
  into the RSS as a literal ``&amp;mdash;`` on every post /12~/59 (2026-09-27).
- **A content brief comes first** — intent, audience, angle, the questions a searcher asks next,
  what only this post has. It is the first key of the draft's JSON, so a draft that stops early
  has still planned, and the questions are what the length rule and the FAQ draw on.

Thomas's other editorial rules (rotation of forms, bold key sentences, ``refresh:`` dates) live in
the vault workflow and are not repeated here: this produces a draft for review, not a post.

Pure: no I/O, no clock.
"""

from __future__ import annotations

import json
import re
from typing import Any, Mapping, Sequence

from . import blog_draft, blog_draft_score, blog_overlap, blog_prompt, naver_research
from .blog_draft_score import Standard

PLATFORM = "tistory"
# .2 (2026-10-05): the length plan. The first live fire ('퍼플렉시티 요금제',
# bcp_390f9954b2beefe109ed) was asked for "3,500~5,000자" as a total and wrote 956 — two sentences
# a paragraph, the model stopping by itself at 2,607 output tokens of 12,000. Naver learned the
# same in September (`blog_naver._length_plan`): a total does not move a draft, a shape does.
PROMPT_VERSION = "tistory_prompt.2026-10-05.2"
PROFILE_VERSION = "tistory_profile.2026-10-05"
STANDARDS_VERSION = "tistory_draft_standards.2026-10-05"
PASTE_FILE = "PASTE.md"

# --- the standards ---------------------------------------------------------------------
#
# `critical` gates `ready_for_review` and drives the one revision, as on Naver: the length
# (Thomas 2026-09-14), the H2 count, and the first-400 intro — the one rule with a measured
# incident behind it (/27's snippet). The rest are reported.
STANDARDS: dict[str, Standard] = {
    "body_chars": Standard("본문 글자수 (공백 제외, FAQ 답 포함)", 3500, 7000, critical=True,
                           note="editorial depth (Thomas 2026-09-14), not an SEO rule"),
    "h2_sections": Standard("H2 섹션", 4, 8, critical=True, note="the runtime's FAQ H2 not counted"),
    "intro_chars": Standard("첫 소제목 앞 도입 (공백 제외)", 400, None, critical=True,
                            note="Tistory's description is the body's first ~400 characters"),
    "images": Standard("이미지 지시", 6, None, note="Thomas 2026-10-03: six, thumbnail included"),
    "faq": Standard("FAQ 문항", 3, 5),
    "sources": Standard("출처", 2, None, note="resolved [S#]/[K#] only"),
    "internal_links": Standard("내부 링크", 2, None, note="-1 when no candidate list could be read"),
    "tables": Standard("표", 1, None),
}

# --- the length plan ----------------------------------------------------------------------
#
# A shape that adds up to the standards, stated in the draft's own JSON shape and in prose:
# 3 intro paragraphs (≥400 for the snippet at the plan's floor: 3 × 160 = 480) + 6 H2 sections ×
# 3 paragraphs + 4 FAQ answers. At 160~190 a paragraph the prose alone is 3,360~3,990 and the FAQ
# adds ~300~500, so the plan's floor clears the 3,500 standard and its ceiling stays far under
# 7,000. The arithmetic is checked against `STANDARDS` in the tests.
PLAN_INTRO_PARAGRAPHS = 3
PLAN_SECTIONS = 6
PLAN_PARAGRAPHS_PER_SECTION = 3
PLAN_FAQ = 4
PLAN_PARAGRAPH_CHARS = (160, 190)          # visible characters, whitespace excluded
PLAN_FAQ_ANSWER_CHARS = 80                 # the floor the plan counts an FAQ answer at
PLAN_SENTENCES_PER_PARAGRAPH = "4"
# One paragraph of the planned length, a LENGTH reference only (generic on purpose, and the
# request says not to reuse it): "175자" alone is a number; this is what it looks like.
LENGTH_EXAMPLE_PARAGRAPH = (
    "요금제 화면에 들어가면 무료와 유료 플랜이 나란히 정리되어 있습니다. 먼저 지금 쓰는 기능이 무료 범위 "
    "안에 있는지 확인하고, 한도에 자주 걸리는 기능이 무엇인지 메모장에 적어 두면 플랜끼리 비교하기가 훨씬 "
    "쉬워집니다. 결제 주기는 월간과 연간 가운데 고를 수 있는데, 몇 달만 써 볼 생각이라면 월간 결제가 부담이 "
    "적습니다. 해지 방법과 환불 조건도 결제하기 전에 같은 화면에서 미리 확인해 두는 편이 안전합니다."
)
MAX_NAMED_PARAGRAPHS = 12


def plan_paragraphs() -> int:
    return PLAN_INTRO_PARAGRAPHS + PLAN_SECTIONS * PLAN_PARAGRAPHS_PER_SECTION


def plan_target() -> int:
    low, high = PLAN_PARAGRAPH_CHARS
    return (low + high) // 2


def plan_body_chars() -> tuple[int, int]:
    """The plan's body total at its paragraph floor and ceiling, FAQ answers at their floor."""
    low, high = PLAN_PARAGRAPH_CHARS
    faq = PLAN_FAQ * PLAN_FAQ_ANSWER_CHARS
    return plan_paragraphs() * low + faq, plan_paragraphs() * high + faq


MIN_TITLES = blog_draft.MIN_TITLES
TITLE_MAX_CHARS = 45
TITLE_KEYWORD_WITHIN = 15
MAX_FAQ = 8
MAX_INTERNAL_LINKS = 6
MAX_LINK_CANDIDATES = 8
INTRO_SNIPPET_CHARS = 400
# Searches the title must not address by role: the post's reader is everyone who searches the
# tool, and the small-business angle is one section of the body (skill 1-A, 1-C).
TITLE_FORBIDDEN_WORDS = ("사장님",)
_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+){1,7}$")
# ASCII, Hangul and CJK ideographs are text; anything else in a title (—, ·, →, ×, “”, …) is a
# symbol Tistory may entity-encode into the feed and the structured-data headline.
_TITLE_SYMBOL_RE = re.compile(r"[^\x20-\x7eᄀ-ᇿ㄰-㆏가-힣一-鿿]")

DRAFT_KEY_ORDER = ("brief", "seo_title", "title_candidates", "slug", "excerpt", "tags",
                   "image_shots", "table", "internal_links", "sources", "fact_checks", "faq",
                   "intro", "sections")
_DRAFT_SHAPE = json.dumps({
    "brief": {"search_intent": "how_to", "audience": "이 검색어를 치는 사람 한 문장",
              "article_angle": "이 글의 관점 한 문장", "secondary_keywords": ["소제목이 받아줄 롱테일"],
              "user_questions": ["검색자가 이어서 묻는 질문"], "differentiators": ["이 글에만 있는 것"],
              "evidence_requirements": ["본문 주장에 필요한 근거"]},
    "seo_title": "메인 키워드로 시작하는 제목: 부제",
    "title_candidates": ["다른 제목 후보"],
    "slug": "english-words-slug",
    "excerpt": "글 목록·공유에 보일 요약 1~2문장",
    "tags": ["태그(# 없이)"],
    "image_shots": [{"after_section": 0, "what_to_capture": "캡처할 실제 화면",
                     "alt_text": "그 화면을 설명하는 한 문장", "tool_name": None}],
    "table": {"after_section": 1, "rows": [["구분", "항목1", "항목2"], ["행 이름", "값", "값"]]},
    "internal_links": [{"link_ref": "[L1]", "anchor_text": "링크 문구", "after_section": 1}],
    "sources": [{"source_ref": "[S1]", "title": None}],
    "fact_checks": [{"claim": "본문 문장", "why": "확인이 필요한 이유", "source_ref": None}],
    "faq": [{"question": "질문", "answer": "2~3문장 답"}],
    "intro": [f"도입 문단 1({PLAN_SENTENCES_PER_PARAGRAPH}문장 — 검색 질문의 답부터)"]
             + [f"도입 문단 {i}({PLAN_SENTENCES_PER_PARAGRAPH}문장)" for i in range(2, PLAN_INTRO_PARAGRAPHS + 1)],
    "sections": [{"heading": "H2 소제목", "level": 2,
                  "paragraphs": [f"문단 {i}({PLAN_SENTENCES_PER_PARAGRAPH}문장)"
                                 for i in range(1, PLAN_PARAGRAPHS_PER_SECTION + 1)]}],
}, ensure_ascii=False)

# --- the request ------------------------------------------------------------------------

KEY_ORDER_RULE = ("키는 위 순서대로 써라 — brief와 짧은 항목을 먼저 모두 쓰고 faq, intro, sections를 맨 "
                  "끝에 써라. 형식의 설명 문구는 글에 옮기지 말고 실제 내용으로 채워라.")
BRIEF_RULE = (
    "brief는 쓰기 전에 정하는 설계다: search_intent는 "
    + "·".join(blog_overlap.INTENTS) + " 중 하나, user_questions는 검색자가 이 검색어 다음에 실제로 "
    "묻는 질문 3~6개, secondary_keywords는 소제목이 받아줄 롱테일 2~4개(근거 블록 [K#]에 있는 키워드를 "
    "먼저), differentiators는 이 글에만 있는 것(비교표·판단 기준·절차 등) 1~3개, article_angle은 한 문장이다."
)
SLUG_RULE = "slug는 영문 소문자·숫자·하이픈으로 된 2~8단어다."
EXCERPT_RULE = "excerpt는 120자 이내 1~2문장으로 이 글이 무엇에 답하는지 쓴다."
STRUCTURE_RULE = (
    "sections는 level 2(H2) 섹션 4~8개로 짓고, 한 H2를 나눠야 할 때만 바로 뒤에 level 3(H3) 섹션을 둔다 — "
    "첫 섹션은 level 2다. 소제목은 설명이 아니라 검색할 수 있는 문구(예: '카페 소개글 예시')로 쓰고, "
    "secondary_keywords를 소제목에 자연스럽게 싣되 키워드를 억지로 반복하지 마라."
)
FAQ_RULE = (f"faq는 {PLAN_FAQ}문항(3~5)이다 — 질문은 brief.user_questions에서 고르고, 답은 2~3문장으로 본문에 근거가 "
            "있는 내용만 쓴다.")
LAYOUT_RULE = (
    "image_shots 6개 이상(생성 이미지가 아니라 실제 화면 캡처, alt_text는 화면을 설명하는 한국어 한 문장이고 "
    "키워드 나열 금지), 표 1개는 table 필드에만(첫 행이 머리글, 데이터 행 3개 이상, 머리글 칸에는 비교하는 "
    "대상의 실제 이름), tags 5~10개, sources 2~5개."
)
def length_plan() -> str:
    """The plan in prose, beside the shape that already carries it: counts, the paragraph's
    target and range, the total, the example, and a count-before-you-answer check both ways."""
    low, high = PLAN_PARAGRAPH_CHARS
    total_low, total_high = plan_body_chars()
    return (
        f"분량 계획(이대로 써라): intro 문단 {PLAN_INTRO_PARAGRAPHS}개 + level 2 섹션 {PLAN_SECTIONS}개 × "
        f"섹션마다 paragraphs {PLAN_PARAGRAPHS_PER_SECTION}개 = 문단 {plan_paragraphs()}개, 그리고 faq 답 "
        f"{PLAN_FAQ}개(각 2~3문장). 문단 하나는 {PLAN_SENTENCES_PER_PARAGRAPH}문장, 공백 빼고 "
        f"{plan_target()}자 안팎({low}~{high}자)이다. 합계 약 {total_low:,}~{total_high:,}자이고, 합계가 "
        f"{STANDARDS['body_chars'].low:,}자에 못 미치거나 intro 합이 {INTRO_SNIPPET_CHARS}자에 못 미치면 "
        "불합격이다. 한두 문장짜리 문단을 만들지 마라 — 각 문단은 방법·이유·예시·주의점 중 둘 이상을 담아 "
        "풀어 써라. 문단 하나의 길이는 이 정도다(길이만 참고하고 내용은 따라 쓰지 마라): "
        f"「{LENGTH_EXAMPLE_PARAGRAPH}」 그래도 쓸 말이 모자라면 같은 말을 되풀이하거나 키워드를 채우지 말고 "
        "brief.user_questions 중 아직 답하지 않은 질문을 섹션으로 더해라. JSON을 내기 전에 문단이 "
        f"{plan_paragraphs()}개인지, 각 문단이 {low}~{high}자인지 세어 보고, 모자란 문단은 더하고 넘치는 "
        f"문단은 덜어 {plan_target()}자 안팎으로 맞춰라."
    )


NO_FABRICATED_EXPERIENCE_RULE = ("글쓴이가 가게를 운영했다거나 무엇을 겪었다는 1인칭 경험, 손님 수·매출 "
                                 "같은 확인할 수 없는 수치를 지어내지 마라.")
NO_LINK_CANDIDATES_RULE = "내부 링크 후보가 없다 — internal_links는 빈 목록 []으로 둬라."
LINK_FACTS_RULE = "[L#]은 internal_links에만 쓰고 본문·facts·fact_checks·sources에는 쓰지 마라."


def _title_rule(target: str) -> str:
    return (f"seo_title은 '{target}'로 시작하고(앞 {TITLE_KEYWORD_WITHIN}자 안) 전체 {TITLE_MAX_CHARS}자 "
            "이내다. 부제는 콜론(': ') 뒤에만 두고 아직 제목에 없는 롱테일을 싣는다. 대시(—)·가운뎃점(·)·"
            "화살표 같은 비ASCII 기호와 '사장님'은 제목에 쓰지 마라. title_candidates는 seo_title과 다른 "
            "후보 2~4개이고 같은 규칙을 따른다.")


def _intro_rule(target: str) -> str:
    return (f"intro 첫 문단의 첫 두 문장으로 검색 질문에 바로 답하고 '{target}'를 넣어라 — 인사말·공감형 "
            f"도입·'아래에서 정리했습니다' 꼬리는 쓰지 마라. intro 문단의 공백 제외 합은 {INTRO_SNIPPET_CHARS}자 "
            "이상이다(티스토리는 메타 디스크립션 칸이 없어 본문 첫 400자가 검색 결과 요약이 된다).")


def _links_lines(context: blog_prompt.DraftContext) -> list[list[str]]:
    if not context.link_candidates:
        return [[NO_LINK_CANDIDATES_RULE]]
    listed = [f"{c.ref} {c.title}" for c in context.link_candidates[:MAX_LINK_CANDIDATES]]
    return [[f"internal_links는 아래 같은 블로그 글 중 이 글과 이어지는 2~3개를 [L#]로 골라 링크 문구"
             f"(anchor_text, 글 제목을 설명하는 말)와 놓을 섹션을 정한다 — 목록 밖의 링크·URL은 쓰지 마라. "
             f"{LINK_FACTS_RULE}"], ["내부 링크 후보:"], *[[line] for line in listed]]


def _repurpose_lines(context: blog_prompt.DraftContext) -> list[list[str]]:
    if not context.repurpose_from:
        return []
    named = ", ".join(f"「{p.get('title') or p.get('keyword')}」({p.get('platform')})"
                      for p in context.repurpose_from[:3])
    return [[f"같은 주제의 글이 다른 플랫폼에 이미 있다: {named}. 그 글의 문장·예시·표를 옮기지 말고 "
             "검색 의도와 구성을 새로 잡아 전부 새로 써라 — 같은 문장은 검색에서 둘 다 손해다."]]


def content_request(target: str, context: blog_prompt.DraftContext | None = None) -> str:
    """The Tistory request: the brief-first structured contract, the Google-facing rules, the
    common evidence policy, and the link candidates the runtime gathered."""
    context = context or blog_prompt.DraftContext()
    return blog_prompt.compose(
        [f"'{target}'를 메인 키워드로 티스토리 블로그 글 초안을 작성해라. 독자는 구글·다음 검색으로 "
         "들어와 검색어의 답을 찾는 사람이다.", blog_prompt.output_contract(_DRAFT_SHAPE)],
        [KEY_ORDER_RULE],
        [BRIEF_RULE],
        [_title_rule(target), SLUG_RULE, EXCERPT_RULE],
        [_intro_rule(target), STRUCTURE_RULE, FAQ_RULE, LAYOUT_RULE],
        [length_plan()],
        [blog_prompt.FACT_CHECK_RULE, blog_prompt.NO_INVENTION_RULE, blog_prompt.PLAIN_PARAGRAPH_RULE,
         NO_FABRICATED_EXPERIENCE_RULE, blog_prompt.EVIDENCE_SPECIFICS_ASK,
         blog_prompt.SECTION_FOCUS_ASK],
        *_links_lines(context),
        *_repurpose_lines(context),
    )


_FAILURE_ASKS = {
    "body_chars": ("intro·섹션 문단·faq 답의 글자수 합을 공백 제외 3,500~7,000자로 맞춰라 — 모자라면 문단을 "
                   "불리지 말고 brief.user_questions 중 아직 답하지 않은 질문을 H2 섹션으로 더하고, 넘치면 "
                   "주제에서 가장 먼 섹션을 덜어내라"),
    "h2_sections": "level 2 섹션을 4~8개로 맞춰라",
    "intro_chars": (f"intro 문단의 공백 제외 합을 {INTRO_SNIPPET_CHARS}자 이상으로 — 첫 문단에 검색 질문의 답을, "
                    "둘째 문단에 조건·예외를 써라"),
    "title_candidates": "seo_title과 다른 제목 후보를 title_candidates에 2~4개 넣어라",
    "seo_title": (f"seo_title을 메인 키워드로 시작해(앞 {TITLE_KEYWORD_WITHIN}자 안) {TITLE_MAX_CHARS}자 이내로, "
                  "비ASCII 기호와 '사장님' 없이 다시 써라"),
    "slug": "slug를 영문 소문자·숫자·하이픈으로 된 2~8단어로 써라",
    "heading_hierarchy": "첫 섹션은 level 2로, level 3 섹션은 level 2 섹션 뒤에만 둬라",
    **blog_prompt.COMMON_FAILURE_ASKS,
}
_GROW_FAILURES = frozenset({"body_chars", "intro_chars"})
REVISION_KEEP_LAYOUT_RULE = ("brief, image_shots(alt_text 포함), table, internal_links, faq는 첫 초안의 것을 "
                             "그대로 유지하라(없으면 새로 채워라).")


def _short_paragraphs(structured: Mapping[str, Any] | None) -> list[str]:
    """Each prose paragraph under the plan's floor, labelled with how much it needs: "섹션 2의
    문단 1(현재 74자, 약 100자 더)". The named list is what moved Naver's short drafts; "longer"
    alone did not."""
    if not structured:
        return []
    low = PLAN_PARAGRAPH_CHARS[0]
    out: list[str] = []

    def label(n: int) -> str:
        return f"현재 {n}자, 약 {max(10, (plan_target() - n + 5) // 10 * 10)}자 더"

    for i, paragraph in enumerate(structured.get("intro") or []):
        if _visible(paragraph) < low:
            out.append(f"도입 문단 {i}({label(_visible(paragraph))})")
    for s_index, section in enumerate(structured.get("sections") or []):
        for p_index, paragraph in enumerate(section.get("paragraphs") or []):
            if _visible(paragraph) < low:
                out.append(f"섹션 {s_index}의 문단 {p_index}({label(_visible(paragraph))})")
    return out


def _length_ask(first: Mapping[str, Any]) -> str:
    """The plan against the draft's own numbers, with the short paragraphs named."""
    measured = first.get("measured") or {}
    structured = first.get("structured") or {}
    paragraphs = len(structured.get("intro") or []) + sum(
        len(s.get("paragraphs") or []) for s in structured.get("sections") or [])
    ask = (f"현재 문단 {paragraphs}개·본문 {measured.get('body_chars', '-')}자·도입 "
           f"{measured.get('intro_chars', '-')}자. {length_plan()}")
    named = _short_paragraphs(structured)
    if named:
        more = f" 외 {len(named) - MAX_NAMED_PARAGRAPHS}개" if len(named) > MAX_NAMED_PARAGRAPHS else ""
        ask += (f" {PLAN_PARAGRAPH_CHARS[0]}자에 못 미치는 문단(번호는 0부터): "
                f"{', '.join(named[:MAX_NAMED_PARAGRAPHS])}{more}. 이 문단마다 적힌 만큼 늘려라.")
    return f"{ask} {blog_prompt.ADD_SUBSTANCE_ASK}"


def grows(first: Mapping[str, Any]) -> bool:
    """Whether the revision is asked to ADD text: the body or the intro under its floor."""
    measured = first.get("measured") or {}
    if "intro_chars" in (first.get("failures") or []):
        return True
    return ("body_chars" in (first.get("failures") or [])
            and int(measured.get("body_chars") or 0) < STANDARDS["body_chars"].low)


def revision_request(
    target: str, first: Mapping[str, Any], text: str, records: Mapping[str, Any] | None = None,
    context: blog_prompt.DraftContext | None = None,
) -> str:
    """The one revision: only what failed, the facts frozen, the first draft attached — and the
    link list again, because ``[L#]`` is the runtime's list, not evidence the revision lacks."""
    context = context or blog_prompt.DraftContext()
    measured = first.get("measured") or {}
    asks = [f"- {_FAILURE_ASKS.get(f, f)} (현재 {measured.get(f, '-')})" for f in first["failures"]]
    if _GROW_FAILURES & set(first["failures"]):
        asks.append(f"- {_length_ask(first)}")
    if first.get("repeated"):
        asks.append(f"- {blog_prompt.repeated_sentences_ask(first['repeated'])}")
    growing = grows(first)
    return blog_prompt.compose(
        [f"아래 '{target}' 티스토리 블로그 초안을 고쳐라.", "고칠 항목은 다음뿐이다:"],
        *([[ask] for ask in asks] or [[]]),
        [blog_prompt.revision_facts_rule(growing), blog_prompt.REVISION_SECTION_SCOPE_RULE,
         REVISION_KEEP_LAYOUT_RULE, blog_prompt.REVISION_NO_EVIDENCE_RULE,
         blog_prompt.revision_output_contract(_DRAFT_SHAPE, last="faq, intro, sections")],
        *_links_lines(context),
        *blog_prompt.revision_tail(first, text, records, grows=growing, key_order=DRAFT_KEY_ORDER),
    )


# --- reading the draft -------------------------------------------------------------------

def _text(value: Any, limit: int) -> str:
    return blog_draft.sanitize_paragraph(str(value).strip()[:limit]) if isinstance(value, str) else ""


def _texts(values: Any, *, limit: int, cap: int) -> list[str]:
    out: list[str] = []
    for value in values if isinstance(values, list) else []:
        text = _text(value, limit)
        if text and text not in out:
            out.append(text)
    return out[:cap]


def slug(value: Any) -> str | None:
    """An ASCII kebab slug, or None. Lower-cased and space-joined; never transliterated —
    Korean in, None out, and the package says the slug is missing."""
    text = re.sub(r"[\s_]+", "-", str(value or "").strip().lower())
    text = re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9-]", "", text)).strip("-")
    return text if _SLUG_RE.match(text) else None


def _brief(value: Any, target: str, records: Mapping[str, Any] | None) -> dict[str, Any]:
    """The draft's brief, coerced: a declared intent outside the lexicon is dropped, and each
    secondary keyword says whether THIS run's keyword brief measured it."""
    data = value if isinstance(value, Mapping) else {}
    measured = {naver_research.normalize_keyword(r.get("keyword"))
                for r in ((records or {}).get("keyword_research") or {}).get("metrics") or []
                if isinstance(r, Mapping) and not str(r.get("source") or "").startswith("mock")}
    intent = str(data.get("search_intent") or "").strip()
    return {
        "primary_keyword": target,
        "secondary_keywords": [{"keyword": k, "measured": naver_research.normalize_keyword(k) in measured}
                               for k in _texts(data.get("secondary_keywords"), limit=50, cap=6)],
        "search_intent": intent if intent in blog_overlap.INTENTS else None,
        "audience": _text(data.get("audience"), 200) or None,
        "article_angle": _text(data.get("article_angle"), 200) or None,
        "user_questions": _texts(data.get("user_questions"), limit=200, cap=8),
        "differentiators": _texts(data.get("differentiators"), limit=200, cap=5),
        "evidence_requirements": _texts(data.get("evidence_requirements"), limit=200, cap=6),
    }


def _faq(value: Any) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, Mapping):
            continue
        question, answer = _text(item.get("question"), 200), _text(item.get("answer"), 1000)
        if question and answer and all(q["question"] != question for q in out):
            out.append({"question": question, "answer": answer})
    return out[:MAX_FAQ]


def _links(value: Any, context: blog_prompt.DraftContext, sections: int) -> list[dict[str, Any]]:
    """The draft's internal links that name a candidate of THIS request; the rest are dropped."""
    by_ref = {c.ref: c for c in context.link_candidates}
    out: list[dict[str, Any]] = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, Mapping):
            continue
        ref = "[" + str(item.get("link_ref") or "").strip().strip("[]") + "]"
        candidate = by_ref.get(ref)
        if candidate is None or any(link["link_ref"] == ref for link in out):
            continue
        after = item.get("after_section")
        after = after if isinstance(after, int) and not isinstance(after, bool) else sections - 1
        out.append({"link_ref": ref, "title": candidate.title[:200], "url": candidate.url[:500],
                    "anchor_text": _text(item.get("anchor_text"), 100) or candidate.title[:100],
                    "after_section": min(max(after, 0), max(sections - 1, 0))})
    return out[:MAX_INTERNAL_LINKS]


def title_problems(title: str, target: str) -> list[str]:
    """What a title breaks of the Tistory title rule, by name; empty when it holds."""
    problems: list[str] = []
    key = re.sub(r"\s", "", target or "")
    pattern = r"\s*".join(re.escape(ch) for ch in key)
    match = re.search(pattern, title, flags=re.IGNORECASE) if key else None
    if match is None:
        problems.append("keyword_missing")
    elif len(re.sub(r"\s", "", title[:match.start()])) >= TITLE_KEYWORD_WITHIN:
        problems.append("keyword_late")
    if len(title) > TITLE_MAX_CHARS:
        problems.append("too_long")
    if _TITLE_SYMBOL_RE.search(title):
        problems.append("non_ascii_symbol")
    if any(word in title for word in TITLE_FORBIDDEN_WORDS):
        problems.append("addresses_owner")
    return problems


def _markdown_table(table: Mapping[str, Any]) -> str:
    rows = table["rows"]
    width = max(len(r) for r in rows)
    padded = [list(r) + [""] * (width - len(r)) for r in rows]
    lines = ["| " + " | ".join(padded[0]) + " |", "|" + " --- |" * width]
    lines += ["| " + " | ".join(r) + " |" for r in padded[1:]]
    return "\n".join(lines)


FAQ_HEADING = "자주 묻는 질문"
SOURCES_HEADING = "참고한 자료"


def render_markdown(
    structured: Mapping[str, Any], *, faq: Sequence[Mapping[str, str]] = (),
    links: Sequence[Mapping[str, Any]] = (), sources: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """The draft as Tistory markdown, deterministically: the intro, each section under its
    ``##``/``###`` heading with its table and internal links after it, the FAQ as ``##`` with a
    ``###`` per question, and the resolved web sources as a linked list. The draft's own
    paragraphs carry no markup; every symbol here is the runtime's."""
    blocks: list[str] = list(structured.get("intro") or [])
    headings: list[dict[str, Any]] = []
    section_end: list[int] = []
    table = structured.get("table")

    def heading(level: int, text: str) -> None:
        headings.append({"paragraph_index": len(blocks), "action": "heading"})
        blocks.append(f"{'#' * level} {text}")

    for index, section in enumerate(structured.get("sections") or []):
        heading(int(section.get("level") or 2), section["heading"])
        blocks.extend(section["paragraphs"])
        if table and table["after_section"] == index:
            blocks.append(_markdown_table(table))
        for link in links:
            if link["after_section"] == index:
                blocks.append(f"함께 보면 좋은 글: [{link['anchor_text']}]({link['url']})")
        section_end.append(len(blocks) - 1)
    if faq:
        heading(2, FAQ_HEADING)
        for item in faq:
            heading(3, item["question"])
            blocks.append(item["answer"])
    linked = [s for s in sources if s.get("url")]
    if linked:
        heading(2, SOURCES_HEADING)
        blocks.append("\n".join(f"- [{s.get('title') or s['url']}]({s['url']})" for s in linked))
    intro_end = max(len(structured.get("intro") or []) - 1, 0)
    shots = []
    for shot in structured.get("image_shots") or []:
        index = shot["after_section"]
        rendered = {"after_paragraph": section_end[index] if 0 <= index < len(section_end) else intro_end,
                    "what_to_capture": shot["what_to_capture"]}
        for key in ("tool_name", "alt_text"):
            if shot.get(key):
                rendered[key] = shot[key]
        shots.append(rendered)
    return {"body_paste": "\n\n".join(blocks), "body_blocks": headings[:blog_draft.MAX_BODY_BLOCKS],
            "image_shots": shots[:blog_draft.MAX_IMAGE_SHOTS], "paragraph_count": len(blocks)}


def _visible(text: str) -> int:
    return len(re.sub(r"\s", "", str(text or "")))


def measure(structured: Mapping[str, Any], *, faq: Sequence[Mapping[str, str]], images: int,
            images_with_alt: int, sources: int, links: int, link_source_state: str,
            keyword: str) -> dict[str, int]:
    """The Tistory standards on the draft's own fields. Exact, no heuristic: the intro is the
    prose before the first heading, the H2s are the level-2 sections."""
    sections = structured.get("sections") or []
    intro = structured.get("intro") or []
    paragraphs = list(intro) + [p for s in sections for p in s["paragraphs"]]
    text = "\n".join([s["heading"] for s in sections] + paragraphs)
    return {
        "body_chars": sum(_visible(p) for p in paragraphs) + sum(_visible(q["answer"]) for q in faq),
        "h2_sections": sum(1 for s in sections if int(s.get("level") or 2) == 2),
        "h3_sections": sum(1 for s in sections if int(s.get("level") or 2) == 3),
        "intro_chars": sum(_visible(p) for p in intro),
        "images": int(images),
        "images_with_alt": int(images_with_alt),
        "faq": len(faq),
        "sources": int(sources),
        "internal_links": int(links) if link_source_state == "measured" else -1,
        "tables": 1 if structured.get("table") is not None else 0,
        "keyword_hits": blog_draft_score.keyword_hits(text, keyword),
    }


def interpret(
    text: str, target_keyword: str, records: Mapping[str, Any] | None = None,
    context: blog_prompt.DraftContext | None = None,
) -> dict[str, Any]:
    """One model answer as a Tistory package's parts, its measurement and its failures. Pure.

    A draft that is not the structured JSON falls back to the shared prose parser
    (`blog_draft.parse_prose`) so the operator still gets a body to edit — recorded as
    ``legacy_markdown`` and failing ``structured_output``, which is what the revision fixes."""
    context = context or blog_prompt.DraftContext()
    index = blog_draft.evidence_index(records)
    structured, parse_reason = blog_draft.parse_structured(text, extended=True)
    if structured is None:
        return _interpret_prose(text, target_keyword, index, parse_reason, context)
    data, _inserted, _reason = blog_draft.load_object(text)
    data = data or {}
    brackets_inserted = int(structured.pop("brackets_inserted", 0) or 0)
    sources = blog_draft.resolve_sources(structured["sources"], index)
    faq = _faq(data.get("faq"))
    links = _links(data.get("internal_links"), context, len(structured["sections"]))
    rendered = render_markdown(structured, faq=faq, links=links, sources=sources)
    seo_title = _text(data.get("seo_title"), 100) or (structured["title_candidates"][:1] or [""])[0]
    titles = [t for t in dict.fromkeys([seo_title, *structured["title_candidates"]]) if t]
    prose = list(structured["intro"]) + [p for s in structured["sections"] for p in s["paragraphs"]]
    shots = structured["image_shots"]
    measured = measure(structured, faq=faq, images=len(shots),
                       images_with_alt=sum(1 for s in shots if s.get("alt_text")),
                       sources=len(sources), links=len(links),
                       link_source_state=context.link_source_state, keyword=target_keyword)
    slug_value = slug(data.get("slug"))
    intro_text = " ".join(structured["intro"])
    metadata = {
        "paste_format": "markdown",
        "seo_title": seo_title or target_keyword,
        "seo_title_problems": title_problems(seo_title, target_keyword) if seo_title else ["missing"],
        "slug": slug_value,
        "excerpt": _text(data.get("excerpt"), 200) or None,
        "meta_description": intro_text[:INTRO_SNIPPET_CHARS] or target_keyword,
        "meta_description_source": "body_first_400",
        "headings": [{"level": int(s.get("level") or 2), "text": s["heading"]} for s in structured["sections"]],
        "faq": faq,
        "internal_links": links,
        "internal_link_candidates": len(context.link_candidates),
        "internal_link_source_state": context.link_source_state,
    }
    failures = list(blog_draft_score.critical_failures(measured, STANDARDS))
    if len(titles) < MIN_TITLES:
        failures.append("title_candidates")
    if metadata["seo_title_problems"]:
        failures.append("seo_title")
    if slug_value is None:
        failures.append("slug")
    if structured["sections"] and int(structured["sections"][0].get("level") or 2) != 2:
        failures.append("heading_hierarchy")
    repeated = blog_draft.repeated_sentences(prose + [q["answer"] for q in faq])
    measured["repeated_sentences"] = blog_draft.repeat_count(prose + [q["answer"] for q in faq])
    if repeated:
        failures.append("repeated_sentences")
    return {
        "draft_format": blog_draft.DRAFT_FORMAT_STRUCTURED,
        "title_candidates": titles[:blog_draft.MAX_TITLES],
        "body_paste": rendered["body_paste"],
        "body_blocks": rendered["body_blocks"],
        "tags": structured["tags"][:blog_draft.MAX_TAGS],
        "image_shots": rendered["image_shots"],
        "sources": sources,
        "fact_checks": blog_draft.fact_checks(structured["fact_checks"], prose + [q["answer"] for q in faq], index),
        "structured": {**structured, "brief": data.get("brief"), "seo_title": data.get("seo_title"),
                       "slug": data.get("slug"), "excerpt": data.get("excerpt"),
                       "internal_links": data.get("internal_links"), "faq": faq},
        "brackets_inserted": brackets_inserted,
        "content_brief": _brief(data.get("brief"), target_keyword, records),
        "platform_metadata": metadata,
        "measured": measured,
        "failures": failures,
        "repeated": repeated,
    }


def _interpret_prose(text: str, target: str, index: Mapping[str, Any], parse_reason: str | None,
                     context: blog_prompt.DraftContext) -> dict[str, Any]:
    parsed = blog_draft.parse_prose(text)
    paragraphs = [p for p in parsed["body_paste"].split("\n\n") if p.strip()]
    heading_at = {b["paragraph_index"] for b in parsed["body_blocks"]}
    first_heading = min(heading_at, default=len(paragraphs))
    prose = [p for i, p in enumerate(paragraphs) if i not in heading_at]
    body = [f"## {p}" if i in heading_at else p for i, p in enumerate(paragraphs)]
    measured = {
        "body_chars": sum(_visible(p) for p in prose),
        "h2_sections": len(heading_at), "h3_sections": 0,
        "intro_chars": sum(_visible(p) for p in paragraphs[:first_heading]),
        "images": len(parsed["image_shots"]), "images_with_alt": 0, "faq": 0, "sources": 0,
        "internal_links": 0 if context.link_source_state == "measured" else -1,
        "tables": 1 if any(" | " in p for p in prose) else 0,
        "keyword_hits": blog_draft_score.keyword_hits(text, target),
    }
    titles = blog_draft.prose_title_candidates(text)
    failures = list(blog_draft_score.critical_failures(measured, STANDARDS))
    failures += ["title_candidates"] if len(titles) < MIN_TITLES else []
    failures += ["structured_output", "seo_title", "slug"]
    repeated = blog_draft.repeated_sentences(prose)
    measured["repeated_sentences"] = blog_draft.repeat_count(prose)
    if repeated:
        failures.append("repeated_sentences")
    return {
        "draft_format": blog_draft.DRAFT_FORMAT_LEGACY, "parse_reason": parse_reason,
        "title_candidates": titles, "body_paste": "\n\n".join(body),
        "body_blocks": parsed["body_blocks"], "tags": parsed["tags"],
        "image_shots": parsed["image_shots"], "sources": [],
        "fact_checks": blog_draft.fact_checks([], prose, index), "structured": None,
        "content_brief": _brief(None, target, None),
        "platform_metadata": {
            "paste_format": "markdown", "seo_title": (titles or [target])[0],
            "seo_title_problems": ["missing"], "slug": None, "excerpt": None,
            "meta_description": " ".join(paragraphs[:first_heading])[:INTRO_SNIPPET_CHARS] or target,
            "meta_description_source": "body_first_400", "headings": [], "faq": [],
            "internal_links": [], "internal_link_candidates": len(context.link_candidates),
            "internal_link_source_state": context.link_source_state,
        },
        "measured": measured, "failures": failures, "repeated": repeated,
    }


def carry_layout(first: Mapping[str, Any], carried: dict[str, Any], measured: dict[str, Any]) -> None:
    """What a revision dropped and the first draft had — capture directions, the table, the
    internal links, the FAQ — put back, and the markdown rendered again with the carried
    sources."""
    old = (first.get("structured") if first.get("draft_format") == blog_draft.DRAFT_FORMAT_STRUCTURED
           else None) or {}
    new = carried.get("structured")
    if not new:
        return
    patched = dict(new)
    last = max(len(new.get("sections") or []) - 1, 0)
    if not new.get("image_shots") and old.get("image_shots"):
        patched["image_shots"] = [dict(s, after_section=min(s["after_section"], last)) for s in old["image_shots"]]
    if new.get("table") is None and old.get("table") is not None:
        patched["table"] = dict(old["table"], after_section=min(old["table"]["after_section"], last))
    metadata = dict(carried.get("platform_metadata") or {})
    first_meta = first.get("platform_metadata") or {}
    if not metadata.get("faq") and first_meta.get("faq"):
        metadata["faq"] = list(first_meta["faq"])
    if not metadata.get("internal_links") and first_meta.get("internal_links"):
        metadata["internal_links"] = [dict(link, after_section=min(link["after_section"], last))
                                      for link in first_meta["internal_links"]]
    # Always rendered again, even with nothing patched: the reference list at the end is drawn
    # from the sources, and those are the first draft's now (`blog_content._carry_first_evidence`).
    rendered = render_markdown(patched, faq=metadata.get("faq") or [],
                               links=metadata.get("internal_links") or [],
                               sources=carried.get("sources") or [])
    carried.update({"structured": patched, "body_paste": rendered["body_paste"],
                    "body_blocks": rendered["body_blocks"], "image_shots": rendered["image_shots"],
                    "platform_metadata": metadata})
    shots = patched.get("image_shots") or []
    measured.update({"images": len(shots), "images_with_alt": sum(1 for s in shots if s.get("alt_text")),
                     "tables": 1 if patched.get("table") is not None else 0,
                     "faq": len(metadata.get("faq") or []),
                     "body_chars": sum(_visible(p) for p in list(patched.get("intro") or [])
                                       + [p for s in patched.get("sections") or [] for p in s["paragraphs"]])
                     + sum(_visible(q["answer"]) for q in metadata.get("faq") or [])})
    if measured.get("internal_links", -1) >= 0:
        measured["internal_links"] = len(metadata.get("internal_links") or [])


def platform_checks(parts: Mapping[str, Any], target: str) -> list[dict[str, Any]]:
    """The Tistory fit of a draft, check by check — the ones that gate are also in `failures`."""
    meta = parts.get("platform_metadata") or {}
    measured = parts.get("measured") or {}
    problems = meta.get("seo_title_problems") or []
    intro = (meta.get("meta_description") or "")
    first_sentence_window = intro[:160]
    checks = [
        {"check": "seo_title", "state": "fail" if problems else "ok",
         "detail": ", ".join(problems) or f"'{meta.get('seo_title')}'"},
        {"check": "slug", "state": "ok" if meta.get("slug") else "fail", "detail": meta.get("slug") or "missing or not ASCII kebab"},
        {"check": "intro_snippet", "state": "ok" if measured.get("intro_chars", 0) >= INTRO_SNIPPET_CHARS else "fail",
         "detail": f"{measured.get('intro_chars', 0)} chars before the first heading"},
        {"check": "keyword_in_snippet", "state": "ok" if blog_draft_score.keyword_hits(first_sentence_window, target) > 0 else "warn",
         "detail": "the keyword within the snippet's first 160 characters"},
        {"check": "heading_hierarchy", "state": "fail" if "heading_hierarchy" in (parts.get("failures") or []) else "ok",
         "detail": f"{measured.get('h2_sections', 0)} H2, {measured.get('h3_sections', 0)} H3"},
        {"check": "image_alt_text", "state": "ok" if measured.get("images", 0) and measured.get("images_with_alt") == measured.get("images") else "warn",
         "detail": f"{measured.get('images_with_alt', 0)}/{measured.get('images', 0)} capture directions carry alt text"},
        {"check": "internal_links", "state": ("not_measured" if measured.get("internal_links", -1) < 0
                                              else "ok" if measured["internal_links"] >= STANDARDS["internal_links"].low else "warn"),
         "detail": f"{max(measured.get('internal_links', -1), 0)} of {meta.get('internal_link_candidates', 0)} candidates linked "
                   f"(source {meta.get('internal_link_source_state')})"},
        {"check": "faq", "state": "ok" if STANDARDS["faq"].within(measured.get("faq", 0)) else "warn",
         "detail": f"{measured.get('faq', 0)} questions"},
        {"check": "excerpt", "state": "ok" if meta.get("excerpt") else "warn", "detail": "list/share summary"},
    ]
    return checks

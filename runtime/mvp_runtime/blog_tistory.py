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

import difflib
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
# .3 (2026-10-05): read against its own output ('노션 AI 요금제', bcp_c4be3ea7381cd1fa1faf, scored
# 6.1/10). The length example was about 요금제·해지·환불 — the lane's own topic — and the draft
# copied it as an intro paragraph (similarity 0.95); the first sentences answered nothing; every
# section closed on "…지름길입니다"; FAQ answers repeated the body; the price came from a
# third-party blog because the shared Korean-reader rule forbids a foreign service's own price;
# and ~900 characters of print-shop naming rules rode along on an AI-tool post. Each is answered
# below, and the example's reuse and the closers are now measured, not only asked about.
# .4 (2026-10-05): the register. '캡컷 유료' (bcp_710bdd04cd3676404b5b) came back with 90 of 92
# sentences in 해라체 ("…언급된다.", "…수치다.") — the blog writes 존댓말, the request never said so,
# and the revision's model mirrored the request's own imperative register. Said in the request
# and in the revision request, and counted (:func:`plain_sentences`).
# .5 (2026-10-05): the vault's own Tistory editorial system, ported where the runtime can keep it.
# One fixed skeleton every week (6 H2 × 3, FAQ always 4) is the "도장" pattern the vault removed in
# September (`naver-to-google-seo` 1-B: FAQ exactly 5 was itself a banned stamp). The post's form
# (판정형·계산형·절차형·통설검증형), its intro hook and its FAQ count now rotate against the vault's
# last Tistory posts (:func:`editorial_plan`); the key sentence of each H2 is bold; a cited
# sentence links its source in place; and the paragraph count rose to 24 — four live drafts wrote
# ~145 characters a paragraph against a 160 floor and three ended under 3,500.
PROMPT_VERSION = "tistory_prompt.2026-10-05.5"
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
# The body under the form's H2s (and their H3s): 21 paragraphs, 3~5 a section. PLAN_SECTIONS is the
# H2 count a form's skeleton names (4) — the shape still shows one section of three as format.
PLAN_SECTIONS = 4
PLAN_SECTION_PARAGRAPHS = 21
PLAN_PARAGRAPHS_PER_SECTION = 3
PLAN_FAQ = 4
FAQ_COUNTS = (3, 4, 5)
PLAN_PARAGRAPH_CHARS = (160, 190)          # visible characters, whitespace excluded
PLAN_FAQ_ANSWER_CHARS = 80                 # the floor the plan counts an FAQ answer at
PLAN_SENTENCES_PER_PARAGRAPH = "4"
# One paragraph of the planned length, a LENGTH reference only: "175자" alone is a number; this is
# what it looks like. Its subject is deliberately far from anything this lane writes about — the
# .2 example was about 요금제 and the first draft under it pasted it in (see PROMPT_VERSION).
LENGTH_EXAMPLE_PARAGRAPH = (
    "베란다에서 허브를 키울 때는 먼저 하루에 햇빛이 드는 시간을 재어 봅니다. 바질과 로즈마리는 햇빛을 "
    "좋아해서 남향 창가에 두면 잎이 두껍게 자라고, 그늘에 두면 줄기만 길어지기 쉽습니다. 물은 흙 겉면이 "
    "마른 것을 손가락으로 직접 확인한 뒤에 주는 편이 뿌리가 썩지 않아 안전합니다. 잎을 딸 때는 맨 위쪽 "
    "순을 두 마디씩 잘라 주면 옆으로 새순이 여러 개 돋아서 한 포기에서 거두는 양이 눈에 띄게 늘어납니다."
)
# A draft paragraph this close to the example is the example, reworded at most.
EXAMPLE_REUSE_RATIO = 0.6
MAX_NAMED_PARAGRAPHS = 12


def plan_paragraphs() -> int:
    return PLAN_INTRO_PARAGRAPHS + PLAN_SECTION_PARAGRAPHS


def plan_target() -> int:
    low, high = PLAN_PARAGRAPH_CHARS
    return (low + high) // 2


def plan_body_chars() -> tuple[int, int]:
    """The plan's body total at its paragraph floor and ceiling, FAQ answers at their floor."""
    low, high = PLAN_PARAGRAPH_CHARS
    faq = min(FAQ_COUNTS) * PLAN_FAQ_ANSWER_CHARS
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
                                 for i in range(1, PLAN_PARAGRAPHS_PER_SECTION + 1)],
                  "key_sentence": "이 섹션 paragraphs 안의 결론 문장 하나 그대로"}],
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
def _faq_rule(count: int) -> str:
    return (f"faq는 {count}문항이다 — 질문은 brief.user_questions에서 고르고, 답은 2~3문장으로 본문에 근거가 "
            "있는 내용만 쓰되 본문 문장을 되풀이하지 말고 그 질문에만 바로 답하는 새 문장으로 쓴다.")
LAYOUT_RULE = (
    "image_shots 6개 이상(생성 이미지가 아니라 실제 화면 캡처, alt_text는 화면을 설명하는 한국어 한 문장이고 "
    "키워드 나열 금지), 표 1개는 table 필드에만(첫 행이 머리글, 데이터 행 3개 이상, 머리글 칸에는 비교하는 "
    "대상의 실제 이름), tags 5~10개, sources 2~5개."
)
def length_plan(faq_count: int = PLAN_FAQ) -> str:
    """The plan in prose, beside the shape that already carries it: counts, the paragraph's
    target and range, the total, the example, and a count-before-you-answer check both ways."""
    low, high = PLAN_PARAGRAPH_CHARS
    total_low, total_high = plan_body_chars()
    return (
        f"분량 계획(이대로 써라): intro 문단 {PLAN_INTRO_PARAGRAPHS}개 + 섹션 문단 합 {PLAN_SECTION_PARAGRAPHS}개"
        f"(아래 뼈대의 H2와 그 아래 H3에 나눠, 섹션마다 3~5개) = 문단 {plan_paragraphs()}개, 그리고 faq 답 "
        f"{faq_count}개(각 2~3문장). 문단 하나는 {PLAN_SENTENCES_PER_PARAGRAPH}문장, 공백 빼고 "
        f"{plan_target()}자 안팎({low}~{high}자)이다. 합계 약 {total_low:,}~{total_high:,}자이고, 합계가 "
        f"{STANDARDS['body_chars'].low:,}자에 못 미치거나 intro 합이 {INTRO_SNIPPET_CHARS}자에 못 미치면 "
        "불합격이다. 한두 문장짜리 문단을 만들지 마라 — 각 문단은 방법·이유·예시·주의점 중 둘 이상을 담아 "
        "풀어 써라. 문단 하나의 길이는 이 정도다(길이만 참고하고 내용은 따라 쓰지 마라): "
        f"「{LENGTH_EXAMPLE_PARAGRAPH}」 그래도 쓸 말이 모자라면 같은 말을 되풀이하거나 키워드를 채우지 말고 "
        "brief.user_questions 중 아직 답하지 않은 질문을 섹션으로 더해라. JSON을 내기 전에 문단이 "
        f"{plan_paragraphs()}개인지, 각 문단이 {low}~{high}자인지 세어 보고, 모자란 문단은 더하고 넘치는 "
        f"문단은 덜어 {plan_target()}자 안팎으로 맞춰라."
    )


# --- the editorial plan: form, intro hook and FAQ count rotate (vault `naver-to-google-seo` 1-B) ---
#
# The runtime takes four of the vault's six forms. 실측형 needs inputs actually run and 사례해부형 a
# real case; this lane has neither, and the vault forbids the form without them.
FORMS: dict[str, dict[str, str]] = {
    "판정형": {
        "asset": "판정트리",
        "skeleton": ("H2 ① 판정 기준 표(조건 | 결과 | 확인할 곳 — table 필드) ② 경우별 판정(H3 세 개: 조건 → "
                     "판정 → 다음에 할 일) ③ 해당되면 할 일과 기한 ④ 판정이 갈리는 경계 사례"),
    },
    "계산형": {
        "asset": "계산예제",
        "skeleton": ("H2 ① 항목과 계산식(표 — table 필드) ② 예제 세 가지(H3마다 입력값 → 단계 계산 → 결과, 근거에 "
                     "있는 수치로만) ③ 예제마다 빠지기 쉬운 함정 ④ 내 경우를 계산하는 순서"),
    },
    "절차형": {
        "asset": "화면절차",
        "skeleton": ("H2 ① 준비물과 조건(표 — table 필드) ② 단계별 진행(H3 단계마다: 화면 이름 → 누르는 것 → "
                     "막히는 지점) ③ 막혔을 때 확인할 것 ④ 끝난 뒤 확인하는 법"),
    },
    "통설검증형": {
        "asset": "원문인용",
        "skeleton": ("H2 ① 통설별 판정(H3 통설마다: 통설 → 맞다·틀리다·조건부 → 근거 → 할 일) ② 정답표(통설 | "
                     "판정 | 근거 — table 필드) ③ 통설이 퍼진 이유 ④ 지금 할 일"),
    },
}
INTRO_HOOKS: dict[str, str] = {
    "결론먼저": "intro 첫 문장에 결론(판정·답)을 쓴다",
    "숫자먼저": "intro 첫 문장을 근거에 있는 숫자(가격·한도·기한·용량)로 시작한다",
    "조건분기": "intro 첫 문장을 '…라면 A, 아니면 B'처럼 조건으로 갈라 쓴다",
    "부정먼저": "intro 첫 문장을 흔한 오해를 바로잡는 문장('…가 아닙니다')으로 시작한다",
    "질문답": "intro 첫 문장은 검색자의 질문, 둘째 문장은 그 답이다",
}
_FORM_AFFINITY = {
    "procedure": ("절차형", "판정형", "통설검증형", "계산형"),
    "troubleshooting": ("절차형", "판정형", "통설검증형", "계산형"),
    "cancellation": ("절차형", "판정형", "통설검증형", "계산형"),
    "how_to": ("절차형", "판정형", "통설검증형", "계산형"),
    "pricing": ("판정형", "계산형", "통설검증형", "절차형"),
    "free_tier": ("판정형", "통설검증형", "계산형", "절차형"),
    "comparison": ("판정형", "계산형", "통설검증형", "절차형"),
}
_DEFAULT_AFFINITY = ("판정형", "통설검증형", "절차형", "계산형")
# Within the last five posts a form may appear at most twice, the new one included (vault 1-B).
RECENT_POSTS = 5
MAX_FORM_IN_RECENT = 2
# Searches whose answer is a number that moves: the post carries a date to re-check it by.
REFRESH_INTENTS = frozenset({"pricing", "free_tier", "cancellation", "comparison"})
REFRESH_DAYS = 30
# Tistory has two categories; the Tistory-only queue is AI tools, and a pricing/limit/error intent
# is one. Anything else is the operator's call (None).
CATEGORY_AI = "사장님 AI 활용법"
FIXED_TAGS = ("소상공인", "자영업")
MAX_TAGS = 10


def editorial_plan(target: str, posts: Sequence[Mapping[str, Any]], now: str | None = None) -> dict[str, Any]:
    """This post's form, intro hook and FAQ count, rotated against the vault's Tistory posts in
    number order — the vault's own rule: not the previous post's form, at most twice in the last
    five; the next intro hook after the previous post's; FAQ 3/4/5 by the post's number. Pure."""
    mine = sorted((p for p in posts if p.get("platform") == PLATFORM and not p.get("reserved")
                   and isinstance(p.get("number"), int)), key=lambda p: p["number"])
    recent = [str(p.get("form") or "") for p in mine[-RECENT_POSTS:]]
    previous = recent[-1] if recent else None
    intent = blog_overlap.keyword_intent(target)
    order = _FORM_AFFINITY.get(intent, _DEFAULT_AFFINITY)
    form = next((f for f in order if f != previous and recent.count(f) < MAX_FORM_IN_RECENT),
                next(f for f in order if f != previous))
    hooks = list(INTRO_HOOKS)
    last_hook = str(mine[-1].get("intro_hook") or "") if mine else ""
    hook = hooks[(hooks.index(last_hook) + 1) % len(hooks)] if last_hook in hooks else hooks[0]
    number = (mine[-1]["number"] + 1) if mine else 0
    plan: dict[str, Any] = {
        "form": form, "unique_asset": FORMS[form]["asset"], "intro_hook": hook,
        "faq_count": FAQ_COUNTS[number % len(FAQ_COUNTS)], "previous_form": previous or None,
        "category": CATEGORY_AI if intent in REFRESH_INTENTS | {"troubleshooting"} else None,
        "refresh_by": None,
    }
    if now and intent in REFRESH_INTENTS:
        import datetime as _dt
        day = _dt.date.fromisoformat(str(now)[:10]) + _dt.timedelta(days=REFRESH_DAYS)
        plan["refresh_by"] = day.isoformat()
    return plan


def default_plan(target: str) -> dict[str, Any]:
    """The plan without the vault (an override fire with no published posts): the keyword's own
    first form, the first hook, four FAQs."""
    return editorial_plan(target, ())


def _editorial_lines(plan: Mapping[str, Any]) -> list[list[str]]:
    form = FORMS[plan["form"]]
    return [[f"이 글의 유형은 {plan['form']}이다 — sections를 이 뼈대 순서로 짓는다(FAQ는 faq 필드가 맡으니 "
             f"섹션으로 쓰지 마라): {form['skeleton']}. 이 글에만 있는 자산으로 {plan['unique_asset']}을(를) "
             f"뼈대 안에 하나 만든다. {INTRO_HOOKS[plan['intro_hook']]}."]]


NO_FABRICATED_EXPERIENCE_RULE = ("글쓴이가 가게를 운영했다거나 무엇을 겪었다는 1인칭 경험, 손님 수·매출 "
                                 "같은 확인할 수 없는 수치를 지어내지 마라.")
# The evidence rule, Tistory's: the shared one (`blog_prompt.EVIDENCE_SPECIFICS_ASK`) carries
# Naver's print-shop naming block and a Korean-reader rule that forbids a foreign service's own
# price — on an AI-tool pricing post that sent the draft to a third-party blog's won price.
EVIDENCE_RULE = (
    "근거 블록([S#])에 나온 구체적인 내용 — 플랜 이름, 가격, 한도, 메뉴·버튼·기능 이름, 절차 단계 — 을 "
    "섹션마다 최소 1개 본문에 그 이름 그대로 쓰고, 그 근거를 sources에 넣어라. 근거에 없는 이름·수치는 "
    "지어내지 마라. 근거 글의 문장은 옮기지 말고 네 말로 풀어 써라."
)
OFFICIAL_SOURCE_RULE = (
    "가격·무료 한도·해지·환불 같은 조건은 그 도구의 공식 요금·도움말 페이지 근거를 먼저 쓰고, 개인 블로그·"
    "뉴스의 수치는 공식 근거가 없을 때만 쓰되 그 문장을 fact_checks에 넣어라. 공식 가격이 달러 같은 외화로만 "
    "나와 있으면 그 금액 그대로 쓰고 '(해외 기준)'을 붙여라 — 원화로 바꾸거나 다른 곳의 원화 가격으로 대신하지 "
    "마라. 근거에 기준 날짜가 있으면 '2026년 10월 기준'처럼 함께 밝혀라."
)
TOOL_NAME_RULE = ("도구 이름(키워드가 다루는 도구와 근거에 나온 도구)은 그대로 밝히고, '특정 앱'·'온라인 서비스'처럼 "
                  "흐리게 부르지 마라. 이 요청에 적힌 지시(분량·키워드·출처 규칙)를 본문 문장으로 옮겨 쓰지 마라.")
KEY_SENTENCE_RULE = ("섹션마다 key_sentence에 그 섹션의 결론·판정·숫자를 담은 문장 하나를 그 섹션 paragraphs 안의 "
                     "문장 그대로 옮겨 적어라 — 글에서 그 문장이 굵게 표시된다. 키워드 단어만 적거나 문장을 바꾸지 마라.")
INLINE_CITATION_RULE = ("근거 블록의 수치·조건을 쓴 문장은 문장 끝에 그 근거 번호([S#])를 붙여라 — 글에서는 그 자리에 "
                        "출처 링크가 걸린다. 근거가 없는 문장에는 번호를 붙이지 마라.")
REGISTER_RULE = ("본문 문단·faq 답·excerpt는 모두 존댓말(…합니다·…습니다·…세요)로 써라 — '…이다'·'…한다'·"
                 "'…된다' 같은 해라체로 문장을 끝내지 마라. 이 요청이 해라체로 쓰여 있어도 글은 존댓말이다. 제목·"
                 "소제목·표는 명사형으로 끝내도 된다.")
CLOSER_RULE = ("문단을 '…이 중요합니다'·'…지름길입니다'·'…도움이 됩니다'·'…바람직합니다'처럼 어느 글에나 붙는 "
               "맺음 문장으로 끝내지 마라 — 마지막 문장도 그 문단에만 있는 정보(수치·조건·예시·다음에 할 일)다.")
NO_LINK_CANDIDATES_RULE = "내부 링크 후보가 없다 — internal_links는 빈 목록 []으로 둬라."
LINK_FACTS_RULE = "[L#]은 internal_links에만 쓰고 본문·facts·fact_checks·sources에는 쓰지 마라."


def _title_rule(target: str) -> str:
    return (f"seo_title은 '{target}'로 시작하고(앞 {TITLE_KEYWORD_WITHIN}자 안) 전체 {TITLE_MAX_CHARS}자 "
            "이내다. 부제는 콜론(': ') 뒤에만 두고 아직 제목에 없는 롱테일을 싣는다. 대시(—)·가운뎃점(·)·"
            "화살표 같은 비ASCII 기호와 '사장님'은 제목에 쓰지 마라. title_candidates는 seo_title과 다른 "
            "후보 2~4개이고 같은 규칙을 따른다.")


def _intro_rule(target: str) -> str:
    return (f"intro 첫 두 문장 안에 근거 블록에 나온 구체적 사실 하나(가격·한도·플랜 이름·조건 중 하나)를 넣고, "
            f"첫 두 문장으로 검색 질문에 바로 답하며 '{target}'를 넣어라 — '…이 중요합니다' 같은 일반론, 인사말·공감형 "
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
    plan = context.editorial or default_plan(target)
    return blog_prompt.compose(
        [f"'{target}'를 메인 키워드로 티스토리 블로그 글 초안을 작성해라. 독자는 구글·다음 검색으로 "
         "들어와 검색어의 답을 찾는 사람이다.", blog_prompt.output_contract(_DRAFT_SHAPE)],
        [KEY_ORDER_RULE],
        [BRIEF_RULE],
        [REGISTER_RULE],
        [_title_rule(target), SLUG_RULE, EXCERPT_RULE],
        [_intro_rule(target), STRUCTURE_RULE, _faq_rule(plan["faq_count"]), LAYOUT_RULE],
        *_editorial_lines(plan),
        [KEY_SENTENCE_RULE, INLINE_CITATION_RULE],
        [length_plan(plan["faq_count"])],
        [blog_prompt.FACT_CHECK_RULE, blog_prompt.NO_INVENTION_RULE, OFFICIAL_SOURCE_RULE,
         blog_prompt.PLAIN_PARAGRAPH_RULE, NO_FABRICATED_EXPERIENCE_RULE, EVIDENCE_RULE, TOOL_NAME_RULE,
         CLOSER_RULE, blog_prompt.SECTION_FOCUS_ASK],
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
    "plain_register": ("해라체('…이다'·'…한다'·'…된다')로 끝나는 문장을 모두 존댓말(…합니다·…습니다)로 바꿔라 — "
                       "내용·수치·문장 순서는 그대로 두고 어미만 바꿔라"),
    "length_example_copied": ("요청의 길이 예시 문단을 본문에 옮겼다 — 그 문단을 지우고 그 자리에 이 글 주제의 "
                              "새 내용(근거에 나온 수치·조건·절차)을 같은 길이로 써라"),
    **blog_prompt.COMMON_FAILURE_ASKS,
}
_GROW_FAILURES = frozenset({"body_chars", "intro_chars"})
REVISION_KEEP_LAYOUT_RULE = ("brief, image_shots(alt_text 포함), table, internal_links, faq, 섹션의 key_sentence는 첫 초안의 것을 "
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
           f"{measured.get('intro_chars', '-')}자. {length_plan(len((first.get('platform_metadata') or {}).get('faq') or []) or PLAN_FAQ)}")
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
         REVISION_KEEP_LAYOUT_RULE, REGISTER_RULE, blog_prompt.REVISION_NO_EVIDENCE_RULE,
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


def with_fixed_tags(tags: Sequence[str]) -> list[str]:
    """The draft's tags with the blog's two fixed tags appended, at most :data:`MAX_TAGS` — the
    vault's tag rule (variants + neighbours + the series' fixed pair, 10 or fewer)."""
    own = [t for t in tags if t not in FIXED_TAGS][:MAX_TAGS - len(FIXED_TAGS)]
    return own + list(FIXED_TAGS)


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


_INLINE_REF_RE = re.compile(r"\s*\[\s*[SK]\d{1,3}(?:\s*,\s*[SK]?\d{1,3})*\s*\]")
# "…가능합니다. [S3]" — a marker after the full stop belongs to the sentence before it.
_TRAILING_REF_RE = re.compile(r"([.!?。])(\s*\[\s*[SK]\d{1,3}(?:\s*,\s*[SK]?\d{1,3})*\s*\])")


def refs_inside(text: str) -> str:
    """Citation markers moved inside the sentence they follow: "…다. [S3]" -> "…다 [S3]."."""
    return _TRAILING_REF_RE.sub(lambda m: f"{m.group(2)}{m.group(1)}", str(text or ""))


def _cite(text: str, by_ref: Mapping[str, Mapping[str, Any]]) -> str:
    """``[S#]`` markers in a paragraph as links to the source they name, in place; a marker that
    resolves to nothing with a URL (a keyword row, a source this run never had) is removed."""
    def link(match: re.Match[str]) -> str:
        for key in blog_draft.ref_keys(match.group(0).strip()):
            source = by_ref.get(f"[{key}]")
            if source and source.get("url"):
                return f" ([{source.get('title') or '출처'}]({source['url']}))"
        return ""
    return _INLINE_REF_RE.sub(link, refs_inside(text))


def _bold(paragraphs: list[str], key: str | None) -> tuple[list[str], bool]:
    """The section's key sentence in bold where it stands — once, and only if it is really there."""
    if not key:
        return paragraphs, False
    out = list(paragraphs)
    for i, paragraph in enumerate(out):
        if key in paragraph:
            out[i] = paragraph.replace(key, f"**{key}**", 1)
            return out, True
    return out, False


def render_markdown(
    structured: Mapping[str, Any], *, faq: Sequence[Mapping[str, str]] = (),
    links: Sequence[Mapping[str, Any]] = (), sources: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """The draft as Tistory markdown, deterministically: the intro, each section under its
    ``##``/``###`` heading with its key sentence in bold, its table and internal links after it,
    the FAQ as ``##`` with a ``###`` per question, and the resolved web sources as a linked list;
    a cited sentence links its source in place. The draft's own paragraphs carry no markup;
    every symbol here is the runtime's."""
    by_ref = {str(s.get("source_ref")): s for s in sources}
    blocks: list[str] = [_cite(p, by_ref) for p in structured.get("intro") or []]
    headings: list[dict[str, Any]] = []
    section_end: list[int] = []
    table = structured.get("table")

    def heading(level: int, text: str) -> None:
        headings.append({"paragraph_index": len(blocks), "action": "heading"})
        blocks.append(f"{'#' * level} {text}")

    bolded = 0
    for index, section in enumerate(structured.get("sections") or []):
        heading(int(section.get("level") or 2), section["heading"])
        paragraphs, done = _bold(list(section["paragraphs"]), section.get("key_sentence"))
        bolded += done
        blocks.extend(_cite(p, by_ref) for p in paragraphs)
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
            blocks.append(_cite(item["answer"], by_ref))
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
            "image_shots": shots[:blog_draft.MAX_IMAGE_SHOTS], "paragraph_count": len(blocks),
            "bold_sections": bolded}


def _key(text: str) -> str:
    return re.sub(r"\s", "", str(text or ""))


def example_reused(paragraphs: Sequence[str]) -> bool:
    """Whether any paragraph is the request's length example, at most reworded."""
    example = _key(LENGTH_EXAMPLE_PARAGRAPH)
    return any(difflib.SequenceMatcher(None, example, _key(p)).ratio() >= EXAMPLE_REUSE_RATIO
               for p in paragraphs if p)


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?。])\s+")
# A closing sentence true of any post: '…이 중요합니다', '…지름길입니다'. A digit keeps it in —
# "월 20회까지라 아껴 쓰는 편이 좋습니다" still says something.
_GENERIC_CLOSER_RE = re.compile(
    r"(중요합니다|필요합니다|지름길입니다|바람직합니다|도움이\s*됩니다|유용합니다|현명합니다|안전합니다|"
    r"좋습니다|만듭니다|길입니다)[.!]?$")
FAQ_ECHO_OVERLAP = 0.6


def _sentences(text: str) -> list[str]:
    return [x.strip() for x in _SENTENCE_SPLIT_RE.split(str(text or "")) if x.strip()]


# A sentence ending in '다' that is not '니다': '…언급된다.', '…수치다.', '…많다.'. Quoted speech and a
# table row are not the post's own voice and are skipped.
_PLAIN_END_RE = re.compile(r"(?<!니)다[.!]?$")
PLAIN_SENTENCES_ALLOWED = 2
PLAIN_SENTENCES_SHARE = 0.1


def plain_sentences(paragraphs: Sequence[str]) -> tuple[int, int]:
    """``(해라체 sentences, all sentences)`` over the post's prose."""
    plain = total = 0
    for paragraph in paragraphs:
        if " | " in paragraph:
            continue
        for sentence in _sentences(paragraph):
            if sentence.endswith(("\"", "'", "」", "”")):
                continue
            total += 1
            plain += bool(_PLAIN_END_RE.search(sentence))
    return plain, total


def plain_register(paragraphs: Sequence[str]) -> int | None:
    """The 해라체 count when it is more than the post can carry (a few quoted or listed lines are
    fine; :data:`PLAIN_SENTENCES_ALLOWED` or :data:`PLAIN_SENTENCES_SHARE`, whichever is larger),
    else None."""
    plain, total = plain_sentences(paragraphs)
    return plain if plain > max(PLAIN_SENTENCES_ALLOWED, int(total * PLAIN_SENTENCES_SHARE)) else None


def generic_closers(paragraphs: Sequence[str]) -> int:
    """How many prose paragraphs end on a sentence that would fit any post."""
    count = 0
    for paragraph in paragraphs:
        sentences = _sentences(paragraph)
        if sentences and " | " not in paragraph and not re.search(r"\d", sentences[-1]) \
                and _GENERIC_CLOSER_RE.search(sentences[-1]):
            count += 1
    return count


def _pairs(text: str) -> set[str]:
    return {text[i:i + 2] for i in range(len(text) - 1)}


def faq_echoes(faq: Sequence[Mapping[str, str]], paragraphs: Sequence[str]) -> int:
    """FAQ answer sentences whose character pairs mostly (:data:`FAQ_ECHO_OVERLAP`) repeat one body
    sentence — an answer that says the body again instead of answering the question."""
    body = [_pairs(_key(x)) for p in paragraphs for x in _sentences(p) if len(_key(x)) >= 15]
    count = 0
    for item in faq:
        for sentence in _sentences(item.get("answer") or ""):
            pairs = _pairs(_key(sentence))
            if len(pairs) >= 14 and any(len(pairs & b) / len(pairs) >= FAQ_ECHO_OVERLAP for b in body):
                count += 1
    return count


def _visible(text: str) -> int:
    """Visible characters of the prose, the citation markers left out (they become links)."""
    return len(re.sub(r"\s", "", blog_draft.strip_evidence_refs(str(text or ""))))


def _clean(paragraphs: Sequence[str]) -> list[str]:
    return [blog_draft.strip_evidence_refs(p).strip() for p in paragraphs]


def _inline_checks(paragraphs: Sequence[str]) -> list[dict[str, Any]]:
    """Each sentence that carries its own ``[S#]`` and states something that changes (a price, a
    limit, a date — `blog_draft.detect_fact_checks`): a check whose source the draft named."""
    checks: list[dict[str, Any]] = []
    for paragraph in paragraphs:
        for sentence in _sentences(refs_inside(paragraph)):
            refs = blog_draft.cited_refs(sentence)
            clean = blog_draft.strip_evidence_refs(sentence).strip()
            if refs and blog_draft.detect_fact_checks([clean]):
                found = blog_draft.detect_fact_checks([clean])[0]
                checks.append({"claim": found["claim"], "why": found["why"], "source_ref": refs[0]})
    return checks


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
    faq = _faq(data.get("faq"))
    raw_prose = list(structured["intro"]) + [p for s in structured["sections"] for p in s["paragraphs"]]
    raw_prose += [q["answer"] for q in faq]
    inline = [{"source_ref": ref} for p in raw_prose for ref in blog_draft.cited_refs(p)]
    # The listed sources, then the ones only cited inline — both resolved against THIS run.
    sources = blog_draft.resolve_sources(list(structured["sources"]) + inline, index)
    links = _links(data.get("internal_links"), context, len(structured["sections"]))
    rendered = render_markdown(structured, faq=faq, links=links, sources=sources)
    seo_title = _text(data.get("seo_title"), 100) or (structured["title_candidates"][:1] or [""])[0]
    titles = [t for t in dict.fromkeys([seo_title, *structured["title_candidates"]]) if t]
    prose = _clean(list(structured["intro"]) + [p for s in structured["sections"] for p in s["paragraphs"]])
    faq_clean = [dict(q, answer=blog_draft.strip_evidence_refs(q["answer"]).strip()) for q in faq]
    shots = structured["image_shots"]
    measured = measure(structured, faq=faq, images=len(shots),
                       images_with_alt=sum(1 for s in shots if s.get("alt_text")),
                       sources=len(sources), links=len(links),
                       link_source_state=context.link_source_state, keyword=target_keyword)
    measured["bold_sections"] = int(rendered["bold_sections"])
    slug_value = slug(data.get("slug"))
    intro_text = " ".join(_clean(structured["intro"]))
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
    if context.editorial:
        metadata["editorial"] = dict(context.editorial)
    failures = list(blog_draft_score.critical_failures(measured, STANDARDS))
    if len(titles) < MIN_TITLES:
        failures.append("title_candidates")
    if metadata["seo_title_problems"]:
        failures.append("seo_title")
    # No slug is a warning, not a failure (.5): this blog's addresses are numbers (`/N`), so the
    # slug only names the vault file — the vault's own rule (`naver-to-google-seo` 3단계).
    if structured["sections"] and int(structured["sections"][0].get("level") or 2) != 2:
        failures.append("heading_hierarchy")
    answers = [q["answer"] for q in faq_clean]
    repeated = blog_draft.repeated_sentences(prose + answers)
    measured["repeated_sentences"] = blog_draft.repeat_count(prose + answers)
    measured["generic_closers"] = generic_closers(prose)
    measured["faq_echoes"] = faq_echoes(faq_clean, prose)
    measured["plain_sentences"] = plain_sentences(prose + answers)[0]
    if repeated:
        failures.append("repeated_sentences")
    if plain_register(prose + answers) is not None:
        failures.append("plain_register")
    if example_reused(prose + answers):
        failures.append("length_example_copied")
    return {
        "draft_format": blog_draft.DRAFT_FORMAT_STRUCTURED,
        "title_candidates": titles[:blog_draft.MAX_TITLES],
        "body_paste": rendered["body_paste"],
        "body_blocks": rendered["body_blocks"],
        "tags": with_fixed_tags(structured["tags"]),
        "image_shots": rendered["image_shots"],
        "sources": sources,
        "fact_checks": blog_draft.fact_checks(list(structured["fact_checks"]) + _inline_checks(raw_prose),
                                              prose + answers, index),
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
    # The citations survive the broken JSON: resolved against THIS run's evidence, they are the
    # sources the one revision carries forward. Dropped, a revision that repaired the structure
    # shipped with zero sources and every check unsourced ('챗gpt 유료 가격',
    # bcp_503b936428417a0034a8, 2026-10-05 — its first draft had cited [S1]..[S3]).
    sources = blog_draft.resolve_sources([{"source_ref": ref} for ref in blog_draft.cited_refs(text)], index)
    measured["sources"] = len(sources)
    titles = blog_draft.prose_title_candidates(text)
    failures = list(blog_draft_score.critical_failures(measured, STANDARDS))
    failures += ["title_candidates"] if len(titles) < MIN_TITLES else []
    failures += ["structured_output", "seo_title"]
    repeated = blog_draft.repeated_sentences(prose)
    measured["repeated_sentences"] = blog_draft.repeat_count(prose)
    measured["generic_closers"] = generic_closers(prose)
    measured["faq_echoes"] = 0
    measured["plain_sentences"] = plain_sentences(prose)[0]
    if repeated:
        failures.append("repeated_sentences")
    if plain_register(prose) is not None:
        failures.append("plain_register")
    if example_reused(prose):
        failures.append("length_example_copied")
    return {
        "draft_format": blog_draft.DRAFT_FORMAT_LEGACY, "parse_reason": parse_reason,
        "title_candidates": titles, "body_paste": "\n\n".join(body),
        "body_blocks": parsed["body_blocks"], "tags": with_fixed_tags(parsed["tags"]),
        "image_shots": parsed["image_shots"], "sources": sources,
        "fact_checks": blog_draft.fact_checks([], prose, index), "structured": None,
        "content_brief": _brief(None, target, None),
        "platform_metadata": {
            "paste_format": "markdown", "seo_title": (titles or [target])[0],
            "seo_title_problems": ["missing"], "slug": None, "excerpt": None,
            "meta_description": " ".join(paragraphs[:first_heading])[:INTRO_SNIPPET_CHARS] or target,
            "meta_description_source": "body_first_400", "headings": [], "faq": [],
            "internal_links": [], "internal_link_candidates": len(context.link_candidates),
            "internal_link_source_state": context.link_source_state,
            **({"editorial": dict(context.editorial)} if context.editorial else {}),
        },
        "measured": measured, "failures": failures, "repeated": repeated,
    }


def _carry_marks(old: Mapping[str, Any], patched: dict[str, Any]) -> None:
    """A revision runs with no evidence and drops every ``[S#]`` and, often, the key sentences.
    A revised sentence that is the first draft's sentence (endings aside — `blog_draft.claim_key`)
    gets its citation back; a section with no key sentence gets the first draft's same-heading
    one, if that sentence is still in it."""
    refs: dict[str, str] = {}
    for paragraph in list(old.get("intro") or []) + [p for s in old.get("sections") or [] for p in s["paragraphs"]]:
        for sentence in _sentences(refs_inside(paragraph)):
            cited = blog_draft.cited_refs(sentence)
            if cited:
                refs[blog_draft.claim_key(blog_draft.strip_evidence_refs(sentence))] = cited[0]

    def mark(paragraph: str) -> str:
        if blog_draft.cited_refs(paragraph):
            return paragraph
        out = []
        for sentence in _sentences(paragraph):
            ref = refs.get(blog_draft.claim_key(sentence))
            out.append(f"{sentence} {ref}" if ref else sentence)
        return " ".join(out)

    keys = {s["heading"]: s.get("key_sentence") for s in old.get("sections") or [] if s.get("key_sentence")}
    patched["intro"] = [mark(p) for p in patched.get("intro") or []]
    sections = []
    for section in patched.get("sections") or []:
        section = dict(section, paragraphs=[mark(p) for p in section["paragraphs"]])
        key = keys.get(section["heading"])
        if not section.get("key_sentence") and key and any(key in p for p in section["paragraphs"]):
            section["key_sentence"] = key
        sections.append(section)
    patched["sections"] = sections


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
    _carry_marks(old, patched)
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
    measured["bold_sections"] = int(rendered["bold_sections"])
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
        {"check": "slug", "state": "ok" if meta.get("slug") else "warn", "detail": meta.get("slug") or "missing or not ASCII kebab"},
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
        {"check": "generic_closers", "state": "ok" if not measured.get("generic_closers") else "warn",
         "detail": f"{measured.get('generic_closers', 0)} paragraphs end on a sentence that fits any post"},
        {"check": "faq_echoes", "state": "ok" if not measured.get("faq_echoes") else "warn",
         "detail": f"{measured.get('faq_echoes', 0)} FAQ answer sentences repeat the body"},
        {"check": "bold_key_sentences",
         "state": "ok" if measured.get("bold_sections", 0) * 2 >= max(measured.get("h2_sections", 0), 1) else "warn",
         "detail": f"{measured.get('bold_sections', 0)} sections carry a bold key sentence"},
        {"check": "register", "state": "fail" if "plain_register" in (parts.get("failures") or []) else "ok",
         "detail": f"{measured.get('plain_sentences', 0)} sentences end in 해라체; the blog writes 존댓말"},
        {"check": "length_example", "state": "fail" if "length_example_copied" in (parts.get("failures") or []) else "ok",
         "detail": "the request's length example is not in the body"},
    ]
    return checks

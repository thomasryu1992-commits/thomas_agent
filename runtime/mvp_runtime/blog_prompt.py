"""The content engine's common prompt policy: the rules every platform's draft request carries.

Each platform profile (`blog_naver`, `blog_tistory`) builds its own request, and both used to
need the same rules — invent nothing, name what the evidence names, the vendor/tool line, the
Korean reader, one subject per section. Those lived inside the Naver request as one long string,
so a second platform could only copy them (and drift) or inherit the Naver prompt whole. They live
here once, each as a named part with the incident that put it there kept beside it.

A request is still ONE string to the model. :func:`compose` only states how parts join: parts of
a line by a space, lines by a newline — the Naver request's own joins, so moving its rules here
did not move a byte of it (`tests/test_mvp_runtime_blog_platform.py` pins that by digest).

Pure: no I/O, no clock.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from . import blog_draft

__all__ = [
    "ADD_SUBSTANCE_ASK",
    "DOMESTIC_READER_ASK",
    "EVIDENCE_SPECIFICS_ASK",
    "FACT_CHECK_RULE",
    "KEYWORD_FORM_ASK",
    "NO_INVENTION_RULE",
    "PLAIN_PARAGRAPH_RULE",
    "REVISION_NO_EVIDENCE_RULE",
    "SECTION_FOCUS_ASK",
    "VENDOR_NAME_ASK",
    "DraftContext",
    "LinkCandidate",
    "compose",
    "evidence_notes",
    "revision_previous",
]


@dataclass(frozen=True)
class LinkCandidate:
    """A post of the same blog the draft may link to, numbered ``[L#]`` in the request. The
    list is the runtime's (published posts with a URL), never the model's: a link the list does
    not hold is dropped, as an unresolvable ``[S#]`` is."""

    ref: str
    title: str
    url: str
    keyword: str = ""


@dataclass(frozen=True)
class DraftContext:
    """What a platform's request may need beyond the keyword, gathered by the engine before the
    content run: the internal-link candidates (``link_source_state`` says whether they could be
    read at all) and the other platform's posts on the same keyword, which the draft must not
    carry over. The Naver request uses none of it."""

    link_candidates: tuple[LinkCandidate, ...] = ()
    link_source_state: str = "unavailable"
    repurpose_from: tuple[Mapping[str, Any], ...] = ()
    # The platform's editorial plan for this post (Tistory: `blog_tistory.editorial_plan`).
    editorial: Mapping[str, Any] | None = None


def compose(*lines: Sequence[str]) -> str:
    """One request from named parts: each line's parts joined by a space, lines by a newline.
    An empty part is left out rather than leaving a double space."""
    return "\n".join(" ".join(part for part in line if part) for line in lines)


# What changes without notice is checked by a human before publishing (`blog_draft.fact_checks`
# flags the same categories deterministically, whether or not the model listed them).
FACT_CHECK_RULE = ("가격·무료 범위·사용 한도·기능 제공 여부·정책·버전·날짜를 쓴 문장은 모두 "
                   "fact_checks에 넣어라.")
# The other half is the runtime's: a reference the run never had is dropped (`resolve_sources`).
NO_INVENTION_RULE = ("근거 블록([S#]·[K#])에 없는 수치·가격·출처를 지어내지 마라 — 근거가 없으면 "
                     "source_ref를 null로 둬라.")
# The paragraphs are plain text on every platform: SmartEditor renders no markdown, and on
# Tistory the runtime itself writes the headings, the table and the links around them.
PLAIN_PARAGRAPH_RULE = "문단 안에 #, **, > 같은 마크다운 기호를 쓰지 마라."
# What the one revision is asked for a failure every platform shares.
COMMON_FAILURE_ASKS = {
    "structured_output": "content_draft를 지정한 JSON 객체 하나로만 출력하라(설명·마크다운 금지)",
    "repeated_sentences": ("같은 문장을 두 번 이상 쓰지 마라 — 반복된 문장은 한 곳에만 남기고, 나머지 자리는 "
                           "그 문단 소제목에 맞는 다른 내용으로 바꿔라"),
}
MAX_NAMED_REPEATS = 5
REVISION_SECTION_SCOPE_RULE = ("문단을 늘리거나 줄일 때도 그 섹션 소제목의 내용 안에서만 하고, 다른 섹션의 "
                               "주제를 끌어오지 마라.")


def output_contract(shape: str) -> str:
    """The structured contract: one JSON object inside the role's `content_draft` string
    (`blog_draft`'s module docstring says why the Role contract itself is not changed)."""
    return f"content_draft 필드에는 아래 형식의 JSON 객체 하나만 문자열로 넣어라(마크다운·설명 금지): {shape}"


def revision_output_contract(shape: str, last: str = "intro와 sections") -> str:
    return f"content_draft에는 같은 JSON 형식으로 전체 초안을 다시 넣어라(키는 이 순서대로, {last}를 맨 끝에): {shape}"


def repeated_sentences_ask(repeated: Sequence[str]) -> str:
    """The sentences a draft pasted twice, named (at most :data:`MAX_NAMED_REPEATS`)."""
    named = ", ".join(f"「{r[:60]}」" for r in repeated[:MAX_NAMED_REPEATS])
    more = f" 외 {len(repeated) - MAX_NAMED_REPEATS}개" if len(repeated) > MAX_NAMED_REPEATS else ""
    return f"두 번 이상 나온 문장: {named}{more}"


def revision_facts_rule(grows: bool) -> str:
    """The facts are frozen in every revision; one that is asked to grow may add only what the
    evidence notes carry (:func:`revision_tail`)."""
    if grows:
        return ("첫 초안의 사실·수치·가격·날짜는 바꾸지 말고 새 출처를 추가하지 마라. 새 사실은 아래 근거 메모에 "
                "있는 것만 쓸 수 있다 — 메모에 없는 이름·수치를 지어내지 마라. 분량을 늘릴 때는 근거 메모의 구체적인 "
                "내용이나 이미 쓴 내용의 방법·예시를 풀어 써라.")
    return "사실·수치·가격·날짜는 바꾸지 말고 새 사실이나 새 출처를 추가하지 마라."


def revision_tail(
    first: Mapping[str, Any], text: str, records: Mapping[str, Any] | None, *,
    grows: bool, key_order: Sequence[str],
) -> list[list[str]]:
    """The revision request's closing lines: the evidence notes when it grows, then the first
    draft itself with its evidence references taken out."""
    lines: list[list[str]] = []
    if grows:
        lines += [["근거 메모(첫 초안을 쓸 때 본 자료에서 발췌, 번호를 붙여 인용하지 말 것):"],
                  [evidence_notes(first, records)]]
    return lines + [["이전 초안:"], [revision_previous(first, text, key_order)]]


# The revision is its own governed run with no evidence of its own (see :func:`revision_previous`).
REVISION_NO_EVIDENCE_RULE = ("이 수정 실행에는 근거 블록이 없다 — [S1]·[K1] 같은 근거 번호를 "
                             "본문·facts·fact_checks 어디에도 쓰지 말고, sources는 빈 목록 []으로 "
                             "둬라(첫 초안의 출처는 그대로 유지된다).")


# What a paragraph grows by. Told to add "이유·예시·주의점", both revisions on 2026-09-30 that grew
# a short body ('CHATGPT요금제' bcp_7f50c01bc7f35aee465a, 'ai 번역기' bcp_4ad51169ab0a26df545d)
# gave every paragraph one more closing line that fits any post — "…지혜가 필요합니다",
# "꼼꼼한 확인이 실수를 미연에 방지합니다" — and the length passed on filler.
#
# The revision runs with no evidence blocks, so "근거 블록([S#])의 수치" (#1066) pointed at nothing:
# on candidate-1066 '포토샵 누끼따기' (bcp_746f52917537a5764286) the revision grew only its two
# intro paragraphs, each by one more closer. The specifics now come from the evidence notes the
# revision request carries (:func:`evidence_notes`).
ADD_SUBSTANCE_ASK = (
    "더하는 문장에는 그 섹션 소제목에 대한 구체적인 내용 — 아래 근거 메모에 있는 수치·메뉴나 버튼 이름·"
    "절차 단계·설정값, 또는 독자가 겪는 구체적인 상황 하나 — 을 담아라. '…이 중요합니다'·'…지혜가 "
    "필요합니다'·'…도움이 됩니다'·'…주의가 필요합니다'처럼 어느 글에나 붙는 맺음 문장으로 늘리지 마라. "
    "다른 문단에 이미 있는 문장을 옮겨 오거나 되풀이해서 늘리지도 마라 — 반복된 문장이 있으면 불합격이다. "
    "바로 앞 문장을 다른 말로 다시 말하는 문장(예: '…버튼을 누르면 등록이 완료됩니다.' 뒤의 '버튼을 누르는 "
    "순간 바로 등록이 끝납니다.')도 늘린 것이 아니다 — 더하는 문장은 그 문단에 아직 없는 정보여야 한다."
)


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


# Per note and in all: the Tavily snippets run 70~1,300 characters, and the revision request
# already carries the whole first draft under intake's 20,000-character cap.
MAX_EVIDENCE_NOTE_CHARS = 400
MAX_EVIDENCE_NOTES_CHARS = 2_000


def evidence_notes(first: Mapping[str, Any], records: Mapping[str, Any] | None) -> str:
    """The content run's web evidence as plain notes for the revision: title and snippet, no
    `[S#]` and no URL.

    The sources the first draft cited, when it cited any — the package carries exactly those
    (`blog_content._carry_first_evidence`), so what the revision adds from them stays covered.
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


def revision_previous(first: Mapping[str, Any], text: str, key_order: Sequence[str]) -> str:
    """The first draft as the revision sees it: its evidence references taken out.

    The revision is its own governed run, and it has no evidence of its own. Its web search runs
    on this request text and comes back empty, and it runs no keyword brief. So any `[S#]`/`[K#]`
    carried into it cites a source THAT run never had. The pipeline's validation said exactly
    that on 2026-09-28 ("A fact cites a source this run never provided: [S1]") and withheld the
    revision. The first draft's resolved sources are carried into the package instead
    (`blog_content._carry_first_evidence`)."""
    if first.get("structured") is not None:
        draft = dict(first["structured"])
        draft["sources"] = []
        draft["fact_checks"] = [{**c, "source_ref": None} for c in draft.get("fact_checks") or []]
        # In the shape's order, so the revision is shown the prose last too.
        draft = {k: draft[k] for k in key_order if k in draft}
        previous = json.dumps(draft, ensure_ascii=False)
    else:
        previous = text
    return blog_draft.strip_evidence_refs(previous)

"""What a requested post overlaps among the posts that already exist, on either platform. Pure.

Until 2026-10-05 the lane knew one answer: a keyword written anywhere — Naver or Tistory, the vault
or the ledger — was "already written", and rule-based selection skipped it
(:func:`covering_keyword`). That is right for a Naver fire and stays exactly so. It is wrong for a
Tistory one: the vault's own practice is that a Tistory post is usually the Google rewrite of a
Naver post (the Tistory front matter's ``source: "[[NN편-…]]"``), so "a Naver post exists" is the
normal starting point of a Tistory post, not a reason to skip it.

So an overlap is classified rather than answered yes/no, and every match is recorded with the
reason and the action the policy took:

- ``exact_duplicate`` — the same keyword on the same platform;
- ``cannibalization_risk`` — the same topic and the same search intent on the same platform (two
  posts competing for one query);
- ``platform_repurpose`` — the same keyword on the other platform;
- ``high_topic_overlap`` — the same topic and intent on the other platform, under another keyword;
- ``safe_distinct_intent`` — the same topic with a different search intent ('소상공인 ChatGPT
  활용법' beside '소상공인을 위한 ChatGPT 업무 자동화 7가지').

The intent and the topic are read off the keyword by a fixed lexicon (:data:`INTENT_MARKERS`),
never by a model: the same two keywords classify the same way every time, and a surprising class
can be traced to the marker that produced it. That is also its limit — a keyword with no marker
reads as ``informational``, and two posts the lexicon cannot tell apart are reported as the same
intent, the cautious direction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from . import naver_research

__all__ = [
    "ACTIONS",
    "ExistingContent",
    "INTENTS",
    "OVERLAP_TYPES",
    "assess",
    "blocks_selection",
    "classify",
    "content_words",
    "covering_keyword",
    "keyword_intent",
    "topic_core",
]

EXACT_DUPLICATE = "exact_duplicate"
CANNIBALIZATION_RISK = "cannibalization_risk"
PLATFORM_REPURPOSE = "platform_repurpose"
HIGH_TOPIC_OVERLAP = "high_topic_overlap"
SAFE_DISTINCT_INTENT = "safe_distinct_intent"
# Most severe first: a post's verdict is its most severe match.
OVERLAP_TYPES = (EXACT_DUPLICATE, CANNIBALIZATION_RISK, PLATFORM_REPURPOSE, HIGH_TOPIC_OVERLAP,
                 SAFE_DISTINCT_INTENT)

BLOCK = "block"
REWRITE_REQUIRED = "rewrite_required"
REVIEW = "review"
ALLOW = "allow"
OPERATOR_OVERRIDE = "operator_override"
ACTIONS = (BLOCK, REWRITE_REQUIRED, REVIEW, ALLOW, OPERATOR_OVERRIDE)

# The policy, per requested platform. Naver keeps the lane's original rule — anything covered on
# either platform is not a target (see `blog_content.written_keywords`) — so a cross-platform
# match there can only be reached by `target=`. Tistory blocks only its own platform: a Naver post
# on the same keyword is the conversion the vault already does by hand, and the draft is told to
# rewrite it rather than carry it over (`blog_tistory.content_request`).
_POLICY: dict[str, dict[str, str]] = {
    "naver": {EXACT_DUPLICATE: BLOCK, CANNIBALIZATION_RISK: BLOCK, PLATFORM_REPURPOSE: BLOCK,
              HIGH_TOPIC_OVERLAP: REVIEW, SAFE_DISTINCT_INTENT: ALLOW},
    "tistory": {EXACT_DUPLICATE: BLOCK, CANNIBALIZATION_RISK: BLOCK,
                PLATFORM_REPURPOSE: REWRITE_REQUIRED, HIGH_TOPIC_OVERLAP: REVIEW,
                SAFE_DISTINCT_INTENT: ALLOW},
}
_REASONS = {
    EXACT_DUPLICATE: "같은 플랫폼에 같은 키워드의 글이 있다",
    CANNIBALIZATION_RISK: "같은 플랫폼에 같은 주제·같은 검색 의도의 글이 있어 한 검색어를 두 글이 나눠 갖는다",
    PLATFORM_REPURPOSE: "다른 플랫폼에 같은 키워드의 글이 있다 — 옮기지 말고 새로 써야 한다",
    HIGH_TOPIC_OVERLAP: "다른 플랫폼에 같은 주제·같은 검색 의도의 글이 다른 키워드로 있다",
    SAFE_DISTINCT_INTENT: "같은 주제지만 검색 의도가 다르다",
}

# Search intent by keyword marker, in priority order: the first intent with a marker in the
# keyword wins ('챗gpt 요금제 비교' is a pricing search before it is a comparison).
INTENT_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("troubleshooting", ("오류", "에러", "안될때", "안됨", "안돼요", "해결")),
    ("cancellation", ("해지", "환불", "탈퇴", "취소")),
    # '유료'·'플랜' since 2026-10-05: without them '챗GPT 유료 차이' read as a comparison and
    # '챗gpt 유료 가격' as pricing, so a Tistory fire drafted the second beside the first —
    # one subject, one searcher, two posts.
    ("pricing", ("요금제", "요금", "가격", "비용", "구독료", "유료", "플랜")),
    ("free_tier", ("무료", "한도", "제한")),
    ("comparison", ("비교", "차이", "vs")),
    ("template", ("양식", "예시", "템플릿", "샘플", "예문")),
    ("listicle", ("추천", "종류", "가지", "모음", "자동화")),
    ("procedure", ("신청", "발급", "신고", "등록", "절차")),
    ("how_to", ("사용법", "사용방법", "활용법", "활용", "방법", "하는법", "만들기", "만드는법", "설정")),
)
INTENTS = tuple(intent for intent, _ in INTENT_MARKERS) + ("informational",)

# Particles a Korean keyword token may carry ('소상공인을' is '소상공인'), and words that frame a
# topic without being one.
_PARTICLES = ("으로", "에서", "을", "를", "이", "가", "은", "는", "의", "에", "로", "와", "과", "도")
_FRAME_WORDS = frozenset({"위한", "위해", "하는", "대한", "관련", "총정리", "정리", "완벽", "가이드",
                          "꿀팁", "팁", "쉽게", "초보", "최신"})
_COUNT_RE = re.compile(r"\d+\s*(?:가지|개|단계|편)")
_YEAR_RE = re.compile(r"20\d\d년?")


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


def keyword_intent(keyword: str) -> str:
    """The search intent a keyword's own words state, or ``informational`` when none does."""
    text = naver_research.normalize_keyword(keyword)
    if _COUNT_RE.search(str(keyword or "")):
        return "listicle"
    for intent, markers in INTENT_MARKERS:
        if any(marker in text for marker in markers):
            return intent
    return "informational"


def content_words(keyword: str) -> list[str]:
    out: list[str] = []
    for token in _YEAR_RE.sub(" ", _COUNT_RE.sub(" ", str(keyword or "").casefold())).split():
        for particle in _PARTICLES:
            if len(token) > len(particle) + 1 and token.endswith(particle):
                token = token[: -len(particle)]
                break
        if token and token not in _FRAME_WORDS:
            out.append(token)
    return out


def topic_core(keyword: str) -> str:
    """The keyword with its intent markers, counts, years, particles and framing words taken off,
    normalized — what the post is ABOUT. '소상공인을 위한 ChatGPT 업무 자동화 7가지' -> '소상공인chatgpt업무'."""
    core = naver_research.normalize_keyword("".join(content_words(keyword)))
    for _intent, markers in INTENT_MARKERS:
        for marker in sorted(markers, key=len, reverse=True):
            core = core.replace(marker, "")
    return core


def _same_topic(a: str, b: str) -> bool:
    core_a, core_b = topic_core(a), topic_core(b)
    if not core_a or not core_b:
        return False
    if core_a == core_b:
        return True
    short, long_ = sorted((core_a, core_b), key=len)
    # One topic inside the other, the shorter carrying at least half of the longer.
    return len(short) >= 2 and short in long_ and len(short) * 2 >= len(long_)


@dataclass(frozen=True)
class ExistingContent:
    """One post or package that already exists: where, under which keyword, and how to find it."""

    platform: str
    keyword: str
    title: str | None = None
    ref: str | None = None          # a vault path or a package id
    origin: str = "vault"           # "vault" | "ledger"


def classify(
    keyword: str, platform: str, existing_keyword: str, existing_platform: str,
) -> str | None:
    """The overlap type of a requested ``keyword`` on ``platform`` against one existing keyword,
    or None when they are unrelated."""
    same_platform = platform == existing_platform
    if naver_research.normalize_keyword(keyword) == naver_research.normalize_keyword(existing_keyword):
        return EXACT_DUPLICATE if same_platform else PLATFORM_REPURPOSE
    covered = covering_keyword(keyword, (existing_keyword,)) is not None
    if not (covered or _same_topic(keyword, existing_keyword)):
        return None
    # The intent decides, not the spelling: the vault keeps '클로드 사용법' (how_to) and '클로드
    # 무료 사용법' (free_tier) side by side on Tistory, though the word-overlap rule calls one
    # covered by the other. That rule still decides Naver's selection (`blog_content`), unchanged.
    if keyword_intent(keyword) == keyword_intent(existing_keyword):
        return CANNIBALIZATION_RISK if same_platform else HIGH_TOPIC_OVERLAP
    return SAFE_DISTINCT_INTENT


def action_for(platform: str, overlap_type: str, *, mode: str = "rule") -> str:
    """What the policy does with a match. Under ``target=`` the operator has decided: a match
    the rule would have blocked is recorded as the override it is, never silently allowed."""
    action = _POLICY[platform][overlap_type]
    if mode == "operator_override" and action == BLOCK:
        return OPERATOR_OVERRIDE
    return action


def blocks_selection(keyword: str, platform: str, existing: Iterable[ExistingContent]) -> str | None:
    """The existing keyword that keeps ``keyword`` from being a rule-selected target on
    ``platform`` (a BLOCK under the policy), or None."""
    for item in existing:
        overlap = classify(keyword, platform, item.keyword, item.platform)
        if overlap is not None and _POLICY[platform][overlap] == BLOCK:
            return f"{item.platform}:{item.keyword}"
    return None


MAX_MATCHES = 10


def assess(
    keyword: str, platform: str, existing: Iterable[ExistingContent], *,
    mode: str = "rule", source_state: str = "measured",
) -> dict[str, Any]:
    """The package's ``overlap`` record: every match (most severe first, at most
    :data:`MAX_MATCHES`), each with its reason and action, and the decision — the most severe.

    ``source_state`` says whether the published posts were read at all (``unavailable`` under a
    `target=` fire with no vault): no match from an unread source is not "no overlap"."""
    items = list(existing)
    matches: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        overlap = classify(keyword, platform, item.keyword, item.platform)
        key = (item.platform, naver_research.normalize_keyword(item.keyword))
        if overlap is None or key in seen:
            continue
        seen.add(key)
        match = {
            "existing_platform": item.platform,
            "existing_keyword": str(item.keyword)[:200],
            "existing_title": (str(item.title)[:200] if item.title else None),
            "existing_ref": (str(item.ref)[:200] if item.ref else None),
            "requested_platform": platform,
            "overlap_type": overlap,
            "reason": _REASONS[overlap],
            "action": action_for(platform, overlap, mode=mode),
        }
        matches.append(match)
    matches.sort(key=lambda m: OVERLAP_TYPES.index(m["overlap_type"]))
    decision: dict[str, Any] = {"overlap_type": None, "action": ALLOW,
                                "reason": "겹치는 기존 글이 없다" if source_state == "measured"
                                else "기존 글 목록을 읽지 못해 겹침을 판정하지 않았다"}
    if matches:
        top = matches[0]
        decision = {"overlap_type": top["overlap_type"], "action": top["action"],
                    "reason": f"{top['reason']} ({top['existing_platform']}: {top['existing_keyword']})"[:300]}
    return {
        "requested_platform": platform,
        "requested_keyword": keyword,
        "keyword_intent": keyword_intent(keyword),
        "source_state": source_state,
        "checked": len(items),
        "decision": decision,
        "matches": matches[:MAX_MATCHES],
    }


def from_published_posts(posts: Iterable[Mapping[str, Any]]) -> list[ExistingContent]:
    """One entry per keyword of every published (or drafted) vault post."""
    out: list[ExistingContent] = []
    for post in posts:
        for keyword in post.get("keywords") or ():
            out.append(ExistingContent(platform=str(post.get("platform")), keyword=str(keyword),
                                       title=post.get("title"), ref=post.get("path")))
    return out

"""Cross-platform overlap: classified, recorded with its reason and action, never a bare yes/no.

The five cases the policy must tell apart, each classified the same way every time, and the
policy's two halves: a Naver fire keeps the lane's original rule (written on either platform =
not a target), and a Tistory fire blocks only its own platform — a Naver post on the keyword is
the conversion the vault already does by hand, recorded as `platform_repurpose` with the draft
told to rewrite rather than carry it over.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime import blog_content, blog_overlap
from tests.test_mvp_runtime_blog_tistory import POSTS, _ideate, _Source, _tdraft

E = blog_overlap.ExistingContent


# --- the five fixtures ----------------------------------------------------------------------

@pytest.mark.parametrize("requested,platform,existing,existing_platform,expected", [
    # 1. exact duplicate: the same keyword on the same platform
    ("클로드 무료 사용법", "tistory", "클로드 무료 사용법", "tistory", "exact_duplicate"),
    # 2. same topic + same intent, another spelling, same platform
    ("캔바 사용 방법", "naver", "캔바 사용법", "naver", "cannibalization_risk"),
    # ... but the same words with another intent are two posts (both exist on the vault's Tistory)
    ("클로드 무료 사용법", "tistory", "클로드 사용법", "tistory", "safe_distinct_intent"),
    # 3. same topic + different intent (the brief's own example)
    ("소상공인을 위한 ChatGPT 업무 자동화 7가지", "tistory", "소상공인 ChatGPT 활용법", "naver",
     "safe_distinct_intent"),
    # 4. unrelated
    ("미리캔버스 포스터", "tistory", "소상공인 ChatGPT 활용법", "naver", None),
    # 5. the same keyword on Naver and on Tistory
    ("공유오피스 사업자등록", "tistory", "공유오피스 사업자등록", "naver", "platform_repurpose"),
    # and the cross-platform twin of 2
    ("캔바 사용 방법", "tistory", "캔바 사용법", "naver", "high_topic_overlap"),
    # the pair the third live Tistory fire missed: one tool's paid plans, asked two ways
    ("챗gpt 유료 가격", "tistory", "챗GPT 유료 차이", "tistory", "cannibalization_risk"),
    # ...while another subject on the same tool stays its own post
    ("챗gpt 유료 가격", "tistory", "챗GPT 클로드 비교", "tistory", "safe_distinct_intent"),
])
def test_each_case_classifies_deterministically(requested, platform, existing, existing_platform, expected):
    for _ in range(3):
        assert blog_overlap.classify(requested, platform, existing, existing_platform) == expected


def test_spacing_and_case_do_not_make_a_duplicate_new():
    assert blog_overlap.classify("ChatGPT 사용법", "naver", "CHATGPT사용법", "naver") == "exact_duplicate"


def test_the_intent_lexicon_reads_the_marker_in_priority_order():
    assert blog_overlap.keyword_intent("챗gpt 요금제 비교") == "pricing"
    assert blog_overlap.keyword_intent("클로드 오류 해결") == "troubleshooting"
    assert blog_overlap.keyword_intent("업무 자동화 7가지") == "listicle"
    assert blog_overlap.keyword_intent("미리캔버스 포스터") == "informational"
    assert blog_overlap.keyword_intent("캡컷 유료") == blog_overlap.keyword_intent("챗GPT 플랜 차이") == "pricing"


@pytest.mark.parametrize("platform,overlap,action", [
    ("naver", "exact_duplicate", "block"), ("naver", "platform_repurpose", "block"),
    ("tistory", "exact_duplicate", "block"), ("tistory", "cannibalization_risk", "block"),
    ("tistory", "platform_repurpose", "rewrite_required"), ("tistory", "high_topic_overlap", "review"),
    ("tistory", "safe_distinct_intent", "allow"),
])
def test_the_policy_per_requested_platform(platform, overlap, action):
    assert blog_overlap.action_for(platform, overlap) == action


def test_an_operator_override_of_a_block_is_recorded_as_the_override_it_is():
    assert blog_overlap.action_for("naver", "exact_duplicate", mode="operator_override") == "operator_override"
    assert blog_overlap.action_for("tistory", "platform_repurpose", mode="operator_override") == "rewrite_required"


def test_the_record_names_platform_keyword_type_reason_and_action_most_severe_first():
    record = blog_overlap.assess("클로드 무료 사용법", "tistory", [
        E("naver", "클로드 무료 사용법", title="클로드 무료로 어디까지 되나", ref="content/naver/73.md"),
        E("tistory", "클로드 요금제"),
        E("tistory", "배민 리뷰 답글"),
    ])
    assert record["checked"] == 3 and record["source_state"] == "measured"
    match, distinct = record["matches"]          # the unrelated post is not a match at all
    assert (distinct["existing_keyword"], distinct["overlap_type"], distinct["action"]) == (
        "클로드 요금제", "safe_distinct_intent", "allow")
    assert match == {"existing_platform": "naver", "existing_keyword": "클로드 무료 사용법",
                     "existing_title": "클로드 무료로 어디까지 되나", "existing_ref": "content/naver/73.md",
                     "requested_platform": "tistory", "overlap_type": "platform_repurpose",
                     "reason": match["reason"], "action": "rewrite_required"}
    assert record["decision"]["overlap_type"] == "platform_repurpose"


def test_an_unread_source_is_not_no_overlap():
    record = blog_overlap.assess("클로드 무료 사용법", "tistory", (), source_state="unavailable")
    assert record["decision"]["action"] == "allow" and "읽지 못해" in record["decision"]["reason"]


# --- selection, per platform ------------------------------------------------------------

METRICS = [{"keyword": "클로드무료사용법", "monthly_total": 1200}, {"keyword": "클로드요금제", "monthly_total": 900}]


def test_naver_selection_still_excludes_a_keyword_written_on_tistory():
    """The original rule, unchanged: written anywhere = not a Naver target."""
    published = _Source().load()
    keyword, reasoning = blog_content.select_target_keyword(
        METRICS, already_written=blog_content.written_keywords(None, published), already_tagged=published.tags)
    assert keyword is None
    assert {c["keyword"]: c["excluded_because"] for c in reasoning["considered"]} == {
        "클로드무료사용법": "already written (클로드 무료 사용법)", "클로드요금제": "already written (클로드 요금제)"}


def test_tistory_selection_blocks_only_its_own_platform():
    published = _Source().load()
    existing = blog_content.existing_content(None, published)
    keyword, reasoning = blog_content.select_target_keyword(
        METRICS, covered_by=lambda k: blog_overlap.blocks_selection(k, "tistory", existing))
    # '클로드 요금제' is a Tistory post; '클로드 무료 사용법' only a Naver one — a conversion.
    assert keyword == "클로드무료사용법"
    excluded = {c["keyword"]: c["excluded_because"] for c in reasoning["considered"]}
    assert excluded["클로드요금제"] == "already written (tistory:클로드 요금제)"


def test_a_naver_posts_google_keyword_is_reserved_on_tistory(tmp_path):
    post = tmp_path / "content" / "naver" / "67.md"
    post.parent.mkdir(parents=True)
    post.write_text("---\ntitle: 공유오피스\nkeywords: [공유오피스 사업자등록]\ngoogle_kw: 공유오피스 사업자등록 거부\n"
                    "url: https://blog.naver.com/thomasai/1\n---\n본문\n", encoding="utf-8")
    published = blog_content.VaultPublishedKeywordSource(tmp_path).load()
    existing = blog_content.existing_content(None, published)
    assert blog_overlap.blocks_selection("공유오피스 사업자등록 거부", "tistory", existing) == \
        "tistory:공유오피스 사업자등록 거부"
    # ... and the union the Naver rule reads is what it always was.
    assert set(published.keywords) == {"공유오피스 사업자등록", "공유오피스 사업자등록 거부"}


def test_a_ledger_package_counts_on_its_own_platform():
    class Ledger:
        def iter_records_with_archive(self, kinds):
            yield {"kind": "blog_content_package", "record": {"target_keyword": "캔바 사용법",
                                                             "package_id": "bcp_1"}}          # v0.2: Naver
            yield {"kind": "blog_content_package", "record": {"target_keyword": "클로드 요금제",
                                                             "platform": "tistory", "package_id": "bcp_2"}}
    assert blog_content.written_keywords(Ledger(), platform="tistory") == ["클로드 요금제"]
    assert blog_content.written_keywords(Ledger(), platform="naver") == ["캔바 사용법"]
    assert blog_content.written_keywords(Ledger()) == ["캔바 사용법", "클로드 요금제"]


# --- in the package ------------------------------------------------------------------------

def test_a_tistory_package_records_the_naver_post_it_must_not_copy():
    package = _ideate([_tdraft()])[0]["package"]
    overlap = package["overlap"]
    assert overlap["decision"]["overlap_type"] == "platform_repurpose"
    assert overlap["decision"]["action"] == "rewrite_required"
    assert overlap["matches"][0]["existing_platform"] == "naver"
    fit = {c["check"]: c for c in package["quality"]["layers"]["platform_fit"]["checks"]}
    assert fit["cross_platform_overlap"]["state"] == "warn"
    # Advisory: the overlap does not hold the draft back from review.
    assert package["quality"]["quality_state"] == "ready_for_review"


def test_an_override_onto_an_existing_tistory_post_is_recorded_as_an_override():
    posts = POSTS + ({"platform": "tistory", "path": "content/tistory/75.md", "title": "클로드 무료 사용법",
                      "url": "https://thomasai.tistory.com/75", "keywords": ("클로드 무료 사용법",), "tags": ()},)
    package = _ideate([_tdraft()], source=_Source(posts))[0]["package"]
    assert package["overlap"]["decision"] == {**package["overlap"]["decision"],
                                              "overlap_type": "exact_duplicate", "action": "operator_override"}
    fit = {c["check"]: c for c in package["quality"]["layers"]["platform_fit"]["checks"]}
    assert fit["cross_platform_overlap"]["state"] == "fail"

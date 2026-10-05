"""The Tistory profile: a Google-facing draft with its own brief, metadata, structure and checks.

What is pinned here is that a `platform=tistory` fire produces a package that is Tistory's own —
brief first, an SEO title held to the vault's title rule, an ASCII slug, the first-400 intro that
Tistory turns into the search snippet, H2/H3 headings rendered as markdown, alt text on every
capture direction, internal links resolved against the runtime's own candidate list (never the
model's), and a quality record whose platform layer says which of those held — and that it is
still a draft for a human, through the same one-revision engine as Naver.
"""

from __future__ import annotations

import json
from unittest import mock

import pytest

from runtime.mvp_runtime import blog_content, blog_draft, blog_prompt, blog_tistory
from runtime.mvp_runtime.errors import ToolError
from runtime.mvp_runtime.paths import repo_root
from runtime.read_only_kernel.schema_validation import validate_against_schema

NOW = "2026-10-05T09:00:00Z"
TARGET = "클로드 무료 사용법"
SCHEMA = repo_root() / "schemas" / "blog_content_package.v0.3.schema.json"


def _para(tag: str, n: int, words: str = "") -> str:
    """One ~170-character paragraph whose every sentence is its own (a repeated sentence is a
    failure of its own)."""
    return (f"{tag} {n}번째 문단에서는 {words} 화면의 위치부터 짚습니다. {tag} {n}번째 단계로 왼쪽 메뉴의 "
            f"설정 항목을 열고 사용량 표시를 확인하면 남은 횟수를 볼 수 있습니다. {tag} {n}번째 주의점은 대화가 "
            f"길어질수록 한도가 빨리 줄어든다는 사실이므로 주제마다 새 대화를 여는 편이 낫습니다. "
            f"{tag} {n}번째 예시로 표를 붙여 넣고 요약을 요청하면 결과가 바로 나옵니다. {tag} {n}번째로 "
            f"결과를 내려받을 때는 엑셀과 워드 가운데 필요한 형식을 고르면 됩니다.")


def _tdraft(*, h2=5, per_section=3, faq=3, links=("[L1]", "[L2]"), **over) -> dict:
    sections = []
    for s in range(h2):
        sections.append({"heading": f"무료 계정 {s}단계 설정", "level": 2,
                         "paragraphs": [_para(f"섹션{s}", p, "무료 계정") for p in range(per_section)]})
        if s == 1:
            sections.append({"heading": "파일 업로드 한도 확인", "level": 3,
                             "paragraphs": [_para("하위", p, "파일 업로드") for p in range(2)]})
    data = {
        "brief": {"search_intent": "free_tier", "audience": "클로드를 무료로 써 보려는 사람",
                  "article_angle": "무료 계정으로 되는 것과 안 되는 것을 판정한다",
                  "secondary_keywords": ["클로드 파일 업로드 제한", "클로드 무료 한도"],
                  "user_questions": ["클로드 무료로 파일 업로드 되나요", "무료 계정 사용량 한도는"],
                  "differentiators": ["무료·유료 기능 비교표"], "evidence_requirements": ["공식 요금 페이지"]},
        "seo_title": "클로드 무료 사용법: 파일 업로드와 한도까지",
        "title_candidates": ["클로드 무료 사용법, 유료와 다른 점", "클로드 무료 사용법 정리: 되는 것과 안 되는 것"],
        "slug": "claude-free-how-to-use",
        "excerpt": "클로드 무료 계정으로 할 수 있는 일과 한도를 정리했습니다.",
        "tags": ["클로드", "클로드무료", "AI도구", "파일업로드", "소상공인"],
        "image_shots": [{"after_section": s % h2, "what_to_capture": f"{s}번 설정 화면",
                         "alt_text": f"클로드 설정 화면 {s}", "tool_name": "Claude"} for s in range(6)],
        "table": {"after_section": 1, "rows": [["기능", "무료", "Pro"], ["파일 업로드", "가능", "가능"],
                                               ["프로젝트", "5개", "무제한"], ["리서치", "불가", "가능"]]},
        "internal_links": [{"link_ref": ref, "anchor_text": f"관련 글 {ref}", "after_section": 2} for ref in links],
        "sources": [{"source_ref": "[S1]", "title": None}, {"source_ref": "[S2]", "title": None}],
        "fact_checks": [],
        "faq": [{"question": f"무료 계정 질문 {i}", "answer": f"질문 {i}의 답은 공식 안내 기준으로 무료 계정에서도 "
                 f"가능하다는 것입니다. 다만 사용량 한도는 시점마다 달라질 수 있어 {i}번째로 확인이 필요합니다."}
                for i in range(faq)],
        "intro": [f"{TARGET}의 결론부터 말하면 무료 계정으로도 웹 검색과 파일 업로드, 엑셀 파일 만들기까지 됩니다. "
                  "새 그림을 그리는 기능은 요금제와 상관없이 없고, 리서치와 상위 모델은 유료 요금제에만 있습니다. "
                  "아래에서는 각 기능을 어디서 켜는지와 무료 한도가 어떻게 줄어드는지를 화면 순서대로 짚습니다. "
                  "가입 직후 기본값 그대로 쓰면 놓치는 설정 두 가지도 함께 다룹니다.",
                  "무료 계정의 한도는 고정된 횟수가 아니라 대화 길이와 첨부 파일 크기에 따라 달라집니다. 같은 질문이라도 "
                  "긴 대화 안에서 하면 빨리 막히고, 새 대화에서 하면 오래 갑니다. 그래서 이 글의 표와 단계는 한도를 아끼는 "
                  "순서로 짰습니다. 유료로 넘어갈지 판단하는 기준은 마지막 섹션의 비교표에 모았습니다.",
                  "무료 계정에서 막혔을 때 기다려야 하는 시간도 정해진 값이 아니어서, 막힌 시각과 풀린 시각을 적어 두면 "
                  "다음 계획을 세우기 쉽습니다. 업무에 쓰는 경우라면 한도가 풀리는 시점에 맞춰 큰 작업을 몰아 두는 편이 "
                  "효율적입니다. 이 순서만 지켜도 무료 계정 하나로 한 주의 문서 작업 대부분을 처리할 수 있습니다."],
        "sections": sections,
    }
    data.update(over)
    return data


def _records(*, trace="trace-content"):
    return {
        "task": {"identity": {"trace_id": trace}},
        "tool_use": {"hits": [{"title": f"Claude 도움말 {i}", "url": f"https://support.claude.com/{i}",
                               "source": "tavily"} for i in range(1, 4)]},
        "keyword_research": {"created_at": NOW, "degraded": False, "degraded_legs": {},
                             "metrics": [{"keyword": "클로드무료사용법", "monthly_pc": 300, "monthly_mobile": 900,
                                          "monthly_total": 1200, "competition": "낮음", "low_volume": False,
                                          "source": "naver_searchad", "competing_posts": 5400},
                                         {"keyword": "클로드파일업로드제한", "monthly_pc": 10, "monthly_mobile": 30,
                                          "monthly_total": 40, "competition": "낮음", "low_volume": False,
                                          "source": "naver_searchad"}],
                             "trend_keyword": TARGET, "trend_points": [{"period": "2026-09-01", "ratio": 80.0}]},
    }


def _result(draft, *, trace):
    text = draft if isinstance(draft, str) else json.dumps(draft, ensure_ascii=False)
    recs = dict(_records(trace=trace), agent_output={"role_specific_output": {"content_draft": text}})
    return {"status": "COMPLETED", "records": recs, "final_response": "(rendered)"}


POSTS = (
    {"platform": "tistory", "path": "content/tistory/72-claude-how-to.md", "title": "클로드 사용법: 처음 쓰는 순서",
     "url": "https://thomasai.tistory.com/72", "status": "published", "keywords": ("클로드 사용법",), "tags": ("클로드사용법",)},
    {"platform": "tistory", "path": "content/tistory/74-claude-pricing.md", "title": "클로드 요금제: Pro와 Max 차이",
     "url": "https://thomasai.tistory.com/74", "status": "published", "keywords": ("클로드 요금제",), "tags": ("클로드요금제",)},
    {"platform": "tistory", "path": "content/tistory/10-baemin.md", "title": "배달의민족 리뷰 답글 예시",
     "url": "https://thomasai.tistory.com/10", "status": "published", "keywords": ("배민 리뷰 답글",), "tags": ()},
    {"platform": "naver", "path": "content/naver/73-claude-free.md", "title": "클로드 무료로 어디까지 되나",
     "url": "https://blog.naver.com/thomasai/1", "keywords": (TARGET,), "tags": ()},
)


class _Source:
    def __init__(self, posts=POSTS):
        self.posts = posts

    def load(self):
        return blog_content.PublishedKeywords(
            keywords=tuple(k for p in self.posts for k in p["keywords"]),
            tags=tuple(t for p in self.posts for t in p["tags"]), posts=tuple(self.posts))


def _ideate(drafts, *, seeds=f"platform=tistory, target={TARGET}", source=None, revision_blocks=False):
    calls: list[tuple[str, str, dict]] = []
    queue = list(drafts)

    def fake_run(kind, request, *, blocked_code, **kwargs):
        calls.append((kind, request, kwargs))
        if revision_blocks and sum(1 for k, *_ in calls if k == "content") == 2:
            raise ToolError(blocked_code, "the content run did not complete: BUDGET")
        return _result(queue.pop(0), trace=f"trace-{len(calls)}")

    with mock.patch.object(blog_content, "_run", fake_run):
        sheet = blog_content.run_content_ideation(
            {"seeds": seeds}, now=NOW, published_source=source if source is not None else _Source())
    return sheet, calls


def tistory_package() -> dict:
    """A complete, schema-valid Tistory package (used by the published-URL writer's tests)."""
    return _ideate([_tdraft()])[0]["package"]


# --- the request ------------------------------------------------------------------------

def test_the_request_is_the_tistory_profiles_and_carries_the_link_candidates():
    _sheet, calls = _ideate([_tdraft()])
    (kind, request, kwargs), = calls
    assert kind == "content" and kwargs["budget_profile"] == "blog_content_tistory"
    assert "티스토리" in request and "네이버 블로그" not in request
    assert '"brief"' in request and request.index('"brief"') < request.index('"intro"')
    assert "[L1] 클로드 사용법: 처음 쓰는 순서" in request
    # An unrelated post is never offered just to fill the list.
    assert "배달의민족" not in request
    # The Naver post on the same keyword is named as what NOT to carry over.
    assert "클로드 무료로 어디까지 되나" in request and "옮기지 말고" in request


def test_a_scheduled_post_or_a_plain_http_url_is_never_a_link_candidate():
    """A scheduled post's URL is dead until it is published, and an http URL would fail the
    package schema — one bad link must not fail the fire."""
    posts = POSTS + (
        {"platform": "tistory", "path": "content/tistory/75.md", "title": "클로드 무료 프로젝트 기능",
         "url": "https://thomasai.tistory.com/75", "status": "scheduled", "keywords": ("클로드 프로젝트",), "tags": ()},
        {"platform": "tistory", "path": "content/tistory/76.md", "title": "클로드 무료 한도 정리",
         "url": "http://thomasai.tistory.com/76", "status": "published", "keywords": ("클로드 무료 한도",), "tags": ()},
    )
    context = blog_content.link_candidates(TARGET, _Source(posts).load(), "tistory")
    assert [c.url for c in context.link_candidates] == ["https://thomasai.tistory.com/72",
                                                       "https://thomasai.tistory.com/74"]


def test_the_length_plan_adds_up_to_every_standard():
    """The first live fire was asked for a total and wrote 956 characters; the plan is a shape,
    and a shape that does not add up to the standards would just move the miss."""
    import re

    low, high = blog_tistory.PLAN_PARAGRAPH_CHARS
    total_low, total_high = blog_tistory.plan_body_chars()
    standard = blog_tistory.STANDARDS
    assert total_low >= standard["body_chars"].low and total_high <= standard["body_chars"].high
    assert blog_tistory.PLAN_INTRO_PARAGRAPHS * low >= standard["intro_chars"].low
    assert standard["faq"].within(blog_tistory.PLAN_FAQ)
    assert standard["h2_sections"].within(blog_tistory.PLAN_SECTIONS)
    assert low <= len(re.sub(r"\s", "", blog_tistory.LENGTH_EXAMPLE_PARAGRAPH)) <= high


def test_the_request_states_the_plan_in_the_shape_and_in_prose():
    request = blog_tistory.content_request(TARGET)
    shape = json.loads(blog_tistory._DRAFT_SHAPE)
    assert len(shape["intro"]) == blog_tistory.PLAN_INTRO_PARAGRAPHS
    assert len(shape["sections"][0]["paragraphs"]) == blog_tistory.PLAN_PARAGRAPHS_PER_SECTION
    plan = blog_tistory.default_plan(TARGET)
    assert request.count(blog_tistory.length_plan(plan["faq_count"])) == 1
    assert f"문단 {blog_tistory.plan_paragraphs()}개" in request


def test_a_short_draft_revision_names_each_short_paragraph_and_how_much_it_needs():
    short = _tdraft(h2=3, per_section=1)
    short["sections"][0]["paragraphs"] = ["두 문장만 있는 짧은 문단입니다. 이것으로 끝입니다."]
    first = blog_tistory.interpret(json.dumps(short, ensure_ascii=False), TARGET, _records())
    request = blog_tistory.revision_request(TARGET, first, json.dumps(short, ensure_ascii=False), _records())
    assert "분량 계획(이대로 써라)" in request
    assert "섹션 0의 문단 0(현재" in request and "자 더)" in request


def test_without_published_posts_the_request_says_there_are_no_link_candidates():
    request = blog_tistory.content_request(TARGET, blog_prompt.DraftContext())
    assert blog_tistory.NO_LINK_CANDIDATES_RULE in request and "내부 링크 후보:" not in request


# --- the package --------------------------------------------------------------------------

def test_a_complete_draft_is_a_valid_ready_for_review_tistory_package():
    sheet, _calls = _ideate([_tdraft()])
    package = sheet["package"]
    validate_against_schema(package, SCHEMA, "blog_content_package")
    assert package["platform"] == "tistory" and sheet["platform"] == "tistory"
    assert package["quality"]["quality_state"] == "ready_for_review", package["quality"]["failures"]
    assert package["quality"]["standards_version"] == blog_tistory.STANDARDS_VERSION
    assert set(package["platform_metadata"]) == {"tistory"}
    assert package["publish_state"] == "draft"


def test_the_brief_records_intent_audience_questions_and_which_keywords_were_measured():
    brief = tistory_package()["platform_metadata"]["tistory"]["brief"]
    assert brief["primary_keyword"] == TARGET and brief["search_intent"] == "free_tier"
    assert brief["audience"] and brief["article_angle"] and brief["user_questions"]
    assert {k["keyword"]: k["measured"] for k in brief["secondary_keywords"]} == {
        "클로드 파일 업로드 제한": True, "클로드 무료 한도": False}
    assert tistory_package()["content_intent"] == {"keyword_intent": "free_tier", "declared_intent": "free_tier"}


def test_seo_metadata_title_slug_excerpt_and_the_derived_description():
    meta = tistory_package()["platform_metadata"]["tistory"]
    assert meta["seo_title"] == "클로드 무료 사용법: 파일 업로드와 한도까지"
    assert meta["slug"] == "claude-free-how-to-use" and meta["excerpt"]
    # No description field exists on Tistory: the snippet is the body's first 400 characters.
    assert meta["meta_description_source"] == "body_first_400"
    assert meta["meta_description"].startswith(f"{TARGET}의 결론부터")


@pytest.mark.parametrize("title,problem", [
    ("무료로 쓰는 법을 모두 알려 드리는 완벽한 클로드 무료 사용법", "keyword_late"),
    ("클로드 무료 사용법 — 파일 업로드", "non_ascii_symbol"),
    ("사장님을 위한 클로드 무료 사용법", "addresses_owner"),
    ("클로드 무료 사용법: " + "가" * 40, "too_long"),
    ("제미나이 무료 사용법", "keyword_missing"),
])
def test_the_title_rule_names_what_it_breaks(title, problem):
    assert problem in blog_tistory.title_problems(title, TARGET)


def test_a_broken_title_is_a_failure_the_revision_is_asked_to_fix_and_a_bad_slug_only_warns():
    """.5: this blog's addresses are numbers (`/N`); the slug only names the vault file."""
    bad = _tdraft(seo_title="사장님을 위한 클로드 무료 사용법 — 정리", slug="클로드-무료")
    sheet, calls = _ideate([bad, _tdraft()])
    first_failures = sheet["package"]["quality"]["first_draft_failures"]
    assert "seo_title" in first_failures and "slug" not in first_failures
    revision = calls[1][1]
    assert "seo_title을 메인 키워드로 시작해" in revision
    first = blog_tistory.interpret(json.dumps(bad, ensure_ascii=False), TARGET, _records())
    assert {c["check"]: c["state"] for c in blog_tistory.platform_checks(first, TARGET)}["slug"] == "warn"
    assert sheet["package"]["quality"]["revision_outcome"] == "REVISED"


def test_headings_render_as_markdown_h2_and_h3_and_a_leading_h3_is_a_hierarchy_failure():
    package = tistory_package()
    body = package["body_paste"]
    assert "\n\n## 무료 계정 0단계 설정\n\n" in body and "\n\n### 파일 업로드 한도 확인\n\n" in body
    assert "| 기능 | 무료 | Pro |\n| --- | --- | --- |" in body
    assert "## 자주 묻는 질문" in body and "## 참고한 자료" in body
    levels = [h["level"] for h in package["platform_metadata"]["tistory"]["headings"]]
    assert levels.count(2) == 5 and levels.count(3) == 1
    leading_h3 = _tdraft()
    leading_h3["sections"][0]["level"] = 3
    first = blog_tistory.interpret(json.dumps(leading_h3, ensure_ascii=False), TARGET, _records())
    assert "heading_hierarchy" in first["failures"]


def test_every_capture_direction_carries_its_alt_text():
    package = tistory_package()
    assert len(package["image_shots"]) == 6
    assert all(s["alt_text"] for s in package["image_shots"])
    checks = {c["check"]: c for c in package["quality"]["layers"]["platform_fit"]["checks"]}
    assert checks["image_alt_text"]["state"] == "ok"


def test_internal_links_resolve_only_to_the_runtimes_candidates():
    package = _ideate([_tdraft(links=("[L1]", "[L2]", "[L9]"))])[0]["package"]
    links = package["platform_metadata"]["tistory"]["internal_links"]
    assert [link["link_ref"] for link in links] == ["[L1]", "[L2]"]   # [L9] was never offered
    assert all(link["url"].startswith("https://thomasai.tistory.com/") for link in links)
    assert "함께 보면 좋은 글: [관련 글 [L1]](https://thomasai.tistory.com/" in package["body_paste"]


def test_internal_links_are_not_measured_when_no_post_list_could_be_read():
    first = blog_tistory.interpret(json.dumps(_tdraft(), ensure_ascii=False), TARGET, _records(),
                                   blog_prompt.DraftContext())
    assert first["measured"]["internal_links"] == -1
    assert first["platform_metadata"]["internal_links"] == []


def test_sources_are_this_runs_evidence_only_and_listed_with_their_urls():
    package = tistory_package()
    assert [s["source_ref"] for s in package["sources"]] == ["[S1]", "[S2]"]
    assert "- [Claude 도움말 1](https://support.claude.com/1)" in package["body_paste"]


def test_the_quality_layers_are_separate_and_none_is_a_single_score():
    layers = tistory_package()["quality"]["layers"]
    assert set(layers) == {"structural", "semantic", "evidence", "platform_fit"}
    assert layers["structural"]["state"] == "pass" and layers["evidence"]["state"] == "ok"
    signals = layers["semantic"]["signals"]
    assert signals["search_intent_match"]["state"] == "ok"
    assert signals["topic_coverage"]["state"] in ("ok", "warn")
    assert signals["information_gain"]["state"] == "not_measured"


def test_a_short_draft_is_revised_once_toward_unanswered_questions_not_padding():
    short = _tdraft(h2=3, per_section=1)
    sheet, calls = _ideate([short, _tdraft()])
    assert [k for k, *_ in calls] == ["content", "content"]
    revision = calls[1][1]
    assert "brief.user_questions 중 아직 답하지 않은 질문" in revision
    assert "근거 메모" in revision                      # it grows, so it gets the notes
    assert "[L1] 클로드 사용법" in revision              # the link list rides along
    assert sheet["package"]["quality"]["revision_count"] == 1


def test_a_revision_further_off_is_not_taken_and_a_blocked_one_keeps_the_first_draft():
    almost = _tdraft()
    almost["intro"] = almost["intro"][:2]      # the snippet ~25% short of 400: one critical miss
    worse = _tdraft(h2=2, per_section=1)       # the body half its floor and too few H2s
    sheet, _ = _ideate([almost, worse])
    assert sheet["package"]["quality"]["revision_outcome"] == "REVISION_FURTHER_OFF:KEPT_FIRST_DRAFT"
    sheet, _ = _ideate([almost], revision_blocks=True)
    assert sheet["package"]["quality"]["revision_outcome"].startswith("REVISION_BLOCKED")
    assert sheet["package"]["quality"]["quality_state"] == "needs_edit"


def test_a_prose_answer_still_becomes_a_reviewable_package_that_names_the_failure():
    prose = f"# {TARGET}\n\n도입 문단입니다. 무료 계정으로 되는 일을 정리합니다.\n\n## 첫 단계\n\n설정을 엽니다."
    sheet, _ = _ideate([prose, prose])
    package = sheet["package"]
    validate_against_schema(package, SCHEMA, "blog_content_package")
    assert package["quality"]["draft_format"] == "legacy_markdown"
    assert "structured_output" in package["quality"]["failures"]


def test_the_package_files_are_post_md_and_paste_md_under_their_own_folder(tmp_path):
    package = tistory_package()
    assert blog_content.package_dir(package).startswith("blog/tistory/2026-10-05-")
    post = blog_content.render_post_md(package)
    assert "## 티스토리 발행 정보" in post and "claude-free-how-to-use" in post
    assert "메타 디스크립션 칸이 없어" in post and "alt:" in post
    assert "## 품질 레이어" in post
    assert blog_content.render_paste_txt(package).startswith(f"{TARGET}의 결론부터")


# --- .3: what the first ready draft showed (bcp_c4be3ea7381cd1fa1faf, scored 6.1/10) ---------

def test_the_length_example_is_far_from_anything_the_lane_writes_about():
    """The .2 example was about 요금제·해지·환불 and was pasted in as an intro paragraph."""
    from runtime.mvp_runtime import blog_overlap
    markers = {m for _intent, ms in blog_overlap.INTENT_MARKERS for m in ms}
    assert not any(m in blog_tistory.LENGTH_EXAMPLE_PARAGRAPH for m in markers | {"요금", "플랜", "AI", "결제"})


def test_a_draft_that_reuses_the_length_example_fails_and_the_revision_is_told_why():
    reused = _tdraft()
    reused["intro"][2] = blog_tistory.LENGTH_EXAMPLE_PARAGRAPH.replace("베란다에서", "집 베란다에서")
    first = blog_tistory.interpret(json.dumps(reused, ensure_ascii=False), TARGET, _records())
    assert "length_example_copied" in first["failures"]
    request = blog_tistory.revision_request(TARGET, first, json.dumps(reused, ensure_ascii=False), _records())
    assert "길이 예시 문단을 본문에 옮겼다" in request
    assert "length_example_copied" not in blog_tistory.interpret(
        json.dumps(_tdraft(), ensure_ascii=False), TARGET, _records())["failures"]


def test_the_request_asks_for_a_fact_first_official_prices_and_no_generic_closers():
    request = blog_tistory.content_request(TARGET)
    assert "intro 첫 두 문장 안에 근거 블록에 나온 구체적 사실 하나" in request
    assert blog_tistory.OFFICIAL_SOURCE_RULE in request and "(해외 기준)" in request
    assert blog_tistory.CLOSER_RULE in request
    assert "본문 문장을 되풀이하지 말고" in request


def test_generic_closers_and_faq_echoes_are_counted_and_pointed_at_not_gated():
    body = ["플러스 요금제는 월 12달러입니다. 연간 결제는 10달러입니다. 안전한 데이터 관리가 곧 업무 효율을 지키는 지름길입니다.",
            "무료 플랜은 AI 체험 횟수가 정해져 있습니다. 횟수는 계정마다 한 번 주어집니다. 월 20회까지라 아껴 쓰는 편이 좋습니다."]
    assert blog_tistory.generic_closers(body) == 1          # the digit keeps the second one in
    faq = [{"question": "무료로 되나요", "answer": "무료 플랜은 AI 체험 횟수가 정해져 있습니다. 결제하면 늘어납니다."}]
    assert blog_tistory.faq_echoes(faq, body) == 1
    draft = _tdraft()
    draft["sections"][0]["paragraphs"][0] += " 꼼꼼한 확인이 성공의 지름길입니다."
    first = blog_tistory.interpret(json.dumps(draft, ensure_ascii=False), TARGET, _records())
    checks = {c["check"]: c["state"] for c in blog_tistory.platform_checks(first, TARGET)}
    assert checks["generic_closers"] == "warn" and "generic_closers" not in first["failures"]


def test_link_candidates_must_name_the_tool_not_just_share_an_intent_word():
    posts = POSTS + ({"platform": "tistory", "path": "content/tistory/44.md", "title": "노션 요금제 정리",
                      "url": "https://thomasai.tistory.com/44", "status": "published",
                      "keywords": ("노션 요금제",), "tags": ()},)
    context = blog_content.link_candidates("노션 AI 요금제", _Source(posts).load(), "tistory")
    # '클로드 요금제' shares only '요금제'; it is not offered.
    assert [c.url for c in context.link_candidates] == ["https://thomasai.tistory.com/44"]


def test_the_reading_file_keeps_blank_lines_between_markdown_blocks():
    post = blog_content.render_post_md(tistory_package())
    body = post.split("## 본문 (PASTE.md와 같은 마크다운)")[1]
    assert "\n\n## 무료 계정 0단계 설정\n\n" in body


def _broken(draft: dict) -> str:
    """The third live fire's damage: a stray quote before the final brace (`…"]}"}`)."""
    text = json.dumps(draft, ensure_ascii=False)
    return text[:-1] + '"}'


def test_a_draft_whose_json_broke_still_hands_its_citations_to_the_package():
    text = _broken(_tdraft())
    assert blog_draft.parse_structured(text)[0] is None
    first = blog_tistory.interpret(text, TARGET, _records())
    assert first["draft_format"] == "legacy_markdown"
    assert [s["source_ref"] for s in first["sources"]] == ["[S1]", "[S2]"]
    assert first["measured"]["sources"] == 2
    # A citation this run never had is still not a source.
    assert all(s["source_ref"] != "[S9]" for s in blog_tistory.interpret(
        text.replace("[S2]", "[S9]"), TARGET, _records())["sources"])


def test_a_revision_that_repairs_the_structure_keeps_the_broken_drafts_sources():
    sheet, _calls = _ideate([_broken(_tdraft()), _tdraft()])
    package = sheet["package"]
    assert package["quality"]["first_draft_failures"][:1] and "structured_output" in package["quality"]["first_draft_failures"]
    assert [s["source_ref"] for s in package["sources"]] == ["[S1]", "[S2]"]
    assert package["quality"]["layers"]["evidence"]["web_sources"] == 2
    assert "- [Claude 도움말 1](https://support.claude.com/1)" in package["body_paste"]


# --- .4: the register ('캡컷 유료', bcp_710bdd04cd3676404b5b: 90 of 92 sentences in 해라체) ---------

def _plain(text: str) -> str:
    return (text.replace("습니다", "다").replace("됩니다", "된다").replace("합니다", "한다")
            .replace("입니다", "이다").replace("니다", "다"))


def test_the_request_and_the_revision_both_ask_for_the_blogs_register():
    assert blog_tistory.REGISTER_RULE in blog_tistory.content_request(TARGET)
    first = blog_tistory.interpret(json.dumps(_tdraft(h2=3), ensure_ascii=False), TARGET, _records())
    assert first["failures"]                       # a short draft: the revision runs for length...
    request = blog_tistory.revision_request(TARGET, first, json.dumps(_tdraft(h2=3), ensure_ascii=False), _records())
    assert blog_tistory.REGISTER_RULE in request     # ...and is still told the register


def test_a_haera_che_draft_fails_and_the_revision_is_told_to_change_only_the_endings():
    plain = json.loads(_plain(json.dumps(_tdraft(), ensure_ascii=False)))
    first = blog_tistory.interpret(json.dumps(plain, ensure_ascii=False), TARGET, _records())
    assert "plain_register" in first["failures"] and first["measured"]["plain_sentences"] > 50
    request = blog_tistory.revision_request(TARGET, first, json.dumps(plain, ensure_ascii=False), _records())
    assert "어미만 바꿔라" in request
    checks = {c["check"]: c["state"] for c in blog_tistory.platform_checks(first, TARGET)}
    assert checks["register"] == "fail"


def test_a_few_plain_lines_are_carried_and_quotes_are_not_counted():
    ok = _tdraft()
    ok["sections"][0]["paragraphs"][0] += " 설정 화면에는 '저장이 완료되었다'라고 나온다."
    ok["sections"][1]["paragraphs"][0] += " 결과는 바로 나온다."
    first = blog_tistory.interpret(json.dumps(ok, ensure_ascii=False), TARGET, _records())
    assert "plain_register" not in first["failures"]
    assert blog_tistory.plain_sentences(["화면에는 \"완료되었다\"", "결과가 나온다."]) == (1, 1)


def test_the_complete_fixture_is_polite_throughout():
    first = blog_tistory.interpret(json.dumps(_tdraft(), ensure_ascii=False), TARGET, _records())
    assert first["measured"]["plain_sentences"] == 0 and "plain_register" not in first["failures"]


# --- .5: the vault's editorial system — forms rotate, key sentences bold, citations link in place ---

def _posts(*forms_hooks):
    return tuple({"platform": "tistory", "number": n, "form": f, "intro_hook": h, "path": f"content/tistory/{n}.md",
                  "keywords": (f"글 {n}",), "tags": (), "status": "published"}
                 for n, (f, h) in enumerate(forms_hooks, start=80))


def test_the_form_is_never_the_previous_posts_and_at_most_twice_in_five():
    # pricing prefers 판정형, but the last post was one
    plan = blog_tistory.editorial_plan("캡컷 유료", _posts(("절차형", "부정먼저"), ("판정형", "숫자먼저")))
    assert plan["form"] == "계산형" and plan["previous_form"] == "판정형"
    # 계산형 twice already in the last five: the next pricing choice
    plan = blog_tistory.editorial_plan("캡컷 유료", _posts(("계산형", "부정먼저"), ("계산형", "숫자먼저"), ("절차형", "질문답")))
    assert plan["form"] == "판정형"
    # how-to prefers 절차형
    assert blog_tistory.editorial_plan("캡컷 사용법", _posts(("판정형", "결론먼저")))["form"] == "절차형"


def test_the_intro_hook_follows_the_previous_posts_and_the_faq_count_rotates():
    one = blog_tistory.editorial_plan("캡컷 유료", _posts(("절차형", "숫자먼저")))
    two = blog_tistory.editorial_plan("캡컷 유료", _posts(("절차형", "숫자먼저"), ("판정형", one["intro_hook"])))
    assert one["intro_hook"] == "조건분기" and two["intro_hook"] != one["intro_hook"]
    assert {one["faq_count"], two["faq_count"]} <= set(blog_tistory.FAQ_COUNTS) and one["faq_count"] != two["faq_count"]


def test_a_pricing_post_carries_a_refresh_date_and_the_ai_category():
    plan = blog_tistory.editorial_plan("캡컷 유료", (), now="2026-10-05T09:00:00Z")
    assert plan["refresh_by"] == "2026-11-04" and plan["category"] == "사장님 AI 활용법"
    assert blog_tistory.editorial_plan("미리캔버스 포스터", (), now="2026-10-05T09:00:00Z")["refresh_by"] is None


def test_the_request_carries_the_forms_skeleton_hook_and_faq_count():
    plan = blog_tistory.editorial_plan("캡컷 사용법", _posts(("판정형", "결론먼저")))
    request = blog_tistory.content_request("캡컷 사용법", blog_prompt.DraftContext(editorial=plan))
    assert "이 글의 유형은 절차형이다" in request and blog_tistory.FORMS["절차형"]["skeleton"] in request
    assert blog_tistory.INTRO_HOOKS[plan["intro_hook"]] in request
    assert f"faq는 {plan['faq_count']}문항이다" in request
    assert blog_tistory.KEY_SENTENCE_RULE in request and blog_tistory.INLINE_CITATION_RULE in request


def _cited(draft: dict) -> dict:
    draft = json.loads(json.dumps(draft, ensure_ascii=False))
    first = draft["sections"][0]
    sentence = "무료 계정의 파일 업로드는 한 번에 20개까지 가능합니다."
    first["paragraphs"][0] += f" {sentence} [S3]"
    first["key_sentence"] = sentence
    return draft


def test_a_key_sentence_is_bold_and_an_inline_citation_becomes_a_link_and_a_sourced_check():
    first = blog_tistory.interpret(json.dumps(_cited(_tdraft()), ensure_ascii=False), TARGET, _records())
    body = first["body_paste"]
    assert "**무료 계정의 파일 업로드는 한 번에 20개까지 가능합니다.**" in body
    assert "([Claude 도움말 3](https://support.claude.com/3))" in body and "[S3]" not in body
    assert "[S3]" in [s["source_ref"] for s in first["sources"]]
    cited = [c for c in first["fact_checks"] if c["source_ref"] == "[S3]"]
    assert cited and cited[0]["verification_state"] == "source_cited"
    assert first["measured"]["bold_sections"] == 1


def test_a_citation_to_evidence_the_run_never_had_is_removed_not_linked():
    draft = _tdraft()
    draft["intro"][0] += " 근거 없는 문장입니다. [S9]"
    body = blog_tistory.interpret(json.dumps(draft, ensure_ascii=False), TARGET, _records())["body_paste"]
    assert "[S9]" not in body and "근거 없는 문장입니다." in body


def test_a_revision_that_drops_the_marks_gets_the_first_drafts_back():
    revised = _tdraft()
    revised["sections"][0]["paragraphs"][0] += " 무료 계정의 파일 업로드는 한 번에 20개까지 가능합니다."
    sheet, _ = _ideate([_cited(_tdraft(h2=3)), revised])
    body = sheet["package"]["body_paste"]
    assert "**무료 계정의 파일 업로드는 한 번에 20개까지 가능합니다.**" in body
    assert "(https://support.claude.com/3))" in body


def test_the_blogs_fixed_tags_ride_along_and_the_package_records_its_editorial_plan():
    package = tistory_package()
    assert package["tags"][-2:] == ["소상공인", "자영업"] and len(package["tags"]) <= 10
    editorial = package["platform_metadata"]["tistory"]["editorial"]
    assert editorial["form"] in blog_tistory.FORMS and editorial["faq_count"] in blog_tistory.FAQ_COUNTS
    post = blog_content.render_post_md(package)
    assert f"form: {editorial['form']}" in post and "unique_asset:" in post

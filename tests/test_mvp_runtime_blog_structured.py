"""The blog draft as a structured artifact, the one bounded revision, and the fact checks.

Before 2026-09-28 the content run's markdown was split with regexes: every heading became a
title candidate, `fact_checks` was hard-coded `[]`, the draft score was advisory only, and the
text that was parsed and scored was the rendered reply — `## Draft` heading and review sections
included — rather than the draft. What is pinned here:

- titles are their own field (at least three), never a section heading;
- the structure renders to the paste body deterministically, with no markdown in it;
- a draft that misses a critical standard gets exactly one revision, asked only about what
  failed, and the package records `ready_for_review` or `needs_edit` — it is never withheld;
- volatile claims are flagged, and nothing is ever marked verified by the runtime.
"""

from __future__ import annotations

import json

import pytest

from runtime.mvp_runtime import blog_content, blog_draft, blog_draft_score
from runtime.mvp_runtime.errors import ToolError
from runtime.mvp_runtime.paths import repo_root
from runtime.read_only_kernel.schema_validation import validate_against_schema

NOW = "2026-09-28T09:00:00Z"
TARGET = "미리캔버스 포스터"
SCHEMA = repo_root() / "schemas" / "blog_content_package.v0.2.schema.json"

_SENTENCE = ("미리캔버스 포스터를 만들 때는 템플릿을 고르고 글자를 바꾸고 색을 가게 분위기에 맞추는 "
             "순서로 진행하면 처음 쓰는 사장님도 삼십 분 안에 한 장을 끝낼 수 있습니다. 완성한 "
             "파일은 인쇄용과 휴대폰용으로 나눠 저장해 두면 다시 고칠 때 편합니다.")


def _para(i: int) -> str:
    return f"{i}단계 설명입니다. {_SENTENCE}"


def _draft(*, sections=5, per_section=3, titles=3, **over) -> dict:
    data = {
        "title_candidates": [f"{TARGET} 만드는 법 {i}가지 순서" for i in range(1, titles + 1)],
        "intro": [_para(0), _para(1)],
        "sections": [{"heading": f"템플릿 고르기 {s}",
                      "paragraphs": [_para(10 * s + p) for p in range(per_section)]}
                     for s in range(sections)],
        "tags": ["미리캔버스", "포스터제작", "소상공인"],
        "image_shots": [{"after_section": s, "what_to_capture": f"{s}번 섹션 화면",
                         "tool_name": "미리캔버스"} for s in range(4)],
        "fact_checks": [],
        "sources": [{"source_ref": "[S1]", "title": "미리캔버스 도움말"},
                    {"source_ref": "[S2]", "title": None}],
    }
    data.update(over)
    return data


def _records(hits=2, *, mock=False, keyword_rows=None, trace="trace-content"):
    return {
        "task": {"identity": {"trace_id": trace}},
        "tool_use": {"hits": [{"title": f"hit {i}", "url": f"https://example.org/{i}",
                               "source": "mock.search" if mock else "tavily"}
                              for i in range(1, hits + 1)]},
        "keyword_research": {"created_at": NOW, "degraded": False, "degraded_legs": {},
                             "metrics": keyword_rows or [], "trend_keyword": TARGET,
                             "trend_points": []},
    }


def _run_result(draft, *, trace="trace-content", records=None):
    text = draft if isinstance(draft, str) else json.dumps(draft, ensure_ascii=False)
    recs = records if records is not None else _records(trace=trace)
    recs = dict(recs, agent_output={"role_specific_output": {"content_draft": text}})
    # The rendered reply wraps the draft and appends review sections — never the thing parsed.
    return {"status": "COMPLETED", "records": recs,
            "final_response": "## Draft\n\n(rendered)\n\n## Key findings\n- review text"}


class _StaticSource:
    def load(self):
        return blog_content.PublishedKeywords()


def _ideate(monkeypatch, drafts, *, revision_blocks=False):
    """Run the lane with the governed runs replaced: one content run per entry in ``drafts``."""
    calls: list[tuple[str, str, dict]] = []
    queue = list(drafts)

    def fake_run(kind, request, *, blocked_code, **kwargs):
        calls.append((kind, request, kwargs))
        content_runs = sum(1 for k, *_ in calls if k == "content")
        if revision_blocks and content_runs == 2:
            raise ToolError(blocked_code, "the content run did not complete: BUDGET")
        return _run_result(queue.pop(0), trace=f"trace-{len(calls)}")

    monkeypatch.setattr(blog_content, "_run", fake_run)
    sheet = blog_content.run_content_ideation(
        {"seeds": f"target={TARGET}"}, now=NOW, published_source=_StaticSource())
    return sheet, calls


# --- 10/11. titles are titles, sections are sections ---------------------------------------

def test_a_section_heading_is_never_a_title_candidate_in_a_structured_draft():
    parts = blog_content.interpret_draft(json.dumps(_draft(), ensure_ascii=False), TARGET)
    headings = {s["heading"] for s in parts["structured"]["sections"]}
    assert parts["title_candidates"] and not headings & set(parts["title_candidates"])


def test_a_prose_draft_offers_only_its_level_one_title_never_an_h2():
    """The regression: '## 프롬프트 만들기' was offered as the post's title."""
    text = "# 챗GPT 프롬프트 가이드\n\n도입 문단입니다.\n\n## 프롬프트 만들기\n\n본문입니다."
    parts = blog_content.interpret_draft(text, "챗GPT 프롬프트")
    assert parts["title_candidates"] == ["챗GPT 프롬프트 가이드"]
    only_h2 = blog_content.interpret_draft("## 프롬프트 만들기\n\n본문입니다.", "챗GPT 프롬프트")
    assert only_h2["title_candidates"] == []
    assert "title_candidates" in only_h2["failures"]


def test_fewer_than_three_explicit_titles_is_a_failure_the_revision_is_asked_to_fix():
    parts = blog_content.interpret_draft(json.dumps(_draft(titles=2), ensure_ascii=False), TARGET)
    assert "title_candidates" in parts["failures"]
    ok = blog_content.interpret_draft(json.dumps(_draft(titles=3), ensure_ascii=False), TARGET)
    assert len(ok["title_candidates"]) >= blog_draft.MIN_TITLES
    assert "title_candidates" not in ok["failures"]


# --- 12/13. deterministic render, clean paste ----------------------------------------------

def test_the_structure_renders_deterministically():
    text = json.dumps(_draft(), ensure_ascii=False)
    a, b = (blog_content.interpret_draft(text, TARGET) for _ in range(2))
    assert a["body_paste"] == b["body_paste"] and a["body_blocks"] == b["body_blocks"]
    paragraphs = a["body_paste"].split("\n\n")
    # intro (2), then per section: heading + 3 paragraphs.
    assert [blk["paragraph_index"] for blk in a["body_blocks"]] == [2, 6, 10, 14, 18]
    assert paragraphs[2] == "템플릿 고르기 0"
    # Each shot lands after the LAST paragraph of the section it names.
    assert [s["after_paragraph"] for s in a["image_shots"]] == [5, 9, 13, 17]
    assert a["image_shots"][0]["tool_name"] == "미리캔버스"
    assert a["tags"] == ["미리캔버스", "포스터제작", "소상공인"]


def test_the_paste_carries_no_markdown_control_syntax():
    dirty = _draft()
    dirty["sections"][0]["heading"] = "## **템플릿** 고르기"
    dirty["sections"][0]["paragraphs"] = [
        "**굵게** 쓴 문장과 `코드` 표시가 있는 문단입니다. [캡처: 템플릿 검색 화면]",
        "> 인용 표시로 시작하는 문단입니다.",
        "항목 | 무료 | 유료\n|---|---|---|\n템플릿 | 있음 | 있음",
    ]
    dirty["tags"] = ["#미리캔버스", "포스터"]
    parts = blog_content.interpret_draft(json.dumps(dirty, ensure_ascii=False), TARGET)
    paste = parts["body_paste"]
    for token in ("##", "**", "`", "> ", "[캡처", "|---"):
        assert token not in paste, token
    assert "항목 | 무료 | 유료" in paste                  # the table row itself is plain text
    assert parts["tags"] == ["미리캔버스", "포스터"]
    # The capture marker became a direction, not lost.
    assert any(s["what_to_capture"] == "템플릿 검색 화면" for s in parts["image_shots"])


def test_a_fenced_or_prose_wrapped_json_still_parses_and_prose_falls_back_by_name():
    fenced = "```json\n" + json.dumps(_draft(), ensure_ascii=False) + "\n```"
    assert blog_draft.parse_structured(fenced)[0] is not None
    parts = blog_content.interpret_draft("그냥 산문 초안입니다.", TARGET)
    assert parts["draft_format"] == blog_draft.DRAFT_FORMAT_LEGACY
    assert "structured_output" in parts["failures"]


def test_the_lane_parses_the_roles_draft_not_the_rendered_reply():
    result = _run_result(_draft())
    assert blog_content._draft_text(result).startswith("{")
    assert "## Key findings" not in blog_content._draft_text(result)


# --- 14-17. one bounded revision ----------------------------------------------------------

def test_a_draft_that_clears_the_bar_is_not_revised(monkeypatch):
    sheet, calls = _ideate(monkeypatch, [_draft()])
    quality = sheet["package"]["quality"]
    assert [k for k, *_ in calls] == ["content"]
    assert (quality["quality_state"], quality["revision_count"]) == ("ready_for_review", 0)
    assert sheet["lineage"]["revision_trace_id"] is None
    validate_against_schema(sheet["package"], SCHEMA, "blog_content_package")


def test_a_critical_miss_gets_exactly_one_revision_and_a_pass_is_ready_for_review(monkeypatch):
    short = _draft(sections=2, per_section=1)           # too short, too few headings
    sheet, calls = _ideate(monkeypatch, [short, _draft()])
    assert [k for k, *_ in calls] == ["content", "content"]
    quality = sheet["package"]["quality"]
    assert quality["quality_state"] == "ready_for_review"
    assert quality["revision_count"] == 1 and quality["revision_outcome"] == "REVISED"
    assert set(quality["first_draft_failures"]) >= {"body_chars", "headings"}
    assert sheet["lineage"]["revision_trace_id"] == "trace-2"
    validate_against_schema(sheet["package"], SCHEMA, "blog_content_package")


def test_a_revision_that_still_fails_is_needs_edit_and_never_retried(monkeypatch):
    short = _draft(sections=2, per_section=1)
    sheet, calls = _ideate(monkeypatch, [short, short, _draft(), _draft()])
    assert [k for k, *_ in calls] == ["content", "content"]           # no third attempt
    quality = sheet["package"]["quality"]
    assert quality["quality_state"] == "needs_edit"
    assert quality["revision_outcome"] == "REVISED_STILL_FAILING"
    assert sheet["package"]["publish_state"] == "draft"               # recorded, not hidden
    validate_against_schema(sheet["package"], SCHEMA, "blog_content_package")


def test_a_blocked_revision_keeps_the_first_draft_as_needs_edit(monkeypatch):
    short = _draft(sections=2, per_section=1)
    sheet, calls = _ideate(monkeypatch, [short], revision_blocks=True)
    quality = sheet["package"]["quality"]
    assert [k for k, *_ in calls] == ["content", "content"]
    assert quality["quality_state"] == "needs_edit"
    assert quality["revision_outcome"].startswith("REVISION_BLOCKED:")
    assert sheet["lineage"]["revision_trace_id"] is None


def test_the_revision_request_names_only_what_failed_and_freezes_the_facts(monkeypatch):
    short = _draft(sections=5, per_section=1)           # headings fine, body too short
    _sheet, calls = _ideate(monkeypatch, [short, _draft()])
    request = calls[1][1]
    assert "1,800~3,500자" in request                    # body_chars
    assert "섹션(소제목)을 4~7개" not in request            # headings passed — not asked
    assert "새 사실이나 새 출처를 추가하지 마라" in request
    assert "keyword_seeds" not in calls[1][2]            # the Naver brief is not re-run
    assert calls[0][2]["keyword_seeds"] == TARGET


def test_a_revision_that_loses_the_structure_does_not_replace_a_structured_draft(monkeypatch):
    titles_short = _draft(titles=1)
    sheet, _calls = _ideate(monkeypatch, [titles_short, "산문으로 돌아온 초안입니다."])
    quality = sheet["package"]["quality"]
    assert quality["draft_format"] == "structured"
    assert quality["revision_outcome"] == "REVISION_UNSTRUCTURED:KEPT_FIRST_DRAFT"
    assert quality["quality_state"] == "needs_edit"


# --- 18/19. fact checks --------------------------------------------------------------------

def test_price_and_free_limit_claims_become_fact_checks_without_the_model_listing_them():
    draft = _draft()
    draft["sections"][1]["paragraphs"][0] = (
        "미리캔버스 무료 플랜은 월 10회까지 다운로드할 수 있습니다. 프로 요금제는 월 14,900원입니다.")
    parts = blog_content.interpret_draft(json.dumps(draft, ensure_ascii=False), TARGET)
    categories = {c["category"] for c in parts["fact_checks"]}
    assert {"free_tier", "price"} <= categories
    assert all(c["verification_state"] == "needs_manual_verification"
               and c["source_ref"] is None and c["verified_at"] is None
               for c in parts["fact_checks"])


def test_a_model_claimed_source_that_the_run_never_had_is_not_a_source():
    """The model cites [S9] and says it is verified; the run had two hits and no ninth."""
    draft = _draft(fact_checks=[
        {"claim": "무료 플랜은 월 10회까지", "why": "한도 변동", "source_ref": "[S9]", "verified": True},
        {"claim": "프로 요금제는 월 14,900원", "why": "가격 변동", "source_ref": "[S1]"},
    ], sources=[{"source_ref": "[S9]", "title": "지어낸 출처"}, {"source_ref": "[S1]", "title": "x"}])
    parts = blog_content.interpret_draft(json.dumps(draft, ensure_ascii=False), TARGET, _records())
    by_claim = {c["claim"]: c for c in parts["fact_checks"]}
    invented = by_claim["무료 플랜은 월 10회까지"]
    assert (invented["verification_state"], invented["source_ref"]) == (
        "needs_manual_verification", None)
    cited = by_claim["프로 요금제는 월 14,900원"]
    assert (cited["verification_state"], cited["source_ref"]) == ("source_cited", "[S1]")
    assert all(c["verification_state"] != "verified" for c in parts["fact_checks"])
    assert [s["source_ref"] for s in parts["sources"]] == ["[S1]"]
    assert parts["sources"][0]["url"] == "https://example.org/1"


def test_a_mock_search_hit_is_not_a_source():
    parts = blog_content.interpret_draft(
        json.dumps(_draft(), ensure_ascii=False), TARGET, _records(mock=True))
    assert parts["sources"] == [] and parts["measured"]["sources"] == 0


def test_the_schema_refuses_a_verified_check_without_its_source_and_time():
    package = _package_from(_draft())
    package["fact_checks"] = [{"claim": "무료 플랜은 월 10회까지", "why": "x", "category": "free_tier",
                               "verification_state": "verified", "source_ref": None,
                               "verified_at": None}]
    from runtime.read_only_kernel.schema_validation import RuntimeSchemaError
    with pytest.raises(RuntimeSchemaError):
        validate_against_schema(package, SCHEMA, "blog_content_package")


def _package_from(draft):
    parts = blog_content.interpret_draft(json.dumps(draft, ensure_ascii=False), TARGET, _records())
    return blog_content.build_package(
        target_keyword=TARGET, draft=parts,
        selection=blog_content.selection_evidence(None, {"rule": "operator override"},
                                                  selected_keyword=TARGET,
                                                  mode="operator_override", seeds=[], now=NOW),
        target=blog_content.target_evidence(TARGET, None, now=NOW),
        lineage={"selection_research_trace_id": None, "target_research_trace_id": None,
                 "content_trace_id": "t", "revision_trace_id": None},
        quality=blog_content.quality_record(parts, first_failures=parts["failures"],
                                            revision_count=0, revision_outcome=None),
        now=NOW)


def test_post_md_shows_the_checks_states_sources_and_quality():
    post = blog_content.render_post_md(_package_from(_draft()))
    assert "## 품질 상태: ready_for_review" in post
    assert "## 제목 후보 (소제목과 별개)" in post
    assert "### 템플릿 고르기 0" in post                    # headings marked in POST.md only
    assert "[S1]" in post and "https://example.org/1" in post


# --- 11. the scorer and the structure agree ----------------------------------------------

def test_structured_measurement_counts_the_structures_own_fields():
    draft = _draft()
    parts = blog_content.interpret_draft(json.dumps(draft, ensure_ascii=False), TARGET, _records())
    m = parts["measured"]
    assert m["headings"] == 5 and m["paragraphs"] == 17 and m["images"] == 4
    assert m["hashtags"] == 3 and m["sources"] == 2
    prose = draft["intro"] + [p for s in draft["sections"] for p in s["paragraphs"]]
    assert m["body_chars"] == sum(len("".join(p.split())) for p in prose)
    assert blog_draft_score.critical_failures(m) == []


def test_body_chars_is_prose_only_in_the_text_measurement_too():
    """Headings, the tag line and capture markers used to count toward the 1,800 floor."""
    text = "## 소제목\n\n본문 열 글자입니다.\n\n[캡처: 아주 긴 캡처 설명 문구]\n\n#태그하나 #태그둘"
    assert blog_draft_score.measure(text)["body_chars"] == len("본문열글자입니다.")
    assert blog_draft_score.STANDARDS_VERSION == "blog_draft_standards.2026-09-29"


# --- what the first real package showed (2026-09-28) ----------------------------------------

def test_a_blocked_revision_keeps_the_pipelines_own_reason(monkeypatch):
    """The first package's revision died as `REVISION_BLOCKED:IDEATION_REVISION_BLOCKED`; the
    pipeline's PROVIDER_ERROR and its message were in the run result and nowhere after it."""
    short = json.dumps(_draft(sections=2, per_section=1), ensure_ascii=False)
    calls: list[str] = []

    def fake_run_task(request, **kwargs):
        calls.append(kwargs["request_kind"])
        if len(calls) == 1:
            return _run_result(short)
        return {"status": "BLOCKED", "records": {},
                "block": {"stage": "worker", "reason_code": "PROVIDER_ERROR",
                          "message": "every provider in the chain failed: openrouter HTTP 429; "
                                     "google_ai_studio HTTP 503; groq HTTP 429"}}

    monkeypatch.setattr(blog_content, "run_task", fake_run_task)
    sheet = blog_content.run_content_ideation(
        {"seeds": f"target={TARGET}"}, now=NOW, published_source=_StaticSource())
    quality = sheet["package"]["quality"]
    assert quality["revision_outcome"] == "REVISION_BLOCKED:PROVIDER_ERROR"
    assert "groq HTTP 429" in quality["revision_detail"]
    assert "자동 수정이 막힌 이유: every provider" in blog_content.render_post_md(sheet["package"])
    validate_against_schema(sheet["package"], SCHEMA, "blog_content_package")


def test_a_blocked_content_run_names_the_inner_reason_and_its_message(monkeypatch):
    monkeypatch.setattr(blog_content, "run_task", lambda request, **k: {
        "status": "BLOCKED", "records": {},
        "block": {"reason_code": "VALIDATION_REVISE", "message": "Missing required sections"}})
    with pytest.raises(ToolError) as exc:
        blog_content._run("content", "r", blocked_code=blog_content.IDEATION_CONTENT_BLOCKED)
    assert exc.value.reason_code == blog_content.IDEATION_CONTENT_BLOCKED
    assert "VALIDATION_REVISE — Missing required sections" in exc.value.reason
    assert exc.value.data == {"inner_reason_code": "VALIDATION_REVISE",
                              "inner_message": "Missing required sections"}


def test_post_md_marks_the_chosen_keyword_whatever_its_spacing():
    """The queue spells '사업자등록증 발급', Search Ad '사업자등록증발급' — the first package
    listed its own choice as a plain candidate."""
    selection = blog_content.selection_evidence(
        None, {"rule": "r", "considered": [
            {"keyword": "사업자등록증발급", "monthly_total": 26500, "ad_competition": "높음",
             "low_volume": False, "excluded_because": None},
            {"keyword": "퇴직금지급기준", "monthly_total": 32830, "ad_competition": "높음",
             "low_volume": False, "excluded_because": None}]},
        selected_keyword="사업자등록증 발급", mode="rule", seeds=[], now=NOW)
    lines = blog_content._render_selection_evidence(selection)
    assert any("사업자등록증발급" in ln and ln.endswith("— 선정") for ln in lines)
    assert any("퇴직금지급기준" in ln and ln.endswith("— 후보") for ln in lines)


# --- the length plan (the first package: 730 characters against 1,800) ----------------------

def test_the_length_plan_adds_up_to_every_standard():
    """The old request's totals were consistent only well above their minimums (10 x 150 =
    1,500 < 1,800); the model met the minimums and landed at 730. The plan is the one shape that
    clears every standard, checked here against the standards themselves."""
    S = blog_draft_score.STANDARDS
    low, high = blog_content.PLAN_PARAGRAPH_CHARS
    total_low, total_high = blog_content.plan_body_chars()
    assert S["body_chars"].within(total_low) and S["body_chars"].within(total_high)
    assert S["paragraphs"].within(blog_content.plan_paragraphs())
    assert S["para_chars"].within(low) and S["para_chars"].within(high)
    assert S["headings"].within(blog_content.PLAN_SECTIONS)


def test_the_request_states_the_plan_not_only_the_totals():
    request = blog_content.content_request(TARGET)
    assert f"문단 {blog_content.plan_paragraphs()}개" in request
    assert "120~140자" in request and "1,800자에 못 미치면 불합격" in request
    # The contradictory triple is gone from the first request.
    assert "문단 10~20개·문단당 70~150자" not in request


def test_a_length_revision_carries_the_plan_against_the_drafts_own_numbers(monkeypatch):
    short = _draft(sections=5, per_section=1)
    _sheet, calls = _ideate(monkeypatch, [short, _draft()])
    request = calls[1][1]
    parts = blog_content.interpret_draft(json.dumps(short, ensure_ascii=False), TARGET)
    measured = parts["measured"]
    assert (f"현재 문단 {measured['paragraphs']}개·문단 평균 {measured['para_chars']}자·"
            f"합계 {measured['body_chars']}자") in request
    assert f"문단 {blog_content.plan_paragraphs()}개" in request


def test_a_revision_for_titles_alone_does_not_carry_the_length_plan(monkeypatch):
    _sheet, calls = _ideate(monkeypatch, [_draft(titles=1), _draft()])
    assert "분량 계획" not in calls[1][1]


# --- the revision carries no evidence references (2026-09-28, bcp_ea2d271d8bd9cd453c2d) -------
#
# The revision is its own governed run with no evidence: its web search runs on the long request
# text and came back empty, and it runs no keyword brief. Handing it the first draft's [S1] made
# the pipeline's validation withhold it: "A fact cites a source this run never provided: [S1]".

def _cited_draft(**over):
    draft = _draft(sections=5, per_section=1, **over)            # short: forces the revision
    draft["intro"][0] = "프로 요금제는 월 14,900원입니다 [S1]. " + draft["intro"][0]
    draft["fact_checks"] = [{"claim": "프로 요금제는 월 14,900원입니다", "why": "가격 변동",
                             "source_ref": "[S1]"}]
    return draft


def test_the_revision_request_carries_no_evidence_reference_and_says_why():
    first = blog_content.interpret_draft(json.dumps(_cited_draft(), ensure_ascii=False), TARGET,
                                         _records())
    assert first["sources"]                                        # the first draft did resolve [S1]
    request = blog_content.revision_request(TARGET, first, "")
    previous = request.split("이전 초안:\n", 1)[1]
    assert "[S1]" not in previous and "[S2]" not in previous and "[K1]" not in previous
    assert json.loads(previous)["sources"] == []
    assert all(c["source_ref"] is None for c in json.loads(previous)["fact_checks"])
    assert "근거 번호를 본문·facts·fact_checks 어디에도 쓰지 말고" in request


def test_a_revised_draft_keeps_the_first_drafts_sources_and_cited_checks(monkeypatch):
    first_draft = _cited_draft()
    revised = _draft()                                            # long enough, cites nothing
    revised["intro"][0] = "프로 요금제는 월 14,900원입니다. " + revised["intro"][0]
    revised["fact_checks"] = [{"claim": "프로 요금제는 월 14,900원입니다", "why": "가격 변동",
                               "source_ref": None}]
    runs = iter([_run_result(first_draft, records=_records(trace="t-content")),
                 _run_result(revised, records=_records(hits=0, trace="t-revision"))])
    monkeypatch.setattr(blog_content, "_run", lambda *a, **k: next(runs))
    sheet = blog_content.run_content_ideation(
        {"seeds": f"target={TARGET}"}, now=NOW, published_source=_StaticSource())
    package = sheet["package"]
    assert package["quality"]["revision_outcome"] == "REVISED"
    assert [s["source_ref"] for s in package["sources"]] == ["[S1]", "[S2]"]
    assert package["quality"]["measured"]["sources"] == 2
    cited = next(c for c in package["fact_checks"] if c["claim"] == "프로 요금제는 월 14,900원입니다")
    assert (cited["verification_state"], cited["source_ref"]) == ("source_cited", "[S1]")
    validate_against_schema(package, SCHEMA, "blog_content_package")


def test_a_reworded_claim_does_not_inherit_the_old_wordings_source():
    first = blog_content.interpret_draft(json.dumps(_cited_draft(), ensure_ascii=False), TARGET,
                                         _records())
    second = blog_content.interpret_draft(json.dumps(dict(_draft(), fact_checks=[
        {"claim": "프로 요금제는 한 달에 14,900원이다", "why": "가격 변동", "source_ref": None}]),
        ensure_ascii=False), TARGET, _records(hits=0))
    carried = blog_content._carry_first_evidence(first, second)
    reworded = next(c for c in carried["fact_checks"] if c["claim"].startswith("프로 요금제는 한 달에"))
    assert reworded["verification_state"] == "needs_manual_verification"


# --- the second length round (2026-09-29): averages 62 -> 76 -> 93 against a 110 floor ------

def test_the_length_example_is_itself_a_paragraph_of_the_planned_length():
    low, high = blog_content.PLAN_PARAGRAPH_CHARS
    n = len("".join(blog_content.LENGTH_EXAMPLE_PARAGRAPH.split()))
    assert low <= n <= high
    request = blog_content.content_request(TARGET)
    assert blog_content.LENGTH_EXAMPLE_PARAGRAPH in request and "내용은 따라 쓰지 마라" in request


def _chars(text):
    return len("".join(text.split()))


def _named_after(request: str, which: str) -> str:
    """The named list that follows ``which`` in a revision request."""
    return request.split(which, 1)[1].split("): ", 1)[1].split(". 이 문단마다", 1)[0]


def test_a_length_revision_names_each_short_paragraph():
    draft = _draft()
    long_para = blog_content.LENGTH_EXAMPLE_PARAGRAPH          # at the plan's length: not named
    draft["intro"] = [long_para, "짧은 도입입니다."]
    for section in draft["sections"]:
        section["paragraphs"] = [long_para] * 3
    draft["sections"][2]["paragraphs"][1] = "너무 짧은 문단입니다."
    draft["sections"][0]["paragraphs"][0] = "항목 | 무료 | 유료"          # a table is not lengthened
    first = dict(blog_content.interpret_draft(json.dumps(draft, ensure_ascii=False), TARGET),
                 failures=["body_chars"])
    request = blog_content.revision_request(TARGET, first, "")
    named = _named_after(request, "못 미치는 문단")
    assert named == (f"도입 문단 1({blog_content._gap(_chars('짧은 도입입니다.'))}), "
                     f"섹션 2의 문단 1({blog_content._gap(_chars('너무 짧은 문단입니다.'))})")


def test_the_named_list_is_capped():
    draft = _draft()
    draft["intro"] = [blog_content.LENGTH_EXAMPLE_PARAGRAPH] * 2
    for s_index, section in enumerate(draft["sections"]):            # 15 distinct short ones
        section["paragraphs"] = [f"짧은 문단 {s_index}-{p}입니다." for p in range(3)]
    first = dict(blog_content.interpret_draft(json.dumps(draft, ensure_ascii=False), TARGET),
                 failures=["body_chars"])
    request = blog_content.revision_request(TARGET, first, "")
    named = _named_after(request, "못 미치는 문단")
    assert named.count("(현재 ") == blog_content.MAX_NAMED_SHORT_PARAGRAPHS
    assert f"외 {15 - blog_content.MAX_NAMED_SHORT_PARAGRAPHS}개" in request


# --- the third round (2026-09-29, bcp_e8571b880a95946c9103) -----------------------------------
#
# The body passed at 2,645 but the paragraph average overshot to 155 (cap 150). The first draft's
# JSON broke inside a pipe-table section ('{": | : | :", "paragraphs": ...}') and lost every key
# after it, so the revision came back with `image_shots: []`. And "[S1, S3]" slipped the
# reference strip.

def test_the_plan_leaves_the_paragraph_ceiling_headroom():
    low, high = blog_content.PLAN_PARAGRAPH_CHARS
    assert (low, high) == (120, 140) and high < blog_draft_score.STANDARDS["para_chars"].high


def test_the_table_is_its_own_field_rendered_after_its_section_and_not_a_prose_paragraph():
    draft = _draft(table={"after_section": 1,
                          "rows": [["구분", "무료", "유료"], ["템플릿", "있음", "더 많음"], ["용량", "5GB", "1TB"]]})
    parts = blog_content.interpret_draft(json.dumps(draft, ensure_ascii=False), TARGET)
    paragraphs = parts["body_paste"].split("\n\n")
    # intro 2, then section 0 (heading + 3), section 1 (heading + 3), then the table.
    assert paragraphs[10] == "구분 | 무료 | 유료\n템플릿 | 있음 | 더 많음\n용량 | 5GB | 1TB"
    assert parts["measured"]["tables"] == 1
    assert parts["measured"]["paragraphs"] == 17                 # the table is not counted
    assert [b["paragraph_index"] for b in parts["body_blocks"]] == [2, 6, 11, 15, 19]
    request = blog_content.content_request(TARGET)
    assert '"table": {"after_section"' in request and "paragraphs 안에 ' | ' 행을 쓰지 마라" in request


@pytest.mark.parametrize("table", [
    {"rows": [["only header"]]},                  # a single-cell row is not a row
    {"rows": [["구분", "값"]]},                    # a header alone is not a table
    "구분 | 값",                                   # not an object
])
def test_a_malformed_table_is_dropped_not_guessed(table):
    parts = blog_content.interpret_draft(json.dumps(_draft(table=table), ensure_ascii=False), TARGET)
    assert parts["structured"]["table"] is None and parts["measured"]["tables"] == 0


def test_a_revision_that_drops_the_capture_directions_gets_the_first_drafts_back(monkeypatch):
    first = _draft(sections=5, per_section=1,
                   table={"after_section": 0, "rows": [["a", "b"], ["c", "d"]]})
    revised = dict(_draft(), image_shots=[])
    revised.pop("table", None)
    runs = iter([_run_result(first), _run_result(revised, records=_records(hits=0))])
    monkeypatch.setattr(blog_content, "_run", lambda *a, **k: next(runs))
    package = blog_content.run_content_ideation(
        {"seeds": f"target={TARGET}"}, now=NOW, published_source=_StaticSource())["package"]
    assert len(package["image_shots"]) == 4 and package["quality"]["measured"]["images"] == 4
    assert "a | b\nc | d" in package["body_paste"] and package["quality"]["measured"]["tables"] == 1
    validate_against_schema(package, SCHEMA, "blog_content_package")


def test_the_revision_request_asks_to_keep_images_and_the_table():
    first = blog_content.interpret_draft(json.dumps(_draft(sections=5, per_section=1), ensure_ascii=False),
                                         TARGET)
    assert "image_shots 4~8개와 table은 첫 초안의 것을 그대로 유지하라" in blog_content.revision_request(
        TARGET, first, "")


@pytest.mark.parametrize("cited", ["[S1, S3]", "[S1,K2]", "[ S2 , 3 ]"])
def test_grouped_citations_are_stripped_from_the_revision_and_resolved_in_sources(cited):
    assert blog_draft.strip_evidence_refs(f"문장 {cited} 끝") == "문장  끝"
    index = {"S1": {"title": "a", "url": "u1"}, "S2": {"title": "b", "url": None},
             "S3": {"title": "c", "url": None}, "K2": {"title": "k", "url": None}}
    resolved = [s["source_ref"] for s in blog_draft.resolve_sources([{"source_ref": cited}], index)]
    assert resolved and all(ref.strip("[]") in index for ref in resolved)


# --- a dropped closing bracket is put back, never guessed ----------------------------------

def _missing_paragraphs_closer(draft) -> str:
    """The 2026-09-29 run's shape: the last section ends ``…"}]`` — the ``]`` closing its
    ``paragraphs`` left out."""
    text = json.dumps(draft, ensure_ascii=False)
    assert text.count('"]}], "tags"') == 1
    return text.replace('"]}], "tags"', '"}], "tags"')


def test_a_dropped_closing_bracket_is_put_back_and_the_draft_parses():
    draft = _draft(table={"after_section": 1, "rows": [["항목", "내용"], ["기간", "1년"]]})
    parsed, reason = blog_draft.parse_structured(_missing_paragraphs_closer(draft))
    assert reason is None and parsed["brackets_inserted"] == 1
    assert [s["paragraphs"] for s in parsed["sections"]] == [
        s["paragraphs"] for s in draft["sections"]]
    assert parsed["table"] is not None and parsed["tags"] == draft["tags"]


def test_a_valid_draft_is_not_repaired():
    text = json.dumps(_draft(), ensure_ascii=False)
    assert blog_draft.repair_brackets(text) is None
    assert blog_draft.parse_structured(text)[0]["brackets_inserted"] == 0


def test_brackets_and_escaped_quotes_inside_the_text_are_left_alone():
    odd = '표기 예시는 "[1]}" 와 {괄호} 입니다. 끝에 \\ 가 와도 됩니다.'
    draft = _draft()
    draft["sections"][-1]["paragraphs"][-1] = odd
    parsed, reason = blog_draft.parse_structured(_missing_paragraphs_closer(draft))
    assert reason is None and parsed["brackets_inserted"] == 1
    assert parsed["sections"][-1]["paragraphs"][-1] == blog_draft.sanitize_paragraph(odd)


@pytest.mark.parametrize("text", [
    '{"sections": [{"heading": "a", "paragraphs": ["b"]}]]}',      # a surplus closer, not a gap
    '{"sections": [{"heading": "a", "paragraphs": ["b}]',            # cut off inside a string
])
def test_damage_that_is_not_a_dropped_closer_still_falls_back(text):
    assert blog_draft.repair_brackets(text[:text.rfind("}") + 1]) is None
    assert blog_draft.parse_structured(text) == (None, "JSON_UNPARSEABLE")


def test_a_repaired_draft_is_ready_for_review_and_the_package_says_it_was_repaired(monkeypatch):
    sheet, calls = _ideate(monkeypatch, [_missing_paragraphs_closer(_draft())])
    quality = sheet["package"]["quality"]
    assert [k for k, *_ in calls] == ["content"]
    assert quality["draft_format"] == "structured" and quality["quality_state"] == "ready_for_review"
    assert quality["brackets_inserted"] == 1
    validate_against_schema(sheet["package"], SCHEMA, "blog_content_package")
    assert "빠진 괄호 1개 보정" in blog_content.render_post_md(sheet["package"])


def test_a_clean_draft_records_no_repair():
    assert "brackets_inserted" not in _package_from(_draft())["quality"]


def test_the_revision_sees_the_repaired_draft_not_the_broken_text():
    short = _draft(sections=2, per_section=1)
    first = blog_content.interpret_draft(_missing_paragraphs_closer(short), TARGET, _records())
    assert first["draft_format"] == "structured"
    previous = blog_content.revision_request(TARGET, first, "").split("이전 초안:\n", 1)[1]
    assert "brackets_inserted" not in json.loads(previous)


def _missing_first_section_opener(draft) -> str:
    """The next run's shape: ``"sections": ["heading": …`` — the first section's ``{`` left out."""
    text = json.dumps(draft, ensure_ascii=False)
    assert text.count('"sections": [{"heading"') == 1
    return text.replace('"sections": [{"heading"', '"sections": ["heading"')


def test_a_dropped_object_opener_is_put_back_and_the_draft_parses():
    draft = _draft()
    parsed, reason = blog_draft.parse_structured(_missing_first_section_opener(draft))
    assert reason is None and parsed["brackets_inserted"] == 1
    assert [s["heading"] for s in parsed["sections"]] == [s["heading"] for s in draft["sections"]]


def test_a_dropped_opener_and_a_dropped_closer_in_one_draft_are_both_put_back():
    # bcp_5c5b3bdcfdea9d38cb47's first draft had both.
    draft = _draft(table={"after_section": 1, "rows": [["항목", "내용"], ["기간", "1년"]]})
    text = _missing_first_section_opener(draft).replace('"]}], "tags"', '"}], "tags"')
    parsed, reason = blog_draft.parse_structured(text)
    assert reason is None and parsed["brackets_inserted"] == 2
    assert len(parsed["sections"]) == 5 and parsed["table"] is not None


def test_a_colon_inside_a_paragraph_string_is_not_a_key():
    draft = _draft()
    draft["sections"][0]["paragraphs"][0] = '준비물: 사업자등록증, "통장 사본": 둘 다 필요합니다.'
    text = json.dumps(draft, ensure_ascii=False)
    assert blog_draft.repair_brackets(text) is None
    parsed, reason = blog_draft.parse_structured(_missing_paragraphs_closer(draft))
    assert reason is None and parsed["brackets_inserted"] == 1


# --- the keyword in the body, spacing ignored ----------------------------------------------

def test_keyword_hits_ignore_spacing_but_not_a_shortened_keyword():
    text = ("소상공인 스마트상점 신청. 소상공인스마트상점 대상. 소상공인 스마트 상점 요건. "
            "스마트상점 기술보급. 소상공인 지원.")
    assert blog_draft_score.keyword_hits(text, "소상공인 스마트상점") == 3
    assert blog_draft_score.keyword_hits(text, None) == -1
    assert blog_draft_score.keyword_hits(text, " ") == -1


def test_both_measurements_count_the_keyword_the_same_way():
    intro = ["소상공인스마트상점 사업은 매장에 기술을 들이는 사업입니다."]
    sections = [{"heading": "소상공인 스마트 상점 신청", "paragraphs": ["스마트상점만 쓴 문단."]}]
    structured = blog_draft_score.measure_structured(
        intro=intro, sections=sections, image_count=0, tags=[], source_count=0,
        keyword="소상공인 스마트상점")
    assert structured["keyword_hits"] == 2
    text = "\n\n".join(intro + ["소상공인 스마트 상점 신청", "스마트상점만 쓴 문단."])
    assert blog_draft_score.measure(text, "소상공인 스마트상점")["keyword_hits"] == 2


def test_the_request_asks_for_the_keyword_in_the_body_in_the_scorers_numbers():
    request = blog_content.content_request("소상공인 스마트상점")
    standard = blog_draft_score.STANDARDS["keyword_hits"]
    assert f"'소상공인 스마트상점'를 {standard.low}~{standard.high}회" in request
    assert "intro 첫 문단" in request and "줄여 쓴 것은 세지 않는다" in request


# --- too long: the revision is told which paragraphs to cut ---------------------------------
#
# bcp_5f459c95cf51fa821f6d (2026-09-29): all 17 paragraphs over 150 (average 168). The revision
# named only SHORT paragraphs (none) and repeated "add sentences to short ones"; it came back 169.

def _long(i: int) -> str:
    """A distinct paragraph over the plan (identical paragraphs are de-duplicated)."""
    return f"{i}번 안내입니다. {blog_content.LENGTH_EXAMPLE_PARAGRAPH} 제출 뒤 접수 번호를 적어 두세요."


def test_an_over_long_revision_names_each_long_paragraph_and_asks_to_cut():
    draft = _draft()
    draft["intro"] = [_long(0), _long(1)]
    for s_index, section in enumerate(draft["sections"]):
        section["paragraphs"] = [_long(10 * (s_index + 1) + p) for p in range(3)]
    draft["sections"][1]["paragraphs"][2] = blog_content.LENGTH_EXAMPLE_PARAGRAPH   # on plan
    first = blog_content.interpret_draft(json.dumps(draft, ensure_ascii=False), TARGET)
    assert "para_chars" in first["failures"]
    request = blog_content.revision_request(TARGET, first, "")
    low, high = blog_content.PLAN_PARAGRAPH_CHARS
    named = _named_after(request, f"{high}자를 넘는 문단")
    assert named.startswith(f"도입 문단 0({blog_content._gap(_chars(_long(0)))}), "
                            f"도입 문단 1({blog_content._gap(_chars(_long(1)))}), 섹션 0의 문단 0(")
    assert "섹션 1의 문단 2" not in named
    assert named.count("(현재 ") == blog_content.MAX_NAMED_SHORT_PARAGRAPHS
    assert named.endswith(f"외 {16 - blog_content.MAX_NAMED_SHORT_PARAGRAPHS}개")
    assert f"{low}~{high}자로 줄여라" in request and "문장을 더 붙이지 마라" in request
    # the lengthening half of the plan is not sent the other way
    assert "짧은 문단에는 문장을 더 붙여라" not in request
    assert "못 미치는 문단" not in request


def test_a_short_draft_still_gets_the_plan_and_the_short_list_not_the_cut():
    draft = _draft()
    draft["sections"][2]["paragraphs"][1] = "너무 짧은 문단입니다."
    first = dict(blog_content.interpret_draft(json.dumps(draft, ensure_ascii=False), TARGET),
                 failures=["body_chars"])
    request = blog_content.revision_request(TARGET, first, "")
    assert "못 미치는 문단" in request and "미치는 문단은 더해서" in request
    assert "줄여라" not in request


def test_the_para_chars_ask_does_not_contradict_the_cut():
    """'긴 문단은 나누고' told the model to split while the cut says keep the paragraph count."""
    assert "나누" not in blog_content._FAILURE_ASKS["para_chars"]


# --- the one revision never leaves the package worse ----------------------------------------
#
# bcp_e9717849588d1f438c66 (2026-09-29): the first draft averaged 153 (ceiling 150); asked to
# cut, the revision halved every paragraph — average 71, body 1,221 against the 1,800 floor —
# and replaced the draft that was far closer to passing.

def test_shortfall_is_relative_distance_from_the_critical_standards():
    ok = {"body_chars": 2000, "headings": 5, "para_chars": 130}
    assert blog_draft_score.shortfall(ok) == 0.0
    assert blog_draft_score.shortfall(dict(ok, para_chars=153)) == pytest.approx(3 / 150)
    assert blog_draft_score.shortfall(dict(ok, body_chars=1221)) == pytest.approx(579 / 1800)
    assert blog_draft_score.shortfall(dict(ok, keyword_hits=0)) == 0.0     # advisory only


def _all_long() -> dict:
    draft = _draft()
    draft["intro"] = [_long(0), _long(1)]
    for s_index, section in enumerate(draft["sections"]):
        section["paragraphs"] = [_long(10 * (s_index + 1) + p) for p in range(3)]
    return draft


def test_a_revision_further_off_than_the_first_draft_is_not_taken(monkeypatch):
    halved = _draft(sections=5, per_section=1)                    # the body falls under 1,800
    sheet, calls = _ideate(monkeypatch, [_all_long(), halved])
    quality = sheet["package"]["quality"]
    assert [k for k, *_ in calls] == ["content", "content"]
    assert quality["revision_outcome"] == "REVISION_FURTHER_OFF:KEPT_FIRST_DRAFT"
    assert quality["failures"] == ["para_chars"] and quality["quality_state"] == "needs_edit"
    assert quality["measured"]["body_chars"] >= 1800                 # the first draft's body
    assert "body_chars" in quality["revision_detail"]
    assert sheet["lineage"]["revision_trace_id"] == "trace-2"        # it ran; it is on record
    validate_against_schema(sheet["package"], SCHEMA, "blog_content_package")
    post = blog_content.render_post_md(sheet["package"])
    assert "- 자동 수정 메모: 수정본이 기준에서 더 벗어나" in post and "막힌 이유" not in post


def test_a_failing_revision_that_is_closer_is_still_taken(monkeypatch):
    very_short = _draft(sections=5, per_section=1)
    closer = _draft(sections=5, per_section=2)
    sheet, _calls = _ideate(monkeypatch, [very_short, closer])
    quality = sheet["package"]["quality"]
    assert quality["revision_outcome"] == "REVISED_STILL_FAILING"
    assert quality["measured"]["body_chars"] > blog_content.interpret_draft(
        json.dumps(very_short, ensure_ascii=False), TARGET)["measured"]["body_chars"]


def test_the_cut_touches_only_the_named_paragraphs_and_states_both_floors():
    first = blog_content.interpret_draft(json.dumps(_all_long(), ensure_ascii=False), TARGET)
    request = blog_content.revision_request(TARGET, first, "")
    low, _high = blog_content.PLAN_PARAGRAPH_CHARS
    assert "적힌 만큼만(대개 한 문장) 덜어내" in request and "나머지 문단은 손대지 마라" in request
    assert f"어떤 문단도 {low}자 아래로 줄이지 마라" in request
    assert "본문 합계가 1,800자 아래면 불합격이다" in request
    assert "한두 문장" not in request


# --- a draft that stopped with brackets still open -------------------------------------------
#
# bcp_cbbabb9953f3c67ba332 (2026-09-29): the first draft ended '…"]}' after its last section —
# no ']' for sections, no root '}', nothing after — and went to the legacy parser, so the
# revision had no paragraph to name.

def _stopped_after(draft: dict, marker: str) -> str:
    text = json.dumps(draft, ensure_ascii=False)
    assert text.count(marker) == 1
    return text[:text.index(marker) + len(marker)]


def test_a_draft_that_stopped_after_its_last_section_is_closed_at_the_end():
    draft = _draft()
    text = _stopped_after(draft, '"]}], "tags"')[:-len('], "tags"')]      # ends '…"]}'
    assert text.endswith('"]}')
    parsed, reason = blog_draft.parse_structured(text)
    assert reason is None and parsed["brackets_inserted"] == 2
    assert [s["paragraphs"] for s in parsed["sections"]] == [
        s["paragraphs"] for s in draft["sections"]]
    assert parsed["tags"] == [] and parsed["image_shots"] == []          # never filled in


def test_a_value_after_the_last_close_brace_is_kept_when_closing_at_the_end():
    draft = _draft()
    text = _stopped_after(draft, '"tags": ["미리캔버스", "포스터제작", "소상공인"]')
    parsed, reason = blog_draft.parse_structured(text)
    assert reason is None and parsed["brackets_inserted"] == 1
    assert parsed["tags"] == draft["tags"]


@pytest.mark.parametrize("cut", ['"tags": ["미리캔버스", ', '"tags": ', '"tags": ["미리'])
def test_a_draft_cut_after_a_comma_a_colon_or_inside_a_string_is_not_closed(cut):
    text = _stopped_after(_draft(), cut)
    assert blog_draft.parse_structured(text) == (None, "JSON_UNPARSEABLE")


def test_closing_at_the_end_is_opt_in():
    text = '{"sections": [{"heading": "a", "paragraphs": ["b"]}'
    assert blog_draft.repair_brackets(text) is None
    assert blog_draft.repair_brackets(text, close_at_end=True) == (text + "]}", 2)


# --- the prose last, and the plan said by its cap (2026-09-29) --------------------------------
#
# Twice the model stopped (finish_reason STOP) right after its last section and never wrote the
# table, tags or capture directions the shape put after `sections`; and paragraph averages ran
# 136~184 against a 120~140 plan whose last words were "120자 이상 ... 짧은 문단에는 더 붙여라".

def test_the_shape_puts_the_short_fields_first_and_the_prose_last():
    shape = json.loads(blog_content._DRAFT_SHAPE)
    assert tuple(shape) == blog_content.DRAFT_KEY_ORDER
    assert blog_content.DRAFT_KEY_ORDER[-2:] == ("intro", "sections")
    for request in (blog_content.content_request(TARGET),
                    blog_content.revision_request(
                        TARGET, blog_content.interpret_draft(
                            json.dumps(_draft(sections=2, per_section=1), ensure_ascii=False),
                            TARGET), "")):
        assert "intro와 sections를 맨 끝에" in request


def test_the_revision_is_shown_the_previous_draft_in_the_shapes_order():
    first = blog_content.interpret_draft(
        json.dumps(_draft(sections=2, per_section=1), ensure_ascii=False), TARGET)
    previous = blog_content.revision_request(TARGET, first, "").split("이전 초안:\n", 1)[1]
    keys = list(json.loads(previous))
    assert keys == [k for k in blog_content.DRAFT_KEY_ORDER if k in keys]
    assert keys[-2:] == ["intro", "sections"]


def test_the_plan_states_target_and_range_evenly_and_checks_both_ways():
    """Said first, the cap overshot the other way: averages 82 and 81 on the next two drafts."""
    low, high = blog_content.PLAN_PARAGRAPH_CHARS
    plan = blog_content._length_plan()
    assert f"{blog_content.plan_target()}자 안팎({low}~{high}자)" in plan
    assert "넘기지 마라" not in plan
    assert "평균이 150자를 넘거나 합계가 1,800자에 못 미치면 불합격이다" in plan
    assert (f"{high}자를 넘는 문단은 덜어내고 {low}자에 못 미치는 문단은 더해서 "
            f"{blog_content.plan_target()}자 안팎으로 맞춰라") in plan
    assert "이상인지 세어" not in plan and "짧은 문단에는 문장을 더 붙여라" not in plan


# --- a raw control character inside a string -------------------------------------------------
#
# bcp_5e855e2073b66b805cfd (2026-09-29): a complete draft, every key in order, failed with
# "Invalid control character" over one raw newline at the end of one paragraph.

def test_a_raw_newline_inside_a_paragraph_does_not_send_the_draft_to_legacy():
    draft = _draft()
    text = json.dumps(draft, ensure_ascii=False)
    last = draft["sections"][-1]["paragraphs"][-1]
    text = text.replace(json.dumps(last, ensure_ascii=False)[:-1] + '"',
                        json.dumps(last, ensure_ascii=False)[:-1] + '\n"', 1)
    assert "\n" in text
    with pytest.raises(ValueError):
        json.loads(text)                                     # strict JSON refuses it
    parsed, reason = blog_draft.parse_structured(text)
    assert reason is None and parsed["brackets_inserted"] == 0
    assert parsed["sections"][-1]["paragraphs"][-1] == blog_draft.sanitize_paragraph(last)


def test_a_raw_tab_and_a_missing_closer_together_still_parse():
    draft = _draft()
    draft["intro"][0] = "첫 문단\t안의 탭입니다. " + draft["intro"][0]
    text = _missing_paragraphs_closer(draft).replace("\\t", "\t")
    parsed, reason = blog_draft.parse_structured(text)
    assert reason is None and parsed["brackets_inserted"] == 1
    assert "\t" in parsed["intro"][0]


# --- the named list says how much (2026-09-29, bcp_dad48bf154c090f00307) --------------------
#
# Asked to add "a sentence or two", paragraphs averaging 81 grew by 23 and the body stopped 25
# short of 1,800.

@pytest.mark.parametrize("n, label", [
    (81, "현재 81자, 약 50자 더"), (120, "현재 120자, 약 10자 더"), (128, "현재 128자, 약 10자 더"),
    (171, "현재 171자, 약 40자 덜"), (145, "현재 145자, 약 20자 덜"),
])
def test_each_named_paragraph_carries_the_distance_to_the_target(n, label):
    assert blog_content.plan_target() == 130
    assert blog_content._gap(n) == label


def test_the_request_says_what_the_amount_in_brackets_means():
    draft = _draft()
    draft["sections"][2]["paragraphs"][1] = "너무 짧은 문단입니다."
    first = dict(blog_content.interpret_draft(json.dumps(draft, ensure_ascii=False), TARGET),
                 failures=["body_chars"])
    request = blog_content.revision_request(TARGET, first, "")
    assert "괄호는 130자까지 더할 양" in request and "적힌 만큼 이미 쓴 내용" in request

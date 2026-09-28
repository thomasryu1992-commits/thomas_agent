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
    assert blog_draft_score.STANDARDS_VERSION == "blog_draft_standards.2026-09-28"

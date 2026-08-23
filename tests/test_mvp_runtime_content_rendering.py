"""The content role's deliverable reaches the reader — not just the ledger.

The failure this pins was measured on the lane's first real ``draft_content`` dispatch
(2026-08-10): ``content.general`` wrote a complete, constraint-honouring draft into
``role_specific_output.content_draft``, and ``render_response`` — which special-cases the
analysis keys and nothing else — delivered only the meta-analysis around it. The caller
concluded the run "produced an analysis instead of a draft", diagnosed its own prompt as
the problem, and was about to burn a second run on a rephrase that would have produced
the same shape. The run never failed; the delivery did.
"""

from __future__ import annotations

from runtime.mvp_runtime.pipeline import render_response

DRAFT = "미리캔버스 포스터 제작, 30분 완성 가이드\n\n사장님들, 외주비 아끼는 방법을 알려드릴게요.\n\n[캡처: 미리캔버스 템플릿 검색 화면]"


def test_the_draft_is_rendered_and_rendered_first():
    """The draft is what was asked for; the analysis sections are the review of it."""
    rendered = render_response({
        "goal": "블로그 초안 작성", "summary": "초안을 작성함.",
        "recommendation": {"action": "발행 전 검토", "reason": "사실 확인 필요"},
        "role_specific_output": {
            "content_draft": DRAFT,
            "key_findings": ["미리캔버스는 무료로 쓸 수 있음."],
        },
    })
    assert "## Draft" in rendered
    assert "[캡처: 미리캔버스 템플릿 검색 화면]" in rendered
    assert rendered.index("## Draft") < rendered.index("## Key findings")
    assert rendered.index("## Draft") < rendered.index("## Recommendation")


def test_an_analysis_output_renders_byte_identically_without_the_key():
    """Every non-content role: no content_draft key, no new section, no drift."""
    output = {"goal": "g", "summary": "s",
              "role_specific_output": {"key_findings": ["f"]}}
    rendered = render_response(output)
    assert "## Draft" not in rendered


def test_a_blank_or_non_string_draft_renders_no_empty_section():
    for bad in ("", "   ", None, ["list"], {"k": "v"}):
        rendered = render_response({
            "goal": "g", "summary": "s",
            "role_specific_output": {"content_draft": bad, "key_findings": ["f"]},
        })
        assert "## Draft" not in rendered, repr(bad)

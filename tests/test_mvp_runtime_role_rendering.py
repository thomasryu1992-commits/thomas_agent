"""Every Role's deliverable reaches the reader, not just the ledger (sequence 2, P01).

``render_response`` special-cased the business analyst's keys and, since 2026-08-10, the content
role's ``content_draft``. Every other Role's own product was dropped from the delivered reply.
Measured for ``translation.general`` in the 2026-09-13 review: the Role contract requires
``translated_text``, the ledger carried it, and the reply carried none of it — reproduced on the
pure function. The research role's four keys and the content role's three companion keys were
dropped the same way. The run never failed; the delivery did.

The business-analysis path is pinned byte-for-byte below, because the fix renders unknown keys
generically and the one thing that must not happen is the default reply changing shape.
"""

from __future__ import annotations

from runtime.mvp_runtime.pipeline import render_response

TRANSLATION = "사장님, 이 문서는 계약 조건을 설명합니다.\n\n둘째 문단은 해지 조건입니다."


def _translation_output(**extra):
    return {
        "goal": "계약서 번역", "summary": "번역을 완료함.",
        "recommendation": {"action": "법무 검수 후 사용", "reason": "법률 용어가 포함됨"},
        "role_specific_output": {
            "key_findings": ["용어를 통일함"],
            "translated_text": TRANSLATION,
            "terminology_notes": ["contract → 계약", "termination → 해지"],
            "ambiguity_notes": ["'terms'는 조건/기간 두 뜻이 가능"],
            **extra,
        },
    }


def test_the_translation_is_rendered_and_rendered_first():
    """The translation is what was asked for; the analysis sections are the review of it."""
    rendered = render_response(_translation_output())
    assert "## Translation" in rendered
    assert "둘째 문단은 해지 조건입니다." in rendered
    assert rendered.index("## Translation") < rendered.index("## Key findings")
    assert rendered.index("## Translation") < rendered.index("## Recommendation")


def test_the_translation_notes_are_rendered_as_their_own_sections():
    rendered = render_response(_translation_output())
    assert "## Terminology notes" in rendered
    assert "- contract → 계약" in rendered and "- termination → 해지" in rendered
    assert "## Ambiguity notes" in rendered
    assert "- 'terms'는 조건/기간 두 뜻이 가능" in rendered
    # The Role's companion keys are read after the findings and before the recommendation.
    assert rendered.index("## Key findings") < rendered.index("## Terminology notes")
    assert rendered.index("## Ambiguity notes") < rendered.index("## Recommendation")


def test_the_research_role_keys_render_and_do_not_collide_with_the_search_hit_sources():
    """`sources` is what the model says it relied on; `## Sources` is what the worker fed it.
    Both render, under different headings, so neither is mistaken for the other."""
    rendered = render_response({
        "goal": "시장 조사", "summary": "조사함.",
        "role_specific_output": {
            "key_findings": ["수요는 확인됨"],
            "sources": ["통계청 2025 소상공인 실태조사"],
            "source_quality": ["통계청: 공식 통계"],
            "conflicting_evidence": [],
            "research_gaps": ["표본 크기 미공개"],
        },
    }, search_hits=[{"title": "T", "url": "http://x"}])
    assert "## Cited sources" in rendered and "- 통계청 2025 소상공인 실태조사" in rendered
    assert "## Sources" in rendered and "[S1] T — http://x" in rendered
    assert "## Source quality" in rendered and "## Research gaps" in rendered
    assert "## Conflicting evidence" not in rendered   # an empty list grows no heading


def test_the_content_companion_keys_render_after_the_draft():
    rendered = render_response({
        "goal": "블로그 초안", "summary": "초안 작성.",
        "recommendation": {"action": "발행 전 검토", "reason": "사실 확인"},
        "role_specific_output": {
            "content_draft": "초안 본문", "key_findings": ["f"],
            "target_audience": "소상공인 사장님",
            "channel_constraints": ["네이버 블로그 2,000자"],
            "publishing_risks": ["과장 표현 주의"],
        },
    })
    assert rendered.index("## Draft") < rendered.index("## Key findings")
    assert "## Target audience" in rendered and "소상공인 사장님" in rendered
    assert "## Channel constraints" in rendered and "- 네이버 블로그 2,000자" in rendered
    assert rendered.index("## Key findings") < rendered.index("## Target audience") < rendered.index("## Recommendation")


def test_the_business_analysis_reply_does_not_change_by_a_byte():
    """The generic rendering must skip the analyst's own keys (`evidence_quality` and
    `unresolved_questions` were never rendered; `key_findings` and `perspectives` have their
    named sections). Captured from the renderer before this change."""
    output = {
        "goal": "Evaluate the idea.", "summary": "A summary.",
        "recommendation": {"action": "Validate first.", "reason": "CAC dominates."},
        "uncertainty": ["Demand unproven."],
        "role_specific_output": {
            "key_findings": ["A finding."],
            "evidence_quality": "mixed",
            "unresolved_questions": ["Who pays?"],
            "perspectives": [{"perspective": "research", "verdict": "POSITIVE", "basis": "Demand looks real."}],
        },
    }
    expected = "\n".join([
        "# Evaluate the idea.", "", "A summary.", "",
        "## Key findings", "- A finding.", "",
        "## Perspectives", "- **research** (POSITIVE): Demand looks real.", "",
        "## Recommendation", "Validate first. — CAC dominates.", "",
        "## Uncertainty", "- Demand unproven.", "",
        "_Read-only analysis; automatically validated, not independently verified._",
    ])
    assert render_response(output) == expected


def test_blank_and_shapeless_role_values_grow_no_section():
    for bad in ("", "   ", None, [], {}, [None, ""], {"k": None}):
        rendered = render_response({
            "goal": "g", "summary": "s",
            "role_specific_output": {"key_findings": ["f"], "translated_text": bad, "terminology_notes": bad},
        })
        assert "## Translation" not in rendered, repr(bad)
        assert "## Terminology notes" not in rendered, repr(bad)


def test_a_mapping_valued_key_renders_as_key_value_lines():
    """`business.analysis` declares `revenue_assessment: object`; a future Role may declare
    others. A mapping renders as one line per field, nested values inline."""
    rendered = render_response({
        "goal": "g", "summary": "s",
        "role_specific_output": {
            "key_findings": ["f"],
            "revenue_assessment": {"model": "subscription", "arpu": 12, "risks": ["churn", "CAC"]},
        },
    })
    assert "## Revenue assessment" in rendered
    assert "- model: subscription" in rendered
    assert "- arpu: 12" in rendered
    assert "- risks: churn, CAC" in rendered


def test_risks_assumptions_and_next_actions_reach_the_reader():
    """They lived in the ledger alone (system review B4, 2026-09-25): validation REVISEs a NEGATIVE
    perspective with no stated risk, and the risk it insisted on was then never shown. Risks and
    assumptions render before the recommendation they bear on; next actions after it."""
    output = {
        "goal": "Evaluate the idea.", "summary": "A summary.",
        "recommendation": {"action": "Validate first.", "reason": "CAC dominates."},
        "risks": ["Thin margins."], "assumptions": ["Demand was not verified."],
        "next_actions": ["Run a paid test."], "uncertainty": ["Demand unproven."],
        "role_specific_output": {"key_findings": ["A finding."]},
    }
    rendered = render_response(output)
    for section in ("## Risks\n- Thin margins.", "## Assumptions\n- Demand was not verified.",
                    "## Next actions\n- Run a paid test."):
        assert section in rendered
    assert (rendered.index("## Risks") < rendered.index("## Assumptions")
            < rendered.index("## Recommendation") < rendered.index("## Next actions")
            < rendered.index("## Uncertainty"))

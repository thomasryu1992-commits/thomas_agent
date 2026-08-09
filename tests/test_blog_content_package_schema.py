"""blog_content_package.v0.1 — the lane's terminal artifact, and its closed schema.

The constraint worth a test of its own is the conditional: a package may not claim
`published` without carrying the URL. That URL is the only input the ranking tracker gets,
because the runtime does not publish and therefore cannot observe a publish. A `published`
state with no URL would end the feedback loop silently — the exact failure the lane's
design calls its one weak point.
"""

from __future__ import annotations

import copy

import pytest

from runtime.mvp_runtime.paths import repo_root
from runtime.read_only_kernel.schema_validation import RuntimeSchemaError, validate_against_schema

SCHEMA = repo_root() / "schemas" / "blog_content_package.v0.1.schema.json"

VALID = {
    "schema_version": "blog_content_package.v0.1",
    "package_id": "bcp_0123456789abcdef0123",
    "created_at_utc": "2026-08-09T09:00:00Z",
    "target_keyword": "AI 회계 자동화",
    "keyword_evidence": {
        "as_of": "2026-08-09T09:00:00Z",
        "degraded": False,
        "metrics": [{
            "keyword": "AI 회계 자동화",
            "monthly_pc": 900,
            "monthly_mobile": 4200,
            "monthly_total": 5100,
            "competition": "중간",
            "low_volume": False,
            "source": "naver_searchad",
        }],
        "total_competing_posts": 48213,
        "trend_points": [{"period": "2026-07-01", "ratio": 100.0}],
    },
    "title_candidates": ["AI 회계 자동화, 소상공인이 30분 만에 시작하는 법"],
    "body_paste": "요즘 장부 정리에 쓰는 시간이 아깝다면.\n\n오늘은 무료로 쓸 수 있는 방법을 정리했습니다.",
    "body_blocks": [{"paragraph_index": 0, "action": "heading", "note": "소제목으로 지정"}],
    "tags": ["AI회계", "소상공인"],
    "image_shots": [{
        "after_paragraph": 1,
        "what_to_capture": "무료 요금제 한도가 보이는 요금제 화면",
        "tool_name": "예시 회계 서비스",
    }],
    "fact_checks": [{"claim": "무료 플랜은 월 100건까지", "why": "무료 한도는 공지 없이 바뀝니다"}],
    "publish_state": "draft",
}


def _validate(record):
    return validate_against_schema(record, SCHEMA, "blog_content_package")


def test_a_draft_package_validates():
    _validate(VALID)


def test_the_schema_is_closed():
    record = copy.deepcopy(VALID)
    record["seo_score"] = 88
    with pytest.raises(RuntimeSchemaError):
        _validate(record)


def test_published_without_a_url_is_refused():
    """The whole point of the conditional: this is what silently ends the feedback loop."""
    record = copy.deepcopy(VALID)
    record["publish_state"] = "published"
    with pytest.raises(RuntimeSchemaError) as excinfo:
        _validate(record)
    assert "published_url" in str(excinfo.value)


def test_published_with_a_url_validates():
    record = copy.deepcopy(VALID)
    record["publish_state"] = "published"
    record["published_url"] = "https://blog.naver.com/example/223456789012"
    record["published_at_utc"] = "2026-08-11T02:00:00Z"
    _validate(record)


def test_a_draft_may_carry_no_url():
    """The conditional must not fire in reverse — a draft is complete without one."""
    assert "published_url" not in VALID
    _validate(VALID)


def test_the_url_must_be_a_naver_blog_post():
    record = copy.deepcopy(VALID)
    record["publish_state"] = "published"
    record["published_url"] = "https://example.com/somewhere"
    with pytest.raises(RuntimeSchemaError):
        _validate(record)


def test_degraded_evidence_is_representable():
    """A package built while the research backend was down must be expressible — and must
    say so, rather than being unrepresentable and therefore faked as healthy."""
    record = copy.deepcopy(VALID)
    record["keyword_evidence"] = {
        "as_of": "2026-08-09T09:00:00Z",
        "degraded": True,
        "degraded_reason_code": "TOOL_TRANSPORT",
        "metrics": [],
    }
    _validate(record)


def test_an_undocumented_competition_level_is_refused():
    record = copy.deepcopy(VALID)
    record["keyword_evidence"]["metrics"][0]["competition"] = "VERY HIGH"
    with pytest.raises(RuntimeSchemaError):
        _validate(record)


def test_naver_caps_a_post_at_thirty_tags():
    record = copy.deepcopy(VALID)
    record["tags"] = [f"태그{i}" for i in range(31)]
    with pytest.raises(RuntimeSchemaError):
        _validate(record)


def test_the_body_carries_no_markdown_heading():
    """Not a schema rule — a reminder encoded as a test. SmartEditor has no HTML mode and
    does not interpret markdown, so a '#' in body_paste reaches the published post as a
    literal character. The schema cannot express this; the fixture pins the intent."""
    assert not any(line.startswith(("#", "*", "-")) for line in VALID["body_paste"].splitlines())

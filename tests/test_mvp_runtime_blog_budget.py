"""The blog draft's own token allowance — and that nobody else got it (review B9).

A blog draft is 1,800-3,500 characters of Korean prose plus the structured JSON around it, and
Korean runs about one token per character: the generic 8,000-token agent share, half of it the
output allowance, truncates exactly the drafts the standards ask for. Raising
`TOKENS_PER_AGENT` would have raised every role. What is pinned here is the narrow version: a
named profile, bound to `content` requests, that sizes the task allocation and the specialist's
assignment together — so the contract's parent-budget invariant holds — and leaves every other
run byte-for-byte as it was.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime import blog_content, budgets
from runtime.mvp_runtime.budgets import (
    BLOG_CONTENT_BUDGET_PROFILE,
    TOKENS_PER_AGENT,
    default_execution_budget,
    output_allowance,
)
from runtime.mvp_runtime.errors import PlannerBlocked
from runtime.mvp_runtime.pipeline import run_task
from runtime.mvp_runtime.worker import MockProvider
from tests._helpers import requires_local_core

NOW = "2026-09-28T09:00:00Z"
BLOG_REQUEST = "'미리캔버스 포스터' 키워드로 네이버 블로그 글 초안을 작성해라."


def _limits(result, key):
    return result["records"][key]["execution_budget"]["limits"]


# --- 20/21. only the blog request grows ---------------------------------------------------

def test_the_blog_profile_doubles_one_agents_share_and_its_output_allowance():
    limits = default_execution_budget(profile=BLOG_CONTENT_BUDGET_PROFILE)["limits"]
    assert limits["token_budget"] == 16000
    assert output_allowance(limits["token_budget"]) == 8000
    # Nothing else in the allocation moves but the runtime, which the slower draft model needs
    # (2026-09-28, tests/test_mvp_runtime_blog_draft_model.py).
    generic = default_execution_budget()["limits"]
    moved = {"token_budget", "max_runtime_seconds"}
    assert {k: v for k, v in limits.items() if k not in moved} == {
        k: v for k, v in generic.items() if k not in moved}
    assert (limits["max_runtime_seconds"], generic["max_runtime_seconds"]) == (360, 120)


def test_without_a_profile_every_allocation_is_what_it_was():
    assert TOKENS_PER_AGENT == 8000
    assert default_execution_budget()["limits"]["token_budget"] == 8000
    assert default_execution_budget(agents=2)["limits"]["token_budget"] == 16000
    assert output_allowance(default_execution_budget()["limits"]["token_budget"]) == 4000


def test_an_unknown_profile_or_a_profile_on_another_kind_is_refused():
    with pytest.raises(PlannerBlocked) as exc:
        default_execution_budget(profile="bigger_please")
    assert exc.value.reason_code == "UNKNOWN_BUDGET_PROFILE"
    for kind in (None, "research", "translation"):
        with pytest.raises(PlannerBlocked) as exc:
            budgets.require_budget_profile(BLOG_CONTENT_BUDGET_PROFILE, kind)
        assert exc.value.reason_code == "BUDGET_PROFILE_KIND_MISMATCH"
    budgets.require_budget_profile(BLOG_CONTENT_BUDGET_PROFILE, "content")   # the one it is for
    budgets.require_budget_profile(None, "analysis")


@requires_local_core
def test_a_blog_content_run_gets_the_profile_and_a_plain_content_run_does_not(tmp_path):
    blog = run_task(BLOG_REQUEST, provider=MockProvider(), now=NOW, request_kind="content",
                    budget_profile=BLOG_CONTENT_BUDGET_PROFILE)
    plain = run_task(BLOG_REQUEST, provider=MockProvider(), now=NOW, request_kind="content")
    analysis = run_task("이 사업 아이디어를 분석해줘: 구독형 반려동물 사료 배송",
                        provider=MockProvider(), now=NOW)
    assert blog["status"] == plain["status"] == analysis["status"] == "COMPLETED"
    assert _limits(blog, "role_assignment")["token_budget"] == 16000
    assert _limits(blog, "task")["token_budget"] == 16000
    assert _limits(plain, "role_assignment")["token_budget"] == 8000
    assert _limits(plain, "task")["token_budget"] == 8000
    assert _limits(analysis, "role_assignment")["token_budget"] == 8000


@requires_local_core
def test_the_profile_on_an_analysis_run_blocks_rather_than_raising_its_budget():
    result = run_task("이 사업 아이디어를 분석해줘: 구독형 반려동물 사료 배송",
                      provider=MockProvider(), now=NOW, budget_profile=BLOG_CONTENT_BUDGET_PROFILE)
    assert result["status"] == "BLOCKED"
    assert result["block"]["reason_code"] == "BUDGET_PROFILE_KIND_MISMATCH"


# --- 22. the parent-budget invariant -------------------------------------------------------

@requires_local_core
@pytest.mark.parametrize("independent_validation", [False, True])
def test_every_assignment_stays_within_its_parent_task(independent_validation):
    result = run_task(BLOG_REQUEST, provider=MockProvider(), now=NOW, request_kind="content",
                      budget_profile=BLOG_CONTENT_BUDGET_PROFILE,
                      independent_validation=independent_validation)
    assert result["status"] == "COMPLETED"
    task = _limits(result, "task")
    keys = ["role_assignment"] + (["validator_assignment"] if independent_validation else [])
    total = 0
    for key in keys:
        assignment = _limits(result, key)
        assert assignment["token_budget"] <= task["token_budget"]
        total += assignment["token_budget"]
    assert total <= task["token_budget"]
    if independent_validation:
        # The reviewer keeps its own generic share; only the drafting specialist grows.
        assert _limits(result, "validator_assignment")["token_budget"] == 8000


# --- the lane asks for it on the drafting runs only ----------------------------------------

def test_the_lane_uses_the_profile_on_the_content_and_revision_runs_only(monkeypatch):
    seen: list[tuple[str, object]] = []

    def fake_run(kind, request, *, blocked_code, **kwargs):
        seen.append((kind, kwargs.get("budget_profile")))
        if kind == "research":
            return {"records": {"keyword_research": {
                "created_at": NOW, "degraded": False, "degraded_legs": {},
                "metrics": [{"keyword": "포스터", "monthly_pc": 10, "monthly_mobile": 90,
                             "monthly_total": 100, "competition": "낮음", "low_volume": False,
                             "source": "naver_searchad"}]}}}
        return {"records": {"agent_output": {"role_specific_output": {
            "content_draft": "산문 초안입니다."}}}}

    class _Source:
        def load(self):
            return blog_content.PublishedKeywords()

    monkeypatch.setattr(blog_content, "_run", fake_run)
    blog_content.run_content_ideation({"seeds": "포스터"}, now=NOW, published_source=_Source())
    assert seen == [("research", None), ("content", BLOG_CONTENT_BUDGET_PROFILE),
                    ("content", BLOG_CONTENT_BUDGET_PROFILE)]

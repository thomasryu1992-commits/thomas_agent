"""The blog draft's own OpenRouter model, and the time budget a slower model needs.

2026-09-28: every blog draft fell to groq, the chain's last member. The analysis chain's free
OpenRouter model is served from Google AI Studio's shared free pool (upstream 429), and Google's
own free tier answered 503 on six of seven models. A non-Google free model answered a real-sized
strict-schema draft, but it took 68 s, and the chain's first member gets 40 s at 120 s. Thomas:
use it for blog drafts only. Pinned here: only the draft and its revision get the swapped
member; the swap adds no authority; the draft chain's members get time enough; everything else
is unchanged.
"""

from __future__ import annotations

import pytest
import yaml

from runtime.mvp_runtime import blog_content, budgets, providers
from runtime.mvp_runtime.errors import ProviderError
from runtime.mvp_runtime.paths import repo_root
from runtime.mvp_runtime.providers import (
    FailoverProvider,
    GoogleAIStudioProvider,
    GroqProvider,
    OpenRouterLightProvider,
    OpenRouterProvider,
    with_openrouter_model,
)
from runtime.mvp_runtime.worker import MockProvider

NOW = "2026-09-28T09:00:00Z"
QWEN = "qwen/qwen3.8-27b:free"
AUTH = object()   # identity is all that is checked: the swap must hand over the SAME object


def _chain():
    return FailoverProvider([OpenRouterProvider(authorization=AUTH),
                             GoogleAIStudioProvider(authorization=None),
                             GroqProvider(authorization=None)])


# --- the swap ---------------------------------------------------------------------------------

def test_only_the_openrouter_member_changes_and_it_keeps_its_authorization():
    chain = _chain()
    swapped = with_openrouter_model(chain, QWEN, member_timeout_cap=120)
    first, second, third = swapped._providers
    assert type(first) is OpenRouterProvider and first.model_version == QWEN
    assert first._authorization is AUTH                     # no new authority, same gate grant
    assert second is chain._providers[1] and third is chain._providers[2]
    assert swapped._member_timeout_cap == 120
    assert chain._providers[0].model_version != QWEN         # the original chain is untouched


def test_a_bound_roles_keys_survive_the_swap():
    bound = _chain().bind_role_output_keys({"content_draft": "string"})
    swapped = with_openrouter_model(bound, QWEN)
    assert swapped._providers[0]._role_output_spec == {"content_draft": "string"}


def test_what_is_not_openrouter_is_returned_as_it_was():
    mock = MockProvider()
    assert with_openrouter_model(mock, QWEN) is mock
    light = OpenRouterLightProvider(authorization=None)
    assert with_openrouter_model(light, QWEN) is light      # the light tier has its own slug
    single = with_openrouter_model(OpenRouterProvider(authorization=AUTH), QWEN)
    assert single.model_version == QWEN and single._authorization is AUTH


# --- time enough for the slower member ---------------------------------------------------------

class _Timed:
    def __init__(self, name, fail=True):
        self.model_id, self.fail, self.timeouts = name, fail, []

    def generate(self, prompt, *, max_output_tokens, timeout_seconds):
        self.timeouts.append(timeout_seconds)
        if self.fail:
            raise ProviderError("PROVIDER_UNAVAILABLE", "503")
        return providers.ProviderResult(analysis={}, model_id=self.model_id, model_version="v",
                                        input_tokens=1, output_tokens=1, latency_ms=1,
                                        finish_reason="stop")


def test_the_draft_chain_gives_each_member_up_to_its_own_cap():
    members = [_Timed("a"), _Timed("b"), _Timed("c", fail=False)]
    FailoverProvider(members, member_timeout_cap=120).generate(
        "p", max_output_tokens=10, timeout_seconds=360)
    assert members[0].timeouts == [120] and members[1].timeouts == [120]


def test_the_analysis_chain_keeps_its_forty_seconds():
    members = [_Timed("a"), _Timed("b"), _Timed("c", fail=False)]
    FailoverProvider(members).generate("p", max_output_tokens=10, timeout_seconds=120)
    assert members[0].timeouts == [40]


def test_the_blog_profile_carries_the_runtime_and_nothing_else_moves():
    blog = budgets.default_execution_budget(profile=budgets.BLOG_CONTENT_BUDGET_PROFILE)["limits"]
    plain = budgets.default_execution_budget()["limits"]
    assert blog["max_runtime_seconds"] == 360 and plain["max_runtime_seconds"] == 120
    # Parent invariant: the task allocation sized with the same profile covers the assignment.
    task = budgets.default_execution_budget(agents=2, profile=budgets.BLOG_CONTENT_BUDGET_PROFILE)
    assert task["limits"]["max_runtime_seconds"] >= blog["max_runtime_seconds"]


# --- the lane: draft and revision only ---------------------------------------------------------

class _Source:
    def load(self):
        return blog_content.PublishedKeywords()


def _lane(monkeypatch, model):
    if model is None:
        monkeypatch.delenv(blog_content.BLOG_OPENROUTER_MODEL_ENV, raising=False)
    else:
        monkeypatch.setenv(blog_content.BLOG_OPENROUTER_MODEL_ENV, model)
    seen: list[tuple[str, object]] = []

    def fake_run(kind, request, *, blocked_code, **kwargs):
        seen.append((kind, kwargs.get("provider")))
        if kind == "research":
            return {"records": {"keyword_research": {
                "created_at": NOW, "degraded": False, "degraded_legs": {},
                "metrics": [{"keyword": "포스터", "monthly_pc": 10, "monthly_mobile": 90,
                             "monthly_total": 100, "competition": "낮음", "low_volume": False,
                             "source": "naver_searchad"}]}}}
        return {"records": {"agent_output": {"role_specific_output": {"content_draft": "산문."}}}}

    monkeypatch.setattr(blog_content, "_run", fake_run)
    chain = _chain()
    blog_content.run_content_ideation({"seeds": "포스터"}, now=NOW, providers={"provider": chain},
                                      published_source=_Source())
    return chain, seen


def test_with_the_env_set_only_the_draft_and_revision_run_on_the_blog_model(monkeypatch):
    chain, seen = _lane(monkeypatch, QWEN)
    assert [k for k, _ in seen] == ["research", "content", "content"]
    assert seen[0][1] is chain                                   # research: the analysis chain
    for _kind, provider in seen[1:]:
        assert provider._providers[0].model_version == QWEN
        assert provider._member_timeout_cap == blog_content.BLOG_DRAFT_MEMBER_TIMEOUT_SECONDS


@pytest.mark.parametrize("model", [None, "", "   "])
def test_unset_or_blank_keeps_the_analysis_chain_for_the_draft_too(monkeypatch, model):
    chain, seen = _lane(monkeypatch, model)
    assert all(provider is chain for _kind, provider in seen)


def test_only_the_pipeline_worker_is_handed_the_blog_model():
    compose = yaml.safe_load((repo_root() / "docker-compose.yml").read_text(encoding="utf-8"))
    holders = {name for name, spec in compose["services"].items()
               if blog_content.BLOG_OPENROUTER_MODEL_ENV in (spec.get("environment") or {})}
    assert holders == {"pipeline-worker"}

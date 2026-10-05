"""The content engine and its platforms: which profile a fire runs, and what each may not borrow.

The Naver request was tuned draft by draft for a month; splitting it out of the engine must not
have moved a byte of it. That is pinned by digest against the profile's `PROMPT_VERSION` — so a
deliberate prompt change is a version bump in the same commit, and an accidental one is a red
test. The rest pins the boundary: an unknown platform blocks before anything is spent, the Naver
request carries none of Tistory's rules and the Tistory request none of Naver's, and a package
says which platform it is for.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from runtime.mvp_runtime import (
    blog_content,
    blog_naver,
    blog_overlap,
    blog_platform,
    blog_prompt,
    blog_tistory,
)
from runtime.mvp_runtime.errors import ToolError
from runtime.mvp_runtime.paths import repo_root
from tests.test_mvp_runtime_blog_structured import TARGET, _draft, _ideate, _records

SCHEMA_V03 = repo_root() / "schemas" / "blog_content_package.v0.3.schema.json"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# Captured from `blog_content.content_request` / `revision_request` on main (d6c2f10f) BEFORE the
# engine/platform split, and re-captured only together with a `blog_naver.PROMPT_VERSION` bump.
# The revision digests are of requests built from `test_mvp_runtime_blog_structured`'s `_draft()`
# and `_records()`: those fixtures are part of the pin, and editing them re-captures these three.
NAVER_PROMPT_DIGESTS = {
    "naver_prompt.2026-10-05": {
        "content:미리캔버스 포스터": "aa590e1ff25fa52d4d7f73ce9a9f78ccd5db3b14d84853528c2349e464e0df2f",
        "content:ChatGPT 사용법": "5e2fae5c41b0320d95c040bc15912209fe999f829a1f02c2ddac18e798c4a9bd",
        "content:2026소상공인지원금신청": "acdaab2902deff33b3055f9da39fd0057b8a7d5569738f41135f17d498cf4e48",
        "revision:short": "31c322190fd9d1bb26db00d9116331ca2d8793f5c141f798da2c2fc06f9704d4",
        "revision:titles": "fc46454309cc786084553f0f38a0268cdf8aec79583243da359cef9ab6bf5132",
        "revision:long": "891971e239ae666c79b2c01e799bb18e2b5921440896e74de04664f96b173e95",
    },
}


def _naver_digests() -> dict[str, str]:
    out = {f"content:{kw}": _sha(blog_naver.content_request(kw))
           for kw in ("미리캔버스 포스터", "ChatGPT 사용법", "2026소상공인지원금신청")}
    long_ = _draft()
    long_["intro"] = [p * 2 for p in long_["intro"]]
    long_["sections"] = [dict(s, paragraphs=[p * 2 for p in s["paragraphs"]]) for s in long_["sections"]]
    for name, draft in (("short", _draft(sections=3, per_section=1)), ("titles", _draft(titles=1)),
                        ("long", long_)):
        text = json.dumps(draft, ensure_ascii=False)
        first = blog_naver.interpret(text, TARGET, _records())
        out[f"revision:{name}"] = _sha(blog_naver.revision_request(TARGET, first, text, _records()))
    return out


def test_the_naver_prompts_are_the_bytes_their_version_names():
    """If this fails after a deliberate prompt change: bump `blog_naver.PROMPT_VERSION` and add
    its digests here. If it fails after a refactor, the refactor changed what Naver is asked."""
    assert blog_naver.PROMPT_VERSION in NAVER_PROMPT_DIGESTS, "a new prompt version needs its digests"
    assert _naver_digests() == NAVER_PROMPT_DIGESTS[blog_naver.PROMPT_VERSION]


def test_a_naver_package_records_the_digest_of_the_request_it_sent(monkeypatch):
    sheet, calls = _ideate(monkeypatch, [_draft()])
    prompt = sheet["package"]["lineage"]["prompt"]
    assert prompt["request_sha256"] == _sha(calls[0][1]) == _sha(blog_naver.content_request(TARGET))
    assert prompt == {**prompt, "platform": "naver", "prompt_version": blog_naver.PROMPT_VERSION,
                      "platform_profile_version": blog_naver.PROFILE_VERSION,
                      "schema_version": "blog_content_package.v0.3", "revision_request_sha256": None}


# --- which profile -------------------------------------------------------------------------

def test_no_platform_is_naver_and_every_existing_schedule_row_stays_naver(monkeypatch):
    sheet, calls = _ideate(monkeypatch, [_draft()])
    assert sheet["platform"] == "naver" and sheet["package"]["platform"] == "naver"
    assert calls[0][2]["budget_profile"] == "blog_content"
    assert calls[0][1] == blog_naver.content_request(TARGET)


def test_platform_naver_named_explicitly_is_the_same_request(monkeypatch):
    from tests.test_mvp_runtime_blog_structured import _StaticSource, _run_result
    seen = []

    def fake_run(kind, request, **kwargs):
        seen.append(request)
        return _run_result(_draft())

    monkeypatch.setattr(blog_content, "_run", fake_run)
    blog_content.run_content_ideation({"seeds": f"platform=naver, target={TARGET}"}, now="2026-10-05T09:00:00Z",
                                      published_source=_StaticSource())
    assert seen == [blog_naver.content_request(TARGET)]


@pytest.mark.parametrize("seeds", ["platform=wordpress, target=x", "platform=naverr, target=x"])
def test_an_unknown_platform_blocks_before_anything_runs(monkeypatch, seeds):
    calls = []
    monkeypatch.setattr(blog_content, "_run", lambda *a, **k: calls.append(a))
    with pytest.raises(ToolError) as exc:
        blog_content.run_content_ideation({"seeds": seeds}, now="2026-10-05T09:00:00Z")
    assert exc.value.reason_code == blog_platform.BLOG_PLATFORM_UNKNOWN and calls == []


def test_a_blank_platform_is_the_default_not_an_error():
    assert blog_platform.resolve("").name == blog_platform.resolve(None).name == "naver"


def test_two_platforms_in_one_request_are_refused_not_resolved():
    with pytest.raises(ToolError) as exc:
        blog_content.parse_platform("platform=naver, platform=tistory, target=x")
    assert exc.value.reason_code == blog_platform.BLOG_PLATFORM_UNKNOWN


def test_the_platform_token_never_becomes_a_seed():
    platform, rest = blog_content.parse_platform("미리캔버스, platform=Tistory, target=포스터")
    assert platform == "tistory"
    assert blog_content.parse_request(rest) == (["미리캔버스"], "포스터", False)


def test_the_profiles_the_schema_and_the_overlap_policy_name_the_same_platforms():
    schema = json.loads(SCHEMA_V03.read_text(encoding="utf-8"))
    assert set(schema["properties"]["platform"]["enum"]) == set(blog_platform.PROFILES)
    assert set(schema["properties"]["platform_metadata"]["properties"]) == set(blog_platform.PROFILES)
    assert set(blog_overlap._POLICY) == set(blog_platform.PROFILES)


# --- what may not leak --------------------------------------------------------------------

def test_naver_only_rules_do_not_reach_the_tistory_request():
    tistory = blog_tistory.content_request("클로드 무료 사용법")
    for naver_only in ("네이버 블로그", blog_naver.KEY_ORDER_RULE, blog_naver.LAYOUT_RULE,
                       "섹션 5개 중 3개", blog_naver._length_plan(), blog_naver.LENGTH_EXAMPLE_PARAGRAPH[:30]):
        assert naver_only not in tistory, naver_only


def test_tistory_only_rules_do_not_reach_the_naver_request():
    naver = blog_naver.content_request(TARGET)
    for tistory_only in ("티스토리", "seo_title", "slug", "alt_text", "internal_links", "faq", '"brief"',
                         "메타 디스크립션", blog_tistory.BRIEF_RULE[:20]):
        assert tistory_only not in naver, tistory_only


def test_both_requests_carry_the_common_evidence_policy_once():
    for request in (blog_naver.content_request(TARGET), blog_tistory.content_request(TARGET)):
        for rule in (blog_prompt.FACT_CHECK_RULE, blog_prompt.NO_INVENTION_RULE,
                     blog_prompt.EVIDENCE_SPECIFICS_ASK, blog_prompt.SECTION_FOCUS_ASK):
            assert request.count(rule) == 1


def test_a_naver_package_is_not_made_to_carry_tistory_metadata(monkeypatch):
    package = _ideate(monkeypatch, [_draft()])[0]["package"]
    assert package["platform_metadata"] == {"naver": {"paste_format": "plain_text", "editor": "smarteditor_one"}}
    assert all("alt_text" not in shot for shot in package["image_shots"])
    assert package["content_intent"]["declared_intent"] is None


def test_the_schema_refuses_a_package_carrying_the_other_platforms_metadata(monkeypatch):
    from runtime.read_only_kernel.schema_validation import RuntimeSchemaError, validate_against_schema
    from tests.test_mvp_runtime_blog_tistory import tistory_package

    naver = _ideate(monkeypatch, [_draft()])[0]["package"]
    crossed = dict(naver, platform_metadata=tistory_package()["platform_metadata"])
    with pytest.raises(RuntimeSchemaError):
        validate_against_schema(crossed, SCHEMA_V03, "blog_content_package")
    tistory_url_on_naver = dict(naver, publish_state="published", published_url="https://thomasai.tistory.com/1",
                                published_at_utc="2026-10-05T09:00:00Z")
    with pytest.raises(RuntimeSchemaError):
        validate_against_schema(tistory_url_on_naver, SCHEMA_V03, "blog_content_package")


def test_the_naver_quality_gate_is_unchanged_by_the_layers(monkeypatch):
    """The layers are advisory: a draft that clears the standards is `ready_for_review` whatever
    a semantic proxy or a platform check says, and one that misses them is `needs_edit`."""
    sheet, _ = _ideate(monkeypatch, [_draft()])
    quality = sheet["package"]["quality"]
    assert quality["quality_state"] == "ready_for_review" and quality["failures"] == []
    assert set(quality["layers"]) == {"structural", "semantic", "evidence", "platform_fit"}
    checks = {c["check"]: c["state"] for c in quality["layers"]["platform_fit"]["checks"]}
    assert checks["plain_text_paste"] == "ok"


def test_a_tistory_package_is_never_rank_tracked_by_the_naver_tracker():
    from runtime.mvp_runtime import blog_rank
    from tests.test_mvp_runtime_blog_tistory import tistory_package

    published = dict(tistory_package(), publish_state="published",
                     published_url="https://thomasai.tistory.com/80")
    assert not blog_rank.trackable(published)
    assert blog_rank.trackable(dict(published, platform="naver",
                                    published_url="https://blog.naver.com/thomasai/1"))

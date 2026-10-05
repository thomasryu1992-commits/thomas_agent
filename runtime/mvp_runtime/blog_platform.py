"""The content engine's platforms: one profile per platform, resolved by name, fail-closed.

`blog_content` is the engine — research, selection, the governed runs, the one revision, the
package — and it asks the profile for everything that depends on where the post goes: the request
and the revision request, how a model answer is read and measured, the standards it is held to,
the paste file it renders, how a revision's dropped layout is restored, and the platform's own
fit checks. A profile is a frozen record of functions over the adapter modules (`blog_naver`,
`blog_tistory`), not a class hierarchy: there is no shared behaviour to inherit, only a shared
shape to fill.

Adding a platform is a new adapter module and one entry in :data:`PROFILES`; the schema's
`platform` enum and the overlap policy (`blog_overlap._POLICY`) are the other two places that
name platforms, and a test pins that all three agree.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from . import blog_draft_score, blog_naver, blog_tistory, budgets
from .blog_draft_score import Standard
from .errors import ToolError

__all__ = ["BLOG_PLATFORM_UNKNOWN", "DEFAULT_PLATFORM", "PROFILES", "PlatformProfile", "resolve"]

BLOG_PLATFORM_UNKNOWN = "BLOG_PLATFORM_UNKNOWN"
# A request with no `platform=` is a Naver request: every schedule row written before
# 2026-10-05 carries none, and they must keep doing what they did.
DEFAULT_PLATFORM = blog_naver.PLATFORM


@dataclass(frozen=True)
class PlatformProfile:
    name: str
    profile_version: str
    prompt_version: str
    standards_version: str
    standards: Mapping[str, Standard]
    budget_profile: str
    paste_file: str
    paste_format: str
    # Per-member ceiling for the draft chain (`blog_content.draft_provider`).
    draft_member_timeout_seconds: int
    content_request: Callable[..., str]
    revision_request: Callable[..., str]
    interpret: Callable[..., dict[str, Any]]
    carry_layout: Callable[..., None]
    platform_checks: Callable[..., list[dict[str, Any]]]
    # The post's editorial plan from the platform's published posts, or None (Naver has none).
    editorial_plan: Callable[..., dict[str, Any]] | None = None


PROFILES: dict[str, PlatformProfile] = {
    blog_naver.PLATFORM: PlatformProfile(
        name=blog_naver.PLATFORM,
        profile_version=blog_naver.PROFILE_VERSION,
        prompt_version=blog_naver.PROMPT_VERSION,
        standards_version=blog_draft_score.STANDARDS_VERSION,
        standards=blog_draft_score.STANDARDS,
        budget_profile=budgets.BLOG_CONTENT_BUDGET_PROFILE,
        paste_file=blog_naver.PASTE_FILE,
        paste_format="plain_text",
        draft_member_timeout_seconds=120,
        # The Naver request takes no context: its bytes are pinned, and nothing the engine
        # gathers for other platforms (link candidates, a repurpose source) is asked of it.
        content_request=lambda target, context=None: blog_naver.content_request(target),
        revision_request=lambda target, first, text, records=None, context=None:
            blog_naver.revision_request(target, first, text, records),
        interpret=lambda text, target, records=None, context=None:
            blog_naver.interpret(text, target, records),
        carry_layout=blog_naver.carry_layout,
        platform_checks=blog_naver.platform_checks,
    ),
    blog_tistory.PLATFORM: PlatformProfile(
        name=blog_tistory.PLATFORM,
        profile_version=blog_tistory.PROFILE_VERSION,
        prompt_version=blog_tistory.PROMPT_VERSION,
        standards_version=blog_tistory.STANDARDS_VERSION,
        standards=blog_tistory.STANDARDS,
        budget_profile=budgets.BLOG_TISTORY_BUDGET_PROFILE,
        paste_file=blog_tistory.PASTE_FILE,
        paste_format="markdown",
        # About twice the Naver draft's output: 120 s (68 s measured for a Naver-sized draft)
        # would time the first member out; 180 s still leaves the second member the rest of 360.
        draft_member_timeout_seconds=180,
        content_request=blog_tistory.content_request,
        revision_request=blog_tistory.revision_request,
        interpret=blog_tistory.interpret,
        carry_layout=blog_tistory.carry_layout,
        platform_checks=blog_tistory.platform_checks,
        editorial_plan=blog_tistory.editorial_plan,
    ),
}


def resolve(name: str | None) -> PlatformProfile:
    """The profile for ``name`` (None or blank = :data:`DEFAULT_PLATFORM`). An unknown name
    BLOCKs: a misspelt `platform=` must not quietly produce a Naver draft."""
    key = DEFAULT_PLATFORM if name is None or not str(name).strip() else str(name).strip().lower()
    profile = PROFILES.get(key)
    if profile is None:
        raise ToolError(BLOG_PLATFORM_UNKNOWN,
                        f"platform must be one of {sorted(PROFILES)}, not {name!r}")
    return profile

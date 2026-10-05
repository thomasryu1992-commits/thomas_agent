"""Which keyword the blog lane may pick — the correctness half of §J's revival order.

Three weekly fires (2026-09-06..09-20) all ended in `NO_ELIGIBLE_KEYWORD`, and two things in the
selection rule were wrong regardless of the seeds: it read Search Ad's `compIdx` (advertiser bid
competition) as blog difficulty and dropped every 높음 row, and its "already written" list came
from the ledger alone, which holds no package — every post so far was written in the vault. What
is pinned here is that neither survives: ad competition is recorded and gates nothing, and the
set of used keywords is the ledger UNION the published posts, matched the way the vault's own
pipeline matches them.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime import blog_content, blog_overlap
from runtime.mvp_runtime.errors import ToolError
from runtime.mvp_runtime.naver_research import normalize_keyword
from runtime.mvp_runtime.store import LedgerStore

NOW = "2026-09-28T09:00:00Z"


def _row(keyword, total, *, competition="낮음", low=False, posts=None):
    row = {"keyword": keyword, "monthly_pc": total // 4, "monthly_mobile": total - total // 4,
           "monthly_total": total, "competition": competition, "low_volume": low,
           "source": "naver_searchad"}
    if posts is not None:
        row["competing_posts"] = posts
    return row


def _post(root, platform, name, front):
    path = root / "content" / platform / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{front}\n---\n\n# 본문\n", encoding="utf-8")


class _StaticSource:
    def __init__(self, keywords=(), tags=()):
        self._published = blog_content.PublishedKeywords(tuple(keywords), tuple(tags))

    def load(self):
        return self._published


# --- 1. Search Ad compIdx is not a blog gate ------------------------------------------------

def test_an_ad_competition_of_high_alone_does_not_exclude_a_keyword():
    keyword, reasoning = blog_content.select_target_keyword(
        [_row("네이버 블로그 만들기", 4680, competition="높음")])
    assert keyword == "네이버 블로그 만들기"
    assert reasoning["considered"][0]["excluded_because"] is None
    assert reasoning["considered"][0]["ad_competition"] == "높음"


def test_the_09_20_shape_now_yields_a_candidate():
    """Eight 높음 rows and two low-volume rows was every fire's brief. Under the old gate all ten
    were excluded; the highest-demand 높음 row is now the pick."""
    rows = [_row(f"키워드{i}", 1000 * (10 - i), competition="높음") for i in range(8)]
    rows += [_row("작은1", 5, low=True), _row("작은2", 5, low=True)]
    keyword, _ = blog_content.select_target_keyword(rows)
    assert keyword == "키워드0"


def test_the_blog_post_count_is_recorded_under_its_own_name_and_gates_nothing():
    keyword, reasoning = blog_content.select_target_keyword(
        [_row("포스터 만들기", 22000, competition="높음", posts=1_750_000)])
    assert keyword == "포스터 만들기"
    entry = reasoning["considered"][0]
    assert entry["blog_competing_posts"] == 1_750_000
    assert "competition" not in entry           # the ambiguous name is gone from the record


# --- 2/3. already-published keywords, ledger UNION published source -----------------------

def test_a_published_keyword_is_not_picked_again():
    keyword, reasoning = blog_content.select_target_keyword(
        [_row("캡컷 사용법", 3580), _row("클로드 사용법", 2830)],
        already_written=["캡컷 사용법"])
    assert keyword == "클로드 사용법"
    assert reasoning["considered"][0]["excluded_because"] == "already written (캡컷 사용법)"


@pytest.mark.parametrize("candidate,written", [
    ("AI회계", "AI 회계"),                    # Search Ad strips the space
    ("CHATGPT사용법", "chatgpt 사용법"),      # ...and upper-cases Latin letters
    ("추석휴무안내문", "휴무안내문"),          # containment at >= 60% of the longer
    ("상세페이지 AI", "ai 상세페이지"),        # same words, other order (Jaccard 1)
])
def test_near_variants_of_a_written_keyword_count_as_written(candidate, written):
    assert blog_overlap.covering_keyword(candidate, [written]) == written


def test_a_short_containment_is_a_new_topic_not_a_duplicate():
    """'네이버플레이스' inside '네이버플레이스영업시간변경' is under 60% of it — a new post."""
    assert blog_overlap.covering_keyword("네이버플레이스영업시간변경", ["네이버플레이스"]) is None


def test_a_tag_covers_only_its_exact_spelling():
    assert blog_overlap.covering_keyword("구글 노트북lm", [], ["구글노트북LM"]) == "구글노트북LM"
    # A short tag must not swallow every topic that mentions it.
    assert blog_overlap.covering_keyword("인스타그램 광고 만들기", [], ["인스타그램"]) is None


def test_the_normalization_is_whitespace_and_case_blind_and_nfc():
    import unicodedata
    decomposed = unicodedata.normalize("NFD", "AI 회계")
    assert normalize_keyword(decomposed) == normalize_keyword("ai회계") == "ai회계"


def test_written_keywords_is_the_ledger_union_the_published_posts(tmp_path):
    ledger = LedgerStore(tmp_path / "ledger")
    ledger.append_records("t1", {blog_content.PACKAGE_RECORD_KIND: {"target_keyword": "레인에서 쓴 글"}})
    published = blog_content.PublishedKeywords(keywords=("볼트에서 쓴 글",))
    assert blog_content.written_keywords(ledger, published) == ["레인에서 쓴 글", "볼트에서 쓴 글"]
    # Neither half alone.
    assert blog_content.written_keywords(ledger) == ["레인에서 쓴 글"]
    assert blog_content.written_keywords(None, published) == ["볼트에서 쓴 글"]


def test_the_vault_source_reads_keywords_google_kw_and_tags_of_both_platforms(tmp_path):
    _post(tmp_path, "naver", "01편-배민.md",
          'title: "배민 리뷰 답글"\nstatus: published\nkeywords: [배민리뷰, 리뷰답글]\n'
          "tags: [배달의민족, 별점테러]")
    _post(tmp_path, "tistory", "sub/t01.md",
          'title: "x"\nstatus: draft\nkeywords: ["캡컷 사용법"]\ngoogle_kw: capcut 사용법\ntags: []')
    # Not a post folder, and a file without front matter: both ignored.
    _post(tmp_path, "_paste", "README.md", "keywords: [무시]")
    (tmp_path / "content" / "naver" / "notes.md").write_text("# no front matter\n", encoding="utf-8")

    published = blog_content.VaultPublishedKeywordSource(tmp_path).load()
    assert set(published.keywords) == {"배민리뷰", "리뷰답글", "캡컷 사용법", "capcut 사용법"}
    assert set(published.tags) == {"배달의민족", "별점테러"}


@pytest.mark.parametrize("make_root", [
    lambda p: p / "missing",                 # not mounted
    lambda p: p,                             # mounted, but no post under content/
])
def test_an_unusable_vault_fails_closed_instead_of_reading_as_nothing_published(tmp_path, make_root):
    with pytest.raises(ToolError) as exc:
        blog_content.VaultPublishedKeywordSource(make_root(tmp_path)).load()
    assert exc.value.reason_code == blog_content.PUBLISHED_KEYWORD_SOURCE_UNAVAILABLE


def test_rule_based_selection_without_a_published_source_refuses_before_any_research(
        tmp_path, monkeypatch):
    monkeypatch.delenv(blog_content.PUBLISHED_ROOT_ENV, raising=False)
    ran: list[str] = []
    monkeypatch.setattr(blog_content, "_run", lambda kind, *a, **k: ran.append(kind))
    with pytest.raises(ToolError) as exc:
        blog_content.run_content_ideation({"seeds": "포스터"}, now=NOW, repo_root=tmp_path)
    assert exc.value.reason_code == blog_content.PUBLISHED_KEYWORD_SOURCE_UNAVAILABLE
    assert ran == []


def test_the_source_root_comes_from_inputs_or_the_env_never_from_the_code(tmp_path, monkeypatch):
    monkeypatch.delenv(blog_content.PUBLISHED_ROOT_ENV, raising=False)
    assert blog_content.select_published_source({}) is None
    assert blog_content.select_published_source({"published_root": str(tmp_path)}).root == tmp_path
    monkeypatch.setenv(blog_content.PUBLISHED_ROOT_ENV, str(tmp_path / "v"))
    assert blog_content.select_published_source({}).root == tmp_path / "v"


def test_the_published_source_feeds_the_weekly_selection(tmp_path, monkeypatch):
    brief = {"created_at": NOW, "degraded": False, "degraded_legs": {},
             "metrics": [_row("캡컷 사용법", 3580), _row("클로드 사용법", 2830)]}

    def fake_run(kind, request, **kwargs):
        if kind == "research":
            return {"records": {"keyword_research": brief}}
        raise AssertionError("stop after selection")   # the content leg is not under test

    monkeypatch.setattr(blog_content, "_run", fake_run)
    with pytest.raises(AssertionError, match="stop after selection"):
        blog_content.run_content_ideation(
            {"seeds": "캡컷, 클로드"}, now=NOW, repo_root=tmp_path,
            published_source=_StaticSource(["캡컷사용법"]))
    selected, _ = blog_content.select_target_keyword(
        brief["metrics"], already_written=blog_content.written_keywords(
            None, _StaticSource(["캡컷사용법"]).load()))
    assert selected == "클로드 사용법"


# --- 4. no eligible keyword names why ------------------------------------------------------

def test_no_eligible_keyword_says_why_counted_by_reason(tmp_path, monkeypatch):
    brief = {"created_at": NOW, "degraded": False, "degraded_legs": {},
             "metrics": [_row("캡컷 사용법", 3580), _row("작은 키워드", 5, low=True)]}
    monkeypatch.setattr(blog_content, "_run",
                        lambda kind, *a, **k: {"records": {"keyword_research": brief}})
    with pytest.raises(ToolError) as exc:
        blog_content.run_content_ideation(
            {"seeds": "캡컷"}, now=NOW, repo_root=tmp_path,
            published_source=_StaticSource(["캡컷 사용법"]))
    assert exc.value.reason_code == blog_content.NO_ELIGIBLE_KEYWORD
    assert "already written: 1" in exc.value.reason
    assert "volume below the venue's reporting floor: 1" in exc.value.reason


def test_an_empty_brief_is_named_as_such(tmp_path, monkeypatch):
    monkeypatch.setattr(blog_content, "_run", lambda kind, *a, **k: {
        "records": {"keyword_research": {"created_at": NOW, "degraded": True,
                                         "degraded_legs": {"volume": "TOOL_ERROR"}, "metrics": []}}})
    with pytest.raises(ToolError) as exc:
        blog_content.run_content_ideation({"seeds": "x"}, now=NOW, repo_root=tmp_path,
                                          published_source=_StaticSource())
    assert "no rows" in exc.value.reason

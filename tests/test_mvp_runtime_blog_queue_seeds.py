"""`source=queue`: the blog lane's seeds come from the vault keyword pipeline's queue.

All three weekly fires (2026-09-06..09-20) failed on the same fixed seeds. The vault already
rebuilds a ranked, reach-checked candidate list every week (`kw_pipeline run` -> queue.md), so
the lane reads that instead. Pinned: only the "다음 편 후보" sections are candidates; written
ones are skipped; at most one Search Ad call's worth of seeds; the choice stays inside the
queue (a related head term the brief surfaces never passed the reach check) and follows the
queue's score once the brief confirms demand; a missing or stale queue refuses.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime import blog_content
from runtime.mvp_runtime.errors import ToolError
from runtime.mvp_runtime.paths import repo_root
from runtime.read_only_kernel.schema_validation import validate_against_schema

NOW = "2026-09-28T09:00:00Z"
SCHEMA = repo_root() / "schemas" / "blog_content_package.v0.3.schema.json"

QUEUE = """# 키워드 큐 — 2026-09-27

> `tools/kw_pipeline.py run` 이 만든 파일이다.

## 시즌 임박 — 정점 1~3주 전 발행이 목표

- **부가세 예정신고·고지(2기)** — D-25 · 보유 글 25편-부가세-예정신고.md

## AI 활용 — 다음 편 후보

1. **네이버 블로그 만들기** — 점수 121.7 · 월 4,680회 · 경쟁 97/6/5 · 도달 0.52
2. **캡컷 사용법** — 점수 91.3 · 월 3,580회 · 경쟁 59/8/6 · 도달 0.51
3. **클로드 사용법** — 점수 75.0 · 월 2,830회 · 경쟁 90/6/7 · 도달 0.53

## 가게 마케팅 — 다음 편 후보

1. **명함제작** — 점수 1,323.7 · 월 43,400회 · 경쟁 39/7/8 · 도달 0.61
2. **스티커제작** — 점수 748.2 · 월 36,500회 · 경쟁 169/7/6 · 도달 0.41

## 실무 가이드 — 다음 편 후보

1. **카드뉴스 만들기** — 점수 57.8 · 월 1,540회 · 경쟁 64/2/5 · 도달 0.75
2. **소량명함제작** — 점수 123.8 · 월 2,750회 · 경쟁 39/1/4 · 도달 0.9

## 티스토리 전용 — 구글에 블로그 자리가 있는 AI 도구 의도 (변환을 건너뛴 슬롯용)

> 도구명 + 요금제·무료 한도·해지·환불·오류·비교.

- 자리 있음: 퍼플렉시티 무료(660)
- 측정 대기: 챗gpt 유료 가격(14,750), 캡컷 유료(5,210), 제미나이 해지(1,680), 클로드 환불(1,410) …
- 자리 없음(쓰지 않는다): 캔바 사용법(3,000)

## 참고: 지금 체급 밖 — 검색량은 크지만 상위가 굳어 있다

1. **CHATGPT** — 점수 900.0 · 월 2,310,000회 · 경쟁 900/10/9 · 도달 0.01

## 보유 키워드의 변형 — 새 글이 아니라 후속편·보강·역링크 후보

1. **부가세 예정고지 기간** — 점수 50.0 · 월 900회 · 경쟁 1/1/1 · 도달 0.9
"""


def _vault(tmp_path, queue=QUEUE):
    path = tmp_path / blog_content.QUEUE_REL
    path.parent.mkdir(parents=True)
    path.write_text(queue, encoding="utf-8")
    return blog_content.VaultKeywordQueue(tmp_path)


def _row(keyword, total, *, low=False, competition="높음"):
    return {"keyword": keyword, "monthly_pc": total // 4, "monthly_mobile": total - total // 4,
            "monthly_total": total, "competition": competition, "low_volume": low,
            "source": "naver_searchad"}


class _Source:
    def __init__(self, keywords=(), root=""):
        self.root = root
        self._published = blog_content.PublishedKeywords(tuple(keywords))

    def load(self):
        return self._published


# --- reading the queue -----------------------------------------------------------------------

def test_only_the_next_post_sections_are_candidates(tmp_path):
    queue = _vault(tmp_path).load(NOW)
    assert queue.as_of == "2026-09-27"
    keywords = [c.keyword for c in queue.candidates]
    assert keywords == ["네이버 블로그 만들기", "캡컷 사용법", "클로드 사용법", "명함제작",
                        "스티커제작", "카드뉴스 만들기", "소량명함제작"]
    # Out-of-league rejects, variants of written posts, and season notes are not seeds.
    assert "CHATGPT" not in keywords and "부가세 예정고지 기간" not in keywords
    assert {c.lane for c in queue.candidates} == {"AI 활용", "가게 마케팅", "실무 가이드"}
    assert queue.candidates[3].score == 1323.7          # thousands separator parsed


@pytest.mark.parametrize("content,code", [
    (None, blog_content.KEYWORD_QUEUE_UNAVAILABLE),                         # no file
    ("# 다른 문서\n\n1. **x** — 점수 1", blog_content.KEYWORD_QUEUE_UNAVAILABLE),  # not a queue
    ("# 키워드 큐 — 2026-09-27\n\n## 참고\n", blog_content.KEYWORD_QUEUE_UNAVAILABLE),  # no candidates
    (QUEUE.replace("2026-09-27", "2026-09-10"), blog_content.KEYWORD_QUEUE_STALE),  # 18 days
])
def test_an_unusable_queue_fails_closed(tmp_path, content, code):
    source = blog_content.VaultKeywordQueue(tmp_path) if content is None else _vault(tmp_path, content)
    with pytest.raises(ToolError) as exc:
        source.load(NOW)
    assert exc.value.reason_code == code


def test_seeds_are_the_top_scored_unwritten_candidates_one_calls_worth(tmp_path):
    queue = _vault(tmp_path).load(NOW)
    seeds, skipped = blog_content.queue_seeds(queue, already_written=["명함 제작"])
    assert [c.keyword for c in seeds] == [
        "스티커제작", "네이버 블로그 만들기", "캡컷 사용법", "클로드 사용법", "카드뉴스 만들기"]
    assert len(seeds) == blog_content.MAX_QUEUE_SEEDS == 5
    # Written as '명함 제작' — the same keyword, and '소량명함제작' contains it at 4/6 >= 60%,
    # which is the vault pipeline's own rule for "already covered".
    assert skipped == ["명함제작", "소량명함제작"]


# --- choosing inside the queue ---------------------------------------------------------------

def _queue_seeds(tmp_path):
    return blog_content.queue_seeds(_vault(tmp_path).load(NOW))[0]


def test_a_related_head_term_is_not_eligible_even_with_more_demand(tmp_path):
    seeds = _queue_seeds(tmp_path)
    rows = [_row("명함", 500_000), _row("명함제작", 43_400), _row("스티커제작", 36_500)]
    keyword, reasoning = blog_content.select_target_keyword(rows, queue=seeds)
    assert keyword == "명함제작"
    excluded = {c["keyword"]: c["excluded_because"] for c in reasoning["considered"]}
    assert excluded["명함"].startswith("not a queue candidate")


def test_the_queue_score_orders_and_the_brief_must_confirm_demand(tmp_path):
    seeds = _queue_seeds(tmp_path)
    rows = [_row("명함제작", 5, low=True),                   # top score, but no measured demand
            _row("스티커제작", 36_500),
            _row("네이버블로그만들기", 90_000)]              # more demand, lower queue score
    keyword, reasoning = blog_content.select_target_keyword(rows, queue=seeds)
    assert keyword == "스티커제작"
    assert "queue" in reasoning["rule"]


def test_the_chosen_keyword_keeps_the_queues_spelling(tmp_path):
    seeds = _queue_seeds(tmp_path)
    keyword, _ = blog_content.select_target_keyword(
        [_row("네이버블로그만들기", 4680)], queue=seeds)
    assert keyword == "네이버 블로그 만들기"


def test_a_queue_seed_with_no_row_in_the_brief_is_recorded_not_silently_dropped(tmp_path):
    seeds = _queue_seeds(tmp_path)
    _k, reasoning = blog_content.select_target_keyword([_row("명함제작", 43_400)], queue=seeds)
    missing = {c["keyword"]: c["excluded_because"] for c in reasoning["considered"]}
    assert missing["캡컷 사용법"] == "no row for it in this brief"


# --- the request grammar and the fire ----------------------------------------------------------

def test_the_request_grammar(tmp_path):
    assert blog_content.parse_request("source=queue") == ([], None, True)
    assert blog_content.parse_request("source=queue, target=명함") == ([], "명함", True)
    assert blog_content.parse_request("미리캔버스, 포스터") == (["미리캔버스", "포스터"], None, False)


def test_fixed_seeds_and_the_queue_together_are_refused(tmp_path):
    with pytest.raises(ToolError) as exc:
        blog_content.run_content_ideation({"seeds": "source=queue, 포스터"}, now=NOW,
                                          published_source=_Source())
    assert exc.value.reason_code == blog_content.IDEATION_INPUTS_CONFLICT


def test_a_queue_fire_researches_the_queue_seeds_and_records_where_they_came_from(
        tmp_path, monkeypatch):
    calls: list[tuple[str, dict]] = []

    def fake_run(kind, request, *, blocked_code, **kwargs):
        calls.append((kind, kwargs))
        if kind == "research":
            return {"records": {"task": {"identity": {"trace_id": "t-sel"}},
                                "keyword_research": {
                "created_at": NOW, "degraded": False, "degraded_legs": {},
                "metrics": [_row("명함", 500_000), _row("스티커제작", 36_500),
                            _row("소량명함제작", 2_750)]}}}
        return {"records": {"task": {"identity": {"trace_id": f"t-{len(calls)}"}},
                            "agent_output": {"role_specific_output": {"content_draft": "산문."}}}}

    monkeypatch.setattr(blog_content, "_run", fake_run)
    sheet = blog_content.run_content_ideation(
        {"seeds": "source=queue"}, now=NOW,
        published_source=_Source(["명함제작"]), keyword_queue=_vault(tmp_path))
    assert calls[0][1]["keyword_seeds"] == (
        "스티커제작, 네이버 블로그 만들기, 캡컷 사용법, 클로드 사용법, 카드뉴스 만들기")
    assert sheet["target_keyword"] == "스티커제작"
    selection = sheet["package"]["selection_evidence"]
    assert selection["seed_source"] == {"kind": "vault_queue", "as_of": "2026-09-27",
                                        "candidates_read": 7, "skipped_written": 2,
                                        "section": "다음 편 후보"}
    assert selection["seeds"][0] == "스티커제작"
    validate_against_schema(sheet["package"], SCHEMA, "blog_content_package")


def test_an_exhausted_queue_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(blog_content, "_run", lambda *a, **k: pytest.fail("no research"))
    written = [c.keyword for c in _vault(tmp_path).load(NOW).candidates]
    with pytest.raises(ToolError) as exc:
        blog_content.run_content_ideation(
            {"seeds": "source=queue"}, now=NOW, published_source=_Source(written),
            keyword_queue=blog_content.VaultKeywordQueue(tmp_path))
    assert exc.value.reason_code == blog_content.NO_ELIGIBLE_KEYWORD
    assert "exhausted" in exc.value.reason


def test_the_queue_is_read_from_the_published_sources_root_by_default(tmp_path, monkeypatch):
    """One vault root for both — the mount lands both folders under /app/blog_vault."""
    _vault(tmp_path, QUEUE.replace("2026-09-27", "2026-01-01"))
    monkeypatch.setattr(blog_content, "_run", lambda *a, **k: pytest.fail("no research"))
    with pytest.raises(ToolError) as exc:
        blog_content.run_content_ideation({"seeds": "source=queue"}, now=NOW,
                                          published_source=_Source(root=tmp_path))
    assert exc.value.reason_code == blog_content.KEYWORD_QUEUE_STALE


# --- the Tistory section ---------------------------------------------------------------------

def test_a_tistory_queue_reads_only_the_tistory_section_and_never_a_no_slot_line(tmp_path):
    """The first Tistory row read Naver's '다음 편 후보' and would have picked '개인사업자정책자금' —
    a 실무 keyword Google has no blog slot for. Tistory reads its own section; "자리 없음" is out."""
    _vault(tmp_path)
    queue = blog_content.VaultKeywordQueue(tmp_path, platform="tistory").load(NOW)
    assert [(c.keyword, c.score) for c in queue.candidates] == [
        ("퍼플렉시티 무료", 660.0), ("챗gpt 유료 가격", 14750.0), ("캡컷 유료", 5210.0),
        ("제미나이 해지", 1680.0), ("클로드 환불", 1410.0)]
    assert {c.lane for c in queue.candidates} == {"티스토리 전용"}
    # Naver still reads what it always read.
    naver = blog_content.VaultKeywordQueue(tmp_path).load(NOW)
    assert "캔바 사용법" not in {c.keyword for c in naver.candidates} | {c.keyword for c in queue.candidates}
    assert "명함제작" in {c.keyword for c in naver.candidates}


def test_a_queue_without_a_tistory_section_fails_closed_for_tistory(tmp_path):
    _vault(tmp_path, QUEUE.split("## 티스토리 전용")[0])
    with pytest.raises(ToolError) as exc:
        blog_content.VaultKeywordQueue(tmp_path, platform="tistory").load(NOW)
    assert exc.value.reason_code == blog_content.KEYWORD_QUEUE_UNAVAILABLE


def test_a_tistory_queue_fire_seeds_from_its_section_and_records_it(tmp_path, monkeypatch):
    from tests.test_mvp_runtime_blog_tistory import _result, _tdraft

    _vault(tmp_path)
    calls = []

    def fake_run(kind, request, **kwargs):
        calls.append((kind, kwargs))
        if kind == "research":
            rows = [{"keyword": k.replace(" ", ""), "monthly_pc": 1, "monthly_mobile": 9, "monthly_total": v,
                     "competition": "낮음", "low_volume": False, "source": "naver_searchad"}
                    for k, v in (("챗gpt 유료 가격", 900), ("캡컷 유료", 400), ("챗gpt", 2000000))]
            return {"status": "COMPLETED", "records": {"task": {"identity": {"trace_id": "trace-sel"}},
                    "keyword_research": {"created_at": NOW, "degraded": False, "metrics": rows}}}
        return _result(_tdraft(), trace="trace-c")

    monkeypatch.setattr(blog_content, "_run", fake_run)
    sheet = blog_content.run_content_ideation(
        {"seeds": "platform=tistory, source=queue"}, now=NOW,
        published_source=_Source(root=tmp_path))
    research = calls[0][1]["keyword_seeds"].split(", ")
    assert research == ["챗gpt 유료 가격", "캡컷 유료", "제미나이 해지", "클로드 환불", "퍼플렉시티 무료"]
    # The head term the brief surfaced is not a queue candidate, whatever its demand.
    assert sheet["target_keyword"] == "챗gpt 유료 가격"
    seed_source = sheet["package"]["selection_evidence"]["seed_source"]
    assert seed_source["section"] == "티스토리 전용" and seed_source["kind"] == "vault_queue"
    validate_against_schema(sheet["package"], SCHEMA, "blog_content_package")

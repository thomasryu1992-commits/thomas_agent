"""The blog department's analysis (BLOG_DEPARTMENT v0.2 P3): the system reads the week's statistics the Mac collected
into the vault, records them, and records beside them whether the weekly review's README row says the same.

The fixtures are the real 2026-10-11, 10-18 and 10-25 weeks, trimmed: on 10-25 every number agrees; on 10-18 the
browser collection never ran, so the Tistory and Search Console numbers are absent on both sides; on 10-11 the row
predates the search-inflow formula of 10-12, and that difference is recorded, not smoothed over."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from runtime.mvp_runtime import blog_department as bd
from runtime.mvp_runtime import management_report as mr
from runtime.mvp_runtime.blog import analysis as ba
from runtime.mvp_runtime.control import ControlStore
from runtime.mvp_runtime.scheduler import (
    KIND_BLOG_ANALYZE, KINDS, MAINTENANCE_KINDS, ScheduleStore, build_schedule, run_due,
)
from runtime.mvp_runtime.store import LEDGER_REL, LedgerStore

NOW = "2026-10-27T05:00:00Z"          # 14:00 KST on Tuesday 10-27

README = """# 블로그 주간 분석

| 주차 | 네이버 편수 | 네이버 조회수 | 티스토리 편수 | 구글 노출 | 구글 클릭 | 이번 주 결정 |
|---|---|---|---|---|---|---|
| [10-05~10-11](2026-10-11.md) | 14편/주 (지난 7일 17편) | **1,445** 채널 (전주 951 → +52%, 검색 83.9%·홈판 11.2%) | 78 (주 15편, 조회 143·검색 74=다음 70) | 346 (10-03~09, 색인 101) | 7 | 결정 |
| [10-12~10-18](2026-10-18.md) | 발행 21 / 계획 21 | **1,770** 채널 (전주 1,445 → +22%, 검색 85.8% = 1,456·홈판 9.0%) | 99 (발행 21 / 계획 21, 조회 미확인) | 미확인 — 브라우저 미승인 | 미확인 | 결정 |
| [10-19~10-25](2026-10-25.md) | 발행 21 / 계획 21 | **2,063** 채널 (전주 1,770 → +17%, 검색 89.2% = 1,758·홈판 6.0%) | 119 (발행 20 / 계획 21, 조회 181·검색 109=다음 86·구글 22) | 726 (10-17~23, 색인 183) | 18 | 결정 |
"""

TITLE = "개인사업자등록증 발급, 처음 신청은 2일이고 상호 정정은 당일입니다"


def _naver(views, prev, inflow, search, share, home):
    return {"collected": "2026-10-26T10:03", "missing": [], "summary": {
        "조회수": {"이번주": views, "지난주": prev}, "순방문자": {"이번주": views - 185, "지난주": 0},
        "유입수": inflow, "검색유입": search, "공식검색유입률": share, "홈판": home,
        "글별": [{"순위": 1, "제목": TITLE, "조회": 111, "편": "90편-사업자등록증-발급",
                 "url": "http://blog.naver.com/thomasai/224437083412", "발행": "2026-10-13"},
                {"순위": 2, "제목": "네이버 예약 만들기", "조회": 102, "편": "59편-네이버-예약-만들기",
                 "url": "http://blog.naver.com/thomasai/224424939357", "발행": "2026-10-03"},
                {"순위": 3, "제목": "옛 글", "조회": 4, "편": None, "url": "http://blog.naver.com/thomasai/1"}],
        "검색어": [{"검색어": "클로바노트 시간 추가", "비율": 1.17, "건수": 23}],
    }, "raw": {"integrated-analysis/view-count": {"url": "https://creator-advisor.naver.com/api/v6/x?channelId=thomasai"}}}


BROWSER = {
    "week_end": "2026-10-25",
    "tistory": {"inflow": {"timestamp": "2026-10-19T00:00:00+09:00", "result": {
        "count": 181, "searchEngine": {"total": 109, "list": {"daum": 86, "google": 22, "bing": 1}},
        "sns": {"total": 9, "list": {"kakaotalk": 9}}, "ref": {"total": 63, "list": {"ref": 32, "refEtc": 31}}}},
        "keyword": [["간이과세자 세금계산서 발행", 20]]},
    "gsc": {"range": ["2026-10-17", "2026-10-23"],
            "totals": {"Total clicks": "18", "Total impressions": "726", "Average CTR": "2.5%", "Average position": "10.2"},
            "queries": [["미리캔버스 환불", "1", "40", "8.1"]]},
    "gsc_pages": {"range": ["2026-09-26", "2026-10-23"],
                  "rows": [["/81", 6, 50, 4.5], ["/5", 4, 149, 8.8], ["/m/12", 0, 3, 30.0], ["/category/x", 1, 1, 1.0]]},
    "index": {"indexed": 183, "not_indexed": 54, "reasons": {}},
    "collected_at": "2026-10-26 11:45 KST", "skipped": [],
}

CAPTURE = ("# 주간 검색 대비 유입 — 형식: 주말일\t검색어\t도착 글\t주간 유입\t월 검색량\t비율\t구분\n"
           "2026-10-18\t다른 주\t44\t9\t100\t0.5\t답형\n"
           "2026-10-25\t주휴수당뜻\t30\t27\t8580\t0.0137\t답형\n"
           "2026-10-25\t노트북 LM 활용 방법\t44\t12\t1960\t0.0266\t답형\n"
           "2026-10-25\t가격 인상 안내 문구\t08\t8\t70\t0.4963\t답형\n"
           "2026-10-25\t노트북lm\t44\t29\t67700\t0.0019\t이름만\n"
           "2026-10-25\t클로드\t85\t40\t3440\t0.05\t이름만\n"
           "2026-10-25\t긴 구절\t59\t5\t10\t\t답형\n")


@pytest.fixture
def vault(tmp_path):
    for p in ("content/naver", "content/tistory", "ops/blog-runs", "analytics/naver-weekly",
              "analytics/weekly-browser", "analytics/keywords"):
        (tmp_path / p).mkdir(parents=True)
    (tmp_path / "content" / "naver" / "1.md").write_text("---\nstatus: draft\n---\n", encoding="utf-8")
    (tmp_path / "content" / "tistory" / "1.md").write_text("---\nstatus: draft\n---\n", encoding="utf-8")
    _put(tmp_path, "naver-weekly/2026-10-11.json", _naver(1445, 951, 1379, 1142, 82.81, 154))
    _put(tmp_path, "naver-weekly/2026-10-18.json", _naver(1770, 1445, 1697, 1456, 85.8, 153))
    _put(tmp_path, "naver-weekly/2026-10-25.json", _naver(2063, 1770, 1970, 1758, 89.24, 119))
    _put(tmp_path, "weekly-browser/2026-10-25.json", BROWSER)
    (tmp_path / "analytics" / "keywords" / "capture.tsv").write_text(CAPTURE, encoding="utf-8")
    (tmp_path / "analytics" / "README.md").write_text(README, encoding="utf-8")
    return tmp_path


def _put(vault: Path, rel: str, data) -> None:
    (vault / "analytics" / rel).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _by_field(parts):
    return {row["field"]: row for row in parts["comparison"]}


def test_a_full_week_reads_the_numbers_the_weekly_review_wrote(vault):
    parts = ba.analyse_week(vault, "2026-10-25")
    rows = _by_field(parts)
    assert {f: r["system"] for f, r in rows.items()} == {
        "naver_views": 2063, "naver_search_inflow": 1758, "naver_search_share_pct": 89.2, "naver_home_share_pct": 6.0,
        "tistory_views": 181, "tistory_search": 109, "tistory_daum": 86, "tistory_google": 22,
        "gsc_impressions": 726, "gsc_indexed": 183, "gsc_clicks": 18}
    assert all(r["agree"] is True for r in rows.values()), [r for r in rows.values() if not r["agree"]]
    assert parts["naver"]["posts"] == [{"post": 90, "views": 111}, {"post": 59, "views": 102}]
    assert parts["naver"]["posts_unmapped"] == 1
    assert [p["page"] for p in parts["browser"]["gsc_pages"]] == [81, 5, 12]
    assert parts["browser"]["gsc"] == {"start": "2026-10-17", "end": "2026-10-23", "clicks": 18, "impressions": 726,
                                       "ctr_pct": 2.5, "position": 10.2}


def test_a_collection_that_never_ran_is_absent_on_both_sides_not_a_disagreement(vault):
    rows = _by_field(ba.analyse_week(vault, "2026-10-18"))
    assert [f for f, r in rows.items() if r["agree"] is True] == [
        "naver_views", "naver_search_inflow", "naver_search_share_pct", "naver_home_share_pct"]
    assert all(r["agree"] is None and r["system"] is None and r["skill"] is None
               for f, r in rows.items() if not f.startswith("naver_"))


def test_a_real_difference_is_recorded_as_one(vault):
    """10-11's row was written before the 10-12 formula: its search share is 83.9 where the official rate is 82.8."""
    rows = _by_field(ba.analyse_week(vault, "2026-10-11"))
    assert rows["naver_search_share_pct"] == {"field": "naver_search_share_pct", "system": 82.8, "skill": 83.9,
                                              "agree": False}
    assert rows["naver_search_inflow"]["skill"] is None and rows["naver_search_inflow"]["agree"] is None
    assert rows["tistory_views"] == {"field": "tistory_views", "system": None, "skill": 143.0, "agree": None}


def test_capture_rows_are_summarised_per_query_class_with_the_reviews_median(vault):
    capture = ba.analyse_week(vault, "2026-10-25")["capture"]
    assert capture["queries"] == 6
    assert capture["classes"]["답형"] == {"queries": 3, "median": 0.0266}      # the middle of three
    assert capture["classes"]["이름만"] == {"queries": 2, "median": 0.05}      # the upper middle of two, as the review
    assert capture["classes"]["이동형"] == {"queries": 0, "median": None}
    assert (capture["unsized"], capture["unsized_inflow"]) == (1, 5.0)


def test_a_cell_in_another_shape_is_none_for_that_number_never_a_crash(vault):
    broken = json.loads(json.dumps(BROWSER))
    broken["gsc"]["totals"] = {"Total clicks": "—", "Total impressions": "1.2K"}
    broken["tistory"]["inflow"] = {}
    _put(vault, "weekly-browser/2026-10-25.json", broken)
    parts = ba.analyse_week(vault, "2026-10-25")
    assert parts["browser"]["tistory"] is None
    assert parts["browser"]["gsc"]["clicks"] is None and parts["browser"]["gsc"]["impressions"] is None
    assert _by_field(parts)["gsc_clicks"]["agree"] is None
    ba.build_record("2026-10-25", parts, now=NOW)        # still a valid record


def test_the_record_carries_no_title_and_no_url(vault, tmp_path):
    record = ba.build_record("2026-10-25", ba.analyse_week(vault, "2026-10-25"), now=NOW)
    text = json.dumps(record, ensure_ascii=False)
    assert "http" not in text and TITLE not in text and "content/" not in text and "thomasai" not in text
    assert record["week"] == {"monday": "2026-10-19", "sunday": "2026-10-25"}
    assert record["summary"] == {"skill_row": True, "compared": 11, "agreed": 11, "differed": []}


def test_the_daily_fire_records_each_week_once_and_again_when_a_late_file_changes_it(vault, tmp_path):
    ledger = LedgerStore(tmp_path / "state" / LEDGER_REL)
    (vault / "analytics" / "weekly-browser" / "2026-10-25.json").unlink()
    assert ba.fire(ledger, now=NOW, vault_root=vault) == "blog_analysed=2026-10-18(4/4),2026-10-25(4/4)"
    assert ba.fire(ledger, now=NOW, vault_root=vault) == "blog_analysis_nothing_new"
    _put(vault, "weekly-browser/2026-10-25.json", BROWSER)              # the browser run redone at noon
    assert ba.fire(ledger, now=NOW, vault_root=vault) == "blog_analysed=2026-10-25(11/11)"
    readme = vault / "analytics" / "README.md"
    readme.write_text(readme.read_text(encoding="utf-8").replace("| 결정 |", "| 결정을 고쳐 씀 |"), encoding="utf-8")
    assert ba.fire(ledger, now=NOW, vault_root=vault) == "blog_analysis_nothing_new"   # wording is not a number
    weeks = [r["record"]["week"]["sunday"] for r in ledger.iter_records(kinds=[ba.RECORD_KIND])
             if r["kind"] == ba.RECORD_KIND]
    assert weeks == ["2026-10-18", "2026-10-25", "2026-10-25"]          # 10-11 is past the two-week lookback


def test_only_finished_weeks_named_by_a_sunday_are_read(vault, tmp_path):
    ledger = LedgerStore(tmp_path / "state" / LEDGER_REL)
    _put(vault, "naver-weekly/2026-10-24.json", _naver(1, 1, 1, 1, 1.0, 1))      # a Saturday
    _put(vault, "naver-weekly/2026-11-01.json", _naver(1, 1, 1, 1, 1.0, 1))      # not over yet
    (vault / "analytics" / "naver-weekly" / "run.log").write_text("exit 0\n", encoding="utf-8")
    assert ba.fire(ledger, now="2026-11-01T05:00:00Z", vault_root=vault) == \
        "blog_analysed=2026-10-18(4/4),2026-10-25(11/11)"


def test_the_kind_is_maintenance_and_fires_through_the_scheduler(vault, tmp_path, monkeypatch):
    assert KIND_BLOG_ANALYZE in KINDS and KIND_BLOG_ANALYZE in MAINTENANCE_KINDS
    monkeypatch.setenv(bd.BLOG_VAULT_ROOT_ENV, str(vault))
    state = tmp_path / "state"
    store = ScheduleStore(state)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.add(build_schedule(kind=KIND_BLOG_ANALYZE, request="", interval_seconds=86400, created_by="op",
                             now="2026-10-26T05:00:00Z"))
    ledger = LedgerStore(state / LEDGER_REL)
    run_due(store, now=NOW, control_store=ControlStore(state), ledger=ledger, repo_root=state)
    terminal = [e for e in ledger.read_scheduler_events() if e["action"] in ("fired", "failed")][-1]
    assert terminal["action"] == "fired" and terminal["status"].startswith("blog_analysed=")


def test_the_weekly_report_shows_the_latest_week_and_the_agreement_streak(vault, tmp_path):
    ledger = LedgerStore(tmp_path / "state" / LEDGER_REL)
    assert bd.analysis_summary(ledger, now=NOW) is None
    ba.fire(ledger, now=NOW, vault_root=vault)
    summary = bd.analysis_summary(ledger, now=NOW)
    assert summary["week"] == {"monday": "2026-10-19", "sunday": "2026-10-25"}
    assert (summary["compared"], summary["agreed"], summary["weeks_agreeing_in_a_row"]) == (11, 11, 2)
    assert summary["capture_median"] == {"답형": 0.0266, "이름만": 0.05, "이동형": None}
    line = mr._analysis_line(summary)
    assert "[주간 통계] 10-19~10-25 · 네이버 조회 2,063(검색 1,758) · 티스토리 181 · 구글 노출 726·클릭 18" in line
    assert "포착률 중앙값 답형 2.7% · 이름만 5.0% · 이동형 —" in line
    assert "주간 리뷰와 일치 11/11 · 연속 일치 2주" in line
    assert mr._analysis_line(None) == "  [주간 통계] 기록된 주가 없다"


def test_a_cell_marked_unconfirmed_gives_no_number_even_when_it_quotes_one(vault):
    readme = vault / "analytics" / "README.md"
    readme.write_text(readme.read_text(encoding="utf-8").replace(
        "**1,770** 채널 (전주 1,445 → +22%, 검색 85.8% = 1,456·홈판 9.0%)", "미확인 (스크린샷 대기, 지난주 1,445)"),
        encoding="utf-8")
    rows = _by_field(ba.analyse_week(vault, "2026-10-18"))
    assert all(rows[f]["skill"] is None for f in rows)


def test_a_week_that_differs_breaks_the_agreement_streak(vault, tmp_path):
    readme = vault / "analytics" / "README.md"
    readme.write_text(readme.read_text(encoding="utf-8").replace("**2,063**", "**2,064**"), encoding="utf-8")
    ledger = LedgerStore(tmp_path / "state" / LEDGER_REL)
    ba.fire(ledger, now=NOW, vault_root=vault)
    summary = bd.analysis_summary(ledger, now=NOW)
    assert (summary["agreed"], summary["differed"], summary["weeks_agreeing_in_a_row"]) == (10, ["naver_views"], 0)
    assert "주간 리뷰와 일치 10/11(다름: naver_views) · 연속 일치 0주" in mr._analysis_line(summary)

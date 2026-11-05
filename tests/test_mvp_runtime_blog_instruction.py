"""The blog department's instruction shadow (BLOG_DEPARTMENT v0.2 P6, built ahead, Thomas 2026-11-03): the
instruction the system would hand the drafting cron, built in the morning gap, and where each reported draft sits in it.

The fixture vault holds one published Naver post (``보유 키워드``), the weekly review's follow-ups (one open, one closed),
a season calendar, and the production history of 2026-11-04, the KST date the keyword run names its file by (it runs
at 2026-11-03T22:10Z). The history's candidates:

* ``가 키워드`` (cluster ``aa``) and ``나 키워드`` (``bb``) in 실무 가이드, ``나`` scoring higher;
* ``시즌 키워드``, which the calendar puts ten days before its peak;
* ``보유 키워드`` (the post covers it) and ``갱신 키워드`` (an update, not a new post), both left out;
* ``바 키워드`` in AI 활용.

The drafting cron starts 2026-11-03T23:02Z (08:02 KST on 11-04)."""

from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

from runtime.mvp_runtime import blog_department as bd
from runtime.mvp_runtime.blog import instruction as bi
from runtime.mvp_runtime.control import ControlStore
from runtime.mvp_runtime.errors import ToolError
from runtime.mvp_runtime.scheduler import (
    KIND_BLOG_INSTRUCTION, KINDS, MAINTENANCE_KINDS, ScheduleStore, build_schedule, run_due,
)
from runtime.mvp_runtime.store import LEDGER_REL, RECORD_KINDS, LedgerStore

RUN_DATE = "2026-11-04"
EARLY, GAP, LATE, AFTER = ("2026-11-03T22:00:00Z", "2026-11-03T22:40:00Z", "2026-11-03T22:55:00Z",
                           "2026-11-03T23:10:00Z")
STARTED = "2026-11-03T23:02:01Z"


def _cand(kw: str, lane: str, score: float, cluster: str = "unresolved", action: str = "create_new") -> dict:
    return {"kw": kw, "lane": lane, "score": score, "decision": {"action": action, "cluster": cluster}}


HISTORY = {
    "date": "2026-11-04", "has_vol": True,
    "candidates": [
        _cand("가 키워드", "실무 가이드", 10.0, "aa"),
        _cand("나 키워드", "실무 가이드", 30.0, "bb"),
        _cand("시즌 키워드", "실무 가이드", 5.0),
        _cand("보유 키워드", "실무 가이드", 50.0),
        _cand("갱신 키워드", "실무 가이드", 60.0, action="update_published"),
        _cand("바 키워드", "AI 활용", 40.0),
    ],
    "tistory": [], "ranks": [],
}
TISTORY = ["티스토리 첫째", "티스토리 둘째"]


def _history(root: Path, day: str, data: dict = HISTORY) -> None:
    (root / "analytics" / "keywords" / "history" / f"{day}.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def vault(tmp_path, monkeypatch):
    root = tmp_path / "vault"
    for p in ("content/naver", "content/tistory", "analytics/keywords/history", "ops/blog-runs"):
        (root / p).mkdir(parents=True)
    (root / "content" / "naver" / "01편-보유.md").write_text(
        "---\nstatus: published\nkeywords: [보유 키워드]\n---\n본문\n", encoding="utf-8")
    kw = root / "analytics" / "keywords"
    (kw / "next_topics.tsv").write_text(
        "date\tkw\twhy\tlane\tclosed\n2026-11-01\t다음 주제\t주간 분석\t실무 가이드\t\n"
        "2026-11-01\t닫은 주제\t주간 분석\t실무 가이드\t접음\n", encoding="utf-8")
    (kw / "calendar.tsv").write_text("11-14\t3\t시즌\t연말 마감\n", encoding="utf-8")
    _history(root, "2026-11-04")
    # The Tistory ranking reads the taxonomy and the Tistory corpus; its own order is the vault tool's business and is
    # pinned by the port's tests. Here it is the list the cron would read.
    real_port = bi._port

    def port(root_, clock, tmp):
        kp = real_port(root_, clock, tmp)
        monkeypatch.setattr(kp, "tistory_rank", lambda today=None, **_: {"rows": [{"kw": k} for k in TISTORY]})
        return kp

    monkeypatch.setattr(bi, "_port", port)
    return root


@pytest.fixture
def ledger(tmp_path):
    return LedgerStore(tmp_path / "state" / LEDGER_REL)


def _report(root: Path, run_date: str, drafts: list[tuple[str, str | None]], started: str = STARTED) -> None:
    rows = [{"path": f"content/{p}/x.md", "platform": p, "keyword": k} for p, k in drafts]
    (root / "ops" / "blog-runs" / f"{run_date}.json").write_text(json.dumps(
        {"schema": bd.RUN_REPORT_SCHEMA, "run_date": run_date, "started_at": started, "drafts": rows},
        ensure_ascii=False), encoding="utf-8")


def _instructions(ledger, kind=bi.RECORD_KIND):
    return [row["record"] for row in ledger.iter_records(kinds=[kind]) if row.get("kind") == kind]


def test_the_instruction_holds_what_the_cron_chooses_from(ledger, vault):
    assert bi.fire(ledger, now=GAP, vault_root=vault) == "blog_instruction=2026-11-04(next=1,queue=4,tistory=2,affiliate=unavailable)"
    [record] = _instructions(ledger)
    assert record["run_date"] == RUN_DATE and record["history_date"] == "2026-11-04" and record["history_today"]
    assert record["naver"]["next_topics"] == [{"kw": "다음 주제", "lane": "실무 가이드"}]
    queue = {q["kw"]: q for q in record["naver"]["queue"]}
    assert set(queue) == {"가 키워드", "나 키워드", "시즌 키워드", "바 키워드"}
    assert [queue[k]["rank"] for k in ("나 키워드", "가 키워드", "시즌 키워드")] == [1, 2, 3]
    assert queue["바 키워드"]["rank"] == 1 and queue["시즌 키워드"]["season_days"] == 10
    assert queue["가 키워드"]["season_days"] is None
    assert record["tistory"] == TISTORY and record["feedback_id"] is None
    assert record["summary"]["filtered_covered"] == 1 and record["summary"]["filtered_not_create"] == 1
    assert record["summary"]["queue_lanes"] == {"AI 활용": 1, "실무 가이드": 3}


def test_the_latest_guideline_orders_the_queue_and_the_unadjusted_rank_is_kept(ledger, vault):
    guideline = {"feedback_id": "blogfeedback_" + "0" * 20,
                 "clusters": [{"cluster": "aa", "factor": 1.2}, {"cluster": "bb", "factor": 0.8}]}
    gathered = bi.gather(vault, HISTORY, RUN_DATE, clock=bi.datetime.fromisoformat(GAP.replace("Z", "+00:00")))
    kept = [dict(c, score=25.0) if c["kw"] == "가 키워드" else c for c in gathered["kept"]]
    rows = {q["kw"]: q for q in bi.queue(kept, guideline)}
    # 가 25 × 1.2 = 30 passes 나 30 × 0.8 = 24
    assert (rows["가 키워드"]["rank"], rows["가 키워드"]["rank_unadjusted"], rows["가 키워드"]["factor"]) == (1, 2, 1.2)
    assert (rows["나 키워드"]["rank"], rows["나 키워드"]["rank_unadjusted"], rows["나 키워드"]["factor"]) == (2, 1, 0.8)
    assert rows["시즌 키워드"]["factor"] == 1.0
    ledger.append_records("fb", {bi.FEEDBACK_KIND: guideline})
    assert bi.fire(ledger, now=GAP, vault_root=vault).startswith("blog_instruction=")
    assert _instructions(ledger)[0]["feedback_id"] == guideline["feedback_id"]


def test_it_is_built_once_inside_the_morning_gap(ledger, vault):
    assert bi.fire(ledger, now=EARLY, vault_root=vault) == "blog_instruction_nothing_new"
    assert bi.fire(ledger, now=AFTER, vault_root=vault) == "blog_instruction_nothing_new"
    assert _instructions(ledger) == []
    assert bi.fire(ledger, now=GAP, vault_root=vault).startswith("blog_instruction=2026-11-04")
    assert bi.fire(ledger, now=LATE, vault_root=vault) == "blog_instruction_nothing_new"
    assert len(_instructions(ledger)) == 1


def test_the_morning_reads_the_history_of_its_kst_day_not_of_the_utc_one(ledger, vault):
    # The run names its file by KST, so at 22:40Z on 11-03 the day's file is 11-04 and 11-03 is yesterday's. Until
    # 2026-11-05 the build looked the file up by the UTC date and took yesterday's while today's was already there.
    yesterday = {**HISTORY, "date": "2026-11-03", "candidates": [_cand("어제 키워드", "실무 가이드", 99.0)]}
    _history(vault, "2026-11-03", yesterday)
    assert bi.fire(ledger, now=GAP, vault_root=vault).startswith("blog_instruction=2026-11-04")
    [record] = _instructions(ledger)
    assert record["history_date"] == "2026-11-04" and record["history_today"] is True
    assert "어제 키워드" not in {q["kw"] for q in record["naver"]["queue"]}


def test_a_morning_without_its_own_history_waits_then_reads_the_last_one(ledger, vault):
    (vault / "analytics" / "keywords" / "history" / "2026-11-04.json").rename(
        vault / "analytics" / "keywords" / "history" / "2026-11-03.json")
    assert bi.fire(ledger, now=GAP, vault_root=vault) == "blog_instruction_waiting_history"
    assert bi.fire(ledger, now=LATE, vault_root=vault).startswith("blog_instruction=2026-11-04")
    [record] = _instructions(ledger)
    assert record["history_date"] == "2026-11-03" and record["history_today"] is False


def test_each_reported_draft_is_located_in_the_instruction(ledger, vault):
    bi.fire(ledger, now=GAP, vault_root=vault)
    _report(vault, RUN_DATE, [("naver", "다음 주제"), ("naver", "가키워드"), ("naver", "보유 키워드"),
                              ("naver", "새로 찾은 말"), ("tistory", "티스토리 둘째"), ("tistory", "발굴 질문"),
                              ("tistory", None)])
    status = bi.fire(ledger, now="2026-11-04T03:00:00Z", vault_root=vault)
    assert status == "checked=2026-11-04:checked(in=3/7,ready)"
    [check] = _instructions(ledger, bi.CHECK_KIND)
    picks = [(p["platform"], p["source"], p["lane"], p["rank"]) for p in check["picks"]]
    assert picks == [("naver", "next_topics", "실무 가이드", None), ("naver", "queue", "실무 가이드", 2),
                     ("naver", "history_filtered", None, None), ("naver", "outside", None, None),
                     ("tistory", "tistory_list", None, 2), ("tistory", "outside", None, None),
                     ("tistory", "no_keyword", None, None)]
    assert check["instruction_id"] == _instructions(ledger)[0]["instruction_id"]
    assert bi.fire(ledger, now="2026-11-04T04:00:00Z", vault_root=vault) == "blog_instruction_nothing_new"


def test_an_instruction_built_after_the_run_started_is_late(ledger, vault):
    bi.fire(ledger, now=GAP, vault_root=vault)
    _report(vault, RUN_DATE, [("naver", "가 키워드")], started="2026-11-03T22:30:00Z")
    assert bi.fire(ledger, now="2026-11-04T03:00:00Z", vault_root=vault) == "checked=2026-11-04:checked(in=1/1,late)"


def test_a_day_without_an_instruction_is_named_and_days_before_the_first_are_not_checked(ledger, vault):
    _report(vault, "2026-11-03", [("naver", "가 키워드")])
    assert bi.fire(ledger, now="2026-11-03T12:00:00Z", vault_root=vault) == "blog_instruction_nothing_new"
    bi.fire(ledger, now=GAP, vault_root=vault)
    _report(vault, RUN_DATE, [("naver", "가 키워드")])
    _report(vault, "2026-11-05", [("naver", "나 키워드")], started="2026-11-04T23:02:00Z")
    status = bi.fire(ledger, now="2026-11-05T03:00:00Z", vault_root=vault)
    assert status == "checked=2026-11-04:checked(in=1/1,ready),2026-11-05:no_instruction"
    checks = {c["run_date"]: c for c in _instructions(ledger, bi.CHECK_KIND)}
    assert set(checks) == {"2026-11-04", "2026-11-05"}
    assert checks["2026-11-05"]["picks"] == [] and checks["2026-11-05"]["timing"] is None


def test_it_opens_no_url_and_records_no_path_or_url(ledger, vault, monkeypatch):
    def refuse(*_a, **_k):
        raise AssertionError("the instruction shadow opened a connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    bi.fire(ledger, now=GAP, vault_root=vault)
    _report(vault, RUN_DATE, [("naver", "가 키워드")])
    bi.fire(ledger, now="2026-11-04T03:00:00Z", vault_root=vault)
    text = json.dumps([r["record"] for r in ledger.iter_records(kinds=[bi.RECORD_KIND, bi.CHECK_KIND])],
                      ensure_ascii=False)
    assert "content/" not in text and "http" not in text and ".md" not in text


def test_an_absent_vault_refuses(ledger, tmp_path):
    with pytest.raises(ToolError) as caught:
        bi.fire(ledger, now=GAP, vault_root=tmp_path / "absent")
    assert caught.value.reason_code == "BLOG_VAULT_UNAVAILABLE"


def test_the_kind_is_maintenance_and_fires_through_the_scheduler(ledger, vault, tmp_path, monkeypatch):
    assert KIND_BLOG_INSTRUCTION in KINDS and KIND_BLOG_INSTRUCTION in MAINTENANCE_KINDS
    assert {bi.RECORD_KIND, bi.CHECK_KIND} <= set(RECORD_KINDS)
    monkeypatch.setenv(bd.BLOG_VAULT_ROOT_ENV, str(vault))
    state = tmp_path / "state"
    store = ScheduleStore(state)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.add(build_schedule(kind=KIND_BLOG_INSTRUCTION, request="", interval_seconds=600, created_by="op",
                             now="2026-11-03T22:30:00Z"))
    run_due(store, now=GAP, control_store=ControlStore(state), ledger=ledger, repo_root=state)
    terminal = [e for e in ledger.read_scheduler_events() if e["action"] in ("fired", "failed")][-1]
    assert terminal["action"] == "fired" and terminal["status"].startswith("blog_instruction=2026-11-04")


def test_the_mornings_affiliate_pick_is_compared_with_the_days_placement(ledger, vault, monkeypatch):
    # P7 (Thomas 2026-11-05 "지시에 싣기"): the instruction names the stock it would place; the report's placement
    # is compared with it by the stock's slug. Neither record carries the stock's file name.
    pick = {"state": "built", "error": None, "stock": 2, "ready": 1, "held": 1, "needs_review": 0, "link_unready": 0,
            "pick": {"id": bi.stock_id("업소용-냉장고"), "keyword": "업소용 냉장고"}, "deferred": None}
    monkeypatch.setattr(bi, "affiliate_pick", lambda root: pick)
    assert bi.fire(ledger, now=GAP, vault_root=vault).endswith("affiliate=built)")
    assert _instructions(ledger)[0]["affiliate"] == pick
    _report(vault, RUN_DATE, [("naver", "나 키워드")])
    path = vault / "ops" / "blog-runs" / f"{RUN_DATE}.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    report["affiliate_placed"] = [{"path": "content/naver/제휴/202편-업소용-냉장고.md", "episode": 202}]
    path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    bi.fire(ledger, now=AFTER, vault_root=vault)
    [check] = _instructions(ledger, bi.CHECK_KIND)
    assert check["affiliate"] == {"state": "compared", "placed": 1, "matched": 1, "differed": 0, "unjudged": 0,
                                  "placements": [{"episode": 202, "matched": True}]}
    text = json.dumps([_instructions(ledger), check], ensure_ascii=False)
    assert "제휴-" not in text and "202편" not in text and "content/" not in text

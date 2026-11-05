"""The blog department, observed (BLOG_DEPARTMENT_V0.1.md stages 1-2): the vault's front matter, counted.

What is under test is that the counts mean what the report says they mean — a planned Naver slot is
"upcoming", never "published"; a file without front matter is unparsed, never guessed — that an absent vault
refuses instead of reading as an empty week, and that the ledger record carries counts and nothing else."""

from __future__ import annotations

import json

import pytest

from runtime.mvp_runtime import blog_department as bd
from runtime.mvp_runtime import management_report as mr
from runtime.mvp_runtime import operator as operator_mod
from runtime.mvp_runtime.control import ControlStore
from runtime.mvp_runtime.errors import ToolError
from runtime.mvp_runtime.scheduler import KIND_MANAGEMENT_REPORT, ScheduleStore, build_schedule, run_due
from runtime.mvp_runtime.store import LEDGER_REL, LedgerStore

NOW = "2026-10-26T00:00:00Z"            # Monday 09:00 KST: the window is 10-19..10-25, ahead 10-26..11-01


def _post(folder, name, **fields):
    lines = ["---", 'title: "제목은 기록에 남지 않는다"'] + [f"{k}: {v}" for k, v in fields.items()] + ["---", "본문"]
    (folder / name).write_text("\n".join(lines), encoding="utf-8")


@pytest.fixture
def vault(tmp_path):
    naver, tistory = tmp_path / "content" / "naver", tmp_path / "content" / "tistory"
    naver.mkdir(parents=True)
    tistory.mkdir(parents=True)
    _post(naver, "1.md", status="published", created="2026-10-10", published="2026-10-20")
    _post(naver, "2.md", status="published", created="2026-10-01", published="2026-10-05")  # published before the window
    _post(naver, "3.md", status="draft", created="2026-10-21", scheduled="2026-10-28 09:00")  # a plan, not a publication
    _post(naver, "4.md", status="hold", created="2026-10-22", scheduled="재검토")
    _post(naver, "5.md", status="published", created="2026-10-22")                            # no published date: not counted
    (naver / "6.md").write_text("front matter 없음", encoding="utf-8")
    _post(tistory, "1.md", status="scheduled", created="2026-10-25", scheduled="2026-10-30 16:16")
    _post(tistory, "2.md", status="published", created="2026-10-18", published="2026-10-25")
    _post(tistory, "3.md", status="scheduled", created="2026-10-19", scheduled="2026-11-03 13:30")  # beyond the week ahead
    # The guards: a published post keeps its slot (95 Naver posts do), and a reserved post can carry a stale date.
    _post(naver, "7.md", status="published", created="2026-10-01", published="2026-10-02", scheduled="2026-10-27 09:00")
    _post(tistory, "4.md", status="scheduled", created="2026-10-01", published="2026-10-21", scheduled="2026-10-29 19:30")
    runs = tmp_path / "ops" / "blog-runs"
    runs.mkdir(parents=True)
    ok = {"exit": 0, "fail": 0, "warn": 1, "error": None}
    _run(runs, "2026-10-21", 0, [_draft("가", ok, ok), _draft("나", {"exit": 1, "fail": 2, "warn": 0, "error": None}, ok)])
    _run(runs, "2026-10-22", 1, [_draft("가", ok, {"exit": None, "fail": None, "warn": None, "error": "TimeoutExpired"})],
         instruction="instr_1")
    _run(runs, "2026-10-18", 0, [_draft("창 밖", ok, ok)])                          # before the window: not counted
    (runs / "2026-10-23.json").write_text("{not json", encoding="utf-8")          # unreadable: counted as such only
    return tmp_path


def _draft(keyword, preflight, variety):
    return {"path": "content/naver/x.md", "platform": "naver", "status": "draft", "cluster": None,
            "keyword": keyword, "scheduled": None, "preflight": preflight, "variety": variety}


def _run(folder, day, draft_rc, drafts, instruction=None):
    report = {"schema": "blog_run_report.v1", "run_date": day, "started_at": f"{day}T00:00:00Z",
              "ended_at": f"{day}T01:00:00Z", "instruction_id": instruction, "kw_status": "kw ok",
              "steps": {"draft": draft_rc, "naver_slot": 0}, "drafts": drafts, "skipped": None}
    (folder / f"{day}.json").write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")


def test_the_counts_mean_what_they_say(vault):
    o = bd.observe(vault, now=NOW)
    assert (o["since"], o["until"], o["ahead"]) == ("2026-10-19", "2026-10-26", "2026-11-02")
    naver, tistory = o["platforms"]["naver"], o["platforms"]["tistory"]
    assert naver["files"] == 7 and naver["unparsed"] == 1
    assert naver["status"] == {"draft": 1, "hold": 1, "scheduled": 0, "published": 4, "other": 0}
    assert (naver["new_drafts"], naver["published"], naver["upcoming"]) == (3, 1, 1)   # 7.md is not "upcoming"
    assert tistory["status"]["scheduled"] == 3 and tistory["status"]["published"] == 1
    assert (tistory["new_drafts"], tistory["published"], tistory["upcoming"]) == (2, 1, 2)  # 4.md is not "published"


def test_the_run_reports_are_counted_in_the_window_and_an_unreadable_one_adds_nothing(vault):
    runs = bd.observe(vault, now=NOW)["runs"]
    assert runs == {"reports": 2, "unreadable": 1, "days_without_report": 5, "draft_step_failed": 1, "instructed": 1,
                    "drafts": 3, "preflight_fail": 1, "variety_fail": 0, "checks_unrun": 1, "distinct_keywords": 2}


def test_an_unmounted_report_folder_is_absent_not_zero(vault):
    import shutil

    shutil.rmtree(vault / "ops")
    assert bd.observe(vault, now=NOW)["runs"] is None
    record = bd.build_record(bd.observe(vault, now=NOW), now=NOW)
    assert record["runs"] is None


@pytest.mark.parametrize("root", [None, "missing"])
def test_an_absent_vault_refuses_rather_than_reading_as_an_empty_week(tmp_path, monkeypatch, root):
    monkeypatch.delenv(bd.BLOG_VAULT_ROOT_ENV, raising=False)
    with pytest.raises(ToolError) as exc:
        bd.observe(None if root is None else tmp_path / root, now=NOW)
    assert exc.value.reason_code == "BLOG_VAULT_UNAVAILABLE"


def test_an_empty_platform_folder_refuses_rather_than_reading_as_a_quiet_week(vault):
    for post in (vault / "content" / "tistory").glob("*.md"):
        post.unlink()
    with pytest.raises(ToolError) as exc:
        bd.observe(vault, now=NOW)
    assert exc.value.reason_code == "BLOG_VAULT_UNAVAILABLE" and "content/tistory" in str(exc.value)


def test_the_record_is_counts_only_and_satisfies_its_closed_schema(vault):
    record = bd.build_record(bd.observe(vault, now=NOW), now=NOW)
    assert record["schema_version"] == "blog_department_week.v0.4" and record["observation_id"].startswith("blogweek_")
    assert set(record["stages"]) == {"verify", "analysis", "kw_replay", "kw_direct", "feedback", "instruction", "slot"}
    assert all(v is None for v in record["stages"].values())       # a week with no stage records reads as none, not zero
    assert "제목" not in json.dumps(record, ensure_ascii=False)
    assert "가" not in json.dumps(record["runs"], ensure_ascii=False)   # keywords are counted, never carried
    from runtime.mvp_runtime import schema_cache
    from runtime.mvp_runtime.paths import repo_root
    from runtime.read_only_kernel.schema_validation import RuntimeSchemaError

    with pytest.raises(RuntimeSchemaError):
        schema_cache.validate_against_schema(dict(record, title="leak"), repo_root() / bd.RECORD_SCHEMA, bd.RECORD_KIND)


def test_the_report_renders_the_blog_section_and_an_unreadable_one(vault):
    class Empty:
        def iter_records_with_archive(self, **_):
            return iter([])

        def iter_events_with_archive(self, *_a, **_k):
            return iter([])

    class NoApprovals:
        def pending(self):
            return []

    class NoSchedules:
        def list(self):
            return []

    def collect(blog):
        return mr.render(mr.collect(now=NOW, ledger=Empty(), schedules=NoSchedules(), approval_store=NoApprovals(),
                                    blog=blog))

    text = collect(lambda: bd.observe(vault, now=NOW))
    assert "■ 블로그" in text
    assert "[네이버] 새 원고 3 · 발행 1 · 다음 7일 예정 1" in text
    assert "지금: draft 1 · 보류 1 · 예약 0 · 발행됨 4 · 읽지 못한 파일 1" in text
    assert "[티스토리] 새 원고 2 · 발행 1 · 다음 7일 예정 2" in text
    assert "[원고 실행 보고] 2일 보고 · 보고 없는 날 5 · 원고 단계 실패 1일 · 읽지 못한 보고 1" in text
    assert "원고 3편(front matter 새 원고 5) · 검수 FAIL preflight 1 · variety 0 · 검수 미실행 1 · 키워드 2개 · 시스템 지시 1일" in text

    def absent():
        raise ToolError("BLOG_VAULT_UNAVAILABLE", "not mounted")

    assert "■ 블로그\n  읽지 못함: BLOG_VAULT_UNAVAILABLE" in collect(absent)


def test_the_weekly_fire_records_the_department_week_on_the_ledger(vault, tmp_path, monkeypatch):
    monkeypatch.setenv(bd.BLOG_VAULT_ROOT_ENV, str(vault))
    monkeypatch.setattr(operator_mod, "select_control_channel", lambda: "control-bot")
    sent: list[str] = []
    monkeypatch.setattr(operator_mod, "notify_operator", lambda channel, text, **_: sent.append(text))
    state = tmp_path / "state"
    store = ScheduleStore(state)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.add(build_schedule(kind=KIND_MANAGEMENT_REPORT, request="", interval_seconds=604800,
                             created_by="op", now="2026-10-19T00:00:00Z"))
    ledger = LedgerStore(state / LEDGER_REL)
    run_due(store, now=NOW, control_store=ControlStore(state), ledger=ledger, repo_root=state)
    rows = [r for r in ledger.iter_records(kinds=[bd.RECORD_KIND]) if r["kind"] == bd.RECORD_KIND]
    assert len(rows) == 1 and rows[0]["record"]["platforms"]["naver"]["published"] == 1
    assert rows[0]["record"]["runs"]["reports"] == 2
    assert rows[0]["record"]["stages"]["verify"]["runs"] == 0 and rows[0]["record"]["stages"]["slot"] is None
    terminal = [e for e in ledger.read_scheduler_events() if e["action"] in ("fired", "failed")][-1]
    assert terminal["action"] == "fired" and f"blog_recorded={rows[0]['record']['observation_id']}" in terminal["status"]
    assert "■ 블로그" in sent[0]


class _Ledger:
    """The shadows' records as the ledger yields them: appended in order, filtered by kind."""

    def __init__(self):
        self.rows: list[dict] = []

    def add(self, kind, **record):
        self.rows.append({"kind": kind, "record": record})
        return self

    def iter_records_with_archive(self, *, appended_since=None, kinds=None):
        return iter([r for r in self.rows if r["kind"] in kinds])


def _stage_ledger():
    ledger = _Ledger()
    for bundle, state, agree in (("20261018-221000", "compared", True),       # before the window: a day, not this week
                                 ("20261020-221000", "generator_stale", None),
                                 ("20261020-221000", "compared", True),       # re-replayed after the regeneration
                                 ("20261021-221000", "compared", False),
                                 ("20261022-221000", "replay_miss", None),
                                 ("20261023-221000", "compared", True),
                                 ("20261023-230000", "compared", True),       # 10-24 has no bundle: neither counts nor breaks
                                 ("20261025-221000", "compared", True)):
        ledger.add(bd.KW_REPLAY_RECORD_KIND, bundle=bundle, state=state, agree=agree)
    for day, moved, adjusted in (("2026-10-18", 0, 0), ("2026-10-20", 3, 5), ("2026-10-21", 0, 2), ("2026-10-21", 1, 2)):
        ledger.add(bd.FEEDBACK_RECORD_KIND, history_date=day, summary={"rank_moved": moved, "adjusted": adjusted})
    for day, today in (("2026-10-17", True), ("2026-10-22", True), ("2026-10-23", False)):
        ledger.add(bd.INSTRUCTION_RECORD_KIND, run_date=day, history_today=today)

    def pick(platform, source, keyword):
        return {"platform": platform, "keyword": keyword, "source": source, "lane": None, "rank": None,
                "season_days": None}

    def counts(**kw):
        return {"drafts": 0, "picks": 0, **{s: 0 for s in bd._PICK_SOURCES}, **kw}

    ledger.add(bd.INSTRUCTION_CHECK_RECORD_KIND, run_date="2026-10-21", state="no_instruction", timing=None, picks=[],
               summary=counts(drafts=2))
    ledger.add(bd.INSTRUCTION_CHECK_RECORD_KIND, run_date="2026-10-22", state="checked", timing="ready",
               picks=[pick("naver", "queue", "가"), pick("naver", "next_topics", "나"), pick("tistory", "outside", "비밀")],
               summary=counts(drafts=3, picks=3, in_instruction=2, next_topics=1, queue=1, outside=1))
    ledger.add(bd.INSTRUCTION_CHECK_RECORD_KIND, run_date="2026-10-23", state="checked", timing="late",
               picks=[pick("tistory", "tistory_list", "다"), pick("naver", "outside", "라")],
               summary=counts(drafts=2, picks=2, in_instruction=1, tistory_list=1, outside=1))
    # A v0.1 slot record (before the Tistory rules) beside a v0.2 one.
    ledger.add(bd.SLOT_RECORD_KIND, run_date="2026-10-22", schema_version="blog_slot.v0.1",
               summary={"naver": 2, "compared": 2, "agreed": 2, "differed": 0, "moved": 0, "not_compared": 0,
                        "tistory_not_compared": 3})
    ledger.add(bd.SLOT_RECORD_KIND, run_date="2026-10-23", schema_version="blog_slot.v0.2",
               summary={"naver": 1, "compared": 1, "agreed": 0, "differed": 1, "moved": 1, "not_compared": 0,
                        "tistory": 3, "tistory_checked": 2, "tistory_agreed": 1, "tistory_differed": 1,
                        "tistory_moved": 0, "tistory_not_checked": 1})
    return ledger


def test_no_stage_record_is_none_never_zero():
    assert bd.stages(_Ledger(), now=NOW) == {k: None for k in ("kw_replay", "kw_direct", "feedback", "instruction", "slot")}


def test_the_keyword_shadow_counts_the_agreeing_run_as_a_calendar_span_and_any_other_state_breaks_it():
    ledger = _stage_ledger()
    s = bd.stages(ledger, now=NOW)["kw_replay"]
    # The window is 10-19..10-25: six bundles, the re-replayed 10-20 counted once as its latest record.
    assert s == {"bundles": 6, "agreed": 4, "differed": 1, "stale": 0, "other": 1, "days": 6, "first": "2026-10-18",
                 "run_first": "2026-10-23", "run_last": "2026-10-25", "run_days": 3,
                 "run_bundle_days": 2}                              # 10-23..10-25; 10-22's replay miss ends it
    ledger.add(bd.KW_REPLAY_RECORD_KIND, bundle="20261025-230000", state="generator_stale", agree=None)
    s = bd.stages(ledger, now=NOW)["kw_replay"]
    assert s["stale"] == 1                                          # one bad bundle makes its whole day bad
    assert (s["run_first"], s["run_last"], s["run_days"], s["run_bundle_days"]) == (None, None, 0, 0)
    assert bd.stages(ledger, now=NOW)["kw_direct"] is None


def test_a_bundle_every_second_or_third_day_keeps_the_run_and_a_longer_gap_ends_it():
    # 2026-11-04 on the live host: root's cron (--max-age 2) left bundles 10-28, 10-29, 10-30, 10-31, 11-02, 11-04.
    # Counted as bundle days, "14 days" would have needed about four weeks of calendar.
    ledger = _Ledger()
    for day in ("20261101", "20261105", "20261107", "20261110", "20261112"):
        ledger.add(bd.KW_REPLAY_RECORD_KIND, bundle=f"{day}-071000", state="compared", agree=True)
    s = bd.stages(ledger, now="2026-11-16T00:00:00Z")["kw_replay"]
    # 11-07 → 11-10 is a three-day gap, still a healthy cron; 11-01 → 11-05 (four days) was never checked.
    assert (s["run_first"], s["run_last"], s["run_days"], s["run_bundle_days"]) == ("2026-11-05", "2026-11-12", 8, 4)
    assert bd.BUNDLE_GAP_DAYS == 3
    assert "연속 일치 8일(11-05부터, 묶음 4일, 조건 14일)" in "\n".join(mr._stage_lines(bd.stages(ledger, now="2026-11-16T00:00:00Z")))


def test_the_feedback_instruction_and_slot_shadows_are_summed_over_the_week_by_their_latest_record():
    s = bd.stages(_stage_ledger(), now=NOW)
    assert s["feedback"] == {"days": 3, "first": "2026-10-18", "week_days": 2, "week_days_moved": 2,
                             "week_rank_moved": 4, "week_adjusted": 7}
    assert s["instruction"] == {
        "days": 3, "first": "2026-10-17", "week_built": 2, "week_history_not_today": 1, "week_checked": 2,
        "week_no_instruction": 1, "week_late": 1, "drafts": 5, "in_instruction": 3, "next_topics": 1, "queue": 1,
        "tistory_list": 1, "history_filtered": 0, "outside": 2, "no_keyword": 0, "outside_naver": 1,
        "outside_tistory": 1}
    assert s["slot"] == {"days": 2, "first": "2026-10-22", "week_days": 2, "compared": 3, "agreed": 2, "differed": 1,
                         "moved": 1, "not_compared": 0, "tistory_checked": 2, "tistory_agreed": 1,
                         "tistory_differed": 1, "tistory_moved": 0, "tistory_not_checked": 4}


def test_the_record_carries_every_stage_as_counts_and_the_report_renders_them(vault):
    ledger = _stage_ledger()
    observation = {**bd.observe(vault, now=NOW), "verification": bd.verification_summary(ledger, now=NOW),
                   "analysis": None, "stages": bd.stages(ledger, now=NOW)}
    record = bd.build_record(observation, now=NOW)        # validates against v0.4
    assert record["stages"]["verify"] == {"runs": 0, "drafts": 0, "compared": 0, "agreed": 0, "differed": 0,
                                          "not_compared": 0}
    assert record["stages"]["analysis"] is None and record["stages"]["kw_direct"] is None
    assert record["stages"]["instruction"]["outside_tistory"] == 1
    text = json.dumps(record, ensure_ascii=False)
    assert not any(k in text for k in ("가", "나", "다", "라", "비밀"))   # picks' keywords are counted, never carried

    lines = "\n".join(mr._stage_lines(observation["stages"]))
    assert "[키워드 재생 P4a] 이번 주 묶음 6 · 일치 4 · 다름 1 · 생성기 낡음 0 · 그 밖 1 · 연속 일치 3일(10-23부터, 묶음 2일, 조건 14일)" in lines
    assert "[키워드 직접 조회 P4b] 기록 없음" in lines
    assert "[피드백 P5] 기록 3일(10-18부터, 조건 14일) · 이번 주 2일 · 순위가 바뀐 날 2 · 바뀐 순위 4 · 조정된 후보 7" in lines
    assert "[지시 P6] 이번 주 지시 2일(이전 이력 사용 1) · 대조 2일 · 지시 없음 1 · 늦음 1" in lines
    assert ("원고 5편 중 지시 안 3(후속 1 · 큐 1 · 티스토리 목록 1) · 이력에서 빠짐 0 · 밖 2(네이버 1 · 티스토리 1)"
            " · 키워드 없음 0") in lines
    assert "[슬롯 P7] 이번 주 2일 · 네이버 비교 3 · 일치 2 · 다름 1 · 옮김 1 · 비교 못 함 0" in lines
    assert "티스토리 검사 2 · 통과 1 · 위반 1 · 옮김 0 · 검사 못 함 4" in lines
    assert all("기록 없음" in line for line in mr._stage_lines(bd.stages(_Ledger(), now=NOW)) if line.startswith("  ["))

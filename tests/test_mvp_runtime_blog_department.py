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
    assert record["schema_version"] == "blog_department_week.v0.2" and record["observation_id"].startswith("blogweek_")
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
    terminal = [e for e in ledger.read_scheduler_events() if e["action"] in ("fired", "failed")][-1]
    assert terminal["action"] == "fired" and f"blog_recorded={rows[0]['record']['observation_id']}" in terminal["status"]
    assert "■ 블로그" in sent[0]

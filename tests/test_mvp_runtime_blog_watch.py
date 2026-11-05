"""The outsourced-side watch (BLOG_DEPARTMENT v0.2, P6 prerequisite, Thomas 2026-11-05): the drafting cron's run
report, the Mac routines, the trend seeds and the weekly snapshot, judged from the vault mounted read-only.

The fixture vault is healthy at NOW (2026-11-04 14:00 KST): the 11-04 run report is clean, both Mac routines ended
``exit 0`` at 08:04 KST that morning, the trend seeds hold a line, and the week of Sunday 11-01 has its statistics note
and a filled snapshot. Each test breaks one thing."""

from __future__ import annotations

import json
import unicodedata
from pathlib import Path

import pytest

from runtime.mvp_runtime import blog_department as bd
from runtime.mvp_runtime.blog import watch
from runtime.mvp_runtime.control import ControlStore
from runtime.mvp_runtime.scheduler import KIND_BLOG_WATCH, KINDS, MAINTENANCE_KINDS, ScheduleStore, build_schedule, run_due
from runtime.mvp_runtime.store import LEDGER_REL, RECORD_KINDS, LedgerStore

NOW = "2026-11-04T05:00:00Z"            # 14:00 KST: past the drafting cron's 12:00 deadline for 11-04
BLOCK = "=== {when} ===\n임시저장 완료\nexit {rc}\n"
SNAPSHOT = "# 주간\n\n## 해석과 결정\n- **이번 주 판단**: 티스토리 비중을 유지한다\n\n## 다음\n"


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _report(root: Path, day: str, **over) -> None:
    body = {"schema": bd.RUN_REPORT_SCHEMA, "run_date": day, "steps": {"draft": 0, "naver_slot": 0}, "drafts": [],
            "skipped": None, **over}
    _write(root, f"ops/blog-runs/{day}.json", json.dumps(body, ensure_ascii=False))


@pytest.fixture
def vault(tmp_path):
    root = tmp_path / "vault"
    for platform in bd.PLATFORMS:                      # what makes a folder a vault (BLOG_VAULT_UNAVAILABLE otherwise)
        (root / "content" / platform).mkdir(parents=True)
    _report(root, "2026-11-04")
    _write(root, "ops/naver-draft/daily.log", BLOCK.format(when="2026-11-03 08:01", rc=0)
           + BLOCK.format(when="2026-11-04 08:04", rc=0))
    _write(root, "analytics/naver-trend/run.log", BLOCK.format(when="2026-11-04 08:04", rc=0))
    _write(root, "analytics/keywords/seeds_trend.txt", "# 트렌드 씨앗\n가게 키워드\n")
    _write(root, "analytics/네이버-주간통계-2026-11-01.md", "통계\n")
    _write(root, "analytics/2026-11-01.md", SNAPSHOT)
    return root


@pytest.fixture
def ledger(tmp_path):
    return LedgerStore(tmp_path / "state" / LEDGER_REL)


def _judge(root, now=NOW):
    from runtime.mvp_runtime import timeutil

    return watch.judge(root, timeutil.parse_iso(now))


def _records(ledger):
    return [r["record"] for r in ledger.iter_records(kinds=[watch.RECORD_KIND]) if r["kind"] == watch.RECORD_KIND]


def test_a_healthy_outsourced_side_is_all_ok(vault):
    checks = _judge(vault)
    assert {c: r["state"] for c, r in checks.items()} == dict.fromkeys(watch.CHECKS, "ok")
    assert checks["RUN_REPORT"]["subject"] == "2026-11-04" and checks["WEEKLY_MISSING"]["subject"] == "2026-11-01"
    assert checks["MAC_DRAFT_STALE"]["subject"] == "2026-11-04"


@pytest.mark.parametrize("change, reasons", [
    (lambda root: (root / "ops/blog-runs/2026-11-04.json").unlink(), ["missing"]),
    (lambda root: _write(root, "ops/blog-runs/2026-11-04.json", "{not json"), ["unreadable"]),
    (lambda root: _report(root, "2026-11-04", schema="other.v9"), ["unreadable"]),
    (lambda root: _report(root, "2026-11-04", steps={"draft": 1}), ["draft_failed"]),
    (lambda root: _report(root, "2026-11-04", skipped="NO_INSTRUCTION"), ["skipped"]),
])
def test_the_days_run_report_must_exist_and_be_clean(vault, change, reasons):
    change(vault)
    assert _judge(vault)["RUN_REPORT"] == {"state": "problem", "reasons": reasons, "subject": "2026-11-04"}


def test_before_the_cron_deadline_the_previous_day_is_judged(vault):
    # 11:00 KST on 11-04: the 11-04 run may still be going, so the day judged is 11-03, which has no report.
    assert _judge(vault, "2026-11-04T02:00:00Z")["RUN_REPORT"] == {
        "state": "problem", "reasons": ["missing"], "subject": "2026-11-03"}


def test_a_routine_is_stale_after_36_hours_of_no_success_and_says_what_came_after(vault):
    log = vault / "ops/naver-draft/daily.log"
    log.write_text(log.read_text(encoding="utf-8") + "=== 2026-11-05 08:02 재시도 ===\nexit 1\n"
                   + BLOCK.format(when="2026-11-05 08:03", rc=2) + "=== 2026-11-06 08:00 ===\n중간에 끊김\n",
                   encoding="utf-8")
    # 11-05 20:05 KST: 36 h and one minute after 11-04 08:04. The retry block is not a run; the 11-05 run failed and
    # the 11-06 one has no exit line.
    late = _judge(vault, "2026-11-05T11:05:00Z")["MAC_DRAFT_STALE"]
    assert late == {"state": "problem", "reasons": ["stale", "failed_since", "cut_since"], "subject": "2026-11-04"}
    assert _judge(vault, "2026-11-05T11:03:00Z")["MAC_DRAFT_STALE"]["state"] == "ok"


@pytest.mark.parametrize("text, reasons", [(None, ["no_log"]), ("=== 2026-11-04 08:04 ===\nexit 3\n", ["no_success"])])
def test_a_routine_without_a_successful_run_is_a_problem(vault, text, reasons):
    log = vault / "analytics/naver-trend/run.log"
    log.unlink() if text is None else log.write_text(text, encoding="utf-8")
    checks = _judge(vault)
    assert checks["MAC_TREND_STALE"] == {"state": "problem", "reasons": reasons, "subject": None}
    # One cause, one problem: the seeds are not judged while the trend routine is not ok.
    assert checks["TREND_SEEDS_EMPTY"] == {"state": "unknown", "reasons": ["trend_not_ok"], "subject": None}


def test_a_folder_not_mounted_is_unknown_never_ok_or_a_problem(vault):
    import shutil

    shutil.rmtree(vault / "ops" / "naver-draft")
    shutil.rmtree(vault / "ops" / "blog-runs")
    checks = _judge(vault)
    assert checks["MAC_DRAFT_STALE"] == {"state": "unknown", "reasons": ["unmounted"], "subject": None}
    assert checks["RUN_REPORT"] == {"state": "unknown", "reasons": ["unmounted"], "subject": "2026-11-04"}


def test_empty_trend_seeds_are_a_problem(vault):
    _write(vault, "analytics/keywords/seeds_trend.txt", "# 주석만\n\n")
    assert _judge(vault)["TREND_SEEDS_EMPTY"] == {"state": "problem", "reasons": ["empty"], "subject": None}


def test_the_weekly_snapshot_needs_its_statistics_and_a_filled_interpretation(vault):
    (vault / "analytics/네이버-주간통계-2026-11-01.md").unlink()
    _write(vault, "analytics/2026-11-01.md", "## 해석과 결정\n- **이번 주 판단**:\n- **다음 주 실행**:\n")
    assert _judge(vault)["WEEKLY_MISSING"] == {
        "state": "problem", "reasons": ["stats_missing", "unfilled"], "subject": "2026-11-01"}


def test_a_decomposed_hangul_name_from_the_mac_still_counts(vault):
    path = vault / "analytics/네이버-주간통계-2026-11-01.md"
    path.rename(path.with_name(unicodedata.normalize("NFD", path.name)))
    assert _judge(vault)["WEEKLY_MISSING"]["state"] == "ok"


def test_the_week_is_due_from_monday_21_kst(vault):
    # Monday 11-09 20:59 KST still judges the week of 11-01; at 21:00 the week of 11-08 is due and has nothing yet.
    assert _judge(vault, "2026-11-09T11:59:00Z")["WEEKLY_MISSING"]["subject"] == "2026-11-01"
    assert _judge(vault, "2026-11-09T12:00:00Z")["WEEKLY_MISSING"] == {
        "state": "problem", "reasons": ["stats_missing", "snapshot_missing"], "subject": "2026-11-08"}


def test_it_records_a_change_or_once_a_day_and_names_what_is_new_and_resolved(ledger, vault):
    assert watch.fire(ledger, now=NOW, vault_root=vault) == "blog_watch_recorded(all_ok)"
    assert watch.fire(ledger, now="2026-11-04T05:30:00Z", vault_root=vault) == "blog_watch_unchanged(all_ok)"
    _report(vault, "2026-11-04", steps={"draft": 1})
    assert watch.fire(ledger, now="2026-11-04T06:00:00Z", vault_root=vault) == "blog_watch_recorded(RUN_REPORT:problem)"
    _report(vault, "2026-11-04")
    watch.fire(ledger, now="2026-11-04T06:30:00Z", vault_root=vault)
    first, failed, fixed = _records(ledger)
    assert (first["problems"], first["new"], first["resolved"]) == ([], [], [])
    assert (failed["problems"], failed["new"]) == (["RUN_REPORT"], ["RUN_REPORT"])
    assert (fixed["problems"], fixed["resolved"]) == ([], ["RUN_REPORT"])
    # The next KST day records again even when nothing changed: a daily heartbeat.
    _report(vault, "2026-11-05")
    _write(vault, "ops/naver-draft/daily.log", BLOCK.format(when="2026-11-05 08:04", rc=0))
    _write(vault, "analytics/naver-trend/run.log", BLOCK.format(when="2026-11-05 08:04", rc=0))
    assert watch.fire(ledger, now="2026-11-05T05:00:00Z", vault_root=vault) == "blog_watch_recorded(all_ok)"
    text = json.dumps(_records(ledger), ensure_ascii=False)
    assert "임시저장" not in text and "가게 키워드" not in text and "티스토리" not in text


def test_an_absent_vault_refuses(ledger, tmp_path, monkeypatch):
    from runtime.mvp_runtime.errors import ToolError

    monkeypatch.delenv(bd.BLOG_VAULT_ROOT_ENV, raising=False)
    with pytest.raises(ToolError):
        watch.fire(ledger, now=NOW, vault_root=tmp_path / "nowhere")


def test_the_kind_is_maintenance_and_fires_through_the_scheduler(ledger, vault, tmp_path, monkeypatch):
    assert KIND_BLOG_WATCH in KINDS and KIND_BLOG_WATCH in MAINTENANCE_KINDS and watch.RECORD_KIND in RECORD_KINDS
    monkeypatch.setenv(bd.BLOG_VAULT_ROOT_ENV, str(vault))
    state = tmp_path / "state"
    store = ScheduleStore(state)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.add(build_schedule(kind=KIND_BLOG_WATCH, request="", interval_seconds=1800, created_by="op",
                             now="2026-11-04T04:00:00Z"))
    run_due(store, now=NOW, control_store=ControlStore(state), ledger=ledger, repo_root=state)
    terminal = [e for e in ledger.read_scheduler_events() if e["action"] in ("fired", "failed")][-1]
    assert terminal["action"] == "fired" and terminal["status"] == "blog_watch_recorded(all_ok)"

"""The backup watch (`scripts/ops/backup_watch.sh`).

It shipped without tests; the mutual-watch check is the occasion to give it some. Every case runs
against a scratch destination with a stub curl, so nothing here reads the host's backups or reaches
Telegram.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "ops" / "backup_watch.sh"

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="backup_watch.sh is a bash script")

OK_LINE = "2026-09-07T08:15:29Z OK mode=core govstate-20260907-0815.tar.gz.age 28M kept=7 enc=age recipient=age1qqqqqqqq anchor=excluded hermes-snapshot=ok workflow-snapshot=ok\n"
PRE_P10_LINE = "2026-09-07T08:15:29Z OK mode=core govstate-20260907-0815.tar.gz.age 28M kept=7 enc=age recipient=age1qqqqqqqq anchor=excluded hermes-snapshot=ok\n"
FAILED_LINE = "2026-09-07T07:45:05Z FAILED mode=core rc=2 hermes-snapshot=ok workflow-snapshot=ok\n"


def _dest(tmp_path: Path, *, core: bool = True, candles: bool = True, log: str = OK_LINE) -> Path:
    dest = tmp_path / "governance-state"
    dest.mkdir(exist_ok=True)
    (dest / "backup.log").write_text(log, encoding="utf-8")
    if core:
        (dest / "govstate-20260907-0815.tar.gz.age").write_text("x", encoding="utf-8")
    if candles:
        (dest / "govstate-candles-20260906-0815.tar.gz").write_text("x", encoding="utf-8")
    return dest


def _health_log(tmp_path: Path, *, age_seconds: int | None = 0) -> Path:
    log = tmp_path / "health-watch.log"
    if age_seconds is None:
        return log                                   # never written
    log.write_text("2026-09-07T15:00:00Z OK services=9\n", encoding="utf-8")
    if age_seconds:
        stamp = log.stat().st_mtime - age_seconds
        os.utime(log, (stamp, stamp))
    return log


def _run(tmp_path: Path, dest: Path, health: Path, *, dry_run: bool = True):
    cmd = [str(SCRIPT)] + (["--dry-run"] if dry_run else [])
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=60,
        env={**os.environ, "HARNESS_BACKUP_DEST": str(dest), "HEALTH_WATCH_LOG": str(health),
             "THOMAS_ENV_FILE": str(tmp_path / "absent.env"),
             "OPERATOR_REGISTRATION": str(tmp_path / "absent.json")},
    )


@posix_only
def test_current_backups_and_a_live_health_watch_say_nothing(tmp_path):
    result = _run(tmp_path, _dest(tmp_path), _health_log(tmp_path))
    assert result.returncode == 0 and "OK" in result.stdout


@posix_only
def test_a_failed_core_run_is_reported(tmp_path):
    result = _run(tmp_path, _dest(tmp_path, log=OK_LINE + FAILED_LINE), _health_log(tmp_path))
    assert result.returncode == 1 and "실패로 끝났습니다" in result.stdout


@posix_only
def test_an_archive_that_is_too_old_is_reported(tmp_path):
    dest = _dest(tmp_path)
    old = dest / "govstate-20260907-0815.tar.gz.age"
    os.utime(old, (0, 0))
    result = _run(tmp_path, dest, _health_log(tmp_path))
    assert result.returncode == 1 and "core 아카이브가" in result.stdout


@posix_only
def test_a_health_watch_that_stopped_writing_is_reported(tmp_path):
    """This is the half of the mutual check that runs daily: a day late beats never."""
    result = _run(tmp_path, _dest(tmp_path), _health_log(tmp_path, age_seconds=3600))
    assert result.returncode == 1
    assert "헬스 감시가" in result.stdout and "crontab" in result.stdout


@posix_only
def test_a_health_watch_log_that_was_never_written_is_reported(tmp_path):
    result = _run(tmp_path, _dest(tmp_path), _health_log(tmp_path, age_seconds=None))
    assert result.returncode == 1 and "한 번도 돌지 않았습니다" in result.stdout


@posix_only
def test_a_health_log_dated_in_the_future_is_reported_rather_than_trusted(tmp_path):
    health = _health_log(tmp_path)
    os.utime(health, (2 ** 31 - 1, 2 ** 31 - 1))     # 2038
    result = _run(tmp_path, _dest(tmp_path), health)
    assert result.returncode == 1 and "헬스 감시가" in result.stdout


@posix_only
def test_a_sibling_problem_does_not_offer_to_re_run_a_backup(tmp_path):
    """Re-running the backup does not restart a stopped health watch, and an alert that suggests
    it sends the reader to the wrong place."""
    stale = _run(tmp_path, _dest(tmp_path), _health_log(tmp_path, age_seconds=3600))
    assert "backup-governance-state.sh core" not in stale.stdout
    assert "짝이 되는 감시기" in stale.stdout

    real = _run(tmp_path, _dest(tmp_path, log=OK_LINE + FAILED_LINE), _health_log(tmp_path))
    assert "backup-governance-state.sh core" in real.stdout


@posix_only
def test_an_unknown_argument_refuses_to_run(tmp_path):
    result = subprocess.run(
        [str(SCRIPT), "--dryrun"], capture_output=True, text=True, timeout=60,
        env={**os.environ, "HARNESS_BACKUP_DEST": str(_dest(tmp_path))},
    )
    assert result.returncode == 64 and "usage" in result.stderr


# --- P10: the workflow snapshot marker ------------------------------------------------------------

OK_WITH_WORKFLOW = "2026-09-14T07:45:29Z OK mode=core govstate-20260914-0745.tar.gz.age 28M kept=7 enc=age recipient=age1qqqqqqqq anchor=excluded hermes-snapshot=ok workflow-snapshot=ok\n"
OK_ABSENT = "2026-09-14T07:45:29Z OK mode=core govstate-20260914-0745.tar.gz.age 28M kept=7 enc=age recipient=age1qqqqqqqq anchor=excluded hermes-snapshot=ok workflow-snapshot=absent\n"
OK_WF_FAILED = "2026-09-14T07:45:29Z OK mode=core govstate-20260914-0745.tar.gz.age 28M kept=7 enc=age recipient=age1qqqqqqqq anchor=excluded hermes-snapshot=ok workflow-snapshot=FAILED\n"


@posix_only
@pytest.mark.parametrize("line", [OK_WITH_WORKFLOW, OK_ABSENT])
def test_a_core_line_with_a_workflow_snapshot_or_none_to_take_is_quiet(tmp_path, line):
    result = _run(tmp_path, _dest(tmp_path, log=line), _health_log(tmp_path))
    assert result.returncode == 0 and "OK" in result.stdout


@posix_only
def test_a_failed_workflow_snapshot_is_reported_even_when_the_archive_was_written(tmp_path):
    result = _run(tmp_path, _dest(tmp_path, log=OK_WF_FAILED), _health_log(tmp_path))
    assert result.returncode == 1 and "워크플로 저장소 스냅샷이 실패" in result.stdout
    assert "복구: /root/backups/backup-governance-state.sh core" in result.stdout


@posix_only
def test_a_core_line_without_the_marker_means_an_old_backup_script(tmp_path):
    result = _run(tmp_path, _dest(tmp_path, log=PRE_P10_LINE), _health_log(tmp_path))
    assert result.returncode == 1 and "workflow-snapshot 표기가 없습니다" in result.stdout


# The core archive is encrypted to an age public key (2026-09-29). The watch tells the four ways
# that can go wrong apart, because each one has a different fix.
ENCRYPT_FAILED = "2026-09-29T07:45:02Z FAILED mode=core stage=encrypt reason=no-recipients-file\n"
ARCHIVE_FAILED = "2026-09-29T07:45:05Z FAILED mode=core stage=archive rc=2 missing=thomas_agent/.env hermes-snapshot=ok workflow-snapshot=ok\n"
PLAIN_OK = "2026-09-28T07:45:29Z OK mode=core govstate-20260928-0745.tar.gz 28M kept=7 hermes-snapshot=ok workflow-snapshot=ok\n"


@posix_only
def test_an_encryption_failure_is_named_as_one(tmp_path):
    result = _run(tmp_path, _dest(tmp_path, log=OK_LINE + ENCRYPT_FAILED), _health_log(tmp_path))
    assert result.returncode == 1 and "암호화 단계에서 실패" in result.stdout
    assert "아카이브 생성(tar)" not in result.stdout


@posix_only
def test_an_archive_failure_is_named_as_one(tmp_path):
    result = _run(tmp_path, _dest(tmp_path, log=OK_LINE + ARCHIVE_FAILED), _health_log(tmp_path))
    assert result.returncode == 1 and "아카이브 생성(tar) 단계에서 실패" in result.stdout
    assert "암호화 단계" not in result.stdout


@posix_only
def test_only_plaintext_archives_count_as_no_encrypted_backup(tmp_path):
    # A plaintext core archive — what the pre-encryption script writes — is not a backup the watch
    # accepts, and an OK line without enc=age names the old script.
    dest = _dest(tmp_path, core=False, log=PLAIN_OK)
    (dest / "govstate-20260928-0745.tar.gz").write_text("x", encoding="utf-8")
    result = _run(tmp_path, dest, _health_log(tmp_path))
    assert result.returncode == 1
    assert "암호화된 core 아카이브(.tar.gz.age)가 하나도 없습니다" in result.stdout
    assert "enc=age 표기가 없습니다" in result.stdout


@posix_only
def test_a_stale_encrypted_archive_is_not_the_same_message_as_a_missing_one(tmp_path):
    dest = _dest(tmp_path)
    os.utime(dest / "govstate-20260907-0815.tar.gz.age", (0, 0))
    result = _run(tmp_path, dest, _health_log(tmp_path))
    assert result.returncode == 1 and "암호화된 core 아카이브가" in result.stdout and "시간 지났습니다" in result.stdout
    assert "하나도 없습니다" not in result.stdout


# EXECUTION_STAGE_ANTI_ROLLBACK D1 a: the archive must not carry the execution stage anchor. The
# archive is encrypted and cannot be listed on this host, so the backup script checks tar's own member
# list and says so on the log line; the watch reads that word.
ANCHOR_FAILED = "2026-09-30T07:45:09Z FAILED mode=core stage=archive reason=anchor-in-archive hermes-snapshot=ok workflow-snapshot=ok\n"
OK_NO_ANCHOR_MARK = "2026-09-30T07:45:29Z OK mode=core govstate-20260930-0745.tar.gz.age 28M kept=7 enc=age recipient=age1qqqqqqqq hermes-snapshot=ok workflow-snapshot=ok\n"


@posix_only
def test_a_backup_refused_for_carrying_the_anchor_is_named_as_that(tmp_path):
    result = _run(tmp_path, _dest(tmp_path, log=OK_LINE + ANCHOR_FAILED), _health_log(tmp_path))
    assert result.returncode == 1 and "실행 단계 앵커" in result.stdout and "--exclude" in result.stdout
    assert "아카이브 생성(tar) 단계에서 실패" not in result.stdout


@posix_only
def test_an_ok_line_without_the_anchor_marker_means_a_script_that_never_checked(tmp_path):
    result = _run(tmp_path, _dest(tmp_path, log=OK_NO_ANCHOR_MARK), _health_log(tmp_path))
    assert result.returncode == 1 and "anchor=excluded 표기가 없습니다" in result.stdout

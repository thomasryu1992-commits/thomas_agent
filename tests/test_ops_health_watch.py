"""The container health watch (`scripts/ops/health_watch.sh`).

Two things are worth pinning about an ops script that speaks to Thomas's phone: that its roster of
containers is the one `docker-compose.yml` actually deploys, and that it stays quiet in the cases
where speaking would train him to ignore it. The script takes its docker binary from `DOCKER_BIN`,
so every case below runs against a stub that prints canned `docker inspect` output — no container,
no network, no state outside `tmp_path`.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

# The watch is a bash script run by cron on the Docker host. The roster check below is plain text
# and runs everywhere; everything that executes the script does not. Adding these skips raises the
# win32 count, so tests/skip_ceiling.json moves in the same commit — the Windows job fails on the
# ceiling, not on pytest, if the two disagree.
posix_only = pytest.mark.skipif(sys.platform == "win32", reason="health_watch.sh is a bash script")

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "ops" / "health_watch.sh"
COMPOSE = REPO_ROOT / "docker-compose.yml"

HEALTHY = "running|healthy|0|false|{id}|2026-09-07T00:00:00Z"


def _roster_from_script() -> list[str]:
    body = SCRIPT.read_text(encoding="utf-8")
    block = re.search(r"^SERVICES=\(\n(.*?)^\)", body, re.MULTILINE | re.DOTALL)
    assert block, "SERVICES=( ... ) roster not found in health_watch.sh"
    return [line.strip() for line in block.group(1).splitlines() if line.strip()]


def _container_names_from_compose() -> list[str]:
    return re.findall(r"^\s+container_name:\s*(\S+)\s*$", COMPOSE.read_text(encoding="utf-8"), re.MULTILINE)


def _stub_docker(tmp_path: Path, table: dict[str, str | None]) -> Path:
    """A `docker` that answers `inspect --format` from a fixed table; None means 'no such container'."""
    lines = ["#!/bin/bash",
             '[ "$1" = "version" ] && exit ${DOCKER_STUB_DAEMON_DOWN:-0}',   # the daemon probe
             'name="$2"', "case \"$name\" in"]
    for name, value in table.items():
        if value is None:
            lines += [f"  {name}) exit 1 ;;"]
        else:
            lines += [f"  {name}) echo '{value.format(id='c' * 64)}' ;;"]
    lines += ["  *) exit 1 ;;", "esac"]
    stub = tmp_path / "docker-stub"
    stub.write_text("\n".join(lines) + "\n", encoding="utf-8")
    stub.chmod(0o755)
    return stub


def _stub_curl(tmp_path: Path, http_code: str) -> Path:
    """A `curl` that reports a status code and files what it was asked to send."""
    stub = tmp_path / f"curl-stub-{http_code}"
    stub.write_text(
        "#!/bin/bash\n"
        f'printf "%s\\n" "$@" > "{tmp_path}/sent.txt"\n'   # the message rides in --data-urlencode
        "cat > /dev/null\n"                                 # drain the config on stdin
        f"printf '{http_code}'\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return stub


def _run(tmp_path: Path, table: dict[str, str | None], *, dry_run: bool = False,
         confirm_runs: int = 2, delivers: str | None = None) -> subprocess.CompletedProcess[str]:
    """`delivers` is the HTTP code a stub curl returns; None means no secret source at all, so
    `send` records SKIPPED and never reaches the network."""
    env = dict(os.environ)
    env.update(
        DOCKER_BIN=str(_stub_docker(tmp_path, table)),
        HEALTH_WATCH_STATE_DIR=str(tmp_path),
        HEALTH_WATCH_CONFIRM_RUNS=str(confirm_runs),
        THOMAS_ENV_FILE=str(tmp_path / "absent.env"),
        OPERATOR_REGISTRATION=str(tmp_path / "absent.json"),
    )
    if delivers is not None:
        env_file = tmp_path / "fake.env"
        env_file.write_text("TELEGRAM_BOT_TOKEN=not-a-real-token\n", encoding="utf-8")
        registration = tmp_path / "fake-registration.json"
        registration.write_text('{"chat_id": "1"}', encoding="utf-8")
        env.update(THOMAS_ENV_FILE=str(env_file), OPERATOR_REGISTRATION=str(registration),
                   CURL_BIN=str(_stub_curl(tmp_path, delivers)))
    cmd = [str(SCRIPT)] + (["--dry-run"] if dry_run else [])
    return subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=60)


def _all_healthy() -> dict[str, str | None]:
    return {name: HEALTHY for name in _roster_from_script()}


def test_the_roster_is_the_one_compose_deploys():
    assert sorted(_roster_from_script()) == sorted(_container_names_from_compose())


@posix_only
def test_nine_healthy_containers_say_nothing(tmp_path):
    result = _run(tmp_path, _all_healthy(), dry_run=True)
    assert result.returncode == 0
    assert "정상" in result.stdout and "⚠️" not in result.stdout


@posix_only
def test_one_unhealthy_container_waits_for_a_second_look(tmp_path):
    """A deploy recreates all nine in about a minute; one sighting is not an outage."""
    table = _all_healthy()
    table["thomas-scheduler"] = "running|unhealthy|0|false|{id}|2026-09-07T00:00:00Z"
    first = _run(tmp_path, table)
    assert first.returncode == 0, "a first sighting must not alert"
    assert "PENDING keys=1 confirmed=0" in (tmp_path / "health-watch.log").read_text(encoding="utf-8")

    second = _run(tmp_path, table, delivers="200")
    assert second.returncode == 1
    assert "unhealthy" in (tmp_path / "sent.txt").read_text(encoding="utf-8")


@posix_only
def test_a_problem_that_clears_within_the_window_never_speaks(tmp_path):
    table = _all_healthy()
    table["hermes"] = "running|unhealthy|0|false|{id}|2026-09-07T00:00:00Z"
    assert _run(tmp_path, table).returncode == 0
    assert _run(tmp_path, _all_healthy()).returncode == 0
    log = (tmp_path / "health-watch.log").read_text(encoding="utf-8")
    assert "send=" not in log and "PENDING keys=1 confirmed=0" in log


@posix_only
def test_a_reported_problem_is_not_repeated_but_its_clearing_is(tmp_path):
    table = _all_healthy()
    table["thomas-operator"] = None                     # gone
    _run(tmp_path, table, delivers="200")
    _run(tmp_path, table, delivers="200")               # confirmed and delivered
    assert "컨테이너가 없습니다" in (tmp_path / "sent.txt").read_text(encoding="utf-8")

    third = _run(tmp_path, table, delivers="200")
    assert third.returncode == 1
    assert "UNCHANGED" in (tmp_path / "health-watch.log").read_text(encoding="utf-8")

    # One clean run is not yet recovery — the key is still remembered, so nothing is announced.
    still_remembering = _run(tmp_path, _all_healthy(), delivers="200")
    assert still_remembering.returncode == 1
    assert "복구" not in (tmp_path / "sent.txt").read_text(encoding="utf-8")

    recovered = _run(tmp_path, _all_healthy(), delivers="200")
    assert recovered.returncode == 0
    assert "복구" in (tmp_path / "sent.txt").read_text(encoding="utf-8")


@posix_only
def test_a_crash_loop_does_not_wait_for_confirmation(tmp_path):
    """RestartCount rising on the same container id is the restart policy, not a deploy."""
    _run(tmp_path, _all_healthy())                      # records the baseline counts
    table = _all_healthy()
    table["thomas-pipeline-worker"] = "running|healthy|3|false|{id}|2026-09-07T00:00:00Z"
    result = _run(tmp_path, table, delivers="200")
    assert result.returncode == 1, "a crash loop is an event, not a state — it skips the wait"
    assert "크래시 루프" in (tmp_path / "sent.txt").read_text(encoding="utf-8")


@posix_only
def test_a_recreated_container_resets_the_counter_without_alerting(tmp_path):
    """`up -d` gives a new id and RestartCount 0; a drop is a deploy, not a crash."""
    table = _all_healthy()
    table["hermes"] = "running|healthy|4|false|{id}|2026-09-07T00:00:00Z"
    _run(tmp_path, table)
    fresh = _all_healthy()                              # different stub id? same string, count back to 0
    result = _run(tmp_path, fresh)
    assert result.returncode == 0, "a lower count on the same id must not read as a crash loop"


@posix_only
def test_starting_is_normal_until_it_is_not(tmp_path):
    table = _all_healthy()
    table["hermes"] = "running|starting|0|false|{id}|2026-09-07T00:00:00Z"   # long past 15 minutes
    result = _run(tmp_path, table, dry_run=True, confirm_runs=1)
    assert result.returncode == 1 and "starting" in result.stdout

    recent = _all_healthy()
    recent["hermes"] = "running|starting|0|false|{id}|2099-01-01T00:00:00Z"  # a deploy, seconds ago
    assert _run(tmp_path, recent, dry_run=True, confirm_runs=1).returncode == 0


@posix_only
def test_an_oom_kill_is_reported_at_once(tmp_path):
    table = _all_healthy()
    table["thomas-operator"] = "running|healthy|0|true|{id}|2026-09-07T00:00:00Z"
    result = _run(tmp_path, table, dry_run=True)
    assert result.returncode == 1 and "OOM" in result.stdout, "an OOM kill skips the wait"


@posix_only
def test_dry_run_writes_neither_state_nor_log(tmp_path):
    table = _all_healthy()
    table["thomas-read-bridge"] = None
    _run(tmp_path, table, dry_run=True)
    assert not (tmp_path / "health-watch.state").exists()
    assert not (tmp_path / "health-watch.log").exists()


@posix_only
def test_the_silence_file_holds_an_alert_back_rather_than_swallowing_it(tmp_path):
    """Planned work should not page. But when the silence lapses the problem is still a problem —
    a suppressed alert must not count as one that was delivered."""
    table = _all_healthy()
    table["thomas-scheduler-maint"] = None
    silence = tmp_path / "health-watch.silence"
    silence.touch()
    _run(tmp_path, table, delivers="200")
    _run(tmp_path, table, delivers="200")               # confirmed, but muted
    assert not (tmp_path / "sent.txt").exists()
    assert "SUPPRESSED" in (tmp_path / "health-watch.log").read_text(encoding="utf-8")

    os.utime(silence, (0, 0))                           # 1970: far past the six-hour ceiling
    _run(tmp_path, table, delivers="200")
    assert "컨테이너가 없습니다" in (tmp_path / "sent.txt").read_text(encoding="utf-8")


@posix_only
def test_a_stubbed_docker_with_a_real_curl_refuses_to_send(tmp_path):
    """The 2026-09-07 mistake: a scratch test with a fake docker and the live token put two false
    outage alerts on Thomas's phone. Stubbing docker without stubbing curl must never page."""
    table = _all_healthy()
    table["thomas-scheduler"] = "running|unhealthy|0|false|{id}|2026-09-07T00:00:00Z"
    env_file = tmp_path / "real-looking.env"
    env_file.write_text("TELEGRAM_BOT_TOKEN=looks-real\n", encoding="utf-8")
    registration = tmp_path / "registration.json"
    registration.write_text('{"chat_id": "1"}', encoding="utf-8")
    result = subprocess.run(
        [str(SCRIPT)], capture_output=True, text=True, timeout=60,
        env={**os.environ, "DOCKER_BIN": str(_stub_docker(tmp_path, table)),
             "HEALTH_WATCH_STATE_DIR": str(tmp_path), "HEALTH_WATCH_CONFIRM_RUNS": "1",
             "THOMAS_ENV_FILE": str(env_file), "OPERATOR_REGISTRATION": str(registration)},
    )
    assert result.returncode == 1
    assert "send=BLOCKED" in (tmp_path / "health-watch.log").read_text(encoding="utf-8")


@posix_only
def test_a_cascade_still_reaches_the_phone(tmp_path):
    """Confirmation counted per problem SET stayed silent while an outage widened — one new failure
    per cycle reset the window forever. Counted per problem, the first one still lands on time."""
    table = _all_healthy()
    table["thomas-scheduler"] = "running|unhealthy|0|false|{id}|2026-09-07T00:00:00Z"
    _run(tmp_path, table, delivers="200")                      # first sighting: waits
    table["thomas-pipeline-worker"] = "running|unhealthy|0|false|{id}|2026-09-07T00:00:00Z"
    _run(tmp_path, table, delivers="200")                      # the set widened; scheduler is due
    sent = (tmp_path / "sent.txt").read_text(encoding="utf-8")
    assert "thomas-scheduler" in sent and "unhealthy" in sent


@posix_only
def test_a_dead_daemon_is_one_line_not_nine(tmp_path):
    """Nine 'container missing' lines name the wrong thing when it is docker that is down."""
    env_down = dict(os.environ, DOCKER_STUB_DAEMON_DOWN="1")
    stub = _stub_docker(tmp_path, {})                       # answers nothing at all
    result = subprocess.run(
        [str(SCRIPT), "--dry-run"], capture_output=True, text=True, timeout=60,
        env={**env_down, "DOCKER_BIN": str(stub), "HEALTH_WATCH_STATE_DIR": str(tmp_path),
             "HEALTH_WATCH_CONFIRM_RUNS": "1"},
    )
    assert result.returncode == 1
    assert "docker 데몬이 응답하지 않습니다" in result.stdout
    assert "컨테이너가 없습니다" not in result.stdout


@posix_only
def test_a_container_docker_cannot_describe_reads_as_missing(tmp_path):
    """An empty inspect line must read as 'missing', not crash the run under `set -u`."""
    table = _all_healthy()
    table["thomas-knowledge-bridge"] = None
    result = _run(tmp_path, table, dry_run=True, confirm_runs=1)
    assert result.returncode == 1
    assert "컨테이너가 없습니다" in result.stdout
    assert "unbound variable" not in result.stderr


@posix_only
def test_a_failed_delivery_is_retried_on_the_next_run(tmp_path):
    """A 500 from Telegram must not read as 'told him'."""
    table = _all_healthy()
    table["thomas-scheduler"] = "running|unhealthy|0|false|{id}|2026-09-07T00:00:00Z"
    _run(tmp_path, table, delivers="500")
    _run(tmp_path, table, delivers="500")               # confirmed, send fails
    state = (tmp_path / "health-watch.state").read_text(encoding="utf-8")
    assert "reported_sig=\n" in state, "a 500 is not 'told him'"
    assert "pending_thomas-scheduler:unhealthy=2" in state
    (tmp_path / "sent.txt").unlink()

    _run(tmp_path, table, delivers="200")               # next run retries immediately
    assert "unhealthy" in (tmp_path / "sent.txt").read_text(encoding="utf-8")


# --- what an adversarial review of 2026-09-07 found missing --------------------------------------

UNHEALTHY = "running|unhealthy|0|false|{id}|2026-09-07T00:00:00Z"


@posix_only
def test_a_flapping_probe_is_still_reported(tmp_path):
    """A health probe that fails intermittently reads healthy on half the samples. Requiring
    consecutive sightings never confirmed it, and a half-dead scheduler stayed silent forever."""
    bad, good = _all_healthy(), _all_healthy()
    bad["thomas-scheduler"] = UNHEALTHY
    _run(tmp_path, bad, delivers="200")          # seen once
    _run(tmp_path, good, delivers="200")         # a clean run in between
    _run(tmp_path, bad, delivers="200")          # seen twice -> confirmed
    assert "thomas-scheduler" in (tmp_path / "sent.txt").read_text(encoding="utf-8")


@posix_only
def test_a_problem_that_really_cleared_is_forgotten(tmp_path):
    """Tolerating one clean run must not mean remembering a problem for ever."""
    bad = _all_healthy()
    bad["thomas-scheduler"] = UNHEALTHY
    _run(tmp_path, bad, delivers="200")
    for _ in range(2):                            # two clean runs in a row: forget it
        _run(tmp_path, _all_healthy(), delivers="200")
    assert "pending_thomas-scheduler" not in (tmp_path / "health-watch.state").read_text(encoding="utf-8")
    _run(tmp_path, bad, delivers="200")           # a fresh sighting starts from one again
    assert not (tmp_path / "sent.txt").exists()


@posix_only
def test_an_hourly_crash_does_not_alternate_with_all_is_well(tmp_path):
    """A restart is something that happened, not something that is wrong now: its absence on the
    next run is not a recovery, and claiming nine healthy containers ten minutes before the next
    crash is how an alert channel becomes noise."""
    _run(tmp_path, _all_healthy(), delivers="200")           # baseline counts
    crashed = _all_healthy()
    crashed["thomas-pipeline-worker"] = "running|healthy|3|false|{id}|2026-09-07T00:00:00Z"
    _run(tmp_path, crashed, delivers="200")
    assert "크래시 루프" in (tmp_path / "sent.txt").read_text(encoding="utf-8")
    (tmp_path / "sent.txt").unlink()

    _run(tmp_path, crashed, delivers="200")                  # count steady: nothing new to say
    assert not (tmp_path / "sent.txt").exists(), "no ✅ for an event that cannot recover"


@posix_only
def test_a_container_stuck_restarting_still_names_the_cause(tmp_path):
    """A crash loop is usually sampled as `restarting`; reading the status first threw away the
    OOM flag and the restart count that say why."""
    _run(tmp_path, _all_healthy(), delivers="200")
    table = _all_healthy()
    table["hermes"] = "restarting|none|7|true|{id}|2026-09-07T00:00:00Z"
    _run(tmp_path, table, delivers="200")
    sent = (tmp_path / "sent.txt").read_text(encoding="utf-8")
    assert "OOM" in sent and "크래시 루프" in sent


@posix_only
def test_an_exited_container_is_reported(tmp_path):
    """The commonest real failure, and nothing pinned it: deleting the status branch passed."""
    table = _all_healthy()
    table["thomas-operator"] = "exited|none|0|false|{id}|2026-09-07T00:00:00Z"
    _run(tmp_path, table, delivers="200")
    _run(tmp_path, table, delivers="200")
    assert "상태 exited" in (tmp_path / "sent.txt").read_text(encoding="utf-8")


@posix_only
def test_a_silence_file_dated_in_the_future_does_not_mute(tmp_path):
    """`touch -d` with a mistyped year, or a clock that stepped back, would otherwise mute this
    for as long as the date is wrong — the one thing the six-hour ceiling exists to prevent."""
    table = _all_healthy()
    table["hermes"] = UNHEALTHY
    silence = tmp_path / "health-watch.silence"
    silence.touch()
    os.utime(silence, (2 ** 31 - 1, 2 ** 31 - 1))            # 2038
    _run(tmp_path, table, delivers="200")
    _run(tmp_path, table, delivers="200")
    assert (tmp_path / "sent.txt").exists()


@posix_only
def test_an_unknown_argument_refuses_to_run(tmp_path):
    """A typo'd rehearsal (`--dryrun`) must not become a live send."""
    result = subprocess.run(
        [str(SCRIPT), "--dryrun"], capture_output=True, text=True, timeout=60,
        env={**os.environ, "HEALTH_WATCH_STATE_DIR": str(tmp_path)},
    )
    assert result.returncode == 64 and "usage" in result.stderr
    assert not (tmp_path / "health-watch.log").exists()


@posix_only
def test_a_state_file_edited_by_hand_does_not_wedge_the_watch(tmp_path):
    """A non-numeric counter reaching `$(( ))` under `set -u` killed the shell before it could
    write state or log — and every later run died in the same place, silently."""
    table = _all_healthy()
    table["thomas-scheduler"] = UNHEALTHY
    _run(tmp_path, table, delivers="200")
    state = tmp_path / "health-watch.state"
    state.write_text(state.read_text(encoding="utf-8").replace("=1:0", "=x:0"), encoding="utf-8")
    result = _run(tmp_path, table, delivers="200")
    assert result.returncode in (0, 1) and "unbound variable" not in result.stderr
    assert "PENDING" in (tmp_path / "health-watch.log").read_text(encoding="utf-8") or \
           (tmp_path / "sent.txt").exists()


@posix_only
def test_a_daemon_outage_does_not_erase_the_restart_baseline(tmp_path):
    """Rewriting only what was observed dropped all nine baselines on one outage, and the first
    run after it had nothing to compare against — a crash loop across the gap would vanish."""
    table = _all_healthy()
    table["hermes"] = "running|healthy|2|false|{id}|2026-09-07T00:00:00Z"
    _run(tmp_path, table, delivers="200")
    down = subprocess.run(
        [str(SCRIPT)], capture_output=True, text=True, timeout=60,
        env={**os.environ, "DOCKER_STUB_DAEMON_DOWN": "1",
             "DOCKER_BIN": str(_stub_docker(tmp_path, {})), "CURL_BIN": str(_stub_curl(tmp_path, "200")),
             "HEALTH_WATCH_STATE_DIR": str(tmp_path), "HEALTH_WATCH_CONFIRM_RUNS": "2"},
    )
    assert down.returncode in (0, 1)
    assert "restart_hermes=" in (tmp_path / "health-watch.state").read_text(encoding="utf-8")


@posix_only
def test_a_state_file_it_cannot_write_is_said_out_loud(tmp_path):
    """A watch that cannot remember confirms nothing, for ever. That must not be silent."""
    state_dir = tmp_path / "blocked"
    state_dir.mkdir()
    # A directory where the temporary file goes: the redirection fails for root too, which a
    # read-only directory does not (these tests run as root on the deployment host).
    (state_dir / "health-watch.state.tmp").mkdir()
    env = dict(os.environ, DOCKER_BIN=str(_stub_docker(tmp_path, _all_healthy())),
               CURL_BIN=str(_stub_curl(tmp_path, "200")), HEALTH_WATCH_STATE_DIR=str(state_dir))
    result = subprocess.run([str(SCRIPT)], capture_output=True, text=True, env=env, timeout=60)
    assert "STATE WRITE FAILED" in result.stderr
    assert "STATE-WRITE-FAILED" in (state_dir / "health-watch.log").read_text(encoding="utf-8")
